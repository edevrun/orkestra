"""dispatch: human-facing messages (English + Turkish). Keys are `dispatch.<name>`; both dicts must hold the same keys and {fields}.

These are the lines the hooks write into the run's activity feed (what `orkestra watch` and the dashboard show). The
text the hooks hand to the MODEL (context, block reasons, deny reasons) is protocol text and stays English in the code."""
from __future__ import annotations

from typing import Dict

EN: Dict[str, str] = {
    # session lifecycle notes
    "dispatch.restart_note": "session restarted: {count} pending subagent record(s) dropped ({who})",
    "dispatch.bound": "session bound ({sid})",
    "dispatch.off_topic_unbound": "session unbound · off-topic prompt (not about {project})",
    "dispatch.copilot_bound": "explicit Copilot /orkestra:resume",
    "dispatch.bind_kept": ("bind refused: chat {sid} still drives {kept_run} in {kept_project} (active {age} s ago). "
                          "Pause that run first, then resume this one"),
    # hand-backs and tool results
    "dispatch.task_notification": "task notification",
    "dispatch.subagent_report": "subagent report ({who})",
    "dispatch.handback": "{label} · {status}",
    "dispatch.question": "question",
    "dispatch.answered": "(answered)",
    "dispatch.empty_reply": "(empty reply)",
    "dispatch.task_returned": "← {kind}: returned",
    "dispatch.agent_done": "{agent} finished",
    # the main turn ended (Stop hook)
    "dispatch.turn_plain": "turn ended",
    "dispatch.turn_done": "turn ended · status done",
    "dispatch.turn_awaiting": "turn ended · status awaiting_user (binding kept for the answer)",
    "dispatch.turn_status": "turn ended · status {status}",
    "dispatch.turn_off_topic": "turn ended · last user message is outside the bound run → session unbound",
    "dispatch.turn_not_driving": "turn ended · this turn did not drive the bound run → session unbound",
    "dispatch.turn_nudge_limit": "turn ended (continue-nudge limit)",
    "dispatch.turn_transcript_off_topic": "turn ended · transcript is off-topic → session unbound",
    "dispatch.turn_session_closed": "turn ended · status {status} (session closed)",
    "dispatch.wait_agents": "waiting for subagent: {who} ({count})",
    "dispatch.wait_report": "subagent finished, waiting for its report ({count})",
    # nudges
    "dispatch.nudge_early_stop": "tried to stop early → kept going",
    "dispatch.nudge_tdd": "TDD evidence missing ({tasks}) → subagent kept going",
    # lifecycle signals and loop breaker
    "dispatch.deny_loop_reason": "deny loop: {guard} x{count}",
    "dispatch.deny_loop_row": "the same call was refused {count}x ({guard}) → run blocked",
    "dispatch.perm_wait": "waiting for your permission: {what}",
    "dispatch.idle_prompt": "idle · waiting for your input",
    "dispatch.api_error": "model/API error: {what}",
    "dispatch.cmd_failed": "✗ command failed",
    "dispatch.cmd_interrupted": "✗ command interrupted",
    "dispatch.session_ended": "session ended ({reason})",
    "dispatch.turn_stopped": "turn stopped before it finished ({status})",
}

TR: Dict[str, str] = {
    "dispatch.restart_note": "oturum yeniden başladı: {count} bekleyen alt-ajan kaydı düşürüldü ({who})",
    "dispatch.bound": "oturum bağlandı ({sid})",
    "dispatch.off_topic_unbound": "oturum çözüldü · konu dışı prompt ({project} değil)",
    "dispatch.copilot_bound": "Copilot /orkestra:resume ile açıkça bağlandı",
    "dispatch.bind_kept": ("bağlanma reddedildi: {sid} sohbeti hâlâ {kept_project} içindeki {kept_run} run'ını sürüyor "
                          "({age} sn önce aktifti). Önce o run'ı duraklat, sonra bunu sürdür"),
    "dispatch.task_notification": "görev bildirimi",
    "dispatch.subagent_report": "alt-ajan raporu ({who})",
    "dispatch.handback": "{label} — {status}",
    "dispatch.question": "soru",
    "dispatch.answered": "(cevaplandı)",
    "dispatch.empty_reply": "(boş yanıt)",
    "dispatch.task_returned": "← {kind}: döndü",
    "dispatch.agent_done": "{agent} bitti",
    "dispatch.turn_plain": "tur bitti",
    "dispatch.turn_done": "tur bitti · durum done",
    "dispatch.turn_awaiting": "tur bitti · durum awaiting_user (cevap için bağ korundu)",
    "dispatch.turn_status": "tur bitti · durum {status}",
    "dispatch.turn_off_topic": "tur bitti · son kullanıcı mesajı bound run dışı → oturum çözüldü",
    "dispatch.turn_not_driving": "tur bitti · bu tur bound run'ı sürmedi → oturum çözüldü",
    "dispatch.turn_nudge_limit": "tur bitti (devam uyarısı limiti)",
    "dispatch.turn_transcript_off_topic": "tur bitti · transcript off-topic → oturum çözüldü",
    "dispatch.turn_session_closed": "tur bitti · durum {status} (oturum kapandı)",
    "dispatch.wait_agents": "alt-ajan bekleniyor: {who} ({count})",
    "dispatch.wait_report": "alt-ajan bitti, raporu bekleniyor ({count})",
    "dispatch.nudge_early_stop": "erken durmaya çalıştı → devam ettirildi",
    "dispatch.nudge_tdd": "TDD kanıtı eksik ({tasks}) → alt-ajan devam ettirildi",
    "dispatch.deny_loop_reason": "ret döngüsü: {guard} x{count}",
    "dispatch.deny_loop_row": "aynı çağrı {count} kez reddedildi ({guard}) → run bloklandı",
    "dispatch.perm_wait": "iznin bekleniyor: {what}",
    "dispatch.idle_prompt": "boşta · girdini bekliyor",
    "dispatch.api_error": "model/API hatası: {what}",
    "dispatch.cmd_failed": "✗ komut başarısız",
    "dispatch.cmd_interrupted": "✗ komut yarıda kesildi",
    "dispatch.session_ended": "oturum bitti ({reason})",
    "dispatch.turn_stopped": "tur bitmeden durdu ({status})",
}
