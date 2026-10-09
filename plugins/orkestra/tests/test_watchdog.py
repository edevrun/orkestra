"""`orkestra watchdog`: classify from outside the session, notify once, resume only when allowed and never twice."""
import json
import os
import stat
import sys
import time
import unittest
from pathlib import Path

import helpers  # noqa: F401
from test_flow import FlowBase
from orkestra_core import watchdog as W

FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
open(os.path.join(os.getcwd(), "resumed.json"), "w").write(json.dumps(
    {"args": args, "prompt": prompt, "headless": os.environ.get("ORKESTRA_HEADLESS")}))
print(json.dumps({"type": "result", "result": "ok"}))
"""


def view(cls=4, mode="headless", age=900, since=900):
    return {"project": "/w/p", "project_name": "p", "run_id": "001-x", "mode": mode, "phase": "implement",
            "stall": {"cls": cls, "key": "silent", "since": since, "text": "sessiz"},
            "sessions": [{"id": "sess-1", "age": age}]}


def cfg(**wd):
    base = {"auto_resume": "headless", "max_auto_resumes": 3, "lease_seconds": 120}
    base.update(wd)
    return {"watchdog": base, "headless": {"permission_mode": "auto", "max_turns": 5}}


class TestResumeDecision(unittest.TestCase):
    def test_table(self):
        now = 10_000.0
        ok = W.resume_decision(view(), cfg(), {}, now)
        self.assertTrue(ok["resume"])
        self.assertEqual((ok["session"], ok["attempt"]), ("sess-1", 1))
        cases = [
            (view(cls=2), cfg(), {}, "not a silent session"),                       # only silent sessions are resumed
            (view(cls=1), cfg(), {}, "not a silent session"),                       # never answer for the user
            (view(), cfg(auto_resume="off"), {}, "auto_resume=off"),
            (view(mode="interactive"), cfg(), {}, "notification only"),             # interactive needs auto_resume=all
            (view(age=30), cfg(), {}, "heartbeat"),                                 # the session is alive
            (dict(view(), sessions=[]), cfg(), {}, "no bound session"),
            (view(), cfg(), {"resumes": {"/w/p|001-x": {"count": 3, "last": 0}}}, "limit reached"),
            (view(), cfg(), {"resumes": {"/w/p|001-x": {"count": 1, "last": now - 60}}}, "before attempt 2"),
        ]
        for v, c, book, why in cases:
            d = W.resume_decision(v, c, book, now)
            self.assertFalse(d["resume"], why)
            self.assertIn(why, d["why"])
        self.assertTrue(W.resume_decision(view(mode="interactive"), cfg(auto_resume="all"), {}, now)["resume"])
        later = {"resumes": {"/w/p|001-x": {"count": 1, "last": now - 16 * 60}}}
        self.assertEqual(W.resume_decision(view(), cfg(), later, now)["attempt"], 2)


class TestWatchdogScan(FlowBase):
    def setUp(self):
        super().setUp()
        self.fake = self.tmp / "claude"
        self.fake.write_text(FAKE_CLAUDE)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IEXEC)
        self._env = {k: os.environ.get(k) for k in ("ORKESTRA_CLAUDE_BIN",)}
        os.environ["ORKESTRA_CLAUDE_BIN"] = str(self.fake)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def config(self, **wd):
        base = {"macos_notify": False}
        base.update(wd)
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.json").write_text(json.dumps({"watchdog": base}))

    def start_headless(self):
        out = self.cli("start", "-p", "demo", "--title", "W", "--request", "r", "--size", "S", "--headless").stdout
        self.rid = out.split("run=")[-1].split()[0]
        self.fdir = self.proj / ".orkestra" / "features" / self.rid
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "Bash", "tool_input": {"command": "orkestra start"},
                                  "tool_response": {"stdout": out, "stderr": ""}, "cwd": str(self.proj)})

    def age_session(self, seconds):
        f = self.home / "sessions" / f"{self.sid}.json"
        os.utime(f, (time.time() - seconds, time.time() - seconds))

    def scan(self, in_future=0.0, **kw):
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            W.main(as_json=True, now=time.time() + in_future, **kw)
        return json.loads(buf.getvalue())

    def test_healthy_run_is_quiet_and_user_wait_is_announced_once(self):
        self.config()
        self.start()
        v = self.scan()[0]
        self.assertEqual((v["stall"]["cls"], v["notify"]), (0, False))
        self.cli("await", "-p", "demo", "Hangi renk?")
        self.assertFalse(self.scan(in_future=60)[0]["notify"])                 # not yet 5 minutes
        v = self.scan(in_future=400)[0]
        self.assertTrue(v["notify"])
        self.assertIn("SENİ BEKLİYOR", v["message"])
        self.assertIn("Hangi renk?", v["message"])
        self.assertFalse(self.scan(in_future=4000)[0]["notify"])               # a user wait is never repeated
        self.assertFalse(v["auto_resume"]["resume"])
        text = self.cli("watchdog", "--dry-run").stdout
        self.assertIn("demo", text)

    def test_silent_interactive_run_notifies_but_is_not_resumed(self):
        self.config()
        self.start()
        self.age_session(1200)
        v = self.scan(in_future=1200)[0]
        self.assertEqual(v["stall"]["cls"], 4)
        self.assertTrue(v["notify"])
        self.assertIn("SESSİZ OTURUM", v["message"])
        self.assertIn("notification only", v["auto_resume"]["why"])
        self.assertFalse((self.proj / "resumed.json").exists())
        self.assertFalse(self.scan(in_future=1300)[0]["notify"])               # same incident: not repeated yet
        self.assertTrue(self.scan(in_future=1200 + 31 * 60)[0]["notify"])      # …but repeated after renotify_minutes

    def test_silent_headless_run_is_resumed_on_its_own_session_with_backoff_and_cap(self):
        self.config(max_auto_resumes=2)
        self.start_headless()
        self.age_session(1200)
        self.assertFalse(self.scan(in_future=1200, dry_run=True)[0]["auto_resume"].get("limit"))
        self.assertFalse((self.proj / "resumed.json").exists())                # dry run starts nothing
        v = self.scan(in_future=1200)[0]
        self.assertIn("otomatik devam ettirildi (deneme 1)", v["message"])
        for _ in range(50):
            if (self.proj / "resumed.json").exists():
                break
            time.sleep(0.1)
        got = json.loads((self.proj / "resumed.json").read_text())
        self.assertEqual(got["args"][:3], ["-p", "--resume", self.sid])        # the SAME session, never a new one
        self.assertEqual(got["prompt"], "/orkestra:resume demo")
        self.assertEqual(got["headless"], "1")
        self.assertIn("watchdog auto-resume #1 (session sess-1", (self.fdir / "ledger.md").read_text())
        # immediately afterwards: back-off (5 min) — nothing is started twice
        v = self.scan(in_future=1260)[0]
        self.assertFalse(v["auto_resume"]["resume"])
        self.assertIn("before attempt 2", v["auto_resume"]["why"])
        v = self.scan(in_future=1200 + 6 * 60)[0]
        self.assertIn("deneme 2", v["message"])
        v = self.scan(in_future=1200 + 60 * 60)[0]                             # cap reached → the user is told
        self.assertFalse(v["auto_resume"]["resume"])
        self.assertIn("otomatik devam sınırı aşıldı", v["message"])
        self.assertEqual((self.fdir / "ledger.md").read_text().count("watchdog auto-resume"), 2)

    def test_never_resumes_what_the_bridge_or_a_live_session_owns(self):
        self.config()
        self.start_headless()
        v = self.scan(in_future=1200)[0]                                       # heartbeat is fresh → lease holds
        self.assertIn("heartbeat", v["auto_resume"]["why"])
        self.age_session(1200)
        jobs = self.home / "bridge" / "jobs"
        jobs.mkdir(parents=True)
        (jobs / "abc.json").write_text(json.dumps({"id": "abc", "session_id": self.sid, "status": "running"}))
        v = self.scan(in_future=1200)[0]
        self.assertIn("still alive", v["auto_resume"]["why"])
        self.assertFalse((self.proj / "resumed.json").exists())

    def test_off_means_off(self):
        self.config(auto_resume="off")
        self.start_headless()
        self.age_session(1200)
        v = self.scan(in_future=1200)[0]
        self.assertIn("auto_resume=off", v["auto_resume"]["why"])
        self.assertFalse((self.proj / "resumed.json").exists())


if __name__ == "__main__":
    unittest.main()
