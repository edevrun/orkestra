"""Activity feed (hooks → activity.jsonl) and the read-only monitor (watch / dashboard)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import helpers
from test_flow import FlowBase

from orkestra_core import activity as A
from orkestra_core import monitor as MON


class ActivityUnit(unittest.TestCase):
    def test_dashboard_compacts_repeated_bash_denials_without_hiding_distinct_reasons(self):
        rows = [
            {"ts": 1000, "agent": "main", "kind": "deny", "tool": "Bash", "what": "internals"},
            {"ts": 1010, "agent": "main", "kind": "deny", "tool": "Bash", "what": "internals"},
            {"ts": 1020, "agent": "main", "kind": "tool", "tool": "Read", "what": "file.py"},
            {"ts": 1030, "agent": "main", "kind": "deny", "tool": "Bash", "what": "roles"},
        ]
        feed = MON._activity_feed(rows, 10)
        denials = [item for item in feed if item["kind"] == "deny"]
        self.assertEqual(len(feed), 3)
        self.assertEqual(len(denials), 2)
        repeated = next(item for item in denials if item["what"] == "internals")
        self.assertEqual(repeated["repeat_count"], 2)

    def test_liveness_levels(self):
        now = 10_000.0
        st = {"status": "active", "phase": "implement"}
        self.assertEqual(A.liveness(dict(st, status="awaiting_user", awaiting={"question": "Renk?"}), [], now)["level"],
                         "wait")
        self.assertEqual(A.liveness(dict(st, status="blocked", blocked={"reason": "x"}), [], now)["level"], "blocked")
        self.assertEqual(A.liveness(dict(st, status="done"), [], now)["level"], "done")
        q = [{"ts": now - 5, "kind": "tool", "tool": "AskUserQuestion", "what": "soru: Kapsam"}]
        self.assertIn("soru açık", A.liveness(st, q, now)["text"])
        answered = q + [{"ts": now - 2, "kind": "answer", "tool": "AskUserQuestion", "what": "Evet"}]
        self.assertEqual(A.liveness(st, answered, now)["level"], "run")
        idle = [{"ts": now - 30, "kind": "idle", "tool": "", "what": "tur bitti"}]
        self.assertIn("turu bitirdi", A.liveness(st, idle, now)["text"])
        resumed_live = A.liveness(st, idle, now, state_mtime=now - 5)
        self.assertEqual(resumed_live["level"], "run")
        self.assertIn("5 sn önce güncellendi", resumed_live["text"])
        old_hook = [{"ts": now - 27_000, "kind": "end", "tool": "Bash", "what": "✓"}]
        recent_state = A.liveness(dict(st, phase="plan"), old_hook, now, state_mtime=now - 5)
        self.assertEqual(recent_state["level"], "run")
        self.assertIn("plan", recent_state["text"])
        self.assertIn("7 sa 30 dk", recent_state["text"])
        bash = [{"ts": now - 300, "kind": "tool", "tool": "Bash", "what": "npm test"}]
        lv = A.liveness(st, bash, now)
        self.assertEqual(lv["level"], "run")
        self.assertIn("komut çalışıyor", lv["text"])
        ended = bash + [{"ts": now - 290, "kind": "end", "tool": "Bash", "what": "✓"}]
        self.assertEqual(A.liveness(st, ended, now)["level"], "quiet")
        self.assertEqual(A.liveness(st, [{"ts": now - 4000, "kind": "end", "tool": "Bash", "what": "✓"}], now)["level"],
                         "stale")
        # clarify/approve with nothing happening → most likely a question/approval is open in the Claude window
        lv = A.liveness(dict(st, phase="clarify"), [], now, state_mtime=now - 600)
        self.assertEqual(lv["level"], "wait")
        self.assertIn("muhtemelen", lv["text"])
        self.assertEqual(A.liveness(dict(st, phase="clarify"), [{"ts": now - 5, "kind": "tool", "tool": "Read",
                                                                  "what": "x"}], now)["level"], "run")

    def test_running_agents(self):
        rows = [{"kind": "tool", "tool": "Agent", "what": "→ orkestra:reviewer: review"},
                {"kind": "tool", "tool": "Agent", "what": "→ orkestra:adversary: attack"},
                {"kind": "result", "tool": "Agent", "what": "← orkestra:reviewer: VERDICT: PASS"}]
        self.assertEqual(A.running_agents(rows), ["orkestra:adversary"])

    def test_mask(self):
        self.assertNotIn("abc123", A._mask("curl -H 'Authorization: Bearer abc123' x"))
        self.assertNotIn("hunter2", A._mask("export PASSWORD=hunter2"))


class ActivityHooks(FlowBase):
    def rows(self):
        p = self.proj / ".orkestra" / "runs" / self.rid / "activity.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def test_hooks_write_feed_and_monitor_reads_it(self):
        self.start()
        kinds = [r["kind"] for r in self.rows()]
        self.assertIn("bind", kinds)
        base = {"session_id": self.sid, "cwd": str(self.proj), "prompt_id": "p1"}
        self.hook("PreToolUse", dict(base, tool_name="Read", tool_input={"file_path": str(self.proj / "calc.py")}))
        self.hook("PreToolUse", dict(base, tool_name="Agent", tool_input={"subagent_type": "orkestra:scout",
                                                                          "description": "map repo", "prompt": "x"}))
        self.hook("PreToolUse", dict(base, tool_name="Grep", agent_type="orkestra:scout", agent_id="s1",
                                     tool_input={"pattern": "def add", "path": str(self.proj)}))
        self.hook("PostToolUse", dict(base, tool_name="Agent", tool_input={"subagent_type": "orkestra:scout"},
                                      tool_response={"content": [{"type": "text", "text": "Found 2 files\nSTATUS: DONE"}]}))
        # source write before approval → denied, and the denial is in the feed
        out = self.hook("PreToolUse", dict(base, tool_name="Write",
                                           tool_input={"file_path": str(self.proj / "calc.py"), "content": "x"}))
        self.assertTrue(self.denied(out))
        self.hook("PreToolUse", dict(base, tool_name="TaskUpdate", tool_input={"taskId": "1"}))   # noise: skipped
        self.hook("PreToolUse", dict(base, tool_name="AskUserQuestion",
                                     tool_input={"questions": [{"header": "Kapsam", "question": "Hangisi?"}]}))
        rows = self.rows()
        by_kind = {}
        for r in rows:
            by_kind.setdefault(r["kind"], []).append(r)
        self.assertTrue(any(r["what"] == "calc.py" for r in by_kind["tool"]))
        self.assertTrue(any(r["agent"] == "orkestra:scout" and r["tool"] == "Grep" for r in by_kind["tool"]))
        self.assertIn("STATUS: DONE", by_kind["result"][0]["what"])
        self.assertIn("phase", by_kind["deny"][0]["what"])
        self.assertFalse(any(r["tool"] == "TaskUpdate" for r in rows))

        views = MON.discover()
        self.assertEqual(len(views), 1)
        v = views[0]
        self.assertEqual(v["run_id"], self.rid)
        self.assertEqual(v["live"]["level"], "wait")              # question open
        self.assertEqual(len(v["sessions"]), 1)
        text = MON.render_text(views, color=False, width=120)
        self.assertIn("SENİ BEKLİYOR", text)
        self.assertIn(self.rid, text)
        for _ in range(3):
            self.hook("PreToolUse", dict(base, tool_name="Bash",
                                          tool_input={"command": f"python3 {helpers.PLUGIN}/hooks/dispatch.py Stop"}))
        compacted = MON.discover()[0]["activity"]
        bash_denials = [row for row in compacted if row["kind"] == "deny" and row["tool"] == "Bash"]
        self.assertEqual(len(bash_denials), 1)
        self.assertEqual(bash_denials[0]["repeat_count"], 3)
        summary = MON.render_text(MON.discover(), color=False, width=120)
        self.assertIn("Koruma ×3", summary)
        self.assertNotIn("deny Bash", summary)

        # answer → running again; then the turn ends → waiting for the user
        self.hook("PostToolUse", dict(base, tool_name="AskUserQuestion",
                                      tool_input={"questions": [{"header": "Kapsam", "question": "Hangisi?"}]},
                                      tool_response={"answers": {"Hangisi?": "Sadece web"}}))
        self.assertEqual(MON.discover()[0]["live"]["level"], "run")
        self.hook("Stop", dict(base))                               # active run → nudge (blocked stop)
        self.hook("Stop", dict(base, stop_hook_active=True))        # continuation ended → idle
        self.assertIn("turu bitirdi", MON.discover()[0]["live"]["text"])
        self.hook("UserPromptSubmit", dict(base, prompt_id="p2", prompt="devam et"))
        self.assertEqual(self.rows()[-1]["kind"], "user")

    def test_cli_watch_once_and_json(self):
        self.start()
        cp = self.cli("watch", "--once")
        self.assertIn(self.rid, cp.stdout)
        data = json.loads(self.cli("watch", "--json").stdout)
        self.assertEqual(data[0]["phase"], "intake")
        cp = self.cli("watch", "--once", "-p", "demo")
        self.assertIn("demo", cp.stdout)

    def test_feed_rotation_is_bounded(self):
        self.start()
        ev = {"tool_name": "Bash", "tool_input": {"command": "echo " + "x" * 400}}
        for _ in range(1600):
            A.record(self.proj, self.rid, ev, "tool")
        p = self.proj / ".orkestra" / "runs" / self.rid / "activity.jsonl"
        self.assertLessEqual(p.stat().st_size, A.MAX_BYTES + 2048)
        self.assertEqual(len(A.tail(self.proj, self.rid, 5)), 5)


class Dashboard(FlowBase):
    def test_dashboard_serves_json_and_rejects_foreign_host(self):
        self.start()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        refused = self.cli("dashboard", "--port", str(port), check=False, env={"CLAUDECODE": "1"})
        self.assertEqual(refused.returncode, 2)                    # agents never start a blocking server
        proc = subprocess.Popen([sys.executable, str(helpers.BIN), "dashboard", "--port", str(port)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            url = f"http://127.0.0.1:{port}"
            for _ in range(50):
                try:
                    body = urllib.request.urlopen(url + "/api/runs", timeout=2).read()
                    break
                except (urllib.error.URLError, ConnectionError):
                    time.sleep(0.1)
            else:
                self.fail("dashboard did not start")
            self.assertEqual(json.loads(body)[0]["run_id"], self.rid)
            page = urllib.request.urlopen(url + "/", timeout=2).read().decode()
            self.assertIn("Orkestra", page)
            req = urllib.request.Request(url + "/api/runs", headers={"Host": "evil.example:80"})
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(req, timeout=2)
            self.assertEqual(cm.exception.code, 403)
        finally:
            proc.terminate()
            proc.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
