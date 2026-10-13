"""Bridge turns are bounded and supervised: a wall-clock budget, an activity cap, process groups the bridge started,
no second driver after a restart, and notification retries that back off and stop. In-process against the real module
with a fake agent that sleeps (no network, no ~/.orkestra)."""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
BRIDGE_DIR = HERE.parent
sys.path.insert(0, str(BRIDGE_DIR))
_import_home = tempfile.mkdtemp(prefix="bridge-turns-import-")
with mock.patch.dict(os.environ, {"ORKESTRA_HOME": _import_home, "ORKESTRA_NO_DAEMON": "1"}):
    import orkestra_bridge as B
    from orkestra_core import i18n
    from orkestra_core.i18n import t
shutil.rmtree(_import_home, ignore_errors=True)

SLEEPY = """#!/usr/bin/env python3
import sys, time
sys.stdin.read()
time.sleep(120)
"""

STUBBORN = """#!/usr/bin/env python3
import os, signal, subprocess, sys, time
signal.signal(signal.SIGINT, signal.SIG_IGN)
sys.stdin.read()
kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kid.pid"), "w").write(str(kid.pid))
time.sleep(120)
"""

QUICK = """#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps({"type": "result", "result": "done for now", "session_id": "s"}))
"""


LEAKY = """#!/usr/bin/env python3
import json, os, subprocess, sys
sys.stdin.read()
kid = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], start_new_session=True)
open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kid.pid"), "w").write(str(kid.pid))
print(json.dumps({"type": "result", "result": "done for now", "session_id": "s"}))
"""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:                                     # a zombie of ours counts as gone
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    except OSError:
        return True
    return bool(out) and not out.startswith("Z")


def wait_for(fn, timeout=20.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(step)
    return fn()


class TurnBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="bridge-turns-")).resolve()
        self.home = self.tmp / "home"
        self.ws = self.tmp / "Projects"
        self.proj = self.ws / "demo"
        self.proj.mkdir(parents=True)
        self.home.mkdir()
        self.env = mock.patch.dict(os.environ, {"ORKESTRA_HOME": str(self.home), "ORKESTRA_WORKSPACE": str(self.ws),
                                                "ORKESTRA_LANG": "en", "ORKESTRA_NO_DAEMON": "1"})
        self.env.start()
        self.patches = [
            mock.patch.object(B, "HOME", self.home), mock.patch.object(B, "CONFIG_PATH", self.home / "b.json"),
            mock.patch.object(B, "JOBS_DIR", self.home / "bridge" / "jobs"),
            mock.patch.object(B, "LOG_PATH", self.home / "logs" / "bridge.log"),
            mock.patch.object(B, "SUPERVISE_TICK", 0.1), mock.patch.object(B, "KILL_GRACE", 0.6),
            mock.patch.object(B, "latest_run_state", lambda project, since: {}),
            mock.patch.object(B, "watchdog_cfg", lambda: {"auto_resume": "off", "max_auto_resumes": 3}),
        ]
        for p in self.patches:
            p.start()
        self.notified = []
        self.n = mock.patch.object(B, "notify", lambda cfg, job: self.notified.append(dict(job)))
        self.n.start()
        self.procs = []

    def tearDown(self):
        for proc in self.procs:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        for pid_file in self.tmp.rglob("kid.pid"):
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (OSError, ValueError):
                pass
        self.n.stop()
        for p in reversed(self.patches):
            p.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def agent(self, body: str) -> str:
        path = self.tmp / "agent.py"
        path.write_text(body)
        path.chmod(0o755)
        return str(path)

    def cfg(self, agent: str):
        return dict(B.DEFAULTS, claude_bin=agent, workspace=str(self.ws), auto_resume_delays=[0, 0, 0])

    def job(self, **kw):
        job = {"id": uuid.uuid4().hex[:12], "project": "demo", "project_path": str(self.proj), "chat_id": None,
               "session_id": str(uuid.uuid4()), "status": "queued", "created": B.now(), "turns": [],
               "last_message": "", "engine": "claude", "approval_mode": "autopilot"}
        job.update(kw)
        B.save_job(job)
        return job


class TestTurnBudget(TurnBase):
    def test_wall_clock_budget_interrupts_the_turn_and_hands_the_job_back(self):
        agent = self.agent(SLEEPY)
        job = self.job()
        with mock.patch.object(B, "turn_limits", lambda: (1.5, 0.0)):
            B.run_turn(self.cfg(agent), job, "go", new=True)
            running = wait_for(lambda: (B.load_job(job["id"]) or {}).get("driver"))
            self.assertTrue(running, "pid/pgid are persisted while the turn runs")
            self.assertEqual(running["pid"], running["pgid"])               # its own session / process group
            pid = running["pid"]
            fresh = wait_for(lambda: (B.load_job(job["id"]) or {}).get("status") == "awaiting_user" and B.load_job(job["id"]))
        self.assertTrue(fresh, "the stalled job re-enters the existing ladder (auto-resume off: back to the user)")
        self.assertIn(t("watchdog.turn_timeout", seconds=1), fresh["last_message"])
        self.assertNotIn("driver", fresh)
        self.assertFalse(alive(pid))
        self.assertEqual(len(self.notified), 1)

    def test_a_child_that_ignores_sigint_and_its_children_are_killed_after_the_grace(self):
        agent = self.agent(STUBBORN)
        job = self.job()
        started = time.time()
        with mock.patch.object(B, "turn_limits", lambda: (1.0, 0.0)):
            B.run_turn(self.cfg(agent), job, "go", new=True)
            fresh = wait_for(lambda: (B.load_job(job["id"]) or {}).get("status") == "awaiting_user" and B.load_job(job["id"]),
                             timeout=30)
        self.assertTrue(fresh)
        self.assertGreaterEqual(time.time() - started, 1.0 + 0.5)              # SIGINT first, SIGKILL after the grace
        kid = int((self.tmp / "kid.pid").read_text())
        self.assertTrue(wait_for(lambda: not alive(kid), timeout=10), "the whole process group is gone")

    def test_activity_cap_interrupts_a_turn_with_no_run_activity_but_spares_a_running_command(self):
        runs = self.proj / ".orkestra" / "runs" / "001-x"
        runs.mkdir(parents=True)
        old = time.time() - 5000
        (runs / "state.json").write_text(json.dumps({"run_id": "001-x", "status": "active"}))
        os.utime(runs / "state.json", (old, old))
        agent = self.agent(SLEEPY)
        job = self.job()
        with mock.patch.object(B, "turn_limits", lambda: (0.0, 1.0)):
            B.run_turn(self.cfg(agent), job, "go", new=True)
            fresh = wait_for(lambda: (B.load_job(job["id"]) or {}).get("status") == "awaiting_user" and B.load_job(job["id"]),
                             timeout=20)
        self.assertTrue(fresh)
        self.assertIn(t("watchdog.turn_idle", seconds=1), fresh["last_message"])
        # a Bash call that is still open (a long gate) is not silence
        (runs / "activity.jsonl").write_text(json.dumps(
            {"ts": time.time() - 4000, "kind": "tool", "agent": "main", "tool": "Bash", "what": "make test"}) + "\n")
        os.utime(runs / "activity.jsonl", (old, old))
        job2 = self.job()
        with mock.patch.object(B, "turn_limits", lambda: (0.0, 1.0)):
            B.run_turn(self.cfg(agent), job2, "go", new=True)
            time.sleep(2.5)
            self.assertEqual(B.load_job(job2["id"])["status"], "running")
            B.cancel_job(B.load_job(job2["id"]))
        self.assertTrue(wait_for(lambda: not alive(B.load_job(job2["id"]).get("driver", {}).get("pid", 0) or 0)))

    def test_no_budget_means_no_cap(self):
        agent = self.agent(QUICK)
        job = self.job()
        with mock.patch.object(B, "turn_limits", lambda: (0.0, 0.0)):
            B.run_turn(self.cfg(agent), job, "go", new=True)
            fresh = wait_for(lambda: (B.load_job(job["id"]) or {}).get("status") not in (None, "running", "queued")
                             and B.load_job(job["id"]))
        self.assertEqual(fresh["status"], "done")
        self.assertEqual(fresh["last_message"], "done for now")
        self.assertNotIn("driver", fresh)


    def test_an_exited_agent_whose_orphan_holds_the_pipe_ends_the_turn_normally(self):
        # DL-03: the leader exited, a setsid descendant keeps stdout open; the turn must finish within the drain wait
        agent = self.agent(LEAKY)
        job = self.job()
        started = time.time()
        with mock.patch.object(B, "turn_limits", lambda: (0.0, 0.0)), mock.patch.object(B, "DRAIN_WAIT", 0.5):
            B.run_turn(self.cfg(agent), job, "go", new=True)
            fresh = wait_for(lambda: (B.load_job(job["id"]) or {}).get("status") not in (None, "running", "queued")
                             and B.load_job(job["id"]), timeout=15)
        self.assertTrue(fresh, "the job never left 'running'")
        self.assertLess(time.time() - started, 12)
        self.assertEqual(fresh["status"], "done")
        self.assertEqual(fresh["last_message"], "done for now")
        self.assertNotIn("interrupted", fresh["turns"][-1])


class TestOneDriver(TurnBase):
    def orphan(self, sid):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "--session-id", sid],
                                start_new_session=True)
        self.procs.append(proc)
        return proc

    def test_reply_is_refused_while_the_previous_driver_still_runs(self):
        sid = str(uuid.uuid4())
        proc = self.orphan(sid)
        job = self.job(session_id=sid, status="awaiting_user", driver={"pid": proc.pid, "pgid": proc.pid, "started": time.time()})
        with mock.patch.object(B, "run_turn") as run:
            with self.assertRaises(ValueError) as cm:
                B.reply_job(self.cfg("claude"), job, "go on")
            run.assert_not_called()
        self.assertIn(str(proc.pid), str(cm.exception))
        self.assertIn("still running", str(cm.exception))
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=5)
        with mock.patch.object(B, "run_turn") as run:
            B.reply_job(self.cfg("claude"), job, "go on")
            run.assert_called_once()

    def test_a_reused_pid_of_some_other_program_is_not_our_driver(self):
        proc = self.orphan("not-our-session")
        job = self.job(status="awaiting_user", driver={"pid": proc.pid, "pgid": proc.pid, "started": time.time()})
        self.assertEqual(B.driver_pid(job), 0)

    def test_restart_keeps_a_live_driver_and_forgets_a_dead_one(self):
        sid = str(uuid.uuid4())
        proc = self.orphan(sid)
        live = self.job(session_id=sid, status="running", driver={"pid": proc.pid, "pgid": proc.pid, "started": time.time()})
        dead = self.job(status="running", driver={"pid": 2 ** 22 + 77, "pgid": 2 ** 22 + 77, "started": time.time()})
        queued = self.job(status="queued")
        B.recover_jobs()
        for jid, keep in ((live["id"], True), (dead["id"], False), (queued["id"], False)):
            j = B.load_job(jid)
            self.assertEqual(j["status"], "awaiting_user")
            self.assertEqual("driver" in j, keep, jid)

    def test_cancel_after_a_restart_kills_the_saved_process_group(self):
        sid = str(uuid.uuid4())
        proc = self.orphan(sid)
        job = self.job(session_id=sid, status="awaiting_user", driver={"pid": proc.pid, "pgid": proc.pid, "started": time.time()})
        B.cancel_job(job)
        self.assertEqual(B.load_job(job["id"])["status"], "cancelled")
        self.assertTrue(wait_for(lambda: proc.poll() is not None, timeout=10))
        self.assertNotIn("driver", B.load_job(job["id"]))

    def test_never_signals_the_bridges_own_group(self):
        job = self.job(status="awaiting_user", driver={"pid": os.getpid(), "pgid": os.getpgrp(), "started": time.time()})
        with mock.patch("os.killpg", side_effect=AssertionError("killpg of our own group")):
            B.cancel_job(job)                                  # the pid is ours: it does not even carry the session id
        self.assertEqual(B.load_job(job["id"])["status"], "cancelled")


class TestLocksAndTimers(TurnBase):
    def test_auto_resume_prepares_its_turn_outside_the_global_lock(self):
        job = self.job(status="running")
        seen = []

        def prepare(cfg, j, prompt, new, said=None):
            seen.append(B._lock._is_owned())
            return None

        with mock.patch.object(B, "_prepare_turn", prepare):
            B.resume_now(self.cfg("claude"), job["id"])
        self.assertEqual(seen, [False])

    def test_auto_resume_timer_is_a_daemon(self):
        made = []

        class FakeTimer:
            def __init__(self, delay, fn):
                self.daemon = False
                made.append(self)

            def start(self):
                pass

        agent = self.agent(QUICK)
        job = self.job()
        with mock.patch.object(B, "watchdog_cfg", lambda: {"auto_resume": "headless", "max_auto_resumes": 3}), \
                mock.patch.object(B, "latest_run_state", lambda p, s: {"status": "active", "run_id": "001-x", "phase": "implement"}), \
                mock.patch.object(B.threading, "Timer", FakeTimer):
            B.run_turn(self.cfg(agent), job, "go", new=True)
            self.assertTrue(wait_for(lambda: made))
        self.assertTrue(made[0].daemon)


class TestNotificationRetries(TurnBase):
    def setUp(self):
        super().setUp()
        self.delays = mock.patch.object(B, "NOTIFY_RETRY_DELAYS", (0.0, 0.0, 0.0))
        self.delays.start()

    def tearDown(self):
        self.delays.stop()
        super().tearDown()

    def test_a_dead_endpoint_is_tried_a_bounded_number_of_times(self):
        n = []
        self.n.stop()
        try:
            job = self.job(status="done")
            with mock.patch.object(B, "post_json", side_effect=lambda *a, **k: n.append(1) or (_ for _ in ()).throw(OSError("down"))):
                B.notify(dict(self.cfg("x"), callback_url="http://127.0.0.1:9/cb"), job)
                self.assertTrue(wait_for(lambda: len(n) >= 4))
                time.sleep(0.5)
            self.assertEqual(len(n), 4)                          # first try + 3 retries, then given up
            self.assertIn("giving up", (self.home / "logs" / "bridge.log").read_text())
        finally:
            self.n.start()

    def test_a_retry_that_succeeds_stops_the_chain(self):
        n = []

        def flaky(*a, **k):
            n.append(1)
            if len(n) < 2:
                raise OSError("down")
            return {}

        self.n.stop()
        try:
            job = self.job(status="done")
            with mock.patch.object(B, "post_json", side_effect=flaky):
                B.notify(dict(self.cfg("x"), callback_url="http://127.0.0.1:9/cb"), job)
                self.assertTrue(wait_for(lambda: len(n) >= 2))
                time.sleep(0.4)
            self.assertEqual(len(n), 2)
        finally:
            self.n.start()

    def test_telegram_chunks_retry_independently(self):
        calls = []

        def post(url, payload, **kw):
            calls.append(payload["text"][:3])
            if len(calls) == 1:
                raise OSError("blip")
            return {}

        text = "a" * B.TG_MAX + "b" * 10
        with mock.patch.object(B, "post_json", side_effect=post):
            B.send_telegram({"telegram": {"bot_token": "T"}}, "42", text)
            self.assertTrue(wait_for(lambda: len(calls) >= 3))
        self.assertEqual(sorted(calls), ["aaa", "aaa", "bbb"])      # chunk 1 retried once, chunk 2 delivered


if __name__ == "__main__":
    unittest.main()
