"""Per-project long-term memory (git-versionable markdown, progressive disclosure).

  memory/PROJECT.md     stack, structure, commands, conventions (verified by onboarding)
  memory/FEATURES.md    one line per delivered feature  → index loaded every run
  memory/DECISIONS.md   ADR-lite, append-only
  memory/LEARNINGS.md   pitfalls worth not repeating (high bar)
  memory/specs/<capability>.md  living specs (current truth per capability)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

from . import paths
from .util import read_text, today, write_text_atomic

FEATURES_HEADER = ("# Features\n\nOne line per delivered orkestra run (newest last). Details live in "
                   "`.orkestra/features/<id>/`.\n\n| id | başlık | tarih | yetenekler | özet |\n|---|---|---|---|---|\n")


def _clean(cell: str) -> str:
    return re.sub(r"\s+", " ", (cell or "").replace("|", "/")).strip()


def feature_add(project: Path, run_id: str, title: str, summary: str, capabilities: List[str]) -> None:
    path = paths.memory_dir(project) / "FEATURES.md"
    text = read_text(path)
    if "| id |" not in text:
        text = FEATURES_HEADER + ("\n" + text if text.strip() and not text.startswith("# Features") else "")
    lines = [ln for ln in text.splitlines() if not ln.startswith(f"| {run_id} |")]
    row = f"| {run_id} | {_clean(title)} | {today()} | {_clean(', '.join(capabilities))} | {_clean(summary)[:220]} |"
    lines.append(row)
    write_text_atomic(path, "\n".join(lines).rstrip() + "\n")


def append_entry(project: Path, name: str, run_id: str, text: str) -> None:
    path = paths.memory_dir(project) / name
    cur = read_text(path) or f"# {name[:-3].title()}\n\n"
    write_text_atomic(path, cur.rstrip() + f"\n\n- {today()} [{run_id}] {text.strip()}\n")


def digest(project: Path, max_features: int = 8) -> Dict[str, str]:
    mdir = paths.memory_dir(project)
    out: Dict[str, str] = {}
    proj = read_text(mdir / "PROJECT.md")
    # first ~25 meaningful lines of PROJECT.md (the summary block)
    lines = [ln for ln in proj.splitlines() if ln.strip()][:25]
    out["project"] = "\n".join(lines)
    feats = [ln for ln in read_text(mdir / "FEATURES.md").splitlines() if re.match(r"^\| \d{3}-", ln)]
    out["features"] = "\n".join(feats[-max_features:])
    out["features_total"] = str(len(feats))
    learn = [ln for ln in read_text(mdir / "LEARNINGS.md").splitlines() if ln.startswith("- ")]
    out["learnings"] = "\n".join(learn[-6:])
    out["learnings_total"] = str(len(learn))
    specs = sorted(p.stem for p in (mdir / "specs").glob("*.md")) if (mdir / "specs").is_dir() else []
    out["specs"] = ", ".join(specs)
    return out
