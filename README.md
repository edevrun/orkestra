# Orkestra

> Türkçe ve ayrıntılı belge: [README.tr.md](README.tr.md) · one-page summary (Turkish): [ORKESTRA.md](ORKESTRA.md)

Orkestra is a plugin for **Claude Code** and **Cursor** that drives a development request through a gated
pipeline instead of letting the model improvise: it learns the project, asks only the questions that change the
result, writes a spec and a plan, waits for **one approval from you**, has a team of subagents implement the plan
test-first, proves nothing that worked is broken, sends the diff through an independent review and hands the
result over. It never commits, pushes or deploys unless you ask for it in that message.

```
/orkestra:run add CSV export to the order list        (Claude Code)
/orkestra-run add CSV export to the order list        (Cursor)
```

What makes the result depend less on how strong the model is:

- **The state machine is code, not a prompt.** Ten phases (intake → discover → clarify → spec → plan → approve →
  implement → verify → review → deliver). `orkestra phase next` refuses to advance until the evidence for the
  current phase exists on disk: a plan that passes `orkestra plan check`, a recorded approval, a red and a green
  test run per task, passing gates compared with the baseline taken before the first edit.
- **Guards are hooks.** Every tool call goes through one hook entry that denies, before the call runs: source
  edits before approval, `git commit` / `push` / branch changes without your say-so in that message, reading
  `.env*` and key files, destructive shell commands, a subagent writing outside its role, and commands that
  would forge the approval itself.
- **The next step is computed.** `orkestra next` prints the one action to take now; the model does not have to
  remember the procedure, and only the playbook of the current phase is loaded (`orkestra playbook`), which
  keeps the context small.
- **Everything is reversible and inspectable.** A snapshot before the first edit (`/orkestra:rollback`), an
  activity log, a live view (`orkestra watch`), a watchdog that resumes a stalled run.

## Requirements

- Python 3.9 or newer (standard library only, nothing to `pip install`), git
- [Claude Code](https://claude.com/claude-code); optionally the Cursor CLI (`agent`) for the Cursor engine
- macOS or Linux. Windows is untested.

## Install

```bash
git clone <repository-url> orkestra
bash orkestra/install.sh            # interactive;  --yes accepts the defaults,  --no-mcp skips optional MCP servers
```

The installer first runs the whole test suite on your machine (about two minutes) and stops if anything is red.
Then it registers the checkout as a Claude Code plugin marketplace (`orkestra@orkestra-local`); the plugin is
loaded in place from `orkestra/plugins/orkestra`. For Cursor, additionally:

```bash
python3 orkestra/plugins/orkestra/bin/orkestra cursor install     # skills, subagents, hook entries in ~/.cursor
python3 orkestra/plugins/orkestra/bin/orkestra cursor status
```

`cursor install` appends its entries to `~/.cursor/hooks.json` without touching yours and makes a timestamped
backup first; `cursor uninstall` removes exactly those entries.

Projects are looked up by name under a workspace folder, `~/Projects` by default. Change it with
`"workspace_root"` in `~/.orkestra/config.json` or the `ORKESTRA_WORKSPACE` environment variable.

## Use

| | Claude Code (default engine) | Cursor |
|---|---|---|
| In the chat | `/orkestra:run <request>` | `/orkestra-run <request>` |
| From a terminal | `orkestra run -p <project> "<request>"` | `orkestra run --engine cursor -p <project> "<request>"` |
| Status / continue | `/orkestra:status`, `/orkestra:resume` | `/orkestra-status`, `/orkestra-resume` |
| Undo a run's file changes | `/orkestra:rollback` | `/orkestra-rollback` |
| Commit and push (only you call it) | `/orkestra:ship` | `/orkestra-ship` |

Health check: `orkestra doctor`. Live view of all runs: `orkestra watch`. An optional HTTP bridge
(`bridge/orkestra_bridge.py`) starts and answers runs from n8n, Telegram or any webhook; see README.tr.md.

## Language

The CLI, the hook messages and the playbooks the model reads are in English. The installer output, some status
lines meant for the human (`watch`, bridge notifications) and most of the long-form documentation are currently
in Turkish. Approval is recognised in both languages (`approve`, `yes`, `onay`, `evet`, …).

## Limits you should know

- Guards are filters on tool calls, not a sandbox: the agent runs as you, on your files. They stop the mistakes
  and shortcuts models actually take; they are not a security boundary against a determined adversary.
- If a hook crashes or times out, the tool call is allowed (a broken guard must not lock you out of your editor).
- The Cursor engine depends on the hook behaviour of the Cursor version it was measured on (CLI 2026.10.01,
  IDE 3.23); the Cursor IDE window was exercised less than the CLI. Token usage is not reported for Cursor runs.
- One machine, one user. No hosted service, no telemetry: nothing leaves your machine except what the model
  provider's own client sends.

## Tests

```bash
cd plugins/orkestra/tests && python3 -m unittest discover -s . -q     # 357 tests
cd bridge/tests && python3 -m unittest discover -s . -q                # 16 tests
cd integrations/openclaw/plugin && npm test                            # 15 tests
```

## Credits and licences

Orkestra is released under the [MIT License](LICENSE).

Its playbooks and agent prompts were written with these projects as references; selected files from them are
kept, unmodified and never loaded at runtime, under `plugins/orkestra/vendor/`, each with its own licence:
[superpowers](https://github.com/obra/superpowers), [spec-kit](https://github.com/github/spec-kit),
[BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD), [gsd-core](https://github.com/open-gsd/gsd-core),
[OpenSpec](https://github.com/Fission-AI/OpenSpec), [humanlayer](https://github.com/humanlayer/humanlayer)
(Apache-2.0), [compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin),
[ECC](https://github.com/affaan-m/ECC) and
[andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills). Copyright notices, pinned commits
and licence files: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Orkestra is an independent project. It is not affiliated with or endorsed by Anthropic, Anysphere (Cursor) or
any of the projects above. "Claude" and "Cursor" are trademarks of their owners.

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) · Security reports: [SECURITY.md](SECURITY.md)
