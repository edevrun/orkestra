"""Closed loops and token-free waiting: review-round caps (autopilot vs manual), launch ring + dispatch escalation,
`orkestra wait`, mode-aware text, the stall classifier (replay), the review packet, `task add --files`, trimmed output."""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from test_tempo import TempoBase
from orkestra_core import activity as A
from orkestra_core import cli as C
from orkestra_core import runs as R
from orkestra_core import tempo as T

PLUGIN = Path(helpers.PLUGIN)
ASK = "AskUserQuestion"


def autopilot(tc: TempoBase) -> None:
    R.set_approval_mode(tc.proj, tc.rid, "autopilot")


def launch(tc: TempoBase, agent_id: str, tasks=("T001",), kind: str = "orkestra:implementer") -> None:
    A.agent_launched(tc.proj, tc.rid, {"id": agent_id, "type": kind, "name": "", "desc": "x", "tasks": list(tasks)},
                     session=tc.sid)


# ------------------------------------------------------------------ (a) review-round cap

class TestCapAutopilot(TempoBase):
    def setUp(self):
        super().setUp()
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        self.review("T001", "FAIL")
        autopilot(self)

    def reopen(self, *extra):
        return self.cli("task", "reopen", "T001", *extra, "-p", "demo", check=False)

    def task(self):
        return json.loads(self.cli("status", "--json", "-p", "demo").stdout)["tasks"]["T001"]

    def test_third_reopen_blocks_the_task_and_names_the_exits(self):
        self.assertEqual(self.reopen("review round 1").returncode, 0)
        self.assertEqual(self.reopen("review round 2").returncode, 0)
        cp = self.reopen("review round 3")
        self.assertEqual(cp.returncode, 2)
        self.assertIn("task defer", cp.stderr)
        self.assertIn("orkestra block", cp.stderr)
        self.assertNotIn(ASK, cp.stderr)
        t = self.task()
        self.assertEqual(t["status"], "blocked")                         # converted, the question is on record
        self.assertTrue(str(t["notes"][-1]).startswith(T.CAP_BLOCK))
        self.assertEqual(T.reopen_count(t), 2)

    def test_force_cannot_lift_the_cap(self):
        self.reopen("review round 1")
        self.reopen("review round 2")
        for attempt in range(3):                                          # --force "alternate approach N" never works
            cp = self.reopen("--force", f"autopilot: alternate approach {attempt}")
            self.assertEqual(cp.returncode, 2, cp.stdout)
            self.assertIn("--force cannot lift", cp.stderr)
        self.assertEqual(T.reopen_count(self.task()), 2)

    def test_next_says_defer_or_block_never_ask(self):
        self.reopen("review round 1")
        self.reopen("review round 2")
        self.reopen("review round 3")
        out = self.cli("next", "-p", "demo").stdout
        self.assertIn("task defer", out)
        self.assertNotIn(ASK, out)
        self.assertEqual(self.nj()["action"], "cli")

    def test_start_is_refused_and_defer_finishes(self):
        for n in range(3):
            self.reopen(f"review round {n}")
        cp = self.cli("task", "start", "T001", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("task defer", cp.stderr)
        self.cli("task", "defer", "T001", "-p", "demo", "review failed 3 rounds")
        self.assertEqual(self.task()["status"], "deferred")


class TestCapManual(TempoBase):
    def setUp(self):
        super().setUp()
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        self.review("T001", "FAIL")

    def reopen(self, *extra):
        return self.cli("task", "reopen", "T001", *extra, "-p", "demo", check=False)

    def test_force_needs_a_human_row_newer_than_the_last_reopen(self):
        self.reopen("review round 1")
        self.reopen("review round 2")
        cp = self.reopen("--force", "kullanıcı: bir tur daha")           # text proves nothing
        self.assertEqual(cp.returncode, 2)
        self.assertIn("needs the user's ruling", cp.stderr)
        A.record(self.proj, self.rid, {"session_id": self.sid}, "answer", "devam: bir tur daha")
        self.assertEqual(self.reopen("--force", "kullanıcı: bir tur daha").returncode, 0)
        cp = self.reopen("--force", "yine")                              # the same ruling is not reused
        self.assertEqual(cp.returncode, 2)
        self.assertIn("needs the user's ruling", cp.stderr)
        A.record(self.proj, self.rid, {"session_id": self.sid}, "user", "tamam, bir tur daha")
        self.assertEqual(self.reopen("--force", "yine").returncode, 0)

    def test_manual_keeps_the_ask_text(self):
        self.reopen("review round 1")
        self.reopen("review round 2")
        self.assertIn(ASK, self.reopen("review round 3").stderr)

    def test_red_missing_reopen_is_not_a_review_round(self):
        # green without a failing-first run: the state `next` answers with `reopen --force "red evidence missing"`
        self.cli("task", "reopen", "T001", "-p", "demo", "--force", "bookkeeping")      # evidence reset (round 1 spent)
        self.cli("task", "start", "T001", "-p", "demo")
        self.cli("tdd", "green", "T001", "-p", "demo", "--", "python3 -m unittest tests.test_calc -q")
        self.assertEqual(self.nj()["step"], "green_no_red")
        self.assertEqual(self.reopen("--force", "red evidence missing").returncode, 0)
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)["tasks"]["T001"]
        self.assertTrue(str(st["notes"][-1]).startswith(T.RED_MISSING))
        self.assertEqual(T.reopen_count(st), 1)                          # only the first reopen counts


# ------------------------------------------------------------------ (b) launch ring + escalation

class TestLaunchRing(TempoBase):
    def test_ring_is_bounded_and_records_type_tasks_at(self):
        self.start()
        for i in range(210):
            A.record_launch(self.proj, self.rid, {"type": "orkestra:implementer", "tasks": ["T001"]}, now=1000.0 + i)
        ring = A.launches(self.proj, self.rid)
        self.assertEqual(len(ring), A.LAUNCH_RING)
        self.assertEqual(set(ring[0]), {"type", "tasks", "at"})
        self.assertEqual(ring[-1]["at"], 1209.0)

    def test_background_and_cursor_launches_are_recorded(self):
        self.start()
        A.agent_launched(self.proj, self.rid, {"id": "a1", "type": "orkestra:reviewer", "tasks": ["T002"]})
        A.cursor_launch(self.proj, self.rid, "s", "l1", "orkestra-implementer", "do T003 now", "")
        got = [(x["type"], x["tasks"]) for x in A.launches(self.proj, self.rid)]
        self.assertEqual(got, [("orkestra:reviewer", ["T002"]), ("orkestra-implementer", ["T003"])])

    def test_launch_from_event_describes_foreground_launches_too(self):
        info = A.launch_from_event({"tool_input": {"subagent_type": "orkestra:reviewer", "description": "review T004",
                                                   "prompt": "scope: task T004"}})
        self.assertEqual(info, {"type": "orkestra:reviewer", "tasks": ["T004"]})

    def _started_ts(self, tid="T001"):
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)
        return T._ts(st["tasks"][tid]["started"])

    def test_three_identical_dispatches_escalate_instead_of_repeating(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        t0 = self._started_ts()
        for i in range(2):
            A.record_launch(self.proj, self.rid, {"type": "orkestra:implementer", "tasks": ["T001"]}, now=t0 + 1 + i)
        self.assertEqual(self.nj()["action"], "dispatch")
        A.record_launch(self.proj, self.rid, {"type": "orkestra:implementer", "tasks": ["T001"]}, now=t0 + 3)
        n = self.nj()
        self.assertEqual(n["action"], "escalate")
        self.assertIn("task block T001", n["text"])
        self.assertNotIn("dispatch orkestra:implementer", n["text"])
        self.assertIn("orkestra block", n["text"])
        self.assertNotIn(ASK, n["text"])                                  # manual run, but the escalation is the same

    def test_other_tasks_launches_do_not_count(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        t0 = self._started_ts()
        for i in range(4):
            A.record_launch(self.proj, self.rid, {"type": "orkestra:implementer", "tasks": ["T002"]}, now=t0 + 1 + i)
        self.assertEqual(self.nj()["action"], "dispatch")

    def test_window_restarts_on_new_evidence_or_reopen(self):
        t = {"started": "2026-10-12T10:00:00+00:00", "red": {"at": "2026-10-12T10:05:00+00:00"}, "green": None}
        base = T._ts(t["started"])
        self.assertEqual(T._task_since(t), T._ts(t["red"]["at"]))
        t["last_reopen_at"] = base + 900
        self.assertEqual(T._task_since(t), base + 900)
        self.assertEqual(T._task_since(t, base + 1000), base + 1000)

    def test_reviewer_dispatches_escalate_too(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        self.assertIn("dispatch orkestra:reviewer", self.nj()["text"])
        now = time.time()
        for i in range(3):
            A.record_launch(self.proj, self.rid, {"type": "orkestra:reviewer", "tasks": ["T001"]}, now=now + 1 + i)
        n = self.nj()
        self.assertEqual(n["action"], "escalate")
        self.assertIn("task block T001", n["text"])

    def test_architect_without_a_plan_escalates(self):
        self.start()
        self.phase_next()
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10))
        self.cli("gate", "baseline", "-p", "demo")
        self.phase_next()
        (self.fdir / "spec.md").write_text(__import__("test_flow").SPEC)
        self.phase_next()                                                  # plan, no plan.md
        self.assertIn("orkestra:architect", self.nj()["text"])
        for i in range(3):
            A.record_launch(self.proj, self.rid, {"type": "orkestra:architect", "tasks": []}, now=time.time() + 1 + i)
        n = self.nj()
        self.assertEqual(n["action"], "escalate")
        self.assertIn("orkestra block", n["text"])


# ------------------------------------------------------------------ (c) orkestra wait

class TestWaitFor(TempoBase):
    """tempo.wait_for with an injected clock: deterministic, no real sleeping."""

    def setUp(self):
        super().setUp()
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def run_wait(self, on_sleep=None, **kw):
        def sleep(s):
            self.sleeps.append(s)
            self.now += s
            if on_sleep:
                on_sleep(self.now)
        return T.wait_for(self.proj, self.rid, sleep=sleep, clock=self.clock, **kw)

    def test_returns_at_once_when_nothing_is_pending(self):
        res = self.run_wait(max_seconds=540)
        self.assertEqual(res["reason"], "idle")
        self.assertEqual(self.sleeps, [])

    def test_returns_when_the_pending_set_empties(self):
        launch(self, "a1")
        res = self.run_wait(on_sleep=lambda n: A.agents_purge(self.proj, self.rid) if n >= 4 else None, max_seconds=540)
        self.assertEqual(res["reason"], "idle")
        self.assertEqual(res["waited"], 4.0)
        self.assertEqual(self.sleeps, [2.0, 2.0])

    def test_never_exceeds_max(self):
        launch(self, "a1")
        res = self.run_wait(max_seconds=7, poll=2)
        self.assertEqual(res["reason"], "timeout")
        self.assertLessEqual(res["waited"], 7.0)
        self.assertEqual(self.sleeps, [2, 2, 2, 1])
        self.assertEqual(self.run_wait(max_seconds=0)["waited"], 0.0)

    def test_status_leaving_active_ends_the_wait(self):
        launch(self, "a1")
        res = self.run_wait(on_sleep=lambda n: self.cli("pause", "-p", "demo"), max_seconds=60)
        self.assertEqual(res["reason"], "status")

    def test_a_handback_ends_the_wait(self):
        launch(self, "a1")
        launch(self, "a2", ("T002",))
        res = self.run_wait(on_sleep=lambda n: A.agent_handed_back(self.proj, self.rid, "a2"), max_seconds=60)
        self.assertEqual(res["reason"], "handback")

    def test_a_new_report_due_ends_the_wait(self):
        launch(self, "a1")
        launch(self, "a2", ("T002",))
        res = self.run_wait(on_sleep=lambda n: A.agent_finished(self.proj, self.rid, "a1", "orkestra:implementer"), max_seconds=60)
        self.assertEqual(res["reason"], "reports")

    def test_a_review_file_ends_the_wait(self):
        launch(self, "a1")
        res = self.run_wait(on_sleep=lambda n: self.review("T001", "PASS"), max_seconds=60)
        self.assertEqual(res["reason"], "artifact")


class TestWaitCli(TempoBase):
    def setUp(self):
        super().setUp()
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")

    def timed(self, *args, env=None):
        t = time.time()
        cp = self.cli(*args, env=env)
        return cp, time.time() - t

    def test_no_pending_returns_at_once_with_the_next_verdict(self):
        cp, dt = self.timed("wait", "-p", "demo", env={"ORKESTRA_HEADLESS": "1"})
        self.assertLess(dt, 4)
        lines = cp.stdout.strip().splitlines()
        self.assertLessEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("next ["), cp.stdout)
        self.assertEqual(lines[0], self.cli("next", "-p", "demo").stdout.splitlines()[0])
        self.assertIn("no subagent is running", lines[1])

    def test_headless_blocks_up_to_max_and_no_longer(self):
        launch(self, "a1")
        cp, dt = self.timed("wait", "--max", "2", "-p", "demo", env={"ORKESTRA_HEADLESS": "1"})
        self.assertGreaterEqual(dt, 1.5)
        self.assertLess(dt, 9)
        self.assertIn("subagent(s) still running", cp.stdout)
        self.assertIn("orkestra wait", cp.stdout)                          # the headless verdict names the wait call
        self.assertIn("cap reached", cp.stdout)

    def test_headless_returns_when_the_registry_empties(self):
        launch(self, "a1")
        threading.Timer(1.0, lambda: A.agents_purge(self.proj, self.rid)).start()
        cp, dt = self.timed("wait", "--max", "60", "-p", "demo", env={"ORKESTRA_HEADLESS": "1"})
        self.assertLess(dt, 5)
        self.assertIn("no subagent is running", cp.stdout)

    def test_interactive_never_blocks(self):
        launch(self, "a1")
        cp, dt = self.timed("wait", "--max", "30", "-p", "demo")
        self.assertLess(dt, 4)
        self.assertIn("END YOUR TURN", cp.stdout)
        self.assertIn("not blocking", cp.stdout)

    def test_help_exists_in_both_languages(self):
        for lang in ("en", "tr"):
            cp = self.cli("wait", "--help", env={"ORKESTRA_LANG": lang})
            self.assertIn("--max", cp.stdout)
        self.assertNotEqual(self.cli("wait", "--help", env={"ORKESTRA_LANG": "en"}).stdout,
                            self.cli("wait", "--help", env={"ORKESTRA_LANG": "tr"}).stdout)

    def test_wait_is_not_a_loop_for_the_flags(self):
        # a real wait blocks for a while: the next row of the agent comes much later (LT-4: instant returns are a loop)
        rows = [{"ts": 1000.0 + 60 * i, "kind": "tool", "agent": "main", "tool": "Bash", "what": "orkestra wait --max 540 -p demo"}
                for i in range(6)]
        self.assertEqual(T.loop_flags(rows, 3), [])
        instant = [dict(r, ts=1000.0 + i) for i, r in enumerate(rows)]
        self.assertTrue(T.loop_flags(instant, 3))                           # waits that return at once are a hot loop
        rows = [dict(r, what="sleep 30; orkestra next -p demo") for r in rows]
        self.assertTrue(T.loop_flags(rows, 3))                              # the old polling is still a loop

    def test_headless_text_uses_wait_not_polling(self):
        self.assertNotIn("sleep 30", C.HEADLESS_WAIT)
        self.assertIn("orkestra wait", C.HEADLESS_WAIT)
        self.assertNotIn("orkestra await", C.HEADLESS_WAIT_AUTO)            # autopilot never awaits
        text = (PLUGIN / "skills" / "run" / "references" / "headless.md").read_text()
        self.assertNotIn("sleep 30", text.replace("Never poll with `sleep 30; orkestra next`", ""))
        self.assertIn("orkestra wait", text)
        n = T.next_action(self.proj, json.loads(self.cli("status", "--json", "-p", "demo").stdout), now=time.time())
        launch(self, "a1")
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)
        os.environ["ORKESTRA_HEADLESS"] = "1"
        try:
            self.assertIn("orkestra wait", T.next_action(self.proj, st)["text"])
            self.assertNotIn("sleep", T.next_action(self.proj, st)["text"])
        finally:
            os.environ.pop("ORKESTRA_HEADLESS", None)

    def test_headless_preface_is_said_once_per_run(self):
        env = {"ORKESTRA_HEADLESS": "1"}
        first = self.cli("playbook", "implement", "-p", "demo", env=env).stdout
        second = self.cli("playbook", "implement", "-p", "demo", env=env).stdout
        self.assertIn("HEADLESS:", first)
        self.assertNotIn("HEADLESS:", second)
        self.assertTrue(first.endswith(second))                              # same playbook body
        self.assertFalse(T.once_per_run(self.proj, self.rid, "headless_wait"))
        self.assertTrue(T.once_per_run(self.proj, self.rid, "something-else"))


# ------------------------------------------------------------------ (d) mode-aware text

class TestModeAwareText(TempoBase):
    def test_phase_goals(self):
        self.assertIn(ASK, C.phase_goal("clarify"))
        self.assertIn("ask for approval", C.phase_goal("approve"))
        for phase in ("clarify", "approve"):
            auto = C.phase_goal(phase, True)
            self.assertNotIn(ASK, auto)
            self.assertNotIn("await", auto)
            self.assertNotIn("ask for approval", auto)
        self.assertIn("orkestra block", C.phase_goal("clarify", True))

    def test_phase_next_output_in_autopilot(self):
        self.start(approval_mode="autopilot")
        self.phase_next()
        (self.fdir / "context.md").write_text("# Context\n" + ("calc.py defines add(); tests in tests/. " * 10))
        self.cli("gate", "baseline", "-p", "demo")
        out = self.phase_next().stdout
        self.assertIn("next action:", out)
        self.assertNotIn(ASK, out)

    def test_blocked_note_and_blocked_task_by_mode(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.cli("task", "note", "T001", "-p", "demo", "BLOCKED: spec contradicts itself")
        manual = self.nj()
        self.assertEqual(manual["action"], "ask_user")
        autopilot(self)
        auto = self.nj()
        self.assertNotIn(ASK, auto["text"])
        self.assertIn("task defer", auto["text"])
        self.assertNotEqual(auto["action"], "ask_user")

    def test_dependency_deadlock_by_mode(self):
        st = {"run_id": "r", "tasks": {"T001": {"status": "pending", "depends": ["T009"]}}}
        manual = T._open_tasks_action(self.proj, dict(st, approval_mode="manual"), False)
        auto = T._open_tasks_action(self.proj, dict(st, approval_mode="autopilot"), False)
        self.assertEqual(manual["action"], "ask_user")
        self.assertIn("task defer", auto["text"])
        self.assertNotEqual(auto["action"], "ask_user")

    def test_stop_tail_by_mode(self):
        self.start()
        auto = T.stop_tail(self.proj, {"approval_mode": "autopilot"})
        manual = T.stop_tail(self.proj, {"approval_mode": "manual"})
        self.assertNotIn(ASK, auto)
        self.assertNotIn("orkestra await", auto)
        self.assertIn("orkestra block", auto)
        self.assertIn(ASK, manual)
        self.assertIn("orkestra await", manual)

    def test_deny_loops_surface_in_next(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        for _ in range(3):
            A.record(self.proj, self.rid, {"agent_type": "orkestra:implementer", "tool_name": "Bash"}, "deny", "git push origin main")
        self.assertIn("hit the same guard", self.cli("next", "-p", "demo").stdout)

    def test_hints_use_the_project_name_not_the_path(self):
        self.to_implement()
        out = self.cli("next", "-p", "demo").stdout
        self.assertIn("-p demo", out)
        self.assertNotIn(str(self.proj), out)
        self.assertEqual(T.p_arg(Path("/somewhere/else/proj")), "-p /somewhere/else/proj")


# ------------------------------------------------------------------ (e) stall classifier

def row(ts, kind="tool", agent="main", tool="", what="", aid=None):
    r = {"ts": float(ts), "kind": kind, "agent": agent, "tool": tool, "what": what}
    if aid:
        r["aid"] = aid
    return r


ST = {"status": "active", "approval_mode": "autopilot", "tasks": {}}
WD = dict(T.WD_DEFAULTS)


def classify(rows, now, **kw):
    return T.classify(ST, rows, kw.pop("pending", []), now=now, wd=WD, **kw)


class TestStallClassifier(unittest.TestCase):
    def test_subagent_command_without_end_while_the_run_moves_is_working(self):
        rows = [row(0, agent="orkestra:implementer", aid="a1", tool="Bash", what="npm test")]
        rows += [row(60 * i, tool="Edit", what=f"f{i}.py") for i in range(1, 21)]
        self.assertEqual(classify(rows, 1200 + 0.5)["cls"], 0)

    def test_abandoned_unterminated_command_is_hung_without_other_rows(self):
        rows = [row(0, tool="Bash", what="npm test")]
        self.assertEqual(classify(rows, 1000)["key"], "hung")
        self.assertNotEqual(classify(rows, 1000, proc_alive=True)["cls"], 3)        # a live process signal vetoes it
        self.assertEqual(classify(rows, 100)["cls"], 0)                              # under the limit

    def test_open_command_is_closed_by_a_later_row_of_the_same_agent(self):
        base = [row(0, tool="Bash", what="make")]
        self.assertEqual(len(T._running_commands(base)), 1)
        self.assertEqual(len(T._running_commands(base + [row(1.5, tool="Edit", what="a.py")])), 1)    # same parallel batch
        self.assertEqual(T._running_commands(base + [row(4, tool="Edit", what="a.py")]), [])
        sub = [row(0, agent="orkestra:implementer", aid="a1", tool="Bash", what="make")]
        self.assertEqual(len(T._running_commands(sub + [row(9, tool="Edit", what="a.py")])), 1)       # another agent's row
        self.assertEqual(T._running_commands(sub + [row(9, agent="orkestra:implementer", aid="a1", tool="Edit", what="a.py")]), [])

    def test_session_end_closes_every_open_command(self):
        rows = [row(0, agent="orkestra:implementer", aid="a1", tool="Bash", what="make"), row(5, kind="idle")]
        self.assertEqual(T._running_commands(rows), [])

    def test_dead_wait_keys_on_a_due_report_with_a_60s_threshold(self):
        done = row(0, kind="agent_done", agent="orkestra:implementer", aid="a1")
        due = [{"at": 0.0, "id": "a1"}]
        self.assertEqual(classify([done], 75, reports_due=due)["key"], "dead_wait")   # 75 s: old threshold (90 s) not reached
        self.assertEqual(classify([done], 55, reports_due=due)["cls"], 0)
        self.assertEqual(classify([done], 75, reports_due=[])["cls"], 0)              # nothing due, nothing to wait for

    def test_handback_or_progress_settles_it(self):
        done = row(0, kind="agent_done", agent="orkestra:implementer", aid="a1")
        due = [{"at": 0.0, "id": "a1"}]
        got = [done, row(20, kind="handback", what="report")]
        self.assertEqual(classify(got, 200, reports_due=due)["cls"], 0)
        reading = [done] + [row(30 * i, tool="Read", what=f"f{i}") for i in range(1, 5)]
        self.assertEqual(classify(reading, 120.5, reports_due=due)["cls"], 0)         # the main session is working
        self.assertEqual(classify([done, row(30, kind="tool", agent="orkestra:scout", aid="s", tool="Grep", what="x")],
                                  75, reports_due=due)["cls"], 0)                      # somebody else is working

    def test_polling_does_not_hide_a_dead_wait(self):
        done = row(0, kind="agent_done", agent="orkestra:implementer", aid="a1")
        polls = [row(t, tool="Bash", what="sleep 30; orkestra next -p demo") for t in (5, 40, 75)]
        self.assertEqual(classify([done] + polls, 120, reports_due=[{"at": 0.0, "id": "a1"}])["key"], "dead_wait")

    def test_agent_silent_seconds_is_the_sign_of_life_rule(self):
        pend = [{"at": 0.0, "type": "orkestra:implementer", "id": "a1"}]
        self.assertIsNone(T.agents_quiet([], pend, 100, WD, "agent_silent_seconds"))
        self.assertEqual(T.agents_quiet([], pend, 200, WD, "agent_silent_seconds"), 200)
        self.assertIsNone(T.agents_quiet([], pend, 200, WD))                             # the classification keeps 600 s
        self.assertIsNone(T.agents_quiet([], pend, 200, WD, "agent_silent_seconds", life=150))   # transcript still written
        open_cmd = [row(10, agent="orkestra:implementer", aid="a1", tool="Bash", what="make")]
        self.assertIsNone(T.agents_quiet(open_cmd, pend, 500, WD, "agent_silent_seconds"))
        self.assertEqual(WD["agent_silent_seconds"], 150)

    def test_transcript_life_reads_the_subagent_transcript(self):
        import tempfile
        from orkestra_core import usage
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "agent-a1.jsonl"
            sub.write_text("{}\n")
            with mock.patch.object(usage, "_locate", return_value=[(str(Path(d) / "main.jsonl"), True), (str(sub), False)]):
                self.assertEqual(T.transcript_life([{"sid": "s", "id": "a1"}]), sub.stat().st_mtime)
                self.assertEqual(T.transcript_life([{"sid": "s", "id": "zz"}]), 0.0)
                self.assertEqual(T.transcript_life([{"sid": "", "id": "a1"}]), 0.0)

    def test_replay_activity_is_never_hung_or_dead_wait(self):
        """A synthetic busy timeline: at every instant a real row (< 1 s old) proves activity; the classifier must
        never call that a hung command or a dead wait, however many commands lost their `end` row."""
        rng = random.Random(7)
        rows, t, due = [], 1000.0, []
        checks = 0
        for step in range(400):
            t += rng.choice([0.3, 1, 5, 40, 80, 200, 700])
            kind = rng.choice(["edit", "bash_noend", "bash_end", "sub_bash_noend", "agent_done", "handback", "tool_sub", "idle"])
            if kind == "edit":
                new = [row(t, tool="Edit", what="a.py")]
            elif kind == "bash_noend":
                new = [row(t, tool="Bash", what="make test")]
            elif kind == "bash_end":
                new = [row(t, tool="Bash", what="ls"), row(t + 0.2, kind="end", tool="Bash", what="✓")]
                t += 0.2
            elif kind == "sub_bash_noend":
                new = [row(t, agent="orkestra:implementer", aid=f"a{step}", tool="Bash", what="npm test")]
            elif kind == "agent_done":
                new = [row(t, kind="agent_done", agent="orkestra:implementer", aid=f"a{step}")]
                due.append({"at": t, "id": f"a{step}"})
            elif kind == "handback":
                new = [row(t, kind="handback", what="report")]
                due = []
            elif kind == "tool_sub":
                new = [row(t, agent="orkestra:reviewer", aid="r1", tool="Read", what="x.py")]
            else:
                new = [row(t, kind="idle")]
            rows += new
            now = new[-1]["ts"] + 0.5
            res = classify(rows, now, reports_due=[d for d in due if d["at"] > max([r["ts"] for r in rows if r["kind"] == "handback"] or [0])])
            self.assertNotIn(res["cls"], (2, 3), f"step {step} {kind}: {res}")
            checks += 1
        self.assertEqual(checks, 400)


# ------------------------------------------------------------------ (f) trimmed output / (g) packet / (h) files

class TestTrimmedOutput(unittest.TestCase):
    def test_gate_tails_are_capped_with_log_paths(self):
        from orkestra_core import gates as G
        results = [{"name": f"gate{i}", "ok": False, "timed_out": False, "duration_s": 1.0, "cmd": "x", "exit": 1,
                    "log": f"/runs/evidence/gate{i}.log", "tail": ("line\n" * 700)} for i in range(6)]
        raw = G.format_results(results)
        self.assertGreater(len(raw), 16000)
        out = C.cap_gate_text(raw)
        self.assertLess(len(out), 9 * 1024)
        for i in range(6):
            self.assertIn(f"gate{i}.log", out)                       # every failing gate is findable by its log path
        self.assertEqual(C.cap_gate_text("short"), "short")


class TestReviewPacket(TempoBase):
    def setUp(self):
        super().setUp()
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")
        self.tdd_cycle("T001", "sub", "a - b", 5, 3, 2)
        (self.fdir / "tasks").mkdir(exist_ok=True)
        (self.fdir / "tasks" / "T001.report.md").write_text("# T001 report\nsub() added.\nDeviations: none\n")

    def packet(self):
        return self.cli("brief", "--review", "T001", "-p", "demo").stdout

    def test_packet_has_the_review_inputs_in_one_call(self):
        out = self.packet()
        for needle in ("# Review packet — T001", "## Acceptance", "Given 5 ve 3", "## RED evidence", "## GREEN evidence",
                       "## Implementer report", "sub() added", "## Deviations", "## Diff of the task's files",
                       "+def sub(a, b):", "new/untracked file: tests/test_sub.py"):
            self.assertIn(needle, out)
        self.assertLess(len(out), 8000)

    def test_tails_and_diff_are_capped(self):
        with open(self.proj / "calc.py", "a") as fh:
            fh.write("\n".join(f"# filler {i}" for i in range(900)) + "\n")
        out = self.packet()
        self.assertIn("more diff lines", out)
        self.assertLess(len(out.splitlines()), 600)
        st = json.loads(self.cli("status", "--json", "-p", "demo").stdout)["tasks"]["T001"]
        self.assertLessEqual(len(C._evidence_tail(st["red"]).splitlines()), 40)

    def test_unknown_task_and_agent_doc(self):
        cp = self.cli("brief", "--review", "T999", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("orkestra brief --review", (PLUGIN / "agents" / "reviewer.md").read_text())


class TestTaskAddFiles(TempoBase):
    def test_files_scope_feeds_the_overlap_check(self):
        self.to_implement()
        self.cli("task", "start", "T001", "-p", "demo")               # T001 owns calc.py
        self.cli("task", "add", "FIX-R001", "-p", "demo", "fix calc", "--files", "calc.py,tests/test_x.py")
        t = json.loads(self.cli("status", "--json", "-p", "demo").stdout)["tasks"]["FIX-R001"]
        self.assertEqual(t["files"], ["calc.py", "tests/test_x.py"])
        cp = self.cli("task", "start", "FIX-R001", "-p", "demo", check=False)
        self.assertEqual(cp.returncode, 2)
        self.assertIn("calc.py", cp.stderr)
        self.cli("task", "add", "FIX-R002", "-p", "demo", "no scope")
        t = json.loads(self.cli("status", "--json", "-p", "demo").stdout)["tasks"]["FIX-R002"]
        self.assertEqual(t["files"], [])


class TestSkillText(unittest.TestCase):
    def test_playbooks_are_autopilot_aware(self):
        skill = (PLUGIN / "skills" / "run" / "SKILL.md").read_text()
        self.assertIn("orkestra wait", skill)
        self.assertIn("--force` cannot lift the cap", skill)
        impl = (PLUGIN / "skills" / "run" / "references" / "phase-implement.md").read_text()
        self.assertIn("brief --review", impl)
        self.assertIn("--files", impl)


if __name__ == "__main__":
    unittest.main()
