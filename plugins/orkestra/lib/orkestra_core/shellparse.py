"""Shell command segmentation for guards.

Splits a Bash command string into simple commands ("segments"), recursing into
`bash -c '...'`, `eval`, `$(...)` and backticks, skipping heredoc bodies, and
peeling wrappers (sudo, env, time, timeout, xargs, nohup, npx, uv run ...).

This is a guard heuristic, not a shell. It is tuned to never miss the common
forms an agent writes, and to avoid false positives from quoted text.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", ";;", "|&", "{", "}"}
# reserved words that start/end compound commands: the word after them is a real command
LEADING_KEYWORDS = {"if", "then", "elif", "else", "do", "while", "until", "!", "time", "{", "}"}
CLOSING_KEYWORDS = {"fi", "done", "esac"}
REDIRECT_OPS = {">", ">>", ">|", "&>", "&>>", "<", "<<", "<<<", "<>", ">&", "<&"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
MAX_DEPTH = 4
# Opt-in (config: guards.test_syntax, default off): understand `[[ a > b ]]`, `(( n > 100 ))` and `$(( 5 > 3 ))` as
# comparisons instead of redirects. Off, those forms are read the strict 1.3.0 way (a `>` inside them counts as a
# write target — a false positive for read-only agents and before approval, never a missed write). The recogniser
# below is deliberately narrow and was checked against bash; it has NOT been checked against zsh on a Mac yet.
TEST_SYNTAX = False


@dataclass
class Segment:
    program: str                     # basename of the executable, e.g. "git"
    args: List[str] = field(default_factory=list)
    redirects: List[Tuple[str, str]] = field(default_factory=list)  # (op, target)
    raw: str = ""

    def argv(self) -> List[str]:
        return [self.program] + self.args


# ---------------------------------------------------------------- pre-processing

_HEREDOC_RE = re.compile(r"(?<!<)<<(?!<)-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


_SHELL_HEREDOC_RE = re.compile(r"^\s*(?:\S*/)?(?:bash|sh|zsh|dash|ksh)\b[^<]*<<")


def _strip_heredocs(cmd: str) -> str:
    """Remove heredoc bodies (they are data, not commands) — unless they are fed to a shell."""
    lines = cmd.split("\n")
    out: List[str] = []
    pending: List[str] = []
    keep_body = False
    for line in lines:
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
                keep_body = False
            elif keep_body:
                out.append(line)
            continue
        out.append(line)
        for m in _HEREDOC_RE.finditer(line):
            pending.append(m.group(2))
            if _SHELL_HEREDOC_RE.match(line):
                keep_body = True
    return "\n".join(out)


def _extract_substitutions(cmd: str) -> Tuple[str, List[str]]:
    """Pull out $(...) and `...` bodies (outside single quotes). Returns (cmd_with_placeholders, bodies)."""
    bodies: List[str] = []
    out = []
    i, n = 0, len(cmd)
    in_single = False
    in_double = False
    while i < n:
        ch = cmd[i]
        if ch == "\\" and not in_single and i + 1 < n:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            out.append(ch)
            i += 1
            continue
        if not in_single and ch == "$" and i + 1 < n and cmd[i + 1] == "(" and not cmd.startswith("$((", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if cmd[j] == "(":
                    depth += 1
                elif cmd[j] == ")":
                    depth -= 1
                j += 1
            bodies.append(cmd[i + 2:j - 1])
            out.append(" __SUBST__ ")
            i = j
            continue
        if not in_single and ch == "`":
            j = cmd.find("`", i + 1)
            if j == -1:
                j = n
            bodies.append(cmd[i + 1:j])
            out.append(" __SUBST__ ")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out), bodies


def _newlines_to_semicolons(cmd: str) -> str:
    out = []
    in_single = in_double = False
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n:
            if cmd[i + 1] == "\n" and not in_single:
                i += 2
                continue
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "\n" and not in_single and not in_double:
            out.append(" ; ")
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_fd_numbers(cmd: str) -> str:
    """Drop the file-descriptor number glued to a redirect (`2>&1`, `2>/dev/null`, `1>>log`) outside quotes.

    shlex splits `2>&1` into "2", ">&", "1": without this the "2" became a stray argument (seen in the field as
    `orkestra --help 2>&1` → subcommand "2", and `npm i x 2>&1` → package "2"). `echo 2 > f` (spaced) is untouched.
    """
    out = []
    in_single = in_double = False
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n and not in_single:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch.isdigit() and not in_single and not in_double and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            j = i
            while j < n and cmd[j].isdigit():
                j += 1
            if j < n and cmd[j] in "<>" and j - i <= 2:
                out.append(" ")              # keep the operator apart from what came before (`true;2>f` → `true; >f`)
                i = j
                continue
        out.append(ch)
        i += 1
    return "".join(out)


_OPERATORS = sorted(SEPARATORS | REDIRECT_OPS, key=len, reverse=True)
_PUNCT = ";&|()<>"


def _space_operators(cmd: str) -> str:
    """Outside quotes, split a run of punctuation into real operators (`;>f` → `; > f`, `)>f` → `) > f`).

    shlex returns such a run as ONE unknown token, which hid the redirect behind it from the guards."""
    out: List[str] = []
    in_single = in_double = False
    i, n = 0, len(cmd)
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n and not in_single:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch in _PUNCT and not in_single and not in_double:
            j = i
            while j < n and cmd[j] in _PUNCT:
                j += 1
            run = cmd[i:j]
            if len(run) > 1 and run not in SEPARATORS and run not in REDIRECT_OPS:
                k = 0
                while k < len(run):
                    op = next((o for o in _OPERATORS if run.startswith(o, k)), run[k])
                    out.append(f" {op} ")
                    k += len(op)
                i = j
                continue
            out.append(run)
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


_AR_TOKEN = re.compile(r"\s*(?:(?P<opnd>\$?\w+|\$\{\w+\}|\$[#?])|(?P<inc>\+\+|--)|(?P<un>[!~])|(?P<pm>[-+])"
                       r"|(?P<bin>\*\*=?|<<=?|>>=?|<=|>=|==|!=|&&|\|\||[*/%&|^]=?|[-+]=|=|<|>|\?|:|,))")


def _is_arithmetic(body: str) -> bool:
    """operand (operator operand)* — one left-to-right pass (the regex this replaces could backtrack for minutes on
    `(( --------… ))`). Two operands in a row (`echo x`) is what a command looks like: not arithmetic."""
    pos, n = 0, len(body)
    want_operand, seen = True, False
    while True:
        m = _AR_TOKEN.match(body, pos)
        if not m:
            break
        pos = m.end()
        kind = m.lastgroup
        if want_operand:
            if kind == "opnd":
                want_operand, seen = False, True
            elif kind not in ("inc", "un", "pm"):       # prefix operators only
                return False
        else:
            if kind in ("bin", "pm"):
                want_operand = True
            elif kind != "inc":                         # postfix ++ / --
                return False
    return seen and not want_operand and not body[pos:].strip()


_ARITH_MAX = 200


def _lift_substitutions(body: str) -> Tuple[str, List[str]]:
    """(`body` with each `$( … )` replaced by 0, the substitutions themselves) — `(( $(wc -l < f) > 100 ))`."""
    if "$(" not in body:
        return body, []
    out: List[str] = []
    subs: List[str] = []
    i, n = 0, len(body)
    while i < n:
        if body.startswith("$(", i) and not body.startswith("$((", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if body[j] == "(":
                    depth += 1
                elif body[j] == ")":
                    depth -= 1
                j += 1
            if depth:                              # unbalanced: not something we understand
                return body, []
            subs.append(body[i:j])
            out.append("0")
            i = j
            continue
        out.append(body[i])
        i += 1
    return "".join(out), subs


def _strip_arithmetic(cmd: str, bare_ok: bool = True) -> str:
    """Replace `$(( expr ))` and `(( expr ))` by a plain word when — and only when — the body is unmistakably an
    arithmetic expression (operands separated by operators). `(( n > 100 ))` compares; it does not write a file
    named 100. Anything else (`((echo x > f))`, `( (cmd > f) )`) is left alone and parsed as commands."""
    if "((" not in cmd or "))" not in cmd:
        return cmd
    out: List[str] = []
    in_single = in_double = False
    i, n = 0, len(cmd)
    next_close = -1
    while i < n:
        ch = cmd[i]
        if ch == "\\" and i + 1 < n and not in_single:
            out.append(cmd[i:i + 2])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and cmd.startswith("((", i + (1 if ch == "$" else 0)) \
                and (ch == "$" or (ch == "(" and not in_double and bare_ok)):
            start = i + (3 if ch == "$" else 2)
            if next_close < start:                       # position of the next `)`, found once per stretch
                next_close = cmd.find(")", start)
                if next_close < 0:
                    next_close = n + _ARITH_MAX + 9
            j, depth, end = start, 0, -1
            # bounded look-ahead, and none at all when no `)` is near: a run of `(` must stay linear
            limit = min(n, start + _ARITH_MAX + 2) if next_close - start <= _ARITH_MAX else start
            while j < limit:
                if cmd[j] == "(":
                    depth += 1
                elif cmd[j] == ")":
                    if depth:
                        depth -= 1
                    else:
                        end = j if cmd.startswith("))", j) else -1      # the closing pair must be adjacent: `))`
                        break
                j += 1
            if end >= 0:
                body, subs = _lift_substitutions(cmd[start:end])
                if _is_arithmetic(body.replace("(", " ").replace(")", " ")):
                    # command substitutions inside the expression still run: keep them for the analysis
                    out.append(("0" if ch == "$" else " true ") + "".join(f" {x}" for x in subs) + " ")
                    i = end + 2
                    continue
        out.append(ch)
        i += 1
    return "".join(out)


def _tokenize(cmd: str) -> List[str]:
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        lex.commenters = "#"
        return list(lex)
    except ValueError:
        # unbalanced quotes: degrade to a crude split that still exposes words
        crude = re.sub(r"([;&|()<>])", r" \1 ", cmd.replace('"', " ").replace("'", " "))
        return crude.split()


# ---------------------------------------------------------------- wrappers

def _peel(tokens: List[str]) -> List[str]:
    """Remove env assignments and transparent wrappers from the front of a simple command."""
    t = list(tokens)
    changed = True
    guard = 0
    while t and changed and guard < 20:
        guard += 1
        changed = False
        while t and t[0] in LEADING_KEYWORDS:
            t.pop(0)
            changed = True
        if t and t[0] in ("for", "select", "case"):
            # `for x in a b; do ...` — the header is not a command; the body follows a separator
            return []
        if t and t[0] in ("[[", "["):
            return t[:1]
        while t and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t[0]):
            t.pop(0)
            changed = True
        if not t:
            break
        head = os.path.basename(t[0])
        if head in ("sudo", "doas"):
            t.pop(0)
            while t and t[0].startswith("-"):
                flag = t.pop(0)
                if flag in ("-u", "-g", "-C", "-h", "-p", "-U") and t:
                    t.pop(0)
            changed = True
        elif head == "env":
            t.pop(0)
            while t and (t[0].startswith("-") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t[0])):
                flag = t.pop(0)
                if flag in ("-u", "--unset", "-C", "--chdir", "-S") and t:
                    t.pop(0)
            changed = True
        elif head in ("command", "builtin", "exec", "nohup", "caffeinate", "unbuffer"):
            t.pop(0)
            while t and t[0].startswith("-"):
                t.pop(0)
            changed = True
        elif head == "time":
            t.pop(0)
            while t and t[0].startswith("-"):
                t.pop(0)
            changed = True
        elif head == "nice":
            t.pop(0)
            if t and t[0] == "-n" and len(t) > 1:
                t = t[2:]
            elif t and re.match(r"^-\d+$", t[0]):
                t.pop(0)
            changed = True
        elif head in ("timeout", "gtimeout"):
            t.pop(0)
            while t and t[0].startswith("-"):
                flag = t.pop(0)
                if flag in ("-s", "--signal", "-k", "--kill-after") and t:
                    t.pop(0)
            if t and re.match(r"^\d+(\.\d+)?[smhd]?$", t[0]):
                t.pop(0)
            changed = True
        elif head == "stdbuf":
            t.pop(0)
            while t and t[0].startswith("-"):
                t.pop(0)
            changed = True
        elif head == "xargs":
            t.pop(0)
            while t and t[0].startswith("-"):
                flag = t.pop(0)
                if flag in ("-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a") and t:
                    t.pop(0)
            changed = True
        elif head in ("npx", "bunx", "pnpx"):
            t.pop(0)
            while t and t[0].startswith("-"):
                flag = t.pop(0)
                if flag in ("-p", "--package", "-c", "--call") and t:
                    t.pop(0)
            changed = True
        elif head in ("pnpm", "yarn") and len(t) > 1 and t[1] == "dlx":
            t = t[2:]
            changed = True
        elif head in ("uv", "poetry", "pipenv", "hatch", "rye", "pdm") and len(t) > 1 and t[1] == "run":
            t = t[2:]
            while t and t[0].startswith("-"):
                flag = t.pop(0)
                if flag in ("--with", "--python", "-p", "--project", "--directory", "--env-file", "--group") and t:
                    t.pop(0)
            changed = True
        elif head == "bundle" and len(t) > 1 and t[1] == "exec":
            t = t[2:]
            changed = True
    return t


_PLAIN_SH_RE = re.compile(r"(?:^|[\s;&|(/'\"])(?:sh|dash|ash|ksh|mksh|fish|busybox|csh|tcsh)(?=[\s;&|)'\"]|$)")
_TEST_LEADERS = {"if", "then", "elif", "else", "do", "while", "until", "!", "time", "{"}
_WORD_END = " \t\n;&|()<>"


def _word_end(cmd: str, i: int) -> Tuple[int, bool]:
    """(index after the shell word starting at i, whether the word is bare: no quotes, no backslash)."""
    n, bare = len(cmd), True
    while i < n:
        ch = cmd[i]
        if ch in _WORD_END:
            break
        if ch == "\\":
            bare = False
            i += 2
            continue
        if ch == "'":
            bare = False
            j = cmd.find("'", i + 1)
            i = n if j < 0 else j + 1
            continue
        if ch == '"':
            bare = False
            i += 1
            while i < n and cmd[i] != '"':
                i += 2 if cmd[i] == "\\" else 1
            i += 1
            continue
        i += 1
    return min(i, n), bare


def _strip_tests(cmd: str) -> str:
    """Replace a real `[[ … ]]` conditional by `[[ ]]`: inside it `<`, `>`, `&&`, `||` and parentheses are part of
    the test, not redirects or separators (`[[ -f a && $x > 3 ]]` writes nothing).

    "Real" is decided on the source text, where quoting is still visible: the opener must be a BARE `[[` word at
    command position (start, after `; & | (` or a newline, or after bare reserved words that are themselves at
    command position) and a bare `]]` word must close it. `'[[' x > f`, `\\[[ x > f`, `echo [[ x > f`,
    `echo then [[ x > f` and `X=1 [[ x > f` are ordinary commands — bash writes f — and are left alone."""
    if "[[" not in cmd or "]]" not in cmd:
        return cmd
    out: List[str] = []
    i, n = 0, len(cmd)
    cmdpos, no_close = True, False
    while i < n:
        ch = cmd[i]
        if ch in " \t":
            out.append(ch)
            i += 1
            continue
        if ch in ";&|(\n":
            out.append(ch)
            i += 1
            cmdpos = True
            continue
        if ch in "<>)":
            out.append(ch)
            i += 1
            cmdpos = False
            continue
        j, bare = _word_end(cmd, i)
        word = cmd[i:j]
        if cmdpos and bare and word == "[[" and not no_close:
            k, end = j, -1
            while k < n:                              # the closing bare `]]` word
                if cmd[k] in _WORD_END:
                    k += 1
                    continue
                k2, b2 = _word_end(cmd, k)
                if b2 and cmd[k:k2] == "]]":
                    end = k
                    break
                k = max(k2, k + 1)
            if end < 0:
                no_close = True                       # none from here on either: never search again (stay linear)
            else:
                inner = cmd[j:end]
                if not any(x in inner for x in ("<(", ">(", "`", "$(")):
                    out.append("[[ ]]")               # $(…) bodies were extracted before and are analysed on their own
                    i = end + 2
                    cmdpos = False
                    continue
        if not (cmdpos and bare and word in _TEST_LEADERS):
            cmdpos = False
        out.append(word)
        i = max(j, i + 1)
    return "".join(out)


# ---------------------------------------------------------------- public api

def segments(cmd: str, _depth: int = 0, _plain_sh: Optional[bool] = None) -> List[Segment]:
    if not cmd or _depth > MAX_DEPTH:
        return []
    if _plain_sh is None:
        # `[[ … ]]` and `(( … ))` are syntax in bash/zsh only. If a plain POSIX shell is involved anywhere in the
        # command (`sh -c`, `dash <<EOF`), they are ordinary commands there and their `>` DOES write a file.
        _plain_sh = bool(_PLAIN_SH_RE.search(cmd))
    cmd = _strip_heredocs(cmd)
    if TEST_SYNTAX:
        cmd = _strip_arithmetic(cmd, bare_ok=not _plain_sh)
    cmd, bodies = _extract_substitutions(cmd)
    if TEST_SYNTAX and not _plain_sh:
        cmd = _strip_tests(cmd)
    cmd = _newlines_to_semicolons(cmd)
    cmd = _strip_fd_numbers(cmd)
    cmd = _space_operators(cmd)
    tokens = _tokenize(cmd)
    result: List[Segment] = []
    for body in bodies:
        result.extend(segments(body, _depth + 1, _plain_sh))

    current: List[str] = []

    def flush() -> None:
        if not current:
            return
        words: List[str] = []
        redirects: List[Tuple[str, str]] = []
        k = 0
        while k < len(current):
            tok = current[k]
            if tok in REDIRECT_OPS:
                target = current[k + 1] if k + 1 < len(current) else ""
                if tok in (">&", "<&") and re.match(r"^\d+$|^-$", target):
                    k += 2
                    continue
                redirects.append((tok, target))
                k += 2
                continue
            words.append(tok)
            k += 1
        redirects = list(dict.fromkeys(redirects))      # `> f > f > f …`: one target, checked once
        peeled = _peel(words)
        if not peeled:
            if any(t for _, t in redirects):  # `exec 3>file`, `> file`, `X=1 >file`: no program, but a file is written
                result.append(Segment(program=":", args=[], redirects=redirects, raw=" ".join(current)))
            current.clear()
            return
        prog = os.path.basename(peeled[0])
        args = peeled[1:]
        if prog in SHELLS:
            inner = _shell_c_arg(args)
            if inner is not None:
                result.extend(segments(inner, _depth + 1, _plain_sh))
                current.clear()
                return
        if prog == "eval":
            result.extend(segments(" ".join(args), _depth + 1, _plain_sh))
            current.clear()
            return
        result.append(Segment(program=prog, args=args, redirects=redirects, raw=" ".join(current)))
        current.clear()

    for tok in tokens:
        if tok in SEPARATORS or (tok in CLOSING_KEYWORDS and not current):
            flush()
        else:
            current.append(tok)
    flush()
    return result


def _shell_c_arg(args: List[str]) -> Optional[str]:
    for i, a in enumerate(args):
        if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
            return args[i + 1] if i + 1 < len(args) else ""
    return None


# ---------------------------------------------------------------- git helpers

GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--super-prefix",
                         "--config-env", "--list-cmds", "--attr-source"}


@dataclass
class GitCall:
    sub: str
    args: List[str]
    globals: List[str] = field(default_factory=list)


def git_call(seg: Segment) -> Optional[GitCall]:
    if seg.program != "git":
        return None
    a = list(seg.args)
    g: List[str] = []
    while a and a[0].startswith("-"):
        flag = a.pop(0)
        g.append(flag)
        if flag in GIT_GLOBAL_WITH_VALUE and a:
            g.append(a.pop(0))
    if not a:
        return GitCall(sub="", args=[], globals=g)
    return GitCall(sub=a[0], args=a[1:], globals=g)


def short_flags(args: List[str]) -> set:
    """Letters of single-dash flag clusters: ['-am', '-n'] -> {'a','m','n'}."""
    out = set()
    for x in args:
        if re.match(r"^-[A-Za-z]+$", x):
            out |= set(x[1:])
    return out


def positional(args: List[str], with_value: Tuple[str, ...] = ()) -> List[str]:
    out, skip = [], False
    for x in args:
        if skip:
            skip = False
            continue
        if x == "--":
            continue
        if x.startswith("-"):
            if x in with_value:
                skip = True
            continue
        out.append(x)
    return out
