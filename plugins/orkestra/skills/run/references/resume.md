# Resume an open run

1. `orkestra resume -p <project>` (binds this session to the run; prints phase and next action). It drops records of
   background subagents that show no sign of life (they belonged to a window that is gone) and says so; if `next` still
   waits for subagents that you know are gone, `orkestra agents` lists them and `orkestra agents clear` removes them.
2. `orkestra next -p <project>` — the one next step, derived from evidence and review files. In autopilot, recover legacy
   `awaiting_user` states with conservative assumptions and continue; in manual mode, only a real user reply satisfies
   an open question or approval. `orkestra status` shows the details behind it.
3. Read the ledger tail: `.orkestra/features/<run>/ledger.md` (last ~20 lines) — trust it and the files over any recollection.
4. If the user's new message changes scope: update spec/plan and rerun the critic before continuing. In autopilot the CLI
   automatically rechecks the new hash; manual mode returns to its approval gate.
5. Do what `orkestra next` said; for the how, `orkestra playbook <phase>`.
