"""Copilot side of Orkestra: install/status/uninstall for hooks under ~/.copilot/plugins/orkestra.

Mirrors cursorkit's hooks merge (keep other entries, timestamped backup, dry-run, refuse broken JSON).
Canonical layout: $COPILOT_HOME/plugins/orkestra/com.github.copilot/hooks/hooks.json with commands that
invoke this checkout's hooks/copilot_hook.py. Never called from hooks — user runs `orkestra copilot install`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import engines as EN

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "copilot_hook.py"
# Events Copilot Agent Plugins may fire; UserPromptSubmit/PreToolUse/PostToolUse are required for readiness.
HOOK_EVENTS = {
    "SessionStart": 20,
    "UserPromptSubmit": 20,
    "subagentStart": 20,
    "PreToolUse": 20,
    "PostToolUse": 20,
    "PostToolUseFailure": 20,
    "Stop": 20,
    "SubagentStop": 20,
}
MARK = "copilot_hook.py"


def copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME") or "~/.copilot").expanduser()


def plugin_dir() -> Path:
    return copilot_home() / "plugins" / "orkestra"


def hooks_path() -> Path:
    return plugin_dir() / "com.github.copilot" / "hooks" / "hooks.json"


def _python() -> str:
    """The interpreter the hook commands name. Not a virtualenv's (it disappears with the venv): its base."""
    if getattr(sys, "base_prefix", sys.prefix) != sys.prefix:
        base = Path(sys.base_prefix) / "bin" / "python3"
        if base.exists():
            return str(base)
    return shutil.which("python3") or sys.executable or "python3"


def hook_command(event: str, python: Optional[str] = None) -> str:
    return f'"{python or _python()}" "{HOOK_SCRIPT}" {event}'


def _is_ours(entry: Any) -> bool:
    return isinstance(entry, dict) and MARK in str(entry.get("command") or "") and "orkestra" in str(entry.get("command") or "")


def _check_shape(doc: Any, where: str = "hooks.json") -> None:
    """Refuse a file we do not understand instead of guessing (nothing is changed)."""
    if doc is None:
        return
    if not isinstance(doc, dict):
        raise EN.EngineError(f"{where} is not a JSON object — fix it first (nothing was changed)")
    hooks = doc.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise EN.EngineError(f"{where}: \"hooks\" is not an object — fix it first (nothing was changed)")
    for event, entries in (hooks or {}).items():
        if not isinstance(entries, list):
            raise EN.EngineError(f"{where}: hooks.{event} is not a list — fix it first (nothing was changed)")


def _kept_python(entries: List[Any], event: str) -> Optional[str]:
    for e in entries:
        m = re.match(r'^"([^"]+)" "([^"]+)" (\S+)$', str(e.get("command") or "")) if _is_ours(e) else None
        if m and m.group(2) == str(HOOK_SCRIPT) and m.group(3) == event and os.path.exists(m.group(1)):
            return m.group(1)
    return None


def merge_hooks(doc: Any) -> Dict[str, Any]:
    """Our entries added (or refreshed) at the END of each event's list; every other entry is kept as it is."""
    _check_shape(doc)
    doc = dict(doc) if isinstance(doc, dict) else {}
    doc.setdefault("version", 1)
    hooks = dict(doc.get("hooks") or {})
    for event, timeout in HOOK_EVENTS.items():
        cur = list(hooks.get(event) or [])
        rest = [e for e in cur if not _is_ours(e)]
        hooks[event] = rest + [{"command": hook_command(event, _kept_python(cur, event)), "timeoutSec": timeout}]
    doc["hooks"] = hooks
    return doc


def strip_hooks(doc: Any) -> Dict[str, Any]:
    _check_shape(doc)
    doc = dict(doc) if isinstance(doc, dict) else {}
    hooks = dict(doc.get("hooks") or {})
    for event in list(hooks):
        cur = list(hooks.get(event) or [])
        rest = [e for e in cur if not _is_ours(e)]
        if rest or len(rest) == len(cur):
            hooks[event] = rest
        else:
            hooks.pop(event)
    doc["hooks"] = hooks
    return doc


def _read_json(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_hooks(hp: Path, doc: Dict[str, Any], actions: List[str], dry: bool) -> None:
    target = Path(os.path.realpath(hp)) if hp.is_symlink() else hp
    if target.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = target.with_name(f"hooks.json.orkestra-backup-{stamp}")
        n = 1
        while backup.exists():
            n += 1
            backup = target.with_name(f"hooks.json.orkestra-backup-{stamp}-{n}")
        actions.append(f"backup {target} → {backup.name}")
        if not dry:
            shutil.copy2(target, backup)
    if dry:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)


def install(dry: bool = False) -> List[str]:
    """Idempotent. Returns what was (or with dry=True: would be) done."""
    actions: List[str] = []
    hp = hooks_path()
    cur = _read_json(hp)
    if (hp.exists() or hp.is_symlink()) and not isinstance(cur, dict):
        raise EN.EngineError(f"{hp} is not valid JSON — fix it first (nothing was changed)")
    new = merge_hooks(cur)
    if new != cur:
        _write_hooks(hp, new, actions, dry)
        actions.append(f"hooks.json: orkestra entries for {', '.join(HOOK_EVENTS)}")
    return actions


def uninstall(dry: bool = False) -> List[str]:
    actions: List[str] = []
    hp = hooks_path()
    cur = _read_json(hp)
    new = strip_hooks(cur) if isinstance(cur, dict) else None
    if new is not None and new != cur:
        _write_hooks(hp, new, actions, dry)
        actions.append("hooks.json: orkestra entries removed")
    return actions


def hooks_active() -> bool:
    """Are OUR prompt/tool guards registered under the Copilot plugin dir?"""
    return EN.copilot_hooks_active(str(plugin_dir()) if plugin_dir().is_dir() else "")


def status(cfg: Optional[Dict[str, Any]] = None) -> List[Tuple[Optional[bool], str, str]]:
    """[(ok | None = information only, label, detail)] — read-only."""
    rows: List[Tuple[Optional[bool], str, str]] = []
    b = EN.binary("copilot", cfg)
    rows.append((bool(b), "copilot CLI", b or "not found"))
    active = hooks_active()
    rows.append((active, "copilot hooks", "active" if active else "not installed"))
    return rows
