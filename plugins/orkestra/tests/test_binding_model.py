"""Control plane: who may bind a chat to a run, who may change a run, and what a denied agent is told.

Background (2026-10-12): parallel subagents of one workflow share the parent session id. One of them printed an
`[orkestra:bind]` line for a throw-away run in a sandbox home; the real PostToolUse hook bound the whole shared session
to it and every other agent was then judged by that run's isolation / internals guards, with no way out. These tests pin
the rules that make that impossible:

  a  only the orkestra CLI, run by the main chat, can bind or unbind (not a subagent, not `cat log.txt`)
  b  the marker names the CLI's home; a hook of another home ignores it; the run must exist, be open, path absolute
  c  a live binding is never silently replaced by another run
  d  bindings that can never be right again are dropped when seen
  e  only explicit signals unbind a chat on a prompt
  f  pause / abort / resume ... only by the run's driver while its heartbeat is fresh; same-status calls are no-ops
  g  every run-guard denial ends with one `unblock:` line
  h  the internals guard does not stop read-only inspection
  i  the incident, end to end
"""
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

import helpers
from test_flow import FlowBase
from test_engines import CursorBase, OTHER
from orkestra_core import guards, intent
from orkestra_core import state as ST
from orkestra_core.paths import DEFAULT_GLOBAL_CONFIG

PLUGIN = helpers.PLUGIN
SANDBOX_MARKER = re.compile(r"^\[orkestra:(bind|unbind)\] project=(.+?) run=(\S+)(?: home=(.+?))?\s*$", re.M)


def marker_line(out: str, kind: str = "bind") -> str:
    for ln in out.splitlines():
        if ln.startswith(f"[orkestra:{kind}]"):
            return ln
    raise AssertionError(f"no {kind} marker in the output")


class Base(FlowBase):
    """FlowBase starts run `demo` and binds `sess-1` the way the PostToolUse hook does."""

    def sess_file(self, sid=None) -> Path:
        return self.home / "sessions" / f"{sid or self.sid}.json"

    def bound_run(self, sid=None):
        f = self.sess_file(sid)
        return json.loads(f.read_text()).get("run_id") if f.exists() else None

    def post_bash(self, command, stdout, sid=None, **extra):
        ev = {"session_id": sid or self.sid, "tool_name": "Bash", "tool_input": {"command": command},
              "tool_response": {"stdout": stdout, "stderr": ""}, "cwd": str(self.proj)}
        ev.update(extra)
        return self.hook("PostToolUse", ev)

    def second_run(self, name="other", title="Second"):
        """A second project with its own open run (not bound to anything). -> (project, run id, CLI output)."""
        proj = self.make_project(name)
        self.cli("init", "-p", name)
        out = self.cli("start", "-p", name, "--title", title, "--request", "ikinci is", "--size", "S").stdout
        rid = re.search(r"run=(\S+)", out).group(1)
        return proj, rid, out

    def ledger_of(self, proj, rid):
        p = proj / ".orkestra" / "features" / rid / "ledger.md"
        return p.read_text() if p.exists() else ""


class TestMarkerTrust(Base):
    """(a) + (b): what may bind and unbind a chat."""

    def setUp(self):
        super().setUp()
        self.cli("init", "-p", "demo")

    def fresh_run_output(self):
        out = self.cli("start", "-p", "demo", "--title", "Fresh", "--request", "x", "--size", "S").stdout
        return out

    def test_the_cli_marker_names_the_home_it_was_printed_for(self):
        out = self.cli("start", "-p", "demo", "--title", "Marked", "--request", "x", "--size", "S").stdout
        m = SANDBOX_MARKER.search(out)
        self.assertEqual(m.group(1), "bind")
        self.assertEqual(Path(m.group(4)), self.home)
        self.cli("pause", "-p", "demo")
        resumed = self.cli("resume", "-p", "demo").stdout
        self.assertEqual(Path(SANDBOX_MARKER.search(resumed).group(4)), self.home)
        paused = self.cli("pause", "-p", "demo").stdout
        self.assertEqual(Path(SANDBOX_MARKER.search(paused).group(4)), self.home)

    def test_a_subagent_event_never_binds(self):
        out = self.fresh_run_output()
        rid = re.search(r"run=(\S+)", out).group(1)
        for extra in ({"agent_type": "Explore", "agent_id": "ag-1"}, {"agent_type": "general-purpose"},
                      {"agent_id": "ag-2"}, {"agentType": "Plan"}, {"agentId": "ag-3"}):
            with self.subTest(extra=extra):
                self.post_bash("orkestra start -p demo --title Fresh --request x", out, **extra)
                self.assertIsNone(self.bound_run(), extra)
        self.post_bash("orkestra start -p demo --title Fresh --request x", out)      # the main chat itself still binds
        self.assertEqual(self.bound_run(), rid)

    def test_a_subagent_cannot_unbind_the_main_chat(self):
        self.start()
        paused = self.cli("pause", "-p", "demo").stdout
        self.cli("resume", "-p", "demo")
        for extra in ({"agent_type": "Explore", "agent_id": "ag-1"}, {"agent_type": "orkestra:scout"}):
            self.post_bash("orkestra pause -p demo", paused, **extra)
            self.assertEqual(self.bound_run(), self.rid, extra)

    def test_a_marker_in_other_output_never_binds_or_unbinds(self):
        self.start()
        paused = self.cli("pause", "-p", "demo").stdout
        resumed = self.cli("resume", "-p", "demo").stdout
        self.sess_file().unlink()
        for cmd in ("cat transcript.log", "grep -rn 'orkestra:bind' docs/", "python3 -m unittest -q",
                    "echo hello", "curl -s http://localhost:1/x", "orkestra status -p demo", "orkestra context -p demo",
                    "git log --oneline | head", "orkestra phase show -p demo", "orkestra watch --once"):
            with self.subTest(cmd=cmd):
                self.post_bash(cmd, resumed)
                self.assertIsNone(self.bound_run(), cmd)
        self.post_bash("orkestra resume -p demo", resumed)
        self.assertEqual(self.bound_run(), self.rid)
        for cmd in ("cat transcript.log", "orkestra status -p demo", "echo done"):
            with self.subTest(cmd=cmd):
                self.post_bash(cmd, paused)
                self.assertEqual(self.bound_run(), self.rid, cmd)

    def test_every_way_of_calling_the_cli_still_counts(self):
        out = self.fresh_run_output()
        self.assertIsNone(self.bound_run())
        cmds = ("orkestra start -p demo --title x --request y", "cd /somewhere && orkestra resume -p demo",
                f"python3 {helpers.BIN} start --title t --request r", "FOO=1 orkestra resume",
                '"${CLAUDE_PLUGIN_ROOT}/bin/orkestra" resume -p demo', "timeout 60 orkestra start --title t --request r",
                "/usr/local/bin/orkestra resume", "ORKESTRA_HOME=/x orkestra start --title t --request r",
                "bash -c 'orkestra resume -p demo'", "true && orkestra resume | tee /tmp/o",
                "orkestra.cmd resume -p demo", "\"C:\\tools\\orkestra.exe\" resume -p demo")
        for n, cmd in enumerate(cmds):
            sid = f"sess-form-{n}"
            with self.subTest(cmd=cmd):
                self.post_bash(cmd, out, sid=sid)
                self.assertIsNotNone(self.bound_run(sid), cmd)

    def test_a_host_that_sends_no_command_is_tolerated(self):
        out = self.fresh_run_output()
        self.hook("PostToolUse", {"session_id": "sess-nocmd", "tool_name": "Bash",
                                  "tool_response": {"stdout": out, "stderr": ""}, "cwd": str(self.proj)})
        self.assertIsNotNone(self.bound_run("sess-nocmd"))

    def test_a_marker_for_another_home_is_ignored(self):
        out = self.fresh_run_output()
        line = marker_line(out)
        foreign = out.replace(line, re.sub(r" home=.*$", " home=/somewhere/else/.orkestra", line))
        self.post_bash("orkestra start -p demo --title x --request y", foreign, sid="sess-foreign")
        self.assertIsNone(self.bound_run("sess-foreign"))
        legacy = out.replace(line, re.sub(r" home=.*$", "", line))        # a CLI of an older version: no home field
        self.post_bash("orkestra start -p demo --title x --request y", legacy, sid="sess-legacy")
        self.assertIsNotNone(self.bound_run("sess-legacy"))
        self.post_bash("orkestra start -p demo --title x --request y", out, sid="sess-own")
        self.assertIsNotNone(self.bound_run("sess-own"))

    def test_a_sandbox_run_cannot_bind_the_real_session(self):
        """The CLI was run with another ORKESTRA_HOME (a test sandbox): its marker is for that home only."""
        scratch = self.make_project("scratch")
        sandbox_home = self.tmp / "sandbox-home"
        self.cli("init", "-p", "scratch", env={"ORKESTRA_HOME": str(sandbox_home)})
        out = self.cli("start", "-p", "scratch", "--title", "Sandbox", "--request", "x", "--size", "S",
                       env={"ORKESTRA_HOME": str(sandbox_home)}).stdout
        self.assertEqual(Path(SANDBOX_MARKER.search(out).group(4)), sandbox_home)
        self.post_bash("orkestra start -p scratch --title Sandbox --request x", out, sid="sess-real")
        self.assertIsNone(self.bound_run("sess-real"))

    def test_the_run_must_exist_be_open_and_the_project_absolute(self):
        out = self.fresh_run_output()
        rid = re.search(r"run=(\S+)", out).group(1)
        line = marker_line(out)
        cases = {
            "relative project": line.replace(f"project={self.proj}", "project=demo"),
            "unknown run": line.replace(f"run={rid}", "run=999-nope"),
            "run id that is a path": line.replace(f"run={rid}", f"run={rid}/../{rid}"),
            "project that does not exist": line.replace(f"project={self.proj}", f"project={self.ws}/ghost"),
        }
        for name, bad in cases.items():
            with self.subTest(name):
                self.post_bash("orkestra resume", bad, sid="sess-bad")
                self.assertIsNone(self.bound_run("sess-bad"))
        self.cli("abort", "-p", "demo", "weg")
        self.post_bash("orkestra resume", out, sid="sess-bad")
        self.assertIsNone(self.bound_run("sess-bad"), "a closed run")

    def test_a_relative_project_is_refused_even_where_it_would_resolve(self):
        """The hook's own directory must not decide which project a marker means (it used to: project=demo was
        stored as is and resolved against whatever directory the next hook ran in)."""
        import subprocess
        out = self.fresh_run_output()
        relative = marker_line(out).replace(f"project={self.proj}", "project=demo")
        ev = {"session_id": "sess-rel", "hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(self.ws),
              "tool_input": {"command": "orkestra resume"}, "tool_response": {"stdout": relative, "stderr": ""}}
        cp = subprocess.run([sys.executable, str(helpers.DISPATCH), "PostToolUse"], input=json.dumps(ev), cwd=str(self.ws),
                            capture_output=True, text=True, env=dict(os.environ))
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertIsNone(self.bound_run("sess-rel"))

    def test_pause_abort_and_done_still_unbind_the_main_chat(self):
        self.start()
        paused = self.cli("pause", "-p", "demo").stdout
        self.post_bash("orkestra pause -p demo", paused)
        self.assertIsNone(self.bound_run())
        self.cli("resume", "-p", "demo")
        self.post_bash("orkestra resume", marker_line(self.cli("resume", "-p", "demo").stdout))
        self.assertEqual(self.bound_run(), self.rid)
        aborted = self.cli("abort", "-p", "demo", "weg").stdout
        self.post_bash("orkestra abort weg -p demo", aborted)
        self.assertIsNone(self.bound_run())


class TestKeepLiveBinding(Base):
    """(c): a chat that drives a live run is not handed to another run by a marker."""

    def test_a_second_run_does_not_steal_a_live_binding(self):
        self.start()
        other, rid2, out2 = self.second_run()
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"}):
            res = self.post_bash("orkestra start -p other --title Second --request x", out2)
        self.assertEqual(self.bound_run(), self.rid)
        ctx = res["hookSpecificOutput"]["additionalContext"]
        self.assertIn(self.rid, ctx)
        self.assertIn(rid2, ctx)
        self.assertIn(f"orkestra pause -p {self.proj}", ctx)
        self.assertIn(f"orkestra resume -p {other}", ctx)
        ledger = self.ledger_of(other, rid2)
        self.assertEqual(len([ln for ln in ledger.splitlines() if self.rid in ln]), 1, ledger)
        self.assertNotRegex(ledger, r"[çğıöşÇĞİÖŞÜ]")

    def test_the_notice_is_in_the_language_of_the_user(self):
        self.start()
        other, rid2, out2 = self.second_run()
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            self.post_bash("orkestra start -p other --title Second --request x", out2)
        ledger = self.ledger_of(other, rid2)
        line = [ln for ln in ledger.splitlines() if self.rid in ln]
        self.assertEqual(len(line), 1, ledger)
        self.assertRegex(line[0], r"[çğıöşÇĞİÖŞÜ]|bağ|çalıştır|sürüyor")

    def test_the_same_refusal_is_reported_once(self):
        self.start()
        other, rid2, out2 = self.second_run()
        first = self.post_bash("orkestra start -p other --title Second --request x", out2)
        again = self.post_bash("orkestra resume -p other", out2)
        self.assertIn("additionalContext", json.dumps(first))
        self.assertEqual(again, {})
        ledger = self.ledger_of(other, rid2)
        self.assertEqual(len([ln for ln in ledger.splitlines() if self.rid in ln]), 1, ledger)
        self.assertEqual(self.bound_run(), self.rid)

    def test_after_the_first_run_is_paused_the_second_binds(self):
        self.start()
        other, rid2, out2 = self.second_run()
        self.post_bash("orkestra start -p other --title Second --request x", out2)
        self.assertEqual(self.bound_run(), self.rid)
        self.post_bash("orkestra pause -p demo", self.cli("pause", "-p", "demo").stdout)
        self.post_bash("orkestra resume -p other", out2)
        self.assertEqual(self.bound_run(), rid2)

    def test_a_silent_old_binding_may_be_replaced(self):
        self.start()
        other, rid2, out2 = self.second_run()
        old = time.time() - 3600
        os.utime(self.sess_file(), (old, old))
        res = self.post_bash("orkestra start -p other --title Second --request x", out2)
        self.assertEqual(self.bound_run(), rid2)
        self.assertNotIn("WARNING", json.dumps(res))

    def test_the_same_run_again_is_not_a_conflict(self):
        self.start()
        res = self.post_bash("orkestra resume -p demo", marker_line(self.cli("resume", "-p", "demo").stdout))
        self.assertEqual(self.bound_run(), self.rid)
        self.assertEqual(res, {})

    def test_a_paused_run_does_not_hold_the_chat(self):
        self.start()
        other, rid2, out2 = self.second_run()
        self.cli("pause", "-p", "demo")                                  # paused from a terminal: no marker reaches the chat
        self.assertEqual(self.bound_run(), self.rid)
        self.post_bash("orkestra start -p other --title Second --request x", out2)
        self.assertEqual(self.bound_run(), rid2)

    def test_the_state_level_rule_is_shared_by_every_engine(self):
        """Cursor binds from the CLI (env) and Copilot from its own prompt hook: both go through try_bind / bind_session."""
        self.start()
        other, rid2, _ = self.second_run()
        kept = ST.try_bind(self.sid, other, rid2, engine="claude", lease=120)
        self.assertEqual(kept["result"], "kept")
        self.assertEqual(kept["prev"]["run_id"], self.rid)
        self.assertFalse(kept["duplicate"])
        again = ST.try_bind(self.sid, other, rid2, engine="claude", lease=120)
        self.assertEqual((again["result"], again["duplicate"]), ("kept", True))
        self.assertEqual(ST.try_bind("sess-new", other, rid2, engine="copilot", lease=120)["result"], "bound")
        self.assertEqual(self.bound_run(), self.rid)

    def test_cursor_cli_keeps_a_live_conversation_binding_and_says_so(self):
        conv = "cur-conv-1"
        env = {"CURSOR_AGENT": "1", "CURSOR_CONVERSATION_ID": conv}
        seen = self.home / "cursor" / "seen"
        seen.mkdir(parents=True)
        (seen / f"{conv}.json").write_text(json.dumps({"ts": time.time()}))
        first = self.cli("start", "-p", "demo", "--title", "A", "--request", "x", "--size", "S", env=env).stdout
        sid = "cursor-" + conv
        self.assertEqual(self.bound_run(sid), re.search(r"run=(\S+)", first).group(1))
        other = self.make_project("other")
        self.cli("init", "-p", "other")
        out = self.cli("start", "-p", "other", "--title", "B", "--request", "x", "--size", "S", env=env).stdout
        self.assertEqual(self.bound_run(sid), re.search(r"run=(\S+)", first).group(1))
        self.assertIn("still bound", out)
        self.assertIn("orkestra pause -p", out)


class TestSelfHealing(Base):
    """(d): bindings that point nowhere are dropped the moment a hook sees them."""

    EVENTS = {
        "SessionStart": {"source": "startup"},
        "UserPromptSubmit": {"prompt": "devam et", "prompt_id": "p1"},
        "PreToolUse": {"tool_name": "Read", "tool_input": {"file_path": "calc.py"}},
        "Stop": {"stop_hook_active": False, "prompt_id": "p1"},
    }

    def write_binding(self, sid, project, run_id):
        self.sess_file(sid).parent.mkdir(parents=True, exist_ok=True)
        self.sess_file(sid).write_text(json.dumps({"project": str(project), "run_id": run_id, "bound_at": "2026-10-12T10:00:00"}))

    def test_bogus_bindings_are_dropped_when_seen(self):
        self.start()
        self.cli("abort", "-p", "demo", "closed")                        # the run self.rid is terminal now
        gone = self.ws / "vanished"
        cases = {"run missing": (self.proj, "003-drift-ca"), "relative project": (Path("proj"), "001-demo-run"),
                 "project vanished": (gone, "001-x"), "run closed": (self.proj, self.rid)}
        for event, extra in self.EVENTS.items():
            for name, (proj, rid) in cases.items():
                with self.subTest(event=event, case=name):
                    self.write_binding("sess-bogus", proj, rid)
                    self.hook(event, dict(extra, session_id="sess-bogus", cwd=str(self.proj)))
                    self.assertFalse(self.sess_file("sess-bogus").exists(), (event, name))

    def test_a_good_binding_is_kept(self):
        self.start()
        for event, extra in self.EVENTS.items():
            self.hook(event, dict(extra, session_id=self.sid, cwd=str(self.proj)))
            self.assertEqual(self.bound_run(), self.rid, event)

    def test_a_paused_run_keeps_its_binding_until_the_turn_ends(self):
        self.start()
        self.cli("pause", "-p", "demo")
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Read", "cwd": str(self.proj),
                                 "tool_input": {"file_path": "calc.py"}})
        self.assertEqual(self.bound_run(), self.rid)

    def test_bind_session_validates_before_it_writes(self):
        self.start()
        other, rid2, _ = self.second_run()
        self.assertTrue(ST.bind_session("sess-ok", other, rid2))
        self.assertTrue(self.sess_file("sess-ok").exists())
        self.assertFalse(ST.bind_session("sess-rel", Path("other"), rid2))
        self.assertFalse(ST.bind_session("sess-nope", other, "999-nope"))
        self.assertFalse(ST.bind_session("sess-ghost", self.ws / "ghost", "001-x"))
        self.cli("abort", "-p", "other", "weg")
        self.assertFalse(ST.bind_session("sess-closed", other, rid2))
        for sid in ("sess-rel", "sess-nope", "sess-ghost", "sess-closed"):
            self.assertFalse(self.sess_file(sid).exists(), sid)
        self.assertFalse(ST.bind_session("", other, rid2))


class TestIncident(Base):
    """(i): 2026-10-12, end to end."""

    def sandbox_marker_output(self):
        scratch = self.make_project("scratch")
        sandbox_home = self.tmp / "sandbox-home"
        env = {"ORKESTRA_HOME": str(sandbox_home)}
        self.cli("init", "-p", "scratch", env=env)
        return self.cli("start", "-p", "scratch", "--title", "Throw-away", "--request", "x", "--size", "S", env=env).stdout

    def main_write_allowed(self):
        ev = {"session_id": self.sid, "tool_name": "Write", "cwd": str(self.proj),
              "tool_input": {"file_path": str(self.proj / "calc.py"), "content": "x = 1\n"}}
        return not self.denied(self.hook("PreToolUse", ev))

    def test_subagent_prints_a_sandbox_marker_and_the_main_session_keeps_working(self):
        out = self.sandbox_marker_output()
        # 1) a subagent of the workflow (same session id) prints it: not bound
        self.post_bash("orkestra start -p scratch --title Throw-away --request x", out, agent_type="general-purpose",
                       agent_id="aud-1")
        self.assertIsNone(self.bound_run())
        # 2) the main session itself shows it (cat of a log): not bound either
        self.post_bash("cat /tmp/run.log", out)
        self.assertIsNone(self.bound_run())
        # 3) the main session's work in the repo is still allowed, also the read-only inspection that used to be refused
        self.assertTrue(self.main_write_allowed())
        for cmd in (f"ls {PLUGIN}/lib | head -3; sysctl -n hw.ncpu", f"find {PLUGIN}/lib -name '*.py' | wc -l",
                    f"cat {PLUGIN}/hooks/hooks.json | head -3; uname -a"):
            ev = {"session_id": self.sid, "tool_name": "Bash", "cwd": str(self.proj), "tool_input": {"command": cmd}}
            self.assertFalse(self.denied(self.hook("PreToolUse", ev)), cmd)

    def test_the_same_with_a_bound_main_session(self):
        self.start()
        out = self.sandbox_marker_output()
        self.post_bash("orkestra start -p scratch --title Throw-away --request x", out, agent_type="general-purpose",
                       agent_id="aud-1")
        self.post_bash("orkestra start -p scratch --title Throw-away --request x", out)
        self.assertEqual(self.bound_run(), self.rid)

    def test_copilot_subagent_marker_is_ignored(self):
        sys.path.insert(0, str(PLUGIN / "hooks"))
        import copilot_hook
        from orkestra_core import engines as EN
        self.cli("init", "-p", "demo")
        out = self.cli("start", "-p", "demo", "--title", "Cp", "--request", "x", "--size", "S", "--engine", "copilot").stdout
        parent = EN.session_key("copilot", "parent-1")
        ST.bind_child(EN.session_key("copilot", "child-1"), parent, self.proj,
                      re.search(r"run=(\S+)", out).group(1), "orkestra:scout")
        copilot_hook.handle_event("PostToolUse", {
            "hook_event_name": "PostToolUse", "session_id": "child-1", "cwd": str(self.proj), "tool_name": "bash",
            "tool_input": {"command": "orkestra resume"},
            "tool_result": {"result_type": "success", "text_result_for_llm": out}})
        self.assertFalse(self.sess_file(parent).exists())
        copilot_hook.handle_event("PostToolUse", {
            "hook_event_name": "PostToolUse", "session_id": "parent-1", "cwd": str(self.proj), "tool_name": "bash",
            "tool_input": {"command": "cat run.log"},
            "tool_result": {"result_type": "success", "text_result_for_llm": out}})
        self.assertFalse(self.sess_file(parent).exists())
        copilot_hook.handle_event("PostToolUse", {                  # the command arrives as a JSON string (`toolArgs`)
            "hook_event_name": "PostToolUse", "session_id": "parent-1", "cwd": str(self.proj), "toolName": "bash",
            "toolArgs": json.dumps({"command": "cat run.log"}),
            "tool_result": {"result_type": "success", "text_result_for_llm": out}})
        self.assertFalse(self.sess_file(parent).exists())
        copilot_hook.handle_event("PostToolUse", {
            "hook_event_name": "PostToolUse", "session_id": "parent-1", "cwd": str(self.proj), "tool_name": "bash",
            "tool_input": {"command": "orkestra resume"},
            "tool_result": {"result_type": "success", "text_result_for_llm": out}})
        self.assertTrue(self.sess_file(parent).exists())


class TestOffTopic(Base):
    """(e): an ordinary message keeps the binding; only explicit signals let go of it."""

    KEEP = ("make the header blue", "devam", "thanks", "thanks, looks good so far", "also use rounded corners on the cards",
            "go on", "keep going", "yes", "tamam", "ok", "başlığı mavi yap", "why is the build slow?", "teşekkürler",
            "add a pause button to the video player", "oynatıcıya duraklat butonu ekle", "devam et", "sonra bakarız",
            "stop words are removed by the tokenizer", "the abort signal handler should log the reason",
            "bunu daha okunaklı yap", "now add the unit tests", "can you explain what T001 does?")
    LET_GO = ("now fix the other-app login", "pause the run", "pause this", "abort the run", "please abort",
              "cancel the run", "stop the run", "forget this, let's do something else", "never mind this one",
              "this is a different topic", "duraklat", "runı duraklat", "bu işi durdur", "orkestrayı durdur",
              "iptal et", "bunu bırak", "başka bir konu: other-app login", "pause orkestra", "konu değişti")

    def setUp(self):
        super().setUp()
        (self.ws / "other-app").mkdir()

    def off(self, text):
        return intent.off_topic_vs_project(text, self.proj, "001-subtract", title="Subtract")

    def test_ordinary_messages_keep_the_binding(self):
        for text in self.KEEP:
            with self.subTest(text=text):
                self.assertFalse(self.off(text), text)

    def test_explicit_signals_let_go_of_it(self):
        for text in self.LET_GO:
            with self.subTest(text=text):
                self.assertTrue(self.off(text), text)

    def test_naming_the_bound_project_keeps_it_even_with_other_words(self):
        self.assertFalse(self.off("pause the demo header work and fix demo colours"))
        self.assertFalse(self.off("devam, other-app ile ilgisi yok"))
        self.assertFalse(self.off("commit et"))

    def test_the_bare_answers_of_an_open_question_keep_it(self):
        for text in ("iptal", "no", "reject", "vazgeç"):
            self.assertFalse(self.off(text), text)

    def test_flow_neutral_prompt_then_driving_turn_is_still_nudged(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt": "make the header blue", "prompt_id": "p-n",
                                       "cwd": str(self.proj)})
        self.assertEqual(self.bound_run(), self.rid)
        self.hook("PreToolUse", {"session_id": self.sid, "tool_name": "Read", "cwd": str(self.proj),
                                 "tool_input": {"file_path": str(self.proj / "calc.py")}})
        out = self.hook("Stop", {"session_id": self.sid, "prompt_id": "p-n", "stop_hook_active": False,
                                 "cwd": str(self.proj)})
        self.assertEqual(out.get("decision"), "block")
        self.assertEqual(self.bound_run(), self.rid)

    def test_flow_explicit_stop_unbinds_and_a_neutral_turn_that_did_not_drive_is_released_at_stop(self):
        self.start()
        self.advance_to_approve()
        self.approve()
        self.phase_next()
        res = self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt": "pause the run", "prompt_id": "p-s",
                                             "cwd": str(self.proj)})
        self.assertIsNone(self.bound_run())
        self.assertIn("Unbound", json.dumps(res))
        self.start_again_bound()
        self.hook("UserPromptSubmit", {"session_id": self.sid, "prompt": "thanks", "prompt_id": "p-t", "cwd": str(self.proj)})
        self.assertEqual(self.bound_run(), self.rid)
        out = self.hook("Stop", {"session_id": self.sid, "prompt_id": "p-t", "stop_hook_active": False, "cwd": str(self.proj)})
        self.assertEqual(out, {})                                         # nothing drove the run this turn: released
        self.assertIsNone(self.bound_run())

    def start_again_bound(self):
        self.post_bash("orkestra resume -p demo", marker_line(self.cli("resume", "-p", "demo").stdout))


class TestControlGrants(unittest.TestCase):
    """(f): the words that let a non-driver change a run."""

    def test_the_user_can_ask_in_both_languages(self):
        table = {
            "pause the run": {"pause"}, "please pause it": {"pause"}, "duraklat": {"pause"}, "runı duraklat": {"pause"},
            "pause orkestra": {"pause"}, "abort the run": {"abort"}, "iptal et": {"abort"}, "cancel this run": {"abort"},
            "devam": {"resume"}, "continue": {"resume"}, "resume": {"resume"}, "/orkestra:resume demo": {"resume"},
            "kaldığın yerden devam et": {"resume"}, "yes": {"resume"}, "evet": {"resume"}, "ok.": {"resume"},
            "yes, and also add a pause button": set(),
            "make the header blue": set(), "add a pause button to the player": set(), "thanks": set(),
            "the abort handler is broken": set(),
        }
        for text, want in table.items():
            with self.subTest(text=text):
                self.assertEqual(set(intent.control_grants(text)), want, text)

    def test_prompt_grants_are_git_and_control_together(self):
        self.assertEqual(intent.prompt_grants("commitle ve duraklat"), ["commit", "pause"])
        self.assertEqual(intent.prompt_grants("commitle"), intent.git_grants("commitle"))


class TestControlOwnership(Base):
    """(f): pause / abort / resume / unblock / block / await belong to the run's driver."""

    COMMANDS = ("pause", "abort weg", "resume", "unblock", "block weg", "await vraag")

    def setUp(self):
        super().setUp()
        self.intruder = "sess-intruder"

    def pre(self, command, sid=None, **extra):
        ev = {"session_id": sid or self.intruder, "tool_name": "Bash", "cwd": str(self.proj),
              "tool_input": {"command": f"orkestra {command} -p demo"}}
        ev.update(extra)
        return self.hook("PreToolUse", ev)

    def reason(self, out):
        return (out.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")

    def test_a_chat_that_is_not_the_driver_is_denied_while_the_driver_is_alive(self):
        self.start()
        for cmd in self.COMMANDS:
            with self.subTest(cmd=cmd):
                out = self.pre(cmd)
                self.assertTrue(self.denied(out), cmd)
                why = self.reason(out)
                self.assertIn("[orkestra:roles]", why)
                self.assertIn("sess-1", why)                              # names the driver
                self.assertEqual(why.splitlines()[-1][:8], "unblock:")

    def test_the_driver_may(self):
        self.start()
        for cmd in self.COMMANDS:
            with self.subTest(cmd=cmd):
                self.assertFalse(self.denied(self.pre(cmd, sid=self.sid)), cmd)

    def test_a_subagent_of_the_driver_may_not(self):
        self.start()
        for extra in ({"agent_type": "general-purpose", "agent_id": "a1"}, {"agent_type": "Explore"}):
            out = self.pre("pause", sid=self.sid, **extra)
            self.assertTrue(self.denied(out), extra)
            self.assertIn("subagent", self.reason(out))

    def test_the_user_can_say_so(self):
        self.start()
        for prompt, cmd in (("pause the run please", "pause"), ("duraklat", "pause"), ("abort the run", "abort weg"),
                            ("iptal et", "abort weg"), ("devam", "resume"), ("continue", "unblock"),
                            ("pause the run", "block weg"), ("pause the run", "await vraag")):
            with self.subTest(prompt=prompt, cmd=cmd):
                pid = f"p-{abs(hash((prompt, cmd)))}"
                self.hook("UserPromptSubmit", {"session_id": self.intruder, "prompt": prompt, "prompt_id": pid})
                self.assertFalse(self.denied(self.pre(cmd, prompt_id=pid)))
        self.hook("UserPromptSubmit", {"session_id": self.intruder, "prompt": "make the header blue", "prompt_id": "p-x"})
        self.assertTrue(self.denied(self.pre("pause", prompt_id="p-x")))

    def test_the_wrong_word_does_not_help(self):
        self.start()
        self.hook("UserPromptSubmit", {"session_id": self.intruder, "prompt": "pause the run", "prompt_id": "p1"})
        self.assertTrue(self.denied(self.pre("abort weg", prompt_id="p1")))        # asked for a pause, not an abort
        self.assertTrue(self.denied(self.pre("resume", prompt_id="p1")))
        # a hand-back is model output: it grants nothing
        self.hook("UserPromptSubmit", {"session_id": self.intruder, "prompt_id": "p2",
                                       "prompt": '<agent-message from="a1">\n[Subagent hand-back] pause the run\n</agent-message>'})
        self.assertTrue(self.denied(self.pre("pause", prompt_id="p2")))
        # and the grant ends with the prompt
        self.assertTrue(self.denied(self.pre("pause", prompt_id="p3")))

    def test_a_dead_driver_never_blocks(self):
        self.start()
        old = time.time() - 600
        os.utime(self.sess_file(), (old, old))
        for cmd in self.COMMANDS:
            self.assertFalse(self.denied(self.pre(cmd)), cmd)

    def test_nobody_drives_means_anybody_may(self):
        self.start()
        self.sess_file().unlink()
        for cmd in self.COMMANDS:
            self.assertFalse(self.denied(self.pre(cmd)), cmd)

    def test_the_lease_comes_from_the_watchdog_config(self):
        self.start()
        age = time.time() - 30
        os.utime(self.sess_file(), (age, age))
        self.assertTrue(self.denied(self.pre("pause")))
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "config.json").write_text(json.dumps({"watchdog": {"lease_seconds": 10}}))
        self.assertFalse(self.denied(self.pre("pause")))

    def test_other_orkestra_commands_and_other_projects_are_not_touched(self):
        self.start()
        for cmd in ("status", "next", "context", "phase show", "gate list", "task list"):
            self.assertFalse(self.denied(self.pre(cmd)), cmd)
        other, rid2, _ = self.second_run()                               # an open run that nobody drives
        out = self.hook("PreToolUse", {"session_id": self.intruder, "tool_name": "Bash", "cwd": str(self.proj),
                                       "tool_input": {"command": "orkestra pause -p other"}})
        self.assertFalse(self.denied(out))
        out = self.hook("PreToolUse", {"session_id": self.intruder, "tool_name": "Bash", "cwd": str(self.proj),
                                       "tool_input": {"command": "orkestra pause -p ghost"}})
        self.assertFalse(self.denied(out))                                # unknown project: the CLI reports it

    def test_nested_and_compound_commands_are_seen(self):
        self.start()
        for command in ("cd /tmp && orkestra pause -p demo", "true; orkestra abort weg -p demo",
                        "orkestra tdd red T001 -p demo -- orkestra pause -p demo", "bash -c 'orkestra pause -p demo'",
                        "orkestra pause --project demo", "orkestra --project=demo pause", "orkestra pause --run %s -p demo"):
            with self.subTest(command=command):
                command = command.replace("%s", self.rid)
                out = self.hook("PreToolUse", {"session_id": self.intruder, "tool_name": "Bash", "cwd": str(self.proj),
                                               "tool_input": {"command": command}})
                self.assertTrue(self.denied(out), command)

    def test_the_project_is_resolved_the_way_the_cli_does(self):
        from orkestra_core import cli, guards as G
        self.start()
        old = os.getcwd()
        os.chdir(self.proj)
        try:
            for arg in ("demo", ".", str(self.proj), "../demo", "nope", None):
                try:
                    want = cli._resolve_project(arg)
                except Exception:
                    want = None
                self.assertEqual(G._cli_project(arg, str(self.proj)), want, arg)
        finally:
            os.chdir(old)


class TestCursorOwnership(CursorBase):
    """The same rule in Cursor's dialect: the conversation that drives the run may pause it; another one may not,
    unless its user says so in that message."""

    def test_another_conversation_needs_the_users_word_to_pause(self):
        self.start()
        out = self.shell("orkestra pause -p demo", conv=OTHER, gen="g-b")
        self.assertTrue(self.no(out), out)
        self.assertIn("[orkestra:roles]", out["user_message"])
        self.assertEqual(out["user_message"].splitlines()[-1][:8], "unblock:")
        self.say("pause the run please", conv=OTHER, gen="g-c")
        self.assertEqual(self.shell("orkestra pause -p demo", conv=OTHER, gen="g-c"), {"permission": "allow"})
        self.say("make the header blue", conv=OTHER, gen="g-d")
        self.assertTrue(self.no(self.shell("orkestra pause -p demo", conv=OTHER, gen="g-d")))

    def test_the_driver_pauses_and_the_row_names_its_conversation(self):
        self.start()
        self.assertEqual(self.shell("orkestra pause -p demo"), {"permission": "allow"})
        self.cli("pause", "-p", "demo", env=self.shell_env)
        rows = [json.loads(x) for x in (self.proj / ".orkestra" / "runs" / self.rid / "activity.jsonl").read_text().splitlines()]
        row = [r for r in rows if r["kind"] == "status"][0]
        self.assertEqual(row["sid"], self.sid)


class TestStatusIsIdempotent(Base):
    def count(self, needle):
        return self.ledger_text().count(needle)

    def ledger_text(self):
        return (self.fdir / "ledger.md").read_text()

    def rows(self, kind="status"):
        p = self.proj / ".orkestra" / "runs" / self.rid / "activity.jsonl"
        out = [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
        return [r for r in out if r.get("kind") == kind]

    def test_the_same_status_twice_writes_one_ledger_line(self):
        self.start()
        self.cli("pause", "-p", "demo")
        n = self.count("→ paused")
        again = self.cli("pause", "-p", "demo").stdout
        self.assertEqual(self.count("→ paused"), n)
        self.assertEqual(n, 1)
        self.assertIn("[orkestra:unbind]", again)                         # a still-bound chat is released
        self.assertEqual(len(self.rows()), 1)
        self.cli("unblock", "-p", "demo")                                 # paused -> active is a change ...
        self.cli("unblock", "-p", "demo")                                 # ... active -> active is not
        self.assertEqual(self.count("→ active"), 1)

    def test_a_pause_leaves_a_visible_row_with_from_and_to(self):
        self.start()
        self.cli("pause", "-p", "demo", "topic changed")
        row = self.rows()[0]
        self.assertIn("active", row["what"])
        self.assertIn("paused", row["what"])
        self.assertEqual(row["agent"], "cli")

    def test_a_pause_by_a_chat_names_the_chat(self):
        self.start()
        ev = {"session_id": self.sid, "tool_name": "Bash", "cwd": str(self.proj),
              "tool_input": {"command": "orkestra pause -p demo"}}
        self.assertFalse(self.denied(self.hook("PreToolUse", ev)))
        self.cli("pause", "-p", "demo")
        row = self.rows()[0]
        self.assertEqual(row["sid"], self.sid)
        self.assertEqual(row["agent"], "main")
        self.assertIn("paused", row["what"])

    def test_the_row_is_in_the_language_of_the_cli(self):
        self.start()
        self.cli("pause", "-p", "demo", env={"ORKESTRA_LANG": "en"})
        self.assertRegex(self.rows()[0]["what"], r"^[^çğıöşÇĞİÖŞÜ]+$")
        self.cli("unblock", "-p", "demo", env={"ORKESTRA_LANG": "tr"})
        self.assertNotEqual(self.rows()[0]["what"], self.rows()[1]["what"])

    def test_block_and_await_update_when_the_reason_changes_only(self):
        self.start()
        self.cli("block", "-p", "demo", "waiting for keys")
        self.cli("block", "-p", "demo", "waiting for keys")
        self.assertEqual(self.count("→ blocked"), 1)
        self.cli("block", "-p", "demo", "waiting for a different thing")
        self.assertEqual(self.count("→ blocked"), 2)
        self.assertEqual(json.loads(self.cli("status", "--json", "-p", "demo").stdout)["blocked"]["reason"],
                         "waiting for a different thing")

    def test_a_closed_run_stays_closed(self):
        self.start()
        self.cli("abort", "-p", "demo", "weg")
        cp = self.cli("pause", "-p", "demo", "--run", self.rid, check=False)
        self.assertNotEqual(cp.returncode, 0)
        self.assertEqual(json.loads(self.cli("status", "--json", "-p", "demo", "--run", self.rid).stdout)["status"], "aborted")


class TestUnblockLine(unittest.TestCase):
    """(g): no run-guard denial leaves the agent without a way out."""

    CFG = DEFAULT_GLOBAL_CONFIG

    @staticmethod
    def binding(phase="implement", approved=True, status="active", project="/w/proj"):
        return {"project": project, "run_id": "001-x",
                "state": {"phase": phase, "status": status, "approved": {"deps": []} if approved else None}}

    def decide(self, tool, ti, **extra):
        ev = {"tool_name": tool, "tool_input": ti, "cwd": "/w/proj", "session_id": "s1"}
        phase = extra.pop("phase", "implement")
        ev.update(extra)
        return guards.pre_tool_decision(ev, self.CFG, self.binding(phase=phase), ())

    CASES = (
        ("isolation", "Write", {"file_path": "/etc/hosts", "content": "x"}, {}),
        ("phase", "Write", {"file_path": "/w/proj/src/a.py", "content": "x"}, {"phase": "plan"}),
        ("internals", "Write", {"file_path": "/w/proj/.orkestra/runs/001-x/state.json", "content": "{}"}, {}),
        ("roles", "Write", {"file_path": "/w/proj/src/a.py", "content": "x"}, {"agent_type": "orkestra:reviewer"}),
        ("deploy", "Bash", {"command": "vercel --prod"}, {}),
        ("deps", "Bash", {"command": "npm install left-pad"}, {}),
        ("wait", "Bash", {"command": "while true; do cat .orkestra/runs/001-x/state.json; sleep 5; done"}, {}),
        ("dialogs", "Bash", {"command": "security show-keychain-info login.keychain"}, {}),
        ("destructive", "Bash", {"command": "rm -rf src"}, {}),
    )

    def test_every_run_guard_denial_ends_with_one_unblock_line(self):
        for guard, tool, ti, extra in self.CASES:
            for who in ("driver", "subagent"):
                if who == "subagent" and (guard == "wait" or "agent_type" in extra):
                    continue                                    # subagents may wait; the roles case is one already
                with self.subTest(guard=guard, who=who):
                    more = dict(extra)
                    if who == "subagent":
                        more.update(agent_type="general-purpose", agent_id="a1")
                    d = self.decide(tool, ti, **more)
                    self.assertTrue(d.denied, f"{guard} was not denied")
                    self.assertEqual(d.guard, guard)
                    lines = d.reason.splitlines()
                    self.assertEqual(sum(1 for ln in lines if ln.startswith("unblock:")), 1, d.reason)
                    self.assertTrue(lines[-1].startswith("unblock:"), d.reason)
                    self.assertNotIn("\u2014", lines[-1])

    def test_the_driver_is_told_how_to_pause_or_abort(self):
        d = self.decide("Write", {"file_path": "/etc/hosts", "content": "x"})
        last = d.reason.splitlines()[-1]
        self.assertIn("orkestra pause -p /w/proj", last)
        self.assertIn("orkestra abort", last)

    def test_a_subagent_is_told_that_the_user_decides(self):
        d = self.decide("Write", {"file_path": "/etc/hosts", "content": "x"}, agent_type="general-purpose", agent_id="a1")
        last = d.reason.splitlines()[-1]
        self.assertIn("not the driver", last)
        self.assertIn("001-x", last)
        self.assertIn("/w/proj", last)
        self.assertIn("user", last)

    def test_denials_outside_a_run_are_unchanged(self):
        d = guards.pre_tool_decision({"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": "/w/proj"},
                                     self.CFG, None, ())
        self.assertTrue(d.denied)
        self.assertNotIn("unblock:", d.reason)


class TestInternalsFalsePositives(unittest.TestCase):
    """(h): reading the plugin is fine; forging through it is not."""

    CFG = DEFAULT_GLOBAL_CONFIG
    P = str(PLUGIN)

    def decide(self, cmd):
        b = {"project": "/w/proj", "run_id": "001-x",
             "state": {"phase": "implement", "status": "active", "approved": {"deps": []}}}
        return guards.pre_tool_decision({"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": "/w/proj",
                                         "session_id": "s1"}, self.CFG, b, ())

    def test_read_only_inspection_is_allowed(self):
        P = self.P
        for cmd in (f"ls {P}/lib | head -3", f"grep -n deny {P}/lib/orkestra_core/guards.py | head -3",
                    f"git -C {P} log --oneline -3", f"find {P}/lib -name '*.py' | wc -l",
                    f"ls {P}/lib | head -2; sysctl -n hw.ncpu", f"wc -l {P}/lib/orkestra_core/*.py | tail -1; date",
                    f"cat {P}/hooks/hooks.json | head -3; uname -a", f"stat {P}/hooks/dispatch.py; whoami",
                    f"find {P} -type f -newer /tmp/x | sort | head", f"ls {P}/lib && sw_vers && ps -o pid= -p 1",
                    f"python3 -c 'print(1)' # looks at {P}/lib",
                    f"shasum {P}/hooks/dispatch.py", f"du -sh {P}/lib; df -h /"):
            with self.subTest(cmd=cmd):
                d = self.decide(cmd)
                self.assertFalse(d.denied, f"{cmd} -> {d.guard} {d.reason[:80]}")

    def test_forging_stays_refused(self):
        P = self.P
        for cmd in (f"python3 {P}/hooks/dispatch.py UserPromptSubmit",
                    "python3 - <<'EOF'\nimport orkestra_core\nEOF",
                    "cat > x.py <<'EOF'\nfrom orkestra_core import runs\nEOF",
                    f"ls {P}/lib; python3 -m unittest -q", f"ls {P}/lib | python3",
                    f"cat {P}/hooks/dispatch.py | tee /tmp/d.py", f"cat {P}/hooks/dispatch.py > /tmp/d.py",
                    f"ls {P}/lib | head -2 > /tmp/out", f"find {P} -name '*.py' -exec python3 {{}} \\;",
                    f"find {P} -name '*.py' -delete", f"cp {P}/hooks/dispatch.py /tmp/d.py",
                    "python3 -c \"import orkestra_core\" # fine", f"echo a; python3 {P}/hooks/dispatch.py x",
                    f"awk 'BEGIN{{system(\"python3 {P}/hooks/dispatch.py x\")}}'",
                    f"ls {P}/lib; node -e 'require(1)'",
                    f"echo x | xargs python3 {P}/lib/orkestra_core/state.py", f"ln -s {P}/hooks/dispatch.py /tmp/d",
                    f"ls {P}/lib; make test"):          # a program that is not provably read-only (GD-1: HEAD refused it too)
            with self.subTest(cmd=cmd):
                d = self.decide(cmd)
                self.assertTrue(d.denied and d.guard == "internals", f"{cmd!r} -> {d.guard or 'allowed'}")

    def test_a_comment_is_not_a_command_but_a_quoted_string_is(self):
        P = self.P
        self.assertFalse(self.decide(f"echo hello # {P}/lib").denied)
        self.assertTrue(self.decide(f"python3 -c 'import orkestra_core  # {P}/lib'").denied)
        self.assertTrue(self.decide(f"python3 -c \"x = 1 # \" ; python3 {P}/hooks/dispatch.py").denied)


if __name__ == "__main__":
    unittest.main()
