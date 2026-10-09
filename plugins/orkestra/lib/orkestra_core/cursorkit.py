"""Cursor side of Orkestra: the generated skills and subagents, and installing them (plus the hooks) into ~/.cursor.

One source of truth: the Claude Code plugin's `skills/*/SKILL.md` and `agents/*.md`. `build()` derives the Cursor
versions (other frontmatter, the vocabulary header from engines.with_notes) into `plugins/orkestra/cursor/`; a test
fails when that folder is stale. `install()` links them into the user's Cursor home and merges our hook entries into
`hooks.json` without touching anybody else's entries (a timestamped backup is written first).

Never called from hooks. Writes outside a project, so it is run by the user (`orkestra cursor install`), not by agents.
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
ASSETS = PLUGIN_ROOT / "cursor"
HOOK_SCRIPT = PLUGIN_ROOT / "hooks" / "cursor_hook.py"
BIN = PLUGIN_ROOT / "bin" / "orkestra"
# preToolUse covers Shell/Read/Write/Delete/Task/MCP; the older before*/after* events would only repeat it
# Seconds Cursor waits for each hook. A hook that is cut off counts as "no answer": a guard that did not run.
# 2026-10-05, machine overloaded (load average 230+): the full path needed up to 19 s, the old 15 s cut it off.
HOOK_EVENTS = {"sessionStart": 30, "beforeSubmitPrompt": 30, "preToolUse": 30, "postToolUse": 30,
               "postToolUseFailure": 30, "stop": 30, "subagentStart": 15, "sessionEnd": 15}
MARK = "cursor_hook.py"


def cursor_home() -> Path:
    return Path(os.environ.get("ORKESTRA_CURSOR_HOME") or "~/.cursor").expanduser()


def _split_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    fm: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "#")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, m.group(2).lstrip("\n")


def _yaml(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)        # a JSON string is a valid YAML scalar


def skill_text(name: str) -> str:
    fm, body = _split_frontmatter((PLUGIN_ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8"))
    desc = fm.get("description", "").strip().strip('"')
    body = body.replace("$ARGUMENTS", "(what the user wrote with this command)")
    head = ["---", f"name: orkestra-{name}", f"description: {_yaml(desc)}"]
    if fm.get("disable-model-invocation", "").lower() == "true":
        head.append("disable-model-invocation: true")
    head.append("---")
    return "\n".join(head) + "\n" + EN.with_notes(body, "cursor").rstrip("\n") + "\n"


def agent_text(name: str) -> str:
    fm, body = _split_frontmatter((PLUGIN_ROOT / "agents" / f"{name}.md").read_text(encoding="utf-8"))
    desc = fm.get("description", "").strip().strip('"')
    # readonly stays false on purpose: a Cursor "readonly" subagent has no shell and cannot write its report file.
    # What each role may touch is enforced by the orkestra guards (hooks), exactly as in Claude Code.
    head = ["---", f"name: orkestra-{name}", f"description: {_yaml(desc)}", "model: inherit", "readonly: false",
            "is_background: false", "---"]
    return "\n".join(head) + "\n" + EN.with_notes(body, "cursor", agent=True).rstrip("\n") + "\n"


def expected() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for n in EN.SKILL_NAMES:
        out[f"skills/orkestra-{n}/SKILL.md"] = skill_text(n)
    for n in EN.AGENT_NAMES:
        out[f"agents/orkestra-{n}.md"] = agent_text(n)
    return out


def stale(root: Optional[Path] = None) -> List[str]:
    root = root or ASSETS
    bad = []
    for rel, text in expected().items():
        p = root / rel
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            bad.append(rel)
    return bad


def build(root: Optional[Path] = None) -> List[str]:
    root = root or ASSETS
    written = []
    for rel, text in expected().items():
        p = root / rel
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
            written.append(rel)
    return written


# ---------------------------------------------------------------- hooks.json

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
    """An entry of ours that already points at this checkout with an interpreter that exists is left as it is
    (a second install from another PATH must not rewrite the file)."""
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
        hooks[event] = rest + [{"command": hook_command(event, _kept_python(cur, event)), "timeout": timeout}]
    doc["hooks"] = hooks
    return doc


def strip_hooks(doc: Any) -> Dict[str, Any]:
    _check_shape(doc)
    doc = dict(doc) if isinstance(doc, dict) else {}
    hooks = dict(doc.get("hooks") or {})
    for event in list(hooks):
        cur = list(hooks.get(event) or [])
        rest = [e for e in cur if not _is_ours(e)]
        if rest or len(rest) == len(cur):           # an event that was empty before we came stays as it was
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
    """Backup, then an atomic write — through a symlink if hooks.json is one (dotfiles repositories)."""
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


def _same(src: Path, dst: Path) -> bool:
    if src.is_dir():
        if not dst.is_dir():
            return False
        names = sorted(p.relative_to(src) for p in src.rglob("*") if p.is_file())
        return names == sorted(p.relative_to(dst) for p in dst.rglob("*") if p.is_file()) and \
            all((src / n).read_bytes() == (dst / n).read_bytes() for n in names)
    return dst.is_file() and src.read_bytes() == dst.read_bytes()


def _link(src: Path, dst: Path, copy: bool, actions: List[str], dry: bool) -> None:
    if dst.is_symlink() or dst.exists():
        if dst.is_symlink() and not copy and os.path.realpath(dst) == os.path.realpath(src):
            return
        if copy and not dst.is_symlink() and _same(src, dst):
            return
        if not dry:
            if dst.is_dir() and not dst.is_symlink():
                shutil.rmtree(dst)
            else:
                dst.unlink()
    actions.append(f"{'copy' if copy else 'link'} {dst} → {src}")
    if dry:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if copy:
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, dst)
    else:
        os.symlink(src, dst)


def _targets() -> List[Tuple[Path, Path]]:
    home = cursor_home()
    out = [(ASSETS / "skills" / f"orkestra-{n}", home / "skills" / f"orkestra-{n}") for n in EN.SKILL_NAMES]
    out += [(ASSETS / "agents" / f"orkestra-{n}.md", home / "agents" / f"orkestra-{n}.md") for n in EN.AGENT_NAMES]
    return out


def shim_path() -> Path:
    return Path(os.environ.get("ORKESTRA_SHIM_DIR") or "~/.local/bin").expanduser() / "orkestra"


def install(copy: bool = False, dry: bool = False) -> List[str]:
    """Idempotent. Returns what was (or with dry=True: would be) done."""
    actions: List[str] = []
    if stale():
        raise EN.EngineError("plugins/orkestra/cursor is stale — run `orkestra cursor build` first")
    home = cursor_home()
    hp = home / "hooks.json"
    cur = _read_json(hp)
    if (hp.exists() or hp.is_symlink()) and not isinstance(cur, dict):       # checked before anything is changed
        raise EN.EngineError(f"{hp} is not valid JSON — fix it first (nothing was changed)")
    new = merge_hooks(cur)
    for src, dst in _targets():
        _link(src, dst, copy, actions, dry)
    if new != cur:
        _write_hooks(hp, new, actions, dry)
        actions.append(f"hooks.json: orkestra entries for {', '.join(HOOK_EVENTS)}")
    shim = shim_path()
    if not shutil.which("orkestra") and shim.parent.is_dir():
        dangling = shim.is_symlink() and not shim.exists()
        if dangling or not os.path.lexists(shim):
            actions.append(f"link {shim} → {BIN}  (Cursor's shell needs `orkestra` on PATH)")
            if not dry:
                if dangling:
                    shim.unlink()
                os.symlink(BIN, shim)
    return actions


def uninstall(dry: bool = False) -> List[str]:
    actions: List[str] = []
    hp = cursor_home() / "hooks.json"
    cur = _read_json(hp)
    new = strip_hooks(cur) if isinstance(cur, dict) else None      # a shape we do not understand: stop before any change
    for src, dst in _targets():
        if dst.is_symlink() or dst.exists():
            actions.append(f"remove {dst}")
            if not dry:
                if dst.is_dir() and not dst.is_symlink():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
    if new is not None and new != cur:
        _write_hooks(hp, new, actions, dry)
        actions.append("hooks.json: orkestra entries removed")
    shim = shim_path()
    if shim.is_symlink() and os.path.realpath(shim) == os.path.realpath(BIN):
        actions.append(f"remove {shim}")
        if not dry:
            shim.unlink()
    return actions


def _entries_ok(doc: Any) -> bool:
    hooks = (doc.get("hooks") if isinstance(doc, dict) else None)
    if not isinstance(hooks, dict):
        return False
    return all(any(_is_ours(x) and str(HOOK_SCRIPT) in str(x.get("command")) for x in (hooks.get(e) or [])
                   if isinstance(hooks.get(e), list))
               for e in HOOK_EVENTS)


def hooks_installed() -> bool:
    """Are OUR hooks (this checkout's cursor_hook.py) registered for every event in the user's Cursor home? Without
    them a Cursor run has no guards at all, so headless drivers refuse to start one."""
    return _entries_ok(_read_json(cursor_home() / "hooks.json"))


def hooks_active(project: Optional[Path] = None) -> bool:
    """…or in the project's own `.cursor/hooks.json` (a team that keeps them per repository)."""
    return hooks_installed() or bool(project and _entries_ok(_read_json(Path(project) / ".cursor" / "hooks.json")))


def stale_claude_copy() -> str:
    """Cursor also loads Claude Code plugins ("third-party plugins") — from the path Claude Code's registry records.
    If that is an old cached copy of orkestra, its hooks answer in Cursor chats too, with old rules. Returns a
    description of such a copy, or ''. Read-only; never touches ~/.claude."""
    reg = _read_json(Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser() / "plugins" / "installed_plugins.json")
    try:
        from . import __version__ as current
        for name, entries in ((reg or {}).get("plugins") or {}).items():
            if not str(name).startswith("orkestra@"):
                continue
            for e in entries if isinstance(entries, list) else []:
                path = Path(str(e.get("installPath") or "")).expanduser()
                if not str(e.get("installPath") or "") or os.path.realpath(path) == os.path.realpath(PLUGIN_ROOT):
                    continue
                marker = path / "hooks" / "cursor_hook.py"      # 1.5.0+ copies stay silent for Cursor's payloads
                if path.is_dir() and not marker.exists():
                    return f"{path} (version {e.get('version') or '?'}; this checkout is {current})"
    except Exception:
        return ""
    return ""


def status(cfg: Optional[Dict[str, Any]] = None) -> List[Tuple[Optional[bool], str, str]]:
    """[(ok | None = information only, label, detail)] — read-only."""
    rows: List[Tuple[bool, str, str]] = []
    b = EN.binary("cursor", cfg)
    rows.append((bool(b), "cursor CLI (agent)", b or "not found — https://cursor.com/cli"))
    rows.append((not stale(), "generated assets current", "" if not stale() else "run `orkestra cursor build`"))
    missing = [str(dst) for src, dst in _targets()
               if not (dst.exists() and (os.path.realpath(dst) == os.path.realpath(src) or not dst.is_symlink()))]
    rows.append((not missing, "skills and subagents installed", f"{len(missing)} missing" if missing else str(cursor_home())))
    doc = _read_json(cursor_home() / "hooks.json")
    hooks = doc.get("hooks") if isinstance(doc, dict) and isinstance(doc.get("hooks"), dict) else {}
    have = [e for e in HOOK_EVENTS if isinstance(hooks.get(e), list) and
            any(_is_ours(x) and str(HOOK_SCRIPT) in str(x.get("command")) for x in hooks[e])]
    rows.append((len(have) == len(HOOK_EVENTS), "hooks.json entries", f"{len(have)}/{len(HOOK_EVENTS)} events"))
    rows.append((bool(shutil.which("orkestra")), "`orkestra` on PATH", shutil.which("orkestra") or "not found (Cursor's shell cannot call the CLI)"))
    old = stale_claude_copy()
    if old:
        rows.append((None, "an older orkestra copy is also loaded by Cursor through Claude Code's plugin registry",
                     f"{old} — its hooks answer in Cursor chats with old rules; refresh it with "
                     f"`claude plugin update orkestra@orkestra-local` (see README: Cursor)"))
    return rows
