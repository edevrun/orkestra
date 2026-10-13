## Summary

<!-- Why this change exists; link issues if any. English or Turkish is fine. -->

## Test plan

Tick what you ran. Details: [CONTRIBUTING.md](https://github.com/soft-ai-solutions/orkestra/blob/main/CONTRIBUTING.md).

- [ ] `cd plugins/orkestra/tests && python3 -m unittest discover -s . -q`
- [ ] `cd bridge/tests && python3 -m unittest discover -s . -q`
- [ ] Also on Python 3.9 (the minimum), for example `python3.9 -m unittest discover -s . -q` in both folders
- [ ] OpenClaw suite if `integrations/openclaw/plugin` was touched: `cd integrations/openclaw/plugin && npm test`
- [ ] Every behaviour change has a test that fails without it; no test was deleted, skipped or weakened

## Generated files and sources

- [ ] If agents, skills, hooks or templates changed: `python3 plugins/orkestra/bin/orkestra cursor build` was run and the
      generated `plugins/orkestra/cursor/` is committed unedited (a test fails when it is stale)
- [ ] No hook, guard or agent file was edited while an Orkestra run was open on this checkout
- [ ] Python stays standard library only; no new dependency (pip or npm)

## Text and languages

- [ ] New human-facing text is in the EN and TR catalogs (`messages/*.py`) and read with `i18n.t(...)`; `test_i18n` is green
      (key and `{field}` parity, no Turkish letters in English text, no Turkish text outside catalogs)
- [ ] New tests cover both languages (`ORKESTRA_LANG=en` and `tr`); protocol tokens still accept every old spelling
- [ ] `README.md` / `README.tr.md` (and `ONBOARDING*`, `ORKESTRA*`) changed together; `test_docs_drift` is green
- [ ] No em dash (U+2014) in new user-visible text

## Safety

- [ ] No secrets, tokens, bridge secrets, personal paths or private sibling-repo defaults in the diff
- [ ] No `dist/`, `internal/` or anything from `~/.orkestra` is included
- [ ] Windows and Python 3.9 compatibility considered (no POSIX-only API without a guard, no `match`, no `X | Y` at runtime)

## Notes for reviewers

<!-- Risk, migration, what you could not test (for example a real Windows machine), docs updates. -->
