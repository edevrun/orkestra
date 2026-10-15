"""FR-001, FR-003, FR-004, FR-009, FR-010: `orkestra mcp` CLI. No network."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import unittest
from unittest import mock

import helpers
from orkestra_core import cli as CLI
from orkestra_core import cliparse as CP
from orkestra_core import i18n
from orkestra_core.messages import cli as MSG

EM_DASH = "\u2014"


class TestMcpCli(helpers.Sandbox):
    def setUp(self) -> None:
        super().setUp()
        self._saved_env = {
            k: os.environ.get(k) for k in ("PATH", "ORKESTRA_CURSOR_HOME", "COPILOT_HOME")
        }
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.cursor = self.tmp / "cursor-home"
        self.copilot = self.tmp / "copilot-home"
        # Temp bin only: never prepend, never keep the machine PATH.
        os.environ["PATH"] = str(self.bin)
        os.environ["ORKESTRA_CURSOR_HOME"] = str(self.cursor)
        os.environ["COPILOT_HOME"] = str(self.copilot)
        self.assertEqual(os.environ["PATH"].split(os.pathsep), [str(self.bin)])
        for tool in ("npx", "claude", "copilot", "cursor", "serena"):
            self.assertIsNone(shutil.which(tool), tool)

    def tearDown(self) -> None:
        for key, value in getattr(self, "_saved_env", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        super().tearDown()

    def invoke(self, argv, *, asker, lang):
        saved = os.environ.get("ORKESTRA_LANG")
        os.environ["ORKESTRA_LANG"] = lang
        out, err = io.StringIO(), io.StringIO()
        try:
            with mock.patch.object(CLI, "_ASKER", asker), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = CLI.main(list(argv))
        finally:
            if saved is None:
                os.environ.pop("ORKESTRA_LANG", None)
            else:
                os.environ["ORKESTRA_LANG"] = saved
        return rc, out.getvalue(), err.getvalue()

    def test_fr001_parse_install_defaults_context7_and_playwright(self) -> None:
        """FR-001: no flags parse as engine all, and omitted --only installs context7 and playwright."""
        parser = CP.build_parser(CP.QuietParser, localize=False)
        ns = CP.parse(parser, ["mcp", "install"])
        self.assertEqual((ns.cmd, ns.action, ns.engine), ("mcp", "install", "all"))
        self.assertIsNone(ns.only)
        with mock.patch("orkestra_core.mcpinstall.install", return_value=[]) as install, \
                mock.patch("orkestra_core.mcpinstall.status", return_value=[]) as status:
            rc, out, err = self.invoke(["mcp", "install"], asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        self.assertEqual(install.call_args, mock.call("all", None))
        self.assertFalse(status.called)
        self.assertNotIn(EM_DASH, out)
        self.cursor.mkdir()
        self.copilot.mkdir()
        rc, out, err = self.invoke(["mcp", "install"], asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        self.assertNotIn(EM_DASH, out)
        cursor = json.loads((self.cursor / "mcp.json").read_text(encoding="utf-8"))
        copilot = json.loads((self.copilot / "mcp-config.json").read_text(encoding="utf-8"))
        self.assertEqual(list(cursor["mcpServers"]), ["context7", "playwright"])
        self.assertEqual(list(copilot["mcpServers"]), ["context7", "playwright"])
        self.assertNotIn("serena", cursor["mcpServers"])
        self.assertNotIn("serena", copilot["mcpServers"])

    def test_fr001_only_serena_on_copilot_in_en_and_tr(self) -> None:
        """FR-001: --only serena --engine copilot writes serena alone. en and tr stdout have no em dash."""
        parser = CP.build_parser(CP.QuietParser, localize=False)
        ns = CP.parse(parser, ["mcp", "install", "--only", "serena", "--engine", "copilot"])
        self.assertEqual(ns.engine, "copilot")
        self.assertEqual(ns.only, ["serena"])
        self.copilot.mkdir()
        argv = ["mcp", "install", "--only", "serena", "--engine", "copilot"]
        rc, out, err = self.invoke(argv, asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        path = self.copilot / "mcp-config.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(doc["mcpServers"]), {"serena"})
        self.assertNotIn("context7", path.read_text(encoding="utf-8"))
        self.assertNotIn("playwright", path.read_text(encoding="utf-8"))
        self.assertNotIn("--context", path.read_text(encoding="utf-8"))
        self.assertEqual(out.splitlines(), [
            MSG.EN["cli.mcp_row"].format(host="copilot", name="serena", state="added"),
        ])
        self.assertNotIn(EM_DASH, out)
        path.unlink()
        rc, out, err = self.invoke(argv, asker=None, lang="tr")
        self.assertEqual(rc, 0, err)
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(doc["mcpServers"]), {"serena"})
        self.assertEqual(out.splitlines(), [
            MSG.TR["cli.mcp_row"].format(host="copilot", name="serena", state="added"),
        ])
        self.assertNotIn(EM_DASH, out)

    def test_asker_refuses_before_install_and_writes_nothing(self) -> None:
        """An agent shell exits 2 and creates no MCP file."""
        self.copilot.mkdir()
        self.cursor.mkdir()
        with mock.patch("orkestra_core.mcpinstall.install", return_value=[]) as install, \
                mock.patch("orkestra_core.mcpinstall.status", return_value=[]) as status:
            rc, out, err = self.invoke(
                ["mcp", "install", "--engine", "copilot"], asker="cursor", lang="en")
        self.assertEqual(rc, 2)
        self.assertFalse(install.called)
        self.assertFalse(status.called)
        self.assertFalse((self.copilot / "mcp-config.json").exists())
        self.assertFalse((self.cursor / "mcp.json").exists())
        self.assertIn(MSG.EN["cli.mcp_refused"], err)
        self.assertNotIn(EM_DASH, err)
        self.assertEqual(out.strip(), "")

    def test_fr004_second_install_keeps_file_bytes(self) -> None:
        """FR-004: a second install does not change the file bytes."""
        self.cursor.mkdir()
        rc, _out, err = self.invoke(["mcp", "install", "--engine", "cursor"], asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        path = self.cursor / "mcp.json"
        raw = path.read_bytes()
        self.assertIn(b"context7", raw)
        self.assertIn(b"playwright", raw)
        rc, _out, err = self.invoke(["mcp", "install", "--engine", "cursor"], asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        self.assertEqual(path.read_bytes(), raw)

    def test_fr003_cursor_array_exits_1_and_keeps_bytes(self) -> None:
        """FR-003: mcp.json [] exits 1 and the bytes stay []."""
        self.cursor.mkdir()
        path = self.cursor / "mcp.json"
        raw = b"[]"
        path.write_bytes(raw)
        rc, _out, err = self.invoke(["mcp", "install", "--engine", "cursor"], asker=None, lang="en")
        self.assertEqual(rc, 1, err)
        self.assertEqual(path.read_bytes(), raw)
        self.assertIn(str(path), err)
        self.assertNotIn("unexpected error", err)

    def test_help_cmd_mcp_in_en_and_tr(self) -> None:
        """help.cmd.mcp exists in both catalogs, stripped, with no em dash and no %."""
        en, tr = i18n.catalog("en"), i18n.catalog("tr")
        for key in ("help.cmd.mcp", "help.opt.mcp_engine", "help.opt.mcp_only", "cli.mcp_row", "cli.mcp_refused"):
            self.assertIn(key, en, key)
            self.assertIn(key, tr, key)
            for text in (en[key], tr[key]):
                self.assertEqual(text, text.strip(), key)
                self.assertNotIn(EM_DASH, text, key)
                self.assertNotIn("%", text, key)
        self.assertNotEqual(en["help.cmd.mcp"], tr["help.cmd.mcp"])

    def test_fr009_explicit_missing_cursor_creates_no_directory(self) -> None:
        """FR-009: --engine cursor with a non-directory home and no cursor binary creates nothing."""
        missing = self.tmp / "absent-cursor"
        os.environ["ORKESTRA_CURSOR_HOME"] = str(missing)
        self.assertFalse(missing.exists())
        self.assertIsNone(shutil.which("cursor"))
        rc, _out, err = self.invoke(["mcp", "install", "--engine", "cursor"], asker=None, lang="en")
        self.assertEqual(rc, 1, err)
        self.assertFalse(missing.exists())
        self.assertNotIn("unexpected error", err)

    def test_fr010_status_prints_present_and_absent_without_writing(self) -> None:
        """FR-010: status prints present and absent and does not change bytes."""
        self.cursor.mkdir()
        path = self.cursor / "mcp.json"
        path.write_text(
            json.dumps({"mcpServers": {"context7": {"url": "https://mcp.context7.com/mcp"}}}) + "\n",
            encoding="utf-8")
        raw = path.read_bytes()
        rc, out, err = self.invoke(["mcp", "status", "--engine", "cursor"], asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(out.splitlines(), [
            MSG.EN["cli.mcp_row"].format(host="cursor", name="context7", state="present"),
            MSG.EN["cli.mcp_row"].format(host="cursor", name="playwright", state="absent"),
        ])
        self.assertNotIn(EM_DASH, out)

    def test_install_all_with_no_host_exits_zero(self) -> None:
        """A skipped host (empty list) exits 0 and creates no directory."""
        rc, out, err = self.invoke(["mcp", "install"], asker=None, lang="en")
        self.assertEqual(rc, 0, err)
        self.assertEqual([ln for ln in out.splitlines() if ln.strip()], [])
        self.assertNotIn(EM_DASH, out)
        self.assertFalse(self.cursor.exists())
        self.assertFalse(self.copilot.exists())


if __name__ == "__main__":
    unittest.main()
