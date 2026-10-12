# Onboarding — Orkestra on your machine

English product overview: [README.md](README.md). Turkish ops detail: [README.tr.md](README.tr.md).
Agent rules for this repo: [AGENTS.md](AGENTS.md).

## Quickstart

```bash
git clone https://github.com/soft-ai-solutions/orkestra.git
cd orkestra
bash ./install.sh            # interactive; --yes accepts defaults
bash ./install.sh --skip-tests   # skip installer unittest run (you own the risk)
orkestra doctor              # exit 0 when required checks pass and at least one engine is ready
orkestra setup               # detect Claude / Cursor / Copilot; ask which to enable
# or: orkestra setup --yes   # enable every detected engine without prompts
```

`install.sh` and `orkestra setup` ensure a PATH shim so `orkestra` resolves from `~/.local/bin`
(override with `ORKESTRA_SHIM_DIR`). Put `~/.local/bin` on your shell `PATH` if it is not already.

Per-engine installs (same as `setup` chooses):

```bash
orkestra cursor install      # ~/.cursor skills, agents, hooks
orkestra cursor status
orkestra copilot install     # Copilot hooks under $COPILOT_HOME (or ~/.copilot)
orkestra copilot status
```

Projects resolve by name under the workspace root (`~/Projects` by default; override with
`ORKESTRA_WORKSPACE` or `workspace_root` in `~/.orkestra/config.json`).

## Copilot host enable (manual)

`orkestra copilot install` writes Orkestra hook entries for the Copilot plugin. There is **no supported API**
in this repository to flip every host UI toggle (VS Code GitHub Copilot agent plugin, session hooks, model
picker). After install:

1. Confirm `orkestra copilot status` reports hooks active.
2. In the Copilot host (VS Code and/or Copilot CLI), enable the Orkestra agent plugin / lifecycle hooks yourself
   so `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `PostToolUse` run `copilot_hook.py`.
3. Do not start or resume a guarded Orkestra run from an unhooked Copilot session.

If a future host exposes a stable enable API, document it here; until then treat host enable as a manual step.

## Smoke

```bash
orkestra doctor
orkestra run -p <project> "describe the smallest safe change you would make"
```

Stop with the host stop control or `orkestra` status/resume tools as described in the README.
