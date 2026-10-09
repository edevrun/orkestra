"""Where is the agent's shell? — for engines whose hook events do not say.

Claude Code sends the working directory with every hook event. Cursor's shell is one long-lived process per
conversation: `cd` in one tool call is still in effect in the next (measured 2026-10-05), and its hook events carry
`cwd: ""`. A guard that resolved `echo x > state.json` against the project root after an earlier `cd .orkestra/runs/…`
would be judging another file than the one written. So the directory is followed here, command by command:

    after(command, cwd) → the directory the shell is in afterwards, or None when that cannot be known
                          (`cd "$X"`, `cd -`, pushd/popd, eval/source, a `cd` inside a loop or a pipeline …)

Unknown is a state of its own: the caller refuses relative work until a command starts with `cd <absolute path>`.
Light module (stdlib only, no shell parser import): it runs for every shell call of every chat.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import List, Optional, Tuple

_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
MOVERS = ("cd", "chdir", "pushd", "popd")
UNKNOWABLE = ("eval", "source", ".", "pushd", "popd", "exec", "trap",       # may move the shell in ways not visible here
              "z", "zi", "j", "autojump", "up", "..", "...", "....", "-")   # well-known directory jumpers / aliases
KEYWORDS = ("if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done", "case", "esac", "function",
            "select", "{", "}", "!", "time", "coproc")
PREFIXES = ("builtin", "command", "noglob", "nocorrect")
DEFINES_RE = re.compile(r"\balias\s+[-\w.]+=|\bfunction\s+[\w.-]+|(?:^|[\s;&|])[\w.-]+\s*\(\)\s*\{|\bCDPATH\b|\bautocd\b|"
                        r"\bchase_?links\b|\bpushd_?\w*\b", re.I)
QUICK_RE = re.compile(r"cd|c\\d|chdir|pushd|popd|eval|source|exec|trap|autojump|(?:^|[\s;&|({`])(?:\.{1,4}|-|z|zi|j|up)(?=$|[\s;&|)}])")


def mentions_move(command: str) -> bool:
    """Cheap pre-check: can this command possibly change the shell's directory? (broad on purpose: `'cd' x`,
    `\\cd x` and `c\\d x` are `cd` too)"""
    return bool(QUICK_RE.search(command or ""))


def redefines(command: str) -> bool:
    """Does the command define an alias / function or set a shell option that changes what `cd` (or any word) does?
    The shell keeps those between calls; from then on a directory followed here is a guess."""
    return bool(DEFINES_RE.search(command or ""))


def _strip_heredocs(cmd: str) -> str:
    lines, out, pending = cmd.split("\n"), [], []
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        pending += [m.group(2) for m in _HEREDOC_RE.finditer(line)]
    return "\n".join(out)


def _commands(cmd: str) -> Optional[List[Tuple[List[str], str]]]:
    """Top-level simple commands as (words, operator that follows). Words inside (…), $(…), `…` are not returned
    (a subshell cannot move its parent). None = quoting / nesting this scanner does not follow."""
    out: List[Tuple[List[str], str]] = []
    words: List[str] = []
    cur, has, depth, i, n = "", False, 0, 0, len(cmd)

    def end_word() -> None:
        nonlocal cur, has
        if has and depth == 0:
            words.append(cur)
        cur, has = "", False

    def end_cmd(op: str) -> None:
        nonlocal words
        end_word()
        if depth == 0:
            out.append((words, op))
            words = []

    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            if cmd[i + 1] != "\n":
                cur, has = cur + cmd[i + 1], True
            i += 2
            continue
        if c == "'":
            j = cmd.find("'", i + 1)
            if j < 0:
                return None
            cur, has = cur + cmd[i + 1:j], True
            i = j + 1
            continue
        if c == '"':
            j, buf = i + 1, ""
            while j < n and cmd[j] != '"':
                if cmd[j] == "\\" and j + 1 < n:
                    buf += cmd[j + 1]
                    j += 2
                    continue
                if cmd[j] in "$`":
                    buf += "\0"                     # an expansion: the value is not known here
                buf += cmd[j]
                j += 1
            if j >= n:
                return None
            cur, has = cur + buf, True
            i = j + 1
            continue
        if c == "`":
            j = cmd.find("`", i + 1)
            if j < 0:
                return None
            cur, has = cur + "\0", True
            i = j + 1
            continue
        if c == "$" and i + 1 < n and cmd[i + 1] == "(":
            d, j = 1, i + 2
            while j < n and d:
                d += cmd[j] == "("
                d -= cmd[j] == ")"
                j += 1
            if d:
                return None
            cur, has = cur + "\0", True
            i = j
            continue
        if c == "$":
            cur, has = cur + "\0$", True
            i += 1
            continue
        if c == "(":
            end_word()
            depth += 1
            i += 1
            continue
        if c == ")":
            end_word()
            depth -= 1
            if depth < 0:
                return None
            i += 1
            continue
        if c in ";&|\n":
            op = c
            if c in "&|" and i + 1 < n and cmd[i + 1] == c:
                op = c * 2
            elif c == ";" and i + 1 < n and cmd[i + 1] == ";":
                op = ";;"
            elif c == "|" and i + 1 < n and cmd[i + 1] == "&":
                op = "|&"
            elif c == "&" and i > 0 and cmd[i - 1] in "<>":
                cur, has = cur + c, True            # `>&2`, `<&3`: part of a redirect, not an operator
                i += 1
                continue
            elif c == "&" and i + 1 < n and cmd[i + 1] == ">":
                cur, has = cur + c, True            # `&>file`
                i += 1
                continue
            end_cmd(op)
            i += len(op)
            continue
        if c in " \t":
            end_word()
            i += 1
            continue
        if c == "#" and not has:
            j = cmd.find("\n", i)
            i = n if j < 0 else j
            continue
        cur, has = cur + c, True
        i += 1
    if depth != 0:
        return None
    end_cmd("")
    return [(w, op) for w, op in out if w]


def _target(arg: str, cwd: str, home: str) -> Optional[str]:
    if "\0" in arg or any(ch in arg for ch in "*?[{"):
        return None
    if arg == "~":
        return home
    if arg.startswith("~/"):
        arg = os.path.join(home, arg[2:])
    elif arg.startswith("~"):
        return None                                 # ~user
    return os.path.normpath(arg if os.path.isabs(arg) else os.path.join(cwd or "/", arg))


def outcome(command: str, cwd: Optional[str], home: Optional[str] = None, prev: Optional[str] = None) -> dict:
    """{"cwd": directory afterwards or None, "conditional": bool, "pure": bool}

    `cwd` is where the shell is if every `&&` link succeeds. `conditional` = a `cd` comes after another command in
    the same `&&` chain (`make && cd build`): whether it ran depends on an exit status nobody has seen yet. `pure` =
    the whole command is one `&&` chain, so a zero exit proves every link ran. The caller keeps the directory
    unknown for a conditional result until the command has finished successfully (and only if it is pure)."""
    res = {"cwd": cwd, "conditional": False, "pure": False}
    command = command or ""
    if not mentions_move(command) and re.search(r"[\s;&|<>$`(]", command.strip()):
        return res                                  # (a single bare word may still be a directory: zsh autocd)
    cmds = _commands(_strip_heredocs(command))
    if cmds is None:
        return dict(res, cwd=None)
    if not cmds:
        return res
    home = home or os.path.expanduser("~")
    ops = [op for _w, op in cmds[:-1]]
    res["pure"] = all(op == "&&" for op in ops) and cmds[-1][1] == ""
    cur, moved, chain_dirty = cwd, False, False
    has_or = any(op == "||" for _w, op in cmds)
    for idx, (words, op) in enumerate(cmds):
        before = cmds[idx - 1][1] if idx else ""
        if before not in ("&&", "||"):
            chain_dirty = False                     # `;`, newline, `&`, `|`: a new chain
        assigns = []
        while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
            assigns.append(words[0].split("=", 1)[0])
            words = words[1:]
        only_looks = False
        while words and words[0] in PREFIXES:
            words = words[1:]
            while words and words[0].startswith("-") and words[0] != "-":
                only_looks = only_looks or "v" in words[0].lower()      # `command -v cd` only prints
                words = words[1:]
        if not words:
            continue
        head = words[0]
        if head in KEYWORDS:
            if any(w in MOVERS + UNKNOWABLE for w in words[1:]):
                return dict(res, cwd=None)          # `if …; then cd x`, `{ cd x; }`, `for … do cd`
            chain_dirty = True
            continue
        if only_looks:
            chain_dirty = True
            continue
        if head in UNKNOWABLE:
            return dict(res, cwd=None)
        if head not in MOVERS:
            if len(words) == 1 and cur is not None and "\0" not in head and \
                    os.path.isdir(os.path.expanduser(head) if head.startswith(("/", "~")) else os.path.join(cur, head)) \
                    and ("/" in head or head.startswith("~") or not shutil.which(head)):
                return dict(res, cwd=None)          # zsh `autocd`: a directory name alone is a `cd`
            chain_dirty = True
            continue
        if has_or or op in ("|", "|&") or before in ("|", "|&") or any(a in ("HOME", "CDPATH", "PWD", "OLDPWD") for a in assigns):
            return dict(res, cwd=None)              # `a || cd x`, a pipeline element, `HOME=… cd`
        if op == "&":
            continue                                # `cd x &` runs in a background subshell
        physical = any(re.match(r"^-[LPe@]*P[LPe@]*$", w) for w in words[1:])
        args = [w for w in words[1:] if not re.match(r"^-[LPe@]+$", w)]
        args = [w for w in args if not re.match(r"^\d*[<>]", w)]
        if len(args) > 1:
            return dict(res, cwd=None)
        if chain_dirty:
            res["conditional"] = True               # runs only if what came before it in this chain succeeded
        if not args:
            nxt = home
        elif args[0] == "-":
            nxt, prev = prev, cur
        elif cur is None and not (os.path.isabs(args[0]) or args[0].startswith("~")):
            nxt = None
        else:
            nxt = _target(args[0], cur or "", home)
            if nxt is not None and cur is not None and not os.path.isabs(args[0]) and ".." in args[0].split("/") \
                    and os.path.realpath(cur) != os.path.normpath(cur):
                nxt = None                          # `cd ..` inside a symlinked directory: logical or physical parent?
        if nxt is None:
            return dict(res, cwd=None)
        if physical:
            nxt = os.path.realpath(nxt)
        prev, cur, moved = cur, nxt, True
    res["cwd"] = cur
    if not moved:
        res["conditional"] = False
    return res


def after(command: str, cwd: Optional[str], home: Optional[str] = None, prev: Optional[str] = None) -> Optional[str]:
    """Directory of the shell after `command` ran from `cwd`, every `&&` link assumed to succeed (None = unknown,
    also as input). See `outcome` for what that assumption is worth."""
    return outcome(command, cwd, home, prev)["cwd"]


def anchors(command: str) -> bool:
    """Does the command begin by putting the shell in a known place (`cd /abs/path && …`)?"""
    cmds = _commands(_strip_heredocs(command or ""))
    if not cmds:
        return False
    words = cmds[0][0]
    return len(words) == 2 and words[0] == "cd" and "\0" not in words[1] and \
        (os.path.isabs(words[1]) or words[1] == "~" or words[1].startswith("~/")) and cmds[0][1] in ("&&", ";", "\n", "")
