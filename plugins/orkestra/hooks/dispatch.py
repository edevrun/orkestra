#!/usr/bin/env python3
"""Single entry point for all Orkestra hooks:  python3 dispatch.py <EventName>  (event JSON on stdin).

Design rules
  * Deterministic, stdlib only, fast (<50 ms typical). No network.
  * Deny decisions are explicit JSON (permissionDecision=deny) with an actionable reason.
  * Any internal error FAILS OPEN (exit 0) and is logged to ~/.orkestra/logs/hooks.log,
    so a bug here can never brick the user's Claude Code session.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

from orkestra_core import activity, guards, intent, paths, runs, snapshot, tempo  # noqa: E402
from orkestra_core.engines import short_sid  # noqa: E402
from orkestra_core import shellparse as sp  # noqa: E402
from orkestra_core import state as ST  # noqa: E402
from orkestra_core.util import now_iso, resolve_path  # noqa: E402

BIND_RE = re.compile(r"^\[orkestra:bind\] project=(.+?) run=(\S+)\s*$", re.M)
UNBIND_RE = re.compile(r"^\[orkestra:unbind\] project=(.+?) run=(\S+)\s*$", re.M)
MAX_STOP_BLOCKS_PER_PROMPT = 2
STATUS_LINE_RE = re.compile(r"\b(?:STATUS|VERDICT):\s*[A-Z_]+[^\n]{0,80}")


GUARD_DEADLINE_SECONDS = 8          # hooks.json gives each hook 15 s


class _Deadline(Exception):
    pass


def _with_deadline(seconds: int, fn):
    """Run fn(); raise _Deadline if it takes longer (SIGALRM: macOS/Linux, main thread — otherwise no limit)."""
    import signal
    if not hasattr(signal, "SIGALRM"):
        return fn()

    def on_alarm(*_):
        raise _Deadline()

    try:
        old = signal.signal(signal.SIGALRM, on_alarm)
    except ValueError:                              # not the main thread
        return fn()
    signal.alarm(seconds)
    try:
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))


def log(msg: str) -> None:
    try:
        d = paths.logs_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hooks.log", "a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} {msg}\n")
    except Exception:
        pass


def context_out(event_name: str, text: str) -> None:
    if text:
        emit({"hookSpecificOutput": {"hookEventName": event_name, "additionalContext": text}})


# ---------------------------------------------------------------- helpers

def binding_for(ev: dict):
    b = ST.session_binding(ev.get("session_id") or "")
    if b:
        ST.touch_session(ev.get("session_id") or "")        # heartbeat (lease for takeover / watchdog)
    return b


def next_hint(project: Path, st: dict, rows=None) -> str:
    """The deterministic next step for hook messages (cheap: small files only). Empty on any error."""
    try:
        fresh = ST.load_state(project, st["run_id"])
        lines = tempo.next_lines(tempo.next_action(project, fresh, deep=False), tempo.flags(project, fresh, rows))
        return "\n".join(lines[:2])
    except Exception as exc:
        log(f"next_hint failed: {exc}")
        return ""


def release_hook_wait(project: Path, st: dict, why: str, min_age: float = 0.0) -> dict:
    """`awaiting_user` set by the AskUserQuestion hook ends with that tool call. If the question was dismissed (no
    PostToolUse) the status used to stay: no hint, no nudge, `watch` kept saying "waiting for you"."""
    aw = st.get("awaiting") or {}
    if st.get("status") != "awaiting_user" or aw.get("source") != "hook":
        return st
    at = tempo._ts(aw.get("at")) or 0.0
    if time.time() - at < min_age:                  # tool calls sent in the same message as the question
        return st
    try:
        runs.set_status(project, st["run_id"], "active", why)
        return ST.load_state(project, st["run_id"])
    except Exception as exc:
        log(f"release_hook_wait failed: {exc}")
        return st


def run_summary(project: Path, st: dict) -> str:
    tasks = st.get("tasks") or {}
    done = sum(1 for t in tasks.values() if t.get("status") in ("done", "deferred"))
    prog = f" · tasks {done}/{len(tasks)}" if tasks else ""
    hint = next_hint(project, st)
    return (f"[orkestra] Active run {st['run_id']} \"{st.get('title', '')}\" in {project} — phase {st['phase']}, "
            f"status {st['status']}{prog}. State lives in files, not in your memory: run `orkestra context -p {project}` "
            f"and `orkestra playbook {st['phase']}` before continuing." + (f"\n{hint}" if hint else ""))


# ---------------------------------------------------------------- events

def on_session_start(ev: dict) -> None:
    b = binding_for(ev)
    if b:
        if ev.get("source") != "compact":
            # startup / resume / clear: this is a new process (or a wiped context) — background subagents of the old
            # one died with it, so their registry entries would only park the run ("waiting for subagents")
            gone = activity.agents_purge(Path(b["project"]), b["run_id"], session=ev.get("session_id") or "")
            if gone:
                who = ", ".join(sorted({activity.short_agent(str(g.get("type") or "")) for g in gone}))
                activity.record(Path(b["project"]), b["run_id"], ev, "note",
                                f"oturum yeniden başladı: {len(gone)} bekleyen alt-ajan kaydı düşürüldü ({who})")
                try:
                    ST.ledger(Path(b["project"]), b["run_id"], f"session restart ({ev.get('source') or '?'}): dropped "
                                                                 f"{len(gone)} pending subagent record(s) ({who})")
                except Exception as exc:
                    log(f"ledger note failed: {exc}")
        context_out("SessionStart", run_summary(Path(b["project"]), b["state"]))
        return
    project = paths.find_project_root(ev.get("cwd") or os.getcwd())
    if project:
        rid = ST.project_active_run(project)
        if rid:
            try:
                st = ST.load_state(project, rid)
            except ST.OrkestraError:
                return
            context_out("SessionStart",
                        f"[orkestra] This project has an open orkestra run {rid} (phase {st['phase']}, status "
                        f"{st['status']}). If the user wants to continue it, use /orkestra:resume. Do not start a new run "
                        f"in this project until it is finished or aborted. This session is NOT driving that run: edits "
                        f"made here are outside its plan, tests and rollback — avoid changing files it is working on, "
                        f"and tell the user if they ask for such an edit.")


def on_user_prompt(ev: dict) -> None:
    sid = ev.get("session_id") or ""
    prompt = ev.get("prompt") or ""
    synthetic = activity.is_synthetic_prompt(prompt)
    if synthetic:
        # subagent hand-backs / task notifications are model output: they never grant git permission, never approve
        # or reject a plan and never count as "the user replied". The turn they start is not the user's message
        # either, so a permission given earlier ends here — explicitly, with or without prompt ids ("commit only
        # when asked in that message").
        ST.clear_grant(sid)
    else:
        cats = intent.git_grants(prompt)
        if cats:
            ST.write_grant(sid, ev.get("prompt_id"), cats, prompt)
        else:
            ST.clear_grant(sid)
    b = binding_for(ev)
    if not b:
        return
    st = b["state"]
    project = Path(b["project"])
    if synthetic:
        m = re.search(r'from="([^"]{1,80})"', prompt[:400])
        key = STATUS_LINE_RE.search(prompt)
        label = "görev bildirimi" if prompt.lstrip().startswith("<task-notification") else \
            f"alt-ajan raporu ({(m.group(1) if m else '?')[:12]})"
        activity.record(project, st["run_id"], ev, "handback", label + (f" — {key.group(0)}" if key else ""))
        if m and not prompt.lstrip().startswith("<task-notification"):
            activity.agent_handed_back(project, st["run_id"], m.group(1))
        st = release_hook_wait(project, st, "a subagent report arrived: the question is no longer on screen")
        if st.get("status") == "active":
            hint = next_hint(project, st, activity.read_rows(project, st["run_id"]))
            if hint:
                context_out("UserPromptSubmit", "[orkestra] The message above is a subagent report / task notification, "
                                                "not the user (it approves nothing and grants nothing).\n" + hint)
        return
    activity.record(project, st["run_id"], ev, "user", " ".join(prompt.split())[:160])
    if st.get("phase") == "approve" and not prompt.strip().startswith("/"):
        decision = intent.approval(prompt)
        if decision:
            runs.record_approval(project, st["run_id"], decision, "prompt", prompt[:300])
            if decision == "approve":
                context_out("UserPromptSubmit", f"[orkestra] User APPROVED the plan for {st['run_id']} (recorded by hook). "
                                                f"Run `orkestra phase next` and continue with `orkestra playbook implement`.")
            else:
                context_out("UserPromptSubmit", f"[orkestra] User did NOT approve the plan for {st['run_id']}; treat the "
                                                f"message as change requests: revise spec/plan (plan-critic again), then ask "
                                                f"for approval again.")
            return
    if st.get("status") == "awaiting_user":
        # the user answered: the run is active again (Stop-hook continuation applies)
        runs.set_status(project, st["run_id"], "active", "user replied")


def git_context(ev: dict) -> dict:
    """Branch facts some git rules need (only computed when the command could use them)."""
    cmd = (ev.get("tool_input") or {}).get("command") or ""
    if ev.get("tool_name") != "Bash" or not re.search(r"\b(checkout|switch|push)\b", cmd):
        return {}
    cwd = ev.get("cwd") or os.getcwd()
    ctx: dict = {}
    try:
        import subprocess
        def g(*a):
            return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, timeout=5)
        cp = g("rev-parse", "--abbrev-ref", "HEAD")
        ctx["current_branch"] = cp.stdout.strip() if cp.returncode == 0 else None
        cp = g("for-each-ref", "--format=%(refname:short)", "refs/heads")
        ctx["branches"] = cp.stdout.split() if cp.returncode == 0 else []
        cp = g("status", "--porcelain", "--untracked-files=no")
        ctx["clean"] = cp.returncode == 0 and not cp.stdout.strip()
    except Exception:
        pass
    return ctx


def on_pre_tool(ev: dict) -> None:
    cfg = paths.global_config()
    b = binding_for(ev)
    grants = ST.current_grant(ev.get("session_id") or "", ev.get("prompt_id"))
    ctx = git_context(ev)
    if ctx:
        ev = dict(ev, _git_ctx=ctx)
    try:
        d = _with_deadline(GUARD_DEADLINE_SECONDS, lambda: guards.pre_tool_decision(ev, cfg, b, grants))
    except _Deadline:
        # A hook that runs into Claude Code's timeout lets the call through UNCHECKED. Refusing is the safe answer.
        d = guards.deny("complex", f"This call could not be analysed within {GUARD_DEADLINE_SECONDS} s. Split the "
                                   f"command, or put it in a script file under the project and run that.")
    if d.denied:
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                     "permissionDecisionReason": f"[orkestra:{d.guard}] {d.reason}"}})
        log(f"DENY [{d.guard}] session={short_sid(ev.get('session_id', ''))} agent={ev.get('agent_type', '-')} "
            f"tool={ev.get('tool_name')} input={json.dumps(ev.get('tool_input'), ensure_ascii=False)[:300]}")
        if b:
            activity.record(Path(b["project"]), b["run_id"], ev, "deny", f"[{d.guard}] {d.reason}"[:200])
        return
    if b:
        activity.record(Path(b["project"]), b["run_id"], ev, "tool")
    # backups before the first modification of a file during a run (enables exact rollback)
    if not b:
        return
    st = b["state"]
    if not ev.get("agent_type"):
        if ev.get("tool_name") == "AskUserQuestion":
            if st.get("status") == "active":
                heads = activity.summarize(ev, Path(b["project"])) or "soru"
                try:    # the run is now legitimately waiting for the user (watch / watchdog / bridge read this)
                    runs.set_status(Path(b["project"]), b["run_id"], "awaiting_user", heads, source="hook")
                except Exception as exc:
                    log(f"awaiting_user not set: {exc}")
        elif st.get("status") == "awaiting_user":
            # the main session is calling another tool: the question call has returned (answered or dismissed)
            st = release_hook_wait(Path(b["project"]), st, "the question was closed without an answer", min_age=10.0)
    if st.get("phase") not in guards.CODE_PHASES:
        return
    project = Path(b["project"])
    rdir = paths.run_dir(project, b["run_id"])
    ev = guards.normalize_event(ev)          # MCP editors (e.g. Serena) → Write/Bash semantics
    cwd = ev.get("cwd") or str(project)
    targets = []
    tool = ev.get("tool_name")
    if tool in guards.WRITE_TOOLS:
        targets = [str(resolve_path(p, cwd)) for p in guards._tool_paths(ev)]
    elif tool == "Bash":
        segs = sp.segments((ev.get("tool_input") or {}).get("command") or "")
        for seg, scwd in guards.with_cwd(segs, cwd):
            targets += [t for t in guards.bash_write_targets(seg, scwd) if not t.endswith("/*")]
    for t in list(dict.fromkeys(targets))[:400]:
        try:
            snapshot.backup_before_edit(project, rdir, Path(t))
        except Exception as exc:  # backups must never block work
            log(f"backup failed for {t}: {exc}")


def on_post_tool(ev: dict) -> None:
    if ev.get("tool_name") == "SendMessage":
        # registered here, not in PreToolUse: PostToolUse only fires when the call went through
        b = binding_for(ev)
        if b and not ev.get("agent_type"):
            ti = ev.get("tool_input") or {}
            resp = ev.get("tool_response")
            text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False, default=str)
            failed = (isinstance(resp, dict) and (resp.get("success") is False or resp.get("error") or resp.get("is_error"))) \
                or bool(re.match(r"\s*\W{0,3}(error|failed|no (such )?(agent|recipient)|unknown|not found)", text[:200], re.I))
            if not failed:
                activity.agent_messaged(Path(b["project"]), b["run_id"], str(ti.get("to") or ti.get("recipient") or ""),
                                        str(ti.get("summary") or ti.get("message") or ""), session=ev.get("session_id") or "")
        return
    if ev.get("tool_name") in ("Agent", "Task"):
        b = binding_for(ev)
        if b:
            info = activity.launch_info(ev)
            if info:        # background launch: the report arrives later as a message; SubagentStop closes the entry
                activity.agent_launched(Path(b["project"]), b["run_id"], info, session=ev.get("session_id") or "")
                activity.record(Path(b["project"]), b["run_id"], ev, "result", f"← {info['type']}: async_launched")
            else:
                activity.record(Path(b["project"]), b["run_id"], ev, "result", agent_result_line(ev))
        return
    if (ev.get("tool_name") or "").startswith("mcp__"):
        ev = guards.normalize_event(ev)
        if ev.get("tool_name") not in guards.WRITE_TOOLS:
            return
    tool = ev.get("tool_name")
    sid = ev.get("session_id") or ""
    if tool == "Bash":
        resp = ev.get("tool_response")
        text = ""
        if isinstance(resp, dict):
            text = str(resp.get("stdout") or "") + "\n" + str(resp.get("stderr") or "")
        elif isinstance(resp, str):
            text = resp
        cur = ST.session_binding(sid)
        if cur:   # marks the end of a Bash call (liveness: "a command is still running" vs "idle")
            activity.record(Path(cur["project"]), cur["run_id"], ev, "end", "✓")
        for m in UNBIND_RE.finditer(text):
            if cur and cur.get("run_id") == m.group(2) and Path(cur.get("project", "")) == Path(m.group(1)):
                ST.unbind_session(sid)
        warnings = []
        for m in BIND_RE.finditer(text):
            bproj, brun = Path(m.group(1)), m.group(2)
            others = [s for s in ST.run_sessions(bproj, brun) if s["id"] != ST._safe(sid)]
            ST.bind_session(sid, bproj, brun, engine=str(ev.get("_engine") or ""))
            activity.record(bproj, brun, ev, "bind", f"oturum bağlandı ({short_sid(sid)})")
            if others:
                lease = int((paths.global_config().get("watchdog") or {}).get("lease_seconds") or 120)
                o = others[0]
                try:
                    if o["age"] < lease:
                        ST.ledger(bproj, brun, f"WARNING second session {short_sid(sid)} bound while {short_sid(o['id'])} is active "
                                               f"({o['age']}s ago)")
                        warnings.append(
                            f"[orkestra] WARNING: another session ({short_sid(o['id'])}) drove run {brun} {o['age']} s ago and "
                            f"may still be working. Two sessions must never drive one run (double execution). Do "
                            f"NOT continue here unless the user confirms the other window is closed.")
                    else:
                        ST.ledger(bproj, brun, f"session {short_sid(sid)} took over (previous {short_sid(o['id'])} silent "
                                               f"{o['age'] // 60} min)")
                except Exception as exc:
                    log(f"takeover note failed: {exc}")
        if warnings:                                # ONE JSON object per hook call (two would be unparseable)
            context_out("PostToolUse", "\n".join(dict.fromkeys(warnings)))
        return
    b = binding_for(ev)
    if not b:
        return
    project = Path(b["project"])
    if tool in guards.WRITE_TOOLS:
        cwd = ev.get("cwd") or str(project)
        for p in guards._tool_paths(ev):
            snapshot.record_touch(project, paths.run_dir(project, b["run_id"]), resolve_path(p, cwd))
        return
    if tool == "AskUserQuestion":
        activity.record(project, b["run_id"], ev, "answer", " | ".join(extract_answers(ev))[:160] or "(cevaplandı)")
        recorded = False
        if b["state"].get("phase") == "approve" and is_approval_question(ev):
            decision, appr, others = approval_from_answers(ev)
            if decision:
                extra = "; ".join(f"{q or 'ek soru'} = {a}" for q, a in others)[:400]
                runs.record_approval(project, b["run_id"], decision, "AskUserQuestion",
                                     " | ".join(appr) + (f" · other answers: {extra}" if extra else ""))
                recorded = True
                if decision == "approve":
                    context_out("PostToolUse", "[orkestra] Plan approval recorded by hook."
                                + (f" The same call also returned: {extra} — NOT part of the approval. If that changes "
                                   f"scope, update spec/plan first (a changed plan needs approval again); otherwise run "
                                   f"`orkestra phase next`." if extra else " Run `orkestra phase next`."))
                else:
                    context_out("PostToolUse", "[orkestra] The user did not approve as-is: apply their feedback to "
                                               "spec/plan, re-check, and ask for approval again.")
        if not recorded and b["state"].get("status") == "awaiting_user":
            runs.set_status(project, b["run_id"], "active", "user answered")


def agent_result_line(ev: dict) -> str:
    """One line from a finished subagent's reply: its STATUS/VERDICT line if any, else the first line."""
    texts: list = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "text" and isinstance(v, str):
                    texts.append(v)
                else:
                    walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)
        elif isinstance(obj, str) and not texts:
            texts.append(obj)

    walk(ev.get("tool_response"))
    lines = [ln.strip() for ln in "\n".join(texts).splitlines() if ln.strip()]
    sub = (ev.get("tool_input") or {}).get("subagent_type") or "agent"
    key = [ln for ln in lines if re.search(r"\b(STATUS|VERDICT):", ln)]
    line = (key[-1] if key else (lines[0] if lines else "(boş yanıt)"))
    return f"← {sub}: {line}"[:200]


def _is_approval_text(text: str) -> bool:
    t = (text or "").lower()
    return "onay" in t or "approv" in t


def answer_pairs(ev: dict) -> list:
    """[(question text or "", answer)] from the tool OUTPUT only; tolerant to format differences."""
    pairs: list = []

    def flat(v) -> str:
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x) for x in v)
        return str(v)

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "answers" and isinstance(v, dict):
                    pairs.extend((str(q), flat(a)) for q, a in v.items())
                elif k == "answers" and isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            q = item.get("question") or item.get("header") or ""
                            a = item.get("answer") if "answer" in item else next(
                                (x for kk, x in item.items() if kk not in ("question", "header") and isinstance(x, (str, list))), "")
                            pairs.append((str(q), flat(a)))
                        else:
                            pairs.append(("", flat(item)))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    walk(ev.get("tool_response"))
    if not pairs:
        raw = ev.get("tool_response")
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        text = text.replace('\\"', '"')
        pairs = [(q, a) for q, a in re.findall(r'"([^"\n]{1,300})"\s*=\s*"([^"\n]*)"', text)]
        if not pairs:
            pairs = [("", a) for a in extract_answers(ev)]
    return pairs


def approval_from_answers(ev: dict):
    """(decision | None, approval answers, other (question, answer) pairs).

    Only the answer to the approval question decides. Extra questions asked in the same call (e.g. an "Ek kapsam"
    multi-select) used to turn a clear "Onayla" into a reject — they are now passed along as context instead."""
    pairs = answer_pairs(ev)
    if not pairs:
        return None, [], []
    qs = [q for q in ((ev.get("tool_input") or {}).get("questions") or []) if isinstance(q, dict)]
    approval_keys = set()
    for q in qs:
        if _is_approval_text(f"{q.get('header', '')} {q.get('question', '')}"):
            approval_keys.update(str(q.get(k) or "").strip().lower() for k in ("question", "header"))
    approval_keys.discard("")
    appr = [(q, a) for q, a in pairs if q and (q.strip().lower() in approval_keys or _is_approval_text(q))]
    if not appr:
        if len(qs) > 1 and len(pairs) == len(qs) and not any(q for q, _ in pairs):
            # unlabeled answers in question order: pick the ones at the approval questions' positions
            idx = [i for i, q in enumerate(qs) if _is_approval_text(f"{q.get('header', '')} {q.get('question', '')}")]
            appr = [pairs[i] for i in idx]
        else:
            appr = list(pairs)                      # cannot map → every answer must approve (old, strict behaviour)
    others = [x for x in pairs if x not in appr]
    verdicts = [intent.approval_answer(a) for _, a in appr]
    decision = "approve" if verdicts and all(v == "approve" for v in verdicts) else "reject"
    return decision, [a for _, a in appr], others


def is_approval_question(ev: dict) -> bool:
    qs = (ev.get("tool_input") or {}).get("questions") or []
    for q in qs:
        if isinstance(q, dict):
            text = f"{q.get('header', '')} {q.get('question', '')}".lower()
            if "onay" in text or "approv" in text:
                return True
    return False


def extract_answers(ev: dict) -> list:
    """AskUserQuestion answers; tolerant to format differences across Claude Code versions."""
    found = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "answers" and isinstance(v, dict):
                    found.extend(str(x) for x in v.values())
                elif k == "answers" and isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            found.extend(str(x) for x in item.values() if isinstance(x, str))
                        else:
                            found.append(str(item))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for x in obj:
                walk(x)

    # trust only the tool's OUTPUT (what the UI collected), never the model-authored input
    walk(ev.get("tool_response"))
    if not found:
        raw = ev.get("tool_response")
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        found = re.findall(r'"=\s*"?([^"\n]+)', text) or re.findall(r'=\\?"([^"\\]+)', text)
    return found


def on_stop(ev: dict) -> None:
    b = binding_for(ev)
    if not b:
        return
    st = b["state"]
    project = Path(b["project"])

    def idle(why: str) -> None:
        activity.record(project, st["run_id"], ev, "idle", why)

    if st.get("status") != "active" or st.get("phase") == "done":
        idle(f"tur bitti · durum {st.get('status')}")
        return
    if ev.get("stop_hook_active"):
        idle("tur bitti")
        return
    cfg = paths.global_config()
    headless = paths.is_headless(st)        # also true for an interactive run the bridge / watchdog continues
    if (cfg.get("tempo") or {}).get("stop_while_agents", True) and not headless:
        wd = tempo.wd_cfg(cfg)
        pending, _lost = tempo.live_agents(project, st["run_id"], wd=wd)    # lost = registered, no sign of life
        if pending:
            # Subagents run in the background and hand their report back as a new message, which starts the next
            # turn. Blocking here only forced the model into sleep/poll loops (measured: 8–29 % of wall time).
            who = ", ".join(sorted({activity.short_agent(str(a.get("type") or "")) for a in pending}))
            activity.record(project, st["run_id"], ev, "wait_agents", f"alt-ajan bekleniyor: {who} ({len(pending)})")
            return
        # the last background subagent has stopped but its report has not been delivered yet: nudging now would
        # re-dispatch the work it just finished (the watchdog reports a report that never arrives as a dead wait)
        due = activity.agents_reports_due(project, st["run_id"], max_age=float(wd["dead_wait_seconds"]))
        if due:
            activity.record(project, st["run_id"], ev, "wait_agents", f"alt-ajan bitti, raporu bekleniyor ({len(due)})")
            return
    key = ev.get("prompt_id") or "noprompt"
    with ST.StateTxn(project, b["run_id"]) as s:
        count = int((s.get("stop_blocks") or {}).get(key, 0))
        if count >= MAX_STOP_BLOCKS_PER_PROMPT:
            idle("tur bitti (devam uyarısı limiti)")
            return
        s.setdefault("stop_blocks", {})[key] = count + 1
        # keep the dict small
        if len(s["stop_blocks"]) > 50:
            s["stop_blocks"] = dict(list(s["stop_blocks"].items())[-20:])
    hint = next_hint(project, st, activity.read_rows(project, st["run_id"]))
    emit({"decision": "block", "reason":
          f"[orkestra] Run {st['run_id']} is still in phase '{st['phase']}' (status active)"
          + ("" if headless else " and no subagent is running") + ": do not end the turn mid-run.\n"
          + (hint + "\n" if hint else f"Continue with `orkestra next -p {project}`.\n")
          + f"If you must wait for the user, ask with AskUserQuestion, or run `orkestra await \"<question>\" -p {project}` "
          f"(headless). If truly blocked, run `orkestra block \"<reason>\" -p {project}` and explain. If the user "
          f"changed topic, run `orkestra pause -p {project}` first."})
    activity.record(project, st["run_id"], ev, "nudge", "erken durmaya çalıştı → devam ettirildi")


def on_subagent_stop(ev: dict) -> None:
    b = binding_for(ev)
    if not b:
        return
    project = Path(b["project"])

    def finished() -> None:
        activity.agent_finished(project, b["run_id"], str(ev.get("agent_id") or ""), str(ev.get("agent_type") or ""))
        activity.record(project, b["run_id"], ev, "agent_done", f"{ev.get('agent_type') or 'agent'} bitti")

    if ev.get("agent_type") != "orkestra:implementer" or ev.get("stop_hook_active"):
        finished()
        return
    st = b["state"]
    in_prog = [(tid, t) for tid, t in (st.get("tasks") or {}).items() if t.get("status") == "in_progress"]
    missing = []
    for tid, t in in_prog:
        notes = t.get("notes") or []
        blocked_note = bool(notes) and str(notes[-1]).upper().startswith("BLOCKED")
        if t.get("tdd") == "required" and not (t.get("green") or {}).get("ok") and not blocked_note:
            missing.append(tid)
    if not missing:
        finished()
        return
    agent_id = ev.get("agent_id") or "?"
    with ST.StateTxn(project, b["run_id"]) as s:
        seen = s.setdefault("subagent_stop_blocks", {})
        already = bool(seen.get(agent_id))
        seen[agent_id] = 1
    if already:
        finished()
        return
    activity.record(project, b["run_id"], ev, "nudge", f"TDD kanıtı eksik ({', '.join(missing)}) → alt-ajan devam ettirildi")
    emit({"decision": "block", "reason":
          f"[orkestra] Task(s) {', '.join(missing)} have no passing-test evidence. Finish TDD: "
          f"`orkestra tdd green <TID> -- <test cmd>`. If you cannot, run `orkestra task note <TID> \"BLOCKED: <why>\"` "
          f"and end with STATUS: BLOCKED and the reason."})


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_user_prompt,
    "PreToolUse": on_pre_tool,
    "PostToolUse": on_post_tool,
    "Stop": on_stop,
    "SubagentStop": on_subagent_stop,
}


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        ev = json.loads(raw) if raw.strip() else {}
        if isinstance(ev, dict) and ev.get("cursor_version"):
            # Cursor imports Claude Code plugins and runs their hooks with ITS OWN payloads (tool "Shell", event
            # "preToolUse" …). Those are handled by hooks/cursor_hook.py; answering here too would do everything twice
            # and with the wrong vocabulary.
            return 0
        if not name:
            name = ev.get("hook_event_name", "")
        handler = HANDLERS.get(name)
        if handler:
            handler(ev)
    except Exception as exc:  # fail open
        log(f"ERROR in {name}: {exc}\n{traceback.format_exc()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
