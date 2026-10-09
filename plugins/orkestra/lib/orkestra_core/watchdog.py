"""`orkestra watchdog` — one scan of every open run from OUTSIDE the Claude session.

Hooks only run while a session produces events; a session that crashed, was closed, or sits in a dead wait cannot
notice it by itself. The watchdog reads the same files as `orkestra watch`, classifies each open run
(tempo.classify) and
  * prints / returns the incidents (who waits for whom, since when, what the next step is),
  * notifies once per incident (macOS notification, optional webhook; the bridge forwards them to Telegram),
  * resumes a SILENT run only as far as `watchdog.auto_resume` allows (off | headless | all). The default,
    `headless`, continues only runs that were STARTED headless (bridge / `claude -p` — unattended by the user's own
    choice); a run driven from an interactive window is never resumed unless the user sets `all`. Always on the same
    session (`claude -p --resume <id>` + /orkestra:resume), never while a claude process of that session or a bridge
    job is alive, at most `max_auto_resumes` times per run with growing pauses. A hung command is reported, never
    killed; a headless run whose process died inside a command is resumed like a silent one.

Run it every minute with launchd/cron (`orkestra watchdog --quiet`) or let the bridge do it (it does by default).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import activity as A
from . import engines as EN
from . import paths
from . import state as ST
from . import tempo as T
from .util import file_lock, read_json, write_json_atomic

NOTIFY_CLASSES = (2, 3, 4)
BACKOFF_MINUTES = (5, 15, 45)


def _state_path() -> Path:
    return paths.orkestra_home() / "watchdog.json"


def _open_runs() -> List[Dict[str, Any]]:
    from .monitor import _project_dirs            # discovery only (no usage / transcript reading)
    out = []
    for proj in _project_dirs(paths.workspace_root()):
        rdir = paths.runs_dir(proj)
        if not rdir.is_dir():
            continue
        for d in sorted(rdir.iterdir()):
            st = read_json(d / "state.json")
            if isinstance(st, dict) and st.get("status") in ST.OPEN_STATUSES and st.get("run_id"):
                out.append({"project": proj, "state": st, "dir": d})
    return out


def scan(now: Optional[float] = None, cfg: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    now = time.time() if now is None else now
    cfg = cfg or paths.global_config()
    wd = T.wd_cfg(cfg)
    views = []
    for item in _open_runs():
        proj, st = item["project"], item["state"]
        rid = st["run_id"]
        rows = A.read_rows(proj, rid)
        pending = A.agents_pending(proj, rid, max_age=float(wd["agent_max_minutes"]) * 60, now=now)
        try:
            mtime = (item["dir"] / "state.json").stat().st_mtime
        except OSError:
            mtime = 0.0
        stall = T.classify(st, rows, pending, now=now, wd=wd, state_mtime=mtime)
        try:
            nxt = T.next_lines(T.next_action(proj, st, deep=False, now=now))[0]
        except Exception:
            nxt = ""
        sessions = ST.run_sessions(proj, rid)
        views.append({"project": str(proj), "project_name": proj.name, "run_id": rid, "title": st.get("title", ""),
                      "phase": st.get("phase"), "status": st.get("status"), "mode": st.get("mode"), "stall": stall,
                      "next": nxt, "sessions": [{"id": s["id"], "age": s["age"]} for s in sessions]})
    return views


def _wants_notice(v: Dict[str, Any], wd: Dict[str, Any]) -> bool:
    s = v["stall"]
    if s["cls"] in NOTIFY_CLASSES:
        return True
    if s["cls"] == 1 and s["key"] in ("user", "turn_ended", "blocked"):
        return (s.get("since") or 0) >= float(wd.get("user_wait_notify_seconds") or 300)
    return False


def _message(v: Dict[str, Any]) -> str:
    s = v["stall"]
    since = f" · {T.dur_tr(s['since'])}" if s.get("since") else ""
    head = {1: "SENİ BEKLİYOR", 2: "ÖLÜ BEKLEME", 3: "ASILI KOMUT", 4: "SESSİZ OTURUM"}.get(s["cls"], "bilgi")
    return f"[{v['project_name']}] {head}{since} — {s['text']} · faz {v['phase']}"


def _macos_notify(title: str, text: str) -> None:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')[:230]  # noqa: E731
    try:
        subprocess.run(["osascript", "-e", f'display notification "{esc(text)}" with title "{esc(title)}"'],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def _webhook(cfg: Dict[str, Any], payload: Dict[str, Any]) -> None:
    n = cfg.get("notify") or {}
    url = n.get("webhook_url")
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Orkestra-Key": str(n.get("token") or "")})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass


def _bridge_owns(session_ids: List[str]) -> bool:
    jdir = paths.orkestra_home() / "bridge" / "jobs"
    if not jdir.is_dir():
        return False
    for f in jdir.glob("*.json"):
        job = read_json(f)
        if isinstance(job, dict) and job.get("status") in ("running", "queued", "awaiting_user") and \
                ST._safe(EN.session_key(str(job.get("engine") or "claude"), str(job.get("session_id") or ""))) in session_ids:
            return True
    return False


_children: Dict[int, Any] = {}                   # auto-resume processes started by THIS process (reaped on poll)


def _pid_alive(pid: Any, session_ids: Any = ()) -> bool:
    """Is the claude we started for the last auto-resume still running? A reused pid of some other program is not:
    the command line must carry one of the run's session ids (we always start it with `--resume <id>`)."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    child = _children.get(pid)
    if child is not None:
        return child.poll() is None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    try:
        cp = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10)
        return any(str(s) and EN.raw_session_id(str(s)) in cp.stdout for s in session_ids)
    except Exception:
        return True


def _process_alive(session_ids: Any, pid: Any = None) -> bool:
    """Is a claude process for this run still there? Checks the pid of our own last auto-resume and every bound
    session id (a headless claude carries it on its command line: --session-id / --resume <id>).

    Limit: a `claude -p` started by hand without the id in argv is invisible here — the lease (heartbeat) and the
    silent threshold are what protect against a second driver in that case."""
    ids = [session_ids] if isinstance(session_ids, str) else list(session_ids or [])
    if pid and _pid_alive(pid, ids):
        return True
    try:
        for sid in ids:
            if not sid:
                continue
            # the engine's CLI carries ITS id on the command line (a Cursor chat id has no `cursor-` prefix there)
            cp = subprocess.run(["pgrep", "-f", EN.raw_session_id(str(sid))], capture_output=True, text=True, timeout=10)
            if [p for p in cp.stdout.split() if p.strip() and int(p) != os.getpid()]:
                return True
        return False
    except Exception:
        return True                                # cannot tell → assume alive (never risk a double execution)


def _claude_bin() -> Optional[str]:
    bridge = read_json(paths.orkestra_home() / "bridge.secret.json")
    cand = (bridge or {}).get("claude_bin") if isinstance(bridge, dict) else None
    env = os.environ.get("ORKESTRA_CLAUDE_BIN")
    for c in (env, cand, "claude"):
        if not c:
            continue
        if os.path.isabs(c) and os.path.exists(c):
            return c
        found = shutil.which(c)
        if found:
            return found
    return None


def resume_decision(v: Dict[str, Any], cfg: Dict[str, Any], book: Dict[str, Any], now: float) -> Dict[str, Any]:
    """Pure decision (unit-tested): may this run be continued automatically right now?"""
    wd = T.wd_cfg(cfg)                              # includes the user's auto_resume / max_auto_resumes / lease_seconds
    mode = str(wd.get("auto_resume") or "off")
    key = f"{v['project']}|{v['run_id']}"
    rec = (book.get("resumes") or {}).get(key) or {"count": 0, "last": 0}
    cls = v["stall"]["cls"]
    if cls == 3 and v.get("mode") == "headless":
        pass            # a command that never ended: resumed only when its claude process is verifiably gone (main())
    elif cls != 4:
        return {"resume": False, "why": "not a silent session"}
    if mode == "off" or (mode == "headless" and v.get("mode") != "headless"):
        return {"resume": False, "why": f"auto_resume={mode} (run mode {v.get('mode')}) — notification only"}
    if not v.get("sessions"):
        return {"resume": False, "why": "no bound session to resume"}
    lease = float(wd.get("lease_seconds") or 120)
    if v["sessions"][0]["age"] < lease:
        return {"resume": False, "why": f"session heartbeat is {v['sessions'][0]['age']} s old (lease {int(lease)} s)"}
    limit = int(wd.get("max_auto_resumes") or 3)
    if rec["count"] >= limit:
        return {"resume": False, "why": f"auto-resume limit reached ({limit}) — needs you", "limit": True}
    pause = BACKOFF_MINUTES[min(rec["count"] - 1, len(BACKOFF_MINUTES) - 1)] * 60 if rec["count"] else 0
    if now - float(rec.get("last") or 0) < pause:
        return {"resume": False, "why": f"waiting {int((pause - (now - rec['last'])) / 60) + 1} min before attempt {rec['count'] + 1}"}
    return {"resume": True, "why": f"silent {T.dur_tr(v['stall'].get('since'))}; attempt {rec['count'] + 1}/{limit}",
            "session": v["sessions"][0]["id"], "attempt": rec["count"] + 1, "needs_dead_process": cls == 3}


def _cursor_bin(cfg: Dict[str, Any]) -> Optional[str]:
    bridge = read_json(paths.orkestra_home() / "bridge.secret.json")
    return EN.binary("cursor", cfg, hint=(bridge or {}).get("cursor_bin") if isinstance(bridge, dict) else None)


def _spawn_resume(v: Dict[str, Any], cfg: Dict[str, Any], session: str, attempt: int) -> Any:
    """Returns the child's pid (int) or an error text (str). The run is continued by the engine that drove it: the
    bound session tells which (a Cursor conversation is bound as `cursor-<chat id>`)."""
    engine = EN.session_engine(session)
    project = Path(v["project"])
    h = cfg.get("headless") or {}
    if engine == "cursor":
        binary = _cursor_bin(cfg)
        if not binary:
            return "cursor CLI (agent) not found"
        try:
            from . import cursorkit
            if not cursorkit.hooks_active(project):  # a Cursor agent without our hooks would run unguarded
                return "orkestra's Cursor hooks are not installed (`orkestra cursor install`)"
        except Exception as exc:
            return f"cursor setup could not be checked: {exc}"
        said = f"/orkestra-resume {project.name}"
        ctx = EN.driver_prompt("cursor", session, said, str(project))      # print mode fires no prompt hook
        if ctx is None:
            return "the Cursor hook refused the watchdog's prompt (watchdog started from inside an agent's shell?)"
        spec = EN.headless_command("cursor", binary, EN.raw_session_id(session),
                                   (ctx + "\n\n" if ctx else "") + EN.resume_prompt("cursor", project.name), False,
                                   {"model": ((cfg.get("engine") or {}).get("cursor") or {}).get("model")})
    else:
        binary = _claude_bin()
        if not binary:
            return "claude CLI not found"
        spec = EN.headless_command("claude", binary, session, EN.resume_prompt("claude", project.name), False,
                                   {"permission_mode": h.get("permission_mode"), "max_turns": h.get("max_turns")})
    env = EN.child_env(engine)
    log = paths.run_dir(project, v["run_id"]) / f"watchdog-resume-{attempt}.log"
    try:
        with open(log, "w", encoding="utf-8") as fh:        # the child keeps its own copy of the descriptor
            proc = subprocess.Popen(spec["argv"], cwd=str(project), env=env, stdin=subprocess.PIPE, stdout=fh,
                                    stderr=subprocess.STDOUT, text=True, start_new_session=True)
        assert proc.stdin is not None
        if spec["stdin"] is not None:
            proc.stdin.write(spec["stdin"])
        proc.stdin.close()
    except Exception as exc:
        return f"could not start {engine}: {exc}"
    ST.ledger(project, v["run_id"], f"watchdog auto-resume #{attempt} ({'cursor ' if engine == 'cursor' else ''}session {EN.short_sid(session)}, {v['stall']['text'][:80]}) pid={proc.pid}")
    _children[int(proc.pid)] = proc
    return int(proc.pid)


def main(once: bool = True, as_json: bool = False, dry_run: bool = False, quiet: bool = False,
         now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    cfg = paths.global_config()
    wd = T.wd_cfg(cfg)
    views = scan(now, cfg)
    sp = _state_path()
    sp.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(sp):
        book = read_json(sp, default={}) or {}
        if not isinstance(book, dict):
            book = {}
        inc: Dict[str, Any] = book.setdefault("incidents", {})
        book.setdefault("resumes", {})
        live_keys = set()
        open_runs = {f"{v['project']}|{v['run_id']}" for v in views}
        for v in views:
            key = f"{v['project']}|{v['run_id']}|{v['stall']['key']}"
            v["notify"] = False
            if _wants_notice(v, wd):
                live_keys.add(key)
                rec = inc.get(key) or {"first": now, "last_notified": 0, "count": 0}
                again = float((cfg.get("watchdog") or {}).get("renotify_minutes") or 30) * 60
                # waiting for the user is announced once; a stalled run is repeated every renotify_minutes
                if (v["stall"]["cls"] == 1 and not rec.get("count")) or \
                        (v["stall"]["cls"] != 1 and now - float(rec.get("last_notified") or 0) >= again):
                    v["notify"] = True
                    if not dry_run:
                        rec["last_notified"], rec["count"] = now, int(rec.get("count") or 0) + 1
                inc[key] = rec
            v["message"] = _message(v)
            dec = resume_decision(v, cfg, book, now)
            sids = [s["id"] for s in v.get("sessions") or []]
            rkey = f"{v['project']}|{v['run_id']}"
            last_pid = (book["resumes"].get(rkey) or {}).get("pid")
            if dec["resume"]:
                alive = _process_alive(sids, last_pid)
                if _bridge_owns(sids) or alive:     # any mode: a live agent with this session = a second driver
                    dec = {"resume": False, "why": f"its {EN.session_engine(str(dec.get('session') or ''))} process / "
                                                   f"bridge job is still alive — not resumed"}
            v["auto_resume"] = dec
            if dec["resume"] and not dry_run:
                res = _spawn_resume(v, cfg, dec["session"], dec["attempt"])
                if isinstance(res, str):
                    v["auto_resume"] = {"resume": False, "why": res}
                else:
                    book["resumes"][rkey] = {"count": dec["attempt"], "last": now, "pid": res}
                    v["message"] += f" → otomatik devam ettirildi (deneme {dec['attempt']})"
                    v["notify"] = True
            elif dec.get("limit"):
                v["message"] += EN.adapt_text(" → otomatik devam sınırı aşıldı, senin devam ettirmen gerekiyor "
                                              "(/orkestra:resume)", EN.session_engine(sids[0] if sids else ""))
        for k in [k for k in inc if k not in live_keys]:
            inc.pop(k, None)                       # incident over → the next one notifies again immediately
        for k in [k for k in book["resumes"] if k not in open_runs]:
            book["resumes"].pop(k, None)
        if not dry_run:
            write_json_atomic(sp, book)
    if not dry_run:
        for v in views:
            if v["notify"]:
                if (cfg.get("watchdog") or {}).get("macos_notify", True):
                    _macos_notify("Orkestra", v["message"])
                _webhook(cfg, {"source": "orkestra-watchdog", "project": v["project_name"], "run_id": v["run_id"],
                               "class": v["stall"]["cls"], "key": v["stall"]["key"], "message": v["message"], "next": v["next"]})
    if as_json:
        sys.stdout.write(json.dumps(views, ensure_ascii=False, indent=2, default=str) + "\n")
        return 0
    shown = [v for v in views if not quiet or v["notify"] or v["stall"]["cls"] in NOTIFY_CLASSES]
    if not views and not quiet:
        sys.stdout.write("Açık orkestra çalışması yok.\n")
    for v in shown:
        sys.stdout.write(v["message"] + "\n")
        if v["stall"].get("loops"):
            sys.stdout.write("   döngü: " + " | ".join(v["stall"]["loops"][:2]) + "\n")
        if v.get("next") and v["stall"]["cls"] != 0:
            sys.stdout.write("   " + v["next"] + "\n")
        if v["auto_resume"].get("why") and v["stall"]["cls"] in (3, 4):
            sys.stdout.write("   otomatik devam: " + v["auto_resume"]["why"] + "\n")
    return 0
