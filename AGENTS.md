# AGENTS.md — Orkestra (this repository)

Binding rules for agents editing **this** repo. Procedure for runs on other projects lives in
`plugins/orkestra/skills/` and `orkestra playbook` — do not copy those playbooks here.

Human contribution rules: [CONTRIBUTING.md](CONTRIBUTING.md). Security surface: [SECURITY.md](SECURITY.md).
First-time setup: [ONBOARDING.md](ONBOARDING.md).

## Absolute

1. **Stdlib-only Python** in `plugins/orkestra/` and `bridge/` (no new `pip` / npm deps without an explicit ask).
2. **Generated Cursor tree.** Edit Claude sources under `plugins/orkestra/{agents,skills,hooks,…}`; refresh
   `plugins/orkestra/cursor/` with `python3 plugins/orkestra/bin/orkestra cursor build`. Do not hand-edit generated files.
3. **Single CLI parse path.** User-facing argv parsing stays in `plugins/orkestra/lib/orkestra_core/cliparse.py`.
4. **No live edits while a run is open** on hook, guard, or agent sources (see CONTRIBUTING). Work on a branch or copy.
5. **One project at a time.** Do not write into sibling apps under the configured workspace root, mix credentials,
   or treat another checkout as this repository.
6. **Gates stay green.** After behaviour changes:
   ```bash
   cd plugins/orkestra/tests && python3 -m unittest discover -s . -q
   cd bridge/tests && python3 -m unittest discover -s . -q
   ```
7. **No git commit, push, deploy, or store upload** unless the human asks in that message.
8. **Language.** Reply to the user in Turkish when they write Turkish; code comments and model-facing text stay English.

## Out of scope here

Phase loops, TDD red/green CLI protocol, adversary hunt lists, app-diff security checklists, and the full guard
inventory — those belong in skills, `ORKESTRA.md`, and README Limits.
