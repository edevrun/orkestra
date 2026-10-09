# Security

Orkestra runs on your machine with your permissions. Its guards are hooks that filter the agent's tool calls;
they are not a sandbox and not a boundary against a hostile program (see "Limits" in the README).

**Reporting.** Please report a way to bypass a guard, to forge an approval, or to make a hook leak data through
the repository's private vulnerability reporting ("Security" tab → "Report a vulnerability") rather than a public
issue. Include the command or tool call, the engine (Claude Code or Cursor) and its version, and
`orkestra version`.

**What Orkestra stores.** State and logs live in `<project>/.orkestra/` and `~/.orkestra/`. The bridge token is in
`~/.orkestra/bridge.secret.json` (mode 600) and the bridge listens on 127.0.0.1 only. Nothing is sent to any
server by Orkestra itself.

**Supported versions.** The latest release.
