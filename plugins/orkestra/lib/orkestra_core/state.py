"""Run state (machine-owned JSON), session bindings, grants and the human ledger."""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths
from .util import (file_lock, now_iso, read_json, read_text, slugify, write_json_atomic,
                   write_text_atomic)

PHASES = ["intake", "discover", "clarify", "plan", "approve",
          "implement", "verify", "review", "deliver", "done"]
OPEN_STATUSES = ("active", "awaiting_user", "blocked", "paused")
TERMINAL_STATUSES = ("done", "aborted")
SCHEMA = 1


class OrkestraError(Exception):
    """User-facing error; message is printed and exit code is 2."""


# ---------------------------------------------------------------- run ids

def next_run_id(project: Path, title: str) -> str:
    fdir = paths.features_dir(project)
    nums = []
    if fdir.is_dir():
        for p in fdir.iterdir():
            m = re.match(r"^(\d{3})-", p.name)
            if m:
                nums.append(int(m.group(1)))
    return f"{(max(nums) + 1) if nums else 1:03d}-{slugify(title)}"


# ---------------------------------------------------------------- state io

def state_path(project: Path, run_id: str) -> Path:
    return paths.run_dir(project, run_id) / "state.json"


def load_state(project: Path, run_id: str) -> Dict[str, Any]:
    st = read_json(state_path(project, run_id))
    if not isinstance(st, dict):
        raise OrkestraError(f"run '{run_id}' not found in {project}")
    return st


def save_state(project: Path, st: Dict[str, Any]) -> None:
    st["updated"] = now_iso()
    write_json_atomic(state_path(project, st["run_id"]), st)


class StateTxn:
    """`with StateTxn(project, run_id) as st:` — locked read-modify-write."""

    def __init__(self, project: Path, run_id: str):
        self.project = Path(project)
        self.run_id = run_id
        self._lock = None
        self.st: Dict[str, Any] = {}

    def __enter__(self) -> Dict[str, Any]:
        self._lock = file_lock(state_path(self.project, self.run_id))
        self._lock.__enter__()
        self.st = load_state(self.project, self.run_id)
        return self.st

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                save_state(self.project, self.st)
        finally:
            assert self._lock is not None
            self._lock.__exit__(exc_type, exc, tb)


def new_state(project: Path, run_id: str, title: str, size: str, mode: str, engine: str = "claude") -> Dict[str, Any]:
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "title": title,
        "project": str(project),
        "created": now_iso(),
        "updated": now_iso(),
        "phase": "intake",
        "status": "active",
        "size": size,
        "mode": mode,
        "engine": engine,               # which agent started it (claude | cursor); any engine may resume it
        "history": [{"at": now_iso(), "from": None, "to": "intake", "note": "run created"}],
        "tasks": {},
        "approved": None,
        "baseline": {},
        "gates": {},
        "awaiting": None,
        "blocked": None,
        "stop_blocks": {},
        "subagent_stop_blocks": {},
    }


# ---------------------------------------------------------------- active runs index

def active_runs() -> Dict[str, str]:
    data = read_json(paths.active_runs_path(), default={}) or {}
    return data if isinstance(data, dict) else {}


def set_active_run(project: Path, run_id: Optional[str]) -> None:
    p = paths.active_runs_path()
    with file_lock(p):
        data = active_runs()
        key = str(Path(project))
        if run_id:
            data[key] = run_id
        else:
            data.pop(key, None)
        write_json_atomic(p, data)


def project_active_run(project: Path) -> Optional[str]:
    rid = active_runs().get(str(Path(project)))
    if rid and state_path(project, rid).exists():
        return rid
    # fall back to scanning runs/ for an open run (index may be stale or from another machine)
    rdir = paths.runs_dir(project)
    if rdir.is_dir():
        cands = []
        for p in rdir.iterdir():
            st = read_json(p / "state.json")
            if isinstance(st, dict) and st.get("status") in OPEN_STATUSES:
                cands.append((st.get("updated", ""), st["run_id"]))
        if cands:
            return sorted(cands)[-1][1]
    return None


# ---------------------------------------------------------------- session bindings

def bind_session(session_id: str, project: Path, run_id: str, engine: str = "") -> None:
    if not session_id:
        return
    data = {"project": str(project), "run_id": run_id, "bound_at": now_iso()}
    if engine:
        data["engine"] = engine                     # which agent drives from this session (claude when absent)
    write_json_atomic(paths.sessions_dir() / f"{_safe(session_id)}.json", data)
    _prune(paths.sessions_dir(), days=21)


# ---------------------------------------------------------------- child conversations (Cursor subagents)
# Claude Code reports a subagent's tool calls under the PARENT session id plus agent_type. Cursor gives every
# subagent its own conversation id and no agent_type. The Cursor hook adapter resolves such a conversation to its
# parent once and remembers it here — outside sessions/*.json, so a child is never taken for a session that drives
# the run (run_sessions, watchdog resume target, takeover warning).

def children_dir() -> Path:
    return paths.sessions_dir() / "children"


def bind_child(conversation_id: str, parent_session: str, project: Path, run_id: str, agent_type: str) -> Dict[str, Any]:
    data = {"project": str(project), "run_id": run_id, "parent": parent_session, "agent_type": agent_type,
            "bound_at": now_iso()}
    if conversation_id:
        write_json_atomic(children_dir() / f"{_safe(conversation_id)}.json", data)
        _prune(children_dir(), days=7)
    return data


def child_binding(conversation_id: str) -> Optional[Dict[str, Any]]:
    if not conversation_id:
        return None
    data = read_json(children_dir() / f"{_safe(conversation_id)}.json")
    return data if isinstance(data, dict) and data.get("parent") else None


def unbind_session(session_id: str) -> None:
    try:
        (paths.sessions_dir() / f"{_safe(session_id)}.json").unlink()
    except FileNotFoundError:
        pass


def session_binding(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    data = read_json(paths.sessions_dir() / f"{_safe(session_id)}.json")
    if not isinstance(data, dict):
        return None
    project = Path(data.get("project", ""))
    rid = data.get("run_id", "")
    st = read_json(state_path(project, rid)) if rid else None
    if not isinstance(st, dict) or st.get("status") in TERMINAL_STATUSES:
        return None
    data["state"] = st
    return data


def touch_session(session_id: str) -> None:
    """Heartbeat: the binding file's mtime is the last time this session produced a hook event."""
    if not session_id:
        return
    try:
        os.utime(paths.sessions_dir() / f"{_safe(session_id)}.json", None)
    except OSError:
        pass


def run_sessions(project: Path, run_id: str) -> List[Dict[str, Any]]:
    """Sessions bound to a run, newest heartbeat first: [{id, bound_at, heartbeat (epoch), age (s)}]."""
    out: List[Dict[str, Any]] = []
    d = paths.sessions_dir()
    if not d.is_dir():
        return out
    now = time.time()
    for f in d.glob("*.json"):
        data = read_json(f)
        if isinstance(data, dict) and data.get("run_id") == run_id and Path(data.get("project", "")) == Path(project):
            try:
                hb = f.stat().st_mtime
            except OSError:
                continue
            out.append({"id": f.stem, "bound_at": data.get("bound_at", ""), "heartbeat": hb, "age": int(now - hb)})
    return sorted(out, key=lambda x: -x["heartbeat"])


# ---------------------------------------------------------------- grants (git intent per prompt)

def write_grant(session_id: str, prompt_id: Optional[str], categories: List[str], prompt_excerpt: str) -> None:
    if not session_id:
        return
    write_json_atomic(paths.grants_dir() / f"{_safe(session_id)}.json", {
        "prompt_id": prompt_id, "categories": sorted(set(categories)),
        "at": now_iso(), "ts": time.time(), "excerpt": prompt_excerpt[:200],
    })
    _prune(paths.grants_dir(), days=2)


def clear_grant(session_id: str) -> None:
    try:
        (paths.grants_dir() / f"{_safe(session_id)}.json").unlink()
    except FileNotFoundError:
        pass


def current_grant(session_id: str, prompt_id: Optional[str]) -> List[str]:
    data = read_json(paths.grants_dir() / f"{_safe(session_id)}.json") if session_id else None
    if not isinstance(data, dict):
        return []
    if prompt_id and data.get("prompt_id"):
        return list(data.get("categories") or []) if data["prompt_id"] == prompt_id else []
    # no prompt ids available: accept grants younger than 30 minutes
    if time.time() - float(data.get("ts", 0)) < 1800:
        return list(data.get("categories") or [])
    return []


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:120]


def _prune(directory: Path, days: int) -> None:
    try:
        cutoff = time.time() - days * 86400
        for p in directory.glob("*.json"):
            if p.stat().st_mtime < cutoff:
                p.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------- ledger (human readable)

def ledger(project: Path, run_id: str, line: str) -> None:
    path = paths.feature_dir(project, run_id) / "ledger.md"
    existing = read_text(path)
    if not existing:
        existing = f"# Ledger — {run_id}\n\nAppend-only event log written by the orkestra CLI.\n\n"
    stamp = now_iso().replace("T", " ")[:16]
    write_text_atomic(path, existing + f"- {stamp} {line}\n")


def bind_marker(project: Path, run_id: str) -> str:
    """Machine-readable line parsed by the PostToolUse hook to bind the session to a run."""
    return f"[orkestra:bind] project={project} run={run_id}"


def unbind_marker(project: Path, run_id: str) -> str:
    return f"[orkestra:unbind] project={project} run={run_id}"


def cwd_project_from_env() -> Optional[Path]:
    env = os.environ.get("ORKESTRA_PROJECT")
    if env:
        return Path(env).expanduser().resolve(strict=False)
    return paths.find_project_root(os.getcwd())
