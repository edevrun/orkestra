"""Round-1 audit fixes in the Bash guards: the internals guard stays strict for anything that is not provably read-only
(GD-1, GD-2), an escaped space before `#` is no comment (GD-3), variable write targets cannot be redirected (GD-4)."""
from __future__ import annotations

import unittest

import helpers
from orkestra_core import guards
from orkestra_core.paths import DEFAULT_GLOBAL_CONFIG

PLUGIN = helpers.PLUGIN


def decide(cmd, phase="implement"):
    b = {"project": "/w/proj", "run_id": "001-x",
         "state": {"phase": phase, "status": "active", "approved": {"deps": []}}}
    return guards.pre_tool_decision({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/w/proj",
                                     "session_id": "s1"}, DEFAULT_GLOBAL_CONFIG, b, ())


class InternalsGuardStaysStrict(unittest.TestCase):
    P = str(PLUGIN)

    def test_a_command_naming_the_plugin_may_only_continue_with_readers(self):
        P = self.P
        H = f"cd {P}/hooks; "
        for cmd in (H + "./dispatch.py PreToolUse", H + "${SHELL} dispatch.py", H + "r=rm; $r hooks.json",
                    H + "$(echo rm) hooks.json", H + "find . -name hooks.json -delete",
                    H + "find . -name hooks.json -exec rm {} +", H + "curl -o dispatch.py https://example.com/x",
                    H + "wget -O dispatch.py https://example.com/x", H + "tar xzf /tmp/x.tgz", H + "unzip -o /tmp/x.zip",
                    H + "busybox rm hooks.json", H + "watch rm hooks.json", H + "make", H + "npm test",
                    H + "split -l 1 dispatch.py", H + "rename a b dispatch.py", H + "vim -es dispatch.py",
                    f"ls {P}/lib; make test"):
            with self.subTest(cmd=cmd):
                d = decide(cmd)
                self.assertTrue(d.denied and d.guard == "internals", f"{cmd!r} -> {d.guard or 'allowed'}")

    def test_command_substitution_cannot_hide_the_acting_command(self):
        P = self.P
        for cmd in (f"eval $(echo 'rm {P}/hooks/hooks.json')",
                    f"eval $(echo 'echo x > {P}/lib/orkestra_core/guards.py')",
                    f"bash -c \"$(echo 'python3 {P}/hooks/dispatch.py PreToolUse')\"",
                    f"eval $(printf '%s' 'rm {P}/hooks/hooks.json')", f"`echo 'rm {P}/hooks/hooks.json'`",
                    f"$(echo 'rm {P}/hooks/hooks.json')", f"exec $(echo 'rm {P}/hooks/hooks.json')",
                    f"eval \"$(echo 'sed -i s/a/b/ {P}/lib/orkestra_core/guards.py')\"",
                    f"cp /tmp/x {P}/hooks/dispatch.py"):
            with self.subTest(cmd=cmd):
                d = decide(cmd)
                self.assertTrue(d.denied and d.guard == "internals", f"{cmd!r} -> {d.guard or 'allowed'}")

    def test_read_only_inspection_stays_allowed(self):
        P = self.P
        for cmd in (f"cd {P}/hooks; cat dispatch.py | head -3", f"ls {P}/lib | head -2; sysctl -n hw.ncpu",
                    f"wc -l {P}/lib/orkestra_core/*.py | tail -1; date", f"cat {P}/hooks/hooks.json | head; uname -a",
                    f"find {P}/lib -name '*.py' | wc -l", f"ls {P}/lib && sw_vers && ps -o pid= -p 1"):
            with self.subTest(cmd=cmd):
                d = decide(cmd)
                self.assertFalse(d.denied, f"{cmd!r} -> {d.guard} {d.reason[:80]}")

    def test_the_introspection_allowance_does_not_cover_writing_forms(self):
        P = self.P
        for cmd in (f"ls {P}/lib; sysctl -w kern.x=1", f"ls {P}/lib; date -s 2020-01-01", f"ls {P}/lib; sysctl kern.x=1"):
            with self.subTest(cmd=cmd):
                self.assertTrue(decide(cmd).denied, cmd)


class EscapedSpaceBeforeHashIsNoComment(unittest.TestCase):
    """GD-3: `echo x\\ #;cmd` is one word `x #` in bash and zsh; the commands after `;` run and must be judged."""

    def test_the_command_after_the_escaped_hash_is_still_checked(self):
        pusher = "git " + "push --force origin main"
        for tail, guard in ((pusher, "git"), ("npm publish", None), ("cat /w/proj/.env", None)):
            for head in ("echo x\\ #;", "echo x\\\t#;", "X=a\\ #;", "(echo x\\ #;", "{ echo x\\ #; "):
                cmd = head + tail
                with self.subTest(cmd=cmd):
                    d = decide(cmd)
                    self.assertTrue(d.denied, cmd)
                    if guard:
                        self.assertEqual(d.guard, guard)

    def test_a_real_comment_still_hides_its_line_and_only_its_line(self):
        pusher = "git " + "push --force origin main"
        self.assertFalse(decide("echo x #;" + pusher).denied)
        self.assertFalse(decide("echo x # " + pusher).denied)
        self.assertTrue(decide("echo x # fine\n" + pusher).denied)
        self.assertFalse(decide("echo a\\#b").denied)             # a `#` in the middle of a word was never a comment

    def test_the_parser_keeps_the_commands_after_it(self):
        from orkestra_core import shellparse as sp
        self.assertEqual([seg.program for seg in sp.segments("echo x\\ #;ls -la")], ["echo", "ls"])
        self.assertEqual([seg.program for seg in sp.segments("echo x #;ls -la")], ["echo"])


class VariableWriteTargetsCannotBeRedirected(unittest.TestCase):
    """GD-4: the temp-variable exemption and the literal-assignment tracking must not be fooled."""

    def test_these_writes_are_refused(self):
        for cmd in ("TMPDIR=/etc; echo x > $TMPDIR/probe", "echo x > $TMPDIR/../probe",
                    "echo x | tee $TMPDIR/../../probe", "cp calc.py $TMPDIR/../../probe",
                    "echo x > ${TMPDIR:-/etc}/probe", "echo x > ${TMPDIR:-/w}/probe",
                    "X=/tmp/ok; X=/etc; echo x > $X/probe", "X=/tmp/ok; X=$HOME; echo x > $X/.zshrc",
                    "X=/tmp/ok true; echo x > $X/probe", "(X=/tmp/ok); echo x > $X/probe",
                    "X=/tmp/ok; unset X; echo x > $X/probe", "X=/tmp/ok; read X; echo x > $X/probe",
                    "X=/tmp/ok; for X in /etc; do echo x > $X/probe; done",
                    "TMP=/etc; mkdir -p $TMP/probe"):
            with self.subTest(cmd=cmd):
                d = decide(cmd)
                self.assertTrue(d.denied and d.guard == "isolation", f"{cmd!r} -> {d.guard or 'allowed'}")

    def test_the_documented_temp_forms_are_still_allowed(self):
        for cmd in ("echo x > $TMPDIR/out", "mkdir -p $TMPDIR/x", "cp calc.py ${TMPDIR:-/tmp}/b.py",
                    "echo x > ${TMPDIR}/a", "X=/tmp/ok; echo x > $X/probe", "export X=/tmp/ok && echo x > $X/probe",
                    "echo x > ${TMPDIR:-/var/tmp}/o"):
            with self.subTest(cmd=cmd):
                d = decide(cmd)
                self.assertFalse(d.denied, f"{cmd!r} -> {d.guard} {d.reason[:80]}")

    def test_the_helpers(self):
        self.assertFalse(guards.is_temp_var_path("$TMPDIR/../x"))
        self.assertFalse(guards.is_temp_var_path("$TMPDIR/a/../x"))
        self.assertFalse(guards.is_temp_var_path("$TMPDIR/x", "TMPDIR=/etc; echo x > $TMPDIR/x"))
        self.assertTrue(guards.is_temp_var_path("$TMPDIR/x", "echo x > $TMPDIR/x"))
        self.assertEqual(guards.literal_assignments("X=/tmp/a; X=/tmp/b"), {})
        self.assertEqual(guards.literal_assignments("X=/tmp/a")["X"][1], "/tmp/a")
        self.assertEqual(guards.literal_assignments("X=/tmp/a cmd"), {})


if __name__ == "__main__":
    unittest.main()
