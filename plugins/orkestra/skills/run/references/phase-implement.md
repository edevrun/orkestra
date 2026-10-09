# Phase: implement

Goal: every task done with red→green evidence and a passing task review. You coordinate; the implementer writes code.

Loop until `orkestra next` answers `orkestra phase next`:
1. `orkestra next -p <project>` → the task and its sub-step (start · red missing · green missing · review · apply the
   verdict). Do exactly that; after any interruption this is how you find your place again.
2. New task: `orkestra task start Txxx -p <project>` then `orkestra brief Txxx -p <project>`.
3. Dispatch `orkestra:implementer` with the **brief output verbatim** plus: `project: <path>` and "Follow your protocol; reply in ≤ 6 lines." (Never paste the whole plan.) Then **end your turn** — its report arrives as a message.
4. Read its reply (not its files) — on `BLOCKED`, decide: clarify from spec/context yourself, ask the user (AskUserQuestion), or re-scope with a plan change-log entry.
5. Dispatch `orkestra:reviewer` with `scope: task Txxx`, project path and run folder (it writes `reviews/Txxx.review.md`). End your turn.
6. Reviewer FAIL → `orkestra task reopen Txxx "review round N" -p <project>` (resets red/green so the fix starts with a
   fresh failing test) → `orkestra task start Txxx` → send the findings to the implementer (SendMessage to the same agent
   if available, otherwise a new implementer with the brief + review file path). The CLI refuses a third round: stop and
   present the trade-off to the user, then `--force "<their ruling>"` or `orkestra task defer`.
7. Reviewer PASS — with or without minors → `orkestra task done Txxx -p <project> "review: PASS"` (the CLI refuses without
   red+green evidence for `TDD: required` tasks and, on M/L runs, without a PASS review of this round). Minors do NOT reopen the task: `orkestra task note Txxx "minor: <what>"`
   and triage them in the final review (the CLI refuses to reopen a PASS without `--force "<why>"`).
8. Plan deviations (extra file, changed approach) → append to `plan.md` → `## Plan değişiklik günlüğü` with time, what, why. Scope changes beyond the spec → stop and ask the user; if accepted: `orkestra phase back plan --reason "..."` (requires re-approval).

Then `orkestra phase next`.

Notes
- Parallel implementers only for tasks whose files do not overlap a task in progress (`task start` refuses otherwise),
  at most 3; each runs its own narrow tests and fast gates, never the full suite. While agents run, end your turn —
  do not sleep or poll (`orkestra next` shows who is still running).
- XS/S runs: you may skip the per-task reviewer and rely on the (mandatory) final review — say so in the `task done` message. M/L runs: per-task review is required.
- A failure that resists two fix attempts → `orkestra playbook debugging` before trying again.
- Long gate runs: implementers run only narrow tests + fast gates; the full suite runs in verify.
