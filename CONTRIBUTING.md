# Contributing

Thank you for looking into Orkestra. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
A few rules keep it safe to change.

1. **Do not edit hook, guard or agent files while a run is open.** Claude Code loads the plugin in place, so a
   half-edited `hooks/`, `lib/orkestra_core/` or `agents/` file takes effect in running sessions immediately.
   Work on a copy or a branch; `orkestra watch` shows open runs.
2. **Every change comes with a test** that fails without it. The suite must stay green on Python 3.9 and on the
   newest Python you have:
   ```bash
   cd plugins/orkestra/tests && python3 -m unittest discover -s . -q
   cd bridge/tests && python3 -m unittest discover -s . -q
   cd integrations/openclaw/plugin && npm test          # logic-only (CI)
   cd integrations/openclaw/plugin && npm run test:e2e  # optional e2e; needs OPENCLAW_PKG
   ```
   The Python suites use only the standard library; the OpenClaw logic suite uses Node's built-in test runner and has no npm dependencies. OpenClaw e2e is optional and skips when `OPENCLAW_PKG` (default Homebrew path) is missing.
3. **Limits on time are CPU budgets, not wall-clock** (`helpers.assert_cpu_under`); tests must pass on a busy machine.
4. **Generated files.** `plugins/orkestra/cursor/` is generated from the Claude sources:
   `python3 plugins/orkestra/bin/orkestra cursor build`. A test fails when it is stale.
5. **Text the model reads is English**; say what to do and give the exact command. If the CLI can decide
   something deterministically, put it in the CLI rather than in a playbook.
6. **Third-party text.** Do not paste text from other projects into playbooks or prompts. References go under
   `plugins/orkestra/vendor/<project>/` with the upstream licence file, a row in `vendor/PINNED.md` and in
   `THIRD_PARTY_NOTICES.md`. Only permissive licences (MIT, BSD, Apache-2.0).
7. **A Telegram bot token has one consumer.** If another program already polls your bot, do not give the same
   token to the bridge (Telegram answers 409 Conflict); use the HTTP API of the bridge from that program instead
   (`integrations/openclaw/CONTRACT.md` is an example).
8. Do not commit `dist/`, `internal/`, secrets, or anything under `~/.orkestra`.

By contributing you agree that your contribution is licensed under the MIT License of this repository.
