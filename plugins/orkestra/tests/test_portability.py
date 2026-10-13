"""Portability: macOS, Linux and Windows, Python 3.9 to 3.14.

Windows cannot be run here, so its branches are simulated: `util._IS_WINDOWS` and `cursorkit._is_windows` are
patched (patching `os.name` itself would break pathlib), `msvcrt` and `fcntl` are replaced by fakes that record what
the code asked of them, and the hook/CLI launchers are run with fake `python3` / `python` / `py` commands on a PATH of
their own. Tests that need a POSIX shell are skipped on Windows; the static checks run everywhere.
"""
from __future__ import annotations

import ast
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from orkestra_core import copilotkit as COP
from orkestra_core import cursorkit as CK
from orkestra_core import guards, paths, util

PLUGIN = helpers.PLUGIN
ROOT = PLUGIN.parent.parent
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
RUN_HOOK = PLUGIN / "hooks" / "run_hook.sh"
BIN = PLUGIN / "bin" / "orkestra"
BIN_CMD = PLUGIN / "bin" / "orkestra.cmd"
SH = "/bin/sh" if os.path.exists("/bin/sh") else shutil.which("sh")
NEEDS_SH = unittest.skipUnless(os.name != "nt" and SH, "needs a POSIX shell (Windows runs the static checks only)")
TR_LETTERS = re.compile("[çğıöşüÇĞİÖŞÜ]")


class TmpCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="orkestra-port-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)


# ---------------------------------------------------------------- locking

class FakeMsvcrt:
    """Records where in the file every lock/unlock was asked for: msvcrt.locking works at the CURRENT position."""
    LK_LOCK = 1
    LK_UNLCK = 0

    def __init__(self):
        self.calls = []

    def locking(self, fd, mode, nbytes):
        self.calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))


class TestFileLock(TmpCase):
    def test_windows_lock_always_covers_byte_zero_new_or_existing_lock_file(self):
        fake = FakeMsvcrt()
        target = self.tmp / "state.json"
        with mock.patch.object(util, "fcntl", None), mock.patch.object(util, "msvcrt", fake):
            with util.file_lock(target):
                pass                                    # the lock file did not exist yet
            with util.file_lock(target):
                pass                                    # …and now it holds the one byte written for the first lock
        self.assertEqual(fake.calls, [(1, 0, 1), (0, 0, 1), (1, 0, 1), (0, 0, 1)])
        # a second process locking at offset 1 would not exclude the first one: that was the bug

    def test_unlock_is_skipped_when_the_lock_was_never_taken(self):
        class Boom(FakeMsvcrt):
            def locking(self, fd, mode, nbytes):
                super().locking(fd, mode, nbytes)
                if mode == self.LK_LOCK:
                    raise OSError(errno.EACCES, "denied")

        fake = Boom()
        with mock.patch.object(util, "fcntl", None), mock.patch.object(util, "msvcrt", fake):
            with self.assertRaises(OSError):
                with util.file_lock(self.tmp / "x"):
                    self.fail("must not run without the lock")
        self.assertEqual([c[0] for c in fake.calls], [1])           # no LK_UNLCK for a lock we do not hold

    def test_a_file_system_without_locking_continues_unlocked(self):
        for code in (errno.ENOLCK, errno.ENOSYS, errno.EINVAL):
            fake = mock.Mock()
            fake.LOCK_EX, fake.LOCK_UN = 2, 8
            fake.flock.side_effect = OSError(code, "no locks here")
            ran = []
            with mock.patch.object(util, "fcntl", fake):
                with util.file_lock(self.tmp / f"lock-{code}"):
                    ran.append(code)
            self.assertEqual(ran, [code])
            fake.flock.assert_called_once()                         # and no unlock of a lock we never had

    def test_other_lock_errors_are_not_swallowed(self):
        fake = mock.Mock()
        fake.LOCK_EX, fake.LOCK_UN = 2, 8
        fake.flock.side_effect = OSError(errno.EBADF, "bad descriptor")
        with mock.patch.object(util, "fcntl", fake), self.assertRaises(OSError):
            with util.file_lock(self.tmp / "x"):
                self.fail("must not run")

    def test_without_any_locking_module_it_is_a_noop(self):
        with mock.patch.object(util, "fcntl", None), mock.patch.object(util, "msvcrt", None):
            with util.file_lock(self.tmp / "none"):
                (self.tmp / "none-ran").write_text("x", encoding="utf-8")
        self.assertTrue((self.tmp / "none-ran").exists())

    @unittest.skipIf(util.fcntl is None, "POSIX advisory locks only")
    def test_real_posix_lock_excludes_a_second_holder(self):
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from orkestra_core import util; from pathlib import Path\n"
                "with util.file_lock(Path(sys.argv[2])):\n    print('held', flush=True); time.sleep(1.0)\n")
        target = self.tmp / "contended.json"
        proc = subprocess.Popen([sys.executable, "-c", code, str(helpers.LIB), str(target)], stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        started = __import__("time").monotonic()
        with util.file_lock(target):
            waited = __import__("time").monotonic() - started
        self.assertGreater(waited, 0.3, "the second holder got the lock while the first still held it")


# ---------------------------------------------------------------- atomic writes

class TestAtomicWrite(TmpCase):
    def leftovers(self):
        return [p.name for p in self.tmp.iterdir() if p.name.startswith(".tmp-")]

    def flaky_replace(self, failures):
        real = os.replace
        state = {"calls": 0}

        def replace(src, dst):
            state["calls"] += 1
            if state["calls"] <= failures:
                raise PermissionError(13, "The process cannot access the file because it is being used by another process")
            return real(src, dst)

        return replace, state

    def test_windows_retries_a_sharing_violation_until_the_reader_lets_go(self):
        target = self.tmp / "state.json"
        replace, state = self.flaky_replace(3)
        sleeps = []
        with mock.patch.object(util, "_IS_WINDOWS", True), mock.patch.object(util.os, "replace", replace), \
                mock.patch.object(util.time, "sleep", sleeps.append):
            util.write_text_atomic(target, "fresh")
        self.assertEqual(target.read_text(encoding="utf-8"), "fresh")
        self.assertEqual(state["calls"], 4)
        self.assertEqual(len(sleeps), 3)
        self.assertEqual(sleeps, sorted(sleeps), "back-off must not shrink")
        self.assertEqual(self.leftovers(), [])

    def test_windows_gives_up_after_six_attempts_and_cleans_its_temp_file(self):
        replace, state = self.flaky_replace(99)
        with mock.patch.object(util, "_IS_WINDOWS", True), mock.patch.object(util.os, "replace", replace), \
                mock.patch.object(util.time, "sleep"), self.assertRaises(PermissionError):
            util.write_text_atomic(self.tmp / "state.json", "x")
        self.assertEqual(state["calls"], 6)
        self.assertEqual(self.leftovers(), [])

    def test_posix_never_retries(self):
        replace, state = self.flaky_replace(99)
        with mock.patch.object(util, "_IS_WINDOWS", False), mock.patch.object(util.os, "replace", replace), \
                mock.patch.object(util.time, "sleep") as sleep, self.assertRaises(PermissionError):
            util.write_text_atomic(self.tmp / "state.json", "x")
        self.assertEqual(state["calls"], 1)
        sleep.assert_not_called()
        self.assertEqual(self.leftovers(), [])

    def test_text_is_written_with_lf_line_endings_on_every_os(self):
        seen = {}
        real = os.fdopen

        def spy(fd, *args, **kwargs):
            seen.update(kwargs)
            return real(fd, *args, **kwargs)

        with mock.patch.object(util.os, "fdopen", spy):
            util.write_text_atomic(self.tmp / "plan.md", "a\nb\n")
        self.assertEqual(seen.get("newline"), "\n", "text mode would write CRLF on Windows")
        self.assertEqual((self.tmp / "plan.md").read_bytes(), b"a\nb\n")

    def test_json_round_trips_non_ascii_text_whatever_the_locale(self):
        data = {"başlık": "Plan onayı", "ok": True}
        util.write_json_atomic(self.tmp / "d.json", data)
        self.assertEqual(util.read_json(self.tmp / "d.json"), data)
        self.assertIn("başlık".encode("utf-8"), (self.tmp / "d.json").read_bytes())

    def test_the_replace_helper_is_shared_by_the_hook_writers(self):
        for mod in (CK, COP):
            self.assertIs(mod.replace_file, util.replace_file)


# ---------------------------------------------------------------- the interpreter named in hook commands

class TestHookPython(TmpCase):
    def test_posix_prefers_the_base_python_of_a_virtualenv(self):
        base = self.tmp / "base"
        (base / "bin").mkdir(parents=True)
        (base / "bin" / "python3").write_text("", encoding="utf-8")
        with mock.patch.object(util, "_IS_WINDOWS", False), mock.patch.object(util.sys, "base_prefix", str(base)), \
                mock.patch.object(util.sys, "prefix", str(self.tmp / "venv")):
            self.assertEqual(util.hook_python(), str(base / "bin" / "python3"))

    def test_posix_without_a_venv_uses_python3_on_path_then_this_interpreter(self):
        with mock.patch.object(util, "_IS_WINDOWS", False), mock.patch.object(util.sys, "base_prefix", util.sys.prefix):
            with mock.patch.object(util.shutil, "which", return_value="/opt/x/python3"):
                self.assertEqual(util.hook_python(), "/opt/x/python3")
            with mock.patch.object(util.shutil, "which", return_value=None):
                self.assertEqual(util.hook_python(), sys.executable)

    def test_windows_never_trusts_python3_on_path_it_may_be_the_store_stub(self):
        stub = r"C:\Users\me\AppData\Local\Microsoft\WindowsApps\python3.exe"
        with mock.patch.object(util, "_IS_WINDOWS", True), mock.patch.object(util.sys, "base_prefix", util.sys.prefix), \
                mock.patch.object(util.shutil, "which", return_value=stub) as which:
            self.assertEqual(util.hook_python(), sys.executable)
            which.assert_not_called()

    def test_windows_virtualenv_names_the_base_python_exe(self):
        base = self.tmp / "Python312"
        base.mkdir()
        (base / "python.exe").write_text("", encoding="utf-8")
        with mock.patch.object(util, "_IS_WINDOWS", True), mock.patch.object(util.sys, "base_prefix", str(base)), \
                mock.patch.object(util.sys, "prefix", str(self.tmp / "venv")):
            self.assertEqual(util.hook_python(), str(base / "python.exe"))
        with mock.patch.object(util, "_IS_WINDOWS", True), mock.patch.object(util.sys, "base_prefix", str(self.tmp / "gone")), \
                mock.patch.object(util.sys, "prefix", str(self.tmp / "venv")):
            self.assertEqual(util.hook_python(), sys.executable)            # base not found: the running one

    def test_both_hook_writers_use_it_and_keep_the_quoted_command_shape(self):
        spaced = r"C:\Program Files\Python 3.12\python.exe"
        for mod, script in ((CK, CK.HOOK_SCRIPT), (COP, COP.HOOK_SCRIPT)):
            with mock.patch.object(mod, "hook_python", return_value=spaced):
                self.assertEqual(mod._python(), spaced)
                cmd = mod.hook_command("preToolUse")
            self.assertEqual(cmd, f'"{spaced}" "{script}" preToolUse')
            self.assertTrue(re.match(r'^"([^"]+)" "([^"]+)" (\S+)$', cmd), "the re-install matcher must still parse it")


# ---------------------------------------------------------------- PATH shim on Windows and POSIX

class TestPathShim(TmpCase):
    def setUp(self):
        super().setUp()
        self.shim_dir = self.tmp / "bin"
        self.shim_dir.mkdir()
        patcher = mock.patch.dict(os.environ, {"ORKESTRA_SHIM_DIR": str(self.shim_dir)})
        patcher.start()
        self.addCleanup(patcher.stop)
        off_path = mock.patch.object(CK, "_orkestra_on_path", return_value=False)
        off_path.start()
        self.addCleanup(off_path.stop)

    def windows(self):
        return mock.patch.object(CK, "_is_windows", return_value=True)

    def test_cmd_shim_is_crlf_and_names_the_python_and_the_launcher(self):
        with self.windows():
            CK.ensure_path_shim()
        raw = (self.shim_dir / "orkestra.cmd").read_bytes()
        self.assertTrue(raw.startswith(b"@echo off\r\n"))
        self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "a bare LF in a batch file")
        text = raw.decode("utf-8")
        self.assertIn(sys.executable, text)
        self.assertIn(str(CK.BIN), text)
        self.assertTrue(text.rstrip().endswith("%*"))

    def test_a_stale_shim_of_ours_is_renewed_and_a_foreign_one_is_left_alone(self):
        cmd = self.shim_dir / "orkestra.cmd"
        with self.windows():
            cmd.write_bytes(b'@echo off\r\n"C:\\Old\\Python311\\python.exe" "C:\\old\\orkestra\\bin\\orkestra" %*\r\n')
            done = CK.ensure_path_shim()
            self.assertTrue(done and "orkestra.cmd" in done[0])
            self.assertIn(sys.executable, cmd.read_text(encoding="utf-8"))
            self.assertEqual(CK.ensure_path_shim(), [], "second run changes nothing")
            cmd.write_bytes(b"@echo off\r\necho my own wrapper\r\n")
            self.assertEqual(CK.ensure_path_shim(), [])
            self.assertIn("my own wrapper", cmd.read_text(encoding="utf-8"))

    def test_dry_run_writes_nothing_on_windows(self):
        with self.windows():
            done = CK.ensure_path_shim(dry=True)
        self.assertTrue(done)
        self.assertEqual(list(self.shim_dir.iterdir()), [])

    def test_windows_default_shim_dir_is_the_per_user_windowsapps_folder(self):
        apps = self.tmp / "Local" / "Microsoft" / "WindowsApps"
        apps.mkdir(parents=True)
        env = {k: v for k, v in os.environ.items() if k != "ORKESTRA_SHIM_DIR"}
        env["LOCALAPPDATA"] = str(self.tmp / "Local")
        with mock.patch.dict(os.environ, env, clear=True):
            with self.windows():
                self.assertEqual(CK.shim_path(), apps / "orkestra")
            self.assertEqual(CK.shim_path(), Path("~/.local/bin").expanduser() / "orkestra")      # POSIX unchanged
            os.environ["ORKESTRA_SHIM_DIR"] = str(self.shim_dir)
            with self.windows():
                self.assertEqual(CK.shim_path(), self.shim_dir / "orkestra")                      # the override wins
        with mock.patch.dict(os.environ, {**env, "LOCALAPPDATA": str(self.tmp / "nowhere")}, clear=True):
            with self.windows():
                self.assertEqual(CK.shim_path(), Path("~/.local/bin").expanduser() / "orkestra")  # folder missing: fallback

    def test_uninstall_removes_our_cmd_shim_and_shell_wrapper_but_nothing_foreign(self):
        (self.shim_dir / "orkestra.cmd").write_bytes(CK._windows_cmd_body().encode("utf-8"))
        (self.shim_dir / "orkestra").write_text(f"#!/usr/bin/env bash\nexec '{sys.executable}' '{CK.BIN}' \"$@\"\n",
                                                encoding="utf-8")
        with mock.patch.object(CK, "cursor_home", return_value=self.tmp / "no-cursor"):
            dry = CK.uninstall(dry=True)
            self.assertEqual(sorted(p.name for p in self.shim_dir.iterdir()), ["orkestra", "orkestra.cmd"])
            self.assertEqual(len(dry), 2)
            CK.uninstall()
        self.assertEqual(list(self.shim_dir.iterdir()), [])
        # a script of somebody else's named orkestra, and a symlink to another tool, survive
        (self.shim_dir / "orkestra").write_text("#!/bin/sh\necho somebody else's tool\n", encoding="utf-8")
        (self.shim_dir / "orkestra.cmd").write_bytes(b"@echo off\r\necho unrelated\r\n")
        with mock.patch.object(CK, "cursor_home", return_value=self.tmp / "no-cursor"):
            self.assertEqual(CK.uninstall(), [])
        self.assertEqual(sorted(p.name for p in self.shim_dir.iterdir()), ["orkestra", "orkestra.cmd"])

    def test_uninstalling_one_checkout_keeps_the_cmd_shim_of_another(self):
        """The stable checkout's wrapper must survive removing a development copy: only a wrapper that names THIS
        checkout's launcher is ours to delete (it used to be any small file that mentions orkestra and `%*`)."""
        cmd = self.shim_dir / "orkestra.cmd"
        foreign = b'@echo off\r\n"D:\\py\\python.exe" "D:\\work\\stable-checkout\\plugins\\orkestra\\bin\\orkestra" %*\r\n'
        cmd.write_bytes(foreign)
        with mock.patch.object(CK, "cursor_home", return_value=self.tmp / "no-cursor"):
            self.assertEqual(CK.uninstall(dry=True), [])
            self.assertEqual(CK.uninstall(), [])
        self.assertEqual(cmd.read_bytes(), foreign)
        cmd.write_bytes(CK._windows_cmd_body().encode("utf-8"))                   # this checkout's own wrapper: removed
        with mock.patch.object(CK, "cursor_home", return_value=self.tmp / "no-cursor"):
            self.assertEqual(len(CK.uninstall()), 1)
        self.assertFalse(cmd.exists())

    @unittest.skipIf(os.name == "nt", "symlinks")
    def test_symlink_shim_still_points_at_the_launcher_and_uninstall_removes_only_that(self):
        done = CK.ensure_path_shim()
        self.assertTrue(done and (self.shim_dir / "orkestra").is_symlink())
        self.assertEqual(os.path.realpath(self.shim_dir / "orkestra"), os.path.realpath(CK.BIN))
        other = self.tmp / "other-tool"
        other.write_text("#!/bin/sh\n", encoding="utf-8")
        with mock.patch.object(CK, "cursor_home", return_value=self.tmp / "no-cursor"):
            CK.uninstall()
            self.assertFalse(os.path.lexists(self.shim_dir / "orkestra"))
            os.symlink(other, self.shim_dir / "orkestra")
            self.assertEqual(CK.uninstall(), [])
        self.assertTrue((self.shim_dir / "orkestra").is_symlink())


class TestGeneratedFilesKeepLf(TmpCase):
    def test_hooks_json_is_written_with_lf_and_replace_file(self):
        hp = self.tmp / "hooks.json"
        calls = []
        real = CK.replace_file
        with mock.patch.object(CK, "replace_file", lambda a, b: (calls.append((a, b)), real(a, b))[1]):
            CK._write_hooks(hp, {"version": 1, "hooks": {}}, [], False)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(b"\r", hp.read_bytes())
        self.assertEqual(json.loads(hp.read_text(encoding="utf-8")), {"version": 1, "hooks": {}})
        calls.clear()
        real_cop = COP.replace_file
        with mock.patch.object(COP, "replace_file", lambda a, b: (calls.append((a, b)), real_cop(a, b))[1]):
            COP._write_hooks(self.tmp / "c.json", {"version": 1, "hooks": {}}, [], False)
        self.assertEqual(len(calls), 1)
        self.assertNotIn(b"\r", (self.tmp / "c.json").read_bytes())


# ---------------------------------------------------------------- case-insensitive file systems

class TestCaseInsensitivePaths(TmpCase):
    def case_insensitive(self) -> bool:
        probe = self.tmp / "CaseProbe"
        probe.mkdir()
        return (self.tmp / "caseprobe").exists()

    def test_same_path_honours_the_file_system(self):
        insensitive = self.case_insensitive()
        self.assertEqual(paths._same_path(self.tmp / "CaseProbe", self.tmp / "caseprobe"), insensitive)
        self.assertTrue(paths._same_path(self.tmp / "CaseProbe", self.tmp / "CaseProbe"))
        self.assertFalse(paths._same_path(self.tmp / "CaseProbe", self.tmp / "Other"))
        self.assertFalse(paths._same_path(self.tmp / "CaseProbe", self.tmp / "CaseProbe" / "sub"))

    def test_the_workspace_root_is_not_a_project_even_when_spelled_in_another_case(self):
        insensitive = self.case_insensitive()
        ws = self.tmp / "Projects"
        (ws / ".orkestra").mkdir(parents=True)
        (ws / "app").mkdir()
        with mock.patch.dict(os.environ, {"ORKESTRA_WORKSPACE": str(self.tmp / "projects")}):
            found = paths.find_project_root(ws / "app")
        if insensitive:
            self.assertIsNone(found, "Projects/ and projects/ are one directory here: the workspace is never a project")
        else:
            self.assertEqual(found, ws)                     # two different directories on a case-sensitive disk

    def test_exact_spelling_still_excludes_the_workspace(self):
        ws = self.tmp / "Projects"
        (ws / ".orkestra").mkdir(parents=True)
        (ws / "app").mkdir()
        with mock.patch.dict(os.environ, {"ORKESTRA_WORKSPACE": str(ws)}):
            self.assertIsNone(paths.find_project_root(ws / "app"))


# ---------------------------------------------------------------- temp directories

class TestTempDirectories(unittest.TestCase):
    def test_the_platform_temp_dir_counts_as_temp_for_the_isolation_guard(self):
        probe = os.path.join(tempfile.gettempdir(), "orkestra-scratch", "x.txt")
        self.assertTrue(guards._is_temp(probe), probe)
        self.assertTrue(guards._is_temp_subdir(os.path.join(tempfile.gettempdir(), "orkestra-scratch")))

    def test_windows_temp_roots_come_from_the_environment_in_any_spelling(self):
        env = {"TEMP": r"C:\Users\Me\AppData\Local\Temp", "TMP": "", "LOCALAPPDATA": r"C:\Users\Me\AppData\Local"}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertTrue(guards._is_temp(r"c:\USERS\me\appdata\local\temp\pytest-1\a.txt"))
            self.assertTrue(guards._is_temp("C:/Users/Me/AppData/Local/Temp/a.txt"))
            self.assertFalse(guards._is_temp(r"C:\Users\Me\Projects\app\a.txt"))

    def test_a_project_directory_is_never_temp(self):
        self.assertFalse(guards._is_temp(os.path.join(os.path.expanduser("~"), "Projects", "app", "a.txt")))


# ---------------------------------------------------------------- hook launcher (hooks.json -> run_hook.sh)

def write_script(path: Path, body: str) -> Path:
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class LauncherCase(TmpCase):
    """A directory of fake interpreters that is the ONLY thing on PATH."""

    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "fakebin"
        self.fake.mkdir()

    def interpreter(self, name, *, ok=True, code=0):
        """`name -c ...` is the version probe (logged, exit 0 when ok); anything else reports its arguments, one
        bracket pair each, and the first line of stdin."""
        body = (f'case "$1" in -c) echo "probe {name}" >> "$FAKE_LOG"; exit {0 if ok else 1};; esac\n'
                f'read -r line\nprintf \'{name}\'; for a in "$@"; do printf \' [%s]\' "$a"; done\n'
                f'printf \' stdin=%s\\n\' "$line"\nexit {code}')
        return write_script(self.fake / name, body)

    def probes(self):
        log = self.tmp / "calls.log"
        return log.read_text(encoding="utf-8").split() if log.exists() else []

    def stub(self, name):
        """The Windows Store alias: found by name, fails when it runs."""
        return write_script(self.fake / name, "echo 'Python was not found; run without arguments to install' >&2\nexit 49")

    def env(self, **extra):
        base = {"PATH": str(self.fake), "HOME": str(self.tmp), "ORKESTRA_HOME": str(self.tmp / "oh"),
                "FAKE_LOG": str(self.tmp / "calls.log"),
                "ORKESTRA_NO_DAEMON": "1"}              # a SessionStart hook must not leave a real watcher behind
        base.update(extra)
        return base

    def run_sh(self, script, *args, env=None, stdin="payload"):
        return subprocess.run([SH, str(script), *args], input=stdin + "\n", capture_output=True, text=True,
                              env=env if env is not None else self.env(), timeout=60)


class TestHooksJson(unittest.TestCase):
    def test_hooks_json_goes_through_the_launcher_for_all_six_events(self):
        doc = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
        self.assertEqual(sorted(doc["hooks"]), ["Notification", "PostToolUse", "PostToolUseFailure", "PreToolUse",
                                                "SessionEnd", "SessionStart", "Stop", "StopFailure", "SubagentStop",
                                                "UserPromptSubmit"])
        for event, groups in doc["hooks"].items():
            for group in groups:
                for hook in group["hooks"]:
                    # POSIX with python3: exec it directly (no second shell, 3 ms); the launcher stays the Windows /
                    # no-python3 path (changed on purpose by the hook start-up work)
                    self.assertEqual(
                        hook["command"],
                        'if [ "${OS:-}" != "Windows_NT" ] && [ -z "${MSYSTEM:-}" ] && command -v python3 >/dev/null 2>&1; '
                        'then exec python3 "${CLAUDE_PLUGIN_ROOT}/hooks/dispatch.py" ' + event + '; fi; '
                        'exec sh "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh" "${CLAUDE_PLUGIN_ROOT}/hooks/dispatch.py" ' + event)
                    self.assertEqual(hook["timeout"], 15)
        self.assertTrue(RUN_HOOK.is_file() and (PLUGIN / "hooks" / "dispatch.py").is_file())

    def test_the_launcher_is_plain_ascii_apart_from_its_turkish_message(self):
        raw = RUN_HOOK.read_bytes()
        self.assertNotIn(b"\r", raw, "CRLF would break `#!/bin/sh` on POSIX")
        raw.decode("utf-8")

    def test_git_keeps_the_shell_launchers_lf_and_the_batch_launcher_crlf_on_every_checkout(self):
        """Git for Windows checks out with CRLF by default: `sh` would choke on a CR, `cmd.exe` on a bare LF."""
        hooks = (PLUGIN / "hooks" / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(hooks, r"(?m)^run_hook\.sh text eol=lf$")
        bin_attrs = (PLUGIN / "bin" / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(bin_attrs, r"(?m)^orkestra text eol=lf$")
        self.assertRegex(bin_attrs, r"(?m)^orkestra\.cmd text eol=crlf$")
        self.assertNotIn(b"\r", BIN.read_bytes())


@NEEDS_SH
class TestHookLauncher(LauncherCase):
    def run_hook(self, *args, **kw):
        return self.run_sh(RUN_HOOK, *args, **kw)

    def test_python3_is_used_at_once_without_a_probe_on_macos_and_linux(self):
        self.interpreter("python3")
        self.interpreter("python")
        cp = self.run_hook("/x/dispatch.py", "Stop")
        self.assertEqual(cp.stdout.strip(), "python3 [/x/dispatch.py] [Stop] stdin=payload")      # stdin reaches the hook
        self.assertEqual(self.probes(), [], "no extra interpreter start on POSIX")

    def test_the_exit_code_of_the_hook_passes_through(self):
        for code in (0, 2, 5):
            self.interpreter("python3", code=code)
            self.assertEqual(self.run_hook("/x/dispatch.py", "Stop").returncode, code)

    def test_arguments_with_spaces_stay_one_argument(self):
        self.interpreter("python3")
        cp = self.run_hook("/My Plugin/hooks/dispatch.py", "Pre Tool")
        self.assertIn("[/My Plugin/hooks/dispatch.py] [Pre Tool]", cp.stdout)

    def test_without_python3_python_is_probed_and_used(self):
        self.interpreter("python")
        cp = self.run_hook("/x/dispatch.py", "Stop")
        self.assertEqual(self.probes(), ["probe", "python"])
        self.assertEqual(cp.stdout.strip(), "python [/x/dispatch.py] [Stop] stdin=payload")

    def test_an_old_python_is_skipped_then_the_py_launcher_runs_with_dash_3(self):
        self.interpreter("python", ok=False)
        write_script(self.fake / "py", 'case "$2" in -c) exit 0;; esac\nread -r line\necho "py $*"')
        cp = self.run_hook("/x/dispatch.py", "Stop")
        self.assertEqual(cp.stdout.strip(), "py -3 /x/dispatch.py Stop")

    def test_on_windows_the_store_stub_is_skipped(self):
        self.stub("python3")
        self.interpreter("python")
        for marker in ({"OS": "Windows_NT"}, {"MSYSTEM": "MINGW64"}):
            cp = self.run_hook("/x/dispatch.py", "Stop", env=self.env(**marker))
            self.assertEqual(cp.stdout.strip(), "python [/x/dispatch.py] [Stop] stdin=payload", marker)

    def test_on_windows_a_working_python3_is_probed_first_and_used(self):
        self.interpreter("python3")
        cp = self.run_hook("/x/dispatch.py", "Stop", env=self.env(OS="Windows_NT"))
        self.assertEqual(self.probes(), ["probe", "python3"])
        self.assertTrue(cp.stdout.startswith("python3 [/x/dispatch.py]"))

    def test_no_python_at_all_says_so_in_the_users_language_and_exits_127(self):
        en = self.run_hook("/x/dispatch.py", "Stop", env=self.env(ORKESTRA_LANG="en"))
        self.assertEqual(en.returncode, 127)
        self.assertIn("no Python 3.9+ found", en.stderr)
        self.assertNotRegex(en.stderr, TR_LETTERS)
        tr = self.run_hook("/x/dispatch.py", "Stop", env=self.env(ORKESTRA_LANG="tr"))
        self.assertEqual(tr.returncode, 127)
        self.assertRegex(tr.stderr, "bulunamadı")
        auto = self.run_hook("/x/dispatch.py", "Stop", env=self.env(LANG="tr_TR.UTF-8"))
        self.assertRegex(auto.stderr, "bulunamadı")
        for text in (en.stderr, tr.stderr):
            self.assertNotIn("\u2014", text, "no em dash in user-visible text")

    def message_language(self, home_config=None, **env):
        """"tr" or "en": the language of the no-Python message under this environment (only shell builtins are on PATH)."""
        oh = self.tmp / "oh"
        if home_config is None:
            if (oh / "config.json").exists():
                (oh / "config.json").unlink()
        else:
            oh.mkdir(exist_ok=True)
            (oh / "config.json").write_bytes(home_config)
        cp = self.run_hook("/x/dispatch.py", "Stop", env=self.env(**env))
        self.assertEqual(cp.returncode, 127, cp.stderr)
        return "tr" if "bulunamadı" in cp.stderr else "en"

    def test_the_no_python_message_picks_its_language_like_the_python_side_does(self):
        """Same order as i18n.lang(): ORKESTRA_LANG, config.json, LC_ALL / LC_MESSAGES / LANG / LANGUAGE, English."""
        table = [
            (dict(ORKESTRA_LANG="auto", LANG="tr_TR.UTF-8"), "tr"),         # `auto` means: look further
            (dict(ORKESTRA_LANG="turkish"), "tr"), (dict(ORKESTRA_LANG="Turkce"), "tr"),
            (dict(ORKESTRA_LANG="Türkçe"), "tr"), (dict(ORKESTRA_LANG="TR"), "tr"),
            (dict(ORKESTRA_LANG="tr_TR.UTF-8"), "tr"),
            (dict(ORKESTRA_LANG="english", LANG="tr_TR.UTF-8"), "en"), (dict(ORKESTRA_LANG="EN", LC_ALL="tr_TR"), "en"),
            (dict(ORKESTRA_LANG="TRUE"), "en"), (dict(ORKESTRA_LANG="trx", LANG="tr_TR"), "en"),
            (dict(ORKESTRA_LANG="de", LANG="tr_TR.UTF-8"), "en"),           # explicit but unsupported: never falls through
            (dict(LANG="tr.UTF-8"), "tr"), (dict(LANG="tr"), "tr"), (dict(LC_MESSAGES="tr_TR.UTF-8"), "tr"),
            (dict(LANGUAGE="tr"), "tr"), (dict(LANGUAGE="de:tr:en"), "tr"), (dict(LANGUAGE="de:fr"), "en"),
            (dict(LC_ALL="C", LANG="tr_TR.UTF-8"), "tr"),                   # C / POSIX say nothing
            (dict(LC_ALL="en_US.UTF-8", LANG="tr_TR.UTF-8"), "en"), (dict(LANG="trx_TR"), "en"), (dict(), "en"),
        ]
        for env, want in table:
            with self.subTest(env=env):
                self.assertEqual(self.message_language(**env), want)

    def test_the_no_python_message_reads_the_language_from_config_json(self):
        cases = [
            (b'{"lang": "tr"}', {}, "tr"), (b'{\n  "lang":"tr",\n  "x": 1\n}\n', {}, "tr"),
            (b'\xef\xbb\xbf{"lang": "tr"}', {}, "tr"),                     # a BOM (Windows editors) is tolerated
            (b'{"x": 1, "lang": "tr_TR.UTF-8"}', {}, "tr"), (b'{"lang": "en"}', dict(LANG="tr_TR"), "en"),
            (b'{"lang": "auto"}', dict(LANG="tr_TR"), "tr"), (b'{"lang": "de"}', dict(LANG="tr_TR"), "tr"),
            (b'{"lang": 5}', dict(LANG="tr_TR"), "tr"), (b'not json', dict(LANG="tr_TR"), "tr"), (b'', dict(LANG="tr_TR"), "tr"),
            (b'{"lang": "tr"}', dict(ORKESTRA_LANG="en"), "en"),            # the environment beats the file
            (b'{"lang": "tr"}', dict(ORKESTRA_LANG="auto"), "tr"),
        ]
        for config, env, want in cases:
            with self.subTest(config=config, env=env):
                self.assertEqual(self.message_language(home_config=config, **env), want)

    def test_a_tilde_in_orkestra_home_is_expanded_like_the_python_side_does(self):
        (self.tmp / ".orkx").mkdir()
        (self.tmp / ".orkx" / "config.json").write_text('{"lang": "tr"}', encoding="utf-8")
        self.assertEqual(self.message_language(ORKESTRA_HOME="~/.orkx"), "tr")

    def test_a_missing_script_argument_is_a_usage_error_not_a_crash(self):
        cp = self.run_hook()
        self.assertEqual(cp.returncode, 64)
        self.assertIn("usage", cp.stderr)

    def test_end_to_end_the_real_dispatcher_denies_through_the_launcher(self):
        write_script(self.fake / "python3", f'exec "{sys.executable}" "$@"')
        dangerous = "git push" + " --force origin main"
        payload = json.dumps({"hook_event_name": "PreToolUse", "session_id": "port", "tool_name": "Bash",
                              "tool_input": {"command": dangerous}, "cwd": str(self.tmp)})
        env = self.env(CLAUDE_PLUGIN_ROOT=str(PLUGIN), ORKESTRA_WORKSPACE=str(self.tmp / "ws"), ORKESTRA_LANG="en")
        cp = subprocess.run([SH, str(RUN_HOOK), str(PLUGIN / "hooks" / "dispatch.py"), "PreToolUse"], input=payload,
                            capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(json.loads(cp.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_end_to_end_with_only_python_on_path_like_a_windows_install(self):
        write_script(self.fake / "python", f'exec "{sys.executable}" "$@"')
        self.stub("python3")
        payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "port", "cwd": str(self.tmp)})
        env = self.env(OS="Windows_NT", CLAUDE_PLUGIN_ROOT=str(PLUGIN), ORKESTRA_WORKSPACE=str(self.tmp / "ws"))
        cp = subprocess.run([SH, str(RUN_HOOK), str(PLUGIN / "hooks" / "dispatch.py"), "SessionStart"], input=payload,
                            capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_the_launcher_has_no_bashisms(self):
        for sh in dict.fromkeys(filter(None, ("/bin/sh", shutil.which("dash"), shutil.which("bash")))):
            cp = subprocess.run([sh, "-n", str(RUN_HOOK)], capture_output=True, text=True)
            self.assertEqual((cp.returncode, cp.stderr), (0, ""), sh)
        text = RUN_HOOK.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        for bashism in ("[[", "function ", "local ", "<<<", "$'"):
            self.assertNotIn(bashism, text)


# ---------------------------------------------------------------- CLI launcher (bin/orkestra, bin/orkestra.cmd)

@NEEDS_SH
class TestCliLauncher(LauncherCase):
    def cli(self, *args, env=None, via_sh=True):
        argv = ([SH] if via_sh else []) + [str(BIN), *args]
        return subprocess.run(argv, capture_output=True, text=True, env=env if env is not None else self.env(), timeout=60)

    def real_python(self, name):
        return write_script(self.fake / name, f'exec "{sys.executable}" "$@"')

    def test_it_is_valid_python_and_valid_sh_at_once(self):
        text = BIN.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!/bin/sh\n"))
        ast.parse(text, filename=str(BIN), feature_version=(3, 8))
        self.real_python("python3")
        # `sh -n` cannot be used (it parses the Python half too); run the file under every shell this machine has
        for sh in dict.fromkeys(filter(None, ("/bin/sh", shutil.which("dash"), shutil.which("bash")))):
            cp = subprocess.run([sh, str(BIN), "version"], capture_output=True, text=True, env=self.env(), timeout=60)
            self.assertEqual(cp.returncode, 0, f"{sh}: {cp.stderr}")
            self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+", sh)

    def test_python_reads_it_as_before(self):
        cp = subprocess.run([sys.executable, str(BIN), "version"], capture_output=True, text=True, env=self.env(), timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+\.\d+")

    def test_sh_finds_python3_on_path(self):
        self.real_python("python3")
        cp = self.cli("version")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+")

    def test_with_only_python_on_path_it_still_runs(self):
        self.real_python("python")
        cp = self.cli("version")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+")

    def test_a_python2_style_python_is_skipped_in_favour_of_the_py_launcher(self):
        write_script(self.fake / "python", "exit 1")
        write_script(self.fake / "py", f'shift\nexec "{sys.executable}" "$@"')
        cp = self.cli("version")
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+")

    def test_windows_store_stub_is_skipped(self):
        self.stub("python3")
        self.real_python("python")
        cp = self.cli("version", env=self.env(OS="Windows_NT"))
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+")

    def test_no_python_exits_127_with_a_message(self):
        cp = self.cli("version")
        self.assertEqual(cp.returncode, 127)
        self.assertIn("no Python 3.9+ found", cp.stderr)

    def test_through_the_path_shim_symlink_the_plugin_is_still_found(self):
        self.real_python("python3")
        shim = self.tmp / "shim"
        shim.mkdir()
        os.symlink(BIN, shim / "orkestra")
        cp = subprocess.run([str(shim / "orkestra"), "version"], capture_output=True, text=True, env=self.env(), timeout=60)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertRegex(cp.stdout, r"^orkestra \d+\.\d+")

    def test_exit_code_and_arguments_pass_through(self):
        self.real_python("python3")
        cp = self.cli("definitely-not-a-command")
        self.assertEqual(cp.returncode, 2)

    def test_turkish_output_survives_a_pure_ascii_console(self):
        """A legacy code page must not turn `Kullanım` into a UnicodeEncodeError (PYTHONIOENCODING=ascii)."""
        self.real_python("python3")
        env = self.env(ORKESTRA_LANG="tr", PYTHONIOENCODING="ascii")
        cp = self.cli("--help", env=env)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertNotIn("UnicodeEncodeError", cp.stderr)
        self.assertTrue(cp.stdout.strip(), "help text expected")


class TestWindowsCmdLauncher(unittest.TestCase):
    def setUp(self):
        self.raw = BIN_CMD.read_bytes()
        self.text = self.raw.decode("ascii")                    # ASCII only: any OEM code page reads it the same

    def test_every_line_ends_with_crlf(self):
        self.assertTrue(self.raw.endswith(b"\r\n"))
        self.assertNotIn(b"\n", self.raw.replace(b"\r\n", b""), "LF-only batch files break labels and blocks in cmd.exe")

    def test_it_probes_py_python_and_python3_for_a_working_python_39(self):
        self.assertTrue(self.text.startswith("@echo off"))
        for needle in ('"py -3"', "python python3", "sys.version_info >= (3, 9)", "%~dp0orkestra", "%*", "exit /b 127"):
            self.assertIn(needle, self.text)
        self.assertIn('-c "import sys', self.text)
        self.assertIn(">nul 2>nul", self.text, "a failing candidate must be silent")

    def test_the_exit_code_is_kept_without_expanding_errorlevel_inside_the_block(self):
        self.assertNotIn("%errorlevel%", self.text.lower(), "expanded when the block is parsed, i.e. before the command ran")
        self.assertRegex(self.text, r"\n\s+exit /b\r\n")

    def test_parentheses_inside_the_block_are_balanced(self):
        block = self.text[self.text.index("do ("):self.text.index("echo orkestra: no Python")]
        unquoted = re.sub(r'"[^"]*"', "", block)
        self.assertEqual(unquoted.count("("), unquoted.count(")"))

    def test_the_message_is_english_and_free_of_em_dashes(self):
        self.assertNotIn("\u2014", self.text)
        self.assertIn("no Python 3.9+ found", self.text)


# ---------------------------------------------------------------- Python 3.9 .. 3.14

class TestPythonVersionCompatibility(unittest.TestCase):
    def sources(self):
        roots = [PLUGIN / "lib", PLUGIN / "hooks", ROOT / "bridge"]
        out = [p for r in roots for p in sorted(r.rglob("*.py"))
               if "__pycache__" not in p.parts and "tests" not in p.parts and "node_modules" not in p.parts]
        return out + [BIN, ROOT / "install.py"]

    def test_everything_parses_as_python_38_syntax(self):
        """3.8 syntax is the strict superset check: no match statements, no parenthesised multi-item `with`."""
        bad = []
        for path in self.sources():
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 8))
            except SyntaxError as exc:
                bad.append(f"{path.relative_to(ROOT)}: {exc}")
        self.assertEqual(bad, [])

    def test_no_python_310_plus_apis_in_the_runtime_code(self):
        """Names that exist only from 3.10: they would fail on the 3.9 the CI matrix and macOS still ship."""
        banned_attrs = {"pairwise", "bit_count", "hardlink_to", "is_junction", "isjunction", "stdlib_module_names",
                        "TaskGroup"}
        banned_names = {"aiter", "anext", "tomllib", "ExceptionGroup", "TypeAlias", "ParamSpec", "Self", "StrEnum"}
        bad = []
        for path in self.sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                where = f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', '?')}"
                if isinstance(node, ast.Attribute) and node.attr in banned_attrs:
                    bad.append(f"{where} .{node.attr}")
                elif isinstance(node, ast.Attribute) and node.attr == "chdir" and getattr(node.value, "id", "") == "contextlib":
                    bad.append(f"{where} contextlib.chdir")
                elif isinstance(node, ast.Name) and node.id in banned_names:
                    bad.append(f"{where} {node.id}")
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        mod = (getattr(node, "module", None) or "") + "." + alias.name
                        if "tomllib" in mod or mod.endswith((".TypeAlias", ".ParamSpec", ".Self", ".StrEnum")):
                            bad.append(f"{where} import {mod}")
                elif isinstance(node, ast.Call):
                    fn = node.func
                    name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                    kwargs = {k.arg for k in node.keywords}
                    if name == "zip" and "strict" in kwargs:
                        bad.append(f"{where} zip(strict=)")
                    if name == "dataclass" and kwargs & {"slots", "kw_only", "match_args"}:
                        bad.append(f"{where} dataclass(slots/kw_only=)")
                    if name in ("read_text", "write_text") and "newline" in kwargs:
                        bad.append(f"{where} Path.{name}(newline=) is 3.10+/3.13+")
                    if name in ("isinstance", "issubclass") and len(node.args) == 2 \
                            and isinstance(node.args[1], ast.BinOp) and isinstance(node.args[1].op, ast.BitOr):
                        bad.append(f"{where} runtime X | Y union")
        self.assertEqual(bad, [])

    def test_modules_import_on_a_real_python_39_when_one_is_installed(self):
        """Annotations are lazy, but a runtime `int | None` or a missing 3.9 API only shows when the module is imported."""
        found = None
        for name in ("python3.9", "/usr/bin/python3"):
            exe = shutil.which(name)
            if exe:
                probe = subprocess.run([exe, "-c", "import sys; print(sys.version_info[:2] == (3, 9))"],
                                       capture_output=True, text=True)
                if probe.stdout.strip() == "True":
                    found = exe
                    break
        if not found:
            self.skipTest("no Python 3.9 on this machine (the CI matrix covers it)")
        code = ("import importlib, pkgutil, sys\n"
                "sys.path[:0] = sys.argv[1:4]\n"
                "import orkestra_core\n"
                "names = [m.name for m in pkgutil.walk_packages(orkestra_core.__path__, 'orkestra_core.')]\n"
                "names += ['dispatch', 'cursor_hook', 'copilot_hook']\n"
                "[importlib.import_module(n) for n in names]\n"
                "print(len(names))\n")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", ORKESTRA_HOME=tempfile.gettempdir())
        cp = subprocess.run([found, "-c", code, str(PLUGIN / "lib"), str(PLUGIN / "hooks"), str(ROOT / "bridge")],
                            capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertGreater(int(cp.stdout.strip()), 20)


class TestNoPosixOnlyAssumptionsWithoutAGuard(unittest.TestCase):
    """The portable fallbacks stay in place: each POSIX-only API is either guarded or has a fallback."""

    def library_text(self, name):
        return (PLUGIN / "lib" / "orkestra_core" / name).read_text(encoding="utf-8")

    def test_fcntl_and_msvcrt_are_optional_imports(self):
        text = self.library_text("util.py")
        self.assertRegex(text, r"try:\s+#[^\n]*\n\s+import fcntl")
        self.assertIn('if os.name == "nt"', text)

    def test_fork_and_setsid_are_only_used_on_the_posix_branch(self):
        text = self.library_text("engines.py")
        self.assertRegex(text, r'if os\.name == "nt":[\s\S]*?else:[\s\S]*?os\.fork\(\)')

    def test_sigalrm_has_a_thread_fallback_in_the_dispatcher(self):
        text = self.library_text("hookmain.py")                  # the hook code (hooks/dispatch.py is a stub since C2)
        self.assertIn('hasattr(signal, "SIGALRM")', text)

    def test_the_watchdog_never_signals_a_pid_on_windows(self):
        """os.kill(pid, 0) TERMINATES the process on Windows: the Windows branch must come first."""
        text = self.library_text("watchdog.py")
        self.assertLess(text.index("if _IS_WINDOWS:"), text.index("os.kill(pid, 0)"))

    def test_no_new_source_file_opens_text_without_an_encoding(self):
        """`open(path)` / `.read_text()` use the locale code page on Windows: every call in the portability-owned
        modules names its encoding (binary and os-level opens excepted)."""
        for name in ("util.py", "paths.py", "cursorkit.py", "copilotkit.py"):
            tree = ast.parse(self.library_text(name))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                attr = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if attr in ("read_text", "write_text"):
                    self.assertIn("encoding", {k.arg for k in node.keywords}, f"{name}:{node.lineno} {attr}")
                if attr == "open" and isinstance(fn, ast.Name):
                    mode = ""
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = str(node.args[1].value)
                    mode = mode or next((str(k.value.value) for k in node.keywords if k.arg == "mode"
                                         and isinstance(k.value, ast.Constant)), "r")
                    if "b" not in mode:
                        self.assertIn("encoding", {k.arg for k in node.keywords}, f"{name}:{node.lineno} open({mode!r})")


class TestDiffInANarrowLocale(helpers.Sandbox):
    """`orkestra diff` reads git's output, which is UTF-8 whatever the locale: the ASCII (or a legacy Windows) code page
    must neither crash it nor turn Turkish letters into mojibake."""

    def test_a_non_utf8_locale_does_not_break_the_diff(self):
        proj = self.make_project("demo")
        (proj / "tr.txt").write_bytes("merhaba dünya: şğıöüç İ\n".encode("utf-8"))
        self.git(proj, "add", "-A")
        self.git(proj, "commit", "-q", "-m", "add tr.txt")
        self.cli("start", "-p", "demo", "--title", "diff", "--request", "x", "--size", "S")
        (proj / "tr.txt").write_bytes("merhaba dünya: şğıöüç İ DEĞİŞTİ\n".encode("utf-8"))
        env = dict(os.environ, LC_ALL="C", PYTHONCOERCECLOCALE="0", PYTHONUTF8="0", PYTHONIOENCODING="utf-8",
                   ORKESTRA_LANG="en")
        cp = subprocess.run([sys.executable, str(BIN), "diff", "-p", "demo"], capture_output=True, env=env,
                            cwd=str(self.tmp), timeout=120)
        out = cp.stdout.decode("utf-8", "replace")
        self.assertEqual(cp.returncode, 0, cp.stderr.decode("utf-8", "replace"))
        self.assertIn("dünya: şğıöüç İ", out)
        self.assertIn("DEĞİŞTİ", out)


if __name__ == "__main__":
    unittest.main()
