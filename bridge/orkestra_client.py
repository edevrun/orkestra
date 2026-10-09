#!/usr/bin/env python3
"""Tiny client for the orkestra bridge — for OpenClaw tools, shell scripts, cron, iOS Shortcuts over SSH ...

    orkestra_client.py start <project> <request...> [--chat-id ID] [--source openclaw]
    orkestra_client.py reply <job-id> <text...>
    orkestra_client.py reply --chat-id ID <text...>        # the job of that chat waiting for an answer
    orkestra_client.py status [<job-id>] [--chat-id ID]
    orkestra_client.py wait <job-id> [--timeout 1800]       # block until the job stops running, print its message
    orkestra_client.py cancel --chat-id ID                  # stop the latest open job of that chat
    orkestra_client.py runs [--json]                        # live view: every open run, its phase, what it does now

Reads the bearer token from ~/.orkestra/bridge.secret.json (same machine). Prints JSON (or the message for `wait`).
Exit codes: 0 ok · 1 bridge/HTTP error · 2 usage.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HOME = Path(os.environ.get("ORKESTRA_HOME") or "~/.orkestra").expanduser()


def _cfg() -> Dict[str, Any]:
    try:
        return json.loads((HOME / "bridge.secret.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        sys.exit(f"bridge not configured — run: python3 {Path(__file__).resolve().parent / 'orkestra_bridge.py'} init")


def _call(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Any]:
    cfg = _cfg()
    url = f"http://{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 47615)}{path}"
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg['token']}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except urllib.error.URLError as e:
        return 0, {"error": f"bridge not reachable ({e.reason}) — start it: python3 {Path(__file__).resolve().parent / 'orkestra_bridge.py'} serve"}


def _opt(args: List[str], name: str, default: Optional[str] = None) -> Tuple[Optional[str], List[str]]:
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1], args[:i] + args[i + 2:]
    return default, args


def main(argv: List[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    chat, args = _opt(args, "--chat-id")
    source, args = _opt(args, "--source", "cli")
    timeout, args = _opt(args, "--timeout", "1800")
    if cmd == "start":
        if len(args) < 2:
            print(__doc__)
            return 2
        code, body = _call("POST", "/jobs", {"project": args[0], "request": " ".join(args[1:]), "chat_id": chat,
                                             "source": source})
    elif cmd == "reply":
        if chat:
            code, body = _call("POST", "/reply", {"chat_id": chat, "text": " ".join(args)})
        elif len(args) >= 2:
            code, body = _call("POST", f"/jobs/{args[0]}/reply", {"text": " ".join(args[1:])})
        else:
            print(__doc__)
            return 2
    elif cmd == "status":
        if args:
            code, body = _call("GET", f"/jobs/{args[0]}")
        else:
            code, body = _call("GET", "/jobs" + (f"?chat_id={urllib.parse.quote(chat)}" if chat else ""))
    elif cmd == "runs":
        code, body = _call("GET", "/runs")
        if code == 200 and "--json" not in args:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from orkestra_bridge import live_text
            print(live_text(body.get("runs") or [], limit=100_000))
            return 0
    elif cmd == "cancel":
        if not chat:
            print(__doc__)
            return 2
        code, body = _call("POST", "/telegram", {"chat_id": chat, "text": "/iptal"})
    elif cmd == "wait":
        if not args:
            print(__doc__)
            return 2
        deadline = time.time() + float(timeout or 1800)
        while True:
            code, body = _call("GET", f"/jobs/{args[0]}")
            if code != 200 or body.get("status") != "running" or time.time() > deadline:
                break
            time.sleep(5)
        if code == 200:
            print(f"[{body.get('project')}] {body.get('status')} · {body.get('phase') or ''}\n\n{body.get('last_message') or ''}")
            return 0
    else:
        print(__doc__)
        return 2
    print(json.dumps(body, ensure_ascii=False, indent=2))
    return 0 if 200 <= code < 300 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
