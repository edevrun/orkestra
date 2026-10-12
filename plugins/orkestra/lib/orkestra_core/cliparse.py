"""The orkestra command line as data: ONE parser for the CLI and for the guards.

The guards used to read `orkestra …` command lines with their own small scanner. Every difference between that
scanner and argparse was a way to run one subcommand while the guard saw another (`phase --no show next`,
`orkestra -- task start T001`, `evidence a <cmd> --`). Now both call `parse()` and `executed_command()` below, so
what the guard analyses is by construction what the CLI executes. No heavy imports here (hooks load this module).
"""
from __future__ import annotations

import argparse
from typing import Any, List, Optional, Tuple

RUNS_A_COMMAND = ("tdd", "evidence")
SUB_ACTION = ("task", "gate", "phase", "memory", "report", "agents", "cursor", "copilot")  # key = "<cmd>:<action>"
SUB_CHECK = ("plan", "trace", "deps", "review", "integrity")                   # key = "<cmd>:check"


class CliParseError(Exception):
    pass


class CliHelp(Exception):
    pass


class QuietParser(argparse.ArgumentParser):
    """argparse that never prints and never exits (for the guards: a hook must not write help text to stdout)."""

    def error(self, message: str) -> Any:               # type: ignore[override]
        raise CliParseError(message)

    def exit(self, status: int = 0, message: Optional[str] = None) -> Any:   # type: ignore[override]
        if status == 0:
            raise CliHelp()
        raise CliParseError(message or "")

    def print_help(self, file: Any = None) -> None:
        pass

    def print_usage(self, file: Any = None) -> None:
        pass

    def _print_message(self, message: str, file: Any = None) -> None:
        pass


def split_remainder(a: argparse.Namespace, tokens: List[str]) -> List[str]:
    """Pull -p/--project/--run that agents place before `--` out of a REMAINDER list.

    Without a `--` the remainder is the command verbatim (it may legitimately contain `-p`).
    """
    if "--" not in tokens:
        return tokens
    out: List[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--":
            out.extend(tokens[i + 1:])
            break
        if tok in ("-p", "--project") and i + 1 < len(tokens):
            a.project = tokens[i + 1]
            i += 2
            continue
        if tok == "--run" and i + 1 < len(tokens):
            a.run = tokens[i + 1]
            i += 2
            continue
        if tok == "--why" and i + 1 < len(tokens):
            a.why = tokens[i + 1]
            i += 2
            continue
        if tok.startswith("--project="):
            a.project = tok.split("=", 1)[1]
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def build_parser(parser_class: Any = argparse.ArgumentParser) -> argparse.ArgumentParser:
    # allow_abbrev=False everywhere: `--no` must never mean `--note` (the guards read the command line literally)
    p = parser_class(prog="orkestra", description="Orkestra — deterministic SDLC engine for Claude Code",
                                allow_abbrev=False)
    p.add_argument("-p", "--project", help="project name (under the workspace) or path")
    p.add_argument("--run", help="run id (default: the project's open run)")
    sub = p.add_subparsers(dest="cmd")

    def sp_(name: str, **kw: Any) -> argparse.ArgumentParser:
        kw.setdefault("allow_abbrev", False)
        return sub.add_parser(name, **kw)

    sp_("context", help="compact digest: project memory + active run + next action")
    sp_("init", help="create .orkestra/ skeleton and detect gates")
    s = sp_("start", help="start a run")
    s.add_argument("--title", required=True)
    s.add_argument("--request")
    s.add_argument("--request-file")
    s.add_argument("--size", default="auto", choices=["auto", "XS", "S", "M", "L"],
                   help="run size; auto (default) infers XS/S/M/L from the request — prefer XS/S for small safe work")
    s.add_argument("--headless", action="store_true")
    s.add_argument("--engine", choices=["claude", "cursor", "copilot"], help="which agent drives the run (default: the one asking, else claude)")
    s.add_argument("--approval-mode", choices=["autopilot", "manual"], default=None,
                   help="default autopilot; use manual when the user explicitly wants to approve the plan")
    sp_("resume")
    s = sp_("status")
    s.add_argument("--json", action="store_true")
    s = sp_("phase")
    s.add_argument("action", choices=["next", "back", "show", "check"])
    s.add_argument("target", nargs="?")
    s.add_argument("--note")
    s.add_argument("--reason")
    for name in ("await", "block", "unblock", "pause", "abort"):
        s = sp_(name)
        s.add_argument("reason", nargs="*")
    s = sp_("gate")
    s.add_argument("action", choices=["detect", "list", "confirm", "baseline", "run", "verify", "last"])
    s.add_argument("names", nargs="*")
    s.add_argument("--write", action="store_true")
    s = sp_("tdd")
    s.add_argument("kind", choices=["red", "green"])
    s.add_argument("task")
    s.add_argument("--why", default="", help="reason when deviating (different command, test changed after red)")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s = sp_("evidence")
    s.add_argument("label")
    s.add_argument("command", nargs=argparse.REMAINDER)
    s = sp_("integrity")
    s.add_argument("check", choices=["check"])
    s = sp_("report")
    s.add_argument("action", choices=["evidence"])
    s.add_argument("--write", action="store_true")
    s = sp_("task")
    s.add_argument("action", choices=["list", "start", "done", "block", "defer", "reopen", "note", "add"])
    s.add_argument("task", nargs="?")
    s.add_argument("text", nargs="*")
    s.add_argument("--agent")
    s.add_argument("--no-tdd", action="store_true")
    s.add_argument("--force", default="", help="reason: reopen a PASS/3rd round, or start despite shared files (goes to the ledger)")
    s = sp_("next", help="the single next step, derived from state + evidence + reviews (use it to resume)")
    s.add_argument("--json", action="store_true")
    s = sp_("tempo", help="where the time went: phases, tasks, review rounds, dead wait")
    s.add_argument("--json", action="store_true")
    s = sp_("agents", help="background subagents registered for the run (clear: drop records of agents that are gone)")
    s.add_argument("action", nargs="?", default="list", choices=["list", "clear"])
    s.add_argument("--json", action="store_true")
    s = sp_("watchdog", help="one scan of every open run: stalled? waiting for you? (notifies; resumes only if allowed)")
    s.add_argument("--json", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="classify and print only: no notification, no resume")
    s.add_argument("--quiet", action="store_true", help="print nothing when every run is healthy (for launchd/cron)")
    s.add_argument("--launchd", action="store_true", help="write a LaunchAgent that runs the scan every minute")
    s = sp_("brief")
    s.add_argument("task")
    for name in ("plan", "trace", "deps", "review"):
        s = sp_(name)
        s.add_argument("check", choices=["check"])
    s = sp_("changes")
    s.add_argument("--json", action="store_true")
    s = sp_("diff")
    s.add_argument("--stat", action="store_true")
    s = sp_("rollback")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--all", action="store_true", help="also revert differences not touched by orkestra tools")
    s = sp_("memory")
    s.add_argument("action", choices=["show", "feature-add", "decision", "learn"])
    s.add_argument("text", nargs="*")
    s.add_argument("--title")
    s.add_argument("--summary")
    s.add_argument("--capabilities")
    s = sp_("playbook")
    s.add_argument("name", nargs="?", help="default: the phase the open run is in")
    s = sp_("template")
    s.add_argument("name")
    s = sp_("watch", help="live terminal view of every run: phase, what it is doing now, who it waits for")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=float, default=2.0)
    s.add_argument("--all", action="store_true", help="also show finished runs")
    s.add_argument("--json", action="store_true")
    s = sp_("dashboard", help="read-only live web page on 127.0.0.1")
    s.add_argument("--port", type=int, default=47616)
    s.add_argument("--open", action="store_true", help="open the browser")
    s = sp_("run", help="start a run from the terminal with the engine of your choice (opens its interactive UI)")
    s.add_argument("request", nargs="+", help="the request, in your own words")
    s.add_argument("--engine", choices=["claude", "cursor", "copilot"], help="default: claude (config: engine.default)")
    s.add_argument("--approval-mode", choices=["autopilot", "manual"], default=None)
    s.add_argument("--print", action="store_true", help="only print the command that would be started")
    s = sp_("cursor", help="Cursor side: build | install | uninstall | status (skills, subagents and hooks in ~/.cursor)")
    s.add_argument("action", choices=["build", "install", "uninstall", "status"])
    s.add_argument("--copy", action="store_true", help="copy files instead of linking them")
    s.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    s = sp_("copilot", help="Copilot side: install | uninstall | status (hooks in ~/.copilot/plugins/orkestra)")
    s.add_argument("action", choices=["install", "uninstall", "status"])
    s.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    s = sp_("setup", help="detect Claude/Cursor/Copilot and install Orkestra for each (interactive, or --yes)")
    s.add_argument("--yes", "-y", action="store_true", help="install every detected engine without asking")
    sp_("doctor")
    sp_("version")
    return p


def hoist_globals(argv: List[str]) -> List[str]:
    """Move -p/--project/--run to the front so they work anywhere (agents put them everywhere).

    Stops at `--`, and for `tdd <kind> <task>` leaves the test command untouched.
    """
    front: List[str] = []
    rest: List[str] = []
    i, positional, sub = 0, 0, None
    while i < len(argv):
        tok = argv[i]
        if tok == "--" or (sub == "tdd" and positional >= 3) or (sub == "evidence" and positional >= 2):
            rest.extend(argv[i:])
            break
        if tok in ("-p", "--project", "--run") and i + 1 < len(argv):
            front += [tok, argv[i + 1]]
            i += 2
            continue
        if tok == "--force" and i + 1 < len(argv):      # keep its value from being read as a positional
            rest += [tok, argv[i + 1]]
            i += 2
            continue
        if tok.startswith(("--project=", "--run=")):
            front.append(tok)
            i += 1
            continue
        if not tok.startswith("-"):
            if sub is None:
                sub = tok
            positional += 1
        rest.append(tok)
        i += 1
    return front + rest


_QUIET: List[Any] = []


def parse(parser: argparse.ArgumentParser, argv: List[str]) -> argparse.Namespace:
    """hoist_globals + parse_args, for the CLI and for the guards alike.

    A `--` before the subcommand is refused here: argparse versions disagree on it (3.9 runs `-- task start T001`
    as `task start`, 3.10+ rejects it), and the guard must never depend on which python runs the CLI."""
    args = hoist_globals(list(argv))
    k = 0
    while k < len(args):
        if args[k] in ("-p", "--project", "--run"):
            k += 2
        elif args[k].startswith(("--project=", "--run=")):
            k += 1
        else:
            break
    if k < len(args) and args[k] == "--":
        parser.error("`--` before the subcommand is not accepted")
    return parser.parse_args(args)


def inspect(argv: List[str]) -> Tuple[str, List[str], str]:
    """(subcommand key, the command the call would execute, parse error) for the arguments after `orkestra`.

    key: "help" | "task:start" | "phase:next" | "plan:check" | "tdd" | … exactly as the CLI would dispatch it.
    The command list is non-empty only for `tdd` / `evidence`, and it is the list `runs.tdd_record` /
    `runs.run_evidence` receive. A non-empty error means the CLI would stop with a usage error (nothing runs)."""
    if not _QUIET:
        _QUIET.append(build_parser(QuietParser))
    try:
        ns = parse(_QUIET[0], argv)
    except CliHelp:
        return "help", [], ""
    except CliParseError as exc:
        return "", [], str(exc) or "usage error"
    cmd = getattr(ns, "cmd", None)
    if not cmd:
        return "help", [], ""
    if cmd in RUNS_A_COMMAND:
        return cmd, split_remainder(ns, list(getattr(ns, "command", None) or [])), ""
    if cmd in SUB_ACTION:
        return f"{cmd}:{getattr(ns, 'action', '')}", [], ""
    if cmd in SUB_CHECK:
        return f"{cmd}:check", [], ""
    return cmd, [], ""
