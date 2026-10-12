"""`orkestra` CLI — the only writer of run state. Agents call it via Bash.

Exit codes: 0 ok · 1 check failed (e.g. regression) · 2 usage/precondition error.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import __version__
from . import engines as EN
from . import evidence as E
from . import gates as G
from . import intent
from . import memory as M
from . import paths
from . import planparse as PP
from . import runs as R
from . import snapshot as S
from . import tempo as T
from .cliparse import build_parser, hoist_globals, split_remainder  # noqa: F401  (one parser: CLI + guards)
from .cliparse import parse as parse_argv
from .state import (PHASES, OrkestraError, bind_marker, load_state, project_active_run, unbind_marker)
from .util import read_json, read_text

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOKS = PLUGIN_ROOT / "skills" / "run" / "references"
TEMPLATES = PLUGIN_ROOT / "templates"

NEXT_ACTION = {
    "intake": "Classify the request (size XS/S/M/L, project, risk) into request.md → `orkestra phase next`.",
    "discover": "Load memory, run scouts (parallel, read-only) → context.md; confirm gates; `orkestra gate baseline` → `orkestra phase next`.",
    "clarify": "Ask only outcome-changing questions (AskUserQuestion; max 4 per round, ≤2 rounds) → spec.md with ## Netleştirmeler → `orkestra phase next`.",
    "plan": "orkestra:architect writes plan.md → orkestra:plan-critic → fix until VERDICT: PASS → `orkestra plan check` → `orkestra phase next`.",
    "approve": "Show the plan summary and ask for approval (header 'Plan onayı', option 'Onayla — uygulamaya başla'). Hooks record the answer → `orkestra phase next`.",
    "implement": "For each task in dependency order: brief → orkestra:implementer (TDD) → orkestra:reviewer → `orkestra task done`. Then `orkestra phase next`.",
    "verify": "`orkestra gate verify` (no regressions) → orkestra:verifier writes verification.md (VERDICT: PASS) → `orkestra phase next`.",
    "review": "Parallel final review (reviewer + adversary) → triage every finding in reviews/triage.md → fix via implementer → re-verify → `orkestra phase next`.",
    "deliver": "Write report.md, update memory (feature-add, specs, decisions, learnings) → `orkestra phase next` (done).",
    "done": "Run complete.",
}


_ASKER = EN.detect_env()       # the agent whose shell runs this command (None: an ordinary terminal)


_footnoted = False


def _out(text: str = "") -> None:
    global _footnoted
    if _ASKER == "cursor" and "--json" not in sys.argv:     # same words, Cursor's names (machine output stays as it is)
        adapted = EN.adapt_text(text, "cursor", footnote=not _footnoted)
        _footnoted = _footnoted or EN.ASK_FOOTNOTE in adapted      # said once per command
        text = adapted
    sys.stdout.write(text + ("\n" if not text.endswith("\n") else ""))


def _bind_here(project: Path, rid: str) -> None:
    """Cursor tells the agent's shell which conversation it is: bind it now (Claude Code sessions are bound by the
    PostToolUse hook from the marker line; in Cursor that hook is a second, equivalent path)."""
    raw = EN.env_session_id()
    if not raw:
        return
    from . import state as ST
    if ST.child_binding(raw):
        return                                      # a subagent's shell: its parent drives the run
    try:
        ST.bind_session(EN.session_key("cursor", raw), project, rid, engine="cursor")
    except Exception:
        pass


def _unbind_here(project: Path, rid: str) -> None:
    """Drop this Cursor conversation bind to rid immediately (pause/abort).
    Claude Code still relies on the marker parsed by PostToolUse; Cursor gets both
    so Stop cannot keep driving after pause."""
    raw = EN.env_session_id()
    if not raw:
        return
    from . import state as ST
    if ST.child_binding(raw):
        return
    try:
        sid = EN.session_key("cursor", raw)
        data = read_json(paths.sessions_dir() / f"{ST._safe(sid)}.json")
        if not isinstance(data, dict):
            return
        if data.get("run_id") != rid or Path(str(data.get("project") or "")) != project:
            return
        ST.unbind_session(sid)
    except Exception:
        pass


def _require_cursor_hooks() -> None:
    """`orkestra start|resume` typed by a Cursor agent: our preToolUse hook saw this very command a moment ago and
    left a mark. No mark = the hooks are not active in this chat — it would run without guards, and the plan
    approval (recorded by a hook) could never arrive."""
    raw = EN.env_session_id()
    if _ASKER != "cursor" or not raw:
        return
    from . import state as ST
    seen = read_json(paths.orkestra_home() / "cursor" / "seen" / f"{ST._safe(raw)}.json")
    if isinstance(seen, dict) and time.time() - float(seen.get("ts") or 0) <= 900:
        return
    raise OrkestraError("orkestra's Cursor hooks did not see this command: they are not active in this chat, so it "
                        "would run without guards and the plan approval could never be recorded. Tell the user: run "
                        "`orkestra cursor install` in a terminal (once), then start again in a NEW chat.")


def _resolve_project(arg: Optional[str], required: bool = True) -> Optional[Path]:
    if arg:
        cand = Path(os.path.expanduser(arg))
        if not cand.is_absolute():
            local = (Path.cwd() / arg).resolve(strict=False)
            ws = (paths.workspace_root() / arg).resolve(strict=False)
            if arg in (".", "..") or arg.startswith(("./", "../")):
                cand = local
            elif ws.is_dir() and ws != paths.workspace_root():
                cand = ws
            else:
                cand = local
            if cand.is_dir() and cand != paths.workspace_root():
                found = paths.find_project_root(cand)
                cand = found or cand
        cand = cand.resolve(strict=False)
        if not cand.is_dir():
            raise OrkestraError(f"project directory not found: {arg}")
        if cand == paths.workspace_root():
            raise OrkestraError("the workspace root is not a project; pass a project folder, e.g. -p shop")
        return cand
    env = os.environ.get("ORKESTRA_PROJECT")
    if env:
        return Path(env).expanduser().resolve(strict=False)
    found = paths.find_project_root(Path.cwd())
    if found is None and required:
        raise OrkestraError("not inside a project. cd into it or pass --project <name|path> "
                            f"(workspace: {paths.workspace_root()})")
    return found


def _run_id(project: Path, arg: Optional[str]) -> str:
    rid = arg or project_active_run(project)
    if not rid:
        raise OrkestraError(f"no open run in {project.name}. Start one with `orkestra start --title ... --request ...`")
    return rid


def _mode() -> str:
    return "headless" if os.environ.get("ORKESTRA_HEADLESS") == "1" else "interactive"


def _progress(st: Dict[str, Any]) -> str:
    tasks = st.get("tasks") or {}
    if not tasks:
        return "—"
    done = sum(1 for t in tasks.values() if t.get("status") in ("done", "deferred"))
    return f"{done}/{len(tasks)}"


def _next_task(st: Dict[str, Any]) -> Optional[str]:
    tasks = st.get("tasks") or {}
    for tid in sorted(tasks):
        t = tasks[tid]
        if t.get("status") in ("pending", "in_progress", "blocked"):
            deps_ok = all((tasks.get(d) or {}).get("status") in ("done", "deferred") for d in t.get("depends") or [])
            if deps_ok:
                return tid
    return None


def _next_lines(project: Path, st: Dict[str, Any], deep: bool = True) -> List[str]:
    """The deterministic next step (≤ 3 lines). Never raises: a broken artifact must not hide the phase goal."""
    try:
        return T.next_lines(T.next_action(project, st, deep=deep), T.flags(project, st))
    except Exception as exc:  # pragma: no cover
        return [f"next: `orkestra playbook {st.get('phase')}` (next-step analysis failed: {exc})"]


def _tempo(project: Path, rid: str) -> str:
    try:
        return T.tempo_line(project, load_state(project, rid))
    except Exception:  # pragma: no cover
        return ""


# ====================================================================== commands

def cmd_context(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project, required=False)
    if project is None:
        ws = paths.workspace_root()
        _out(f"ORKESTRA CONTEXT — no project resolved from cwd ({Path.cwd()}).")
        _out(f"workspace: {ws}")
        if ws.is_dir():
            projs = sorted(p.name for p in ws.iterdir() if p.is_dir() and not p.name.startswith("."))
            _out("projects: " + ", ".join(projs))
        _out("Resolve the target project from the user's request and pass `-p <name>` to every orkestra command.")
        return 0
    cfg = paths.project_config(project)
    dg = M.digest(project) if paths.memory_dir(project).is_dir() else {}
    gates_txt = ", ".join(g["name"] for g in cfg.get("gates") or []) or "none configured"
    if not paths.okdir(project).is_dir():
        gates_txt = ", ".join(g["name"] for g in G.detect(project)) + " (detected, not saved)"
    _out(f"ORKESTRA CONTEXT — project: {project.name} ({project})")
    _out(f"mode: {_mode()} · plugin: {__version__}")
    if dg:
        _out(f"memory: features {dg.get('features_total', '0')} · learnings {dg.get('learnings_total', '0')} · "
             f"specs: {dg.get('specs') or '—'}")
    else:
        _out("memory: NOT INITIALIZED → run onboarding first (`orkestra init` + playbook onboard)")
    _out(f"gates: {gates_txt} · confirmed: {'yes' if cfg.get('gates_confirmed') else 'no'}")
    rid = project_active_run(project)
    if rid:
        st = load_state(project, rid)
        nt = _next_task(st)
        _out(f"active run: {rid} · \"{st['title']}\" · phase {st['phase']} · status {st['status']} · tasks {_progress(st)}"
             + (f" · next task {nt}" if nt and st['phase'] == 'implement' else ""))
        if st.get("awaiting"):
            _out(f"awaiting user: {st['awaiting'].get('question', '')}")
        if st.get("blocked"):
            _out(f"blocked: {st['blocked'].get('reason', '')}")
        _out(f"phase goal: {NEXT_ACTION.get(st['phase'], '')}")
        for ln in _next_lines(project, st):
            _out(ln)
        if _mode() == "headless" and st["phase"] in ("clarify", "approve"):
            _out("headless: no AskUserQuestion — follow `orkestra playbook headless` for questions/approval")
        elif _ASKER == "cursor" and st["phase"] in ("clarify", "approve"):
            _out("cursor: questions and the plan approval follow `orkestra playbook headless` (chat question → "
                 "`orkestra await \"<summary>\"` → end your turn; approval = the user types \"onay\")")
        _out(f"playbook: `orkestra playbook {st['phase']}`")
        _out(f"artifacts: {paths.feature_dir(project, rid)}")
        _out("this session is not driving the run unless it ran `orkestra start` or `orkestra resume`")
    else:
        _out("active run: none")
    if dg.get("project"):
        _out("--- PROJECT.md (head)\n" + dg["project"])
    if dg.get("features"):
        _out("--- recent features\n" + dg["features"])
    if dg.get("learnings"):
        _out("--- latest learnings\n" + dg["learnings"])
    return 0


def cmd_init(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    created = R.init_project(project)
    _out(f"initialized {project.name}: " + (", ".join(created) if created else "already initialized"))
    cfg = paths.project_config(project)
    _out("detected gates: " + (", ".join(f"{g['name']}=`{g['cmd']}`" for g in cfg.get("gates") or []) or "none"))
    return 0


def cmd_start(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    request = a.request or ""
    if a.request_file:
        request = read_text(Path(a.request_file))
    if not request.strip():
        raise OrkestraError("--request or --request-file is required (the user's request, verbatim)")
    mode = "headless" if (a.headless or _mode() == "headless") else "interactive"
    try:
        engine = EN.normalize(getattr(a, "engine", None) or _ASKER, EN.default_engine(paths.global_config()))
    except EN.EngineError as exc:
        raise OrkestraError(str(exc))
    _require_cursor_hooks()
    approval_mode = a.approval_mode or os.environ.get("ORKESTRA_APPROVAL_MODE") or (
        "manual" if intent.manual_approval_requested(request) else "autopilot")
    if approval_mode not in ("autopilot", "manual"):
        raise OrkestraError("ORKESTRA_APPROVAL_MODE must be autopilot or manual")
    size_arg = getattr(a, "size", "auto") or "auto"
    size = intent.infer_size(request) if size_arg == "auto" else size_arg
    if size not in intent.SIZES:
        raise OrkestraError(f"invalid size {size!r}")
    st = R.start_run(project, a.title, request, size, mode, engine, approval_mode)
    rid = st["run_id"]
    _bind_here(project, rid)
    _out(f"run started: {rid} in {project}" + ("" if engine == "claude" else f" (engine: {engine})"))
    _out(f"size: {size}" + (" (inferred from request; override with --size XS|S|M|L)" if size_arg == "auto" else ""))
    _out(f"baseline: git={st['baseline']['git']} head={str(st['baseline'].get('head'))[:12]} "
         f"uncommitted_files={st['baseline']['dirty_files']}")
    if st["baseline"]["dirty_files"]:
        _out("NOTE: the working tree already had uncommitted changes. They are backed up and will never be "
             "attributed to this run. Tell the user.")
    _out(f"artifacts: {paths.feature_dir(project, rid)}  (short path: .orkestra/current/ — no globs needed)")
    others = R.other_open_runs(project)
    if len(others) >= 2:
        _out(f"WARNING: {len(others)} other runs are open on this machine ({', '.join(others)}). They share CPU with this "
             f"one (tests, builds, simulators) and every run gets slower. Tell the user in one line; continue if they want.")
    _out(f"next action: {NEXT_ACTION['intake']}")
    _out(bind_marker(project, rid))
    return 0


def cmd_resume(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    _require_cursor_hooks()
    st = R.resume_run(project, a.run)
    _bind_here(project, st["run_id"])
    _out(f"resumed {st['run_id']} · phase {st['phase']} · status {st['status']} · tasks {_progress(st)}")
    # A resume is the user saying "continue from here". Subagent records with no sign of life for a lease period
    # belong to a window that is gone: waiting for them would park the run in the very first answer.
    from . import activity as A
    from .state import ledger
    lease = float((paths.global_config().get("watchdog") or {}).get("lease_seconds") or 120)
    _, lost = T.live_agents(project, st["run_id"], wd=dict(T.wd_cfg(), silent_seconds=lease))
    # …but only when the window that launched them is silent too. Called from the SAME live window (its heartbeat
    # was refreshed by this very command), a quiet agent is just thinking: the normal silent_seconds rule applies.
    ages = A.session_ages(project, st["run_id"])
    lost = [g for g in lost if not g.get("sid") or ages.get(A._safe_sid(str(g["sid"])), lease + 1) > lease]
    if lost:
        gone = A.agents_purge(project, st["run_id"], ids=[str(g.get("id")) for g in lost])
        if gone:
            who = ", ".join(f"{A.short_agent(str(g.get('type') or ''))} {'/'.join(g.get('tasks') or [])}".strip() for g in gone[:6])
            ledger(project, st["run_id"], f"resume: dropped {len(gone)} subagent record(s) with no sign of life ({who})")
            _out(f"NOTE: dropped {len(gone)} subagent record(s) with no sign of life for {T.dur(lease)}+ ({who}) — if their "
                 f"work is unfinished, the next step below dispatches it again")
    for ln in _next_lines(project, st):
        _out(ln)
    _out(f"playbook: `orkestra playbook {st['phase']}`")
    _out(bind_marker(project, st["run_id"]))
    return 0


def cmd_status(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    st = load_state(project, rid)
    if a.json:
        _out(json.dumps(st, indent=2, ensure_ascii=False))
        return 0
    _out(f"{rid} · \"{st['title']}\" · phase {st['phase']} · status {st['status']} · size {st['size']} · mode {st['mode']}"
         f" · approval {st.get('approval_mode', 'autopilot')}"
         + (f" · engine {st['engine']}" if (st.get("engine") or "claude") != "claude" else ""))
    for tid in sorted(st.get("tasks") or {}):
        t = st["tasks"][tid]
        ev = ("R" if (t.get("red") or {}).get("ok") else "-") + ("G" if (t.get("green") or {}).get("ok") else "-")
        _out(f"  {tid} [{t.get('status')}] tdd={t.get('tdd')} evidence={ev} {t.get('title')}")
    appr = read_json(paths.run_dir(project, rid) / "approval.json")
    _out(f"approval: {appr.get('decision')} via {appr.get('source')} at {appr.get('at')}" if isinstance(appr, dict) else "approval: none")
    for ln in _next_lines(project, st):
        _out(ln)
    return 0


def cmd_phase(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    if a.action == "show":
        st = load_state(project, rid)
        _out(" → ".join(f"[{p}]" if p == st["phase"] else p for p in PHASES))
        return 0
    if a.action == "check":
        st = load_state(project, rid)
        target = PHASES[min(PHASES.index(st["phase"]) + 1, len(PHASES) - 1)]
        problems = R.preconditions(project, st, target)
        _out(f"{st['phase']} → {target}: " + ("READY" if not problems else "NOT READY\n  - " + "\n  - ".join(problems)))
        return 0 if not problems else 1
    if a.action == "next":
        cur, nxt = R.phase_next(project, rid, a.note or "")
        _out(f"phase {cur} → {nxt}")
        if nxt == "done":
            _out(unbind_marker(project, rid))
            _out("Run complete. Remind the user: nothing was committed; the changes are in the working tree.")
        else:
            _out(f"next action: {NEXT_ACTION.get(nxt, '')}")
            _out(f"playbook: `orkestra playbook {nxt}`")
            if nxt == "approve":
                note = _large_plan_note(read_text(paths.feature_dir(project, rid) / "plan.md"))
                if note:
                    _out(note)
            _out(_tempo(project, rid))
        return 0
    if a.action == "back":
        if not a.target or not a.reason:
            raise OrkestraError("usage: orkestra phase back <target> --reason \"...\"")
        cur, nxt = R.phase_back(project, rid, a.target, a.reason)
        _out(f"phase back {cur} → {nxt}")
        return 0
    raise OrkestraError(f"unknown phase action {a.action}")


def cmd_status_change(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    mapping = {"await": "awaiting_user", "block": "blocked", "unblock": "active", "pause": "paused", "abort": "aborted"}
    status = mapping[a.cmd]
    reason = " ".join(a.reason or []).strip()
    if a.cmd in ("await", "block", "abort") and not reason:
        raise OrkestraError(f"orkestra {a.cmd} needs a reason/question text")
    R.set_status(project, rid, status, reason)
    _out(f"{rid}: status → {status}" + (f" ({reason})" if reason else ""))
    if status in ("aborted", "paused"):
        # Pause used to leave the session bound: Stop could still force-continue the run.
        _unbind_here(project, rid)
        _out(unbind_marker(project, rid))
        if status == "aborted":
            _out("Run aborted. Files are untouched; use /orkestra:rollback if the user wants changes reverted.")
    return 0


def cmd_gate(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    cfg = paths.project_config(project)
    if a.action == "detect":
        det = G.detect(project)
        for g in det:
            _out(f"- {g['name']} ({g['kind']}): `{g['cmd']}`")
        if a.write:
            cfg["gates"] = det
            paths.save_project_config(project, cfg)
            _out(f"saved {len(det)} gates to {paths.project_config_path(project)}")
        return 0
    if a.action == "list":
        for g in cfg.get("gates") or []:
            _out(f"- {g['name']} ({g.get('kind')}): `{g['cmd']}` timeout={g.get('timeout', G.DEFAULT_TIMEOUT)}s")
        if not cfg.get("gates"):
            _out("no gates configured — edit .orkestra/config.json or run `orkestra gate detect --write`")
        return 0
    if a.action == "confirm":
        cfg["gates_confirmed"] = True
        paths.save_project_config(project, cfg)
        _out("gates marked as confirmed")
        return 0
    rid = _run_id(project, a.run)
    if a.action == "baseline":
        results, text = R.gates_baseline(project, rid)
        _out(text if results else "no gates configured → empty baseline recorded (RISK: no automated regression net)")
        return 0
    if a.action == "run":
        gates = cfg.get("gates") or []
        results = G.run_all(project, gates, paths.run_dir(project, rid) / "evidence", "adhoc", only=a.names or None)
        _out(G.format_results(results) if results else "no matching gates")
        return 0 if all(r["ok"] for r in results) else 1
    if a.action == "verify":
        comparison, text = R.gates_verify(project, rid)
        _out(text)
        return 0 if comparison["ok"] else 1
    if a.action == "last":
        rdir = paths.run_dir(project, rid)
        last = read_json(rdir / "gates-verify.json")
        if not isinstance(last, dict):
            _out("no `orkestra gate verify` result yet")
            return 1
        base = (read_json(rdir / "gates-baseline.json") or {}).get("results") or []
        _out(f"last verify at {last.get('at')}")
        _out(G.format_results(last.get("results") or [], last.get("comparison"), base))
        newest = R._newest_change_mtime(project, rdir)
        if newest and newest > float(last.get("ts", 0)):
            _out("WARNING: files changed after this verify — it is stale; the orchestrator must re-run it")
        return 0 if (last.get("comparison") or {}).get("ok") else 1
    raise OrkestraError(f"unknown gate action {a.action}")


def cmd_tdd(a: argparse.Namespace) -> int:
    cmd = split_remainder(a, list(a.command or []))
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    rec = R.tdd_record(project, rid, a.task, a.kind, cmd, getattr(a, "why", "") or "")
    want = "a FAILING test (red)" if a.kind == "red" else "PASSING tests (green)"
    _out(f"{a.task} tdd {a.kind}: exit {rec['exit']} → {'OK' if rec['ok'] else 'NOT OK — expected ' + want}")
    if not rec["ok"] or a.kind == "red":
        _out(rec["tail"])
    _out(f"log: {rec['log']}")
    return 0 if rec["ok"] else 1


def cmd_evidence(a: argparse.Namespace) -> int:
    cmd = split_remainder(a, list(a.command or []))
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    rec = R.run_evidence(project, rid, a.label, cmd)
    _out(f"evidence {a.label}: exit {rec['exit']} · cite this in verification.md: {rec['rel']}")
    _out(rec["tail"])
    return 0


def cmd_integrity(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    problems = E.integrity_problems(project, rid)
    _out("integrity check: " + ("OK (no removed tests, no skip/only markers, no new suppressions)" if not problems
                                else "FAIL\n  - " + "\n  - ".join(problems)))
    return 0 if not problems else 1


def cmd_report(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    if a.write:
        path = E.write_block(project, rid)
        _out(f"machine evidence block written into {path} (write your narrative around it; never edit the block)")
    else:
        _out(E.evidence_block(project, rid))
    return 0


def cmd_task(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    text = " ".join(a.text or []).strip()
    if a.action == "list":
        st = load_state(project, rid)
        for tid in sorted(st.get("tasks") or {}):
            t = st["tasks"][tid]
            _out(f"{tid} [{t.get('status')}] {t.get('title')} · files: {', '.join(t.get('files') or [])}")
        nt = _next_task(st)
        _out(f"next: {nt or '—'}")
        minors = [(tid, [n for n in (st["tasks"][tid].get("notes") or []) if str(n).lower().startswith("minor")])
                  for tid in sorted(st.get("tasks") or {})]
        minors = [(tid, ns) for tid, ns in minors if ns]
        if minors:
            _out("carried minors (triage them in the final review — they never reopen a task):")
            for tid, ns in minors:
                for n in ns:
                    _out(f"  {tid}: {n}")
        return 0
    if not a.task:
        raise OrkestraError("task id required")
    force = (getattr(a, "force", "") or "").strip()
    if a.action == "start":
        for w in R.task_start(project, rid, a.task, a.agent or "", force):
            _out(f"NOTE: {w}")
    elif a.action == "done":
        if not R.task_done(project, rid, a.task, text):
            _out(f"NOTE: {a.task} was already done — nothing changed.")
    elif a.action in ("block", "defer"):
        if not text:
            raise OrkestraError(f"orkestra task {a.action} {a.task} \"reason\"")
        R.task_set(project, rid, a.task, "blocked" if a.action == "block" else "deferred", text)
    elif a.action == "reopen":
        R.task_reopen(project, rid, a.task, text or force or "another round", force)
    elif a.action == "add":
        if not text:
            raise OrkestraError("orkestra task add FIX-R001 \"title\" [--no-tdd]")
        R.task_add(project, rid, a.task, text, "n/a" if a.no_tdd else "required")
    elif a.action == "note":
        R.task_note(project, rid, a.task, text)
    _out(f"{a.task}: {a.action} ok")
    if a.action in ("done", "defer", "reopen"):
        _out(_tempo(project, rid))
        for ln in _next_lines(project, load_state(project, rid), deep=False)[:1]:
            _out(ln)
    return 0


def cmd_next(a: argparse.Namespace) -> int:
    """The single next step, derived from state + evidence + review files (same answer every time it is asked)."""
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    st = load_state(project, rid)
    n = T.next_action(project, st, deep=True)
    fl = T.flags(project, st)
    if a.json:
        _out(json.dumps(dict(n, flags=fl), indent=2, ensure_ascii=False, default=str))
        return 0
    for ln in T.next_lines(n, fl):
        _out(ln)
    return 0


def cmd_agents(a: argparse.Namespace) -> int:
    """Background subagents the hooks registered for the run; `clear` drops records of agents that are gone."""
    from . import activity as A
    from .state import ledger
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    running, lost = T.live_agents(project, rid)
    if a.action == "clear":
        gone = A.agents_purge(project, rid)
        if gone:
            ledger(project, rid, f"pending subagent records cleared by CLI ({len(gone)}): "
                                 + ", ".join(f"{g.get('type')} {'/'.join(g.get('tasks') or [])}".strip() for g in gone[:8]))
        _out(f"cleared {len(gone)} pending subagent record(s)")
        for ln in _next_lines(project, load_state(project, rid), deep=False)[:1]:
            _out(ln)
        return 0
    if a.json:
        _out(json.dumps({"running": running, "lost": lost}, indent=2, ensure_ascii=False, default=str))
        return 0
    if not running and not lost:
        _out("no background subagent is registered as running")
    for tag, items in (("running", running), ("LOST? (no sign of life)", lost)):
        for g in items:
            _out(f"{tag}: {g.get('type')} {'/'.join(g.get('tasks') or [])} · {T.dur(g.get('age'))} · {g.get('desc') or ''}".rstrip(" ·"))
    if lost:
        _out("`orkestra agents clear` drops the lost records (use it only when those agents are really gone)")
    return 0


def cmd_tempo(a: argparse.Namespace) -> int:
    """Where the time went: phases, tasks, review rounds, measured dead wait (read-only)."""
    from . import activity as A
    project = _resolve_project(a.project)
    assert project is not None
    rid = a.run or project_active_run(project)
    if not rid:
        runs = sorted(p.name for p in paths.runs_dir(project).iterdir()) if paths.runs_dir(project).is_dir() else []
        if not runs:
            raise OrkestraError(f"no run in {project.name}")
        rid = runs[-1]
    st = load_state(project, rid)
    tm = T.timings(st)
    rows = A.read_all_rows(project, rid)
    dw = T.dead_wait_share(rows)
    cls = T.classify(st, A.read_rows(project, rid), A.agents_pending(project, rid))
    if a.json:
        _out(json.dumps({"run": rid, "timings": tm, "dead_wait": dw, "stall": cls, "flags": T.flags(project, st, rows[-200:])},
                        indent=2, ensure_ascii=False, default=str))
        return 0
    _out(f"{rid} · status {st['status']} · phase {st['phase']} · total {T.dur(tm['run'])}")
    _out("phases: " + " · ".join(f"{name} {T.dur(sec)}" for name, sec in tm["phases"].items()))
    if tm["tasks_total"]:
        _out(f"tasks: {tm['tasks_done']}/{tm['tasks_total']} done"
             + (f" · implement time per finished task {T.dur(tm['task_avg'])}" if tm.get("task_avg") else "")
             + (f" · median last round {T.dur(tm['task_last_round_median'])}" if tm.get("task_last_round_median") else "")
             + f" · review rejects {tm['reopens']} on {tm['tasks_reopened']} task(s)")
    if dw["window"]:
        _out(f"dead wait (subagent finished → main session's next action): {T.dur(dw['dead'])} of {T.dur(dw['window'])} "
             f"recorded = {dw['share'] * 100:.0f}% · {dw['count']} gaps · longest {T.dur(dw['longest'])}")
    _out(f"now: {cls['text']}" + (f" ({T.dur(cls['since'])})" if cls.get("since") else ""))
    for f in dict.fromkeys(T.flags(project, st, rows[-200:]) + cls.get("loops", [])):
        _out(f"flag: {f}")
    return 0


def cmd_watchdog(a: argparse.Namespace) -> int:
    from . import watchdog
    if a.launchd:
        plist = Path("~/Library/LaunchAgents/com.orkestra.watchdog.plist").expanduser()
        plist.parent.mkdir(parents=True, exist_ok=True)
        path_env = os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin")
        logs = paths.logs_dir()
        logs.mkdir(parents=True, exist_ok=True)
        plist.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.orkestra.watchdog</string>
  <key>ProgramArguments</key><array><string>{sys.executable}</string><string>{PLUGIN_ROOT / 'bin' / 'orkestra'}</string><string>watchdog</string><string>--quiet</string></array>
  <key>EnvironmentVariables</key><dict><key>PATH</key><string>{path_env}</string></dict>
  <key>StartInterval</key><integer>60</integer><key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{logs}/watchdog.out</string>
  <key>StandardErrorPath</key><string>{logs}/watchdog.err</string>
</dict></plist>
""")
        _out(f"wrote {plist}\nload it with:  launchctl load -w {plist}\n"
             f"(not needed when the bridge runs: it scans every minute by itself)")
        return 0
    return watchdog.main(once=True, as_json=a.json, dry_run=a.dry_run, quiet=a.quiet)


def cmd_brief(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    fdir = paths.feature_dir(project, rid)
    plan = read_text(fdir / "plan.md")
    spec = read_text(fdir / "spec.md")
    sec = PP.task_section(plan, a.task)
    if not sec:
        raise OrkestraError(f"{a.task} not found in plan.md")
    st = load_state(project, rid)
    ids = sorted(set(PP.ID_RE.findall(sec)))
    spec_lines = [ln for ln in spec.splitlines() if any(i in ln for i in ids)]
    constraints = PP.section(plan, "Genel kısıtlar", "Global constraints") or ""
    bounds = PP.section(spec, "Sınırlar", "Boundaries") or ""
    gates = ", ".join(f"{g['name']}=`{g['cmd']}`" for g in paths.project_config(project).get("gates") or [])
    _out(f"# Task brief — {a.task} · run {rid} · project {project.name} ({project})")
    _out(f"artifacts: {fdir}  (context.md has the code map; read only what you need)")
    if constraints.strip():
        _out("## Global constraints\n" + constraints.strip())
    if bounds.strip():
        _out("## Boundaries (spec)\n" + bounds.strip())
    _out("## Task\n" + sec.strip())
    if spec_lines:
        _out("## Referenced requirements\n" + "\n".join(spec_lines))
    _out(f"## Gates\n{gates or 'none'}")
    t = (st.get("tasks") or {}).get(a.task) or {}
    if t.get("notes"):
        _out("## Notes from earlier attempts\n" + "\n".join(f"- {n}" for n in t["notes"][-5:]))
    rv = T.task_review(fdir, a.task) if t.get("status") != "done" else {}
    if rv and (rv.get("verdict") == "FAIL" or float(rv.get("mtime") or 0) <= float(t.get("review_seen") or 0)):
        _out(f"## Review to fix (read it first; fix only the blocker/major rows of its latest round)\n{rv['path']}")
    _out(f"## Report file\n{fdir / 'tasks' / (a.task + '.report.md')}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    fdir = paths.feature_dir(project, rid)
    spec = read_text(fdir / "spec.md")
    if a.cmd == "plan":
        problems = PP.check_plan(spec, read_text(fdir / "plan.md"), project)
    elif a.cmd == "trace":
        ver = read_text(fdir / "verification.md")
        gate_names = [g["name"] for g in paths.project_config(project).get("gates") or []]
        problems = PP.check_trace(spec, ver) + E.verification_problems(project, rid, spec, ver, gate_names)
    elif a.cmd == "deps":
        problems = R.deps_problems(project, load_state(project, rid))
    elif a.cmd == "review":
        tri = read_text(fdir / "reviews" / "triage.md")
        problems = ["reviews/triage.md missing"] if not tri.strip() else \
            [f"open: {x}" for x in PP.open_triage_items(tri)]
    else:
        raise OrkestraError("unknown check")
    _out(f"{a.cmd} check: " + ("OK" if not problems else "FAIL\n  - " + "\n  - ".join(problems)))
    if a.cmd == "plan" and not problems:
        note = _large_plan_note(read_text(fdir / "plan.md"))
        if note:
            _out(note)
    return 0 if not problems else 1


LARGE_PLAN_TASKS = 20


def _large_plan_note(plan_text: str) -> str:
    n = len(PP.parse_tasks(plan_text))
    if n <= LARGE_PLAN_TASKS:
        return ""
    return (f"NOTE: LARGE PLAN — {n} tasks (> {LARGE_PLAN_TASKS}). One run of this size takes many hours and its context "
            f"grows with every task. In the approval summary, recommend splitting it into {max(2, round(n / 15))} runs by "
            f"capability (state which tasks go first); the user decides.")


def cmd_changes(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    items = S.changed_files(project, paths.run_dir(project, rid))
    if a.json:
        _out(json.dumps(items, indent=2))
    else:
        if not items:
            _out("no changes attributable to this run")
        for it in items:
            _out(f"{it['change']:9} {it['path']}")
        from . import hints
        books = hints.playbooks_for([it["path"] for it in items], project)
        if books:
            _out(f"playbooks: {', '.join(books)}  (`orkestra playbook <name>` — task-scope review: only the first; "
                 f"final review: all)")
    return 0


def cmd_diff(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    items = S.changed_files(project, paths.run_dir(project, rid))
    mods = [it["path"] for it in items if it["change"] != "created"]
    created = [it["path"] for it in items if it["change"] == "created"]
    if S.is_git_repo(project) and mods:
        args = ["git", "-C", str(project), "--no-pager", "diff"] + (["--stat"] if a.stat else []) + ["--"] + mods
        cp = subprocess.run(args, capture_output=True, text=True)
        _out(cp.stdout or "(no tracked diff)")
    for c in created:
        _out(f"new file: {c}")
    return 0


def cmd_rollback(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    rid = _run_id(project, a.run)
    actions = S.rollback(project, paths.run_dir(project, rid), dry_run=not a.yes, include_all=a.all)
    if not actions:
        _out("nothing to roll back")
        return 0
    run_items = [x for x in actions if x["scope"] == "run"]
    extra = [x for x in actions if x["scope"] == "extra"]
    _out(("APPLIED" if a.yes else "DRY RUN (nothing changed yet)") + f" — {len(actions)} file(s)")
    if run_items:
        _out("Changed by orkestra tools during the run:")
        for act in run_items:
            _out(f"- {act['path']}: {act['how']}")
    if extra:
        _out("Other differences since the run started (maybe yours, or from an undetected command) — "
             + ("included (--all):" if a.all else "left alone unless --all:"))
        for act in extra:
            _out(f"- {act['path']}: {act['how']}")
    if a.yes:
        saved = [x for x in actions if x.get("saved")]
        if saved:
            _out(f"Current contents were saved before overwriting: {Path(saved[0]['saved']).parent}")
        from .state import ledger
        ledger(project, rid, f"rollback applied ({len(actions)} file(s), all={a.all})")
    return 0


def cmd_memory(a: argparse.Namespace) -> int:
    project = _resolve_project(a.project)
    assert project is not None
    text = " ".join(a.text or []).strip()
    if a.action != "show":
        R.init_project(project)
    if a.action == "show":
        if not paths.memory_dir(project).is_dir():
            _out("memory not initialized for this project")
            return 0
        dg = M.digest(project, max_features=50)
        for k, v in dg.items():
            _out(f"--- {k}\n{v}")
        return 0
    rid = a.run or project_active_run(project) or "manual"
    if a.action == "feature-add":
        if not a.title or not a.summary:
            raise OrkestraError("orkestra memory feature-add --title ... --summary ... [--capabilities a,b]")
        caps = [c.strip() for c in (a.capabilities or "").split(",") if c.strip()]
        M.feature_add(project, rid, a.title, a.summary, caps)
    elif a.action in ("decision", "learn"):
        if not text:
            raise OrkestraError(f"orkestra memory {a.action} \"text\"")
        M.append_entry(project, "DECISIONS.md" if a.action == "decision" else "LEARNINGS.md", rid, text)
    _out(f"memory {a.action}: ok")
    return 0


HEADLESS_WAIT = ("HEADLESS: wherever the text below tells you to stop after dispatching subagents, do NOT end your turn "
                 "— this process exits with the turn and kills them. Wait inside the turn: `sleep 30; orkestra next` "
                 "until it no longer reports running subagents (the wait guard is off here). The turn ends only after "
                 "`orkestra await`, `orkestra block`, or when the run is done.")


def _open_run_state(a: argparse.Namespace):
    """State of the run this call belongs to, or None (playbooks are also read before / outside a run)."""
    try:
        project = _resolve_project(getattr(a, "project", None), required=False)
        if project is None:
            return None
        return load_state(project, _run_id(project, getattr(a, "run", None)))
    except (OrkestraError, OSError, ValueError, AssertionError):
        return None


def cmd_playbook(a: argparse.Namespace) -> int:
    name = a.name
    st = _open_run_state(a)
    lead = []
    if not name:
        if not st or st.get("phase") in (None, "done"):
            avail = sorted(p.stem for p in PLAYBOOKS.glob("*.md"))
            raise OrkestraError(f"no open run here, so there is no current phase. Name one: run, {', '.join(avail)}")
        name = str(st["phase"])
    elif st and st.get("phase") not in (None, "done") and name != st["phase"] and (PLAYBOOKS / f"phase-{name}.md").exists():
        lead.append(f"NOTE: run {st.get('run_id')} is in phase `{st['phase']}`, not `{name}`. This is the `{name}` playbook "
                    f"you asked for; the one to follow now is `orkestra playbook` (no argument).")
    if st and paths.is_headless(st) and st.get("phase") not in (None, "done"):
        lead.append(HEADLESS_WAIT)
    if lead:
        _out("\n".join(lead) + "\n")
    path = PLAYBOOKS / f"phase-{name}.md"
    if not path.exists():
        path = PLAYBOOKS / f"{name}.md"
    if name == "run":           # the run skill itself: how an engine without the Claude Code plugin gets its start
        text = read_text(PLUGIN_ROOT / "skills" / "run" / "SKILL.md")
        text = text.split("\n---\n", 1)[1] if text.startswith("---\n") and "\n---\n" in text else text
    elif not path.exists():
        avail = sorted(p.stem for p in PLAYBOOKS.glob("*.md"))
        raise OrkestraError(f"no playbook '{name}'. available: run, {', '.join(avail)}")
    else:
        text = read_text(path)
    if _ASKER == "cursor":      # the vocabulary header, then the document with Cursor's names
        sys.stdout.write(EN.with_notes(text, "cursor").rstrip("\n") + "\n")
        return 0
    _out(text)
    return 0


def cmd_run(a: argparse.Namespace) -> int:
    """Start a run from the terminal with the engine of your choice (default: claude). Opens that engine's own
    interactive UI in the project with the request submitted — questions and the plan approval happen there."""
    import shlex
    if _ASKER:
        raise OrkestraError("`orkestra run` opens an agent UI: use it from your terminal. Inside an agent session "
                            "start the run with the run skill (/orkestra:run).")
    project = _resolve_project(a.project)
    assert project is not None
    request = " ".join(a.request or []).strip()
    if len(request) < 3:
        raise OrkestraError('usage: orkestra run [--engine claude|cursor] [-p <proje>] "<istek>"')
    cfg = paths.global_config()
    try:
        engine = EN.normalize(a.engine, EN.default_engine(cfg))
    except EN.EngineError as exc:
        raise OrkestraError(str(exc))
    binary = EN.binary(engine, cfg)
    if not binary:
        raise OrkestraError(f"{engine} CLI not found on PATH" + (" (Cursor CLI: https://cursor.com/cli)" if engine == "cursor" else ""))
    if engine == "cursor":
        from . import cursorkit as CK
        if not CK.hooks_active(project):            # without them the agent would run unguarded
            raise OrkestraError("orkestra's hooks are not installed for Cursor — run `orkestra cursor install` once "
                                "(it keeps your own ~/.cursor/hooks.json entries), then try again.")
    plugin_dir = ""
    if engine == "copilot":
        plugin_dir = EN.copilot_plugin_dir(cfg)
        if not EN.copilot_hooks_active(plugin_dir):
            raise OrkestraError("Orkestra's Copilot hooks are not installed. Install the Orkestra plugin with "
                                "UserPromptSubmit, PreToolUse and PostToolUse hooks before starting a guarded Copilot run.")
    opts = dict(((cfg.get("engine") or {}).get(engine)) or {})
    if plugin_dir:
        opts["plugin_dir"] = plugin_dir
    approval_mode = a.approval_mode or ("manual" if intent.manual_approval_requested(request) else "autopilot")
    opts["approval_mode"] = approval_mode
    argv = EN.interactive_command(engine, binary, EN.run_prompt(engine, request), opts)
    if a.print:
        _out(f"cd {shlex.quote(str(project))} && " + " ".join(shlex.quote(x) for x in argv))
        return 0
    os.chdir(str(project))
    env = EN.child_env(engine)
    env.pop("ORKESTRA_HEADLESS", None)              # this is the interactive UI: the user answers in it
    env["ORKESTRA_APPROVAL_MODE"] = approval_mode
    os.execve(binary, argv, env)
    return 0  # pragma: no cover


def cmd_cursor(a: argparse.Namespace) -> int:
    """Cursor side: build the generated skills/subagents, install them and the hooks into ~/.cursor, or check."""
    from . import cursorkit as CK
    if _ASKER:
        raise OrkestraError("`orkestra cursor …` changes the Cursor configuration: run it from your terminal.")
    try:
        if a.action == "build":
            done = CK.build()
            _out(f"cursor assets: {len(done)} file(s) written" + (": " + ", ".join(done) if done else " (already current)"))
        elif a.action == "install":
            done = CK.install(copy=a.copy, dry=a.dry_run)
            for line in done:
                _out(("would " if a.dry_run else "") + line)
            _out("cursor: " + ("nothing to change" if not done else ("dry run — nothing written" if a.dry_run else
                 "installed. New Cursor chats (IDE and `agent`) load it; open ones need a new chat.")))
        elif a.action == "uninstall":
            done = CK.uninstall(dry=a.dry_run)
            for line in done:
                _out(("would " if a.dry_run else "") + line)
            _out("cursor: " + ("nothing installed" if not done else ("dry run" if a.dry_run else "removed")))
        if a.action in ("status", "install"):
            ok = True
            for good, label, detail in CK.status(paths.global_config()):
                ok = ok and good is not False
                _out(f"[{'i ' if good is None else ('OK' if good else '!!')}] {label}" + (f" — {detail}" if detail else ""))
            if a.action == "install":               # the install itself worked (or raised); the lines above say what
                return 0                            # else is still missing (Cursor CLI, PATH)
            return 0 if ok else 1
    except EN.EngineError as exc:
        raise OrkestraError(str(exc))
    return 0


def cmd_copilot(a: argparse.Namespace) -> int:
    """Copilot side: install/uninstall/status for hooks under ~/.copilot/plugins/orkestra."""
    from . import copilotkit as CK
    if _ASKER:
        raise OrkestraError("`orkestra copilot …` changes the Copilot configuration: run it from your terminal.")
    try:
        if a.action == "install":
            done = CK.install(dry=a.dry_run)
            for line in done:
                _out(("would " if a.dry_run else "") + line)
            _out("copilot: " + ("nothing to change" if not done else ("dry run — nothing written" if a.dry_run else
                 "installed. Restart Copilot / VS Code chats so they load the hooks.")))
        elif a.action == "uninstall":
            done = CK.uninstall(dry=a.dry_run)
            for line in done:
                _out(("would " if a.dry_run else "") + line)
            _out("copilot: " + ("nothing installed" if not done else ("dry run" if a.dry_run else "removed")))
        if a.action in ("status", "install"):
            ok = True
            for good, label, detail in CK.status(paths.global_config()):
                ok = ok and good is not False
                _out(f"[{'i ' if good is None else ('OK' if good else '!!')}] {label}" + (f" — {detail}" if detail else ""))
            if a.action == "install":
                return 0
            return 0 if ok else 1
    except EN.EngineError as exc:
        raise OrkestraError(str(exc))
    return 0


def _ask_yn(question: str, default: str = "y") -> bool:
    """Interactive yes/no for setup (same defaults shape as install.sh ask())."""
    hint = "Y/n" if default == "y" else "y/N"
    try:
        raw = input(f"{question} [{hint}] ").strip()
    except EOFError:
        raw = ""
    ans = (raw or default).lower()
    return ans[:1] in ("y", "e")


def _setup_detect() -> Dict[str, bool]:
    """Which host engines look present (Claude on PATH, Cursor/Copilot home dirs)."""
    from . import cursorkit as Cur
    from . import copilotkit as Cop
    return {
        "claude": shutil.which("claude") is not None,
        "cursor": Cur.cursor_home().is_dir(),
        "copilot": Cop.copilot_home().is_dir(),
    }


def _setup_install_claude() -> None:
    """Claude Code marketplace + plugin (repo root next to plugins/)."""
    root = PLUGIN_ROOT.parent.parent
    listed = subprocess.run(["claude", "plugin", "marketplace", "list"], capture_output=True, text=True, timeout=120)
    if listed.returncode == 0 and "orkestra-local" in (listed.stdout or ""):
        subprocess.run(["claude", "plugin", "marketplace", "update", "orkestra-local"], check=False, timeout=120)
    else:
        subprocess.run(["claude", "plugin", "marketplace", "add", str(root)], check=False, timeout=120)
    subprocess.run(["claude", "plugin", "install", "orkestra@orkestra-local", "-s", "user"], check=False, timeout=120)


def cmd_setup(a: argparse.Namespace) -> int:
    """Detect Claude/Cursor/Copilot and install Orkestra for each (ask, or --yes for all detected)."""
    from . import cursorkit as Cur
    from . import copilotkit as Cop
    if _ASKER:
        raise OrkestraError("`orkestra setup` changes host configuration: run it from your terminal.")
    # FR-003: PATH shim always (motorsuz / declined engines); agent refuse above skips this.
    Cur.ensure_path_shim()
    detected = _setup_detect()
    if not any(detected.values()):
        _out("setup: motorsuz — no Claude CLI, Cursor home, or Copilot home detected; nothing installed.")
        return 1
    yes = bool(getattr(a, "yes", False))
    if detected["claude"]:
        if yes or _ask_yn("Claude marketplace/plugin kurulsun mu?", "y"):
            _setup_install_claude()
            _out("setup: Claude marketplace/plugin done")
        else:
            _out("setup: Claude skipped")
    if detected["cursor"]:
        if yes or _ask_yn("Cursor skill, alt-ajan ve hooks kurulsun mu?", "y"):
            try:
                done = Cur.install()
            except EN.EngineError as exc:
                raise OrkestraError(str(exc))
            for line in done:
                _out(line)
            _out("setup: Cursor " + ("already current" if not done else "installed"))
        else:
            _out("setup: Cursor skipped")
    if detected["copilot"]:
        if yes or _ask_yn("Copilot hooks kurulsun mu?", "y"):
            try:
                done = Cop.install()
            except EN.EngineError as exc:
                raise OrkestraError(str(exc))
            for line in done:
                _out(line)
            _out("setup: Copilot " + ("already current" if not done else "installed"))
        else:
            _out("setup: Copilot skipped")
    return 0


def cmd_template(a: argparse.Namespace) -> int:
    path = TEMPLATES / a.name
    if not path.exists():
        path = TEMPLATES / f"{a.name}.md"
    if not path.exists():
        avail = sorted(str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.md"))
        raise OrkestraError(f"no template '{a.name}'. available: {', '.join(avail)}")
    _out(read_text(path))
    return 0


def cmd_watch(a: argparse.Namespace) -> int:
    from . import monitor
    project = _resolve_project(a.project) if a.project else None
    return monitor.watch(project, max(0.5, a.interval), a.once, a.all, a.json)


def cmd_dashboard(a: argparse.Namespace) -> int:
    from . import monitor
    project = _resolve_project(a.project) if a.project else None
    return monitor.dashboard(project, a.port, a.open)


def cmd_doctor(a: argparse.Namespace) -> int:
    ok = True

    def line(status: bool, label: str, detail: str = "") -> None:
        nonlocal ok
        ok = ok and status
        _out(f"[{'OK' if status else '!!'}] {label}" + (f" — {detail}" if detail else ""))

    def soft(found: bool, label: str, detail: str = "") -> None:
        _out(f"[{'OK' if found else '--'}] {label}" + (f" — {detail}" if detail else ""))

    py = sys.version_info
    line(py >= (3, 9), "python >= 3.9", platform.python_version())
    line(shutil.which("git") is not None, "git", shutil.which("git") or "not found")
    soft(shutil.which("claude") is not None, "claude",
         shutil.which("claude") or "not found (optional — Cursor/Copilot hooks also count)")
    for tool in ("node", "npx", "uv", "uvx", "gh", "jq"):
        soft(shutil.which(tool) is not None, tool, shutil.which(tool) or "not found (optional)")
    ws = paths.workspace_root()
    line(ws.is_dir(), "workspace root", str(ws))
    home = paths.orkestra_home()
    paths.ensure_global_config()
    line(home.is_dir(), "orkestra home", str(home))
    line((PLUGIN_ROOT / "hooks" / "hooks.json").exists(), "plugin root", str(PLUGIN_ROOT))
    # hook self-test: a forbidden command must be denied, a harmless one allowed
    disp = PLUGIN_ROOT / "hooks" / "dispatch.py"
    try:
        ev = {"hook_event_name": "PreToolUse", "session_id": "doctor", "tool_name": "Bash",
              "tool_input": {"command": "git push --force origin main"}, "cwd": str(Path.cwd())}
        cp = subprocess.run([sys.executable, str(disp), "PreToolUse"], input=json.dumps(ev), capture_output=True,
                            text=True, timeout=20)
        line('"deny"' in cp.stdout, "hook self-test (deny force-push)", cp.stdout.strip()[:80] or cp.stderr.strip()[:80])
    except Exception as exc:  # pragma: no cover
        line(False, "hook self-test", str(exc))
    from . import cursorkit as CK
    claude_ready = shutil.which("claude") is not None
    cursor_ready = CK.hooks_active()
    copilot_dir = EN.copilot_plugin_dir(paths.global_config())
    copilot_ready = EN.copilot_hooks_active(copilot_dir)
    soft(cursor_ready, "cursor hooks", "active" if cursor_ready else "not installed")
    soft(copilot_ready, "copilot hooks", "active" if copilot_ready else "not installed")
    engines_ready = sum((claude_ready, cursor_ready, copilot_ready))
    line(engines_ready >= 1, "engine ready",
         f"{engines_ready} ready" if engines_ready else "none — install Claude, `orkestra cursor install`, or Copilot hooks")
    if claude_ready:
        try:
            cp = subprocess.run(["claude", "mcp", "list"], capture_output=True, text=True, timeout=30,
                                stdin=subprocess.DEVNULL)
            out = cp.stdout.lower()
            for name in ("context7", "playwright", "serena"):
                soft(name in out, f"MCP {name}", "configured" if name in out else "not configured (optional)")
        except Exception:
            soft(False, "claude mcp list", "could not run")
    _out("doctor: " + ("all required checks passed" if ok else "some required checks FAILED"))
    return 0 if ok else 1


# ====================================================================== parser

HANDLERS = {
    "context": cmd_context, "init": cmd_init, "start": cmd_start, "resume": cmd_resume, "status": cmd_status,
    "phase": cmd_phase, "await": cmd_status_change, "block": cmd_status_change, "unblock": cmd_status_change,
    "pause": cmd_status_change, "abort": cmd_status_change, "gate": cmd_gate, "tdd": cmd_tdd, "task": cmd_task,
    "brief": cmd_brief, "plan": cmd_check, "trace": cmd_check, "deps": cmd_check, "review": cmd_check,
    "changes": cmd_changes, "diff": cmd_diff, "rollback": cmd_rollback, "memory": cmd_memory,
    "playbook": cmd_playbook, "template": cmd_template, "doctor": cmd_doctor,
    "watch": cmd_watch, "dashboard": cmd_dashboard,
    "evidence": cmd_evidence, "integrity": cmd_integrity, "report": cmd_report,
    "next": cmd_next, "tempo": cmd_tempo, "watchdog": cmd_watchdog, "agents": cmd_agents,
    "run": cmd_run, "cursor": cmd_cursor, "copilot": cmd_copilot, "setup": cmd_setup,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    a = parse_argv(parser, list(sys.argv[1:] if argv is None else argv))
    if not a.cmd:
        parser.print_help()
        return 0
    if a.cmd == "version":
        _out(f"orkestra {__version__}")
        return 0
    try:
        return HANDLERS[a.cmd](a)
    except OrkestraError as exc:
        sys.stderr.write(f"orkestra: {exc}\n")
        return 2
    except KeyboardInterrupt:  # pragma: no cover
        return 130
