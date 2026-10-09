"""Run lifecycle: start, phase transitions with hard preconditions, tasks, TDD evidence, approvals."""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import evidence as E
from . import gates as G
from . import paths
from . import planparse as PP
from . import snapshot as S
from . import tempo as T
from .state import (OPEN_STATUSES, PHASES, OrkestraError, StateTxn, active_runs, bind_marker, ledger, load_state,
                    new_state, next_run_id, project_active_run, save_state, set_active_run, unbind_marker)
from .util import (now_iso, read_json, read_text, sha256_bytes, tail_lines, write_json_atomic,
                   write_text_atomic)

TEMPLATES = Path(__file__).resolve().parent.parent.parent / "templates"

BACK_EDGES = {
    "discover": ["intake"],
    "clarify": ["discover"],
    "plan": ["clarify"],
    "approve": ["plan", "clarify"],
    "implement": ["plan"],
    "verify": ["implement"],
    "review": ["implement"],
    "deliver": ["review", "implement"],
}


# ---------------------------------------------------------------- project init / memory skeleton

def init_project(project: Path) -> List[str]:
    created: List[str] = []
    ok = paths.okdir(project)
    for d in (paths.memory_dir(project) / "specs", paths.features_dir(project), paths.runs_dir(project)):
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d.relative_to(project)))
    gi = ok / ".gitignore"
    if not gi.exists():
        write_text_atomic(gi, "# orkestra: machine state, backups and logs stay local\nruns/\n*.lock\ncurrent\n")
        created.append(str(gi.relative_to(project)))
    for name in ("PROJECT.md", "FEATURES.md", "DECISIONS.md", "LEARNINGS.md"):
        target = paths.memory_dir(project) / name
        if not target.exists():
            tpl = read_text(TEMPLATES / "memory" / name) or f"# {name[:-3]}\n"
            write_text_atomic(target, tpl.replace("{{project}}", project.name))
            created.append(str(target.relative_to(project)))
    cfgp = paths.project_config_path(project)
    if not cfgp.exists():
        cfg = {"gates": G.detect(project), "gates_confirmed": False, "ui": {"kind": "unknown"}, "notes": ""}
        paths.save_project_config(project, cfg)
        created.append(str(cfgp.relative_to(project)))
    return created


# ---------------------------------------------------------------- start / resume

def start_run(project: Path, title: str, request: str, size: str, mode: str, engine: str = "claude") -> Dict[str, Any]:
    project = project.resolve(strict=False)
    existing = project_active_run(project)
    if existing:
        st = load_state(project, existing)
        raise OrkestraError(f"project already has an open run '{existing}' (phase {st['phase']}, status {st['status']}). "
                            f"Resume it with `orkestra resume` or close it with `orkestra abort \"reason\"`.")
    init_project(project)
    run_id = next_run_id(project, title)
    fdir = paths.feature_dir(project, run_id)
    rdir = paths.run_dir(project, run_id)
    (fdir / "tasks").mkdir(parents=True, exist_ok=True)
    (fdir / "reviews").mkdir(parents=True, exist_ok=True)
    rdir.mkdir(parents=True, exist_ok=True)
    st = new_state(project, run_id, title, size, mode, engine)
    base = S.take_baseline(project, rdir)
    st["baseline"] = {"git": base["git"], "head": base.get("head"), "at": base["at"],
                      "dirty_files": len(base.get("files") or {})}
    st["baseline"]["deps"] = G.dependency_sets(project)
    save_state(project, st)
    req_md = (f"# İstek — {title}\n\n- run: `{run_id}`\n- boyut: {size}\n- mod: {mode}\n- motor: {engine}\n- tarih: {now_iso()}\n\n"
              f"## Ham istek\n\n{request.strip()}\n")
    write_text_atomic(fdir / "request.md", req_md)
    set_active_run(project, run_id)
    point_current(project, run_id)
    ledger(project, run_id, f"run started: {title!r} (size {size}, mode {mode}, engine {engine}); baseline git={base['git']} "
                            f"head={str(base.get('head'))[:10]} dirty_files={st['baseline']['dirty_files']}")
    return st


def other_open_runs(project: Path) -> List[str]:
    """Open runs in OTHER projects (they share this machine's CPU: tests, builds, simulators)."""
    out: List[str] = []
    me = str(Path(project).resolve(strict=False))
    for key, rid in sorted(active_runs().items()):
        if str(Path(key).resolve(strict=False)) == me:
            continue
        st = read_json(paths.run_dir(Path(key), rid) / "state.json")
        if isinstance(st, dict) and st.get("status") in OPEN_STATUSES:
            out.append(f"{Path(key).name} ({st.get('phase')})")
    return out


def point_current(project: Path, run_id: Optional[str]) -> None:
    """`.orkestra/current` → features/<run> (stable short path for agents; removed when the run closes)."""
    link = paths.okdir(project) / "current"
    try:
        if link.is_symlink() or link.exists():
            link.unlink()
        if run_id:
            link.symlink_to(Path("features") / run_id)
    except OSError:
        pass


def resume_run(project: Path, run_id: Optional[str]) -> Dict[str, Any]:
    project = project.resolve(strict=False)
    rid = run_id or project_active_run(project)
    if not rid:
        raise OrkestraError(f"no open run in {project}")
    st = load_state(project, rid)
    if st["status"] in ("done", "aborted"):
        raise OrkestraError(f"run '{rid}' is {st['status']}")
    set_active_run(project, rid)
    point_current(project, rid)
    if st["status"] == "paused":
        with StateTxn(project, rid) as s:
            s["status"] = "active"
        st = load_state(project, rid)
        ledger(project, rid, "resumed")
    return st


def usage_snapshot(project: Path, st: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Token usage of the sessions bound to this run, read once when the run closes (best effort, never raises)."""
    try:
        from . import usage as U
        from .util import read_json as _rj
        ids = []
        d = paths.sessions_dir()
        if d.is_dir():
            for f in d.glob("*.json"):
                data = _rj(f)
                if isinstance(data, dict) and data.get("run_id") == st.get("run_id") \
                        and Path(data.get("project", "")) == Path(project):
                    ids.append(f.stem)
        created = T._ts(st.get("created"))
        u = U.for_sessions(ids, since=created - 120 if created else None) if ids else None
        if u:
            u = {k: u.get(k) for k in ("input", "output", "cache_write", "cache_read", "total", "messages", "context")}
            u["at"] = now_iso()
        return u
    except Exception:
        return None


# ---------------------------------------------------------------- preconditions

def spec_plan_hash(project: Path, run_id: str) -> str:
    fdir = paths.feature_dir(project, run_id)
    data = (fdir / "spec.md").read_bytes() if (fdir / "spec.md").exists() else b""
    data += b"\0" + ((fdir / "plan.md").read_bytes() if (fdir / "plan.md").exists() else b"")
    return sha256_bytes(data)


def preconditions(project: Path, st: Dict[str, Any], target: str) -> List[str]:
    rid = st["run_id"]
    fdir = paths.feature_dir(project, rid)
    rdir = paths.run_dir(project, rid)
    spec = read_text(fdir / "spec.md")
    plan = read_text(fdir / "plan.md")
    problems: List[str] = []
    if target == "discover":
        if not (fdir / "request.md").exists():
            problems.append("request.md missing")
    elif target == "clarify":
        ctx = read_text(fdir / "context.md")
        if len(ctx.strip()) < 200:
            problems.append("context.md missing or too thin (discovery output, >= 200 chars)")
        if not (rdir / "gates-baseline.json").exists():
            problems.append("baseline gates not recorded: run `orkestra gate baseline` (it records an empty "
                            "baseline if the project has no gates, which is reported as a risk)")
    elif target == "plan":
        if not spec.strip():
            problems.append("spec.md missing")
        elif not PP.has_section(spec, "Netleştirmeler", "Clarifications"):
            problems.append("spec.md needs a `## Netleştirmeler` section (questions asked + answers, or why none were needed)")
        if spec.strip() and not PP.spec_ids(spec):
            problems.append("spec.md defines no FR-###/SC-### ids")
    elif target == "approve":
        problems += PP.check_plan(spec, plan, project)
        if st.get("size") != "XS":
            critic = read_text(fdir / "reviews" / "plan-critic.md")
            v = PP.verdict(critic)
            if v not in ("PASS", "PASS_WITH_NOTES"):
                problems.append("plan-critic verdict missing or not PASS (reviews/plan-critic.md → `VERDICT: PASS`)")
    elif target == "implement":
        appr = read_json(rdir / "approval.json")
        if not isinstance(appr, dict) or appr.get("decision") != "approve":
            problems.append("no user approval recorded. Approval is written ONLY by hooks when the user answers the "
                            "approval question with 'Onayla' or types 'onay'. Ask the user. (Never record it "
                            "yourself — calling the hooks, writing the file or replaying a test payload is forging the "
                            "user's consent, also when they said not to ask questions: the approval is not a question "
                            "you may skip.)")
        elif appr.get("hash") != spec_plan_hash(project, rid):
            problems.append("spec/plan changed after approval — show the diff and ask the user to approve again")
        problems += PP.check_plan(spec, plan, project)
    elif target == "verify":
        open_tasks = [tid for tid, t in st.get("tasks", {}).items() if t.get("status") not in ("done", "deferred")]
        if open_tasks:
            problems.append(f"tasks not done: {', '.join(sorted(open_tasks))}")
        for tid, t in st.get("tasks", {}).items():
            if t.get("status") == "done" and t.get("tdd") == "required" and not (t.get("green") or {}).get("ok"):
                problems.append(f"{tid}: done without green test evidence (`orkestra tdd green {tid} -- <cmd>`)")
            if t.get("status") == "deferred" and not t.get("deferred_reason"):
                problems.append(f"{tid}: deferred without a reason")
    elif target == "review":
        last = read_json(rdir / "gates-verify.json")
        if not isinstance(last, dict):
            problems.append("`orkestra gate verify` has not been run")
        elif not (last.get("comparison") or {}).get("ok"):
            problems.append("last `orkestra gate verify` shows regressions/failures — fix before review")
        else:
            newest_change = _newest_change_mtime(project, rdir)
            if newest_change and newest_change > float(last.get("ts", 0)):
                problems.append("files changed after the last `orkestra gate verify` — run it again")
        ver = read_text(fdir / "verification.md")
        if PP.verdict(ver) != "PASS":
            problems.append("verification.md must end with `VERDICT: PASS`")
        problems += PP.check_trace(spec, ver)
        gate_names = [g["name"] for g in paths.project_config(project).get("gates") or []]
        problems += E.verification_problems(project, rid, spec, ver, gate_names)
        problems += E.integrity_problems(project, rid)
        problems += deps_problems(project, st)
    elif target == "deliver":
        open_tasks = [tid for tid, t in st.get("tasks", {}).items() if t.get("status") not in ("done", "deferred")]
        if open_tasks:
            problems.append(f"tasks not done (incl. review fixes): {', '.join(sorted(open_tasks))}")
        tri = read_text(fdir / "reviews" / "triage.md")
        if not tri.strip():
            problems.append("reviews/triage.md missing (every review finding needs a verdict: fix/defer/reject)")
        else:
            open_items = PP.open_triage_items(tri)
            if open_items:
                problems.append(f"open review items: {', '.join(open_items)}")
        last = read_json(rdir / "gates-verify.json")
        newest_change = _newest_change_mtime(project, rdir)
        if isinstance(last, dict) and newest_change and newest_change > float(last.get("ts", 0)):
            problems.append("code changed during review fixes — run `orkestra gate verify` again")
        elif isinstance(last, dict) and not (last.get("comparison") or {}).get("ok"):
            problems.append("last gate verify is not green")
    elif target == "done":
        rep = read_text(fdir / "report.md")
        if len(rep.strip()) < 300:
            problems.append("report.md missing or too short")
        ok, why = E.report_has_current_block(project, rid)
        if not ok:
            problems.append(why)
        feats = read_text(paths.memory_dir(project) / "FEATURES.md")
        if rid not in feats:
            problems.append(f"memory/FEATURES.md has no entry for {rid} (`orkestra memory feature-add ...`)")
    return problems


def _newest_change_mtime(project: Path, rdir: Path) -> float:
    newest = 0.0
    for item in S.changed_files(project, rdir):
        p = project / item["path"]
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            newest = max(newest, 0.0)
    return newest


def deps_problems(project: Path, st: Dict[str, Any]) -> List[str]:
    before = (st.get("baseline") or {}).get("deps") or {}
    after = G.dependency_sets(project)
    added = G.new_dependencies(before, after)
    approved = {G.normalize_pkg(x) for x in ((st.get("approved") or {}).get("deps") or [])}
    unapproved = [d for d in added if G.normalize_pkg(d) not in approved]
    return [f"new dependency not approved by the user: {d}" for d in unapproved]


# ---------------------------------------------------------------- transitions

def phase_next(project: Path, run_id: str, note: str = "") -> Tuple[str, str]:
    with StateTxn(project, run_id) as st:
        cur = st["phase"]
        if cur == "done":
            raise OrkestraError("run is already done")
        target = PHASES[PHASES.index(cur) + 1]
        problems = preconditions(project, st, target)
        if problems:
            raise OrkestraError(f"cannot move {cur} → {target}:\n  - " + "\n  - ".join(problems))
        if target == "implement":
            _apply_approval(project, st)
        if target == "done":
            st["status"] = "done"
            snap = usage_snapshot(project, st)
            if snap:
                st["usage"] = snap
        st["phase"] = target
        st["awaiting"] = None
        st["history"].append({"at": now_iso(), "from": cur, "to": target, "note": note})
    ledger(project, run_id, f"phase {cur} → {target}" + (f" — {note}" if note else ""))
    if target == "done":
        set_active_run(project, None)
        point_current(project, None)
    return cur, target


def phase_back(project: Path, run_id: str, target: str, reason: str) -> Tuple[str, str]:
    with StateTxn(project, run_id) as st:
        cur = st["phase"]
        if target not in BACK_EDGES.get(cur, []):
            raise OrkestraError(f"cannot go back {cur} → {target}; allowed: {BACK_EDGES.get(cur, [])}")
        if PHASES.index(target) < PHASES.index("implement") and st.get("approved"):
            st["approved"] = None
            appr = paths.run_dir(project, run_id) / "approval.json"
            if appr.exists():
                appr.rename(appr.with_name(f"approval-superseded-{int(time.time())}.json"))
        st["phase"] = target
        st["status"] = "active"
        tasks = st.get("tasks") or {}
        # "mark": how much task work existed when the phase went back (tempo.next_action: was anything added since?)
        st["history"].append({"at": now_iso(), "from": cur, "to": target, "note": f"back: {reason}",
                              "mark": {"tasks": len(tasks), "reopens": sum(T.reopen_count(t) for t in tasks.values())}})
    ledger(project, run_id, f"phase back {cur} → {target}: {reason}")
    return cur, target


def _apply_approval(project: Path, st: Dict[str, Any]) -> None:
    rid = st["run_id"]
    fdir = paths.feature_dir(project, rid)
    rdir = paths.run_dir(project, rid)
    plan = read_text(fdir / "plan.md")
    fm, _ = PP.parse_frontmatter(plan)
    deps = fm.get("approved_dependencies") or []
    if not isinstance(deps, list):
        deps = [str(deps)]
    appr = read_json(rdir / "approval.json") or {}
    st["approved"] = {"hash": appr.get("hash"), "at": appr.get("at"), "source": appr.get("source"),
                      "deps": [str(d) for d in deps if d]}
    tasks: Dict[str, Any] = {}
    for t in PP.parse_tasks(plan):
        prev = (st.get("tasks") or {}).get(t.id) or {}
        # keep everything the run recorded about the task (started, review_seen, history …); the plan owns the rest
        tasks[t.id] = dict(prev, title=t.title, status=prev.get("status", "pending"), tdd=t.tdd, files=t.files,
                           depends=t.depends, red=prev.get("red"), green=prev.get("green"), notes=prev.get("notes", []))
    st["tasks"] = tasks
    adir = rdir / "approved"
    adir.mkdir(parents=True, exist_ok=True)
    for name in ("spec.md", "plan.md"):
        if (fdir / name).exists():
            shutil.copy2(fdir / name, adir / name)
    # stamp spec as approved for humans (frontmatter edit happens on the snapshot copy only)


def record_approval(project: Path, run_id: str, decision: str, source: str, detail: str = "") -> Optional[Dict[str, Any]]:
    """Called by hooks only (UserPromptSubmit / PostToolUse on AskUserQuestion)."""
    rdir = paths.run_dir(project, run_id)
    st = load_state(project, run_id)
    if st.get("phase") != "approve":
        return None
    rec = {"decision": decision, "source": source, "detail": detail[:500], "at": now_iso(),
           "hash": spec_plan_hash(project, run_id)}
    write_json_atomic(rdir / "approval.json", rec)
    with StateTxn(project, run_id) as s:
        s["awaiting"] = None
        if s.get("status") == "awaiting_user":
            s["status"] = "active"
    ledger(project, run_id, f"user {decision} via {source}" + (f": {detail[:120]}" if detail else ""))
    return rec


# ---------------------------------------------------------------- status changes

def set_status(project: Path, run_id: str, status: str, reason: str = "", source: str = "") -> None:
    with StateTxn(project, run_id) as st:
        st["status"] = status
        if status == "aborted":
            snap = usage_snapshot(project, st)
            if snap:
                st["usage"] = snap
        if status == "awaiting_user":
            st["awaiting"] = {"question": reason, "at": now_iso()}
            if source:                              # "hook": set by the AskUserQuestion hook, ends with that call
                st["awaiting"]["source"] = source
        elif status == "blocked":
            st["blocked"] = {"reason": reason, "at": now_iso()}
        elif status == "active":
            st["blocked"] = None
            st["awaiting"] = None
    ledger(project, run_id, f"status → {status}" + (f": {reason}" if reason else ""))
    if status == "aborted":
        set_active_run(project, None)
        point_current(project, None)


# ---------------------------------------------------------------- tasks & TDD

def _need_task(st: Dict[str, Any], tid: str) -> Dict[str, Any]:
    t = (st.get("tasks") or {}).get(tid)
    if not t:
        raise OrkestraError(f"unknown task {tid} (tasks are loaded from plan.md at approval)")
    return t


def task_start(project: Path, run_id: str, tid: str, agent: str = "", force: str = "") -> List[str]:
    """Returns warnings. Two tasks may be in progress at once only when their file lists do not overlap."""
    warnings: List[str] = []
    with StateTxn(project, run_id) as st:
        if st["phase"] not in ("implement", "verify", "review"):
            raise OrkestraError(f"tasks can start only after approval (phase is {st['phase']})")
        t = _need_task(st, tid)
        if t.get("status") in ("done", "deferred"):
            raise OrkestraError(f"{tid} is already {t['status']}. Another round starts with `orkestra task reopen {tid} "
                                f"\"<why>\"` (after a review FAIL), not with task start.")
        if t.get("status") == "in_progress":
            return [f"{tid} is already in progress (started {t.get('started')}) — nothing changed; continue with "
                    f"`orkestra next`."]
        for dep in t.get("depends") or []:
            if (st["tasks"].get(dep) or {}).get("status") not in ("done", "deferred"):
                raise OrkestraError(f"{tid} depends on {dep}, which is not done")
        others = sorted(o for o, v in st["tasks"].items() if o != tid and v.get("status") == "in_progress")
        clash = T.overlap(st["tasks"], tid, others)
        if clash and not force:
            o, shared = clash[0]
            raise OrkestraError(f"{tid} shares {', '.join(shared[:3])} with {o}, which is still in progress. One working "
                                f"tree: finish {o} first (`orkestra next`). If the overlap is harmless, add "
                                f"--force \"<why>\" (recorded in the ledger).")
        if clash:
            warnings.append(f"started despite shared files with {clash[0][0]}: {force}")
        elif others:
            warnings.append(f"{', '.join(others)} also in progress (disjoint files). Parallel implementers are fine only "
                            f"while their files and tests stay disjoint; run gates per task, never the full suite.")
        t["status"] = "in_progress"
        t["agent"] = agent
        t["started"] = now_iso()
        t.setdefault("first_started", t["started"])
    ledger(project, run_id, f"{tid} started" + (f" by {agent}" if agent else "")
           + (f" · forced over shared files: {force}" if force and clash else ""))
    return warnings


def task_done(project: Path, run_id: str, tid: str, note: str = "") -> bool:
    """False when the task was already done (idempotent: nothing is recorded twice)."""
    if (_need_task(load_state(project, run_id), tid).get("status")) == "done":
        return False
    with StateTxn(project, run_id) as st:
        t = _need_task(st, tid)
        if t.get("tdd") == "required":
            if not E.test_files_state(project, paths.run_dir(project, run_id)):
                raise OrkestraError(f"{tid}: TDD task but no test file was added or changed in this run. Write the "
                                    f"test first (or mark the task `TDD: n/a (reason)` in the plan and re-approve).")
            if not (t.get("green") or {}).get("ok"):
                raise OrkestraError(f"{tid}: no passing test evidence. Run `orkestra tdd green {tid} -- <test cmd>`.")
            if not (t.get("red") or {}).get("ok"):
                raise OrkestraError(f"{tid}: no failing-first evidence. TDD requires `orkestra tdd red {tid} -- <cmd>` "
                                    f"before the implementation (or mark the task `TDD: n/a (reason)` in the plan and re-approve).")
        why = review_gap(project, run_id, st, tid, t)
        if why:
            raise OrkestraError(why)
        t["status"] = "done"
        t["done_at"] = now_iso()
        if note:
            t.setdefault("notes", []).append(note)
    ledger(project, run_id, f"{tid} done" + (f" — {note}" if note else ""))
    return True


def is_adhoc(t: Dict[str, Any]) -> bool:
    """Added after approval (`task add`): covered by the final review and its triage, not by a per-task review."""
    return bool(t.get("adhoc")) or any(str(n).startswith("ad-hoc") for n in (t.get("notes") or [])[:1])


def review_gap(project: Path, run_id: str, st: Dict[str, Any], tid: str, t: Dict[str, Any]) -> str:
    """'' when the task may be closed; else what is missing. M/L runs: every plan task needs a PASS review of the
    current round (the playbook always said so — a weak orchestrator simply ran `task done` without dispatching the
    reviewer, or with a FAIL on file). Off: ~/.orkestra/config.json → tempo.task_review_required = false."""
    if st.get("size") in ("XS", "S") or is_adhoc(t):
        return ""
    if (paths.global_config().get("tempo") or {}).get("task_review_required", True) is False:
        return ""
    rv = T.task_review(paths.feature_dir(project, run_id), tid)
    if not rv or float(rv.get("mtime") or 0) <= float(t.get("review_seen") or 0):
        return (f"{tid}: no review of this round. Size {st.get('size')} runs need one per task: dispatch "
                f"orkestra:reviewer with `scope: task {tid}` (it writes reviews/{tid}.review.md), then `task done`.")
    if rv.get("verdict") not in ("PASS", "PASS_WITH_NOTES"):
        if rv.get("verdict") is None:
            return (f"{tid}: {rv.get('name')} has no `VERDICT: PASS | PASS_WITH_NOTES | FAIL` line — ask the reviewer "
                    f"to end the file with one.")
        return (f"{tid}: the review says {rv.get('verdict')} ({rv.get('name')}). Fix it first: "
                f"`orkestra task reopen {tid} \"review round N\"`, then `orkestra brief {tid}` for the implementer.")
    return ""


def task_set(project: Path, run_id: str, tid: str, status: str, reason: str) -> None:
    with StateTxn(project, run_id) as st:
        t = _need_task(st, tid)
        t["status"] = status
        if status == "deferred":
            t["deferred_reason"] = reason
        t.setdefault("notes", []).append(f"{status}: {reason}")
    ledger(project, run_id, f"{tid} → {status}: {reason}")


def task_reopen(project: Path, run_id: str, tid: str, reason: str, force: str = "") -> None:
    """Reopen for another round: evidence is reset so the next round starts with a fresh red test.

    Tempo rules (deterministic; `--force "<why>"` overrides and is written to the ledger):
      * a task whose latest review says PASS is not reopened for minors — they go to the final triage
      * after MAX_REVIEW_ROUNDS - 1 reopen rounds the next one needs the user's ruling
    """
    fdir = paths.feature_dir(project, run_id)
    with StateTxn(project, run_id) as st:
        t = _need_task(st, tid)
        rounds = T.reopen_count(t)
        rv = T.task_review(fdir, tid)
        if rv:
            t["review_seen"] = rv.get("mtime")      # this verdict is now applied; the next round needs a newer review
        if not force:
            if rv.get("verdict") in ("PASS", "PASS_WITH_NOTES"):
                raise OrkestraError(
                    f"{tid}: the latest review ({rv.get('name')}) says {rv['verdict']}. A PASS is not reopened for minors: "
                    f"`orkestra task done {tid} \"review: PASS\"` and carry them with `orkestra task note {tid} \"minor: …\"` "
                    f"(the final review triages them). If a fix truly cannot wait: --force \"<why>\".")
            if rounds >= T.MAX_REVIEW_ROUNDS - 1:
                raise OrkestraError(
                    f"{tid}: already reopened {rounds}×. Round {rounds + 1} failed too → stop and put the trade-off to the "
                    f"user (AskUserQuestion), then pass the ruling: `orkestra task reopen {tid} --force \"<user's ruling>\"` "
                    f"or `orkestra task defer {tid} \"<why>\"`.")
        for k in ("red", "green"):
            if t.get(k):
                t.setdefault("history", []).append({k: t[k]})
            t[k] = None
        t["status"] = "pending"
        t.setdefault("notes", []).append(f"reopened: {reason}" + (f" [forced: {force}]" if force else ""))
    ledger(project, run_id, f"{tid} reopened (evidence reset): {reason}" + (f" · forced: {force}" if force else ""))


def task_add(project: Path, run_id: str, tid: str, title: str, tdd: str = "required") -> None:
    """Ad-hoc task (e.g. a review fix R-001) after approval; recorded in the ledger and the report."""
    if not re.match(r"^[A-Z][A-Z0-9-]{1,20}$", tid):
        raise OrkestraError("task id must look like FIX-R001 or T009")
    with StateTxn(project, run_id) as st:
        if st["phase"] not in ("implement", "verify", "review"):
            raise OrkestraError(f"tasks can be added only after approval (phase {st['phase']})")
        if tid in (st.get("tasks") or {}):
            raise OrkestraError(f"{tid} already exists")
        st.setdefault("tasks", {})[tid] = {"title": title, "status": "pending", "tdd": tdd, "files": [], "depends": [],
                                           "red": None, "green": None, "adhoc": True,
                                           "notes": ["ad-hoc (added after approval)"]}
    ledger(project, run_id, f"{tid} added after approval: {title} (tdd {tdd})")


def task_note(project: Path, run_id: str, tid: str, note: str) -> None:
    with StateTxn(project, run_id) as st:
        _need_task(st, tid).setdefault("notes", []).append(note)


def _norm_cmd(c: str) -> str:
    return re.sub(r"\s+", " ", (c or "").strip())


def tdd_record(project: Path, run_id: str, tid: str, kind: str, cmd: List[str], why: str = "") -> Dict[str, Any]:
    """Run the test command and record red/green evidence.

    Anti-gaming rules (deterministic):
      * red must be a real failure (not success, timeout or command-not-found)
      * green must run the SAME command as red, unless --why explains the deviation
      * test files must not change between red and green (that test version was never seen failing),
        unless --why explains it; deviations are recorded and shown in the report
    """
    if not cmd:
        raise OrkestraError("usage: orkestra tdd red|green T001 -- <test command>")
    st = load_state(project, run_id)
    t = _need_task(st, tid)
    if kind == "red" and (t.get("green") or {}).get("ok"):
        raise OrkestraError(f"{tid} already has green evidence; red must come first "
                            f"(the orchestrator can `orkestra task reopen {tid}` for another round)")
    rdir_run = paths.run_dir(project, run_id)
    shell_cmd = shlex.join(cmd) if len(cmd) > 1 else cmd[0]
    deviations: List[str] = []
    owned_elsewhere: List[str] = []
    foreign_note = ""
    red = t.get("red") or {}
    if kind == "green" and red.get("ok") and t.get("tdd") == "required":
        if _norm_cmd(red.get("cmd", "")) != _norm_cmd(shell_cmd):
            if not why:
                raise OrkestraError(f"{tid}: green must run the same command as red (`{red.get('cmd')}`). If a different "
                                    f"command is genuinely needed, add --why \"<reason>\" (it is shown in the report).")
            deviations.append(f"green command differs from red: {why}")
        before = red.get("test_state") or {}
        now_state = E.test_files_state(project, rdir_run)
        changed_tests = sorted(k for k in set(before) | set(now_state) if before.get(k) != now_state.get(k))
        # A test file that belongs to a task which was WORKING between this red and now (in progress, or finished
        # after the red) changed because of that task — its own red/green covers it. A file of any other task
        # (done long ago, not started) is not exempt: rewriting it is exactly what this rule exists to catch.
        mine = set(t.get("files") or [])
        red_at = T._ts(red.get("at")) or 0.0
        owners: Dict[str, str] = {}
        for o, v in (st.get("tasks") or {}).items():
            concurrent = v.get("status") == "in_progress" or (T._ts(v.get("done_at")) or 0.0) >= red_at > 0
            if o != tid and concurrent:
                for f in v.get("files") or []:
                    owners.setdefault(f, o)
        owned_elsewhere = [k for k in changed_tests if k in owners and k not in mine and k not in shell_cmd]
        if owned_elsewhere:
            changed_tests = [k for k in changed_tests if k not in owned_elsewhere]
            foreign_note = ", ".join(f"{k} ({owners[k]})" for k in owned_elsewhere)
        if changed_tests:
            if not why:
                raise OrkestraError(f"{tid}: test file(s) changed after the red run: {', '.join(changed_tests)}. That test "
                                    f"version was never seen failing. Re-run red with the final test (before the fix), or add "
                                    f"--why \"<reason>\" (shown in the report).")
            deviations.append(f"tests changed after red ({', '.join(changed_tests)}): {why}")
    elif why:
        deviations.append(why)
    rdir = rdir_run / "evidence"
    rdir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    try:
        cp = subprocess.run(shell_cmd, shell=True, cwd=str(project), capture_output=True, timeout=1800,
                            env=_ci_env())
        code = cp.returncode
        out = (cp.stdout or b"").decode("utf-8", "replace") + (cp.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        code, out = 124, "[orkestra] TIMEOUT"
    log = rdir / f"{tid}-{kind}-{int(started)}.log"
    write_text_atomic(log, f"$ {shell_cmd}\n\n{out}\n[exit {code}]\n")
    # red must be a real test failure: not success, not timeout, not "command not found"/not executable
    ok = (code not in (0, 124, 126, 127)) if kind == "red" else code == 0
    rec: Dict[str, Any] = {"ok": ok, "exit": code, "cmd": shell_cmd, "log": str(log), "at": now_iso(),
                           "tail": tail_lines(out, 25, 2500), "deviations": deviations}
    if kind == "red":
        rec["test_state"] = E.test_files_state(project, rdir_run)
    if owned_elsewhere:                     # changed by a concurrent task that owns them — that task's evidence covers them
        rec["foreign_tests_changed"] = owned_elsewhere
        rec["foreign_note"] = f"tests changed by a concurrent task, not counted: {foreign_note}"
    with StateTxn(project, run_id) as s:
        _need_task(s, tid)[kind] = rec
    ledger(project, run_id, f"{tid} tdd {kind}: exit {code} → {'OK' if ok else 'NOT OK'} ({shell_cmd})"
                            + (f" · deviation: {'; '.join(deviations)}" if deviations else "")
                            + (f" · concurrent-task tests changed (exempt): {foreign_note}" if foreign_note else ""))
    return rec


def run_evidence(project: Path, run_id: str, label: str, cmd: List[str]) -> Dict[str, Any]:
    """Record an ad-hoc verification command (e.g. a manual CLI check) as a real log the report can cite."""
    if not cmd:
        raise OrkestraError("usage: orkestra evidence <label> -- <command>")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", label)[:40] or "check"
    shell_cmd = shlex.join(cmd) if len(cmd) > 1 else cmd[0]
    rdir = paths.run_dir(project, run_id) / "evidence"
    rdir.mkdir(parents=True, exist_ok=True)
    try:
        cp = subprocess.run(shell_cmd, shell=True, cwd=str(project), capture_output=True, timeout=900, env=_ci_env())
        code = cp.returncode
        out = (cp.stdout or b"").decode("utf-8", "replace") + (cp.stderr or b"").decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        code, out = 124, "[orkestra] TIMEOUT"
    log = rdir / f"{safe}-{int(time.time())}.log"
    write_text_atomic(log, f"$ {shell_cmd}\n\n{out}\n[exit {code}]\n")
    ledger(project, run_id, f"evidence {safe}: exit {code} ({shell_cmd}) → {log.name}")
    return {"exit": code, "log": str(log), "rel": f"evidence/{log.name}", "tail": tail_lines(out, 30, 3000)}


def _ci_env() -> Dict[str, str]:
    import os
    env = dict(os.environ)
    env.setdefault("CI", "1")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


# ---------------------------------------------------------------- gates (state wiring)

def gates_baseline(project: Path, run_id: str) -> Tuple[List[Dict[str, Any]], str]:
    cfg = paths.project_config(project)
    rdir = paths.run_dir(project, run_id)
    results = G.run_all(project, cfg.get("gates") or [], rdir / "evidence", "baseline")
    G.save_results(rdir / "gates-baseline.json", results, {"ts": time.time()})
    ok = sum(1 for r in results if r["ok"])
    ledger(project, run_id, f"gate baseline: {ok}/{len(results)} passing")
    return results, G.format_results(results)


def gates_verify(project: Path, run_id: str) -> Tuple[Dict[str, Any], str]:
    cfg = paths.project_config(project)
    rdir = paths.run_dir(project, run_id)
    base = (read_json(rdir / "gates-baseline.json") or {}).get("results") or []
    label = f"verify-{int(time.time())}"
    results = G.run_all(project, cfg.get("gates") or [], rdir / "evidence", label)
    comparison = G.compare(base, results)
    # stamp AFTER the gates ran: files the gates themselves write (junit.xml, caches) must not look like new edits
    G.save_results(rdir / "gates-verify.json", results, {"comparison": comparison, "ts": time.time()})
    ledger(project, run_id, f"gate verify: {'NO REGRESSIONS' if comparison['ok'] else 'REGRESSION'} "
                            f"{comparison}")
    return comparison, G.format_results(results, comparison, base)


def finish_marker(project: Path, run_id: str) -> str:
    return unbind_marker(project, run_id)


def start_marker(project: Path, run_id: str) -> str:
    return bind_marker(project, run_id)
