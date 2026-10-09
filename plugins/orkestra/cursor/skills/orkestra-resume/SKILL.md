---
name: orkestra-resume
description: "Continue an open Orkestra run in the current (or named) project after a pause, a new session or context compaction. Use when the user says devam / kaldığın yerden / resume about orkestra work."
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

Continue the open orkestra run. Project hint: (what the user wrote with this command)

1. `orkestra context` (add `-p <project>` if given) → confirm there is an active run.
2. Follow `orkestra playbook resume` (`orkestra next` names the exact step to continue with), then keep looping `orkestra playbook <phase>` → work → `orkestra phase next` exactly as the `orkestra:run` skill describes (all its non-negotiables apply).
