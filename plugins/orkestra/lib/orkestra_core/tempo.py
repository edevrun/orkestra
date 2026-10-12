"""Tempo: where a run stands, what the single next step is, how long things take, and whether it is stalled.

Read-only and derived from files that already exist (state.json, evidence, review files, activity.jsonl,
agents.json) — there is no second source of truth. `orkestra next`, `orkestra tempo`, `orkestra watch`,
`orkestra watchdog` and the hooks all call the same functions, so a run interrupted at any sub-step resumes with the
same deterministic answer: nothing is skipped, nothing is done twice.
"""
from __future__ import annotations

import datetime as _dt
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import activity as A
from . import intent
from . import paths
from . import planparse as PP
from .state import PHASES
from .util import read_json, read_text

# thresholds live in ~/.orkestra/config.json → "watchdog" (defaults: paths.DEFAULT_GLOBAL_CONFIG)
WD_DEFAULTS: Dict[str, Any] = {"dead_wait_seconds": 90, "hung_command_seconds": 900, "gate_command_seconds": 1800,
                               "silent_seconds": 600, "user_wait_notify_seconds": 300, "loop_repeats": 3,
                               "agent_max_minutes": 60}
MAX_REVIEW_ROUNDS = 3                    # the 3rd FAIL goes to the user (run skill: "same task fails review 3×")
WAIT_CMD_RE = re.compile(r"\bsleep\s+\d|time\.sleep\(|while\s+time\.time\(\)")
OPEN_TASK = ("pending", "in_progress", "blocked")


def wd_cfg(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    out = dict(WD_DEFAULTS)
    user = ((cfg if cfg is not None else paths.global_config()).get("watchdog") or {})
    out.update({k: v for k, v in user.items() if v is not None})
    return out


def _ts(iso: Any) -> Optional[float]:
    try:
        return _dt.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


def dur(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(max(0, seconds))
    if s < 90:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    return f"{s // 3600}h{s % 3600 // 60:02d}m"


def dur_tr(seconds: Optional[float]) -> str:
    if seconds is None:
        return "?"
    s = int(max(0, seconds))
    if s < 90:
        return f"{s} sn"
    if s < 3600:
        return f"{s // 60} dk"
    return f"{s // 3600} sa {s % 3600 // 60} dk"


# ---------------------------------------------------------------- per-task sub-step (derived, never stored)

def reopen_count(t: Dict[str, Any]) -> int:
    return sum(1 for n in (t.get("notes") or []) if str(n).startswith("reopened"))


def task_review(fdir: Path, tid: str) -> Dict[str, Any]:
    """Newest review file of a task (reviews/T001.review.md, reviews/T001.md, reviews/task-T001-review.md …)."""
    pat = re.compile(rf"(?<![A-Za-z0-9]){re.escape(tid)}(?![A-Za-z0-9])", re.I)
    best: Optional[Tuple[float, Path]] = None
    rdir = fdir / "reviews"
    if rdir.is_dir():
        for p in rdir.glob("*.md"):
            if pat.search(p.stem):
                try:
                    m = p.stat().st_mtime
                except OSError:
                    continue
                if best is None or m > best[0]:
                    best = (m, p)
    if best is None:
        return {}
    text = read_text(best[1])
    return {"path": str(best[1]), "name": best[1].name, "verdict": PP.verdict(text), "mtime": best[0],
            "minors": len(re.findall(r"^\s*\|[^\n]*\|\s*minor\s*\|", text, re.M | re.I))}


def task_step(project: Path, st: Dict[str, Any], tid: str, deep: bool = False) -> Dict[str, Any]:
    """started · red · green_no_red · green_stale · review_pending · review_fail_rN · review_pass · done …"""
    t = (st.get("tasks") or {}).get(tid) or {}
    fdir = paths.feature_dir(project, st["run_id"])
    status = t.get("status") or "pending"
    rounds = reopen_count(t)
    out: Dict[str, Any] = {"task": tid, "status": status, "round": rounds + 1, "step": status}
    if status != "in_progress":
        if status == "pending" and rounds:
            out["step"] = "reopened"
        return out
    tdd = t.get("tdd") == "required"
    red_ok = bool((t.get("red") or {}).get("ok"))
    green = t.get("green") or {}
    green_ok = bool(green.get("ok"))
    notes = t.get("notes") or []
    if notes and str(notes[-1]).upper().startswith("BLOCKED") and not (tdd and green_ok):
        out.update(step="blocked_note", note=str(notes[-1])[:160])     # the implementer gave up: a decision is needed
        return out
    if tdd and not green_ok:
        out["step"] = "red" if red_ok else "started"
        return out
    if tdd and not red_ok:
        out["step"] = "green_no_red"
        return out
    if not tdd:
        # the report must belong to THIS round: after a reopen the round-1 report is still on disk
        try:
            fresh = (fdir / "tasks" / f"{tid}.report.md").stat().st_mtime >= (_ts(t.get("started")) or 0.0)
        except OSError:
            fresh = False
        if not fresh:
            out["step"] = "started"
            return out
    green_at = _ts(green.get("at")) or _ts(t.get("started")) or 0.0
    if deep and tdd:
        for rel in t.get("files") or []:
            try:
                if (project / rel).stat().st_mtime > green_at + 2:
                    out.update(step="green_stale", file=rel, cmd=green.get("cmd"))
                    return out
            except OSError:
                continue
    rv = task_review(fdir, tid)
    out["review"] = rv.get("name")
    # a review already applied by `task reopen` (review_seen) belongs to an earlier round
    if not rv or rv.get("verdict") is None or float(rv.get("mtime") or 0) <= float(t.get("review_seen") or 0):
        out["step"] = "review_pending"
    elif rv["verdict"] in ("PASS", "PASS_WITH_NOTES"):       # the same two verdicts `task reopen` treats as a pass
        out.update(step="review_pass", minors=rv.get("minors") or 0)
    elif rv["verdict"] == "FAIL":
        out["step"] = f"review_fail_r{rounds + 1}"
    else:
        out.update(step="review_unclear", verdict=rv["verdict"])
    return out


def next_task(st: Dict[str, Any]) -> Optional[str]:
    tasks = st.get("tasks") or {}
    for tid in sorted(tasks):
        t = tasks[tid]
        if t.get("status") == "pending" and all((tasks.get(d) or {}).get("status") in ("done", "deferred")
                                                for d in t.get("depends") or []):
            return tid
    return None


# ---------------------------------------------------------------- background subagents: running or lost?

def agents_quiet(rows: List[Dict[str, Any]], pending: List[Dict[str, Any]], now: float,
                 wd: Dict[str, Any]) -> Optional[float]:
    """Seconds since any subagent showed life, when that is longer than silent_seconds — else None.

    A working subagent writes a row for every tool call. Registered agents with no row and no open command for that
    long are lost (killed, or their session crashed): waiting for them would park the run."""
    if not pending:
        return None
    if any(agent != "main" for agent, _, _ in _running_commands(rows)):
        return None
    last = max([float(a.get("at") or 0) for a in pending]
               + [float(r.get("ts") or 0) for r in rows if (r.get("agent") or "main") != "main"])
    gap = now - last
    return gap if gap > float(wd.get("silent_seconds") or 600) else None


def live_agents(project: Path, run_id: str, now: Optional[float] = None, wd: Optional[Dict[str, Any]] = None,
                rows: Optional[List[Dict[str, Any]]] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(running, lost) — the registered subagents, split by sign of life. The activity log is read only when
    something is registered at all."""
    now = time.time() if now is None else now
    wd = wd or wd_cfg()
    pending = A.agents_pending(project, run_id, max_age=float(wd["agent_max_minutes"]) * 60, now=now)
    if not pending:
        return [], []
    rows = A.read_rows(project, run_id) if rows is None else rows
    if agents_quiet(rows, pending, now, wd) is None:
        return pending, []
    return [], pending


# ---------------------------------------------------------------- the single next step

def _n(action: str, text: str, why: str = "", cmd: str = "", **extra: Any) -> Dict[str, Any]:
    d = {"action": action, "text": text, "why": why, "cmd": cmd}
    d.update(extra)
    return d


def _task_action(project: Path, st: Dict[str, Any], tid: str, deep: bool) -> Dict[str, Any]:
    s = task_step(project, st, tid, deep)
    step, p = s["step"], f"-p {project}"
    small = st.get("size") in ("XS", "S")
    base = {"task": tid, "step": step}
    if step == "started":
        return _n("dispatch", f"{tid}: no evidence yet → dispatch orkestra:implementer with the output of `orkestra brief {tid} {p}` "
                              f"(same brief as before — do not start another task for it)",
                  "task is in progress, no red/green recorded", f"orkestra brief {tid} {p}", **base)
    if step == "blocked_note":
        return _n("ask_user", f"{tid}: the implementer reported {s.get('note')} → decide from spec/context or ask the user; "
                              f"record it with `orkestra task note {tid} \"decision: …\" {p}` and dispatch the implementer "
                              f"again with that decision (or `orkestra task block {tid} \"<why>\" {p}`)",
                  "the task's last note is BLOCKED — sending the same brief again would fail the same way", **base)
    if step == "review_unclear":
        return _n("dispatch", f"{tid}: {s.get('review')} ends with VERDICT: {s.get('verdict')} — only PASS, PASS_WITH_NOTES "
                              f"or FAIL count → ask the reviewer (SendMessage) to finish it with one of them",
                  "the review verdict is not one the CLI accepts", **base)
    if step == "red":
        return _n("dispatch", f"{tid}: red recorded, green missing → implementer continues from GREEN with the same brief "
                              f"(`orkestra brief {tid} {p}`); the red test must not change",
                  "failing-first evidence exists; implementation or green run is missing", f"orkestra brief {tid} {p}", **base)
    if step == "green_no_red":
        return _n("cli", f"{tid}: green without failing-first evidence → `orkestra task reopen {tid} --force \"red evidence missing\" {p}`, "
                         f"then the implementer redoes red → green", "TDD task: `task done` will refuse without red",
                  f"orkestra task reopen {tid} --force \"red evidence missing\" {p}", **base)
    if step == "green_stale":
        return _n("cli", f"{tid}: {s.get('file')} changed after the green run → re-run `orkestra tdd green {tid} {p} -- {s.get('cmd') or '<same command>'}`",
                  "green evidence is older than the code it proves", f"orkestra tdd green {tid} {p} -- {s.get('cmd') or ''}".strip(), **base)
    if step == "review_pending":
        if small:
            return _n("cli", f"{tid}: green recorded → `orkestra task done {tid} {p} \"final review covers it (XS/S)\"`",
                      "XS/S runs may skip the per-task review", f"orkestra task done {tid} {p} \"final review covers it (XS/S)\"", **base)
        return _n("dispatch", f"{tid}: green recorded, no review of this round → dispatch orkestra:reviewer (scope: task {tid}; "
                              f"it writes reviews/{tid}.review.md)", "evidence is in; the task review is the only missing step", **base)
    if step.startswith("review_fail"):
        rnd = s["round"]
        if rnd >= MAX_REVIEW_ROUNDS:
            return _n("ask_user", f"{tid}: review FAILED {rnd} times → stop; explain the trade-off to the user (AskUserQuestion), "
                                  f"then `orkestra task reopen {tid} --force \"<the user's ruling>\" {p}` or `orkestra task defer {tid} \"<why>\" {p}`",
                      f"round limit ({MAX_REVIEW_ROUNDS}) reached — the user decides", **base)
        return _n("cli", f"{tid}: review FAIL (round {rnd}) → `orkestra task reopen {tid} \"review round {rnd}\" {p}` → "
                         f"`orkestra task start {tid} {p}` → implementer with the brief + {s.get('review')}",
                  "the verdict on file is FAIL and has not been applied", f"orkestra task reopen {tid} \"review round {rnd}\" {p}", **base)
    if step == "review_pass":
        minors = int(s.get("minors") or 0)
        note = f"review: PASS ({minors} minor carried to final triage)" if minors else "review: PASS"
        return _n("cli", f"{tid}: review PASS → `orkestra task done {tid} {p} \"{note}\"`"
                         + (" — minors do NOT reopen the task; they are triaged in the final review" if minors else ""),
                  "the verdict on file is PASS and has not been applied", f"orkestra task done {tid} {p} \"{note}\"", **base)
    if step == "blocked":
        notes = (st["tasks"][tid].get("notes") or ["?"])
        return _n("ask_user", f"{tid} is blocked ({str(notes[-1])[:120]}) → decide from spec/context, or ask the user; then "
                              f"`orkestra task start {tid} {p}`", "a blocked task needs a decision", **base)
    return _n("cli", f"{tid}: `orkestra task start {tid} {p}` → `orkestra brief {tid} {p}` → dispatch orkestra:implementer",
              "next task in dependency order", f"orkestra task start {tid} {p}", **base)


def _open_tasks_action(project: Path, st: Dict[str, Any], deep: bool) -> Optional[Dict[str, Any]]:
    tasks = st.get("tasks") or {}
    in_prog = sorted(t for t, v in tasks.items() if v.get("status") == "in_progress")
    if in_prog:
        act = _task_action(project, st, in_prog[0], deep)
        if len(in_prog) > 1:
            act["also"] = [task_step(project, st, t, deep) for t in in_prog[1:6]]
        return act
    nt = next_task(st)
    if nt:
        return _task_action(project, st, nt, deep)
    blocked = sorted(t for t, v in tasks.items() if v.get("status") == "blocked")
    if blocked:
        return _task_action(project, st, blocked[0], deep)
    stuck = sorted(t for t, v in tasks.items() if v.get("status") == "pending")
    if stuck:
        return _n("ask_user", f"{stuck[0]} cannot start: its dependencies are not done/deferred — fix the plan order or defer it",
                  "dependency deadlock")
    return None


def next_action(project: Path, st: Dict[str, Any], deep: bool = True, now: Optional[float] = None) -> Dict[str, Any]:
    """The one thing to do now. deep=False (hooks) only reads small files; deep=True (CLI) also runs the real
    `phase next` preconditions so the answer is never 'phase next' when the CLI would refuse it."""
    now = time.time() if now is None else now
    project = Path(project)
    rid, phase, status = st["run_id"], st.get("phase", "intake"), st.get("status")
    fdir, rdir, p = paths.feature_dir(project, rid), paths.run_dir(project, rid), f"-p {project}"
    head = {"run": rid, "phase": phase, "status": status}

    def done(d: Dict[str, Any]) -> Dict[str, Any]:
        d.update(head)
        return d

    if status in ("done", "aborted"):
        return done(_n("none", f"run is {status} — nothing to do"))
    if status == "paused":
        return done(_n("cli", f"run is paused → `orkestra resume {p}` (only when the user wants to continue)", "paused by the user or a topic change",
                       f"orkestra resume {p}"))
    if status == "blocked":
        return done(_n("wait_user", f"BLOCKED: {(st.get('blocked') or {}).get('reason') or '?'} — needs the user; after it is "
                                    f"resolved: `orkestra unblock {p}`", "blocked runs are never continued automatically"))
    autopilot_user_wait = st.get("approval_mode", "autopilot") == "autopilot"
    if status == "awaiting_user" and not autopilot_user_wait:
        return done(_n("wait_user", f"WAITING FOR THE USER: {(st.get('awaiting') or {}).get('question') or '?'} — never answer "
                                    f"on their behalf", "a question or approval is open"))
    pending, lost = live_agents(project, rid, now=now)
    if pending:
        who = ", ".join(f"{A.short_agent(a.get('type') or '')}"
                        + (f" {'/'.join(a.get('tasks') or [])}" if a.get("tasks") else "") + f" {dur(a.get('age'))}" for a in pending[:4])
        if paths.is_headless(st):
            # `claude -p` exits with its turn and takes its subagents with it: here the wait happens inside the turn
            return done(_n("wait_agents", f"{len(pending)} subagent(s) still running ({who}) → headless run: do NOT end the "
                                          f"turn (the process would exit and kill them). Wait for their reports inside this "
                                          f"turn; do not re-dispatch.",
                           "background subagents report back on their own", agents=pending[:8]))
        return done(_n("wait_agents", f"{len(pending)} subagent(s) still running ({who}) → END YOUR TURN now; each hand-back "
                                      f"arrives as a message and resumes you. Do not poll, sleep or re-dispatch.",
                       "background subagents report back on their own", agents=pending[:8]))
    if lost:
        head["lost_agents"] = [{"type": a.get("type"), "tasks": a.get("tasks") or [], "age": a.get("age")} for a in lost[:8]]

    spec, plan = read_text(fdir / "spec.md"), read_text(fdir / "plan.md")
    act: Optional[Dict[str, Any]] = None
    if phase == "intake":
        if "## Sınıflandırma" not in read_text(fdir / "request.md"):
            act = _n("work", "classify the request: append `## Sınıflandırma` to request.md (`orkestra playbook intake`)",
                     "request.md has no classification yet")
    elif phase == "discover":
        size = st.get("size") or "M"
        need = intent.min_context_chars(size)
        if len(read_text(fdir / "context.md").strip()) < need:
            if size == "XS":
                act = _n("work", "XS fast track: read 1–3 files yourself and write context.md "
                                 f"(≥{need} chars); skip scouts (`orkestra playbook discover`)",
                         "context.md is missing or too thin")
            elif size == "S":
                act = _n("work", "S fast track: optional 0–1 scout OR write context.md yourself "
                                 f"(≥{need} chars), then continue (`orkestra playbook discover`)",
                         "context.md is missing or too thin")
            else:
                act = _n("dispatch", "dispatch the scouts in ONE message (+ researcher if third-party APIs), then merge their notes "
                                     "into context.md (`orkestra playbook discover`)", "context.md is missing or too thin")
        elif not (rdir / "gates-baseline.json").exists():
            act = _n("cli", f"`orkestra gate baseline {p}`", "the baseline gates are not recorded", f"orkestra gate baseline {p}")
    elif phase == "clarify":
        if not spec.strip():
            if st.get("approval_mode", "autopilot") == "autopilot":
                act = _n("work", "Autopilot: infer conservative defaults from the request, codebase and project memory; record "
                                 "each material assumption in `spec.md` → `## Netleştirmeler`, then continue without asking the user.",
                         "spec.md does not exist")
            else:
                act = _n("work", "ask only the outcome-changing questions (AskUserQuestion, ≤ 4), then write spec.md with "
                                 "`## Netleştirmeler` (`orkestra playbook clarify`)", "spec.md does not exist")
        elif not PP.has_section(spec, "Netleştirmeler", "Clarifications") or not PP.spec_ids(spec):
            act = _n("work", ("Autopilot: finish spec.md with FR-###/SC-### ids, a `## Netleştirmeler` section and "
                               "conservative assumptions; do not ask the user." if autopilot_user_wait else
                               "finish spec.md: FR-###/SC-### ids and a `## Netleştirmeler` section"), "spec.md is incomplete")
    elif phase == "plan":
        critic = PP.verdict(read_text(fdir / "reviews" / "plan-critic.md"))
        size = st.get("size") or "M"
        if not plan.strip():
            if size in ("XS", "S"):
                act = _n("work", f"{size}: write a short plan.md yourself (1–3 tasks), then `orkestra plan check` "
                                 "(`orkestra playbook plan`)", "plan.md does not exist")
            else:
                act = _n("dispatch", "dispatch orkestra:architect to write plan.md (`orkestra playbook plan`)", "plan.md does not exist")
        elif not intent.skips_plan_critic(size) and critic is None:
            act = _n("dispatch", "dispatch orkestra:plan-critic (it writes reviews/plan-critic.md)", "the plan has no critic verdict")
        elif not intent.skips_plan_critic(size) and critic == "FAIL":
            act = _n("dispatch", "plan-critic FAILED → send its blockers to the architect, then run the critic again "
                                 "(max 3 rounds, then ask the user)", "reviews/plan-critic.md ends with VERDICT: FAIL")
    elif phase == "approve":
        appr = read_json(rdir / "approval.json")
        if not isinstance(appr, dict):
            if st.get("approval_mode", "autopilot") == "autopilot":
                act = _n("cli", f"Autopilot: the plan and critic passed. Run `orkestra phase next {p}` to record the hash-bound "
                                 "policy approval and continue.", "no policy approval is recorded", f"orkestra phase next {p}")
            else:
                act = _n("ask_user", "show the plan summary and ask for approval — ONE AskUserQuestion with header `Plan onayı` and "
                                     "nothing else in that call.", "no approval is recorded")
        elif appr.get("decision") != "approve":
            act = _n("work", "the user asked for changes: apply them to spec/plan, `orkestra plan check`, then ask for approval "
                             "again", f"last decision: {appr.get('decision')} ({str(appr.get('detail') or '')[:80]})")
    elif phase == "implement":
        act = _open_tasks_action(project, st, deep)
        hist = st.get("history") or []
        last = hist[-1] if hist else {}
        if act is None and str(last.get("note") or "").startswith("back:"):
            tasks_now = st.get("tasks") or {}
            mark = last.get("mark")
            if isinstance(mark, dict):          # exact: a task was added or reopened after the phase went back
                worked = len(tasks_now) > int(mark.get("tasks") or 0) \
                    or sum(reopen_count(v) for v in tasks_now.values()) > int(mark.get("reopens") or 0)
            else:                               # runs started before 1.4.0 have no mark: compare times
                back_at = _ts(last.get("at")) or 0.0
                worked = any((_ts(v.get("started")) or 0.0) > back_at or (_ts(v.get("done_at")) or 0.0) > back_at
                             for v in tasks_now.values())
            if not worked:      # came back to fix something, but no task carries the fix: `phase next` would ping-pong
                act = _n("cli", f"back in implement ({str(last.get('note'))[6:90]}) and every task is done → give the fix a "
                                f"task: `orkestra task add FIX-V001 \"<what to fix>\" {p}` (or `orkestra task reopen <TID> "
                                f"--force \"<why>\" {p}`), then start → implementer → review → done",
                         "the phase was moved back, but nothing has been reopened or added since",
                         f"orkestra task list {p}")
    elif phase == "verify":
        act = _open_tasks_action(project, st, deep)
        last = read_json(rdir / "gates-verify.json")
        ver = PP.verdict(read_text(fdir / "verification.md"))
        if act is None and not isinstance(last, dict):
            act = _n("cli", f"`orkestra gate verify {p}`", "the full gates have not run after implementation",
                     f"orkestra gate verify {p}")
        elif act is None and not (last.get("comparison") or {}).get("ok"):
            act = _n("cli", f"gate REGRESSION → `orkestra phase back implement --reason \"<gate> regressed\" {p}`, fix the cause "
                            f"(never the test), then verify again", "the last `gate verify` is not green",
                     f"orkestra gate last {p}")
        elif act is None and ver is None:
            act = _n("dispatch", "dispatch orkestra:verifier (it writes verification.md)", "gates are green; verification.md is missing")
        elif act is None and ver not in ("PASS", "PASS_WITH_NOTES"):
            act = _n("work", "verification FAILED → fix the failing ids through an implementer "
                             f"(`orkestra phase back implement --reason \"verification\" {p}`), then verify again",
                 "verification.md does not end with VERDICT: PASS or PASS_WITH_NOTES")
    elif phase == "review":
        act = _open_tasks_action(project, st, deep)
        if act is None:
            names = [x.name.lower() for x in (fdir / "reviews").glob("*.md")] if (fdir / "reviews").is_dir() else []
            has_final = any("final" in n and "adversary" not in n for n in names)
            has_adv = any("adversary" in n for n in names)
            tri = read_text(fdir / "reviews" / "triage.md")
            if not has_final or (not intent.skips_adversary(st.get("size") or "") and not has_adv):
                act = _n("dispatch", "dispatch orkestra:reviewer (scope: final → reviews/final-review.md)"
                                     + ("" if intent.skips_adversary(st.get("size") or "") else
                                        " and orkestra:adversary (→ reviews/adversary.md) in ONE message"),
                         "the final review has not been written")
            elif not tri.strip():
                act = _n("work", "triage every finding yourself → reviews/triage.md (`orkestra template triage`); include the "
                                 "minors carried from task reviews (`orkestra task list`)", "reviews/triage.md is missing")
            else:
                items = PP.open_triage_items(tri)
                if items:
                    act = _n("work", f"open triage items: {', '.join(items[:6])} → for each `fix`: `orkestra task add FIX-R00n "
                                     f"\"<title>\" {p}` → start → implementer → done; then mark the row done",
                             "triage has fix rows that are not done")
    elif phase == "deliver":
        if len(read_text(fdir / "report.md").strip()) < 300:
            act = _n("work", "write report.md (`orkestra template report`), then `orkestra report evidence --write`",
                     "report.md is missing or too short")
        elif rid not in read_text(paths.memory_dir(project) / "FEATURES.md"):
            act = _n("cli", f"`orkestra memory feature-add {p} --title \"…\" --summary \"…\" --capabilities \"…\"`",
                     "memory/FEATURES.md has no entry for this run")
    if act is None:
        if phase == "done":
            return done(_n("none", "run complete"))
        target = PHASES[PHASES.index(phase) + 1]
        problems: List[str] = []
        if deep:
            from . import runs as R           # late import: runs imports the heavy evidence/snapshot modules
            try:
                problems = R.preconditions(project, st, target)
            except Exception as exc:          # never let a broken artifact hide the next step
                problems = [f"precondition check failed: {exc}"]
        if problems:
            act = _n("work", f"before {phase} → {target}: {problems[0]}"
                             + (f" (+{len(problems) - 1} more: `orkestra phase check {p}`)" if len(problems) > 1 else ""),
                     "`orkestra phase next` would refuse", f"orkestra phase check {p}", problems=problems[:8])
        else:
            act = _n("cli", f"`orkestra phase next {p}` ({phase} → {target})", "this phase's outputs are complete",
                     f"orkestra phase next {p}")
    return done(act)


def next_lines(n: Dict[str, Any], flags: Optional[List[str]] = None) -> List[str]:
    """At most 3 lines for the model."""
    out = [f"next [{n.get('phase')}{' · ' + n['task'] + ' · ' + n['step'] if n.get('task') else ''}]: {n.get('text')}"]
    if n.get("also"):
        out.append("also in progress: " + ", ".join(f"{s['task']} ({s['step']})" for s in n["also"]))
    lost = n.get("lost_agents") or []
    if lost:
        who = ", ".join(sorted({A.short_agent(str(a.get("type") or "")) for a in lost}))
        out.append(f"note: {len(lost)} subagent record(s) ignored ({who}: no sign of life for {dur(min(a.get('age') or 0 for a in lost))}) "
                   f"— presumed lost; `orkestra agents clear` removes them")
    if flags and len(out) < 3:
        out.append("flags: " + " | ".join(flags[:3]))
    elif n.get("why") and len(out) < 3:
        out.append(f"why: {n['why']}")
    return out[:3]


# ---------------------------------------------------------------- timing (state + ledger times only)

def timings(st: Dict[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    hist = [(h.get("to"), _ts(h.get("at"))) for h in (st.get("history") or []) if _ts(h.get("at"))]
    closed = st.get("status") in ("done", "aborted")
    end = (_ts(st.get("updated")) or now) if closed else now
    phases: Dict[str, float] = {}
    for i, (name, at) in enumerate(hist):
        nxt = hist[i + 1][1] if i + 1 < len(hist) else end
        if name and name != "done":
            phases[name] = phases.get(name, 0.0) + max(0.0, float(nxt or end) - float(at or end))
    tasks = st.get("tasks") or {}
    done = [t for t in tasks.values() if t.get("status") in ("done", "deferred")]
    durs = [d for d in ((_ts(t.get("done_at")) or 0) - (_ts(t.get("started")) or 0) for t in done
                        if t.get("done_at") and t.get("started")) if d > 0]
    reopened = [tid for tid, t in tasks.items() if reopen_count(t)]
    start = _ts(st.get("created")) or (hist[0][1] if hist else now)
    return {"run": max(0.0, end - float(start or end)), "phase": st.get("phase"),
            "phase_elapsed": max(0.0, end - float(hist[-1][1])) if hist else 0.0, "phases": phases,
            "tasks_done": len(done), "tasks_total": len(tasks),
            "task_avg": (phases.get("implement", 0.0) / len(done)) if done and phases.get("implement") else None,
            "task_last_round_median": sorted(durs)[len(durs) // 2] if durs else None,
            "tasks_reopened": len(reopened), "reopens": sum(reopen_count(t) for t in tasks.values())}


def overlap(tasks: Dict[str, Any], tid: str, others: List[str]) -> List[Tuple[str, List[str]]]:
    mine = set((tasks.get(tid) or {}).get("files") or [])
    out = []
    for o in others:
        shared = sorted(mine & set((tasks.get(o) or {}).get("files") or []))
        if shared:
            out.append((o, shared))
    return out


def flags(project: Path, st: Dict[str, Any], rows: Optional[List[Dict[str, Any]]] = None,
          wd: Optional[Dict[str, Any]] = None) -> List[str]:
    """Things the orchestrator must resolve before doing more of the same (each says what to do)."""
    wd = wd or wd_cfg()
    out: List[str] = []
    tasks = st.get("tasks") or {}
    for tid in sorted(tasks):
        t = tasks[tid]
        if t.get("status") in OPEN_TASK and reopen_count(t) >= MAX_REVIEW_ROUNDS - 1:
            out.append(f"{tid}: review round {reopen_count(t) + 1} — if it fails again, ask the user instead of reopening")
    in_prog = sorted(t for t, v in tasks.items() if v.get("status") == "in_progress")
    for i, tid in enumerate(in_prog):
        for other, shared in overlap(tasks, tid, in_prog[i + 1:]):
            out.append(f"{tid} and {other} are in progress and share {shared[0]} — run them one after the other")
    if rows:
        out.extend(loop_flags(rows, int(wd.get("loop_repeats") or 3)))
    return out


def loop_flags(rows: List[Dict[str, Any]], repeats: int = 3) -> List[str]:
    out: List[str] = []
    recent = rows[-120:]
    denies: Dict[Tuple[str, str], int] = {}
    for r in recent:
        if r.get("kind") == "deny":
            key = (str(r.get("agent") or "main"), str(r.get("what") or "")[:90])
            denies[key] = denies.get(key, 0) + 1
    for (agent, what), n in denies.items():
        if n >= repeats:
            out.append(f"{A.short_agent(agent)} hit the same guard {n}×: {what[:70]} — change the approach, do not retry")
    last_cmd: Dict[str, Tuple[str, int]] = {}
    for r in recent:
        if r.get("kind") == "tool" and r.get("tool") == "Bash":
            agent, what = str(r.get("agent") or "main"), str(r.get("what") or "")
            prev = last_cmd.get(agent)
            last_cmd[agent] = (what, prev[1] + 1) if prev and prev[0] == what else (what, 1)
        elif r.get("kind") == "tool":
            last_cmd.pop(str(r.get("agent") or "main"), None)
    for agent, (what, n) in last_cmd.items():
        if n >= repeats and what:
            out.append(f"{A.short_agent(agent)} ran the same command {n}× in a row: {what[:60]} — "
                       f"`orkestra playbook debugging` before another try")
    return out


def tempo_line(project: Path, st: Dict[str, Any], now: Optional[float] = None,
               rows: Optional[List[Dict[str, Any]]] = None) -> str:
    """One line appended to `task done` / `phase next` output: elapsed, pace, reject rate, flags."""
    tm = timings(st, now)
    parts = [f"run {dur(tm['run'])}", f"{tm['phase']} {dur(tm['phase_elapsed'])}"]
    if tm["tasks_total"]:
        avg = f", avg {dur(tm['task_avg'])}/task" if tm.get("task_avg") else ""
        parts.append(f"tasks {tm['tasks_done']}/{tm['tasks_total']}{avg}")
        if tm["reopens"]:
            parts.append(f"review rejects {tm['reopens']} on {tm['tasks_reopened']} task(s)")
    fl = flags(project, st, rows)
    return "tempo: " + " · ".join(parts) + (" · FLAGS: " + " | ".join(fl[:2]) if fl else "")


# ---------------------------------------------------------------- stall classification (read-only)

def _running_commands(rows: List[Dict[str, Any]]) -> List[Tuple[str, float, str]]:
    """Per agent: a Bash call that started and has no `end` row yet."""
    open_: Dict[str, Tuple[str, float, str]] = {}
    for r in rows:
        agent = str(r.get("agent") or "main")
        key = str(r.get("aid") or agent)            # two implementers are two agents: one stopping must not close
        if r.get("tool") == "Bash" and r.get("kind") == "tool":       # the other one's running command
            open_[key] = (agent, float(r.get("ts") or 0), str(r.get("what") or ""))
        elif r.get("kind") in ("end", "deny") and r.get("tool") == "Bash":
            open_.pop(key, None)
        elif r.get("kind") == "agent_done":
            if open_.pop(key, None) is None:        # rows written before agent ids were recorded are keyed by type
                open_.pop(agent, None)
        elif agent == "main" and r.get("kind") in ("idle", "wait_agents", "user"):
            open_.pop("main", None)
    return list(open_.values())


def classify(st: Dict[str, Any], rows: List[Dict[str, Any]], pending: Optional[List[Dict[str, Any]]] = None,
             now: Optional[float] = None, wd: Optional[Dict[str, Any]] = None,
             state_mtime: Optional[float] = None) -> Dict[str, Any]:
    """cls: 0 working · 1 waiting for the user (legitimate) · 2 dead wait · 3 hung command · 4 silent session.
    `loops` (class 5) is reported next to any of them."""
    now = time.time() if now is None else now
    wd = wd or wd_cfg()
    pending = pending or []
    status = st.get("status")
    loops = loop_flags(rows, int(wd.get("loop_repeats") or 3))
    tasks = st.get("tasks") or {}
    loops += [f"{tid}: {reopen_count(t)} review rounds" for tid, t in sorted(tasks.items())
              if t.get("status") in OPEN_TASK and reopen_count(t) >= MAX_REVIEW_ROUNDS]

    def res(cls: int, key: str, since: float, tr: str) -> Dict[str, Any]:
        return {"cls": cls, "key": key, "since": int(max(0, now - since)) if since else None, "text": tr, "loops": loops}

    if status in ("done", "aborted"):
        return res(0, "closed", 0, "kapalı")
    if status == "awaiting_user":
        at = _ts((st.get("awaiting") or {}).get("at")) or 0
        return res(1, "user", at, f"kullanıcı bekleniyor — {(st.get('awaiting') or {}).get('question') or 'soru'}")
    if status == "blocked":
        return res(1, "blocked", _ts((st.get("blocked") or {}).get("at")) or 0,
                   f"bloke — {(st.get('blocked') or {}).get('reason') or ''}")
    if status == "paused":
        return res(1, "paused", 0, "duraklatıldı")
    q = A.pending_question(rows)
    if q:
        ts = next((float(r.get("ts") or 0) for r in reversed(rows) if r.get("tool") == "AskUserQuestion"), 0)
        return res(1, "user", ts, f"kullanıcı bekleniyor — açık soru ({q})")
    main_rows = [r for r in rows if (r.get("agent") or "main") == "main" and r.get("kind") not in ("end", "nudge")]
    main_last = main_rows[-1] if main_rows else None
    state_ts = float(state_mtime or 0)
    if main_last and main_last.get("kind") == "idle" and state_ts <= float(main_last.get("ts") or 0):
        return res(1, "turn_ended", float(main_last.get("ts") or 0), "tur bitti — kullanıcının mesajı bekleniyor")
    for agent, ts, what in _running_commands(rows):
        limit = float(wd["gate_command_seconds"] if re.search(r"\borkestra\s+(\S+\s+)*(gate|tdd|evidence)\b", what)
                      else wd["hung_command_seconds"])
        if now - ts > limit and not (agent == "main" and WAIT_CMD_RE.search(what)):
            return res(3, "hung", ts, f"asılı komut ({A.short_agent(agent)}, {dur_tr(now - ts)}): {what[:80]} — komut "
                                      f"takılmış ya da oturum komut sırasında kapanmış olabilir")
    quiet = agents_quiet(rows, pending, now, wd)
    if pending and quiet is None:
        who = ", ".join(A.short_agent(str(a.get("type") or "")) for a in pending[:4])
        return res(0, "agents", float(pending[0].get("at") or now), f"alt-ajan çalışıyor: {who}")
    done_ts = max([float(r.get("ts") or 0) for r in rows if r.get("kind") == "agent_done"] or [0.0])
    if done_ts:
        progressed = any(float(r.get("ts") or 0) > done_ts and r.get("kind") in ("tool", "answer", "deny", "result", "user")
                         and not (r.get("tool") == "Bash" and WAIT_CMD_RE.search(str(r.get("what") or "")))
                         for r in main_rows)
        main_waiting = any(a == "main" and WAIT_CMD_RE.search(w) for a, _, w in _running_commands(rows))
        got_report = any(float(r.get("ts") or 0) > done_ts and r.get("kind") == "handback" for r in main_rows)
        if not progressed and (main_waiting or not got_report) and now - done_ts > float(wd["dead_wait_seconds"]):
            why = "ana oturum bekleme komutunda" if main_waiting else "alt-ajan raporu ana oturuma ulaşmadı"
            return res(2, "dead_wait", done_ts, f"ölü bekleme — alt-ajan {dur_tr(now - done_ts)} önce bitti, {why}")
    activity_ts = max([float(r.get("ts") or 0) for r in rows] or [0.0])
    last_ts = max(activity_ts, state_ts)
    if last_ts and now - last_ts > float(wd["silent_seconds"]) and not _running_commands(rows):
        lost = f" ({len(pending)} alt-ajan kaydı var ama {dur_tr(quiet)} hiçbir iz yok)" if pending and quiet else ""
        if state_ts > activity_ts:
            hook_activity = f"son hook aktivitesi {dur_tr(now - activity_ts)} önce" if activity_ts else "henüz hook aktivitesi yok"
            return res(4, "silent", last_ts, f"sessiz oturum — run state {dur_tr(now - state_ts)} önce güncellendi; "
                       f"{hook_activity}; canlılık hook kayıtlarından doğrulanamıyor{lost}")
        return res(4, "silent", last_ts, f"sessiz oturum — {dur_tr(now - last_ts)} kayıt yok; oturum kapanmış ya da çökmüş "
                                         f"olabilir{lost}")
    return res(0, "working", last_ts, "çalışıyor")


def dead_wait_share(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Measured dead wait: time between a subagent's stop and the main session's next real action."""
    rows = sorted(rows, key=lambda r: float(r.get("ts") or 0))
    if len(rows) < 2:
        return {"window": 0.0, "dead": 0.0, "share": 0.0, "count": 0, "longest": 0.0}
    window = float(rows[-1]["ts"]) - float(rows[0]["ts"])
    dead, count, longest = 0.0, 0, 0.0
    waiting_since: Optional[float] = None
    running = 0
    for r in rows:
        kind, agent, ts = r.get("kind"), (r.get("agent") or "main"), float(r.get("ts") or 0)
        if kind == "result" and str(r.get("what") or "").rstrip().endswith("async_launched"):
            running += 1
        elif kind == "agent_done":
            running = max(0, running - 1)
            if running == 0 and waiting_since is None:
                waiting_since = ts
        elif agent == "main" and waiting_since is not None and kind in ("tool", "answer", "result", "deny") \
                and not (r.get("tool") == "Bash" and WAIT_CMD_RE.search(str(r.get("what") or ""))):
            gap = ts - waiting_since
            if gap > 20:
                dead += gap
                count += 1
                longest = max(longest, gap)
            waiting_since = None
    return {"window": window, "dead": dead, "share": (dead / window) if window else 0.0, "count": count, "longest": longest}
