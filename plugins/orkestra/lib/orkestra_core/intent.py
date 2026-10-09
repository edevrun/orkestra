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
