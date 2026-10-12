# Security Policy

Orkestra runs on your machine with your OS user permissions. Its guards are filters on the agent's tool calls;
they are not a sandbox and not a boundary against a hostile program (see "Limits" in the README).

## Reporting a vulnerability

Please report ways to bypass a guard, forge an approval, or make a hook leak data through **GitHub Security Advisories**
(repository **Security** tab → **Report a vulnerability**). Prefer that private channel over a public issue.

Include:

- Orkestra version (`orkestra version` or the release tag)
- Engine and host version (Claude Code, Cursor CLI/IDE, or Copilot)
- Exact command or tool call that demonstrates the issue
- Whether a run was bound (`orkestra status`) and the phase, if relevant

Do not open a public issue with a working exploit. We will acknowledge private reports and coordinate a fix before
any disclosure.

## Threat surface (product)

This section describes where Orkestra trusts the environment. It is **not** a checklist for reviewing application
diffs in a target project (those playbooks live under `plugins/orkestra/skills/`).

### Not a sandbox

- Hooks deny or allow **tool calls** from a bound agent session. They do not isolate processes, filesystems, or
  network. The agent runs as your OS user.
- Claude Code and Cursor hooks are **fail-open**: if a hook crashes or times out, the call is allowed so a broken
  guard cannot lock you out of the editor. Treat fail-open as availability over hard deny — that remains the
  product decision for those engines.
- Copilot `PreToolUse` is **fail-closed**: a hook crash or invalid payload returns `permissionDecision: deny` and
  blocks the tool call. Other Copilot hook events still fail open. Do not assume one fail policy across engines.

### Approval forge

- Plan approval is recorded only through the guarded approval path (user reply in manual mode, or policy approval
  of a current reviewed plan in autopilot). Hook origin checks refuse tool calls that try to forge that record
  from inside the agent shell. Report any path that writes an approval without that gate.

### Local bridge

- The optional HTTP bridge (`bridge/orkestra_bridge.py`) listens on **loopback** (`127.0.0.1`) only. The shared
  secret is `~/.orkestra/bridge.secret.json` (mode 600). Anything that can call loopback as your user can use a
  stolen token.
- The browser workbench keeps the token in page memory only (not URL, not persistent browser storage). It is not a
  boundary against other local programs.
- Telegram / webhook integrations share that trust model: one bot token has one consumer; do not reuse a token
  already polled elsewhere (see CONTRIBUTING).

### Model providers

- Orkestra itself does not phone home. Claude Code, Cursor, and Copilot clients may send prompts and project
  context to their providers under those products' terms. Orkestra does not implement or audit those clients.

## What Orkestra stores

State and logs live in `<project>/.orkestra/` and `~/.orkestra/`. The bridge token is in
`~/.orkestra/bridge.secret.json` (mode 600) and the bridge listens on `127.0.0.1` only. Nothing is sent to any
server by Orkestra itself.

The local browser UI is served by that loopback bridge. It asks you to enter the token, does not put it in the URL
or browser storage, and keeps it in page memory only. The selected Claude Code or Cursor client may send prompts and
project context to its own provider under that provider's terms. The UI is not a security boundary against other
programs running as your OS user.

## Supported versions

Only the latest released version receives security fixes.
