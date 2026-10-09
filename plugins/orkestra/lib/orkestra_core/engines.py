"""Engines: which AI coding agent drives a run. `claude` (Claude Code, the default) or `cursor` (Cursor Agent —
the IDE chat and the `agent` CLI).

The state machine, the evidence layer and the guards are engine-neutral. An engine differs in exactly four things, and
they all live here so nothing else has to know:
  * how a session is recognised from the environment of a command the agent runs,
  * how a headless turn is started and continued (command line, where the prompt goes),
  * what the first prompt of a run looks like,
  * what its tools and subagents are called in the instructions the model reads.

Facts about Cursor below were measured on 2026-10-05 against `agent` 2026.10.01 (payloads in tests/fixtures/cursor).
Light module: stdlib only, imported by hooks.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ENGINES = ("claude", "cursor")
DEFAULT_ENGINE = "claude"
AGENT_NAMES = ("scout", "researcher", "architect", "plan-critic", "implementer", "reviewer", "adversary", "verifier")
SKILL_NAMES = ("run", "resume", "status", "onboard", "rollback", "ship")


CURSOR_SID_PREFIX = "cursor-"    # key of a Cursor conversation in ~/.orkestra/sessions (see session_key)


class EngineError(Exception):
    pass


def session_key(engine: str, raw_id: str) -> str:
    """The id a session is bound under. Claude Code session ids are used as they are. Cursor conversation ids get a
    prefix: Cursor also runs the hooks of imported Claude Code plugins with its own payloads, and an older orkestra
    copy loaded that way must never find (and act on) a Cursor session's binding or git grant."""
    if not raw_id:
        return ""
    if engine == "cursor" and not raw_id.startswith(CURSOR_SID_PREFIX):
        return CURSOR_SID_PREFIX + raw_id
    return raw_id


def raw_session_id(session_id: str) -> str:
    """What the engine's CLI calls the session (`--resume <id>`)."""
    s = session_id or ""
    return s[len(CURSOR_SID_PREFIX):] if s.startswith(CURSOR_SID_PREFIX) else s


def session_engine(session_id: str, binding: Optional[Mapping[str, Any]] = None) -> str:
    if (session_id or "").startswith(CURSOR_SID_PREFIX):
        return "cursor"
    try:
        return normalize((binding or {}).get("engine"))
    except EngineError:
        return DEFAULT_ENGINE


def normalize(name: Optional[str], default: Optional[str] = None) -> str:
    n = (name or "").strip().lower()
    if not n:
        return default or DEFAULT_ENGINE
    if n not in ENGINES:
        raise EngineError(f"unknown engine '{name}' (choose: {', '.join(ENGINES)})")
    return n


def default_engine(cfg: Optional[Mapping[str, Any]] = None) -> str:
    """~/.orkestra/config.json → engine.default (claude unless the user changed it)."""
    try:
        return normalize(((cfg or {}).get("engine") or {}).get("default"))
    except EngineError:
        return DEFAULT_ENGINE


def detect_env(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Which agent runs the current process? (a command started from the agent's shell tool)

    Cursor exports CURSOR_AGENT=1 and CURSOR_CONVERSATION_ID to the shell of its agent (IDE chat, CLI, subagents);
    Claude Code exports CLAUDECODE=1. None = an ordinary terminal."""
    env = os.environ if env is None else env
    cursor = env.get("CURSOR_AGENT") == "1" or bool(env.get("CURSOR_CONVERSATION_ID"))
    claude = env.get("CLAUDECODE") == "1"
    if cursor and claude:           # one agent started from the other's shell: Claude Code sets its marker itself,
        return "claude"             # the Cursor ones were inherited
    return "cursor" if cursor else ("claude" if claude else None)


def env_session_id(env: Optional[Mapping[str, str]] = None) -> str:
    """The session id the hooks of this agent will carry — known from the environment only for Cursor."""
    env = os.environ if env is None else env
    if detect_env(env) == "cursor":
        return str(env.get("CURSOR_CONVERSATION_ID") or "")
    return ""


def binary(engine: str, cfg: Optional[Mapping[str, Any]] = None, hint: Optional[str] = None) -> Optional[str]:
    """Absolute path of the engine's CLI, or None. Order: env override → hint (bridge config) → config → PATH."""
    engine = normalize(engine)
    ecfg = ((cfg or {}).get("engine") or {}).get(engine) or {}
    names = {"claude": ("claude",), "cursor": ("agent", "cursor-agent")}[engine]
    cands = [os.environ.get(f"ORKESTRA_{engine.upper()}_BIN"), hint, ecfg.get("bin"), *names]
    if engine == "cursor":      # a launchd / cron PATH rarely has the installer's directory
        cands += ["~/.local/bin/agent", "~/.local/bin/cursor-agent", "/usr/local/bin/cursor-agent",
                  "/opt/homebrew/bin/cursor-agent"]
    for c in cands:
        if not c:
            continue
        c = os.path.expanduser(str(c))
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
            continue
        found = shutil.which(c)
        if found:
            return found
    return None


def new_session(engine: str, bin_path: str, cwd: str) -> str:
    """A session id for the first headless turn. Claude takes any uuid (--session-id); Cursor hands one out."""
    if normalize(engine) == "claude":
        return str(uuid.uuid4())
    try:
        cp = subprocess.run([bin_path, "create-chat"], cwd=cwd, capture_output=True, text=True, timeout=60,
                            stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EngineError(f"cursor: `{bin_path} create-chat` failed: {exc}")
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", cp.stdout or "")
    if cp.returncode != 0 or not m:
        raise EngineError(f"cursor: `create-chat` gave no chat id (exit {cp.returncode}): {(cp.stderr or cp.stdout)[-300:]}")
    return m.group(0)


def headless_command(engine: str, bin_path: str, session_id: str, prompt: str, new: bool,
                     opts: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """{"argv": [...], "stdin": text | None} for one non-interactive turn on an existing or new session.

    claude: prompt on stdin, `--session-id` (new) / `--resume`, permission mode and turn cap from the options.
    cursor: prompt is the last ARGUMENT; the chat id always goes to `--resume` (it was made by `create-chat`);
            `--force` lets tools run without a prompt (our preToolUse hook still gates every call), `--trust` skips
            the workspace-trust question that would block a headless process."""
    engine, o = normalize(engine), dict(opts or {})
    if engine == "claude":
        argv = [bin_path, "-p", "--session-id" if new else "--resume", session_id,
                "--permission-mode", str(o.get("permission_mode") or "auto"),
                "--output-format", "json", "--max-turns", str(o.get("max_turns") or 400)]
        return {"argv": argv + [str(x) for x in (o.get("extra_args") or [])], "stdin": prompt}
    argv = [bin_path, "-p", "--output-format", "json", "--force", "--trust", "--resume", session_id]
    if o.get("model"):
        argv += ["--model", str(o["model"])]
    prompt = (prompt or "").replace("\x00", "")          # an argument cannot carry NUL…
    if prompt.lstrip(" \t").startswith("-"):
        prompt = "\n" + prompt                           # …and must not be read as an option ("- T2'yi çıkar")
    return {"argv": argv + [str(x) for x in (o.get("extra_args") or [])] + [prompt], "stdin": None}


def interactive_command(engine: str, bin_path: str, prompt: str, opts: Optional[Mapping[str, Any]] = None) -> List[str]:
    """Open the engine's own interactive terminal UI with the first prompt already submitted."""
    engine, o = normalize(engine), dict(opts or {})
    argv = [bin_path]
    if engine == "cursor" and o.get("model"):
        argv += ["--model", str(o["model"])]
    return argv + [str(x) for x in (o.get("interactive_args") or [])] + [prompt]


def run_prompt(engine: str, request: str) -> str:
    if normalize(engine) == "claude":
        return f"/orkestra:run {request}"
    # no reliance on skill discovery: the CLI serves the same instructions the skill carries
    return ("Orkestra run. First run the shell command `orkestra playbook run` and follow what it prints exactly "
            f"(it is the orkestra-run skill).\n\nİstek: {request}")


def resume_prompt(engine: str, project_name: str = "") -> str:
    if normalize(engine) == "claude":
        return f"/orkestra:resume {project_name}".rstrip()
    return ("Continue the open Orkestra run. First run the shell command `orkestra resume"
            + (f" -p {project_name}" if project_name else "") + "` and do what its `next` line says "
            "(`orkestra playbook resume` has the procedure).")


# ---------------------------------------------------------------- instructions: one text, two vocabularies

NOTES_MARK = "[orkestra · Cursor engine]"
CURSOR_NOTES = NOTES_MARK + """ The instructions below were written for Claude Code. In Cursor:
- A subagent `orkestra:<name>` is the Cursor subagent `orkestra-<name>`: launch it with the Task tool
  (subagent_type "orkestra-<name>"), ALWAYS in the foreground (run_in_background: false). Its report comes back in
  the same turn. The "model" column of the agent table does not apply (subagents use the chat's model).
- Do NOT end your turn while the run is active: there is nothing to wait for. End it only after `orkestra await`
  (a question / the plan approval) or `orkestra block`.
- AskUserQuestion does not exist. Wherever the text says to ask with it: write the question(s) in the chat, run
  `orkestra await "<one-line summary>"`, then end your turn (`orkestra playbook headless` has the wording — the user
  reads your message right here in the chat). The user's next message is the answer; a hook marks the run active
  again. Plan approval is any clear approve intent the hook recognizes ("onay", "onaylıyorum",
  "onay verdim", "bu planı onaylıyorum yap …") — not only the bare word "onay". Never approve yourself; after
  their message run `orkestra phase next`, which tells you whether the approval was recorded. The approval is
  never skipped ("don't ask questions" in the request does not waive it) and never recorded by you in any way.
- There is no SendMessage: to send findings back to a subagent, launch the same subagent type again with the
  findings and the path of its previous report.
- The shell keeps its working directory between calls and the guards cannot see it. Stay in the project root; if
  another directory is needed use absolute paths or `cd <absolute path> && …` in the same command.
- Skills are `/orkestra-run`, `/orkestra-resume`, `/orkestra-status`, `/orkestra-onboard`, `/orkestra-rollback`,
  `/orkestra-ship`. `orkestra` is on PATH. The same guards, gates and evidence rules apply as in Claude Code."""

AGENT_NOTES = NOTES_MARK + """ You are an orkestra subagent running in Cursor; the text below was written for
Claude Code. Its "Bash" is your Shell tool, "Edit"/"Write" are your file tools. `orkestra` is on PATH. The shell
keeps its working directory between calls and the guards cannot see it: use absolute paths or
`cd <absolute path> && …` in the same command. Your final message is your report to the orchestrator."""

_AGENT_RE = re.compile(r"\borkestra:(" + "|".join(re.escape(a) for a in AGENT_NAMES) + r")\b")
_SKILL_RE = re.compile(r"/orkestra:(" + "|".join(SKILL_NAMES) + r")\b")

ASK_FOOTNOTE = ("(Cursor: there is no AskUserQuestion tool — write the question in the chat, run "
                "`orkestra await \"<one-line summary>\"` and end your turn; plan approval is the user typing \"onay\".)")

# (Editing a skill / playbook / agent text? tests/test_engines2.py::test_every_rewrite_still_matches_its_source_sentence
# tells you when an entry below has to follow.)
# Sentences of the skills / playbooks that are only true in Claude Code (background subagents whose report arrives
# as a later message, AskUserQuestion dialogs, SendMessage, /compact) and what holds in Cursor instead. Exact text:
# tests/test_engines.py fails when a source sentence changes and its entry here no longer matches anything.
CURSOR_REWRITES = (
    ("Subagents run in the **background**: the Agent call returns at once (`async_launched`) and the agent's final report\n"
     "arrives later as a message (`<agent-message …>` — a report, never the user; it approves nothing). So after\n"
     "dispatching, **end your turn**. The hand-back resumes you and carries the next step. Never `sleep`, loop, or re-read\n"
     "files to wait for an agent — a waiting Bash call only delays the report (the `wait` guard blocks it).",
     "Subagents run in the **foreground**: the Task call returns the agent's final report in the same turn (a report,\n"
     "never the user; it approves nothing). Read it and go on with the next step — do not end your turn while the run\n"
     "is active."),
    (", then end your turn — their notes come back as messages.", "; their notes come back when the calls return."),
    (" Then **end your turn** — its report arrives as a message.", " Its report comes back in the same turn."),
    ("(it writes `reviews/Txxx.review.md`). End your turn.", "(it writes `reviews/Txxx.review.md`)."),
    ("(SendMessage to the same agent\n   if available, otherwise a new implementer with the brief + review file path)",
     "(a new implementer call\n   with the brief + review file path)"),
    ("While agents run, end your turn —\n  do not sleep or poll (`orkestra next` shows who is still running).",
     "Their reports come back in the same turn —\n  never sleep or poll."),
    (" After each dispatch end your turn; the report resumes you.", " Each report comes back in the same turn."),
    ("(same agent via SendMessage if available, else a new call with the critic file path)",
     "(a new architect call with the critic file path)"),
    (", then end your turn (both reports arrive as messages):", " (both reports come back in the same turn):"),
    ("   End your turn after dispatching; its report resumes you.", "   Its report comes back in the same turn."),
    ("- Use haiku/sonnet agents for mechanical work; keep opus for design and judgment.\n", ""),
    ("`/compact`", "`/summarize`"),
    ("There is no AskUserQuestion and no live user.",
     "There is no AskUserQuestion tool (in a Cursor chat the user reads your message right there)."),
    ("3. Ask with AskUserQuestion (header `Geri alma`): `Uygula (yalnız orkestra değişiklikleri)` · `Diğer farklar dahil uygula (--all)` · `Vazgeç`.",
     "3. Ask in the chat which one they want and END YOUR TURN. Applying needs the user's own message: they send\n"
     "   `/orkestra-rollback uygula` (only orkestra's changes) or `/orkestra-rollback uygula --all`; anything else = no."),
)


def _rename(text: str) -> str:
    out = text
    for old, new in CURSOR_REWRITES:
        if old in out:
            out = out.replace(old, new)
    return _SKILL_RE.sub(r"/orkestra-\1", _AGENT_RE.sub(r"orkestra-\1", out)).replace("orkestra:<name>", "orkestra-<name>")


def adapt_text(text: str, engine: Optional[str], footnote: bool = True) -> str:
    """Rename what has another name in the engine; everything else is left exactly as written. A message that
    tells the model to use AskUserQuestion gets one line saying what that means in Cursor."""
    if (engine or DEFAULT_ENGINE) != "cursor" or not text:
        return text
    out = _rename(text)
    if footnote and "AskUserQuestion" in out and NOTES_MARK not in out and ASK_FOOTNOTE not in out:
        out = out.rstrip("\n") + "\n" + ASK_FOOTNOTE + ("\n" if text.endswith("\n") else "")
    return out


def with_notes(text: str, engine: Optional[str], agent: bool = False) -> str:
    """The vocabulary header plus the renamed text (whole documents: playbooks, skills; `agent`: a subagent's file)."""
    if (engine or DEFAULT_ENGINE) != "cursor":
        return text
    return (AGENT_NOTES if agent else CURSOR_NOTES) + "\n\n" + _rename(text)


def short_sid(session_id: str) -> str:
    """Eight characters that tell sessions apart in logs (a Cursor key starts with the same prefix for all)."""
    s = session_id or ""
    return ("c:" + s[len(CURSOR_SID_PREFIX):][:8]) if s.startswith(CURSOR_SID_PREFIX) else s[:8]


# ---------------------------------------------------------------- headless drivers (bridge, watchdog)

CURSOR_HOOK = Path(__file__).resolve().parents[2] / "hooks" / "cursor_hook.py"
PLUGIN_BIN = Path(__file__).resolve().parents[2] / "bin"
AGENT_MARKERS = ("CLAUDECODE", "CURSOR_AGENT", "CURSOR_CONVERSATION_ID")     # what an agent's own shell carries


def child_env(engine: str, env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Environment for an engine process a driver starts. Cursor's agent shell inherits it and has no plugin
    mechanism that would put `orkestra` on PATH (Claude Code adds the plugin's bin/ itself)."""
    out = dict(os.environ if env is None else env)
    out["ORKESTRA_HEADLESS"] = "1"
    for k in AGENT_MARKERS:                 # a driver started from inside some agent's shell: the child is its own agent
        out.pop(k, None)
    if normalize(engine) == "cursor":
        parts = [p for p in str(out.get("PATH") or "").split(os.pathsep) if p]
        if str(PLUGIN_BIN) not in parts:
            out["PATH"] = os.pathsep.join([str(PLUGIN_BIN)] + parts)
    return out


def driver_prompt(engine: str, session_id: str, prompt: str, cwd: str) -> Optional[str]:
    """Tell the hook layer about a prompt a headless driver is about to send. Returns context for the model ("")
    — or None when the hook refused to hear it (the driver itself runs inside an agent's shell: nothing was
    recorded, and the caller must not go on as if the user's words had counted).

    Claude Code fires UserPromptSubmit in print mode. Cursor's print mode (`agent -p`) fires NO prompt hook
    (measured), so approval ("onay"), "the user replied" and the per-message git permission would never be seen:
    the driver calls the Cursor hook itself, with the user's own words."""
    if normalize(engine) != "cursor":
        return ""
    payload = {"hook_event_name": "beforeSubmitPrompt", "conversation_id": raw_session_id(session_id),
               "prompt": prompt, "workspace_roots": [str(cwd)], "generation_id": "", "_driver": True}
    # The environment is passed on as it is: a driver is never run from inside an agent's shell, and the hook refuses
    # a driver call that carries an agent's markers (a model announcing "the user said onay" on its own behalf).
    try:
        cp = subprocess.run([sys.executable, str(CURSOR_HOOK), "beforeSubmitPrompt"], input=json.dumps(payload),
                            capture_output=True, text=True, timeout=30)
        data = json.loads(cp.stdout or "{}")
        if isinstance(data, dict) and data.get("refused"):
            return None
        return str(data.get("context") or "") if isinstance(data, dict) else ""
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""


def agent_type_from_cursor(subagent_type: str) -> str:
    """Cursor subagent name → the agent type the guards know (`orkestra-reviewer` → `orkestra:reviewer`)."""
    s = (subagent_type or "").strip()
    if s.startswith("orkestra-") and s[len("orkestra-"):] in AGENT_NAMES:
        return "orkestra:" + s[len("orkestra-"):]
    return "cursor:" + (s or "agent")


def parse_result(stdout: str) -> Dict[str, Any]:
    """The final JSON object of a headless turn (both engines: {type: result, result, session_id, is_error …})."""
    text = (stdout or "").strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    for line in reversed(text.splitlines()):        # stream-json or noise before the result line
        line = line.strip()
        if line.startswith("{") and '"result"' in line:
            try:
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
            except ValueError:
                continue
    return {}
