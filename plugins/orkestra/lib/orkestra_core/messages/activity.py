"""activity: human-facing messages (English + Turkish). Keys are `activity.<name>`; both dicts must hold the same keys and {fields}.

Covers the activity-feed summaries and the one-line "what is this run doing right now" status (`liveness`) that
`orkestra watch`, the dashboard and the bridge show. `{where}` is the place the user has to look at: the Claude window
or the Cursor chat (`where_claude` / `where_cursor`); `{resume}` is the engine's resume command."""
from __future__ import annotations

from typing import Dict

EN: Dict[str, str] = {
    # small words and units
    "activity.main_agent": "main",
    "activity.question": "question",
    "activity.ask_summary": "question: {heads}",
    "activity.follow_up": "follow-up message",
    "activity.dur_s": "{n}s",
    "activity.dur_m": "{n}m",
    "activity.dur_h": "{h}h {m}m",
    "activity.where_claude": "the Claude window",
    "activity.where_cursor": "the Cursor chat",
    # liveness: one line per state
    "activity.live_done": "done ✓",
    "activity.live_aborted": "aborted",
    "activity.live_wait_question": "WAITING FOR YOU · question: {q}",
    "activity.live_blocked": "BLOCKED · {reason}",
    "activity.live_paused": "paused · run {resume} in that project to continue",
    "activity.live_wait_open_question": "WAITING FOR YOU · a question is open in {where} ({q})",
    "activity.live_agents_running": "subagent running: {who}",
    "activity.live_main_waits": "main session is waiting for the report",
    "activity.live_wait_turn_ended": "WAITING FOR YOU · Claude finished its turn ({ago}); reply in that window",
    "activity.live_maybe_waiting": "probably WAITING FOR YOU · phase {phase}{idle}; "
                                   "a question or approval may be open in {where} of that project",
    "activity.live_idle_for": ", no activity for {dur}",
    "activity.live_phase_open": "{phase} phase open · run state updated {state_ago}; {hook}",
    "activity.live_phase_fallback": "active",
    "activity.live_hook_last": "last hook activity {ago}",
    "activity.live_hook_none": "no hook activity yet",
    "activity.live_no_record": "no activity recorded yet",
    "activity.live_running_agents": "running subagent: {names}",
    "activity.live_command": "command running · {dur}: {what}{who}",
    "activity.live_working": "working",
    "activity.live_working_agent": "working · {agent}{who}",
    "activity.live_quiet": "quiet · {dur}: the model is thinking or a long step is running{who}",
    "activity.live_stale": "no activity for {dur}: the session may have closed; run {resume} in that project",
}

TR: Dict[str, str] = {
    "activity.main_agent": "ana",
    "activity.question": "soru",
    "activity.ask_summary": "soru: {heads}",
    "activity.follow_up": "devam mesajı",
    "activity.dur_s": "{n} sn",
    "activity.dur_m": "{n} dk",
    "activity.dur_h": "{h} sa {m} dk",
    "activity.where_claude": "Claude penceresinde",
    "activity.where_cursor": "Cursor sohbetinde",
    "activity.live_done": "bitti ✓",
    "activity.live_aborted": "iptal edildi",
    "activity.live_wait_question": "SENİ BEKLİYOR — soru: {q}",
    "activity.live_blocked": "BLOKE — {reason}",
    "activity.live_paused": "duraklatıldı — devam için o projede {resume}",
    "activity.live_wait_open_question": "SENİ BEKLİYOR — {where} soru açık ({q})",
    "activity.live_agents_running": "alt-ajan çalışıyor: {who}",
    "activity.live_main_waits": "ana oturum raporu bekliyor",
    "activity.live_wait_turn_ended": "SENİ BEKLİYOR — Claude turu bitirdi ({ago}); o pencereye cevap yaz",
    "activity.live_maybe_waiting": "muhtemelen SENİ BEKLİYOR — {phase} fazında{idle}; "
                                   "o projenin {where} soru/onay açık olabilir",
    "activity.live_idle_for": " {dur} hareket yok",
    "activity.live_phase_open": "{phase} fazı açık — run durumu {state_ago} güncellendi; {hook}",
    "activity.live_phase_fallback": "aktif",
    "activity.live_hook_last": "son hook aktivitesi {ago}",
    "activity.live_hook_none": "henüz hook aktivitesi yok",
    "activity.live_no_record": "henüz aktivite kaydı yok",
    "activity.live_running_agents": "çalışan alt-ajan: {names}",
    "activity.live_command": "komut çalışıyor · {dur}: {what}{who}",
    "activity.live_working": "çalışıyor",
    "activity.live_working_agent": "çalışıyor — {agent}{who}",
    "activity.live_quiet": "sessiz · {dur} — model düşünüyor ya da uzun bir adım sürüyor{who}",
    "activity.live_stale": "{dur} hareket yok — oturum kapanmış olabilir; o projede {resume}",
}
