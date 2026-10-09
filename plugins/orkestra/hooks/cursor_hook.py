#!/usr/bin/env python3
"""Cursor entry point for the Orkestra hooks:  python3 cursor_hook.py <cursorEventName>  (event JSON on stdin).

Cursor (IDE agent chat and the `agent` CLI) speaks another hook dialect than Claude Code: other event names, other
tool names, another reply format, and a conversation id of its own for every subagent. This file translates a Cursor
event into the shape hooks/dispatch.py already handles, calls the SAME handlers (one set of guards, one state
machine), and translates the answer back. Nothing is decided here.

    Cursor event          → handler in dispatch.py      reply
    sessionStart          → on_session_start            additional_context (+ env for later hooks)
    beforeSubmitPrompt    → on_user_prompt              continue: true   (context is delivered with the next tool result)
    preToolUse            → on_pre_tool                 permission allow | deny (+ user_message / agent_message)
    postToolUse           → on_post_tool                additional_context
    postToolUseFailure    → on_post_tool                (a shell command that exited non-zero ends here, not above)
    stop                  → on_stop                     followup_message (only after a "completed" turn)
    subagentStart         → child bookkeeping           allow
    sessionEnd            → the turn's permission ends  nothing

Measured against `agent` 2026.10.01 (2026-10-05; payloads in tests/fixtures/cursor):
  * print mode (`agent -p`) fires neither beforeSubmitPrompt nor stop, and sessionStart only for a new chat — the
    headless driver (bridge / watchdog) calls this script itself for the prompt step (engines.driver_prompt);
  * subagentStart / subagentStop and postToolUse for Task did not fire: a subagent is a conversation of its own,
    recognised from the parent's Task call (activity.cursor_launch / cursor_claim) — its first batch of calls
    arrives before its transcript file exists;
  * a follow-up (our stop reply) runs under a NEW generation id and fires no prompt hook; a later print-mode turn
    has a fresh generation id too. `_turn_id` maps all of that back to "one user prompt";
  * the shell is one long-lived process per conversation (`cd` persists) and events carry `cwd: ""`: the directory
    is followed command by command (orkestra_core.shellcwd).

A chat that has nothing to do with an orkestra run is answered before any orkestra module is imported (this script
runs for every tool call of every Cursor chat). Like dispatch.py: stdlib only, never exits non-zero, fails OPEN on
internal errors (logged), except that a guard which cannot finish in time refuses the call.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
# subprocess, traceback and uuid are imported where they are used: this script starts for EVERY Cursor event

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))

ENGINE = "cursor"
PREFIX = "cursor-"
PERMISSION_EVENTS = ("preToolUse", "subagentStart", "beforeShellExecution", "beforeReadFile", "beforeMCPExecution")
TOOL_EVENTS = ("preToolUse", "postToolUse", "postToolUseFailure")
FIRST_LINE_MAX = 4_000_000   # a transcript's first line holds the whole Task prompt
ADOPT_SECONDS = 900          # how long a turn waits for its first (or follow-up) generation to show up
RECENT_SESSION_SECONDS = 12 * 3600   # (every guarded event of a run, its subagents' too, refreshes the session file)
# Files of a run that only the orkestra CLI and the hooks write. With a shell whose directory is followed, not
# reported, a bare relative name like these must be given by its absolute path (see on_pre_tool_use).
STATE_NAME_RE = re.compile(r"(?:^|[\s>'\"=(|;&])(?!/|~)(?:\./)?(?:state\.json|approval\.json|agents\.json|gates-[\w-]+\.json|"
                           r"(?:approved|evidence|snapshot)/[\w./-]*)(?=$|[\s'\")|;&<>])")
AGENT_MARKERS = ("CURSOR_AGENT", "CURSOR_CONVERSATION_ID")     # what the agent's own shell carries (hooks do not)
# How Cursor wraps a command the AGENT runs (measured, 2026.10.01): `zsh -c '… builtin eval "$1" < /dev/null; …' -- <cmd>`.
# A hook command gets the same wrapper without the `< /dev/null` (its stdin is the event).
TOOL_WRAPPER_MARK = 'builtin eval "$1" < /dev/null'
LIGHT_EVENTS = ("beforeSubmitPrompt", "stop", "subagentStart")     # answered without orkestra's modules when no run is concerned
GUARDED_ORIGIN = ("beforeSubmitPrompt", "stop", "sessionStart", "sessionEnd", "postToolUse", "postToolUseFailure",
                  "subagentStart")
WRITE_TOOLS = ("Write", "Delete", "Edit", "StrReplace", "MultiEdit", "EditNotebook", "NotebookEdit", "ApplyPatch",
               "SearchReplace", "EditFile", "CreateFile", "DeleteFile")
PATH_KEYS = ("file_path", "path", "target_file", "target_notebook", "notebook_path", "relative_path")
MCP_VERB_RE = re.compile(r"(?:^|_)(replace_|insert_|rename_|delete_|safe_delete_|create_|write_|edit_|move_|remove_|"
                         r"execute_shell_command|run_shell|shell_exec|execute_command|run_command)")
UNKNOWN_CWD = ("The guards lost track of this shell's working directory (Cursor's shell keeps it between calls and "
               "does not report it; `cd -`, `cd \"$VAR\"`, pushd/popd, eval/source or a `cd` inside a loop cannot be "
               "followed). Start this command with `cd <absolute path> && …`.")


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:120]


def _home() -> Path:
    return Path(os.environ.get("ORKESTRA_HOME") or "~/.orkestra").expanduser()


def _conv(ev: dict) -> str:
    return str(ev.get("conversation_id") or ev.get("session_id") or "")


def _root(ev: dict) -> str:
    roots = ev.get("workspace_roots") or []
    return str((roots[0] if roots else "") or ev.get("cwd") or os.getcwd())


def _read(path: Path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _write(path: Path, data) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _drop(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _side(kind: str, key: str) -> Path:
    """Per-conversation notes of this adapter: ~/.orkestra/cursor/<kind>/<id>.json (turns, notes, cwd, seen)."""
    return _home() / "cursor" / kind / f"{_safe(key)}.json"


def _prune_side(days: int = 7) -> None:
    try:
        cutoff = time.time() - days * 86400
        for p in (_home() / "cursor").glob("*/*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- who started this process?

def started_by_an_agent_command() -> str:
    """'' for a hook the Cursor host started; else why this process must be something the AGENT ran.

    2026-10-05: a model, told "no need to ask questions", ran this script itself with {"prompt": "onay"} and
    approved its own plan. A process started from the agent's shell carries the agent's environment markers, and
    one of its ancestors is the wrapper Cursor puts around an agent command; a hook has neither. (Not proof against
    a process that strips its environment AND detaches from its parents — the command guard is the other layer.)"""
    if not _trust_marked() and any(os.environ.get(k) for k in AGENT_MARKERS):
        return "its environment carries the agent shell's markers"
    try:
        import subprocess
        pid, deadline = os.getppid(), time.time() + 2.0
        for _ in range(8):
            if pid <= 1 or time.time() > deadline:
                break
            cp = subprocess.run(["ps", "-ww", "-o", "ppid=,command=", "-p", str(pid)], capture_output=True, text=True, timeout=1)
            line = cp.stdout.strip()
            if not line:
                break
            ppid, _sep, command = line.partition(" ")
            if TOOL_WRAPPER_MARK in command:
                return "it runs inside a command of the agent"
            pid = int(ppid)
    except Exception:
        pass
    return ""


def _trust_marked() -> bool:
    """Escape hatch (config engine.cursor.hooks_carry_agent_env): a Cursor build whose own hook processes carry the
    agent markers would otherwise have every prompt refused."""
    cfg = _read(_home() / "config.json")
    try:
        return bool(((cfg or {}).get("engine") or {}).get("cursor", {}).get("hooks_carry_agent_env"))
    except AttributeError:
        return False


# ---------------------------------------------------------------- where the shell is (events do not say)

def _cwd_get(conv: str, ev: dict):
    """Directory of this conversation's shell: what the event says, else what we followed, else the workspace
    root. None = unknown (see shellcwd)."""
    if ev.get("cwd"):
        return str(ev["cwd"])
    d = _read(_side("cwd", conv))
    if isinstance(d, dict) and "cwd" in d:
        c = d.get("cwd")
        if d.get("guess"):
            return None                 # an alias / function / option was defined in this shell: `cd` may mean anything
        return str(c) if c and os.path.isdir(str(c)) else None      # a `cd` that failed left the shell elsewhere
    return _root(ev)


def _cwd_follow(conv: str, command: str, cur, call_id: str = "") -> None:
    """Called when a shell command is let through: where will the shell be afterwards?"""
    from orkestra_core import shellcwd
    guess = shellcwd.redefines(command)
    if cur is not None and not guess and not shellcwd.mentions_move(command) and re.search(r"[\s;&|<>$`(]", command.strip()):
        return
    old = _read(_side("cwd", conv))
    guess = guess or bool(isinstance(old, dict) and old.get("guess"))
    res = shellcwd.outcome(command, cur)
    rec = {"cwd": res["cwd"], "ts": time.time()}
    if guess:
        rec["guess"] = True
    if res["conditional"] and res["cwd"] is not None:
        # `make && cd build`: unknown until the command is back with exit 0 — and only a pure && chain proves it
        rec = dict(rec, cwd=None, pending={"id": call_id, "cwd": res["cwd"], "pure": bool(res["pure"]) and bool(call_id)})
    if rec.get("cwd") != cur or guess or "pending" in rec or (isinstance(old, dict) and "pending" in old):
        _write(_side("cwd", conv), rec)


def _cwd_settle(conv: str, call_id: str, ok: bool) -> None:
    """The command is back. A zero exit of a pure `&&` chain proves its `cd` ran."""
    d = _read(_side("cwd", conv))
    pend = d.get("pending") if isinstance(d, dict) else None
    if not isinstance(pend, dict):
        return
    if ok and call_id and pend.get("id") == call_id and pend.get("pure"):
        d["cwd"] = pend.get("cwd")
    d.pop("pending", None)
    _write(_side("cwd", conv), d)


# ---------------------------------------------------------------- a chat that is none of our business

def _global_guards() -> bool:
    """Outside an orkestra run Cursor chats are left alone unless the user opted in (config engine.cursor.global_guards)."""
    cfg = _read(_home() / "config.json")
    try:
        return bool(((cfg or {}).get("engine") or {}).get("cursor", {}).get("global_guards"))
    except AttributeError:
        return False


def _cursor_run_recent() -> bool:
    try:
        cutoff = time.time() - RECENT_SESSION_SECONDS
        return any(p.stat().st_mtime >= cutoff for p in (_home() / "sessions").glob(PREFIX + "*.json"))
    except OSError:
        return False


def _concerned(conv: str) -> bool:
    """Does (or did) this conversation drive an orkestra run, or is it a run's subagent?"""
    sessions = _home() / "sessions"
    return (sessions / f"{_safe(PREFIX + conv)}.json").exists() or (sessions / "children" / f"{_safe(conv)}.json").exists()


def _light(name: str, ev: dict):
    """The answer to a prompt / stop / subagent start of a chat no orkestra run knows — or None when the full path
    has to look.

    Nothing can be approved, nudged or recorded for such a chat. What is left is bookkeeping that only ever takes
    something AWAY (an older git permission, a note nobody collected), so it needs neither orkestra's modules nor the
    origin check (process listings). Measured 2026-10-05 on an overloaded machine (load average 230+, bare `python3
    -c pass` 2 s): the full path took 7–19 s for the first prompt of a chat and Cursor cut it off at its timeout —
    while adding its weight to every other hook of that prompt. A prompt that GIVES something (git permission:
    "commit et"), a driver's announcement and everything in a chat a run knows still take the full path."""
    if ev.get("_driver"):
        return None
    conv = _conv(ev)
    if name == "subagentStart":
        parent = str(ev.get("parent_conversation_id") or conv)
        if parent and (_home() / "sessions" / f"{_safe(session_key(parent))}.json").exists():
            return None                             # the parent drives (or drove) a run: the child may have to be bound
        return {"permission": "allow"}
    if not conv:
        return {"continue": True} if name == "beforeSubmitPrompt" else {}
    if _concerned(conv):
        return None
    sid = session_key(conv)
    if name == "stop":
        if _cursor_run_recent():
            return None                             # may be a subagent of a running Cursor run that used no tool yet
        if str(ev.get("status") or "completed") == "completed":
            _turn_id(sid, str(ev.get("generation_id") or ""))
            _drop(_side("notes", sid))
        return {}
    from orkestra_core import intent                # regexes only (no state, no guards)
    if intent.git_grants(str(ev.get("prompt") or "")):
        return None                                 # gives a permission: origin-checked and written by the full path
    _turn_begin(sid, str(ev.get("generation_id") or ""))
    _drop(_home() / "grants" / f"{_safe(sid)}.json")            # an older prompt's permission ends with a new prompt
    _drop(_side("notes", sid))
    return {"continue": True}


def _quiet(name: str, ev: dict):
    """The answer for an event of a chat no orkestra run can be concerned with — or None when the full path has to
    look. Only cheap checks: a few stats, no orkestra import (prompts: its small intent module)."""
    if name in LIGHT_EVENTS:
        return _light(name, ev)
    if name not in TOOL_EVENTS:
        return None
    conv = _conv(ev)
    if not conv:
        return {"permission": "allow"} if name == "preToolUse" else {}
    if _concerned(conv):
        return None                                 # drives (or drove) a run, or is a run's subagent
    ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
    if ev.get("tool_name") == "Shell":
        command = str(ti.get("command") or "")
        if "orkestra" in command or "[orkestra:" in str(ev.get("tool_output") or ""):
            return None                             # `orkestra start` / a bind line: the full path handles it
        if name == "preToolUse":
            _cwd_follow(conv, command, _cwd_get(conv, ev), str(ev.get("tool_use_id") or ""))
        else:
            _cwd_settle(conv, str(ev.get("tool_use_id") or ""), name == "postToolUse")
    if _global_guards() or _cursor_run_recent():
        return None                                 # opted in, or it may be a subagent of a running Cursor run
    return {"permission": "allow"} if name == "preToolUse" else {}


# ---------------------------------------------------------------- full path (orkestra modules loaded lazily)

class _Lazy:
    def __getattr__(self, name):
        import dispatch                                         # same directory: the handlers
        from orkestra_core import activity, engines, paths
        from orkestra_core import state as ST
        self.D, self.activity, self.engines, self.paths, self.ST = dispatch, activity, engines, paths, ST
        return self.__dict__[name]


L = _Lazy()


def session_key(conversation_id: str) -> str:
    return PREFIX + conversation_id if conversation_id and not conversation_id.startswith(PREFIX) else conversation_id


def _first_message(path: str) -> str:
    """The first user message of a conversation transcript (the Task prompt, for a subagent)."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            line = fh.readline(FIRST_LINE_MAX)
        row = json.loads(line)
        content = ((row.get("message") or {}).get("content")) if isinstance(row, dict) else None
        if isinstance(content, str):
            text = content
        else:
            text = " ".join(str(p.get("text") or "") if isinstance(p, dict) else str(p) for p in (content or []))
        m = re.search(r"<user_query>(.*?)</user_query>", text, re.S)
        return (m.group(1) if m else text).strip()
    except Exception:
        return ""


def _cursor_home() -> Path:
    return Path(os.environ.get("ORKESTRA_CURSOR_HOME") or "~/.cursor").expanduser()


def _related(project: str, ev: dict) -> bool:
    """Could a conversation with these workspace roots be working for a run of `project`? (the project, a folder
    above it such as ~/Projects, or below it). No roots in the event: cannot tell — yes."""
    roots = [str(r) for r in (ev.get("workspace_roots") or []) if r]
    if not roots:
        return True
    p = os.path.realpath(project)
    for r in roots:
        r = os.path.realpath(r)
        if r == p or p.startswith(r.rstrip("/") + "/") or r.startswith(p.rstrip("/") + "/"):
            return True
    return False


def _open_cursor_runs(ev: dict):
    """(project, run id, session key) of every open run a Cursor conversation drives — NOT looked up through the
    event's directory: a subagent of a run in ~/Projects/demo may have ~/Projects as its workspace root."""
    out, seen = [], set()
    try:
        files = sorted((_home() / "sessions").glob(PREFIX + "*.json"), key=lambda p: -p.stat().st_mtime)
    except OSError:
        return out
    for f in files[:40]:
        b = L.ST.session_binding(f.stem)
        if not b or (b["project"], b["run_id"]) in seen or not _related(str(b["project"]), ev):
            continue
        seen.add((b["project"], b["run_id"]))
        out.append((Path(b["project"]), b["run_id"], f.stem))
    return out


def _transcript(conv: str, ev: dict, runs) -> str:
    """Where this conversation's transcript is. The first events of a new conversation carry no path (measured:
    `transcript_path: null`), so the known locations are tried too."""
    cands = [str(ev.get("transcript_path") or ""), str(os.environ.get("CURSOR_TRANSCRIPT_PATH") or "")]
    cands = [c for c in cands if c and f"/{conv}" in c]
    if re.fullmatch(r"[A-Za-z0-9-]{8,80}", conv):
        for project, rid, _sid in runs:
            data = _read(L.paths.run_dir(project, rid) / "agents.json") or {}
            for launch in reversed([x for x in (data.get("cursor_launches") or []) if isinstance(x, dict)][-6:]):
                if launch.get("tdir"):
                    cands.append(os.path.join(str(launch["tdir"]), conv, conv + ".jsonl"))
        try:
            cands += [str(p) for p in (_cursor_home() / "projects").glob(f"*/agent-transcripts/{conv}/{conv}.jsonl")][:3]
        except OSError:
            pass
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return ""


def _claim_child(conv: str, ev: dict):
    """An unknown conversation while a Cursor-driven run has a subagent launch nobody has claimed yet."""
    runs = _open_cursor_runs(ev)
    if not runs:
        return None
    tpath = _transcript(conv, ev, runs)
    first = _first_message(tpath) if tpath else ""
    blind = None
    for project, rid, _sid in runs:
        hit = L.activity.cursor_claim(project, rid, conv, first)
        if not hit:
            continue
        if hit.get("ambiguous"):    # role for THIS event only; asked again on the next one
            blind = blind or {"parent": hit.get("sid") or "", "agent_type": str(hit.get("type") or "orkestra:unknown"),
                              "project": str(project), "run_id": rid}
            continue
        return L.ST.bind_child(conv, str(hit.get("sid") or ""), project, rid, str(hit.get("type") or "orkestra:unknown"))
    return blind


def actor(ev: dict):
    """(session id the run is bound under, agent type or "", agent id or "")."""
    conv = _conv(ev)
    if not conv:
        return "", "", ""
    child = L.ST.child_binding(conv)
    if child:
        return str(child["parent"]), str(child.get("agent_type") or "orkestra:unknown"), conv
    sid = session_key(conv)
    if isinstance(_read(L.paths.sessions_dir() / f"{_safe(sid)}.json"), dict):
        return sid, "", ""
    claimed = _claim_child(conv, ev)
    if claimed and claimed.get("parent"):
        return str(claimed["parent"]), str(claimed.get("agent_type") or "orkestra:unknown"), conv
    return sid, "", ""


def _paths_of(name: str, ti: dict):
    if name == "ApplyPatch":
        text = str(ti.get("patch") or ti.get("input") or ti.get("content") or "")
        found = re.findall(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", text, re.M)
        found += re.findall(r"^(?:\+\+\+|---) (?:[ab]/)?(\S+)", text, re.M)
        return [p for p in dict.fromkeys(found) if p and p != "/dev/null"][:50]
    return [str(ti[k]) for k in PATH_KEYS if isinstance(ti.get(k), str) and ti.get(k)][:1]


def map_tool(name: str, ti: dict):
    """Cursor tool → [(Claude-side tool name, tool_input)] as the guards expect them (one entry per file written)."""
    if name == "Shell":
        return [("Bash", {"command": str(ti.get("command") or "")})]
    if name in WRITE_TOOLS:
        text = "\n".join(str(ti[k]) for k in ("content", "new_string", "patch", "contents", "code_edit")
                         if isinstance(ti.get(k), str))[:200000]            # what is written is checked too
        return [("Write", {"file_path": p, "content": text}) for p in (_paths_of(name, ti) or [""])]
    if name == "Read":
        return [("Read", {"file_path": str(ti.get("file_path") or ti.get("path") or ti.get("target_file") or "")})]
    if name == "Grep":      # Cursor: {pattern, file_path, glob, output_mode}
        return [("Grep", {"pattern": str(ti.get("pattern") or ""), "path": str(ti.get("path") or ti.get("file_path") or ""),
                          "glob": str(ti.get("glob") or "")})]
    if name == "Glob":
        return [("Glob", {"pattern": str(ti.get("pattern") or ti.get("glob_pattern") or ""),
                          "path": str(ti.get("path") or ti.get("target_directory") or "")})]
    if name == "Task":
        return [("Agent", {"subagent_type": L.engines.agent_type_from_cursor(str(ti.get("subagent_type") or "")),
                           "description": str(ti.get("description") or ""), "prompt": str(ti.get("prompt") or ""),
                           "run_in_background": bool(ti.get("run_in_background"))})]
    if name.startswith("MCP:"):
        tool = re.sub(r"[^A-Za-z0-9_]", "_", name[4:])
        m = MCP_VERB_RE.search(tool)        # `serena_replace_symbol_body` → the guards look at the verb
        return [("mcp__cursor__" + (tool[m.start(1):] if m else tool), dict(ti))]
    return [(name, dict(ti))]


def tool_response(name: str, ev: dict):
    if ev.get("hook_event_name") == "postToolUseFailure" or "error_message" in ev:
        return {"stdout": str(ev.get("error_message") or ""), "stderr": ""}
    out = ev.get("tool_output")
    try:
        parsed = json.loads(out) if isinstance(out, str) else out
    except ValueError:
        parsed = out
    if name == "Shell":
        text = parsed.get("output") if isinstance(parsed, dict) else parsed
        return {"stdout": str(text or ""), "stderr": ""}
    return parsed


# ---------------------------------------------------------------- one user prompt = one turn

def _turn_begin(sid: str, gen: str) -> str:
    """A user prompt starts a turn; returns its id (what dispatch calls prompt_id). `gen` is empty when a headless
    driver announces the prompt: the first generation that shows up belongs to this turn."""
    if not gen:
        import uuid
    root = gen or ("drv-" + uuid.uuid4().hex[:16])
    _write(_side("turns", sid), {"root": root, "gens": [gen] if gen else [], "adopt": not gen, "ts": time.time()})
    return root


def _turn_id(sid: str, gen: str):
    """The turn a generation belongs to. Permission given in a message ("commit et") and the limit on stop nudges
    are both per user prompt; Cursor's generation id changes inside one (follow-ups), so known generations map to
    the turn's id and the first unknown one after a follow-up / driver prompt is adopted. Anything else is a turn
    we were never told about (a hand-started `agent -p --resume`): it gets its own id and inherits nothing."""
    if not gen:
        return None
    t = _read(_side("turns", sid))
    if not isinstance(t, dict) or not t.get("root"):
        return gen
    gens = [str(g) for g in (t.get("gens") or [])]
    if gen in gens:
        return str(t["root"])
    if t.get("adopt") and time.time() - float(t.get("ts") or 0) <= ADOPT_SECONDS:
        t["gens"], t["adopt"] = (gens + [gen])[-20:], False
        _write(_side("turns", sid), t)
        return str(t["root"])
    return gen


def _turn_followup(sid: str) -> None:
    t = _read(_side("turns", sid))
    if isinstance(t, dict) and t.get("root"):
        t["adopt"], t["ts"] = True, time.time()
        _write(_side("turns", sid), t)


def claude_event(name: str, ev: dict) -> dict:
    sid, agent_type, agent_id = actor(ev)
    conv = _conv(ev)
    out = {"session_id": sid, "cwd": _cwd_get(conv, ev) or _root(ev), "hook_event_name": name, "_engine": ENGINE,
           "transcript_path": ev.get("transcript_path")}
    if agent_type:
        out["agent_type"], out["agent_id"] = agent_type, agent_id
    elif name in ("PreToolUse", "PostToolUse", "Stop"):
        pid = _turn_id(sid, str(ev.get("generation_id") or ""))
        if pid:
            out["prompt_id"] = pid
    return out


def _capture(fn, cev: dict) -> list:
    outs: list = []
    saved = L.D.emit
    L.D.emit = outs.append
    try:
        fn(cev)
    finally:
        L.D.emit = saved
    return outs


def _contexts(outs: list) -> str:
    texts = [str((o.get("hookSpecificOutput") or {}).get("additionalContext") or "") for o in outs if isinstance(o, dict)]
    return L.engines.adapt_text("\n".join(t for t in texts if t), ENGINE)


def _take_note(sid: str) -> str:
    p = _side("notes", sid)
    d = _read(p)
    if d is None:
        return ""
    _drop(p)
    return str(d.get("text") or "").strip() if isinstance(d, dict) else ""


def _known(sid: str, cev: dict) -> bool:
    """Has this conversation (or its parent) ever driven a run? Then the every-session guards keep applying after
    the run has closed — a commit right after `phase next → done` is refused exactly as in Claude Code."""
    return bool(cev.get("agent_type")) or isinstance(_read(L.paths.sessions_dir() / f"{_safe(sid)}.json"), dict)


def _close_tasks(sid: str, b: dict) -> None:
    """The main conversation is moving again: its foreground Task calls are back (Cursor sends no event for that)."""
    project, rid = Path(b["project"]), b["run_id"]
    for x in L.activity.cursor_returned(project, rid, sid):
        kind = str(x.get("type") or "agent")
        cev = {"session_id": sid, "tool_name": "Agent", "tool_input": {"subagent_type": kind}}
        L.activity.record(project, rid, cev, "result", f"← {kind}: döndü")
        if x.get("child"):
            L.activity.record(project, rid, {"session_id": sid, "agent_type": kind, "agent_id": str(x["child"])},
                              "agent_done", f"{kind} bitti")


def _deny(reason: str) -> dict:
    reason = L.engines.adapt_text(reason, ENGINE)
    return {"permission": "deny", "user_message": reason, "agent_message": reason}


# ---------------------------------------------------------------- events

def on_session_start(ev: dict) -> dict:
    conv = _conv(ev)
    if conv:
        _drop(_side("cwd", conv))                   # a new process: a new shell, in the workspace root
    cev = claude_event("SessionStart", ev)
    cev["source"] = "startup"
    out = {"env": {"ORKESTRA_ENGINE": ENGINE}}
    if cev.get("agent_type"):
        return out                                  # a subagent's own session: nothing to tell it
    text = _contexts(_capture(L.D.on_session_start, cev))
    if text:
        out["additional_context"] = text
    return out


def on_before_submit_prompt(ev: dict) -> dict:
    conv = _conv(ev)
    driver = bool(ev.get("_driver"))
    if not conv or L.ST.child_binding(conv):
        return {"continue": True}
    sid = session_key(conv)
    cev = {"session_id": sid, "prompt": str(ev.get("prompt") or ""), "cwd": _root(ev),
           "prompt_id": _turn_begin(sid, "" if driver else str(ev.get("generation_id") or "")),
           "hook_event_name": "UserPromptSubmit", "_engine": ENGINE}
    b = L.ST.session_binding(sid)
    if b:
        _close_tasks(sid, b)
    text = _contexts(_capture(L.D.on_user_prompt, cev))
    if driver:      # the driver puts it in front of the prompt it sends
        _take_note(sid)
        return {"continue": True, "context": text}
    if text:        # Cursor cannot add context to a prompt: it rides on the next tool result (or the stop follow-up)
        _write(_side("notes", sid), {"text": text})
    else:
        _take_note(sid)                             # a note nobody collected belongs to an older prompt
    return {"continue": True}


def on_pre_tool_use(ev: dict) -> dict:
    name = str(ev.get("tool_name") or "")
    ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
    conv = _conv(ev)
    command = str(ti.get("command") or "") if name == "Shell" else ""
    if command and "orkestra" in command and conv:
        _write(_side("seen", conv), {"ts": time.time()})        # `orkestra start` checks that we are in the loop
    cev = claude_event("PreToolUse", ev)
    sid = cev["session_id"]
    b = L.ST.session_binding(sid) if sid else None
    cur = _cwd_get(conv, ev) if name == "Shell" else ""
    call_id = str(ev.get("tool_use_id") or "")
    if not b and not _known(sid, cev) and not _global_guards():
        if name == "Shell":
            _cwd_follow(conv, command, cur, call_id)
        return {"permission": "allow"}              # not an orkestra run: this chat is none of our business
    if name == "Shell" and cur is None:
        from orkestra_core import shellcwd
        if not shellcwd.anchors(command):
            return _deny("[orkestra:cwd] " + UNKNOWN_CWD)
    if name == "Shell" and b and not ev.get("cwd") and STATE_NAME_RE.search(command) and \
            not (Path(b["project"]) / "plugins" / "orkestra" / "hooks").is_dir():
        # the directory of this shell is followed, not known: a relative name that could be a run's state file is
        # not judged on a guess
        return _deny("[orkestra:cwd] This command names state.json / approval.json / agents.json / gates-*.json or "
                     "approved/, evidence/, snapshot/ by a relative path. Cursor's shell does not report its "
                     "directory, so give such paths in full (absolute). (A run's own files of that name are written "
                     "by the orkestra CLI only.)")
    if b and not cev.get("agent_type") and name != "Task":
        _close_tasks(sid, b)
    mapped = map_tool(name, ti)
    if name == "Task" and b:
        # written down BEFORE the subagent's first tool call can arrive (a subagent's own Task call: same run)
        tp = str(ev.get("transcript_path") or "")
        tdir = os.path.dirname(os.path.dirname(tp)) if tp else ""
        L.activity.cursor_launch(Path(b["project"]), b["run_id"], sid, str(ev.get("tool_use_id") or "")[:80],
                                 mapped[0][1]["subagent_type"], mapped[0][1]["prompt"], tdir)
    for tool, tinput in mapped:
        one = dict(cev, tool_name=tool, tool_input=tinput)
        for o in _capture(L.D.on_pre_tool, one):
            hs = (o.get("hookSpecificOutput") or {}) if isinstance(o, dict) else {}
            if hs.get("permissionDecision") == "deny":
                return _deny(str(hs.get("permissionDecisionReason") or "[orkestra] denied"))
    if name == "Shell":
        _cwd_follow(conv, command, cur, call_id)
    return {"permission": "allow"}


def on_post_tool_use(ev: dict) -> dict:
    name = str(ev.get("tool_name") or "")
    ti = ev.get("tool_input") if isinstance(ev.get("tool_input"), dict) else {}
    if name == "Shell":
        _cwd_settle(_conv(ev), str(ev.get("tool_use_id") or ""), ev.get("hook_event_name") == "postToolUse")
    if ev.get("hook_event_name") == "postToolUseFailure" and name != "Shell":
        return {}                                   # nothing was written / read: only a shell command's end matters
    cev = claude_event("PostToolUse", ev)
    texts = []
    for tool, tinput in map_tool(name, ti):
        one = dict(cev, tool_name=tool, tool_input=tinput, tool_response=tool_response(name, ev))
        texts.append(_contexts(_capture(L.D.on_post_tool, one)))
    if not cev.get("agent_type"):
        texts.append(_take_note(cev["session_id"]))
    text = "\n".join(dict.fromkeys(t for t in texts if t))
    return {"additional_context": text} if text else {}


def on_stop(ev: dict) -> dict:
    if str(ev.get("status") or "completed") != "completed":
        return {}                                   # the user stopped it, or it failed: never push it on
    cev = claude_event("Stop", ev)
    if cev.get("agent_type"):
        return {}
    cev["stop_hook_active"] = False                 # dispatch counts its own nudges per prompt (prompt_id)
    b = L.ST.session_binding(cev["session_id"]) if cev["session_id"] else None
    if b:
        _close_tasks(cev["session_id"], b)
    note = _take_note(cev["session_id"])
    for o in _capture(L.D.on_stop, cev):
        if isinstance(o, dict) and o.get("decision") == "block":
            reason = L.engines.adapt_text(str(o.get("reason") or ""), ENGINE)
            _turn_followup(cev["session_id"])       # the follow-up runs under a new generation id
            return {"followup_message": (note + "\n" if note else "") + reason}
    return {}


def on_subagent_start(ev: dict) -> dict:
    sub = str(ev.get("subagent_id") or "")
    parent = session_key(str(ev.get("parent_conversation_id") or ev.get("conversation_id") or ""))
    b = L.ST.session_binding(parent) if parent else None
    if sub and b and not L.ST.child_binding(sub):
        L.ST.bind_child(sub, parent, Path(b["project"]), b["run_id"],
                        L.engines.agent_type_from_cursor(str(ev.get("subagent_type") or "")))
    return {"permission": "allow"}


def on_session_end(ev: dict) -> dict:
    """The chat (or a print-mode process) is gone: its shell is gone, and a permission given in its last message
    ends with it. Print mode has no stop event, so this is also where its turn ends in the log."""
    conv = _conv(ev)
    if not conv:
        return {}
    _drop(_side("cwd", conv))
    _drop(_side("seen", conv))
    if (_home() / "sessions" / "children" / f"{_safe(conv)}.json").exists():
        return {}
    sid = session_key(conv)
    if (_home() / "sessions" / f"{_safe(sid)}.json").exists():
        L.ST.clear_grant(sid)
        b = L.ST.session_binding(sid)
        if b:
            _close_tasks(sid, b)
            L.activity.record(Path(b["project"]), b["run_id"], {"session_id": sid}, "idle",
                              f"tur bitti · durum {b['state'].get('status')} (oturum kapandı)")
    else:
        _drop(_home() / "grants" / f"{_safe(sid)}.json")
    _drop(_side("turns", sid))
    _drop(_side("notes", sid))
    _prune_side()
    return {}


HANDLERS = {
    "sessionStart": on_session_start,
    "beforeSubmitPrompt": on_before_submit_prompt,
    "preToolUse": on_pre_tool_use,
    "postToolUse": on_post_tool_use,
    "postToolUseFailure": on_post_tool_use,
    "stop": on_stop,
    "subagentStart": on_subagent_start,
    "sessionEnd": on_session_end,
}


def fallback(name: str) -> dict:
    if name in PERMISSION_EVENTS:
        return {"permission": "allow"}
    if name == "beforeSubmitPrompt":
        return {"continue": True}
    return {}


def _log(text: str) -> None:
    try:
        d = _home() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "hooks.log", "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}\n")
    except OSError:
        pass


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else ""
    out: dict = {}
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        ev = json.loads(raw) if raw.strip() else {}
        if not isinstance(ev, dict):
            ev = {}
        if not name:
            name = str(ev.get("hook_event_name") or "")
        ev.setdefault("hook_event_name", name)
        out = _quiet(name, ev)
        if out is None:
            why = started_by_an_agent_command() if name in GUARDED_ORIGIN else ""
            if why:     # the model running a hook itself: nothing is recorded, nothing changes
                _log(f"REFUSED cursor:{name} for conversation {_conv(ev)[:8]}: not started by Cursor ({why})")
                out = dict(fallback(name), refused=why) if ev.get("_driver") else fallback(name)
            else:
                handler = HANDLERS.get(name)
                out = handler(ev) if handler else fallback(name)
    except Exception as exc:  # fail open — but always with a well-formed answer (Cursor blocks on malformed JSON)
        import traceback
        _log(f"ERROR in cursor:{name}: {exc}\n{traceback.format_exc()}")
        out = fallback(name)
    try:        # bytes, ASCII-only JSON: a deny must not be lost to the locale of the process that started Cursor
        sys.stdout.buffer.write(json.dumps(out if isinstance(out, dict) else {}, ensure_ascii=True).encode("ascii"))
        sys.stdout.buffer.flush()
    except Exception as exc:
        _log(f"ERROR writing the answer of cursor:{name}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
