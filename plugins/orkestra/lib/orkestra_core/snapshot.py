"""Baseline snapshots, change attribution and rollback — without any git writes.

Model:
  * At run start we record the working-tree baseline:
      - git repos: HEAD sha + every dirty/untracked path with its content hash, and a
        backup copy of each such file (so the user's uncommitted work is restorable).
      - non-git dirs: a content-hash manifest of project files.
  * Before the first Edit/Write of a path during a run, the PreToolUse hook backs up
    the original (or records that it did not exist).
  * "Changed by this run" = paths whose content differs from the baseline.
  * Rollback restores from backups, or from `git show HEAD:<path>` (read-only git),
    and deletes files the run created. It never runs a mutating git command.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .util import is_relative_to, now_iso, read_json, sha256_file, write_json_atomic

MAX_BACKUP_BYTES = 8 * 1024 * 1024        # per file
MAX_BACKUP_TOTAL_BYTES = 300 * 1024 * 1024  # per baseline
MAX_BACKUP_FILES = 3000
MAX_SCAN_FILES = 25000
# untracked paths under these directories are tool output, never attributed to a run
NOISE_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".next",
              ".nuxt", ".turbo", ".cache", ".parcel-cache", "coverage", "Pods", "DerivedData", ".gradle", ".dart_tool",
              ".expo", ".svelte-kit", ".terraform", ".serverless", ".vercel", ".netlify"}
SKIP_DIRS = {
    ".git", ".orkestra", "node_modules", ".next", ".nuxt", ".svelte-kit", "dist", "build", "out",
    ".turbo", ".cache", ".parcel-cache", "coverage", ".venv", "venv", "env", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", ".gradle", ".dart_tool", "Pods",
    "DerivedData", ".expo", ".idea", ".vscode", "vendor", "bin", "obj", "Library", "Temp",
    ".terraform", ".serverless", ".vercel", ".netlify",
}


def _git(project: Path, *args: str, binary: bool = False) -> Tuple[int, Any]:
    try:
        cp = subprocess.run(["git", "-C", str(project), *args], capture_output=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 127, b"" if binary else ""
    return cp.returncode, (cp.stdout if binary else cp.stdout.decode("utf-8", "replace"))


def is_git_repo(project: Path) -> bool:
    rc, out = _git(project, "rev-parse", "--is-inside-work-tree")
    return rc == 0 and out.strip() == "true"


def git_toplevel(project: Path) -> Optional[Path]:
    rc, out = _git(project, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if rc == 0 and out.strip() else None


def _porcelain(project: Path) -> Dict[str, str]:
    """path (relative to project) -> 2-char status. Uses -z for robust parsing."""
    rc, out = _git(project, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", ".", binary=True)
    if rc != 0:
        return {}
    top = git_toplevel(project) or project
    entries = out.decode("utf-8", "replace").split("\0")
    result: Dict[str, str] = {}
    i = 0
    while i < len(entries):
        e = entries[i]
        if not e:
            i += 1
            continue
        status, rel = e[:2], e[3:]
        if status[0] in "RC":  # rename/copy: next entry is the source path
            i += 1
        abs_path = (top / rel).resolve(strict=False)
        if is_relative_to(abs_path, project.resolve(strict=False)):
            relp = str(abs_path.relative_to(project.resolve(strict=False)))
            noisy = status.strip() == "??" and any(part in NOISE_DIRS for part in Path(relp).parts)
            if not relp.startswith(".orkestra/") and relp != ".orkestra" and not noisy:
                result[relp] = status
        i += 1
    return result


def _scan_manifest(project: Path) -> Dict[str, str]:
    """Content hashes of project files. Contains the key "\0incomplete" when the scan hit its cap."""
    manifest: Dict[str, str] = {}
    count = 0
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".tmp-")]
        for f in files:
            p = Path(root) / f
            try:
                if p.stat().st_size > MAX_BACKUP_BYTES:
                    continue
            except OSError:
                continue
            h = sha256_file(p)
            if h:
                manifest[str(p.relative_to(project))] = h
            count += 1
            if count >= MAX_SCAN_FILES:
                manifest["\0incomplete"] = "1"
                return manifest
    return manifest


def take_baseline(project: Path, run_dir: Path) -> Dict[str, Any]:
    project = project.resolve(strict=False)
    snap = run_dir / "snapshot"
    files_dir = snap / "baseline-files"
    files_dir.mkdir(parents=True, exist_ok=True)
    info: Dict[str, Any] = {"at": now_iso(), "git": False, "head": None, "files": {}, "manifest": None}
    if is_git_repo(project):
        info["git"] = True
        rc, head = _git(project, "rev-parse", "HEAD")
        info["head"] = head.strip() if rc == 0 else None
        total_bytes = 0
        backups = 0
        for rel, status in _porcelain(project).items():
            p = project / rel
            entry: Dict[str, Any] = {"status": status}
            if p.is_file():
                entry["sha256"] = sha256_file(p)
                size = p.stat().st_size
                if size <= MAX_BACKUP_BYTES and backups < MAX_BACKUP_FILES and total_bytes + size <= MAX_BACKUP_TOTAL_BYTES:
                    dest = files_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, dest)
                    entry["backup"] = True
                    backups += 1
                    total_bytes += size
            else:
                entry["sha256"] = None  # deleted in working tree at baseline
            info["files"][rel] = entry
    else:
        info["manifest"] = _scan_manifest(project)
    write_json_atomic(snap / "baseline.json", info)
    return info


def load_baseline(run_dir: Path) -> Dict[str, Any]:
    return read_json(run_dir / "snapshot" / "baseline.json", default={}) or {}


def backup_before_edit(project: Path, run_dir: Path, abs_path: Path) -> None:
    """Called by the PreToolUse hook before Edit/Write. Idempotent per path."""
    project = project.resolve(strict=False)
    abs_path = abs_path.resolve(strict=False)
    if not is_relative_to(abs_path, project):
        return
    rel = str(abs_path.relative_to(project))
    if rel.startswith(".orkestra"):
        return
    manifest_path = run_dir / "snapshot" / "edits.json"
    manifest = read_json(manifest_path, default={}) or {}
    if rel in manifest:
        return
    entry: Dict[str, Any] = {"at": now_iso(), "existed": abs_path.exists()}
    if abs_path.is_file():
        entry["sha256"] = sha256_file(abs_path)
        if abs_path.stat().st_size <= MAX_BACKUP_BYTES:
            dest = run_dir / "snapshot" / "edits" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(abs_path, dest)
            entry["backup"] = True
    manifest[rel] = entry
    write_json_atomic(manifest_path, manifest)


def record_touch(project: Path, run_dir: Path, abs_path: Path) -> None:
    """PostToolUse: remember a path the run wrote (useful for non-git projects)."""
    project = project.resolve(strict=False)
    abs_path = abs_path.resolve(strict=False)
    if not is_relative_to(abs_path, project):
        return
    rel = str(abs_path.relative_to(project))
    if rel.startswith(".orkestra"):
        return
    p = run_dir / "snapshot" / "touched.json"
    data = read_json(p, default=[]) or []
    if rel not in data:
        data.append(rel)
        write_json_atomic(p, data)


def changed_files(project: Path, run_dir: Path) -> List[Dict[str, Any]]:
    """List of {path, change: modified|created|deleted} attributable to this run."""
    project = project.resolve(strict=False)
    base = load_baseline(run_dir)
    edits = read_json(run_dir / "snapshot" / "edits.json", default={}) or {}
    touched = read_json(run_dir / "snapshot" / "touched.json", default=[]) or []
    out: List[Dict[str, Any]] = []

    def cur_hash(rel: str) -> Optional[str]:
        p = project / rel
        return sha256_file(p) if p.is_file() else None

    if base.get("git"):
        now = _porcelain(project)
        base_files: Dict[str, Any] = base.get("files", {})
        candidates = set(now) | set(base_files) | set(edits) | set(touched)
        for rel in sorted(candidates):
            if rel.startswith(".orkestra"):
                continue
            h_now = cur_hash(rel)
            if rel in base_files:
                h_base = base_files[rel].get("sha256")
                if h_now != h_base:
                    out.append({"path": rel, "change": _kind(h_base, h_now)})
            elif rel in now:
                status = now[rel]
                if status.strip() == "??" or status[0] == "A" or status[1] == "A":
                    out.append({"path": rel, "change": "created"})
                elif "D" in status:
                    out.append({"path": rel, "change": "deleted"})
                else:
                    out.append({"path": rel, "change": "modified"})
            elif rel in edits:
                e = edits[rel]
                if e.get("sha256") != h_now:
                    out.append({"path": rel, "change": _kind(e.get("sha256") if e.get("existed") else None, h_now)})
    else:
        manifest: Dict[str, str] = dict(base.get("manifest") or {})
        incomplete = bool(manifest.pop("\0incomplete", None))
        current = _scan_manifest(project)
        current_incomplete = bool(current.pop("\0incomplete", None))
        known = set(edits) | set(touched)
        for rel in sorted(set(manifest) | set(current) | known):
            if (incomplete or current_incomplete) and rel not in known and (rel not in manifest or rel not in current):
                continue  # scan cap reached: only trust paths orkestra tools touched
            h_base = manifest.get(rel)
            if rel in edits and rel not in manifest:
                h_base = edits[rel].get("sha256") if edits[rel].get("existed") else None
            h_now = current.get(rel, cur_hash(rel))
            if h_base != h_now:
                out.append({"path": rel, "change": _kind(h_base, h_now)})
    return out


def _kind(h_base: Optional[str], h_now: Optional[str]) -> str:
    if h_base is None and h_now is not None:
        return "created"
    if h_base is not None and h_now is None:
        return "deleted"
    return "modified"


def rollback(project: Path, run_dir: Path, dry_run: bool = True, include_all: bool = False) -> List[Dict[str, str]]:
    """Revert files to their pre-run state.

    Default scope = files orkestra tools edited/touched during the run (Edit/Write and detected shell writes).
    Other differences (e.g. a file the USER created meanwhile, or output of an undetected command) are listed
    with scope "extra" and only reverted with include_all=True. Before any overwrite/delete the current content
    is copied to <run>/rollback-<ts>/, so a rollback is itself reversible.
    """
    project = project.resolve(strict=False)
    base = load_baseline(run_dir)
    edits = read_json(run_dir / "snapshot" / "edits.json", default={}) or {}
    touched = set(read_json(run_dir / "snapshot" / "touched.json", default=[]) or [])
    safety = run_dir / f"rollback-{int(time.time())}"
    actions: List[Dict[str, str]] = []
    for item in changed_files(project, run_dir):
        rel = item["path"]
        target = project / rel
        scope = "run" if (rel in edits or rel in touched) else "extra"
        bfile = (base.get("files") or {}).get(rel)
        action: Dict[str, str] = {"path": rel, "scope": scope}
        src: Optional[Path] = None
        if rel in edits and edits[rel].get("backup"):
            src = run_dir / "snapshot" / "edits" / rel
            action["how"] = "restore pre-edit copy"
        elif rel in edits and not edits[rel].get("existed"):
            action["how"] = "delete (created by this run)"
        elif bfile is not None:
            if bfile.get("backup"):
                src = run_dir / "snapshot" / "baseline-files" / rel
                action["how"] = "restore baseline copy (your uncommitted version)"
            elif bfile.get("sha256") is None:
                action["how"] = "delete (did not exist at baseline)"
            else:
                action["how"] = "UNRESTORABLE (no backup — file was too large)"
        elif base.get("git"):
            action["how"] = "delete (did not exist at baseline)" if item["change"] == "created" \
                else "restore from HEAD (git show, read-only)"
        else:
            action["how"] = "delete (did not exist at baseline)" if item["change"] == "created" \
                else "UNRESTORABLE (non-git, no backup)"
        actions.append(action)
        if dry_run or action["how"].startswith("UNRESTORABLE") or (scope == "extra" and not include_all):
            if scope == "extra" and not include_all and not dry_run:
                action["how"] += " — SKIPPED (not touched by orkestra; use --all)"
            continue
        if target.is_file():
            keep = safety / rel
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, keep)
            action["saved"] = str(keep)
        if src is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
        elif action["how"].startswith("delete"):
            if target.is_file() or target.is_symlink():
                target.unlink()
        elif action["how"].startswith("restore from HEAD"):
            top = git_toplevel(project) or project
            rel_top = str((project / rel).resolve(strict=False).relative_to(top.resolve(strict=False)))
            rc, data = _git(project, "show", f"HEAD:{rel_top}", binary=True)
            if rc == 0:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            else:
                action["how"] += " — FAILED"
    return actions
