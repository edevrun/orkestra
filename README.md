# Orkestra

**A local, hook-enforced delivery pipeline for AI coding agents — same phases and gates on Claude Code, Cursor, and GitHub Copilot.**

> Türkçe ve ayrıntılı belge: [README.tr.md](README.tr.md) · one-page summary (Turkish): [ORKESTRA.md](ORKESTRA.md)

Orkestra turns a development request into a repeatable path across **Claude Code**, **Cursor**, and **GitHub Copilot** (VS Code and Copilot CLI). Autopilot is the default: it resolves ordinary ambiguity from the request and repository, records assumptions, validates the plan and continues through TDD, no-regression gates, independent review, and delivery. A real plan-approval question is asked only when the human user explicitly selects manual mode. It never commits, pushes, deploys, or uploads to a store unless the repository owner asks in that message.

Why Orkestra: the state machine, playbooks, and deny/allow decisions live in code and host hooks — not only in a prompt the model can ignore. That reduces variance across models and sessions; it does not make agents infallible (see Limits).

```
/orkestra:run add CSV export to the order list        (Claude Code)
/orkestra-run add CSV export to the order list        (Cursor)
/orkestra:run add CSV export to the order list        (GitHub Copilot)
```

What makes the result depend less on how strong the model is:

- **The state machine is code, not a prompt.** Ten phases (intake → discover → clarify → plan → approve →
  implement → verify → review → deliver → done). `orkestra phase next` checks the current spec/plan hash, critic
  verdict, red/green task evidence, and gates. Autopilot records a policy approval only for a current, reviewed plan;
  manual mode requires the real user's reply.
- **Guards are hooks.** Claude Code, Cursor, and Copilot adapters share one hook-enforced state machine. It denies,
  before a call runs: source edits before the mode-specific plan gate, `git commit` / `push` / branch changes without your say-so in that message, reading
  `.env*` and key files, destructive shell commands, a subagent writing outside its role, and commands that
  would forge the approval itself.
- **The next step is computed.** `orkestra next` prints the one action to take now; the model does not have to
  remember the procedure, and only the playbook of the current phase is loaded (`orkestra playbook`), which
  keeps the context small.
- **Everything is reversible and inspectable.** A snapshot before the first edit (`/orkestra:rollback`), an
  activity log, a live view (`orkestra watch`), a watchdog that resumes a stalled run.

## Requirements

- Python 3.9 or newer (standard library only, nothing to `pip install`), git
- [Claude Code](https://claude.com/claude-code), the Cursor CLI (`agent`), or the Copilot CLI (`copilot`), depending on the engine
- VS Code GitHub Copilot with the Orkestra agent plugin and lifecycle hooks for IDE-chat runs
- macOS or Linux. Windows is untested.

## Install

```bash
git clone https://github.com/soft-ai-solutions/orkestra.git
cd orkestra
bash ./install.sh            # interactive;  --yes accepts the defaults,  --no-mcp skips optional MCP servers
bash ./install.sh --skip-tests   # skip the installer's plugin/bridge unittest run (faster; you own the risk)
```

The installer first runs the Python plugin and bridge test suites on your machine (a few minutes; CPU and system
load affect the duration) and stops if anything is red, unless you pass `--skip-tests`. The OpenClaw Node.js
logic suite is a required CI job (`npm test`; see Tests); full plugin e2e is local/optional and needs a local
OpenClaw install (`npm run test:e2e`). Then it registers the checkout as a Claude Code plugin marketplace
(`orkestra@orkestra-local`); the plugin is loaded in place from `plugins/orkestra`. Without Claude Code CLI, install
continues on a Cursor-only path (warns if neither Claude nor Cursor Orkestra setup is ready). For Cursor, additionally:

```bash
python3 plugins/orkestra/bin/orkestra cursor install     # skills, subagents, hook entries in ~/.cursor
python3 plugins/orkestra/bin/orkestra cursor status
```

For Copilot CLI / VS Code, install and enable the Orkestra Copilot plugin so `SessionStart`, `UserPromptSubmit`,
`PreToolUse`, and `PostToolUse` invoke `plugins/orkestra/hooks/copilot_hook.py`. An unhooked Copilot session must not
start or resume a guarded Orkestra run.

`cursor install` appends its entries to `~/.cursor/hooks.json` without touching yours and makes a timestamped
backup first; `cursor uninstall` removes exactly those entries. Install and `orkestra setup` also ensure a PATH
shim for the `orkestra` CLI at `~/.local/bin/orkestra` (override the directory with `ORKESTRA_SHIM_DIR`).

Projects are looked up by name under a workspace folder, `~/Projects` by default. Change it with
`"workspace_root"` in `~/.orkestra/config.json` or the `ORKESTRA_WORKSPACE` environment variable.

## Use

|                                    | Claude Code (default for ordinary terminals) | Cursor                                                  | GitHub Copilot                                           |
| ---------------------------------- | -------------------------------------------- | ------------------------------------------------------- | -------------------------------------------------------- |
| In the chat                        | `/orkestra:run <request>`                    | `/orkestra-run <request>`                               | `/orkestra:run <request>`                                |
| From a terminal                    | `orkestra run -p <project> "<request>"`      | `orkestra run --engine cursor -p <project> "<request>"` | `orkestra run --engine copilot -p <project> "<request>"` |
| Status / continue                  | `/orkestra:status`, `/orkestra:resume`       | `/orkestra-status`, `/orkestra-resume`                  | `/orkestra:status`, `/orkestra:resume`                   |
| Undo a run's file changes          | `/orkestra:rollback`                         | `/orkestra-rollback`                                    | `/orkestra:rollback`                                     |
| Commit and push (only you call it) | `/orkestra:ship`                             | `/orkestra-ship`                                        | `/orkestra:ship`                                         |

Copilot CLI launches use `gpt-6-luna` with maximum reasoning effort by default. A single supported model or effort
explicitly named in the request overrides that profile; unknown or ambiguous choices fall back to the configured
default. VS Code custom agents inherit the active chat model. VS Code does not expose an API for a plugin to set the
active chat's model/effort, so select GPT-6 Luna / max—or the explicitly requested alternative—in the host model
picker. Approval remains autopilot unless manual mode is explicitly requested.

Health check: `orkestra doctor`. Live view of all runs: `orkestra watch`. An optional HTTP bridge
(`bridge/orkestra_bridge.py`) starts and answers runs from n8n, Telegram or any webhook; see README.tr.md.
With the bridge running, open `http://127.0.0.1:47615/` (default port) for the local chat workbench: select a project
and Claude Code/Cursor/Copilot engine, start a request, answer follow-up questions and stop a run. It asks for the
bridge token; the page keeps it in memory only. Its live flow map shows the current phase, reported subagents,
in-progress tasks, approval/verification gates and recent hook activity. The UI is loopback-only, not a sandbox.

## Use cases

Concrete jobs teams run through Orkestra. Each one uses the same phase machine and hooks; only the request text and
the project's configured gates change.

| Use case | Example request | What Orkestra enforces |
| -------- | --------------- | ---------------------- |
| **Feature slice with TDD** | `add CSV export to the order list` | Spec + plan + critic before edits; each task red→green evidence; no-regression gates; independent review |
| **Bug fix without regressions** | `fix null crash when the cart is empty` | Scoped plan; failing test first when the task requires TDD; verify phase re-runs project gates |
| **Refactor behind green gates** | `extract pricing into a pure module; keep public API` | File-scoped implementer; gates must stay green; rollback snapshot if you reject the result |
| **Manual approval for sensitive work** | `… and ask me for plan approval` | Switches that run to manual mode; only your chat reply records approve/reject — not the model |
| **Multi-engine same project** | Claude / Cursor / Copilot on one repo | One `state.json` and evidence tree; resume continues on the engine that drove the run |
| **Headless / bridge automation** | Telegram `/yap shop …` or `POST /runs` on the local bridge | Same hooks as interactive chat; loopback UI + token; no cloud control plane |
| **Safe ship** | `/orkestra:ship` after you reviewed the diff | Commit/push only when **you** invoke ship (or grant git in that message); deploy/store uploads are never automatic |

Patterns that stay outside Orkestra by design: open-ended chat without a run, editing another project's files from a
bound session (off-topic prompts unbind Stop follow-ups), and forging plan approval from inside the agent shell
(origin checks refuse those hook calls).

## Language

The CLI, the hook messages and the playbooks the model reads are in English. The installer output, some status
lines meant for the human (`watch`, bridge notifications) and most of the long-form documentation are currently
in Turkish. Approval is recognised in both languages (`approve`, `yes`, `onay`, `evet`, …).

## Limits you should know

- Guards are filters on tool calls, not a sandbox: the agent runs as you, on your files. They stop the mistakes
  and shortcuts models actually take; they are not a security boundary against a determined adversary.
- Claude Code and Cursor hooks are fail-open: if a hook crashes or times out, the tool call is allowed (a broken
  guard must not lock you out of your editor). Copilot `PreToolUse` is fail-closed: a hook crash or invalid payload
  denies the tool call. That asymmetry is intentional; Claude/Cursor fail-open stays the product decision.
- The Cursor engine depends on the hook behaviour of the Cursor version it was measured on (CLI 2026.10.01,
  IDE 3.23); the Cursor IDE window was exercised less than the CLI. Token usage is not reported for Cursor runs.
- One machine, one user. No hosted service, no telemetry: nothing leaves your machine except what the model
  provider's own client sends.

## Tests

```bash
cd plugins/orkestra/tests && python3 -m unittest discover -s . -q     # plugin suite (count in CI log)
cd bridge/tests && python3 -m unittest discover -s . -q                # bridge suite
cd integrations/openclaw/plugin && npm test                            # OpenClaw logic-only (required CI)
cd integrations/openclaw/plugin && npm run test:e2e                    # e2e (local/optional; needs OPENCLAW_PKG / Homebrew openclaw)
```

CI is configured to run the Python suites on Linux and macOS with Python 3.9, 3.13, and 3.14; the required
OpenClaw logic-only job runs on Node.js 22 (`npm test`, no Homebrew `openclaw`, no `continue-on-error`).

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

Contributing: [CONTRIBUTING.md](CONTRIBUTING.md) · Onboarding: [ONBOARDING.md](ONBOARDING.md) · Agent rules: [AGENTS.md](AGENTS.md) · Code of Conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) · Security reports: [SECURITY.md](SECURITY.md)
