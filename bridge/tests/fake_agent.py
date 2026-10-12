#!/usr/bin/env python3
"""Stand-in for Cursor's `agent` CLI in the bridge tests. It behaves like the real one where the bridge depends on
it (measured 2026-10-05): `create-chat` hands out the chat id, a print-mode turn takes the prompt as the LAST
argument and continues the chat given to `--resume`, the agent's shell carries CURSOR_AGENT / CURSOR_CONVERSATION_ID,
and no prompt hook fires — the driver has to announce the prompt itself."""
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

args = sys.argv[1:]
if args[:1] == ["create-chat"]:
    print(str(uuid.uuid4()))
    sys.exit(0)
assert args[:6] == ["-p", "--output-format", "json", "--force", "--trust", "--resume"], args
sid, prompt = args[6], args[-1]
assert os.environ.get("ORKESTRA_HEADLESS") == "1", "the bridge must run headless"
assert "CURSOR_AGENT" not in os.environ, "the driver's own agent markers must not leak into the engine process"
ORK = shutil.which("orkestra")
assert ORK, "Cursor's shell needs `orkestra` on PATH (the bridge puts the plugin's bin/ there)"
shell_env = dict(os.environ, CURSOR_AGENT="1", CURSOR_CONVERSATION_ID=sid, CURSOR_INVOKED_AS="agent")
log = Path(os.getcwd()) / ".fake-agent.jsonl"
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"args": args, "stdin": sys.stdin.read()}, ensure_ascii=False) + "\n")


HOOKS = json.loads((Path(os.environ["ORKESTRA_CURSOR_HOME"]) / "hooks.json").read_text()).get("hooks", {})


def fire(event, **fields):
    """Run the installed hooks for an event the way Cursor does: JSON on stdin, the host's environment (no agent
    markers), every command of the event. Returns False when one of them refuses.

    Reparent each hook (double-fork): when this fake runs under an IDE agent shell in CI/tests, a plain
    subprocess still has the agent wrapper as an ancestor and GUARDED_ORIGIN events (sessionEnd, …) are refused.
    """
    payload = dict({"conversation_id": sid, "generation_id": sid, "session_id": sid, "hook_event_name": event,
                    "cursor_version": "fake", "workspace_roots": [os.getcwd()], "transcript_path": None}, **fields)
    ok = True
    drop = ("CURSOR_AGENT", "CURSOR_CONVERSATION_ID", "CURSOR_INVOKED_AS", "CLAUDECODE")
    for entry in HOOKS.get(event, []):
        out = Path(os.getcwd()) / f".hook-out-{os.getpid()}-{event}.txt"
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        script = (
            "import os, subprocess, sys, time\n"
            f"out, cmd, payload = {str(out)!r}, {entry['command']!r}, {json.dumps(json.dumps(payload))}\n"
            f"drop = {drop!r}\n"
            "env = {k: v for k, v in os.environ.items() if k not in drop}\n"
            "if os.fork() == 0:\n"
            "    os.setsid()\n"
            "    if os.fork() == 0:\n"
            "        cp = subprocess.run(cmd, shell=True, input=payload, capture_output=True, text=True, env=env)\n"
            "        open(out, 'w', encoding='utf-8').write(cp.stdout or '')\n"
            "        os._exit(0)\n"
            "    os._exit(0)\n"
            "os.wait()\n"
        )
        subprocess.run([sys.executable, "-c", script], check=False, timeout=60)
        for _ in range(200):
            if out.exists():
                break
            time.sleep(0.01)
        text = out.read_text(encoding="utf-8") if out.exists() else ""
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            ok = ok and json.loads(text or "{}").get("permission") != "deny"
        except ValueError:
            pass
    return ok


def ork(*a):
    command = "orkestra " + " ".join(a) + " -p " + os.getcwd()
    ti = {"command": command, "cwd": "", "timeout": 30000}
    if not fire("preToolUse", tool_name="Shell", tool_input=ti, tool_use_id="t1", cwd=""):
        return subprocess.CompletedProcess(a, 126, "", "denied by a hook")
    cp = subprocess.run([sys.executable, ORK, *a, "-p", os.getcwd()], capture_output=True, text=True, env=shell_env)
    if cp.returncode == 0:
        fire("postToolUse", tool_name="Shell", tool_input=ti, tool_use_id="t1", cwd="",
             tool_output=json.dumps({"output": cp.stdout, "exitCode": 0}))
    else:
        fire("postToolUseFailure", tool_name="Shell", tool_input=ti, tool_use_id="t1", cwd="",
             error_message=cp.stdout + cp.stderr, failure_type="error")
    return cp


said = prompt.split("\n\n")[-1].strip()            # the driver may put hook context in front of the user's words
if "orkestra playbook run" in prompt and "İstek:" in prompt:
    cp = ork("start", "--title", "fake", "--request", prompt.split("İstek:", 1)[1].strip(), "--size", "S")
    assert cp.returncode == 0, cp.stderr
    ork("await", "Varsayılan öncelik ne olsun?")
    result = "1. Varsayılan öncelik ne olsun? (a) medium (Önerilen)"
elif "orkestra resume" in prompt:
    if (Path(os.getcwd()) / ".always-stall").exists():
        result = "yine yarıda kaldım"
    else:
        ork("await", "Devam sorusu?")
        result = "kaldığım yerden devam ettim; bir sorum var"
elif said == "engelle":
    ork("block", "kullanıcı test etti")
    result = "Engellendi: test"
elif said == "hep takıl":
    (Path(os.getcwd()) / ".always-stall").write_text("1")
    result = "yarıda kaldım"
else:                                               # the turn ends while the run is active: a stall
    result = f"devam ediyorum: {said}"
fire("sessionEnd", reason="completed", final_status="completed")       # print mode: the process ends with its turn
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": sid,
                  "usage": {"inputTokens": 1, "outputTokens": 1}}))
