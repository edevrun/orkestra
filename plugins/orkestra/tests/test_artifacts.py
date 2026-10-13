"""FR: regenerable Playwright/Android dumps are removed after a run completes (and via `orkestra clean`)."""
from __future__ import annotations

import json
import unittest

from helpers import Sandbox

import orkestra_core.artifacts as ART
import orkestra_core.paths as P
import orkestra_core.runs as R
class TestArtifactCleanup(Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self.proj = self.make_project("demo")

    def _plant(self) -> dict:
        """Create regenerable trees + protected paths that must survive."""
        keep = self.proj / "calc.py"
        tr = self.proj / "test-results" / "run-a"
        tr.mkdir(parents=True)
        big = tr / "report.json"
        big.write_bytes(b"x" * 4096)
        (self.proj / "playwright-report").mkdir()
        (self.proj / "playwright-report" / "index.html").write_text("<html/>", encoding="utf-8")
        android = self.proj / "android" / "app" / "build" / "intermediates" / "merged_native_libs"
        android.mkdir(parents=True)
        so = android / "libreactnative.so"
        so.write_bytes(b"\0" * 2048)
        secret = self.proj / ".orkestra" / "keep.txt"
        secret.parent.mkdir(parents=True, exist_ok=True)
        secret.write_text("stay\n", encoding="utf-8")
        node = self.proj / "node_modules" / "pkg"
        node.mkdir(parents=True)
        (node / "index.js").write_text("1\n", encoding="utf-8")
        return {"keep": keep, "big": big, "so": so, "secret": secret, "node": node / "index.js"}

    def test_discover_finds_playwright_and_android_build_not_protected(self):
        self._plant()
        found = {p.resolve() for p in ART.discover_artifacts(self.proj)}
        self.assertTrue(any(p.name == "test-results" for p in found))
        self.assertTrue(any(p.name == "playwright-report" for p in found))
        self.assertTrue(any(p.as_posix().endswith("android/app/build") for p in found))
        self.assertFalse(any(".orkestra" in p.parts for p in found))
        self.assertFalse(any("node_modules" in p.parts for p in found))

    def test_clean_project_removes_dumps_keeps_source(self):
        planted = self._plant()
        report = ART.clean_project(self.proj, dry_run=False)
        self.assertGreaterEqual(len(report["removed"]), 2)
        self.assertFalse((self.proj / "test-results").exists())
        self.assertFalse((self.proj / "playwright-report").exists())
        self.assertFalse((self.proj / "android" / "app" / "build").exists())
        self.assertTrue(planted["keep"].is_file())
        self.assertTrue(planted["secret"].is_file())
        self.assertTrue(planted["node"].is_file())
        self.assertGreater(report["freed_bytes"], 0)

    def test_dry_run_does_not_delete(self):
        self._plant()
        report = ART.clean_project(self.proj, dry_run=True)
        self.assertGreaterEqual(len(report["removed"]), 1)
        self.assertTrue((self.proj / "test-results").is_dir())

    def test_cleanup_disabled_by_project_config(self):
        self._plant()
        P.okdir(self.proj).mkdir(parents=True, exist_ok=True)
        (P.okdir(self.proj) / "config.json").write_text(
            json.dumps({"cleanup_artifacts": False}), encoding="utf-8")
        self.assertFalse(ART.cleanup_enabled(self.proj))
        report = ART.clean_after_run(self.proj)
        self.assertTrue(report.get("skipped"))
        self.assertTrue((self.proj / "test-results").is_dir())

    def test_clean_after_run_removes_dumps(self):
        planted = self._plant()
        report = ART.clean_after_run(self.proj)
        self.assertFalse(report.get("skipped"))
        self.assertFalse((self.proj / "test-results").exists())
        self.assertTrue(planted["keep"].is_file())

    def test_phase_next_to_done_invokes_cleanup(self):
        planted = self._plant()
        R.init_project(self.proj)
        opened = R.start_run(self.proj, title="cleanup-test", request="clean dumps", size="XS",
                             mode="advisory", engine="claude")
        rid = opened["run_id"]
        rdir = P.run_dir(self.proj, rid)
        fdir = P.feature_dir(self.proj, rid)
        st = json.loads((rdir / "state.json").read_text(encoding="utf-8"))
        st["phase"] = "deliver"
        st["status"] = "active"
        st["approval_mode"] = "autopilot"
        (rdir / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (fdir / "verification.md").write_text("# Verification\n", encoding="utf-8")
        (fdir / "reviews").mkdir(parents=True, exist_ok=True)
        (fdir / "reviews" / "triage.md").write_text("none\n", encoding="utf-8")
        (fdir / "report.md").write_text("# Teslim raporu\n" + ("detay " * 80), encoding="utf-8")
        self.cli("memory", "feature-add", "-p", "demo", "--title", "Cleanup",
                 "--summary", "artifact cleanup on done", "--capabilities", "cleanup")
        self.cli("report", "evidence", "--write", "-p", "demo")
        # Must prove runs.phase_next(deliver→done) invokes cleanup — no OrkestraError fallback.
        cur, target = R.phase_next(self.proj, rid)
        self.assertEqual((cur, target), ("deliver", "done"))
        self.assertFalse((self.proj / "test-results").exists())
        self.assertTrue(planted["keep"].is_file())
        ledger = (fdir / "ledger.md").read_text(encoding="utf-8")
        self.assertIn("artifact cleanup", ledger)

    def test_cli_clean_dry_run(self):
        self._plant()
        cp = self.cli("clean", "--dry-run", "-p", str(self.proj), check=True)
        self.assertTrue((self.proj / "test-results").is_dir())
        self.assertRegex(cp.stdout, r"DRY RUN|KURU")
        # FR-003 / SC-003: cli.clean_bytes must fill {size}, never leave the placeholder
        self.assertNotIn("{size}", cp.stdout)

    def test_cmake_embedded_absolute_path_is_not_android_build(self):
        """CMakeFiles may contain a directory literally named …/Users/…/android/app/build — must not match."""
        fake = (self.proj / "android" / "app" / "obj" / "CMakeFiles" /
                "appmodules.dir" / "Users" / "someone" / "Projects" / "app" / "android" / "app" / "build")
        fake.mkdir(parents=True)
        (fake / "junk.o").write_bytes(b"o")
        real = self.proj / "android" / "app" / "build" / "intermediates"
        real.mkdir(parents=True)
        (real / "a.so").write_bytes(b"s")
        found = ART.discover_artifacts(self.proj)
        self.assertTrue(any(p.resolve() == (self.proj / "android" / "app" / "build").resolve() for p in found))
        self.assertFalse(any(p.resolve() == fake.resolve() for p in found))
        ART.clean_project(self.proj, dry_run=False)
        self.assertFalse((self.proj / "android" / "app" / "build").exists())
        self.assertTrue(fake.is_dir(), "CMake embed path must survive")

    def test_node_modules_package_android_build_is_cleaned(self):
        pkg_build = self.proj / "node_modules" / "react-native-screens" / "android" / "build" / "intermediates"
        pkg_build.mkdir(parents=True)
        (pkg_build / "libreactnative.so").write_bytes(b"\0" * 100)
        keep_js = self.proj / "node_modules" / "react-native-screens" / "src" / "index.js"
        keep_js.parent.mkdir(parents=True, exist_ok=True)
        keep_js.write_text("export {}\n", encoding="utf-8")
        ART.clean_project(self.proj, dry_run=False)
        self.assertFalse((self.proj / "node_modules" / "react-native-screens" / "android" / "build").exists())
        self.assertTrue(keep_js.is_file())


if __name__ == "__main__":
    unittest.main()
