# Headless mode (n8n / Telegram / `claude -p`)

`orkestra context` shows `mode: headless`. There is no AskUserQuestion and no live user.

- Questions (clarify) → write them to `<run folder>/questions.md` (numbered, each with options and your recommendation), then `orkestra await "<1-line summary>"` and end your turn with the questions as your final message.
- Approval → write the summary (same content as the approve playbook) as your final message, ending with: `Onaylamak için "onay" yaz; değişiklik için ne değişsin yaz.` Then `orkestra await "plan onayı"` and end the turn. The user's reply arrives as the next prompt; the hook records "onay".
- Everything else runs autonomously like interactive mode, with one difference: after dispatching subagents do not end your turn (this process exits with the turn and kills them) — wait inside it: `sleep 30; orkestra next` until no subagent is reported running.
- The plan approval is never skipped and never recorded by you — not even when the request says "don't ask":
  calling the hook scripts or writing the approval yourself is forging the user's consent.
- Never decide on the user's behalf what the interactive flow would have asked. If waiting is impossible, `orkestra block` with the reason.
- Your final message is what the user receives on their phone: in the language of the user's request, ≤ 20 lines, no tables wider than 3 columns.
