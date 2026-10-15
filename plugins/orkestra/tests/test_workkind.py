"""FR-001, FR-002, FR-003: classify returns only software, writing, or general."""

import argparse
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import helpers
from orkestra_core.workkind import WORK_KINDS, classify

# Explicit markers beat hints. kind takes software|writing|general; is/iş takes yazilim|makale|genel.
_EXPLICIT = (
    ("kind: writing fix the bug", "writing"),
    ("iş: yazilim makale yaz", "software"),
    ("kind: writing iş: yazılım", "software"),
    ("kind: general iş: makale", "general"),
    ("kind: other", "software"),
    ("the task is: writing a parser", "software"),
    ("iş: yazılım", "software"),
    ("kind: writing", "writing"),
    ("kind: makale fix the bug", "software"),
    ("is: general", "software"),
    ("KIND: Writing fix the bug", "writing"),
    ("kind:writing", "writing"),
    ("kind : general", "general"),
    ("İŞ: YAZILIM", "software"),
    ("is : makale", "writing"),
)

# After ASCII fold, software hints beat writing, writing beats general, else software.
_HINTS = (
    ("çeviri fonksiyonu ekle", "software"),
    ("çeviri fonksiyonları ekle", "software"),
    ("report-api ekle", "software"),
    ("deneme yaz", "writing"),
    ("deneme yazısı", "software"),
    ("paragrafı çevir", "writing"),
    ("aylık raporu", "writing"),
    ("alışveriş listesi", "general"),
    ("e-posta", "general"),
    ("rapor ve kontrol listesi", "writing"),
    ("yarın konuşalım", "software"),
)


class TestClassify(unittest.TestCase):
    def test_blank_and_non_text_are_software(self):
        self.assertEqual(WORK_KINDS, ("software", "writing", "general"))
        for text in ("", " ", "\t", "\n", " \t\n ", None, 1):
            self.assertEqual(classify(text), "software", repr(text))
            self.assertEqual(classify(text), classify(text), repr(text))

    def test_explicit_markers(self):
        for text, want in _EXPLICIT:
            self.assertIn(want, WORK_KINDS)
            self.assertEqual(classify(text), want, text)
            self.assertEqual(classify(text), classify(text), text)

    def test_hints_fold_and_word_boundaries(self):
        for text, want in _HINTS:
            self.assertIn(want, WORK_KINDS)
            self.assertEqual(classify(text), want, text)
            self.assertEqual(classify(text), classify(text), text)

    def test_module_source_has_no_network_or_model(self):
        src = (Path(__file__).resolve().parents[1] / "lib" / "orkestra_core" / "workkind.py").read_text(
            encoding="utf-8")
        for banned in ("urllib", "socket", "subprocess", "openai"):
            self.assertNotIn(banned, src)


class TestRunState(helpers.Sandbox):
    """FR-004, FR-007: a new run stores work_kind; the software path and grants stay."""

    def test_missing_or_foreign_kind_is_software(self):
        from orkestra_core.state import new_state
        from orkestra_core.workkind import kind_of

        for st in ({}, {"phase": "intake"}, {"work_kind": "other"}, None, "writing"):
            self.assertEqual(kind_of(st), "software", repr(st))
        bare = new_state(self.tmp / "bare", "000-bare", "t", "M", "delivery")
        foreign = new_state(self.tmp / "foreign", "000-foreign", "t", "M", "delivery", work_kind="other")
        self.assertEqual(bare["work_kind"], "software")
        self.assertEqual(foreign["work_kind"], "software")
        self.assertEqual(kind_of({"work_kind": "writing"}), "writing")
        self.assertEqual(kind_of({"work_kind": "general"}), "general")

    def test_start_and_open_store_classified_kind(self):
        from orkestra_core.runs import open_run_for_request, start_run
        from orkestra_core.state import load_state
        from orkestra_core.workkind import kind_of

        writing = "kind: writing bir makale"
        article = self.make_project("article")
        opened_proj = self.make_project("article-open")
        started = start_run(article, "makale", writing, "M", "delivery")
        self.assertEqual(started["work_kind"], "writing")
        self.assertEqual(kind_of(load_state(article, started["run_id"])), "writing")
        opened = open_run_for_request(opened_proj, writing, engine="claude", mode="delivery")
        self.assertEqual(opened["work_kind"], "writing")
        self.assertEqual(kind_of(load_state(opened_proj, opened["run_id"])), "writing")

        general = "kind: general"
        listed = self.make_project("listed")
        gst = start_run(listed, "liste", general, "S", "delivery")
        self.assertEqual(gst["work_kind"], "general")
        self.assertEqual(kind_of(load_state(listed, gst["run_id"])), "general")

    def test_phases_and_advisory_helpers_stay(self):
        from orkestra_core.intent import infer_size, is_advisory_request, skips_adversary, skips_plan_critic
        from orkestra_core.state import PHASES

        text = "explain how parse works"
        self.assertEqual(infer_size(text), "XS")
        self.assertTrue(is_advisory_request(text))
        self.assertFalse(skips_plan_critic("M"))
        self.assertTrue(skips_plan_critic("XS"))
        self.assertFalse(skips_adversary("M"))
        self.assertTrue(skips_adversary("XS"))
        self.assertEqual(PHASES, ["intake", "discover", "clarify", "plan", "approve",
                                  "implement", "verify", "review", "deliver", "done"])

    def test_general_run_does_not_grant_commit_and_node_gate_stays(self):
        from orkestra_core.gates import detect
        from orkestra_core.intent import git_grants
        from orkestra_core.paths import DEFAULT_GLOBAL_CONFIG

        self.assertNotIn("commit", git_grants("kind: general"))
        guards = DEFAULT_GLOBAL_CONFIG["guards"]
        self.assertTrue(guards["git"])
        self.assertTrue(guards["secrets"])
        self.assertTrue(guards["deploy"])
        node = self.tmp / "nodeapp"
        node.mkdir()
        (node / "package.json").write_text('{"name":"n","scripts":{"test":"node --test"}}\n', encoding="utf-8")
        self.assertEqual([g["cmd"] for g in detect(node) if g["kind"] == "test"], ["npm test"])


_PLAN_SPEC = "- **FR-005**: yalniz teslim\n- **SC-003**: E5\n"
_SOFT_SPEC = "- **FR-001**: code\n- **SC-001**: same\n"


def _task_plan(files: str, tdd: str, spec_id: str = "FR-005", sc: str = "SC-003") -> str:
    return (
        "---\n"
        "approved_dependencies: []\n"
        "---\n"
        "### T001: Deliver\n"
        f"- Files: {files}\n"
        f"- Acceptance: metin ({spec_id})\n"
        "- Test: `python3 -m unittest test_workkind.TestPlanRule -q`\n"
        f"- TDD: {tdd}\n"
        "\n"
        f"{sc}\n"
    )


def _good_plan(rid: str) -> str:
    feature = f".orkestra/features/{rid}/deliverable.md"
    current = ".orkestra/current/deliverable.md"
    both = f"`{feature}` (yeni), `{current}` (yeni)"
    return (
        "---\n"
        "approved_dependencies: []\n"
        "---\n"
        "### T001: Turkish reason\n"
        f"- Files: {both}\n"
        "- Acceptance: metin (FR-005)\n"
        "- Test: `python3 -m unittest test_workkind.TestPlanRule -q`\n"
        "- TDD: n/a (içerik kontrolü)\n"
        "\n"
        "### T002: English reason\n"
        f"- Files: {both}\n"
        "- Acceptance: ayni teslim\n"
        "- Test: `python3 -m unittest test_workkind.TestPlanRule -q`\n"
        "- TDD: n/a (content check)\n"
        "\n"
        "SC-003\n"
    )


class TestPlanRule(helpers.Sandbox):
    """FR-005: writing and general plans allow only the deliverable, with a content-check TDD reason."""

    def _open(self, name: str, request: str):
        from orkestra_core.runs import start_run

        project = self.make_project(name)
        st = start_run(project, name, request, "S", "delivery")
        return project, st["run_id"]

    def _write(self, project, rid: str, plan: str, spec: str = _PLAN_SPEC) -> None:
        from orkestra_core import paths

        fdir = paths.feature_dir(project, rid)
        (fdir / "spec.md").write_text(spec, encoding="utf-8")
        (fdir / "plan.md").write_text(plan, encoding="utf-8")

    def _ready(self, project, rid: str) -> None:
        from orkestra_core import paths
        from orkestra_core.runs import spec_plan_hash
        from orkestra_core.util import write_json_atomic

        rdir = paths.run_dir(project, rid)
        write_json_atomic(rdir / "approval.json", {
            "decision": "approve", "source": "test", "detail": "",
            "hash": spec_plan_hash(project, rid), "at": "2026-10-15T00:00:00Z",
        })
        (rdir / "gates-baseline.json").write_text("{}\n", encoding="utf-8")

    def _gates(self, project, rid: str):
        from orkestra_core import planparse as PP
        from orkestra_core.cli import cmd_check
        from orkestra_core.runs import preconditions
        from orkestra_core.state import load_state

        st = load_state(project, rid)
        calls = []
        orig = PP.check_plan

        def wrapped(*args, **kwargs):
            calls.append(kwargs)
            return orig(*args, **kwargs)

        with mock.patch("orkestra_core.planparse.check_plan", side_effect=wrapped):
            approve = preconditions(project, st, "approve")
            implement = preconditions(project, st, "implement")
            buf = StringIO()
            with redirect_stdout(buf):
                rc = cmd_check(argparse.Namespace(project=str(project), run=rid, cmd="plan"))
        return calls, approve, implement, rc, buf.getvalue()

    def _assert_passed(self, calls, kind: str, rid: str) -> None:
        self.assertEqual(len(calls), 3, calls)
        for kwargs in calls:
            self.assertEqual(kwargs.get("work_kind"), kind, kwargs)
            self.assertEqual(kwargs.get("run_id"), rid, kwargs)

    def test_gates_reject_product_tdd_and_runs_path(self):
        kinds = (("writing", "kind: writing bir makale"), ("general", "kind: general"))
        for kind, request in kinds:
            project, rid = self._open(f"bad-{kind}", request)
            feature = f".orkestra/features/{rid}/deliverable.md"
            current = ".orkestra/current/deliverable.md"
            runs = f".orkestra/runs/{rid}/deliverable.md"
            cases = (
                ("product", "`src/app.py` (yeni)", "n/a (content check)", "src/app.py"),
                ("tdd-required", f"`{feature}` (yeni)", "required", "TDD"),
                ("docs-only", f"`{current}` (yeni)", "n/a (docs only)", "TDD"),
                ("runs", f"`{runs}` (yeni)", "n/a (içerik kontrolü)", runs),
                ("other-run", "`.orkestra/features/999-other/deliverable.md` (yeni)",
                 "n/a (content check)", ".orkestra/features/999-other/deliverable.md"),
            )
            for name, files, tdd, token in cases:
                with self.subTest(kind=kind, case=name):
                    self._write(project, rid, _task_plan(files, tdd))
                    calls, approve, implement, rc, out = self._gates(project, rid)
                    self._assert_passed(calls, kind, rid)
                    self.assertTrue(any(token in p for p in approve), approve)
                    self.assertTrue(any(token in p for p in implement), implement)
                    self.assertIn(token, out)
                    self.assertEqual(rc, 1)

    def test_gates_accept_feature_and_current_deliverable(self):
        kinds = (("writing", "kind: writing bir makale"), ("general", "kind: general"))
        for kind, request in kinds:
            with self.subTest(kind=kind):
                project, rid = self._open(f"ok-{kind}", request)
                self._write(project, rid, _good_plan(rid))
                self._ready(project, rid)
                calls, approve, implement, rc, out = self._gates(project, rid)
                self.assertEqual(approve, [], approve)
                self.assertEqual(implement, [], implement)
                self.assertEqual(rc, 0, out)
                self.assertIn("plan check: OK", out)
                self._assert_passed(calls, kind, rid)

    def test_hidden_product_paths_and_loose_reasons_are_rejected(self):
        project, rid = self._open("hide", "kind: writing bir makale")
        feature = f".orkestra/features/{rid}/deliverable.md"
        hidden = (
            ("bare", f"`{feature}` (yeni), src/app.py", "n/a (content check)", "src/app.py"),
            ("second-line", None, "n/a (content check)", "src/app.py"),
            ("not-a-check", f"`{feature}` (yeni)", "n/a (not a content check)", "TDD"),
            ("national", f"`{feature}` (yeni)", "n/a (national (content check))", "TDD"),
        )
        for name, files, tdd, token in hidden:
            with self.subTest(case=name):
                if name == "second-line":
                    plan = _task_plan(f"`src/app.py` (yeni)", tdd).replace(
                        f"- TDD: {tdd}",
                        f"- Files: `{feature}` (yeni)\n- TDD: {tdd}",
                        1,
                    )
                else:
                    plan = _task_plan(files, tdd)
                self._write(project, rid, plan)
                _calls, approve, implement, rc, out = self._gates(project, rid)
                self.assertTrue(any(token in p for p in approve), approve)
                self.assertTrue(any(token in p for p in implement), implement)
                self.assertIn(token, out)
                self.assertEqual(rc, 1)

    def test_software_and_missing_kind_keep_today(self):
        from orkestra_core import planparse as PP
        from orkestra_core.state import StateTxn

        project, rid = self._open("soft", "kind: software fix the function")
        plan = _task_plan("`src/app.py` (yeni)", "required", spec_id="FR-001", sc="SC-001")
        self._write(project, rid, plan, _SOFT_SPEC)
        self._ready(project, rid)
        today = PP.check_plan(_SOFT_SPEC, plan, project)
        calls, approve, implement, rc, out = self._gates(project, rid)
        self.assertEqual(approve, today, approve)
        self.assertEqual(implement, today, implement)
        self.assertEqual(rc, 0 if not today else 1, out)
        self.assertFalse(any("src/app.py" in p for p in today))
        self._assert_passed(calls, "software", rid)

        with StateTxn(project, rid) as st:
            st.pop("work_kind", None)
        calls, approve, implement, rc, out = self._gates(project, rid)
        self.assertEqual(approve, today, approve)
        self.assertEqual(implement, today, implement)
        self.assertEqual(rc, 0 if not today else 1, out)
        self._assert_passed(calls, "software", rid)
        self.assertEqual(PP.check_plan(_SOFT_SPEC, plan, project, work_kind="software", run_id=rid), today)
        self.assertEqual(PP.check_plan(_SOFT_SPEC, plan, project, work_kind=None, run_id=rid), today)

        thin = (
            "---\napproved_dependencies: []\n---\n"
            "### T001: App\n- Files: `src/app.py` (yeni)\n- Acceptance: x (FR-001)\n- TDD: required\n\nSC-001\n"
        )
        bare = PP.check_plan(_SOFT_SPEC, thin)
        self.assertTrue(any("Test" in p for p in bare), bare)
        self.assertEqual(PP.check_plan(_SOFT_SPEC, thin, work_kind="software", run_id=rid), bare)
        self.assertEqual(PP.check_plan(_SOFT_SPEC, thin, run_id=rid), bare)
        self.assertFalse(any("deliverable" in p for p in bare))


# Exact level-2 titles. An en dash is allowed; an em dash is not. Hyphen is allowed.
_GOOD_TR = (
    "## İstek\n"
    "Write the piece - keep it short.\n"
    "\n"
    "## Teslim\n"
    "The piece is ready \u2013 ship it.\n"
    "\n"
    "## Sınırlar\n"
    "Feature folder only.\n"
)
_GOOD_EN = (
    "## Request\n"
    "Write the piece - keep it short.\n"
    "\n"
    "## Deliverable\n"
    "The piece is ready \u2013 ship it.\n"
    "\n"
    "## Boundaries\n"
    "Feature folder only.\n"
)
_KINDS = (("writing", "kind: writing bir makale"), ("general", "kind: general"))


class TestDeliverable(helpers.Sandbox):
    """FR-006: writing and general cannot enter verify without a valid feature deliverable.md."""

    def _open(self, name: str, request: str):
        from orkestra_core.runs import start_run
        from orkestra_core.state import StateTxn

        project = self.make_project(name)
        st = start_run(project, name, request, "S", "delivery")
        rid = st["run_id"]
        with StateTxn(project, rid) as live:
            live["tasks"] = {"T001": {"status": "done", "tdd": "n/a", "title": "Deliver"}}
        return project, rid

    def _feature(self, project, rid: str) -> Path:
        from orkestra_core import paths

        return paths.feature_dir(project, rid) / "deliverable.md"

    def _runs_file(self, project, rid: str) -> Path:
        from orkestra_core import paths

        return paths.run_dir(project, rid) / "deliverable.md"

    def _put(self, project, rid: str, text: str = None, raw: bytes = None, feature: bool = True) -> Path:
        path = self._feature(project, rid)
        if path.exists():
            path.unlink()
        if feature and raw is not None:
            path.write_bytes(raw)
        elif feature and text is not None:
            path.write_text(text, encoding="utf-8")
        return path

    def _verify(self, project, rid: str):
        from orkestra_core.runs import preconditions
        from orkestra_core.state import load_state

        return preconditions(project, load_state(project, rid), "verify")

    def _gates(self, project):
        from orkestra_core.paths import project_config

        return project_config(project).get("gates") or []

    def test_verify_accepts_exact_slots_with_en_dash(self):
        self.assertIn("\u2013", _GOOD_TR)
        self.assertIn("-", _GOOD_TR)
        self.assertNotIn("\u2014", _GOOD_TR)
        self.assertIn("\u2013", _GOOD_EN)
        self.assertNotIn("\u2014", _GOOD_EN)
        for kind, request in _KINDS:
            project, rid = self._open(f"ok-{kind}", request)
            before = self._gates(project)
            for label, body in (("tr", _GOOD_TR), ("en", _GOOD_EN)):
                with self.subTest(kind=kind, headings=label):
                    self._put(project, rid, text=body)
                    self.assertEqual(self._verify(project, rid), [])
            self.assertEqual(self._gates(project), before)
            self.assertFalse(any("deliverable" in str(gate) for gate in before))

    def test_verify_rejects_missing_empty_encoding_headings_and_runs(self):
        hash3 = "### İstek\nbody\n### Teslim\nbody\n### Sınırlar\nbody\n"
        lower = "## istek\nbody\n## Teslim\nbody\n## Sınırlar\nbody\n"
        extra = "## İstek notu\nbody\n## Teslim\nbody\n## Sınırlar\nbody\n"
        em = _GOOD_TR + "\nA note \u2014 here.\n"
        for kind, request in _KINDS:
            project, rid = self._open(f"bad-{kind}", request)
            before = self._gates(project)
            cases = (
                ("missing", None, None, False, "deliverable.md missing"),
                ("empty", " \n\t\n", None, True, "empty"),
                ("utf8", None, b"\xff", True, "not UTF-8"),
                ("hash3", hash3, None, True, "İstek"),
                ("lower", lower, None, True, "İstek"),
                ("extra", extra, None, True, "İstek"),
                ("emdash", em, None, True, "U+2014"),
            )
            for name, text, raw, feature, token in cases:
                with self.subTest(kind=kind, case=name):
                    self._put(project, rid, text=text, raw=raw, feature=feature)
                    problems = self._verify(project, rid)
                    self.assertTrue(any(token in p for p in problems), (name, token, problems))
            with self.subTest(kind=kind, case="runs-only"):
                self._put(project, rid, feature=False)
                runs = self._runs_file(project, rid)
                runs.write_text(_GOOD_TR, encoding="utf-8")
                self.assertTrue(runs.is_file())
                self.assertFalse(self._feature(project, rid).exists())
                problems = self._verify(project, rid)
                self.assertTrue(any("deliverable.md missing" in p for p in problems), problems)
            self.assertEqual(self._gates(project), before)

    def test_software_does_not_require_deliverable(self):
        from orkestra_core.state import StateTxn

        project, rid = self._open("soft", "kind: software fix the function")
        before = self._gates(project)
        self.assertFalse(self._feature(project, rid).exists())
        self.assertEqual(self._verify(project, rid), [])
        self._put(project, rid, text="### İstek\n\u2014\n")
        self.assertEqual(self._verify(project, rid), [])
        with StateTxn(project, rid) as live:
            live.pop("work_kind", None)
        self._put(project, rid, feature=False)
        self.assertEqual(self._verify(project, rid), [])
        self.assertEqual(self._gates(project), before)

    def test_repo_gate_list_has_no_deliverable_command(self):
        from orkestra_core.paths import project_config

        root = Path(__file__).resolve().parents[3]
        gates = project_config(root).get("gates") or []
        self.assertGreaterEqual(len(gates), 1)
        self.assertFalse(any("deliverable" in str(gate) for gate in gates))


# The size-S sentence, including the en dash. A writing/general hint keeps this and adds the deliverable rule.
_SHORT_PLAN = (
    "write a short plan.md yourself (1\u20133 tasks), then `orkestra plan check` (`orkestra playbook plan`)"
)
_ARCHITECT = "dispatch orkestra:architect to write plan.md (`orkestra playbook plan`)"


class TestPlanHint(helpers.Sandbox):
    """FR-005: an empty plan for writing or general names deliverable.md and does not dispatch the architect."""

    def _hint(self, name: str, request: str, size: str, *, drop_kind: bool = False):
        from orkestra_core import paths
        from orkestra_core.runs import start_run
        from orkestra_core.state import StateTxn, load_state
        from orkestra_core.tempo import next_action

        project = self.make_project(name)
        st = start_run(project, name, request, size, "delivery")
        rid = st["run_id"]
        with StateTxn(project, rid) as live:
            live["phase"] = "plan"
            live["size"] = size
            if drop_kind:
                live.pop("work_kind", None)
        (paths.feature_dir(project, rid) / "plan.md").write_text("\n", encoding="utf-8")
        return rid, next_action(project, load_state(project, rid), deep=False)

    def test_empty_plan_writing_or_general_names_deliverable_not_architect(self):
        cases = (
            ("writing", "kind: writing bir makale", "M"),
            ("general", "kind: general", "M"),
            ("writing", "kind: writing bir makale", "S"),
            ("general", "kind: general", "S"),
        )
        for kind, request, size in cases:
            with self.subTest(kind=kind, size=size):
                rid, action = self._hint(f"{kind}-{size}", request, size)
                text = action["text"]
                self.assertEqual(action["action"], "work")
                self.assertEqual(action["why"], "plan.md does not exist")
                self.assertIn("deliverable.md", text)
                self.assertIn("TDD: n/a", text)
                self.assertIn("content check", text)
                self.assertIn(f".orkestra/features/{rid}/deliverable.md", text)
                self.assertIn(".orkestra/current/deliverable.md", text)
                self.assertIn("(new)", text)
                self.assertNotIn("architect", text)
                self.assertNotIn("dispatch", text)
                if size == "S":
                    self.assertIn(f"{size}: {_SHORT_PLAN}", text)

    def test_missing_kind_keeps_today_plan_hint(self):
        _, medium = self._hint("miss-m", "kind: writing bir makale", "M", drop_kind=True)
        self.assertEqual(medium["action"], "dispatch")
        self.assertEqual(medium["text"], _ARCHITECT)
        self.assertEqual(medium["why"], "plan.md does not exist")
        self.assertNotIn("deliverable.md", medium["text"])

        _, small = self._hint("miss-s", "kind: general", "S", drop_kind=True)
        self.assertEqual(small["action"], "work")
        self.assertIn(f"S: {_SHORT_PLAN}", small["text"])
        self.assertNotIn("deliverable.md", small["text"])
        self.assertNotIn("architect", small["text"])
