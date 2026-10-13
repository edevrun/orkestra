---
name: orkestra-ship
description: "Commit (and push, if a remote is configured) the finished changes of the current project with a conventional commit message. User-invoked only — the explicit permission that orkestra's git guard requires."
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
  again. Plan approval is any clear approve intent the hook recognizes ("approve", "I approve", "go ahead", "lgtm",
  "onay", "onaylıyorum", "onay verdim", "bu planı onaylıyorum yap …") — not only the bare word "approve". Never
  approve yourself; after their message run `orkestra phase next`, which tells you whether the approval was recorded. The approval is
  never skipped ("don't ask questions" in the request does not waive it) and never recorded by you in any way.
- There is no SendMessage: to send findings back to a subagent, launch the same subagent type again with the
  findings and the path of its previous report.
- The shell keeps its working directory between calls and the guards cannot see it. Stay in the project root; if
  another directory is needed use absolute paths or `cd <absolute path> && …` in the same command.
- Skills are `/orkestra-run`, `/orkestra-resume`, `/orkestra-status`, `/orkestra-onboard`, `/orkestra-rollback`,
  `/orkestra-ship`. `orkestra` is on PATH. The same guards, gates and evidence rules apply as in Claude Code.

The user explicitly asked to commit and push. Hint: (what the user wrote with this command)

1. Resolve the project (`orkestra context`). Show `git status --short` and `git diff --stat`.
2. Stage only the files that belong to the finished work (use `orkestra changes --run <last run>` when relevant; never stage secrets, `.env*`, keys, build output). `.orkestra/` memory and feature docs are fine to include; `.orkestra/runs/` is git-ignored.
3. Commit with the message from the hint, or the "Commit suggestion" section (Turkish report: "Commit önerisi") in the latest `report.md`, or a conventional commit you write. Never `--no-verify`; if a pre-commit hook fails, fix the cause or report it.
4. Push the current branch to its upstream (`git push`); if there is no upstream, ask before setting one. Never force-push.
5. Report the commit hash, branch and push result in the user's language (English by default, Turkish when they write Turkish).
