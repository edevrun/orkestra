---
name: orkestra-rollback
description: "Revert the files changed by an Orkestra run back to their pre-run state (restores your own uncommitted work from backup, never runs git writes). User-invoked only."
disable-model-invocation: true
---
[orkestra · Cursor engine] The instructions below were written for Claude Code. In Cursor:
- A subagent `orkestra:<name>` is the Cursor subagent `orkestra-<name>`: launch it with the Task tool
  (subagent_type "orkestra-<name>"), ALWAYS in the foreground (run_in_background: false). Its report comes back in
  the same turn. The "model" column of the agent table does not apply (subagents use the chat's model).
- Do NOT end your turn while the run is active: there is nothing to wait for. End it only after `orkestra await`
  (a question / the plan approval) or `orkestra block`.
- AskUserQuestion does not exist. Wherever the text says to ask with it: write the question(s) in the chat, run
  `orkestra await "<one-line summary>"`, then end your turn (`orkestra playbook headless` has the wording — the user
  reads your message right here in the chat). The user's next message is the answer; a hook marks the run active
  again. Plan approval is any clear approve intent the hook recognizes ("onay", "onaylıyorum",
  "onay verdim", "bu planı onaylıyorum yap …") — not only the bare word "onay". Never approve yourself; after
  their message run `orkestra phase next`, which tells you whether the approval was recorded. The approval is
  never skipped ("don't ask questions" in the request does not waive it) and never recorded by you in any way.
- There is no SendMessage: to send findings back to a subagent, launch the same subagent type again with the
  findings and the path of its previous report.
- The shell keeps its working directory between calls and the guards cannot see it. Stay in the project root; if
  another directory is needed use absolute paths or `cd <absolute path> && …` in the same command.
- Skills are `/orkestra-run`, `/orkestra-resume`, `/orkestra-status`, `/orkestra-onboard`, `/orkestra-rollback`,
  `/orkestra-ship`. `orkestra` is on PATH. The same guards, gates and evidence rules apply as in Claude Code.

The user explicitly asked to roll back an orkestra run. Hint: (what the user wrote with this command)

1. `orkestra context` (with `-p <project>`) and identify the run (active run, or the run id given; finished runs need `--run <id>`).
2. Dry run: `orkestra rollback -p <project> [--run <id>]`. Show the two groups in Turkish: files orkestra changed, and
   "other differences" (possibly the user's own edits made meanwhile — these are kept unless `--all`).
3. Ask in the chat which one they want and END YOUR TURN. Applying needs the user's own message: they send
   `/orkestra-rollback uygula` (only orkestra's changes) or `/orkestra-rollback uygula --all`; anything else = no.
4. Apply accordingly: `orkestra rollback -p <project> [--run <id>] --yes [--all]`. Current contents are saved first
   (the CLI prints where), so the rollback itself can be undone.
5. If the run was still open: `orkestra abort "rolled back by user" -p <project>`.
6. Report what was restored, deleted, skipped or could not be restored.
