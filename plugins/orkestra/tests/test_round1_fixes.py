"""Regression tests for the round-1 audit fixes (lost activity rows, evidence kill-tree, guards, loops)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

import helpers
from helpers import LIB, Sandbox
from test_binding_model import Base
from test_engines import CursorBase, OTHER

from orkestra_core import activity, paths


class ActivityConcurrentAppend(Sandbox):
    """DL-01: a rotation must not drop rows appended while it rewrites the file."""

    def test_no_row_is_lost_while_rotating(self):
        proj = self.make_project()
        run = paths.run_dir(proj, "001-x")
        run.mkdir(parents=True)
        script = textwrap.dedent("""
            import sys
            sys.path.insert(0, %r)
            from pathlib import Path
            from orkestra_core import activity
            activity.MAX_BYTES, activity.KEEP_LINES = 20000, 60
            proj, who, n = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
            for j in range(n):
                activity.record(proj, "001-x", {}, "end", "M%%s-%%d" %% (who, j))
        """ % str(LIB))
        procs, n, k = [], 300, 6
        for i in range(k):
            procs.append(subprocess.Popen([sys.executable, "-c", script, str(proj), str(i), str(n)], env=dict(os.environ)))
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        seen = set()
        for f in run.glob("activity*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    seen.add(json.loads(line)["what"])
        self.assertEqual(len(seen), n * k, "rows lost: %d of %d" % (n * k - len(seen), n * k))


@unittest.skipIf(os.name == "nt", "POSIX process groups")
class EvidenceRunnerKillsTheTree(unittest.TestCase):
    """DL-02: evidence / tdd must not wait for a background descendant, and a timeout must not orphan the runner."""

    def test_a_background_descendant_does_not_hold_the_command_up(self):
        from orkestra_core import runs as R
        t = time.monotonic()
        cp = R._run_shell_or_list("sh -c 'sleep 11 & echo hi'", cwd=os.getcwd(), timeout=60, env=dict(os.environ))
        self.assertLess(time.monotonic() - t, 8.0)
        self.assertEqual(cp.returncode, 0)
        self.assertIn(b"hi", (cp.stdout or b"") + (cp.stderr or b""))

    def test_a_timeout_kills_the_whole_tree(self):
        from orkestra_core import runs as R
        marker = "sleep 37.%d" % (os.getpid() % 900 + 100)
        with self.assertRaises(subprocess.TimeoutExpired):
            R._run_shell_or_list(marker + "; echo done", cwd=os.getcwd(), timeout=1, env=dict(os.environ))
        time.sleep(0.3)
        left = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [], "orphaned runner survived the timeout")


class BindMarkerMustMatchTheCommandTarget(Base):
    """DL-04 + RD-3: a bind marker counts only for the project the orkestra call targeted; interpreter flags are fine."""

    def setUp(self):
        super().setUp()
        self.cli("init", "-p", "demo")

    def test_a_forged_marker_before_the_real_one_does_not_rebind_the_chat(self):
        self.start()
        other, other_rid, other_out = self.second_run()
        self.cli("pause", "-p", "demo")
        real = self.cli("resume", "-p", "demo").stdout
        self.sess_file().unlink()
        fake = next(ln for ln in other_out.splitlines() if ln.startswith("[orkestra:bind]"))
        self.post_bash("cat notes.txt; orkestra resume -p demo", fake + "\nresumed\n" + real)
        self.assertEqual(self.bound_run(), self.rid, "the chat must follow the run the command targeted")

    def test_interpreter_flags_do_not_hide_start_and_resume(self):
        from orkestra_core import hookmain
        for cmd, want in (("python3 -u /a/bin/orkestra start --title t", {"start"}),
                          ("python3 -B -X utf8 /a/bin/orkestra resume -p x", {"resume"}),
                          ("python3 /a/bin/orkestra start --title t", {"start"}),
                          ("python3 -c 'print(1)' /a/bin/orkestra start", set())):
            with self.subTest(cmd=cmd):
                self.assertEqual(hookmain.cli_keys(cmd), want)

    def test_a_flagged_interpreter_resume_binds(self):
        self.start()
        self.cli("pause", "-p", "demo")
        resumed = self.cli("resume", "-p", "demo").stdout
        self.sess_file().unlink()
        self.post_bash("python3 -u %s resume -p demo" % helpers.BIN, resumed)
        self.assertEqual(self.bound_run(), self.rid)


class OwnershipGuardSeesFlaggedInterpreters(Base):
    """RD-2: `python3 -u <orkestra> abort` is the same call as `python3 <orkestra> abort`."""

    def pre(self, command, sid="sess-intruder"):
        return self.hook("PreToolUse", {"session_id": sid, "tool_name": "Bash", "cwd": str(self.proj),
                                        "tool_input": {"command": command}})

    def test_flags_before_the_script_do_not_bypass_the_guard(self):
        self.start()
        for cmd in ("python3 %s abort stop -p demo", "python3 -u %s abort stop -p demo",
                    "python3 -B %s pause --reason x -p demo", "python3 -X utf8 -u %s pause -p demo"):
            with self.subTest(cmd=cmd):
                out = self.pre(cmd % helpers.BIN)
                self.assertTrue(self.denied(out), cmd)
                self.assertIn("[orkestra:roles]", out["hookSpecificOutput"]["permissionDecisionReason"])


@unittest.skipIf(os.name == "nt", "flock")
class SingletonSurvivesAProbe(Sandbox):
    """DL-05: the shared-lock probe of is_running() must not make a booting watcher give up."""

    def test_acquire_retries_while_a_probe_holds_the_shared_lock(self):
        import fcntl
        import threading
        from orkestra_core import fastwatch as F
        pid_file = Path(os.environ["ORKESTRA_HOME"]) / "watchdog.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        probe = open(pid_file, "a+")
        fcntl.flock(probe.fileno(), fcntl.LOCK_SH)
        timer = threading.Timer(0.04, lambda: (fcntl.flock(probe.fileno(), fcntl.LOCK_UN), probe.close()))
        timer.start()
        s = F.Singleton()
        try:
            self.assertTrue(s.acquire(), "a transient probe made the watcher give up for this boot")
        finally:
            timer.join()
            s.release()

    def test_a_real_holder_is_still_refused(self):
        import fcntl
        from orkestra_core import fastwatch as F
        pid_file = Path(os.environ["ORKESTRA_HOME"]) / "watchdog.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        holder = open(pid_file, "a+")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
        try:
            t = time.monotonic()
            self.assertFalse(F.Singleton().acquire())
            self.assertLess(time.monotonic() - t, 1.0)
        finally:
            holder.close()


class DeadlineDenialsAreCounted(Sandbox):
    """LT-2: a call that always exceeds the deadline must not get the same text forever."""

    def run_pre(self, patches, payload):
        import io
        from unittest import mock
        sys.path.insert(0, str(helpers.DISPATCH.parent))
        import dispatch as D
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["dispatch.py", "PreToolUse"]), \
                mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), mock.patch.object(sys, "stdout", out), \
                mock.patch.object(D, "utf8_stdio", lambda: None), mock.patch.object(D, "HOOK_CPU_SECONDS", 0.2), \
                mock.patch.object(D, "HOOK_WALL_SECONDS", 0.3), mock.patch.dict(D.HANDLERS, patches):
            D.main()
        return json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecisionReason"]

    def ev(self):
        return {"session_id": "s-deadline", "tool_name": "Bash", "tool_input": {"command": "echo hi"}}

    def test_a_cpu_bound_call_ends_in_do_not_retry(self):
        def spin(ev):
            end = time.monotonic() + 8
            while time.monotonic() < end:
                sum(range(5000))
        texts = [self.run_pre({"PreToolUse": spin}, self.ev()) for _ in range(4)]
        self.assertIn("[orkestra:complex]", texts[0])
        self.assertIn("Split the command", texts[0])
        self.assertIn("denied 2x", texts[1])
        self.assertIn("denied 3x: do not retry", texts[2])
        self.assertNotIn("Split the command", texts[2])

    def test_a_busy_machine_loop_ends_in_a_short_stop_but_never_blocks(self):
        texts = [self.run_pre({"PreToolUse": lambda ev: time.sleep(5)}, self.ev()) for _ in range(4)]
        self.assertIn("Retry the same call once", texts[0])
        self.assertIn("Retry the same call once", texts[1])
        for later in texts[2:]:
            self.assertIn("[orkestra:busy] denied", later)
            self.assertIn("Do not retry", later)


@unittest.skipIf(os.name == "nt", "flock")
class SessionStartSkipsTheFastwatchImport(Sandbox):
    """PERF-1: with a watcher already running, SessionStart must not import fastwatch (164 modules instead of 95)."""

    def probe(self, hold: bool) -> str:
        import fcntl
        home = Path(os.environ["ORKESTRA_HOME"])
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.json").write_text(json.dumps({"watchdog": {"autostart": False}}))      # never spawn a daemon here
        fh = open(home / "watchdog.pid", "a+")
        if hold:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        code = ("import sys\nsys.path.insert(0, %r)\nfrom orkestra_core import hookmain\n"
                "from unittest import mock\n"
                "with mock.patch('orkestra_core.hookmain.log'):\n"
                "    try:\n        hookmain._ensure_watcher(%r)\n    except Exception: pass\n"
                "print('orkestra_core.fastwatch' in sys.modules)\n" % (str(LIB), str(self.ws)))
        env = dict(os.environ)
        env.pop("ORKESTRA_NO_DAEMON", None)
        env["ORKESTRA_HOME"] = str(home)
        try:
            cp = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=60)
        finally:
            fh.close()
        return cp.stdout.strip()

    def test_a_running_watcher_is_detected_without_importing_fastwatch(self):
        self.assertEqual(self.probe(hold=True), "False")

    def test_without_a_watcher_fastwatch_is_still_asked(self):
        env_probe = self.probe(hold=False)
        self.assertEqual(env_probe, "True")


class LangDoesNotCopyTheEnvironment(unittest.TestCase):
    """PERF-3: lang() without ORKESTRA_LANG and config must not copy os.environ on every message."""

    def test_only_the_locale_variables_are_handed_to_the_os_lookup(self):
        import tempfile
        from unittest import mock
        from orkestra_core import i18n
        seen = []

        def spy(env, posix):
            seen.append(dict(posix))
            return "en"

        with tempfile.TemporaryDirectory() as td, \
                mock.patch.dict(os.environ, {"ORKESTRA_HOME": td, "LANG": "en_US.UTF-8", "SOME_OTHER_VAR": "x"}), \
                mock.patch.object(i18n, "_from_os", spy):
            os.environ.pop("ORKESTRA_LANG", None)
            self.assertEqual(i18n.lang(), "en")
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(set(seen[0]), set(i18n._LOCALE_VARS))
        self.assertEqual(seen[0].get("LANG"), "en_US.UTF-8")


class CursorUnboundChatStaysUnguarded(CursorBase):
    """RD-1: a Cursor chat with no run is not subject to the every-session guards just because a command says 'orkestra pause'."""

    def test_words_in_a_command_do_not_switch_the_guards_on(self):
        for cmd in ('git commit -m "resume orkestra notes"', "cat .env # orkestra pause", "echo orkestra abort > /dev/null"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.shell(cmd, conv=OTHER, gen="g-u"), {"permission": "allow"})

    def test_a_real_control_call_on_a_run_another_chat_drives_is_still_denied(self):
        self.start()
        out = self.shell("orkestra pause -p demo", conv=OTHER, gen="g-b")
        self.assertTrue(self.no(out), out)
        self.assertIn("[orkestra:roles]", out["user_message"])


class EveryRunGuardDenialEndsWithUnblock(Base):
    """RD-4: README promises an `unblock:` last line on every run-guard denial, also on the escalated texts."""

    def test_the_last_line_is_always_the_way_out(self):
        self.start()
        target = str(self.proj / "src" / "a.py")
        texts = []
        for _ in range(9):
            out = self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Write", "cwd": str(self.proj),
                                           "prompt_id": "p1", "tool_input": {"file_path": target, "content": "x"}})
            self.assertTrue(self.denied(out))
            texts.append(out["hookSpecificOutput"]["permissionDecisionReason"])
        self.assertIn("denied 2x", texts[1])
        self.assertIn("denied 3x", texts[2])
        self.assertIn("now blocked", texts[7])
        for i, text in enumerate(texts[:8]):
            with self.subTest(attempt=i + 1):
                self.assertEqual(text.splitlines()[-1][:8], "unblock:", text)


if __name__ == "__main__":
    unittest.main()
