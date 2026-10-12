"""Deterministic intent detection on the USER's prompt text (never on model output).

* git_grants(prompt)  -> categories the user explicitly asked for in THIS prompt:
      commit, push, force, amend, branch, pr, gh_write, discard, sync, tag, stash, hard_reset
  Mentions are not requests: "son commit'te ne değişti?", "commit mesajı öner",
  "commitleme", "don't push" grant nothing.
* approval(prompt)    -> "approve" | "reject" | None, used only while a plan awaits approval.
"""
from __future__ import annotations

import re
from typing import List, Optional, Set

from .util import norm_lower

_NOUNISH = (r"(?!\s*(mesaj|message|msg|hash|id\b|geçmiş|history|log|sayısı|count|listesi|list|bildirim|notification|"
            r"servis|service|server|sunucu|token|izni|permission|'?(i|ı|u|ü)n\b|'?te\b|'?de\b|'?ler))")
_QUESTION_TAIL = re.compile(r"\b(commit|komit|push)\w*\s+(mı|mi|mu|mü)\b|\b(commit|komit|push)'?l(e|a)(n)?d(ı|i)(n|niz)?\s*m(ı|i)", re.I)
_PLURAL = re.compile(r"\b(commit|komit)'?ler\w*|\bpush'?lar\b", re.I)
_LEAD = r"(^|[.!?,;:]\s*|\b(please|and|then|now|also|just|only|ve|sonra|lütfen|şimdi|hemen|sadece|yalnızca|bir\s+de|da|de)\s+)"

_COMMIT_POS = re.compile(
    r"\b(commit|komit)'?ler\s*m[ıi]s[ıi]n\w*"
    r"|\b(commit|komit)'?le(?!n?m[ae]|r\b|ri\b|rin|re\b|rde|rden|ri\w)\w*"   # commitle, commitleyip (not commitler/-leri)
    r"|\b(commit|komit)\s+(at|et|yap|oluştur|create)(?!m[ae])\b"  # commit at / commit et
    r"|\bcommit\s+(it|this|that|these|them|all|everything|the\s+changes|changes|my\s+changes|your\s+changes|the\s+fix|the\s+work)\b"
    r"|" + _LEAD + r"commit\b" + _NOUNISH +
    r"|\bgit\s+commit\b", re.I)
_COMMIT_NEG = re.compile(r"\b(commit|komit)'?(le)?n?m[ae]\w*|\b(commit|komit)\s+(atma|etme|yapma)\w*", re.I)

_PUSH_POS = re.compile(
    r"\bpush'?lar\s*m[ıi]s[ıi]n\w*|\bpush'?la(?!n?m[ae]|r\b|rı|rın)\w*"
    r"|\bpush\s+(et|yap|at)(?!m[ae])\b"
    r"|\bpush\s+(it|this|that|them|the\s+changes|changes|everything|to\s+\w+|origin|upstream)\b"
    r"|" + _LEAD + r"push\b" + _NOUNISH +
    r"|\bgit\s+push\b", re.I)
_PUSH_NEG = re.compile(r"\bpush'?(la)?n?m[ae]\w*|\bpush\s+(etme|yapma|atma)\w*", re.I)

_NEG_EN = re.compile(r"\b(don'?t|do\s+not|never|no|without|avoid|dont)\s+(\w+\s+){0,3}?(commit|push)\w*", re.I)

_FORCE = re.compile(r"\b(force[\s-]*push|push\s+--force|--force-with-lease|zorla\s*push|force\s+ile)(?!\s*(yapma|etme|atma|kullanma)|\w*\s+yapma)", re.I)
_AMEND = re.compile(r"(--amend|\bamend\w*)\b", re.I)
_BRANCH = re.compile(
    r"\b(branch|dal)\w*\s+(aç|oluştur|yarat|create|değiştir|switch)(?!m[ae])"
    r"|\b\S+'?(e|a|ye|ya)\s+(geç|switch)\b(?!m[ae])"
    r"|\b(switch|checkout)\s+(to\s+)?\S+"
    r"|\b(yeni|new|create|switch\s+to|checkout)\s+(a\s+)?(branch|dal)\b"
    r"|checkout\s+-b|switch\s+-c|\bbranch'?(e|a)\s+geç", re.I)
_PR = re.compile(r"\b(pr|pull\s*request|merge\s*request)\b\s*(aç|oluştur|create|open|gönder)(?!m[ae])"
                 r"|\b(open|create)\s+(a\s+)?(pr|pull\s*request)\b", re.I)
_GH_WRITE = re.compile(r"\b(issue|release|label|gist)\w*\s+(aç|oluştur|kapat|yaz|ekle|create|close|open|publish|yayınla)(?!m[ae])"
                       r"|\b(pr|issue)\w*\s+(yorum|comment)\w*\s+(yaz|ekle|bırak|post|add)", re.I)
_SYNC = re.compile(r"\b(merge|rebase|pull|cherry-pick|revert)\w*\s+(et|yap|çek|it|this|that|from|origin|main|master|develop|the)(?!m[ae])"
                   r"|\b(son\s+)?değişiklikleri\s+çek(?!m[ae])"
                   r"|\bgit\s+(merge|rebase|pull|cherry-pick|revert)\b|\b(pull|çek)\s+(the\s+)?(latest|son\s+değişiklikleri)", re.I)
_TAG = re.compile(r"\b(tag|etiket)\w*\s+(oluştur|at|ekle|create|bas)(?!m[ae])|\bcreate\s+(a\s+)?tag\b|\bgit\s+tag\s+\S|\btag'?l[ae]\w*", re.I)
_STASH = re.compile(r"\bstash\w*\s*(le|yap|et|it|this|changes|pop|apply)?(?!m[ae])|\bgit\s+stash\b", re.I)
_HARD_RESET = re.compile(r"reset\s+--hard|hard\s+reset|git\s+clean|untracked\s+(dosyaları|files)\s*(sil|temizle|remove|delete)", re.I)
_DISCARD = re.compile(r"\b(değişiklikleri\s+(at|sil|geri\s*al)(?!m[ae])|discard\s+(the\s+)?changes|reset\s+--hard)\b", re.I)


def git_grants(prompt: str) -> List[str]:
    text = norm_lower(prompt or "")
    cats: Set[str] = set()
    if text.strip().startswith(("/orkestra:ship", "/orkestra-ship")):         # Claude Code / Cursor skill names
        cats |= {"commit", "push"}
    if text.strip().startswith(("/orkestra:rollback", "/orkestra-rollback")):
        cats.add("discard")

    commit_ok = bool(_COMMIT_POS.search(text)) and not _COMMIT_NEG.search(text)
    push_neg = [m for m in _PUSH_NEG.finditer(text) if not re.search(r"force[\s-]*$", text[:m.start()])]
    push_ok = bool(_PUSH_POS.search(text)) and not push_neg
    if _QUESTION_TAIL.search(text):
        q = _QUESTION_TAIL.search(text).group(0).lower()  # type: ignore[union-attr]
        if q.startswith(("commit", "komit")):
            commit_ok = False
        else:
            push_ok = False
    if re.search(r"\bpush\s+(bildirim|notification|servis|service|server|sunucu|token|izni|permission)", text):
        push_ok = bool(re.search(r"\bgit\s+push\b|\bpush'?la(?!n?m[ae])\w*\s*(ve|and)?\s*$", text))
    if _PLURAL.search(text) and not re.search(r"\bcommit'?le(yip|yin|\b|sene)|\bcommit'?ler\s*m[ıi]s[ıi]n", text):
        commit_ok = False
    for m in _NEG_EN.finditer(text):
        verb = m.group(3).lower()
        if verb == "commit":
            commit_ok = False
        elif verb == "push":
            push_ok = False

    if commit_ok:
        cats.add("commit")
    if push_ok:
        cats.add("push")
        if _FORCE.search(text):
            cats.add("force")
    if _AMEND.search(text) and (commit_ok or re.search(r"\bamend\w*\s+(the\s+)?(last\s+)?commit|son\s+commit'?i\s+amend", text)):
        cats |= {"amend", "commit"}
    if _STASH.search(text):
        cats.add("stash")
    if _HARD_RESET.search(text):
        cats.add("hard_reset")
    if _BRANCH.search(text):
        cats.add("branch")
    if _PR.search(text):
        cats.add("pr")
    if _GH_WRITE.search(text):
        cats.add("gh_write")
    if _DISCARD.search(text):
        cats.add("discard")
    if _SYNC.search(text):
        cats.add("sync")
    if _TAG.search(text):
        cats.add("tag")
    return sorted(cats)


_APPROVE_EXACT = re.compile(
    r"^(onay|onayla|onaylıyorum|onayladım|onaylandı|onay\s+verdim|onay\s+veriyorum|evet|evet onay|evet onaylıyorum|"
    r"evet başla|tamam başla|tamam onay|başla|devam|devam et|uygula|kabul|kabul ediyorum|lgtm|approve|approved|"
    r"i approve|go ahead|yes|ok başla|okey başla)"
    r"[\s.!👍✅]*$", re.I)
_NEG_HINT = re.compile(r"(\?|\bmı\b|\bmi\b|\bmu\b|\bmü\b|değil|yok\b|bekle|vermiyorum|etmiyorum|\w+m[ıi]yorum\b|"
                       r"\bama\b|\bfakat\b|\bancak\b|\bbut\b|\bnot\b|\bno\b|\bwait\b|hayır|iptal|vazgeç|değiştir|"
                       r"revize|düzelt|ekle|çıkar|reject|cancel|change|later|sonra|yarın)", re.I)

# Sentence-level approval: user affirms the plan and may add execution fluff ("yap", "en uzman…").
_APPROVE_PHRASE = re.compile(
    r"(?:^|[\s,;:.!])("
    r"onay\s*(?:lıyorum|ladım|landı|lıyoruz)|"
    r"onay\s+ver(?:dim|iyorum|iyoruz|ildi)|"
    r"(?:bu\s+)?plan(?:ı|i)?(?:\s+da)?\s+onay\w*|"
    r"onaylıyorum|onayladım|"
    r"i\s+approve|we\s+approve|\bapproved\b|\blgtm\b|go\s+ahead"
    r")(?:\b|$)",
    re.I,
)
# Change/condition requests that void an otherwise-approving sentence.
_APPROVE_CHANGE = re.compile(
    r"(?:\bama\b|\bfakat\b|\bancak\b|\bbut\b).{0,60}?(?:değiştir|çıkar|ekle|revize|düzelt|T0\d{2}|task)|"
    r"onay\s*vermiyorum|onaylamıyorum|reddet\w*|"
    r"\b(?:değiştir|revize|iptal|vazgeç|reject|cancel)\w*",
    re.I,
)
_APPROVE_QUESTION = re.compile(
    r"\bonay\w*\s+(?:mı|mi|mu|mü)\b|\bonaylıyor\s+musun|\bonaylar\s+mısın|\?\s*$",
    re.I,
)

_EXPLICIT_REJECT = re.compile(r"(onaylamıyorum|onay\s+vermiyorum|reddet\w*|\bhayır\b|\biptal\b|vazgeç\w*|değiştir\w*|"
                              r"revize|düzelt\w*|\bçıkar\w*|\bekle(?!me)\w*\s|\breject\w*|\bcancel\w*|\bchange\w*|\bama\b|\bbut\b)", re.I)
_MANUAL_APPROVAL_REQUEST = re.compile(
    r"\b(?:ben\s+karar\s+verici(?:\s+ol(?:ayım|malıyım|mak\s+istiyorum))?|"
    r"karar(?:ı|ları)\s+ben\s+ver(?:eyim|meliyim|mek\s+istiyorum)|"
    r"(?:onayı|onayları|onay)\s+bana\s+sor\w*|bana\s+onay\s+sor\w*|benden\s+onay\s+al\w*|"
    r"i\s+want\s+to\s+(?:decide|approve|be\s+the\s+decision\s+maker)|"
    r"let\s+me\s+(?:decide|approve)|ask\s+me\s+for\s+approval)\b",
    re.I,
)
_MANUAL_APPROVAL_NEGATION = re.compile(
    r"\b(?:don'?t|do\s+not|never|no\s+need\s+to)\b.{0,60}\b(?:ask|approval|approve|decide)\w*|"
    r"\b(?:sorma|sormana\s+gerek\s+yok|onay\s+bekleme|onay\s+isteme)\b|"
    r"\b(?:unless|if|when|whenever|only\s+if|eğer|şayet|kullanıcı\s+isterse|"
    r"user\s+(?:wants|asks|says))\b.{0,100}\b(?:ben|bana|me|let\s+me|karar|onay|approve)\b",
    re.I,
)


def manual_approval_requested(prompt: str) -> bool:
    """Whether the user clearly asks to retain the plan-approval decision."""
    text = norm_lower(prompt or "")
    return bool(_MANUAL_APPROVAL_REQUEST.search(text)) and not bool(_MANUAL_APPROVAL_NEGATION.search(text))


def approval(prompt: str) -> Optional[str]:
    """Unambiguous plan approval → 'approve'; explicit change/reject → 'reject'; else None.

    Whole-message short forms still work (`onay`, `onaylıyorum`). Longer messages that clearly
    approve the plan and only add execution instructions (`…onaylıyorum yap`, `onay verdim devam et`)
    also count as approve — unless they attach a change request (`ama T001'i çıkar`).
    """
    text = norm_lower(prompt or "").strip()
    if not text:
        return None
    if _APPROVE_EXACT.match(text) and not _NEG_HINT.search(text):
        return "approve"
    if _APPROVE_PHRASE.search(text) and not _APPROVE_CHANGE.search(text) and not _APPROVE_QUESTION.search(text):
        return "approve"
    if _EXPLICIT_REJECT.search(text + " "):
        return "reject"
    return None


def approval_answer(answer: str) -> Optional[str]:
    """AskUserQuestion answer: the predefined option label, or free text typed via 'Other'."""
    a = norm_lower(answer or "").strip()
    if not a:
        return None
    if a.startswith("onayla — uygulamaya başla") or a in ("onayla", "approve"):
        return "approve"
    if _APPROVE_EXACT.match(a) and not _NEG_HINT.search(a):
        return "approve"
    return "reject"


# --- run size (XS/S finish fast; M/L keep the full pipeline) -----------------

SIZES = ("XS", "S", "M", "L")

_EXPLICIT_SIZE = re.compile(
    r"(?:(?:--)?size|boyut)\s*[:=]?\s*(XS|S|M|L)\b|\b(XS|S|M|L)\s*(?:boyut|size)\b",
    re.I,
)
_L_SIZE = re.compile(
    r"\b(migrat(?:e|ion|ing)?|bulk|çoklu\s+platform|multi[- ]?platform|"
    r"tüm\s+(sistem|uygulama|proje|kod|modül)|rewrite\s+the\s+whole|"
    r"büyük\s+(iş|özellik|refactor|değişiklik)|end[- ]to[- ]end|"
    r"across\s+(all|every)|her\s+(platform|ekran|modül)|"
    r"full\s+rewrite|kapsamlı\s+refactor)\b",
    re.I,
)
_RISK_FLOOR_M = re.compile(
    r"\b(auth(?:entication|orization)?|oauth|payment|billing|stripe|revenuecat|"
    r"deploy|store\s+submit|testflight|play\s+console|schema\s+change|"
    r"security|kvkk|gdpr|credential|secret|encryption|rls|"
    r"permission|pci|hipaa)\b",
    re.I,
)
_XS_SIZE = re.compile(
    r"\b(typo|yazım\s*hatası|rename|yeniden\s*adlandır|one[- ]line|tek\s*satır|"
    r"trivial|çok\s*basit|çok\s*küçük|minik|nit\b|cosmetic|"
    r"fix\s+the\s+typo|sadece\s+(şunu|bunu)\s+değiştir|"
    r"tek\s+kelime|one\s+word)\b",
    re.I,
)
_S_SIZE = re.compile(
    r"\b(küçük|basit|small\s+(fix|change|bug|tweak)|bug\s*fix|hotfix|"
    r"bir\s+dosya|tek\s+dosya|few\s+files|birkaç\s+dosya|"
    r"add\s+(a\s+)?(test|helper|function|method)|"
    r"(fonksiyon|function|metod|method)\s+ekle|"
    r"sub\s+fonksiyonu|düzelt(?:me)?\b|fix\s+(this|the|a)\b)\b",
    re.I,
)
_M_SIZE = re.compile(
    r"\b(feature|özellik|ekran|screen|endpoint|api\s+ekle|yeni\s+(modül|ekran|sayfa)|"
    r"refactor\b|yeniden\s+yapılandır|dark\s+mode|yeni\s+özellik)\b",
    re.I,
)


def skips_plan_critic(size: str) -> bool:
    """XS/S: no opus plan-critic round — plan check + final review still apply."""
    return (size or "") in ("XS", "S")


def skips_adversary(size: str) -> bool:
    """XS/S: final reviewer only; adversary is reserved for M/L."""
    return (size or "") in ("XS", "S")


def min_context_chars(size: str) -> int:
    """Discovery note floor: XS can be a short self-written note."""
    return 80 if (size or "") == "XS" else 200


def infer_size(request: str) -> str:
    """Pick XS/S/M/L from the user's request text. Prefer smaller when clearly safe; floor at M on risk.

    Explicit `--size` / `boyut: S` in the text wins. Unclear feature work stays M (full pipeline).
    """
    text = (request or "").strip()
    if not text:
        return "M"
    m = _EXPLICIT_SIZE.search(text)
    if m:
        return (m.group(1) or m.group(2) or "M").upper()
    low = norm_lower(text)
    n = len(low)
    if _L_SIZE.search(low):
        return "L"
    if _RISK_FLOOR_M.search(low):
        return "M"
    if _XS_SIZE.search(low) and n < 220:
        return "XS"
    if _M_SIZE.search(low) or n > 480:
        return "M"
    if _S_SIZE.search(low) or n < 140:
        return "S"
    return "M"


_CONTINUE_INTENT = re.compile(
    r"(?:"
    r"\bdevam(?:\s*et\w*|\s*edelim)?\b|"
    r"\bcontinue\b|\bresume\b|"
    r"/orkestra[-:]?resume\b|"
    r"\b(?:sonraki|next)\s+(?:g[oö]rev|task|ad[iı]m|step)\b|"
    r"\borkestra\s+(?:next|status)\b|"
    r"\b(?:durum(?:\s+ne)?|status|ne\s+oluyor)\b|"
    r"\bkald[iı][gğ][iı]n\s+yerden\b|"
    r"\bT\d{3}\b"
    r")",
    re.I,
)
# In-run operational asks (tests/gates/git/skills) keep the bind; they are not a new feature request.
_RUN_OPS_INTENT = re.compile(
    r"(?:"
    r"\b(?:test(?:ler(?:i)?)?|tests|gate|kapı|build|lint|typecheck|verify|coverage)\b|"
    r"\b(?:çalıştır|koş(?:tur)?|run\s+(?:the\s+)?tests?|run\s+(?:the\s+)?gate)\b|"
    r"\b(?:commitle|pushla|commit\s+et|push\s+et)\b|"
    r"/orkestra[-:]?\w+|"
    r"^\s*(?:uygula|apply)\b"
    r")",
    re.I,
)


def _mentions_bound_project(text: str, project, run_id: str = "", title: str = "") -> bool:
    from pathlib import Path as _Path
    project = _Path(project)
    pname = project.name.lower()
    if pname and re.search(rf"(?<![a-z0-9_-]){re.escape(pname)}(?![a-z0-9_-])", text):
        return True
    rid = str(run_id or "").lower()
    if rid and re.search(rf"(?<![a-z0-9_-]){re.escape(rid)}(?![a-z0-9_-])", text):
        return True
    try:
        if str(project.resolve(strict=False)).lower() in text:
            return True
    except Exception:
        pass
    for w in re.findall(r"[a-z0-9_-]{4,}", norm_lower(title or "")):
        if w in ("feature", "implement", "fix", "bug", "with", "from", "that", "this", "have", "into"):
            continue
        if re.search(rf"(?<![a-z0-9_-]){re.escape(w)}(?![a-z0-9_-])", text):
            return True
    return False


def _mentions_other_workspace_project(text: str, project) -> bool:
    from pathlib import Path as _Path
    project = _Path(project)
    pname = project.name.lower()
    try:
        from . import paths as _paths
        root = _paths.workspace_root()
    except Exception:
        return False
    if not root.is_dir():
        return False
    for p in root.iterdir():
        if not p.is_dir() or p.name.startswith("."):
            continue
        n = p.name.lower()
        if n == pname or len(n) < 3:
            continue
        if re.search(rf"(?<![a-z0-9_-]){re.escape(n)}(?![a-z0-9_-])", text):
            return True
    return False


def off_topic_vs_project(prompt: str, project, run_id: str = "", title: str = "") -> bool:
    """True when the user's message is not about the bound run (another project, or no bound cue).

    On-topic: names the bound project / run_id / path / title keywords, or an explicit continue
    (devam/continue/resume/orkestra-resume/next task/T0xx) or plan approval wording.
    Off-topic: names a different workspace project, OR (v1) has none of the on-topic cues — so
    "make this dashboard friendlier" while still bound does not keep Stop driving the old run.
    """
    from pathlib import Path as _Path
    project = _Path(project)
    text = norm_lower(prompt or "")
    if not text.strip():
        return False
    if _mentions_bound_project(text, project, run_id, title):
        return False
    if _CONTINUE_INTENT.search(text):
        return False
    # Git permission / run-ops lines ("şimdi commit et", "testleri çalıştır") are about the
    # bound run — unbinding would drop the grant and open a hole in the quiet path.
    if git_grants(prompt) or _RUN_OPS_INTENT.search(text):
        return False
    # Only clear approvals keep the bind. Reject heuristics match words like "düzelt" in
    # unrelated requests — those must still unbind.
    if approval(text) == "approve":
        return False
    if manual_approval_requested(prompt):
        return False
    if re.fullmatch(r"(hayır|iptal|reject|vazgeç\.?|no)", text.strip()):
        return False
    if _mentions_other_workspace_project(text, project):
        return True
    return True
