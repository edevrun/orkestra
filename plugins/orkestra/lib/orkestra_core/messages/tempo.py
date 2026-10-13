"""tempo: human-facing messages (English + Turkish). Keys are `tempo.<name>`; both dicts must hold the same keys and {fields}.

Covers the compact duration (`i18n.duration`) and the stall verdicts `tempo.classify` hands to `watch`, the dashboard and
the watchdog notifications. The verdict `cls` / `key` stay language-independent machine values; only `text` is localized.
Everything the model reads (`orkestra next`, flags, tempo lines) stays English in tempo.py and has no catalog entry.
The Turkish side keeps the wording the tool always had; the English side avoids the em dash (U+2014).
"""
from __future__ import annotations

from typing import Dict

EN: Dict[str, str] = {
    # ---- duration units (see i18n.duration): 45s · 12m · 2h 5m
    "tempo.dur_s": "{n}s",
    "tempo.dur_m": "{n}m",
    "tempo.dur_hm": "{h}h {m}m",
    # ---- stall verdicts (tempo.classify)
    "tempo.stall_closed": "closed",
    "tempo.stall_user_waiting": "waiting for the user: {what}",
    "tempo.question_word": "question",
    "tempo.stall_blocked": "blocked: {reason}",
    "tempo.stall_paused": "paused",
    "tempo.stall_open_question": "waiting for the user: open question ({question})",
    "tempo.stall_turn_ended": "turn ended: waiting for the user's message",
    "tempo.stall_hung": "hung command ({agent}, {dur}): {cmd} · the command may be stuck, or the session may have "
                        "closed while it was running",
    "tempo.stall_agents": "subagents running: {who}",
    "tempo.stall_dead_wait": "dead wait: a subagent finished {ago}, {why}",
    "tempo.dead_why_wait_cmd": "the main session is stuck in a wait command",
    "tempo.dead_why_no_report": "the subagent report never reached the main session",
    "tempo.lost_agents": "(subagents registered: {n}, but no sign of life for {dur})",
    "tempo.hook_last": "last hook activity {ago}",
    "tempo.hook_none": "no hook activity yet",
    "tempo.stall_silent_state": "silent session: run state updated {ago}; {hook}; liveness cannot be confirmed from hook "
                                "records{lost}",
    "tempo.stall_silent": "silent session: no records for {dur}; the session may have closed or crashed{lost}",
    "tempo.stall_working": "working",
}

TR: Dict[str, str] = {
    "tempo.dur_s": "{n} sn",
    "tempo.dur_m": "{n} dk",
    "tempo.dur_hm": "{h} sa {m} dk",
    "tempo.stall_closed": "kapalı",
    "tempo.stall_user_waiting": "kullanıcı bekleniyor — {what}",
    "tempo.question_word": "soru",
    "tempo.stall_blocked": "bloke — {reason}",
    "tempo.stall_paused": "duraklatıldı",
    "tempo.stall_open_question": "kullanıcı bekleniyor — açık soru ({question})",
    "tempo.stall_turn_ended": "tur bitti — kullanıcının mesajı bekleniyor",
    "tempo.stall_hung": "asılı komut ({agent}, {dur}): {cmd} — komut takılmış ya da oturum komut sırasında kapanmış "
                        "olabilir",
    "tempo.stall_agents": "alt-ajan çalışıyor: {who}",
    "tempo.stall_dead_wait": "ölü bekleme — alt-ajan {ago} bitti, {why}",
    "tempo.dead_why_wait_cmd": "ana oturum bekleme komutunda",
    "tempo.dead_why_no_report": "alt-ajan raporu ana oturuma ulaşmadı",
    "tempo.lost_agents": "({n} alt-ajan kaydı var ama {dur} hiçbir iz yok)",
    "tempo.hook_last": "son hook aktivitesi {ago}",
    "tempo.hook_none": "henüz hook aktivitesi yok",
    "tempo.stall_silent_state": "sessiz oturum — run state {ago} güncellendi; {hook}; canlılık hook kayıtlarından "
                                "doğrulanamıyor{lost}",
    "tempo.stall_silent": "sessiz oturum — {dur} kayıt yok; oturum kapanmış ya da çökmüş olabilir{lost}",
    "tempo.stall_working": "çalışıyor",
}
