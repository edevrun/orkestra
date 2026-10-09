import json
import unittest
from pathlib import Path

import helpers
from orkestra_core import gates as G
from orkestra_core import planparse as PP
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


if __name__ == "__main__":
    unittest.main()
