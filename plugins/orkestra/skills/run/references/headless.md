# Headless mode (n8n / Telegram / `claude -p`)

`orkestra context` shows `mode: headless`. There is no AskUserQuestion and no live user. Read the run's `approval_mode` from `.orkestra/current/state.json`.

- Autopilot (default) → never ask a question or run `orkestra await`. Resolve ambiguity conservatively, record assumptions in `spec.md`, and continue. At approve, `orkestra phase next` records a hash-bound policy approval after plan and critic checks pass.
- Manual mode → write clarification questions to `<run folder>/questions.md`, then `orkestra await "<1-line summary>"` and end the turn with the questions. At approve, write the plan summary, end with `Onaylamak için "onay" yaz; değişiklik için ne değişsin yaz.`, then `orkestra await "plan onayı"`.
- Everything else runs autonomously like interactive mode, with one difference: after dispatching subagents do not end your turn (this process exits with the turn and kills them) — wait inside it: `sleep 30; orkestra next` until no subagent is reported running.
- The plan approval is never skipped and never recorded by you — not even when the request says "don't ask":
  calling the hook scripts or writing the approval yourself is forging the user's consent.
- Never decide on the user's behalf what the interactive flow would have asked. If waiting is impossible, `orkestra block` with the reason.
- Your final message is what the user receives on their phone: in the language of the user's request, ≤ 20 lines, no tables wider than 3 columns.
