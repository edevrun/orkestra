# Resume an open run

1. `orkestra resume -p <project>` (binds this session to the run; prints phase and next action). It drops records of
   background subagents that show no sign of life (they belonged to a window that is gone) and says so; if `next` still
   waits for subagents that you know are gone, `orkestra agents` lists them and `orkestra agents clear` removes them.
2. `orkestra next -p <project>` — the one next step, derived from state, evidence and review files (so nothing is
   skipped and nothing is done twice: a task with green evidence is not re-implemented, a verdict on file is applied,
   a question or approval is never answered for the user). `orkestra status` shows the details behind it.
3. Read the ledger tail: `.orkestra/features/<run>/ledger.md` (last ~20 lines) — trust it and the files over any recollection.
4. If the user's new message changes scope: phases before approval → just update spec/plan; after approval → explain, then `orkestra phase back plan --reason "..."` and re-approve.
5. Do what `orkestra next` said; for the how, `orkestra playbook <phase>`.
