"""Evidence layer: deterministic checks that turn model claims into verifiable facts.

  * is_test_path / count_tests      — what counts as a test (language-agnostic heuristics)
  * baseline_content                — the pre-run version of any file (backups / git HEAD / absent)
  * integrity_problems              — removed tests, added skip/only markers, added lint/type suppressions
  * verification_problems           — verification.md may only cite files/tests that exist, every
                                      requirement row needs a verifiable reference
  * evidence_block                  — the report's evidence section, generated from machine state only
"""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import paths
from . import planparse as PP
from . import snapshot as S
from .util import read_json, read_text, sha256_file

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|specs?|integration_test|androidTest|testing)(/|$)"
    r"|[._-](test|spec|tests)\.[A-Za-z0-9]+$"
    r"|(^|/)test_[^/]+\.py$|_test\.(go|py|dart|exs?|rb)$"
    r"|Tests?\.(cs|swift|kt|java|scala)$|(^|/)[^/]+Tests?/[^/]+\.(cs|swift)$", re.I)

TEST_MARKERS = [
    re.compile(r"^\s*(async\s+)?def\s+test\w*\s*\(", re.M),                 # python
    re.compile(r"(?<![\w.])(it|test)(\.each\([^)]*\))?\s*\(\s*[\"'`]", re.M),  # jest/vitest/mocha
    re.compile(r"^\s*@(Test|ParameterizedTest|RepeatedTest)\b", re.M),         # junit/kotlin
    re.compile(r"^\s*func\s+Test[A-Z_]\w*\s*\(", re.M),                       # go
    re.compile(r"\[(Fact|Theory|Test|TestMethod|TestCase)\b", re.M),           # .NET
    re.compile(r"^\s*#\[(tokio::)?test\]", re.M),                             # rust
    re.compile(r"(?<![\w.])(testWidgets|test)\s*\(\s*['\"]", re.M),           # dart
    re.compile(r"^\s*func\s+test\w*\s*\(\s*\)", re.M),                        # XCTest
    re.compile(r"^\s*(it|specify|scenario)\s+['\"]", re.M),                   # rspec
]

SKIP_MARKERS = re.compile(
    r"\.(skip|only)\s*\(|(?<![\w.])x(it|describe|test)\s*\(|(?<![\w.])f(it|describe)\s*\(|@pytest\.mark\.(skip|xfail)"
    r"|@unittest\.(skip|expectedFailure)|pytest\.skip\s*\(|@Disabled\b|@Ignore\b|\bt\.Skip(Now|f)?\s*\("
    r"|#\[ignore\]|\bskip\s*:\s*true\b|\.todo\s*\(|Assert\.Inconclusive|\[Ignore\b|XCTSkip", re.I)

SUPPRESSIONS = re.compile(
    r"@ts-ignore|@ts-nocheck|@ts-expect-error|eslint-disable|#\s*type:\s*ignore|#\s*noqa|//\s*nolint|@SuppressWarnings"
    r"|#pragma\s+warning\s+disable|swiftlint:disable|//\s*ignore:|#\s*pylint:\s*disable|rubocop:disable"
    r"|@Suppress\(|biome-ignore|deno-lint-ignore|prettier-ignore", re.I)

TEXT_EXT = re.compile(r"\.(py|pyi|js|jsx|mjs|cjs|ts|tsx|vue|svelte|go|rs|dart|kt|kts|java|scala|cs|swift|rb|php|ex|exs|"
                      r"c|cc|cpp|h|hpp|m|mm|lua|sh|bash|zsh|sql|ya?ml|json|toml|gradle|xml)$", re.I)


def is_test_path(rel: str) -> bool:
    return bool(TEST_PATH_RE.search(rel.replace("\\", "/")))


def count_tests(text: str) -> int:
    return sum(len(rx.findall(text)) for rx in TEST_MARKERS)


# ---------------------------------------------------------------- baseline content

def baseline_content(project: Path, run_dir: Path, rel: str) -> Optional[str]:
    """Text of `rel` before the run (None = did not exist / unknown)."""
    base = S.load_baseline(run_dir)
    bfile = (base.get("files") or {}).get(rel)
    if bfile is not None:
        if bfile.get("backup"):
            return read_text(run_dir / "snapshot" / "baseline-files" / rel) or ""
        if bfile.get("sha256") is None:
            return None
    edits = read_json(run_dir / "snapshot" / "edits.json", default={}) or {}
    e = edits.get(rel)
    if e is not None:
        if e.get("backup"):
            return read_text(run_dir / "snapshot" / "edits" / rel) or ""
        if not e.get("existed"):
            return None
    if base.get("git"):
        top = S.git_toplevel(project) or project
        try:
            rel_top = str((project / rel).resolve(strict=False).relative_to(top.resolve(strict=False)))
        except ValueError:
            return None
        cp = subprocess.run(["git", "-C", str(project), "show", f"HEAD:{rel_top}"], capture_output=True)
        if cp.returncode == 0:
            return cp.stdout.decode("utf-8", "replace")
        return None
    return None


def _added_lines(before: Optional[str], after: Optional[str]) -> List[str]:
    a = (before or "").splitlines()
    b = (after or "").splitlines()
    out = []
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return out


# ---------------------------------------------------------------- test-change detection (TDD)

def test_files_state(project: Path, run_dir: Path) -> Dict[str, Optional[str]]:
    """{rel: sha256} of test files the run changed so far (plus files whose test count grew)."""
    state: Dict[str, Optional[str]] = {}
    for item in S.changed_files(project, run_dir):
        rel = item["path"]
        p = project / rel
        if is_test_path(rel):
            state[rel] = sha256_file(p) if p.is_file() else None
        elif p.is_file() and TEXT_EXT.search(rel):
            now = read_text(p)
            if count_tests(now) > count_tests(baseline_content(project, run_dir, rel) or ""):
                state[rel] = sha256_file(p)   # inline tests (e.g. Rust #[cfg(test)])
    return state


# ---------------------------------------------------------------- integrity

def approved_exceptions(project: Path, run_id: str) -> Set[str]:
    """Paths listed under `## İstisnalar` in the APPROVED plan (user-approved exceptions only)."""
    plan = read_text(paths.run_dir(project, run_id) / "approved" / "plan.md")
    sec = PP.section(plan, "İstisnalar", "Exceptions") or ""
    return {p.strip() for p in re.findall(r"`([^`]+)`", sec)}


def integrity_problems(project: Path, run_id: str) -> List[str]:
    rdir = paths.run_dir(project, run_id)
    allowed = approved_exceptions(project, run_id)
    problems: List[str] = []
    for item in S.changed_files(project, rdir):
        rel = item["path"]
        if rel in allowed or not TEXT_EXT.search(rel):
            continue
        p = project / rel
        before = baseline_content(project, rdir, rel)
        after = read_text(p) if p.is_file() else None
        if is_test_path(rel) or count_tests(before or "") > 0:
            n_before, n_after = count_tests(before or ""), count_tests(after or "")
            if after is None and n_before:
                problems.append(f"{rel}: test file deleted ({n_before} tests)")
            elif n_after < n_before:
                problems.append(f"{rel}: test count dropped {n_before} → {n_after}")
        added = _added_lines(before, after)
        for line in added:
            if SKIP_MARKERS.search(line) and (is_test_path(rel) or count_tests(after or "")):
                problems.append(f"{rel}: adds a skip/only/xfail marker: {line.strip()[:100]}")
                break
        for line in added:
            if SUPPRESSIONS.search(line):
                problems.append(f"{rel}: adds a lint/type suppression: {line.strip()[:100]}")
                break
    if problems:
        problems.append("Fix the cause instead. If the user truly wants an exception, list the path under "
                        "`## İstisnalar` in plan.md with the reason and get the plan re-approved.")
    return problems


# ---------------------------------------------------------------- verification references

PATH_TOKEN = re.compile(r"(?<![\w/.-])((?:\.{0,2}/)?[\w.@+-]+(?:/[\w.@+-]+)+\.[A-Za-z0-9]{1,8})(?:::([\w.\[\]-]+))?")
TEST_NAME_TOKEN = re.compile(r"`([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*)`")


def _project_test_corpus(project: Path, limit_files: int = 4000, limit_bytes: int = 30_000_000) -> str:
    chunks: List[str] = []
    total = 0
    count = 0
    for p in project.rglob("*"):
        if count >= limit_files or total >= limit_bytes:
            break
        try:
            rel = str(p.relative_to(project))
        except ValueError:
            continue
        if any(part in S.SKIP_DIRS for part in Path(rel).parts):
            continue
        if not p.is_file() or not TEXT_EXT.search(rel):
            continue
        if not is_test_path(rel) and "test" not in p.name.lower():
            continue
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.append(txt)
        total += len(txt)
        count += 1
    return "\n".join(chunks)


def verification_problems(project: Path, run_id: str, spec_text: str, ver_text: str,
                          gate_names: Iterable[str]) -> List[str]:
    """Anti-hallucination checks on verification.md (on top of planparse.check_trace)."""
    rdir = paths.run_dir(project, run_id)
    fdir = paths.feature_dir(project, run_id)
    problems: List[str] = []
    gates = {g.lower() for g in gate_names}
    corpus: Optional[str] = None
    ids = PP.spec_ids(spec_text)
    rows: Dict[str, List[str]] = {}
    for line in ver_text.splitlines():
        for i in re.findall(r"\b((?:FR|SC)-\d{3})\b", line):
            rows.setdefault(i, []).append(line)

    def exists(token: str) -> bool:
        t = token.lstrip("./")
        for base in (project, fdir, rdir, rdir / "evidence"):
            if (base / token).exists() or (base / t).exists():
                return True
        return False

    for line in ver_text.splitlines():
        for m in PATH_TOKEN.finditer(line):
            token, test_name = m.group(1), m.group(2)
            if token.startswith(("http", "www.")) or "://" in line[max(0, m.start() - 8):m.start()]:
                continue
            if not exists(token):
                problems.append(f"verification.md cites a file that does not exist: {token}")
            elif test_name:
                target = next((b / token for b in (project, fdir, rdir) if (b / token).exists()), None)
                last = test_name.split(".")[-1].split("[")[0]
                if target is not None and target.is_file() and last not in read_text(target):
                    problems.append(f"verification.md cites test `{test_name}` which is not in {token}")
    for i in ids:
        lines = rows.get(i) or []
        joined = " ".join(lines)
        ok_ref = False
        for m in PATH_TOKEN.finditer(joined):
            if exists(m.group(1)):
                ok_ref = True
        for name in TEST_NAME_TOKEN.findall(joined):
            if "test" in name.lower():
                if corpus is None:
                    corpus = _project_test_corpus(project)
                if name.split(".")[-1] in corpus:
                    ok_ref = True
                else:
                    problems.append(f"{i}: cites test `{name}` that does not exist in the project's tests")
        if any(g and re.search(rf"\b{re.escape(g)}\b", joined.lower()) for g in gates):
            ok_ref = True
        if re.search(r"\b(manual|manuel|elle)\b", joined, re.I) and not re.search(r"evidence/\S+\.log", joined):
            problems.append(f"{i}: a manual check must cite its recorded log (`orkestra evidence {i} -- <cmd>` → evidence/…log)")
        if lines and not ok_ref:
            problems.append(f"{i}: no verifiable reference (existing test file/test name, gate name or evidence log)")
    return sorted(set(problems), key=problems.index)


# ---------------------------------------------------------------- report evidence block

BEGIN = "<!-- orkestra:evidence:begin (generated by `orkestra report evidence` — do not edit) -->"
END = "<!-- orkestra:evidence:end -->"


def evidence_block(project: Path, run_id: str) -> str:
    from .state import load_state  # local import to avoid a cycle
    st = load_state(project, run_id)
    rdir = paths.run_dir(project, run_id)
    lines: List[str] = [BEGIN, "### Makine kanıtı", ""]
    appr = read_json(rdir / "approval.json")
    if isinstance(appr, dict):
        lines.append(f"- Onay: **{appr.get('decision')}** · kaynak {appr.get('source')} · {appr.get('at')} · "
                     f"plan özeti `{str(appr.get('hash'))[:12]}`")
    else:
        lines.append("- Onay: kayıt yok")
    base = {r["name"]: r for r in ((read_json(rdir / "gates-baseline.json") or {}).get("results") or [])}
    ver = read_json(rdir / "gates-verify.json") or {}
    lines += ["", "| Gate | Baseline | Son verify |", "|---|---|---|"]
    for r in ver.get("results") or []:
        b = base.get(r["name"])
        lines.append(f"| {r['name']} | {'PASS' if b and b['ok'] else ('FAIL' if b else '—')} | {'PASS' if r['ok'] else 'FAIL'} |")
    comp = ver.get("comparison") or {}
    lines.append(f"\nRegresyon: {'yok' if comp.get('ok') else 'VAR / doğrulanmadı'}"
                 + (f" · baştan beri kırık: {', '.join(comp['pre_existing_failures'])}" if comp.get("pre_existing_failures") else ""))
    lines += ["", "| Görev | Red (exit) | Green (exit) | Not |", "|---|---|---|---|"]
    for tid in sorted(st.get("tasks") or {}):
        t = st["tasks"][tid]
        red, green = t.get("red") or {}, t.get("green") or {}
        note = "; ".join(str(x) for x in (green.get("deviations") or []) + (red.get("deviations") or [])
                         + ([green["foreign_note"]] if green.get("foreign_note") else []))
        lines.append(f"| {tid} {t.get('status')} | {red.get('exit', '—')} | {green.get('exit', '—')} | "
                     f"{('TDD n/a' if t.get('tdd') == 'n/a' else '')}{(' · ' if note and t.get('tdd') == 'n/a' else '')}{note} |")
    integ = integrity_problems(project, run_id)
    lines.append(f"\nTest bütünlüğü: {'temiz' if not integ else 'SORUN: ' + '; '.join(integ[:-1])}")
    exc = approved_exceptions(project, run_id)
    if exc:
        lines.append(f"Onaylı istisnalar: {', '.join(sorted(exc))}")
    changed = S.changed_files(project, rdir)
    lines.append(f"Değişen dosyalar ({len(changed)}): " + ", ".join(f"{c['path']} ({c['change']})" for c in changed[:40]))
    tri = read_text(paths.feature_dir(project, run_id) / "reviews" / "triage.md")
    counts = {v: len(re.findall(rf"\|\s*{v}\s*\|", tri, re.I)) for v in ("fix", "defer", "reject")}
    lines.append(f"Review triage: {counts['fix']} düzeltildi · {counts['defer']} ertelendi · {counts['reject']} reddedildi")
    lines.append("Commit/push: yapılmadı (değişiklikler çalışma ağacında)")
    lines.append(END)
    return "\n".join(lines)


def _norm(block: str) -> str:
    return re.sub(r"\s+", " ", block).strip()


def report_has_current_block(project: Path, run_id: str) -> Tuple[bool, str]:
    rep = read_text(paths.feature_dir(project, run_id) / "report.md")
    m = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), rep, re.S)
    if not m:
        return False, "report.md lacks the machine evidence block — run `orkestra report evidence --write`"
    if _norm(m.group(0)) != _norm(evidence_block(project, run_id)):
        return False, "report.md evidence block is outdated or was edited — run `orkestra report evidence --write`"
    return True, ""


def write_block(project: Path, run_id: str) -> Path:
    from .util import write_text_atomic
    path = paths.feature_dir(project, run_id) / "report.md"
    rep = read_text(path)
    block = evidence_block(project, run_id)
    pattern = re.escape(BEGIN) + r".*?" + re.escape(END)
    if re.search(pattern, rep, re.S):
        rep = re.sub(pattern, lambda _m: block, rep, flags=re.S)
    else:
        rep = (rep.rstrip() + "\n\n" if rep.strip() else "") + block + "\n"
    write_text_atomic(path, rep)
    return path
