"""Wiring found by the integrator: foreground launches reach the launch ring, and the watchdog / monitor classify with
the due reports (otherwise `dead_wait` can never be seen from outside the session)."""
import json
import time
import unittest

import helpers  # noqa: F401
from orkestra_core import activity as A, watchdog as W
from test_hooks2 import Base


class TestIntegrateWiring(Base):
    def test_a_foreground_launch_enters_the_launch_ring(self):
        self.to_implement()
        ti = {"subagent_type": "orkestra:reviewer", "description": "Review T001", "prompt": "scope: task T001"}
        self.hook("PostToolUse", {"session_id": self.sid, "tool_name": "Agent", "tool_input": ti, "cwd": str(self.proj),
                                  "tool_response": {"content": [{"type": "text", "text": "STATUS: DONE"}]}})
        ring = A.launches(self.proj, self.rid)
        self.assertEqual([(x["type"], x["tasks"]) for x in ring], [("orkestra:reviewer", ["T001"])])

    def test_the_watchdog_sees_a_dead_wait_from_a_due_report(self):
        self.to_implement()
        now = time.time()
        A.agent_launched(self.proj, self.rid, {"id": "a1", "type": "orkestra:reviewer", "tasks": ["T001"]}, now=now + 900)
        A.agent_finished(self.proj, self.rid, "a1", "orkestra:reviewer", now=now + 1000)
        self.assertTrue(A.agents_reports_due(self.proj, self.rid, max_age=3600.0, now=now + 1300))
        views = W.scan(now=now + 1300)
        mine = [v for v in views if v["run_id"] == self.rid]
        self.assertTrue(mine)
        self.assertEqual(mine[0]["stall"]["key"], "dead_wait", json.dumps(mine[0]["stall"]))


if __name__ == "__main__":
    unittest.main()
