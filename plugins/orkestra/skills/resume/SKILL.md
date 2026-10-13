---
name: resume
description: Continue an open Orkestra run in the current (or named) project after a pause, a new session or context compaction. Use when the user says resume / continue / pick up where we left off / devam / kaldığın yerden about orkestra work.
argument-hint: "[project]"
---
Continue the open orkestra run. Project hint: $ARGUMENTS

1. `orkestra context` (add `-p <project>` if given) → confirm there is an active run.
2. Follow `orkestra playbook resume` (`orkestra next` names the exact step to continue with), then keep looping `orkestra playbook <phase>` → work → `orkestra phase next` exactly as the `orkestra:run` skill describes (all its non-negotiables apply).
