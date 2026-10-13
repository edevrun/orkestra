import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from orkestra_core import gates as G
from orkestra_core import planparse as PP
from orkestra_core import util as U
from orkestra_core.cli import hoist_globals


class TestHoist(unittest.TestCase):
    def test_hoist_anywhere(self):
        self.assertEqual(hoist_globals(["task", "done", "T001", "-p", "demo", "ok"]), ["-p", "demo", "task", "done", "T001", "ok"])
        self.assertEqual(hoist_globals(["status", "--run", "001-x"]), ["--run", "001-x", "status"])

    def test_tdd_command_untouched(self):
        argv = ["tdd", "red", "T001", "pytest", "-p", "no:cacheprovider"]
        self.assertEqual(hoist_globals(argv), argv)
        self.assertEqual(hoist_globals(["-p", "x", "tdd", "green", "T1", "--", "npm", "test"]),
                         ["-p", "x", "tdd", "green", "T1", "--", "npm", "test"])


class TestPlanParse(unittest.TestCase):
    PLAN = """---
feature: x
approved_dependencies: [zod, "@tanstack/react-query"]
---
## Görevler
### T001 — First
- Dosyalar: `a.ts`, `a.test.ts`
- Bağımlı: —
- Kabul: Given x Then y (FR-001)
- Test: `npm test -- a`
- TDD: required

### T002: Second
- **Files**: `b.ts`
- Depends: T001
- Acceptance: covers FR-002
- Test: `npm test -- b`
- TDD: n/a (config only)

## Test stratejisi
SC-001
"""

    def test_frontmatter_lists(self):
        fm, _ = PP.parse_frontmatter(self.PLAN)
        self.assertEqual(fm["approved_dependencies"], ["zod", "@tanstack/react-query"])
        fm2, _ = PP.parse_frontmatter("---\napproved_dependencies:\n  - a\n  - b\nrisk: low\n---\nbody")
        self.assertEqual(fm2["approved_dependencies"], ["a", "b"])
        self.assertEqual(fm2["risk"], "low")

    def test_tasks(self):
        tasks = PP.parse_tasks(self.PLAN)
        self.assertEqual([t.id for t in tasks], ["T001", "T002"])
        self.assertEqual(tasks[0].files, ["a.ts", "a.test.ts"])
        self.assertEqual(tasks[1].depends, ["T001"])
        self.assertEqual(tasks[1].tdd, "n/a")
        self.assertEqual(tasks[0].tdd, "required")

    def test_check_plan_ok_and_coverage(self):
        spec = "FR-001 FR-002 SC-001"
        self.assertEqual(PP.check_plan(spec, self.PLAN), [])
        probs = PP.check_plan(spec + " FR-003", self.PLAN)
        self.assertTrue(any("FR-003" in p for p in probs))

    def test_check_plan_requires_fields(self):
        bad = "---\napproved_dependencies: []\n---\n### T001 — x\n- Dosyalar: `a`\n"
        probs = PP.check_plan("FR-001", bad)
        self.assertTrue(any("Kabul" in p for p in probs))
        self.assertTrue(any("Test" in p for p in probs))

    def test_trace(self):
        spec = "FR-001 SC-001"
        self.assertEqual(PP.check_trace(spec, "| FR-001 | t | PASS |\n| SC-001 | t | PASS |"), [])
        self.assertTrue(PP.check_trace(spec, "| FR-001 | t | PASS |"))
        self.assertTrue(PP.check_trace(spec, "| FR-001 | t | FAIL |\n| SC-001 | t | PASS |"))

    def test_triage(self):
        txt = "| R-001 | rev | x | fix | open |\n| R-002 | adv | y | reject | — |\n| R-003 | adv | z | fix | done |"
        self.assertEqual(PP.open_triage_items(txt), ["R-001"])

    def test_verdict_last_wins(self):
        self.assertEqual(PP.verdict("VERDICT: <PASS|FAIL>\n...\nVERDICT: FAIL\n"), "FAIL")
        self.assertEqual(PP.verdict("VERDICT: PASS\nround 2\nVERDICT: FAIL"), "FAIL")

    def test_verdict(self):
        self.assertEqual(PP.verdict("...\nVERDICT: PASS_WITH_NOTES\n"), "PASS_WITH_NOTES")
        self.assertEqual(PP.verdict("**VERDICT:** FAIL"), "FAIL")


class TestGateDetect(helpers.Sandbox):
    def proj(self, files):
        p = self.ws / "p"
        p.mkdir(exist_ok=True)
        for name, content in files.items():
            (p / name).parent.mkdir(parents=True, exist_ok=True)
            (p / name).write_text(content)
        return p

    def names(self, p):
        return {g["name"]: g["cmd"] for g in G.detect(p)}

    def test_node_pnpm(self):
        p = self.proj({"package.json": json.dumps({"scripts": {"test": "vitest run", "lint": "eslint .", "typecheck": "tsc -b", "build": "vite build"}}),
                       "pnpm-lock.yaml": ""})
        n = self.names(p)
        self.assertEqual(n["test"], "pnpm run test")
        self.assertEqual(n["typecheck"], "pnpm run typecheck")
        self.assertIn("build", n)

    def test_node_default_test_script_ignored(self):
        p = self.proj({"package.json": json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}),
                       "tsconfig.json": "{}"})
        n = self.names(p)
        self.assertNotIn("test", n)
        self.assertEqual(n["typecheck"], "npx --no-install tsc --noEmit")

    def test_python_uv(self):
        p = self.proj({"pyproject.toml": "[project]\nname='x'\ndependencies=['requests>=2']\n[tool.ruff]\n", "uv.lock": "", "tests/test_a.py": ""})
        n = self.names(p)
        self.assertEqual(n["pytest"], "uv run pytest -q")
        self.assertEqual(n["ruff"], "uv run ruff check .")

    def test_go_flutter_dotnet(self):
        self.assertIn("go-test", self.names(self.proj({"go.mod": "module x\n"})))
        p2 = self.ws / "f"
        p2.mkdir()
        (p2 / "pubspec.yaml").write_text("name: x\ndependencies:\n  flutter:\n    sdk: flutter\n")
        (p2 / "test").mkdir()
        self.assertIn("flutter-test", {g["name"] for g in G.detect(p2)})
        p3 = self.ws / "d"
        p3.mkdir()
        (p3 / "App.sln").write_text("")
        self.assertIn("dotnet-test", {g["name"] for g in G.detect(p3)})

    def test_multi_stack_monorepo(self):
        p = self.proj({"package.json": json.dumps({"scripts": {"test:web": "x", "deploy:api": "y"}}),
                       "backend/pom.xml": "<project/>", "backend/mvnw": "",
                       "apps/web/package.json": json.dumps({"scripts": {"test": "vitest run", "build": "next build"}}),
                       "apps/mobile/pubspec.yaml": "name: m\ndependencies:\n  flutter:\n    sdk: flutter\n",
                       "apps/mobile/test/a_test.dart": ""})
        gs = {g["name"]: g for g in G.detect(p)}
        self.assertEqual(gs["test"]["cmd"], "npm run test:web")
        self.assertEqual(gs["backend:maven-test"]["cmd"], "./mvnw -q test")
        self.assertEqual(gs["backend:maven-test"]["cwd"], "backend")
        self.assertEqual(gs["web:test"]["cwd"], "apps/web")
        self.assertIn("mobile:flutter-test", gs)

    def test_dotnet_nested_tests(self):
        p = self.proj({"tests/Pomi.Engine.Tests/Pomi.Engine.Tests.csproj": "<Project/>", "tools/X/X.csproj": "<Project/>"})
        gs = {g["name"]: g for g in G.detect(p)}
        self.assertEqual(gs["dotnet-test"]["cmd"], "dotnet test tests/Pomi.Engine.Tests --nologo -v q")

    def test_dependency_sets(self):
        p = self.proj({"package.json": json.dumps({"dependencies": {"react": "1"}, "devDependencies": {"vitest": "1"}}),
                       "requirements.txt": "requests==2.0\n# c\nflask>=1\n"})
        d = G.dependency_sets(p)
        self.assertEqual(d["package.json"], ["react", "vitest"])
        self.assertEqual(d["requirements.txt"], ["flask", "requests"])
        self.assertEqual(G.new_dependencies(d, {"package.json": ["react", "vitest", "zod"]}), ["zod"])

    def test_disabled_gate_skipped(self):
        p = self.proj({"x.txt": ""})
        gates = [{"name": "a", "cmd": "true"}, {"name": "slow", "cmd": "false", "enabled": False}]
        res = G.run_all(p, gates, self.tmp / "logs", "t")
        self.assertEqual([r["name"] for r in res], ["a"])
        self.assertEqual([r["name"] for r in G.run_all(p, gates, self.tmp / "logs", "t", only=["slow"])], ["slow"])

    def test_normalize_pkg(self):
        self.assertEqual(G.normalize_pkg("zod@3.23"), "zod")
        self.assertEqual(G.normalize_pkg("@tanstack/react-query@5.1"), "@tanstack/react-query")
        self.assertEqual(G.normalize_pkg("requests==2.31"), "requests")


class TestFileLock(unittest.TestCase):
    """FR-001 / SC-001: POSIX fcntl, Windows msvcrt, else no-op."""

    def _path(self, td: str) -> Path:
        return Path(td) / "state.json"

    def test_e1_msvcrt_locking_when_fcntl_absent(self):
        msvcrt = mock.Mock()
        msvcrt.LK_LOCK = 1
        msvcrt.LK_UNLCK = 0
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(U, "fcntl", None), \
                 mock.patch.object(U, "msvcrt", msvcrt, create=True):
                with U.file_lock(self._path(td)):
                    pass
        calls = msvcrt.locking.call_args_list
        self.assertGreaterEqual(len(calls), 2, "msvcrt.locking must lock then unlock (no-op forbidden)")
        self.assertEqual(calls[0].args[1:], (msvcrt.LK_LOCK, 1))
        self.assertEqual(calls[-1].args[1:], (msvcrt.LK_UNLCK, 1))

    def test_e2_fcntl_flock_when_present(self):
        fake_fcntl = mock.Mock()
        fake_fcntl.LOCK_EX = 2
        fake_fcntl.LOCK_UN = 8
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(U, "fcntl", fake_fcntl), \
                 mock.patch.object(U, "msvcrt", mock.Mock(), create=True):
                with U.file_lock(self._path(td)):
                    pass
        modes = [c.args[1] for c in fake_fcntl.flock.call_args_list]
        self.assertEqual(modes, [fake_fcntl.LOCK_EX, fake_fcntl.LOCK_UN])

    def test_e3_noop_when_both_absent(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(U, "fcntl", None), \
                 mock.patch.object(U, "msvcrt", None, create=True):
                with U.file_lock(self._path(td)):
                    pass  # must not raise

    def test_docstring_does_not_exclude_windows(self):
        src = Path(U.__file__).read_text(encoding="utf-8")
        self.assertNotIn("Windows is not a target", src)


class TestWinSubprocessRunner(unittest.TestCase):
    """A list runs as argv without a shell; a string runs through the platform shell on every OS (on Windows cmd.exe,
    so `npm test`, `cd x && y` and quoted arguments behave as in a terminal)."""

    def test_run_cmd_list_never_shell(self):
        with mock.patch.object(G.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")) as run:
            G._run_cmd(["python", "-m", "unittest"], cwd=".", env={}, timeout=5)
        self.assertEqual(run.call_args.kwargs.get("shell"), False)
        self.assertEqual(run.call_args.args[0], ["python", "-m", "unittest"])

    def test_run_cmd_string_on_nt_goes_through_the_shell(self):
        # Intentional change: this used to pin shell=False + shlex.split(posix=False) on Windows, which could not run
        # npm.cmd / yarn.cmd / mvn.cmd, `&&` chains or quoted arguments (a failing quoted command even passed).
        with mock.patch.object(G, "_IS_WINDOWS", True), \
             mock.patch.object(G.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")) as run:
            G._run_cmd('cd sub && python -m unittest -k "a and b"', cwd=".", env={}, timeout=5)
        self.assertEqual(run.call_args.kwargs.get("shell"), True)
        self.assertEqual(run.call_args.args[0], 'cd sub && python -m unittest -k "a and b"')

    def test_run_cmd_string_on_posix_keeps_shell(self):
        with mock.patch.object(G, "_IS_WINDOWS", False), \
             mock.patch.object(G.subprocess, "run", return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")) as run:
            G._run_cmd("python -m unittest", cwd=".", env={}, timeout=5)
        self.assertEqual(run.call_args.kwargs.get("shell"), True)

    def test_a_list_keeps_every_argument_and_reports_the_real_exit_code(self):
        cp = G._run_cmd([sys.executable, "-c", "import sys; sys.exit(3)"], cwd=os.getcwd(), env=dict(os.environ), timeout=60)
        self.assertEqual(cp.returncode, 3)

    def test_windows_resolves_the_program_through_path_like_a_shell(self):
        """CreateProcess ignores PATHEXT: `npm` has to become `...\\npm.CMD` before it is started."""
        with mock.patch.object(G, "_IS_WINDOWS", True), \
             mock.patch.object(G.shutil, "which", return_value="C:\\node\\npm.CMD") as which, \
             mock.patch.object(G.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
            G._run_cmd(["npm", "test", "--", "-k", "a b"], cwd=".", env={"PATH": "C:\\node"}, timeout=5)
        which.assert_called_once_with("npm", path="C:\\node")
        self.assertEqual(run.call_args.args[0], ["C:\\node\\npm.CMD", "test", "--", "-k", "a b"])
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_an_unresolvable_program_stays_as_typed_and_posix_never_resolves(self):
        with mock.patch.object(G, "_IS_WINDOWS", True), mock.patch.object(G.shutil, "which", return_value=None):
            self.assertEqual(G._resolve_argv(["nope", "x"], {}), ["nope", "x"])
        with mock.patch.object(G, "_IS_WINDOWS", False), mock.patch.object(G.shutil, "which") as which:
            self.assertEqual(G._resolve_argv(["npm", 5], {}), ["npm", "5"])
        which.assert_not_called()

    def test_a_string_goes_through_the_shell_on_either_os(self):
        for windows in (True, False):
            with self.subTest(windows=windows), mock.patch.object(G, "_IS_WINDOWS", windows), \
                 mock.patch.object(G.subprocess, "run", return_value=mock.Mock(returncode=0)) as run:
                G._run_cmd("cd sub && npm test", cwd=".", env={}, timeout=5)
            self.assertIs(run.call_args.kwargs["shell"], True)
            self.assertEqual(run.call_args.args[0], "cd sub && npm test")


class TestGateThatCannotStart(unittest.TestCase):
    """A program that is missing or not executable is a failed gate (127 / 126, as a shell reports it), not a crash
    that ends `orkestra gate baseline` with 'could not read or write a file'."""

    def test_a_missing_program_is_a_failed_gate_not_a_crash(self):
        with tempfile.TemporaryDirectory() as td:
            res = G.run_gate(Path(td), {"name": "unit", "cmd": ["orkestra-no-such-program-xyz", "--flag"]},
                             Path(td) / "logs", "t")
            self.assertEqual((res["exit"], res["ok"], res["timed_out"]), (127, False, False))
            self.assertIn("could not start", res["tail"])
            self.assertIn("could not start", Path(res["log"]).read_text(encoding="utf-8"))

    def test_a_program_that_is_not_executable_is_126(self):
        # run_gate streams through Popen in its own process group now (it used to pin subprocess.run)
        with mock.patch.object(G.subprocess, "Popen", side_effect=PermissionError(13, "denied")), \
             tempfile.TemporaryDirectory() as td:
            res = G.run_gate(Path(td), {"name": "unit", "cmd": ["x"]}, Path(td) / "logs", "t")
        self.assertEqual(res["exit"], 126)

    def test_a_shell_string_for_a_missing_program_stays_127(self):
        with tempfile.TemporaryDirectory() as td:
            res = G.run_gate(Path(td), {"name": "unit", "cmd": "orkestra-no-such-program-xyz"}, Path(td) / "logs", "t")
        self.assertEqual((res["exit"], res["ok"]), (127, False))

    def test_runs_helper_mirrors_gates(self):
        from orkestra_core import runs as R
        self.assertTrue(hasattr(R, "_run_shell_or_list"))
        # The helper now runs through G._stream_cmd (own process group, kill-tree on timeout); the list-versus-shell
        # decision is still visible in the first argument that runner receives.
        ok = (0, b"", "")
        with mock.patch.object(G, "_IS_WINDOWS", True), mock.patch.object(G, "_stream_cmd", return_value=ok) as run:
            R._run_shell_or_list("python -c pass", cwd=".", timeout=5, env={})
        self.assertEqual(run.call_args.args[0], "python -c pass")                  # a lone string: through the shell
        # Several arguments run as a list on Windows. `shlex.join` quotes with ' which cmd.exe does not understand: the
        # old path split that record back with posix=False and ran `python -c "'import sys; sys.exit(3)'"`, a string
        # statement that exits 0, so a failing command was recorded as a pass.
        argv = [sys.executable, "-c", "import sys; sys.exit(3)"]
        with mock.patch.object(G, "_IS_WINDOWS", True), mock.patch.object(G.shutil, "which", return_value=None), \
             mock.patch.object(G.subprocess, "Popen", wraps=G.subprocess.Popen) as popen:
            cp = R._run_shell_or_list(shlex.join(argv), cwd=os.getcwd(), timeout=60, env=dict(os.environ), argv=argv)
        self.assertEqual(cp.returncode, 3)
        self.assertEqual(popen.call_args.args[0], argv)
        self.assertIs(popen.call_args.kwargs["shell"], False)
        # a lone command string (`npm test && echo ok`) and every POSIX run keep going through the shell
        with mock.patch.object(G, "_IS_WINDOWS", True), mock.patch.object(G, "_stream_cmd", return_value=ok) as run:
            R._run_shell_or_list("npm test && echo ok", cwd=".", timeout=5, env={}, argv=["npm test && echo ok"])
        self.assertEqual(run.call_args.args[0], "npm test && echo ok")
        with mock.patch.object(G, "_IS_WINDOWS", False), mock.patch.object(G, "_stream_cmd", return_value=ok) as run:
            R._run_shell_or_list(shlex.join(argv), cwd=".", timeout=5, env={}, argv=argv)
        self.assertEqual(run.call_args.args[0], shlex.join(argv))


class TestConfigMergeIsTypeSafe(unittest.TestCase):
    def test_wrong_typed_settings_fall_back_to_the_defaults(self):
        from orkestra_core import paths
        merged = paths._deep_merge(paths.DEFAULT_GLOBAL_CONFIG, {
            "engine": "cursor", "guards": {"git": "yes", "secrets": False}, "workspace_root": 5, "lang": ["tr"],
            "watchdog": {"dead_wait_seconds": "soon", "loop_repeats": 4.5}, "extra": {"anything": 1}})
        self.assertEqual(merged["engine"], paths.DEFAULT_GLOBAL_CONFIG["engine"])           # object expected
        self.assertIs(merged["guards"]["git"], True)                                         # flag expected
        self.assertIs(merged["guards"]["secrets"], False)                                    # a valid override still applies
        self.assertEqual(merged["workspace_root"], "~/Projects")
        self.assertEqual(merged["lang"], "auto")
        self.assertEqual(merged["watchdog"]["dead_wait_seconds"], 90)
        self.assertEqual(merged["watchdog"]["loop_repeats"], 4.5)                            # numbers interchange
        self.assertEqual(merged["extra"], {"anything": 1})                                   # unknown keys pass through


class TestWorkspaceDisplayIsPortable(unittest.TestCase):
    def test_home_paths_become_tilde_and_tilde_config_stays(self):
        from orkestra_core import paths
        home = Path.home().resolve(strict=False)
        self.assertEqual(paths.display_path(home / "Projects"), "~/Projects")
        self.assertEqual(paths.display_path(str(home / "Projects" / "demo")), "~/Projects/demo")
        self.assertEqual(paths.display_path("~/Projects"), "~/Projects")
        self.assertEqual(paths.display_path("~"), "~")
        outside = Path("/tmp/orkestra-display-outside")
        self.assertEqual(paths.display_path(outside), str(outside).replace("\\", "/"))
        with mock.patch.dict(os.environ, {"ORKESTRA_WORKSPACE": str(home / "Projects")}, clear=False):
            self.assertEqual(paths.workspace_display(), "~/Projects")
            self.assertNotRegex(paths.workspace_display(), r"^/(?:Users|home)/")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ORKESTRA_WORKSPACE", None)
            with mock.patch.object(paths, "global_config", return_value={"workspace_root": "~/Projects"}):
                self.assertEqual(paths.workspace_display(), "~/Projects")


if __name__ == "__main__":
    unittest.main()
