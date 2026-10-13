"""Regenerable build/test artifact cleanup (Playwright dumps, Android intermediates, …).

Orkestra does not create `report.json` / `libreactnative.so`; tools under a run do. After a run
reaches `done`, we remove well-known regenerable trees inside the project so disk does not keep
growing. Never touches `.git`, `.orkestra`, `node_modules`, source, or secrets. Fail-open: cleanup
errors must not block completing a run.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .lazy import LazyModule
from . import paths as P
from .util import is_relative_to

shutil = LazyModule("shutil")

# Directory basenames that are always tool output (safe to wipe when found under the project).
ARTIFACT_DIR_NAMES = frozenset({
    "test-results",
    "playwright-report",
    "blob-report",
    "coverage",
    ".turbo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
})

# Never enter / delete these (even if a child matched an artifact name).
PROTECTED_DIR_NAMES = frozenset({
    ".git",
    ".orkestra",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "Pods",
    "Carthage",
    ".yarn",
    ".pnpm-store",
})

# Project-relative Android/Gradle regenerable trees only (not CMake-embedded absolute path dirs).
# Optional monorepo prefix: apps/<name>/ or packages/<name>/.
_ANDROID_BUILD_RE = re.compile(
    r"^(?:(?:apps|packages)/[^/]+/)?"
    r"android/(?:app/)?(?:build|\.cxx)$"
)

MAX_WALK_DIRS = 50_000
MAX_REMOVE = 500


def _posix(p: Path) -> str:
    return p.as_posix()


def _is_android_build_tree(rel_posix: str) -> bool:
    """True for `android/app/build`, `android/build`, `android/app/.cxx`, and monorepo variants."""
    return bool(_ANDROID_BUILD_RE.match(rel_posix))


def _dir_size_bytes(root: Path) -> int:
    total = 0
    try:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in PROTECTED_DIR_NAMES]
            for fn in fns:
                try:
                    total += (Path(dp) / fn).stat().st_size
                except OSError:
                    pass
    except OSError:
        return 0
    return total


def _node_modules_android_builds(root: Path) -> List[Path]:
    """`node_modules/<pkg>/android/build` (and scoped packages) without walking the whole tree."""
    nm = root / "node_modules"
    if not nm.is_dir():
        return []
    out: List[Path] = []

    def consider(pkg: Path) -> None:
        for rel in ("android/app/build", "android/build", "android/app/.cxx", "android/.cxx"):
            cand = pkg / rel
            if cand.is_dir():
                out.append(cand)

    try:
        for entry in nm.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.startswith("@"):
                try:
                    for scoped in entry.iterdir():
                        if scoped.is_dir():
                            consider(scoped)
                except OSError:
                    continue
            else:
                consider(entry)
    except OSError:
        return []
    return out


def discover_artifacts(project: Path) -> List[Path]:
    """Absolute paths of regenerable artifact trees under `project` (deduplicated, deepest-first)."""
    root = project.resolve()
    if not root.is_dir():
        return []
    found: List[Path] = list(_node_modules_android_builds(root))
    walked = 0
    for dirpath, dirnames, _filenames in os.walk(root, topdown=True):
        walked += 1
        if walked > MAX_WALK_DIRS:
            break
        here = Path(dirpath)
        # Prune protected
        dirnames[:] = [d for d in dirnames if d not in PROTECTED_DIR_NAMES]
        try:
            rel = _posix(here.relative_to(root))
        except ValueError:
            dirnames[:] = []
            continue
        if rel == ".":
            rel = ""
        # Match android build trees on this directory (segment-exact, not string suffix)
        if rel and _is_android_build_tree(rel):
            found.append(here)
            dirnames[:] = []
            continue
        # Match artifact basename
        name = here.name
        if name in ARTIFACT_DIR_NAMES and here != root:
            found.append(here)
            dirnames[:] = []
            continue
        # Also catch children named as artifacts without walking into them later
        keep: List[str] = []
        for d in list(dirnames):
            if d in ARTIFACT_DIR_NAMES:
                found.append(here / d)
                continue
            child_rel = f"{rel}/{d}" if rel else d
            if _is_android_build_tree(child_rel):
                found.append(here / d)
            else:
                keep.append(d)
        dirnames[:] = keep

    # Drop a path if an ancestor is also selected
    abs_found = sorted({p.resolve() for p in found if p.exists()}, key=lambda p: len(_posix(p)), reverse=True)
    selected: List[Path] = []
    selected_posix: List[str] = []
    for p in abs_found:
        pp = _posix(p)
        if any(pp == a or pp.startswith(a + "/") for a in selected_posix):
            continue
        # still under project?
        if not is_relative_to(p, root):
            continue
        selected.append(p)
        selected_posix.append(pp)
        if len(selected) >= MAX_REMOVE:
            break
    # delete deepest first so parents are not removed before children when nested (should not nest)
    return sorted(selected, key=lambda p: len(_posix(p)), reverse=True)


def clean_project(project: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    """Remove discovered artifact trees. Returns a report dict; never raises for IO errors."""
    root = Path(project).resolve()
    removed: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    freed = 0
    for target in discover_artifacts(root):
        try:
            if target.is_symlink() or target.is_file():
                sz = target.stat().st_size if target.exists() else 0
                if not dry_run:
                    target.unlink(missing_ok=True)  # type: ignore[call-arg]
                freed += sz
                removed.append({"path": str(target), "bytes": sz, "kind": "file"})
            elif target.is_dir():
                sz = _dir_size_bytes(target)
                if not dry_run:
                    shutil.rmtree(target)
                freed += sz
                removed.append({"path": str(target), "bytes": sz, "kind": "dir"})
        except TypeError:
            # unlink(missing_ok=) on older Python
            try:
                if not dry_run and target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                removed.append({"path": str(target), "bytes": 0, "kind": "path"})
            except OSError as e:
                errors.append({"path": str(target), "error": f"{type(e).__name__}: {e}"})
        except OSError as e:
            errors.append({"path": str(target), "error": f"{type(e).__name__}: {e}"})
    return {
        "project": str(root),
        "dry_run": dry_run,
        "removed": removed,
        "freed_bytes": freed,
        "errors": errors,
    }


def cleanup_enabled(project: Optional[Path] = None) -> bool:
    """Global `cleanup_artifacts` (default True); project `.orkestra/config.json` may set the same key to false."""
    cfg = P.global_config()
    if cfg.get("cleanup_artifacts", True) is False:
        return False
    if project is not None:
        pcfg = P.project_config(project)
        if isinstance(pcfg, dict) and pcfg.get("cleanup_artifacts") is False:
            return False
    return True


def clean_after_run(project: Path) -> Dict[str, Any]:
    """Best-effort cleanup when a run reaches `done`. Disabled via config; never raises."""
    try:
        if not cleanup_enabled(project):
            return {"skipped": True, "reason": "disabled", "freed_bytes": 0, "removed": []}
        return clean_project(project, dry_run=False)
    except Exception as e:  # fail-open
        return {"skipped": True, "reason": f"{type(e).__name__}: {e}", "freed_bytes": 0, "removed": [], "errors": []}


def discover_gradle_transforms(home: Optional[Path] = None) -> List[Path]:
    """Optional: `~/.gradle/caches/*/transforms*` (machine-wide; only for explicit CLI use)."""
    base = (home or Path.home()) / ".gradle" / "caches"
    if not base.is_dir():
        return []
    out: List[Path] = []
    try:
        for p in sorted(base.glob("*/transforms*")):
            if p.is_dir():
                out.append(p.resolve())
    except OSError:
        return []
    return out


def clean_gradle_transforms(*, dry_run: bool = False) -> Dict[str, Any]:
    removed: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    freed = 0
    for target in discover_gradle_transforms():
        try:
            sz = _dir_size_bytes(target)
            if not dry_run:
                shutil.rmtree(target)
            freed += sz
            removed.append({"path": str(target), "bytes": sz, "kind": "dir"})
        except OSError as e:
            errors.append({"path": str(target), "error": f"{type(e).__name__}: {e}"})
    return {"dry_run": dry_run, "removed": removed, "freed_bytes": freed, "errors": errors}


# re-export for typing / tests
__all__ = (
    "ARTIFACT_DIR_NAMES",
    "clean_after_run",
    "clean_gradle_transforms",
    "clean_project",
    "cleanup_enabled",
    "discover_artifacts",
    "discover_gradle_transforms",
)
