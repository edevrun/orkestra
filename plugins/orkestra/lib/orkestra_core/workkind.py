"""Classify a request as software, writing, or general. Stdlib plus ascii_fold. No network."""

from __future__ import annotations

import re
from pathlib import Path

from .messages.protocol import ascii_fold

WORK_KINDS = ("software", "writing", "general")

# Markers are read after ascii_fold and before hyphens between letters are removed.
_MARKER = re.compile(
    r"(?<![a-z0-9])(?:kind\s*:\s*(software|writing|general)|is\s*:\s*(yazilim|makale|genel))(?![a-z0-9])"
)
_IS_KIND = {"yazilim": "software", "makale": "writing", "genel": "general"}

# Whole words. A trailing vowel run still counts, so `fonksiyonu` is `fonksiyon`.
_SOFTWARE = frozenset((
    "kod", "code", "fonksiyon", "function", "metod", "method", "bug", "bugfix", "hotfix", "fix",
    "test", "unittest", "api", "endpoint", "refactor", "sinif", "class", "bilesen", "component",
    "modul", "module", "script", "ekran", "screen", "typo",
))
_SOFTWARE_PHRASES = ("yazim hatasi", "hata duzelt")
_WRITING_STEMS = ("makale", "article", "rapor", "report", "cevir", "translat")
_TAIL = re.compile(r"[aeiou]+|l(?:ar|er)[aeiou]*")
_GENERAL = frozenset((
    "alisveris", "shopping", "tarif", "recipe", "eposta", "email", "toplanti", "meeting",
    "checklist", "ajanda", "agenda", "siir", "poem",
))


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text)


def _phrase(text: str, phrase: str) -> bool:
    body = r"\s+".join(re.escape(part) for part in phrase.split())
    return re.search(rf"(?<![a-z0-9]){body}(?![a-z0-9])", text) is not None


def _stem_hit(word: str, stems) -> bool:
    """True when `word` is a stem plus only vowels or a plural (`fonksiyonlari`, `ceviri`)."""
    for stem in stems:
        if not word.startswith(stem):
            continue
        rest = word[len(stem):]
        if rest == "" or _TAIL.fullmatch(rest):
            return True
    return False


def _software_word(word: str) -> bool:
    return _stem_hit(word, _SOFTWARE)


def _writing_word(word: str) -> bool:
    return _stem_hit(word, _WRITING_STEMS)


def _marked(folded: str) -> list[str]:
    return [m.group(1) or _IS_KIND[m.group(2)] for m in _MARKER.finditer(folded)]


def kind_of(st: object) -> str:
    """Read work_kind. A missing field or any value outside the three kinds is software."""
    if isinstance(st, dict):
        kind = st.get("work_kind")
        if kind in WORK_KINDS:
            return kind
    return "software"


def classify(text: object) -> str:
    """Return software, writing, or general. The same text always returns the same kind."""
    if not isinstance(text, str) or not text.strip():
        return "software"
    folded = ascii_fold(text)
    marked = _marked(folded)
    if "software" in marked:
        return "software"
    if marked:
        return marked[0]
    hinted = re.sub(r"(?<=[a-z])-+(?=[a-z])", "", folded)
    words = _words(hinted)
    if any(_phrase(hinted, phrase) for phrase in _SOFTWARE_PHRASES) or any(_software_word(w) for w in words):
        return "software"
    if _phrase(hinted, "deneme yaz") or "essay" in words or any(_writing_word(w) for w in words):
        return "writing"
    if _phrase(hinted, "kontrol listesi") or any(w in _GENERAL for w in words):
        return "general"
    return "software"


def _allowed_deliverable(path: str, run_id: str) -> bool:
    if path == ".orkestra/current/deliverable.md":
        return True
    return bool(run_id) and path == f".orkestra/features/{run_id}/deliverable.md"


def _content_reason(raw: str) -> bool:
    folded = ascii_fold(raw).strip().lower()
    return re.fullmatch(r"(?:n/a|na|yok|gerekmez|none)\s*\((?:content check|icerik kontrolu)\)", folded) is not None


def _paths_in_body(body: str) -> list[str]:
    """Paths on every Files line, backticked or bare, including a line that a later Files line replaced."""
    found: list[str] = []
    for line in body.splitlines():
        fm = re.match(r"^\s*[-*]\s+\**([^:*]+?)\**\s*:\s*(.*)$", line)
        if not fm:
            continue
        label = ascii_fold(fm.group(1)).strip().lower()
        if label not in {"files", "file", "dosyalar", "dosya"}:
            continue
        raw = fm.group(2)
        found.extend(m.group(1).strip() for m in re.finditer(r"`([^`]+)`", raw) if m.group(1).strip())
        bare = re.sub(r"`[^`]*`", " ", raw)
        bare = re.sub(r"\([^)]*\)", " ", bare)
        for part in re.split(r"[,;]", bare):
            token = part.strip().strip("`")
            if token and ("/" in token or "." in token):
                found.append(token)
    return found


def nonsoftware_plan_problems(tasks, run_id: str) -> list[str]:
    """writing and general: every file is the run deliverable, and TDD is a content check."""
    rid = run_id if isinstance(run_id, str) else ""
    problems: list[str] = []
    for task in tasks:
        seen = []
        for path in list(task.files) + _paths_in_body(getattr(task, "body", "") or ""):
            if path in seen:
                continue
            seen.append(path)
            if not _allowed_deliverable(path, rid):
                problems.append(f"{task.id}: `{path}` is not an allowed deliverable path")
        fields = task.fields if isinstance(getattr(task, "fields", None), dict) else {}
        raw = str(fields.get("tdd") or "")
        if task.tdd != "n/a" or not _content_reason(raw):
            problems.append(f"{task.id}: TDD must be n/a (content check) or n/a (icerik kontrolu)")
    return problems


# Exact level-2 titles. ### is not a slot. Compared as written: no case fold, no ASCII fold.
_ISTEK = "\u0130stek"
_SINIRLAR = "S\u0131n\u0131rlar"
_SLOTS = (
    (_ISTEK, frozenset({_ISTEK, "Request"})),
    ("Teslim", frozenset({"Teslim", "Deliverable"})),
    (_SINIRLAR, frozenset({_SINIRLAR, "Boundaries"})),
)
_EM_DASH = "\u2014"


def _level2_title(line: str) -> str:
    if not line.startswith("##") or line.startswith("###"):
        return ""
    rest = line[2:]
    if not rest or rest[0] not in " \t":
        return ""
    return rest.strip()


def deliverable_problems(text: str) -> list[str]:
    """Empty text, a U+2014, or a missing exact level-2 slot. U+2013 and '-' are allowed."""
    if not isinstance(text, str) or not text.strip():
        return ["deliverable.md empty"]
    problems: list[str] = []
    if _EM_DASH in text:
        problems.append("deliverable.md contains U+2014")
    titles = {_level2_title(line) for line in text.splitlines()}
    titles.discard("")
    for label, names in _SLOTS:
        if titles.isdisjoint(names):
            problems.append(f"deliverable.md missing heading {label}")
    return problems


def deliverable_file_problems(path: Path) -> list[str]:
    """Read one deliverable.md. Missing, not UTF-8, or deliverable_problems."""
    path = Path(path)
    if not path.is_file():
        return ["deliverable.md missing"]
    try:
        raw = path.read_bytes()
    except OSError:
        return ["deliverable.md missing"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ["deliverable.md is not UTF-8"]
    return deliverable_problems(text)
