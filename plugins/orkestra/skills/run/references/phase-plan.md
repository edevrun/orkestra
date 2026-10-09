# Phase: plan

Goal: an approval-ready, implementation-proof plan. Output: `plan.md` + `reviews/plan-critic.md` with PASS.

1. Dispatch `orkestra:architect`:
   ```
   project: <abs path> · run folder: <path>
   inputs: request.md, context.md, scout-*.md, research*.md, spec.md, memory/PROJECT.md (+ relevant memory/specs/*.md, DECISIONS.md, LEARNINGS.md), AGENTS.md
   task: write plan.md for this spec (template: `orkestra template plan`); size <XS|S|M|L>; then run `orkestra plan check`.
   ```
   XS: you may write the plan yourself (1–2 tasks) instead. After each dispatch end your turn; the report resumes you.
2. Dispatch `orkestra:plan-critic` with the same paths. It writes `reviews/plan-critic.md` ending in `VERDICT: …`.
   XS runs may skip the critic (the CLI allows it); `orkestra plan check` must still pass.
3. FAIL → send the blockers back to the architect (same agent via SendMessage if available, else a new call with the critic file path). Max 3 rounds; then bring the disagreement to the user.
4. `orkestra plan check -p <project>` must print OK. If it adds `NOTE: LARGE PLAN`, carry that recommendation (split
   into several runs, which part first) into the approval summary — the user decides.
5. `orkestra phase next`.
