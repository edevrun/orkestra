## Summary

<!-- Why this change exists; link issues if any. -->

## Test plan

- [ ] `cd plugins/orkestra/tests && python3 -m unittest discover -s . -q`
- [ ] `cd bridge/tests && python3 -m unittest discover -s . -q`
- [ ] OpenClaw suite if touched: `cd integrations/openclaw/plugin && npm test`
- [ ] No personal paths, secrets, or private sibling-repo defaults in the diff

## Notes for reviewers

<!-- Risk, migration, docs updates. -->
