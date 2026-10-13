---
name: ship
description: Commit (and push, if a remote is configured) the finished changes of the current project with a conventional commit message. User-invoked only — the explicit permission that orkestra's git guard requires.
argument-hint: "[project] [message]"
disable-model-invocation: true
---
The user explicitly asked to commit and push. Hint: $ARGUMENTS

1. Resolve the project (`orkestra context`). Show `git status --short` and `git diff --stat`.
2. Stage only the files that belong to the finished work (use `orkestra changes --run <last run>` when relevant; never stage secrets, `.env*`, keys, build output). `.orkestra/` memory and feature docs are fine to include; `.orkestra/runs/` is git-ignored.
3. Commit with the message from the hint, or the "Commit suggestion" section (Turkish report: "Commit önerisi") in the latest `report.md`, or a conventional commit you write. Never `--no-verify`; if a pre-commit hook fails, fix the cause or report it.
4. Push the current branch to its upstream (`git push`); if there is no upstream, ask before setting one. Never force-push.
5. Report the commit hash, branch and push result in the user's language (English by default, Turkish when they write Turkish).
