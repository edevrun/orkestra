---
name: rollback
description: Revert the files changed by an Orkestra run back to their pre-run state (restores your own uncommitted work from backup, never runs git writes). User-invoked only.
argument-hint: "[project] [run-id]"
disable-model-invocation: true
---
The user explicitly asked to roll back an orkestra run. Hint: $ARGUMENTS

1. `orkestra context` (with `-p <project>`) and identify the run (active run, or the run id given; finished runs need `--run <id>`).
2. Dry run: `orkestra rollback -p <project> [--run <id>]`. Show the two groups in the user's language (English by default, Turkish when they write Turkish): files orkestra changed, and
   "other differences" (possibly the user's own edits made meanwhile — these are kept unless `--all`).
3. Ask with AskUserQuestion (header `Rollback`): `Apply (orkestra changes only)` · `Apply including other diffs (--all)` · `Never mind`. When the conversation is Turkish use the header `Geri alma` and the options `Uygula (yalnız orkestra değişiklikleri)` · `Diğer farklar dahil uygula (--all)` · `Vazgeç`.
4. Apply accordingly: `orkestra rollback -p <project> [--run <id>] --yes [--all]`. Current contents are saved first
   (the CLI prints where), so the rollback itself can be undone.
5. If the run was still open: `orkestra abort "rolled back by user" -p <project>`.
6. Report what was restored, deleted, skipped or could not be restored.
