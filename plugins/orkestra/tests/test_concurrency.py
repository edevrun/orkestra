"""Locking, lost updates and slow work under locks (agents must never block each other).

What is pinned here:
  * file_lock polls with a budget and raises LockTimeout naming the holder (hooks degrade, the CLI waits longer);
  * StateTxn gives its lock back on every failure path and never recreates a vanished run;
  * the shared manifests (edits.json, touched.json, ledger.md) lose nothing under parallel writers;
  * every git call is bounded and read-only on the index; baselines come from one `git cat-file --batch`;
  * slow precondition work runs outside the state lock; the non-git scan reuses a stat cache;
  * a bounded 16-process stress run over one run directory ends with exactly the expected totals.

Timing is deterministic where it can be (an injected clock); real-process tests use generous bounds.
"""
from __future__ import annotations

import ast
import contextlib
import errno
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

import helpers
from orkestra_core import evidence as E, paths, runs as R, snapshot as S, state as ST, util
from test_flow import FlowBase

LIB = str(helpers.LIB)
CORE = helpers.LIB / "orkestra_core"
NEEDS_FLOCK = unittest.skipIf(util.fcntl is None, "POSIX advisory locks only")
TR_LETTERS = re.compile("[çğıöşüÇĞİÖŞÜ]")
REAL_RUN = subprocess.run

# os.scandir/os.listdir are recorded through an audit hook: it sees the call whatever the caller (os.walk, pathlib)
# bound at import time, which patching os.scandir cannot. Hooks cannot be removed, so it is switched by a flag.
_LISTED: List[str] = []
_LISTING = [False]


def _audit(event: str, args: Any) -> None:
    if _LISTING[0] and event in ("os.scandir", "os.listdir") and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _LISTED.append(os.fsdecode(args[0]))


sys.addaudithook(_audit)


class FakeClock:
    """Virtual time for file_lock: every nap advances the clock instead of sleeping."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.naps: List[float] = []

    def __call__(self) -> float:
        return self.now

    def nap(self, seconds: float) -> None:
        self.naps.append(seconds)
        self.now += seconds


class SimMsvcrt:
    """Windows byte-range semantics: one holder per file, a held byte makes LK_NBLCK fail with EACCES. Records the
    position of every call: msvcrt.locking works at the CURRENT file position."""
    LK_UNLCK, LK_LOCK, LK_NBLCK = 0, 1, 2

    def __init__(self) -> None:
        self.held: Dict[int, int] = {}
        self.calls: List[Any] = []

    def locking(self, fd: int, mode: int, nbytes: int) -> None:
        pos = os.lseek(fd, 0, os.SEEK_CUR)
        ino = os.fstat(fd).st_ino
        self.calls.append((mode, pos, nbytes))
        if mode == self.LK_UNLCK:
            if self.held.get(ino) == fd:
                del self.held[ino]
            return
        if mode == self.LK_LOCK:
            raise AssertionError("polling must use LK_NBLCK: LK_LOCK blocks inside msvcrt for ten seconds")
        if ino in self.held and self.held[ino] != fd:
            raise OSError(errno.EACCES, "Permission denied")
        self.held[ino] = fd


class HolderMixin:
    """A second process that holds a file_lock until it is killed (or for a number of seconds)."""

    def start_holder(self, target: Path, seconds: float) -> subprocess.Popen:
        script = self.tmp / "lockholder.py"
        script.write_text(textwrap.dedent('''
            import sys, time
            sys.path.insert(0, sys.argv[1])
            from pathlib import Path
            from orkestra_core import util
            with util.file_lock(Path(sys.argv[2])):
                print("held", flush=True)
                time.sleep(float(sys.argv[3]))
        '''), encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(script), LIB, str(target), str(seconds)], stdout=subprocess.PIPE,
                                text=True)
        self.addCleanup(self._reap, proc)
        self.assertEqual(proc.stdout.readline().strip(), "held")
        return proc

    @staticmethod
    def _reap(proc: subprocess.Popen) -> None:
        with contextlib.suppress(Exception):
            if hasattr(signal, "SIGCONT"):
                proc.send_signal(signal.SIGCONT)
            proc.kill()
        proc.wait()
        proc.stdout.close()


def reset_lock_policy() -> None:
    getattr(util, "set_lock_policy", lambda _kind: None)(None)


class Base(HolderMixin, helpers.Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.addCleanup(reset_lock_policy)

    def patch_clock(self) -> FakeClock:
        clock = FakeClock()
        for name, fn in (("_clock", clock), ("_pause", clock.nap)):
            patcher = mock.patch.object(util, name, fn)
            patcher.start()
            self.addCleanup(patcher.stop)
        return clock

    def make_run(self, phase: str = "implement", size: str = "XS", tasks: Dict[str, Any] = None, git: bool = True) -> None:
        self.proj = self.make_project("demo", git=git)
        self.rid = "001-demo"
        st = ST.new_state(self.proj, self.rid, "Demo", size, "feature")
        st["phase"] = phase
        st["tasks"] = tasks or {}
        self.rdir = paths.run_dir(self.proj, self.rid)
        self.fdir = paths.feature_dir(self.proj, self.rid)
        self.rdir.mkdir(parents=True)
        self.fdir.mkdir(parents=True)
        ST.save_state(self.proj, st)
        self.state_file = ST.state_path(self.proj, self.rid)



# ---------------------------------------------------------------- (a) the lock itself

class TestFileLockBudget(Base):
    def setUp(self) -> None:
        super().setUp()
        self.target = self.tmp / "x" / "state.json"

    @NEEDS_FLOCK
    def test_waits_in_small_steps_then_raises_naming_the_holder(self):
        clock = self.patch_clock()
        with util.file_lock(self.target):
            with self.assertRaises(util.LockTimeout) as cm:
                with util.file_lock(self.target, timeout=5):
                    self.fail("must not run without the lock")
        exc = cm.exception
        self.assertIsInstance(exc, OSError)
        spent = clock.now - 1000.0
        self.assertGreaterEqual(spent, 5.0 - 1e-6, "the whole budget is used before giving up")
        self.assertLess(spent, 5.1, "and not much more")
        self.assertTrue(clock.naps and min(clock.naps) > 0 and max(clock.naps) <= 0.07, clock.naps)
        text = str(exc)
        self.assertIn(str(os.getpid()), text, "the holder's pid is named")
        self.assertIn("state.json", text)
        self.assertNotIn("\n", text)

    @NEEDS_FLOCK
    def test_message_is_localized_and_one_line(self):
        self.patch_clock()
        texts = {}
        for lang in ("en", "tr"):
            with mock.patch.dict(os.environ, {"ORKESTRA_LANG": lang}), util.file_lock(self.target):
                with self.assertRaises(util.LockTimeout) as cm:
                    with util.file_lock(self.target, timeout=1):
                        pass
            texts[lang] = str(cm.exception)
            self.assertNotIn("\n", texts[lang])
            self.assertNotIn("\u2014", texts[lang])
        self.assertIsNone(TR_LETTERS.search(texts["en"]), texts["en"])
        self.assertIsNotNone(TR_LETTERS.search(texts["tr"]), texts["tr"])
        self.assertNotEqual(texts["en"], texts["tr"])

    @NEEDS_FLOCK
    def test_zero_timeout_tries_once(self):
        clock = self.patch_clock()
        with util.file_lock(self.target):
            with self.assertRaises(util.LockTimeout):
                with util.file_lock(self.target, timeout=0):
                    pass
        self.assertEqual(clock.naps, [])

    @NEEDS_FLOCK
    def test_cli_policy_waits_ten_seconds_then_raises(self):
        util.set_lock_policy("cli")
        clock = self.patch_clock()
        self.assertEqual(util.CLI_LOCK_TIMEOUT, 10.0)
        with util.file_lock(self.target):
            with self.assertRaises(util.LockTimeout):
                with util.file_lock(self.target):
                    self.fail("must not run without the lock")
        self.assertGreaterEqual(clock.now - 1000.0, 10.0 - 1e-6)
        self.assertLess(clock.now - 1000.0, 10.1)

    @NEEDS_FLOCK
    def test_hook_policy_continues_unlocked_after_two_seconds_and_logs_once(self):
        util.set_lock_policy("hook")
        clock = self.patch_clock()
        self.assertTrue(1.5 <= util.HOOK_LOCK_TIMEOUT <= 2.0)
        ran = []
        with util.file_lock(self.target):
            with util.file_lock(self.target):
                ran.append("first")
            with util.file_lock(self.target):
                ran.append("second")
        self.assertEqual(ran, ["first", "second"], "bookkeeping paths go on without the lock")
        spent = clock.now - 1000.0
        self.assertAlmostEqual(spent, 2 * util.HOOK_LOCK_TIMEOUT, delta=0.2)
        log = (paths.logs_dir() / "hooks.log").read_text(encoding="utf-8")
        mine = [line for line in log.splitlines() if str(self.target) in line]
        self.assertEqual(len(mine), 1, "one log line per lock, not one per call: " + log)
        self.assertIn("without the lock", mine[0])

    @NEEDS_FLOCK
    def test_an_explicit_raise_beats_the_hook_policy(self):
        util.set_lock_policy("hook")
        self.patch_clock()
        with util.file_lock(self.target):
            with self.assertRaises(util.LockTimeout):
                with util.file_lock(self.target, on_timeout="raise"):
                    self.fail("state writes must not run unlocked")

    def test_policy_follows_the_entry_script_unless_set(self):
        for script, want in (("/p/hooks/dispatch.py", "hook"), ("/p/hooks/cursor_hook.py", "hook"),
                             ("/p/hooks/copilot_hook.py", "hook"), ("/p/bin/orkestra", "cli"), ("-c", "cli"), ("", "cli")):
            with mock.patch.object(sys, "argv", [script, "Stop"]):
                self.assertEqual(util.lock_policy(), want, script)
        with mock.patch.object(sys, "argv", ["/p/bin/orkestra"]):
            util.set_lock_policy("hook")
            self.assertEqual(util.lock_policy(), "hook")
            util.set_lock_policy(None)
            self.assertEqual(util.lock_policy(), "cli")

    @NEEDS_FLOCK
    def test_environment_overrides_the_budget(self):
        clock = self.patch_clock()
        with mock.patch.dict(os.environ, {"ORKESTRA_LOCK_TIMEOUT": "0.5"}), util.file_lock(self.target):
            with self.assertRaises(util.LockTimeout):
                with util.file_lock(self.target):
                    pass
        self.assertLess(clock.now - 1000.0, 0.6)

    def test_windows_path_polls_without_blocking_and_names_the_holder(self):
        sim = SimMsvcrt()
        clock = self.patch_clock()
        with mock.patch.object(util, "fcntl", None), mock.patch.object(util, "msvcrt", sim):
            with util.file_lock(self.target):
                with self.assertRaises(util.LockTimeout) as cm:
                    with util.file_lock(self.target, timeout=3):
                        self.fail("must not run without the lock")
            with util.file_lock(self.target):
                pass                                    # free again once the holder let go
        self.assertGreaterEqual(clock.now - 1000.0, 3.0 - 1e-6)
        self.assertTrue(all(pos == 0 and n == 1 for _mode, pos, n in sim.calls), sim.calls)
        self.assertNotIn(SimMsvcrt.LK_LOCK, [m for m, _p, _n in sim.calls])
        data = Path(str(self.target) + ".lock").read_bytes()
        self.assertEqual(data[:1], b"\0", "the locked byte stays in place; the holder note follows it")
        self.assertIn(("pid=%d" % os.getpid()).encode(), data)
        self.assertIn(str(os.getpid()), str(cm.exception))

    @NEEDS_FLOCK
    def test_a_killed_holder_frees_the_lock_at_once_and_the_next_holder_is_named(self):
        proc = self.start_holder(self.target, 60)
        proc.kill()
        proc.wait()
        started = time.monotonic()
        with util.file_lock(self.target):
            waited = time.monotonic() - started
            note = Path(str(self.target) + ".lock").read_text(encoding="utf-8", errors="replace")
        self.assertLess(waited, 1.0, "the kernel drops a dead holder's flock")
        self.assertIn("pid=%d" % os.getpid(), note)
        self.assertNotIn("pid=%d" % proc.pid, note)

    @NEEDS_FLOCK
    def test_a_frozen_holder_times_out_naming_pid_and_command(self):
        proc = self.start_holder(self.target, 60)
        os.kill(proc.pid, signal.SIGSTOP)
        started = time.monotonic()
        with self.assertRaises(util.LockTimeout) as cm:
            with util.file_lock(self.target, timeout=0.5):
                self.fail("must not run without the lock")
        waited = time.monotonic() - started
        self.assertTrue(0.4 <= waited < 6.0, waited)
        self.assertIn(str(proc.pid), str(cm.exception))
        self.assertIn("lockholder.py", str(cm.exception))


# ---------------------------------------------------------------- (c) StateTxn failure paths

class TestStateTxn(Base):
    def setUp(self) -> None:
        super().setUp()
        self.make_run()

    def lock_is_free(self) -> bool:
        fd = os.open(str(self.state_file) + ".lock", os.O_RDWR | os.O_CREAT)
        try:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            fcntl.flock(fd, fcntl.LOCK_UN)
            return True
        finally:
            os.close(fd)

    @NEEDS_FLOCK
    def test_lock_is_free_after_a_damaged_state_failed_the_enter(self):
        self.state_file.write_text("{not json", encoding="utf-8")
        txn = ST.StateTxn(self.proj, self.rid)
        with self.assertRaises(ST.OrkestraError) as cm:
            txn.__enter__()
        keep = (txn, cm.exception)                    # the traceback keeps the frames, and what they own, alive
        self.assertTrue(self.lock_is_free(), "a failed __enter__ never reaches __exit__")
        del keep

    @NEEDS_FLOCK
    def test_lock_is_free_after_any_exception_in_the_body(self):
        for exc in (KeyboardInterrupt, SystemExit, RuntimeError):
            txn = ST.StateTxn(self.proj, self.rid)
            with self.assertRaises(exc):
                with txn as st:
                    st["phase"] = "review"
                    raise exc()
            self.assertTrue(self.lock_is_free(), exc.__name__)
            self.assertEqual(ST.load_state(self.proj, self.rid)["phase"], "implement", "no half-done change is saved")

    @NEEDS_FLOCK
    def test_lock_is_free_when_the_save_fails(self):
        txn = ST.StateTxn(self.proj, self.rid)
        with mock.patch.object(ST, "save_state", side_effect=OSError(errno.ENOSPC, "disk full")):
            with self.assertRaises(OSError):
                with txn as st:
                    st["phase"] = "review"
        self.assertTrue(self.lock_is_free())

    def test_a_vanished_run_is_not_recreated_by_the_lock(self):
        gone = paths.run_dir(self.proj, "009-gone")
        self.assertFalse(gone.exists())
        with self.assertRaises(ST.OrkestraError):
            ST.StateTxn(self.proj, "009-gone").__enter__()
        self.assertFalse(gone.exists(), "taking the lock must not leave an empty run shell behind")

    @NEEDS_FLOCK
    def test_a_busy_state_is_a_one_line_error_for_hooks_and_cli_in_both_languages(self):
        self.patch_clock()
        for policy in ("hook", "cli"):
            for lang in ("en", "tr"):
                util.set_lock_policy(policy)
                with mock.patch.dict(os.environ, {"ORKESTRA_LANG": lang}), util.file_lock(self.state_file):
                    with self.assertRaises(ST.OrkestraError) as cm:
                        with ST.StateTxn(self.proj, self.rid):
                            self.fail("a state write must not run without the lock")
                text = str(cm.exception)
                self.assertNotIn("\n", text, (policy, lang))
                self.assertIn(str(os.getpid()), text)
                self.assertIn("state.json", text)
                if lang == "en":
                    self.assertIsNone(TR_LETTERS.search(text), text)


# ---------------------------------------------------------------- (b) lost updates

WORKER_PRELUDE = '''
import json, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from orkestra_core import snapshot, state, util
cfg = json.loads(sys.argv[2])
Path(cfg["ready"]).write_text("")
go = Path(cfg["go"])
while not go.exists():
    time.sleep(0.001)
'''

WIDEN_SNAPSHOT = '''
real_write = snapshot.write_json_atomic
def slow_write(path, data):         # widens the read-modify-write window: a missing lock cannot go unnoticed
    time.sleep(0.05)
    real_write(path, data)
snapshot.write_json_atomic = slow_write
'''

WIDEN_LEDGER = '''
real_replace = util.replace_file
def slow_replace(src, dst):         # an atomic rewrite of the ledger: every worker has read the old text by now
    time.sleep(0.05)
    real_replace(src, dst)
util.replace_file = slow_replace
'''


class TestLostUpdates(Base):
    N = 16

    def setUp(self) -> None:
        super().setUp()
        self.make_run()
        self.sync = self.tmp / "sync"
        self.sync.mkdir()

    def run_workers(self, body: str, widen: str = "", extra: Dict[str, Any] = None) -> None:
        script = self.tmp / "worker.py"
        script.write_text(WORKER_PRELUDE + widen + body, encoding="utf-8")
        procs = []
        for wid in range(self.N):
            cfg = dict(extra or {}, wid=wid, project=str(self.proj), rid=self.rid, go=str(self.sync / "go"),
                       ready=str(self.sync / f"ready-{wid}"))
            procs.append(subprocess.Popen([sys.executable, str(script), LIB, json.dumps(cfg)], stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True))
        deadline = time.monotonic() + 30
        while len(list(self.sync.glob("ready-*"))) < self.N and time.monotonic() < deadline:
            time.sleep(0.01)
        (self.sync / "go").write_text("")
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)

    def test_backup_before_edit_keeps_every_first_original(self):
        body = '''
project = Path(cfg["project"]); rdir = project / ".orkestra" / "runs" / cfg["rid"]
f = project / "work" / ("f%d.txt" % cfg["wid"])
f.parent.mkdir(exist_ok=True)
f.write_text("original %d" % cfg["wid"])
snapshot.backup_before_edit(project, rdir, f)
'''
        (self.proj / "work").mkdir()
        self.run_workers(body, WIDEN_SNAPSHOT)
        manifest = json.loads((self.rdir / "snapshot" / "edits.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(manifest), sorted(f"work/f{i}.txt" for i in range(self.N)))
        for i in range(self.N):
            self.assertEqual((self.rdir / "snapshot" / "edits" / "work" / f"f{i}.txt").read_text(), f"original {i}")

    def test_record_touch_keeps_every_path(self):
        body = '''
project = Path(cfg["project"]); rdir = project / ".orkestra" / "runs" / cfg["rid"]
snapshot.record_touch(project, rdir, project / "work" / ("t%d.txt" % cfg["wid"]))
'''
        self.run_workers(body, WIDEN_SNAPSHOT)
        touched = json.loads((self.rdir / "snapshot" / "touched.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(touched), sorted(f"work/t{i}.txt" for i in range(self.N)))

    def test_a_second_edit_of_the_same_path_never_replaces_the_first_backup(self):
        f = self.proj / "calc.py"
        f.write_text("first\n")
        S.backup_before_edit(self.proj, self.rdir, f)
        f.write_text("second\n")
        S.backup_before_edit(self.proj, self.rdir, f)
        self.assertEqual((self.rdir / "snapshot" / "edits" / "calc.py").read_text(), "first\n")

    def test_ledger_lines_are_neither_lost_nor_interleaved_and_the_header_comes_once(self):
        body = 'state.ledger(Path(cfg["project"]), cfg["rid"], "w%d" % cfg["wid"])\n'
        self.assertFalse((self.fdir / "ledger.md").exists())
        self.run_workers(body, WIDEN_LEDGER)
        lines = (self.fdir / "ledger.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len([x for x in lines if x.startswith("# Ledger")]), 1, lines)
        entries = [x for x in lines if x.startswith("- ")]
        self.assertEqual(len(entries), self.N, lines)
        self.assertTrue(all(re.match(r"^- \d{4}-\d\d-\d\d \d\d:\d\d w\d+$", x) for x in entries), entries)
        self.assertEqual(sorted(x.split()[-1] for x in entries), sorted(f"w{i}" for i in range(self.N)))

    def test_ledger_appends_with_one_write_and_keeps_old_lines(self):
        path = self.fdir / "ledger.md"
        ST.ledger(self.proj, self.rid, "one")
        opened: List[int] = []
        wrote: List[int] = []
        real_open, real_write = os.open, os.write

        def spy_open(p, flags, *a, **kw):
            fd = real_open(p, flags, *a, **kw)
            if str(p) == str(path):
                opened.append(flags)
                wrote.append(fd)
            return fd

        calls: List[int] = []

        def spy_write(fd, data):
            if fd in wrote:
                calls.append(fd)
            return real_write(fd, data)

        with mock.patch.object(os, "open", spy_open), mock.patch.object(os, "write", spy_write), \
                mock.patch.object(util, "replace_file", side_effect=AssertionError("a rewrite loses concurrent lines")):
            ST.ledger(self.proj, self.rid, "two")
        self.assertEqual(len(calls), 1, "one write per ledger line")
        self.assertTrue(opened and opened[0] & os.O_APPEND, "O_APPEND makes the write atomic against other writers")
        text = path.read_text(encoding="utf-8")
        self.assertIn(" one\n", text)
        self.assertTrue(text.endswith(" two\n"))
        self.assertEqual(text.count("# Ledger"), 1)

    def test_registries_keep_every_key(self):
        body = '''
state.set_active_run(Path("/stress/p%d" % cfg["wid"]), cfg["rid"])
'''
        self.run_workers(body)
        got = ST.active_runs()
        self.assertEqual(sorted(got), sorted(f"/stress/p{i}" for i in range(self.N)))


# ---------------------------------------------------------------- (d) git calls

class GitRecorder:
    def __init__(self) -> None:
        self.calls: List[Any] = []

    def __call__(self, args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and args[0] == "git":
            self.calls.append((list(args), kw))
        return REAL_RUN(args, *a, **kw)

    def count(self, verb: str) -> int:
        return sum(1 for args, _kw in self.calls if verb in args[3:])


class TestGitCalls(Base):
    def setUp(self) -> None:
        super().setUp()
        self.make_run()
        self.base = S.take_baseline(self.proj, self.rdir)

    def exercise(self) -> None:
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n")
        (self.proj / "tests" / "test_new.py").write_text("def test_a():\n    pass\n")
        S.changed_files(self.proj, self.rdir)
        E.test_files_state(self.proj, self.rdir)
        E.baseline_content(self.proj, self.rdir, "calc.py")
        E.baseline_contents(self.proj, self.rdir, ["calc.py", "Makefile"])
        E.integrity_problems(self.proj, self.rid)
        S.take_baseline(self.proj, self.tmp / "other-run")

    def test_every_git_call_has_a_timeout_and_never_takes_the_index_lock(self):
        rec = GitRecorder()
        with mock.patch.object(subprocess, "run", rec):
            self.exercise()
        self.assertGreaterEqual(len(rec.calls), 3)
        for args, kw in rec.calls:
            self.assertTrue(kw.get("timeout"), f"no timeout: {args}")
            self.assertEqual((kw.get("env") or {}).get("GIT_OPTIONAL_LOCKS"), "0", f"index lock not avoided: {args}")

    def test_source_has_no_unbounded_git_call(self):
        for name in ("snapshot.py", "evidence.py", "cli.py"):
            tree = ast.parse((CORE / name).read_text(encoding="utf-8"))
            for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                git_lists = [n for n in ast.walk(fn) if isinstance(n, ast.List) and n.elts
                             and isinstance(n.elts[0], ast.Constant) and n.elts[0].value == "git"]
                if not git_lists:
                    continue
                for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                             and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                             and n.func.value.id == "subprocess" and n.func.attr in ("run", "Popen", "check_output", "call")]:
                    keys = {k.arg for k in call.keywords}
                    self.assertIn("timeout", keys, f"{name}:{call.lineno} runs git without a timeout")
                    self.assertIn("env", keys, f"{name}:{call.lineno} runs git without GIT_OPTIONAL_LOCKS=0")

    def test_a_missing_git_degrades_without_raising(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        (self.proj / "calc.py").write_text("changed\n")
        with mock.patch.dict(os.environ, {"PATH": str(empty)}):
            self.assertFalse(S.is_git_repo(self.proj))
            self.assertIsNone(E.baseline_content(self.proj, self.rdir, "tests/test_calc.py"))
            self.assertEqual(E.baseline_contents(self.proj, self.rdir, ["calc.py"]), {"calc.py": None})
            S.changed_files(self.proj, self.rdir)
            E.test_files_state(self.proj, self.rdir)
            E.integrity_problems(self.proj, self.rid)
            info = S.take_baseline(self.proj, self.tmp / "no-git-run")
        self.assertFalse(info["git"])
        self.assertIsInstance(info["manifest"], dict)

    def test_a_git_timeout_degrades_without_raising(self):
        def hang(args, *a, **kw):
            if isinstance(args, (list, tuple)) and args and args[0] == "git":
                raise subprocess.TimeoutExpired(args, kw.get("timeout") or 1)
            return REAL_RUN(args, *a, **kw)

        (self.proj / "calc.py").write_text("changed\n")
        with mock.patch.object(subprocess, "run", hang):
            self.assertFalse(S.is_git_repo(self.proj))
            self.assertIsNone(E.baseline_content(self.proj, self.rdir, "tests/test_calc.py"))
            S.changed_files(self.proj, self.rdir)


# ---------------------------------------------------------------- (e) slow work out of the lock

def old_test_files_state(project: Path, run_dir: Path) -> Dict[str, Any]:
    """The previous implementation: one baseline lookup per changed text file."""
    state: Dict[str, Any] = {}
    for item in S.changed_files(project, run_dir):
        rel = item["path"]
        p = project / rel
        if E.is_test_path(rel):
            state[rel] = util.sha256_file(p) if p.is_file() else None
        elif p.is_file() and E.TEXT_EXT.search(rel):
            now = util.read_text(p)
            if E.count_tests(now) > E.count_tests(E.baseline_content(project, run_dir, rel) or ""):
                state[rel] = util.sha256_file(p)
    return state


class TestBaselines(Base):
    def setUp(self) -> None:
        super().setUp()
        self.proj = self.make_project("demo")
        self.rid = "001-demo"
        self.rdir = paths.run_dir(self.proj, self.rid)
        self.rdir.mkdir(parents=True)

    def commit_all(self, msg: str = "more") -> None:
        self.git(self.proj, "add", "-A")
        self.git(self.proj, "commit", "-q", "-m", msg)

    def mixed_repo(self) -> List[str]:
        """Tracked files with and without tests, odd names, a dirty-at-baseline file; then changes of every kind."""
        files = {
            "src/plain one.py": "x = 1\n",
            "src/plain_two.py": "y = 2\n",
            "src/with_tests.py": "def test_a():\n    pass\n\ndef test_b():\n    pass\n",
            "src/grows.rs": "fn main() {}\n",
            "src/gone.py": "def test_old():\n    pass\n",
            "src/renamed_from.py": "def test_r():\n    pass\n",
            "src/\u00e9clair.py": "z = 3\n",
            "src/dirty.py": "def test_d():\n    pass\n",
            "src/we\nird.py": "def test_w():\n    pass\n",             # a newline cannot go through --batch
        }
        for rel, text in files.items():
            (self.proj / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.proj / rel).write_text(text, encoding="utf-8")
        self.commit_all("files")
        (self.proj / "src/dirty.py").write_text("def test_d():\n    pass\n\ndef test_e():\n    pass\n")   # before the run
        S.take_baseline(self.proj, self.rdir)
        (self.proj / "src/plain one.py").write_text("x = 1\n# changed\n")
        (self.proj / "src/with_tests.py").write_text("def test_a():\n    pass\n")                         # a test removed
        (self.proj / "src/grows.rs").write_text("fn main() {}\n#[test]\nfn t() {}\n")                      # inline test added
        (self.proj / "src/gone.py").unlink()
        (self.proj / "src/renamed_from.py").rename(self.proj / "src/renamed_to.py")
        (self.proj / "src/\u00e9clair.py").write_text("z = 3\n# TODO\n", encoding="utf-8")
        (self.proj / "src/dirty.py").write_text("def test_d():\n    pass\n\n@pytest.mark.skip\ndef test_e():\n    pass\n")
        (self.proj / "src/brand_new.py").write_text("def test_n():\n    pass\n")
        return sorted(files) + ["src/brand_new.py", "src/renamed_to.py"]

    def test_batched_baselines_equal_git_show_and_the_local_copies(self):
        rels = self.mixed_repo()
        got = E.baseline_contents(self.proj, self.rdir, rels)
        self.assertEqual(sorted(got), sorted(set(rels)))
        for rel in rels:
            cp = REAL_RUN(["git", "-C", str(self.proj), "show", f"HEAD:{rel}"], capture_output=True)
            want = cp.stdout.decode("utf-8", "replace") if cp.returncode == 0 else None
            if rel == "src/dirty.py":
                want = "def test_d():\n    pass\n\ndef test_e():\n    pass\n"            # the baseline backup, not HEAD
            self.assertEqual(got[rel], want, rel)
            self.assertEqual(E.baseline_content(self.proj, self.rdir, rel), want, rel)

    def test_git_blobs_edge_cases(self):
        got = S.git_blobs(self.proj, ["calc.py", "nope.py", "../outside.txt", "tests", "calc.py"])
        self.assertEqual(sorted(got), ["../outside.txt", "calc.py", "nope.py", "tests"])
        self.assertEqual(got["calc.py"], b"def add(a, b):\n    return a + b\n")
        self.assertIsNone(got["nope.py"])
        self.assertIsNone(got["../outside.txt"], "outside the repository is unknown")
        self.assertIsNone(got["tests"], "a directory is not a file")
        fresh = self.ws / "fresh"
        fresh.mkdir()
        self.git(fresh, "init", "-q")
        self.assertEqual(S.git_blobs(fresh, ["a.py"]), {"a.py": None}, "no commit yet: nothing is in HEAD")
        self.assertEqual(S.git_blobs(self.proj, []), {})

    def test_test_files_state_matches_the_per_file_implementation(self):
        self.mixed_repo()
        want = old_test_files_state(self.proj, self.rdir)
        self.assertEqual(E.test_files_state(self.proj, self.rdir), want)
        self.assertIn("src/grows.rs", want)
        self.assertIn("src/brand_new.py", want)

    def test_integrity_problems_are_found_with_one_batched_call(self):
        self.mixed_repo()
        rec = GitRecorder()
        with mock.patch.object(subprocess, "run", rec):
            problems = E.integrity_problems(self.proj, self.rid)
        text = "\n".join(problems)
        self.assertIn("src/with_tests.py: test count dropped 2 \u2192 1", text)
        self.assertIn("src/gone.py: test file deleted (1 tests)", text)
        self.assertIn("src/dirty.py: adds a skip/only/xfail marker", text)
        self.assertEqual(rec.count("show"), 0, "no per-file git show")
        self.assertLessEqual(rec.count("cat-file"), 1)

    def test_files_without_tests_never_ask_git_for_their_baseline(self):
        for i in range(30):
            (self.proj / f"mod{i}.py").write_text(f"v = {i}\n")
        (self.proj / "lib.rs").write_text("fn f() {}\n")
        self.commit_all()
        S.take_baseline(self.proj, self.rdir)
        for i in range(30):
            (self.proj / f"mod{i}.py").write_text(f"v = {i + 1}\n")
        (self.proj / "lib.rs").write_text("fn f() {}\n#[test]\nfn t() {}\n")
        rec = GitRecorder()
        with mock.patch.object(subprocess, "run", rec):
            state = E.test_files_state(self.proj, self.rdir)
        self.assertEqual(sorted(state), ["lib.rs"])
        self.assertEqual(rec.count("show"), 0)
        self.assertLessEqual(rec.count("cat-file"), 1)
        self.assertLessEqual(len(rec.calls), 6, "status + toplevel + one batch, not one process per file")

    def test_no_tests_anywhere_needs_no_baseline_lookup_at_all(self):
        for i in range(10):
            (self.proj / f"mod{i}.py").write_text(f"v = {i}\n")
        self.commit_all()
        S.take_baseline(self.proj, self.rdir)
        for i in range(10):
            (self.proj / f"mod{i}.py").write_text(f"v = {i + 1}\n")
        rec = GitRecorder()
        with mock.patch.object(subprocess, "run", rec):
            self.assertEqual(E.test_files_state(self.proj, self.rdir), {})
        self.assertEqual(rec.count("show") + rec.count("cat-file"), 0)

    def test_verification_corpus_never_descends_into_skipped_directories(self):
        for i in range(5):
            d = self.proj / "node_modules" / f"pkg{i}" / "tests"
            d.mkdir(parents=True)
            (d / "test_dep.js").write_text("it('dep', () => {})\n")
        (self.proj / "Pods" / "x").mkdir(parents=True)
        (self.proj / "Pods" / "x" / "foo.test.js").write_text("it('pod', () => {})\n")
        (self.proj / "tests" / "nested").mkdir()
        (self.proj / "tests" / "nested" / "test_deep.py").write_text("def test_deep_one():\n    pass\n")
        del _LISTED[:]
        _LISTING[0] = True
        try:
            corpus = E._project_test_corpus(self.proj)
        finally:
            _LISTING[0] = False
        self.assertIn("test_deep_one", corpus)
        self.assertIn("def test_add", corpus)
        self.assertNotIn("dep'", corpus)
        self.assertNotIn("pod'", corpus)
        root = Path(os.path.realpath(self.proj))
        inside = [p for p in _LISTED if Path(os.path.realpath(p)) != root and root in Path(os.path.realpath(p)).parents]
        bad = [p for p in inside if {"node_modules", "Pods"} & set(Path(p).parts)]
        self.assertTrue(inside, "the walk listed project directories at all")
        self.assertEqual(bad, [], "pruned directories must not even be listed")


class TestOutsideTheStateLock(Base):
    """The slow part of a transition runs before the lock is taken; the lock only validates and commits."""

    def setUp(self) -> None:
        super().setUp()

    def free(self) -> bool:
        try:
            with util.file_lock(self.state_file, timeout=0):
                return True
        except util.LockTimeout:
            return False

    @NEEDS_FLOCK
    def test_phase_next_computes_preconditions_before_taking_the_lock(self):
        self.make_run(phase="verify")
        seen: List[bool] = []

        def fake(project, st, target):
            seen.append(self.free())
            return []

        with mock.patch.object(R, "preconditions", fake):
            self.assertEqual(R.phase_next(self.proj, self.rid), ("verify", "review"))
        self.assertEqual(seen, [True], "preconditions ran once, with the state lock free")
        self.assertEqual(ST.load_state(self.proj, self.rid)["phase"], "review")

    @NEEDS_FLOCK
    def test_phase_next_rechecks_inside_when_the_state_moved_meanwhile(self):
        self.make_run(phase="verify", tasks={"T1": {"status": "pending", "tdd": "n/a"}})
        seen: List[bool] = []

        def fake(project, st, target):
            seen.append(self.free())
            if len(seen) == 1:
                with ST.StateTxn(project, st["run_id"]) as other:       # another agent reopens work meanwhile
                    other["tasks"]["T1"]["status"] = "in_progress"
                return []
            open_tasks = [t for t, v in st["tasks"].items() if v["status"] != "done"]
            return [f"tasks not done: {', '.join(open_tasks)}"]

        with mock.patch.object(R, "preconditions", fake):
            with self.assertRaises(ST.OrkestraError) as cm:
                R.phase_next(self.proj, self.rid)
        self.assertIn("tasks not done: T1", str(cm.exception))
        self.assertEqual(seen, [True, False], "stale answer discarded, recomputed under the lock")
        self.assertEqual(ST.load_state(self.proj, self.rid)["phase"], "verify")

    @NEEDS_FLOCK
    def test_hook_bookkeeping_does_not_invalidate_the_precomputed_answer(self):
        self.make_run(phase="verify")
        calls: List[int] = []

        def fake(project, st, target):
            calls.append(1)
            self.assertTrue(self.free(), "preconditions must run with the state lock free")
            with ST.StateTxn(project, st["run_id"]) as other:           # what the Stop hook writes
                other.setdefault("stop_blocks", {})["p1"] = 1
            return []

        with mock.patch.object(R, "preconditions", fake):
            R.phase_next(self.proj, self.rid)
        self.assertEqual(len(calls), 1)

    @NEEDS_FLOCK
    def test_phase_next_to_done_reads_usage_before_taking_the_lock(self):
        self.make_run(phase="deliver")
        seen: List[bool] = []

        def usage(project, st):
            seen.append(self.free())
            return {"total": 1}

        with mock.patch.object(R, "preconditions", return_value=[]), mock.patch.object(R, "usage_snapshot", usage):
            R.phase_next(self.proj, self.rid)
        self.assertEqual(seen, [True])
        self.assertEqual(ST.load_state(self.proj, self.rid)["usage"], {"total": 1})

    @NEEDS_FLOCK
    def test_task_done_checks_the_test_files_before_taking_the_lock(self):
        task = {"status": "in_progress", "tdd": "required", "red": {"ok": True}, "green": {"ok": True}}
        self.make_run(tasks={"T1": task})
        seen: List[bool] = []

        def fake(project, run_dir, changed=None):
            seen.append(self.free())
            return {"tests/test_x.py": "abc"}

        with mock.patch.object(E, "test_files_state", fake):
            self.assertTrue(R.task_done(self.proj, self.rid, "T1", "ok"))
        self.assertEqual(seen, [True])
        self.assertEqual(ST.load_state(self.proj, self.rid)["tasks"]["T1"]["status"], "done")

    def test_task_done_still_refuses_a_tdd_task_without_test_files(self):
        task = {"status": "in_progress", "tdd": "required", "red": {"ok": True}, "green": {"ok": True}}
        self.make_run(tasks={"T1": task})
        with mock.patch.object(E, "test_files_state", return_value={}):
            with self.assertRaises(ST.OrkestraError) as cm:
                R.task_done(self.proj, self.rid, "T1")
        self.assertIn("no test file was added or changed", str(cm.exception))
        self.assertEqual(ST.load_state(self.proj, self.rid)["tasks"]["T1"]["status"], "in_progress")

    def test_review_preconditions_scan_the_changes_once(self):
        self.make_run(phase="verify", git=True)
        S.take_baseline(self.proj, self.rdir)
        (self.proj / "calc.py").write_text("def add(a, b):\n    return a + b\n# x\n")
        util.write_json_atomic(self.rdir / "gates-verify.json", {"ts": time.time() + 3600, "comparison": {"ok": True}})
        calls: List[int] = []
        real = S.changed_files

        def counting(*a, **kw):
            calls.append(1)
            return real(*a, **kw)

        with mock.patch.object(S, "changed_files", counting):
            R.preconditions(self.proj, ST.load_state(self.proj, self.rid), "review")
        self.assertEqual(len(calls), 1, "newest-change and integrity share one scan")


# ---------------------------------------------------------------- (f) stat cache for the non-git scan

class TestStatCache(Base):
    N = 400

    def setUp(self) -> None:
        super().setUp()
        self.make_run(git=False)
        old = time.time() - 3600
        for i in range(self.N):
            p = self.proj / "pkg" / f"d{i % 10}" / f"f{i}.txt"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"content {i}\n")
            os.utime(p, (old, old))
        for p in (self.proj / "calc.py", self.proj / "Makefile", self.proj / "tests" / "test_calc.py"):
            os.utime(p, (old, old))
        self.hashed: List[str] = []
        real = util.sha256_file

        def counting(path, limit=None):
            self.hashed.append(str(path))
            return real(path, limit)

        patcher = mock.patch.object(S, "sha256_file", counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.base = S.take_baseline(self.proj, self.rdir)
        self.assertGreaterEqual(len(self.hashed), self.N)
        self.hashed.clear()

    def edit(self, rel: str, text: str, mtime: float = None) -> None:
        p = self.proj / rel
        p.write_text(text)
        if mtime is not None:
            os.utime(p, (mtime, mtime))

    def test_unchanged_files_are_not_hashed_again(self):
        self.edit("pkg/d1/f1.txt", "changed 1\n")
        self.edit("pkg/d2/f2.txt", "changed 2\n")
        first = S.changed_files(self.proj, self.rdir)
        self.assertEqual(sorted(i["path"] for i in first), ["pkg/d1/f1.txt", "pkg/d2/f2.txt"])
        self.assertLessEqual(len(self.hashed), 4, f"hashed {len(self.hashed)} files for 2 changes")
        self.hashed.clear()
        second = S.changed_files(self.proj, self.rdir)
        self.assertEqual(second, first)
        self.assertLessEqual(len(self.hashed), 4)

    def test_cache_is_persisted_per_run_in_the_documented_shape(self):
        S.changed_files(self.proj, self.rdir)
        cache = json.loads((self.rdir / "snapshot" / "stat-cache.json").read_text(encoding="utf-8"))
        entry = cache["pkg/d1/f1.txt"]
        self.assertEqual(len(entry), 3)
        self.assertEqual(entry[1], len("content 1\n"))
        self.assertEqual(entry[2], util.sha256_bytes(b"content 1\n"))

    def test_same_size_edit_with_a_new_mtime_is_detected(self):
        self.edit("pkg/d3/f3.txt", "content X\n")                    # same length as "content 3\n"
        self.assertEqual([i["path"] for i in S.changed_files(self.proj, self.rdir)], ["pkg/d3/f3.txt"])

    def test_files_the_tools_touched_are_always_rehashed_even_with_a_restored_mtime(self):
        old = time.time() - 3600
        S.record_touch(self.proj, self.rdir, self.proj / "pkg/d4/f4.txt")
        self.edit("pkg/d4/f4.txt", "content Z\n", mtime=old)         # same size, mtime put back
        self.assertEqual([i["path"] for i in S.changed_files(self.proj, self.rdir)], ["pkg/d4/f4.txt"])

    def test_test_files_are_always_rehashed(self):
        old = time.time() - 3600
        self.edit("tests/test_calc.py", "x" * len((self.proj / "tests/test_calc.py").read_text()), mtime=old)
        self.assertEqual([i["path"] for i in S.changed_files(self.proj, self.rdir)], ["tests/test_calc.py"])

    def test_a_racy_entry_is_rehashed_so_a_same_size_same_mtime_edit_cannot_hide(self):
        p = self.proj / "pkg/d7/f7.txt"
        self.edit("pkg/d7/f7.txt", "changed 7\n")                    # same size as "content 7\n"
        stamp = p.stat().st_mtime_ns
        self.assertEqual([i["path"] for i in S.changed_files(self.proj, self.rdir)], ["pkg/d7/f7.txt"])
        p.write_text("content 7\n")                                  # back to the baseline text ...
        os.utime(p, ns=(stamp, stamp))                               # ... with the very same size and mtime
        self.assertEqual(S.changed_files(self.proj, self.rdir), [],
                         "an entry written within 2 s of its mtime is a guess, not a fact")

    def test_a_scan_that_finds_nothing_new_does_not_rewrite_the_cache(self):
        cache = self.rdir / "snapshot" / "stat-cache.json"
        before = cache.stat().st_mtime_ns
        time.sleep(0.05)
        self.assertEqual(S.changed_files(self.proj, self.rdir), [])
        self.assertEqual(S.changed_files(self.proj, self.rdir), [])
        self.assertEqual(cache.stat().st_mtime_ns, before, "test files are re-read every time, but nothing is rewritten")

    def test_a_damaged_cache_is_ignored(self):
        S.changed_files(self.proj, self.rdir)
        (self.rdir / "snapshot" / "stat-cache.json").write_text("{broken", encoding="utf-8")
        self.edit("pkg/d6/f6.txt", "changed 6\n")
        self.assertEqual([i["path"] for i in S.changed_files(self.proj, self.rdir)], ["pkg/d6/f6.txt"])

    def test_the_scan_cap_still_marks_the_manifest_incomplete(self):
        with mock.patch.object(S, "MAX_SCAN_FILES", 25):
            cached = S._scan_manifest(self.proj, self.rdir / "snapshot" / "stat-cache.json")
            plain = S._scan_manifest(self.proj)
        self.assertIn("\0incomplete", cached)
        self.assertIn("\0incomplete", plain)
        self.assertEqual(len(cached), len(plain))


# ---------------------------------------------------------------- (g) bounded stress

STRESS = '''
import json, os, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from orkestra_core import activity, paths, snapshot, state
cfg = json.loads(sys.argv[2])
project = Path(cfg["project"]); rid = cfg["rid"]; wid = cfg["wid"]
rdir = paths.run_dir(project, rid)
Path(cfg["ready"]).write_text("")
go = Path(cfg["go"])
while not go.exists():
    time.sleep(0.001)
deadline = time.monotonic() + cfg["seconds"]
done = 0
worst = 0.0
while time.monotonic() < deadline and done < cfg["cap"]:
    i = done
    t0 = time.monotonic()
    with state.StateTxn(project, rid) as st:
        st["counter"] = st.get("counter", 0) + 1
        st.setdefault("marks", []).append("%d-%d" % (wid, i))
    state.ledger(project, rid, "w%d i%d" % (wid, i))
    f = project / "work" / ("f-%d-%d.txt" % (wid, i))
    f.write_text("x")
    snapshot.backup_before_edit(project, rdir, f)
    snapshot.record_touch(project, rdir, f)
    activity.record(project, rid, {"tool_name": "Bash", "tool_input": {"command": "echo %d-%d" % (wid, i)},
                                   "session_id": "s%d" % wid}, "tool")
    state.set_active_run(Path("/stress/p%d" % wid), rid)
    worst = max(worst, time.monotonic() - t0)
    done += 1
print(json.dumps({"done": done, "worst": worst}))
'''


class TestStress(Base):
    WORKERS = 16
    SECONDS = 4.0
    CAP = 20

    def test_sixteen_processes_hammer_one_run_and_nothing_is_lost(self):
        self.make_run()
        (self.proj / "work").mkdir()
        sync = self.tmp / "sync"
        sync.mkdir()
        script = self.tmp / "stress.py"
        script.write_text(STRESS, encoding="utf-8")
        procs = []
        for wid in range(self.WORKERS):
            cfg = {"wid": wid, "project": str(self.proj), "rid": self.rid, "go": str(sync / "go"),
                   "ready": str(sync / f"ready-{wid}"), "seconds": self.SECONDS, "cap": self.CAP}
            procs.append(subprocess.Popen([sys.executable, str(script), LIB, json.dumps(cfg)], stdout=subprocess.PIPE,
                                          stderr=subprocess.PIPE, text=True))
        started = time.monotonic()
        while len(list(sync.glob("ready-*"))) < self.WORKERS and time.monotonic() - started < 30:
            time.sleep(0.01)
        (sync / "go").write_text("")
        began = time.monotonic()
        results = []
        try:
            for proc in procs:
                out, err = proc.communicate(timeout=60)
                self.assertEqual(proc.returncode, 0, err)
                results.append(json.loads(out.strip().splitlines()[-1]))
        finally:
            for proc in procs:
                if proc.poll() is None:
                    proc.kill()
        elapsed = time.monotonic() - began
        total = sum(r["done"] for r in results)
        self.assertGreater(total, self.WORKERS, "the workers did real work")
        self.assertLess(elapsed, 55, "no deadlock, no convoy")
        self.assertLess(max(r["worst"] for r in results), 8.0, "max wait is bounded")

        st = ST.load_state(self.proj, self.rid)
        self.assertEqual(st["counter"], total, "state transitions")
        self.assertEqual(len(st["marks"]), total)
        self.assertEqual(len(set(st["marks"])), total)

        ledger = (self.fdir / "ledger.md").read_text(encoding="utf-8").splitlines()
        entries = [x for x in ledger if x.startswith("- ")]
        self.assertEqual(len(entries), total, "ledger lines")
        self.assertEqual(len([x for x in ledger if x.startswith("# Ledger")]), 1)
        self.assertTrue(all(re.match(r"^- \d{4}-\d\d-\d\d \d\d:\d\d w\d+ i\d+$", x) for x in entries))

        edits = json.loads((self.rdir / "snapshot" / "edits.json").read_text(encoding="utf-8"))
        self.assertEqual(len(edits), total, "edits manifest")
        touched = json.loads((self.rdir / "snapshot" / "touched.json").read_text(encoding="utf-8"))
        self.assertEqual(len(touched), total, "touched manifest")
        self.assertEqual(len(set(touched)), total)

        rows = [json.loads(x) for x in (self.rdir / "activity.jsonl").read_text(encoding="utf-8").splitlines() if x]
        self.assertEqual(len(rows), total, "activity rows")
        self.assertEqual(sorted(ST.active_runs()), sorted(f"/stress/p{i}" for i in range(self.WORKERS)))


# ---------------------------------------------------------------- hooks degrade instead of hanging

@unittest.skipUnless(os.name != "nt" and util.fcntl is not None, "POSIX flow fixtures")
class TestHooksUnderALockedManifest(HolderMixin, FlowBase):
    def test_post_tool_write_does_not_hang_behind_a_frozen_manifest_lock(self):
        self.start(approval_mode="autopilot")
        rdir = self.proj / ".orkestra" / "runs" / self.rid
        target = rdir / "snapshot" / "touched.json"
        proc = self.start_holder(target, 60)
        os.kill(proc.pid, signal.SIGSTOP)
        started = time.monotonic()
        payload = {"hook_event_name": "PostToolUse", "session_id": self.sid, "tool_name": "Write",
                   "cwd": str(self.proj), "tool_input": {"file_path": str(self.proj / "calc.py")}}
        subprocess.run([sys.executable, str(helpers.DISPATCH), "PostToolUse"], input=json.dumps(payload),
                       capture_output=True, text=True, env=dict(os.environ), timeout=30)   # a hang fails, it never blocks
        waited = time.monotonic() - started
        self.assertLess(waited, 6.0, "the hook gives up on the lock after its budget instead of waiting for the holder")
        touched = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("calc.py", touched, "the touch is still recorded")
        log = (paths.logs_dir() / "hooks.log").read_text(encoding="utf-8")
        self.assertIn("without the lock", log)


    def test_stop_hook_answers_within_its_budget_when_the_state_lock_is_frozen(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.drive_turn(prompt="devam et", prompt_id="p9")
        event = {"hook_event_name": "Stop", "session_id": self.sid, "prompt_id": "p9", "stop_hook_active": False,
                 "cwd": str(self.proj)}
        proc = self.start_holder(self.proj / ".orkestra" / "runs" / self.rid / "state.json", 60)
        os.kill(proc.pid, signal.SIGSTOP)
        started = time.monotonic()
        cp = subprocess.run([sys.executable, str(helpers.DISPATCH), "Stop"], input=json.dumps(event), capture_output=True,
                            text=True, env=dict(os.environ), timeout=30)
        waited = time.monotonic() - started
        self.assertEqual(cp.returncode, 0, "a hook never fails the host call")
        self.assertLess(waited, 6.0, "the Stop hook gave up on the lock after its budget")
        self.assertEqual(cp.stdout.strip(), "", "no nudge was decided without the state")
        log = (paths.logs_dir() / "hooks.log").read_text(encoding="utf-8")
        self.assertRegex(log, r"ERROR in Stop: .*state\.json")
        self.assertIn(str(proc.pid), log, "the log line names the holder")
        proc.kill()
        proc.wait()
        self.assertEqual(self.hook("Stop", event).get("decision"), "block", "the unspent nudge is still there afterwards")


if __name__ == "__main__":
    unittest.main()
