"""Localization foundation: language resolution, catalog integrity, and the "no stray Turkish in code" lock."""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import helpers
from orkestra_core import i18n
from orkestra_core.messages import protocol

TR_LETTERS = re.compile("[çğıöşüÇĞİÖŞÜ]")
LIB = helpers.PLUGIN / "lib" / "orkestra_core"


class TestNormalize(unittest.TestCase):
    def test_codes_and_names(self):
        table = {"tr": "tr", "TR": "tr", "tr_TR.UTF-8": "tr", "tr-TR": "tr", "tr_TR@euro": "tr", "Turkish": "tr",
                 "türkçe": "tr", "en": "en", "EN": "en", "en_US.UTF-8": "en", "en-GB": "en", "English": "en",
                 "auto": None, "": None, "C": None, "POSIX": None, "de": None, "de_DE.UTF-8": None, "trx": None,
                 "  tr  ": "tr", None: None, 7: None}
        for raw, want in table.items():
            self.assertEqual(i18n.normalize(raw), want, repr(raw))


class TestUseLang(unittest.TestCase):
    """`use_lang`: one thread answers in another language for a while (the bridge, one request at a time)."""

    def test_the_block_changes_this_thread_only_and_restores_afterwards(self):
        import threading
        seen = {}
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"}):
            self.assertEqual(i18n.lang(), "en")
            with i18n.use_lang("tr"):
                self.assertEqual(i18n.lang(), "tr")
                self.assertEqual(i18n.t("common.ago", dur="5m"), i18n.catalog("tr")["common.ago"].format(dur="5m"))
                self.assertEqual(i18n.lang_source(), ("tr", "request"))
                self.assertEqual(i18n.lang({"ORKESTRA_LANG": "en"}), "en", "an explicit env mapping is never overridden")
                worker = threading.Thread(target=lambda: seen.update(other=i18n.lang()))
                worker.start()
                worker.join()
                with i18n.use_lang("en"):
                    self.assertEqual(i18n.lang(), "en", "blocks nest")
                self.assertEqual(i18n.lang(), "tr")
            self.assertEqual(i18n.lang(), "en")
            self.assertEqual(i18n.lang_source()[1], "env")
        self.assertEqual(seen["other"], "en", "another thread keeps the process language")

    def test_nothing_or_an_unsupported_code_changes_nothing(self):
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            for code in (None, "", "auto", "de", "klingon", 7):
                with i18n.use_lang(code):
                    self.assertEqual(i18n.lang(), "tr", repr(code))
            with i18n.use_lang("en"):
                with i18n.use_lang("de"):
                    self.assertEqual(i18n.lang(), "en", "an unsupported inner code keeps the outer one")

    def test_spellings_and_exceptions(self):
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"}):
            with i18n.use_lang("TR_tr.UTF-8"):
                self.assertEqual(i18n.lang(), "tr")
            with self.assertRaises(RuntimeError):
                with i18n.use_lang("tr"):
                    raise RuntimeError("boom")
            self.assertEqual(i18n.lang(), "en", "restored even when the block raises")


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="orkestra-i18n-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {"ORKESTRA_HOME": str(self.tmp)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, value):
        (self.tmp / "config.json").write_text(value if isinstance(value, str) else json.dumps(value), encoding="utf-8")

    def test_default_is_english(self):
        self.assertEqual(i18n.lang({}), "en")

    def test_env_beats_everything(self):
        self.config({"lang": "tr"})
        self.assertEqual(i18n.lang({"ORKESTRA_LANG": "en", "LANG": "tr_TR.UTF-8"}), "en")
        self.assertEqual(i18n.lang({"ORKESTRA_LANG": "tr", "LANG": "en_US.UTF-8"}), "tr")

    def test_config_beats_locale(self):
        self.config({"lang": "tr"})
        self.assertEqual(i18n.lang({"LANG": "en_US.UTF-8"}), "tr")
        self.config({"lang": "en"})
        self.assertEqual(i18n.lang({"LANG": "tr_TR.UTF-8"}), "en")

    def test_auto_falls_through_to_locale(self):
        self.config({"lang": "auto"})
        self.assertEqual(i18n.lang({"LANG": "tr_TR.UTF-8"}), "tr")
        self.assertEqual(i18n.lang({"ORKESTRA_LANG": "auto", "LC_ALL": "tr_TR.UTF-8"}), "tr")
        self.assertEqual(i18n.lang({"LANG": "de_DE.UTF-8"}), "en")

    def test_locale_variable_order_and_language_list(self):
        self.assertEqual(i18n.lang({"LC_ALL": "tr_TR.UTF-8", "LANG": "en_US.UTF-8"}), "tr")
        self.assertEqual(i18n.lang({"LC_ALL": "C", "LANG": "tr_TR.UTF-8"}), "tr")
        self.assertEqual(i18n.lang({"LANGUAGE": "de:tr:en"}), "tr")

    def test_unsupported_explicit_value_means_english_not_the_locale(self):
        self.assertEqual(i18n.lang({"ORKESTRA_LANG": "de", "LANG": "tr_TR.UTF-8"}), "en")

    def test_broken_or_odd_config_is_ignored(self):
        for bad in ("{not json", "[]", '"tr"', json.dumps({"lang": 5}), json.dumps({"lang": "klingon"}), ""):
            self.config(bad)
            self.assertEqual(i18n.lang({"LANG": "tr_TR.UTF-8"}), "tr", bad)
        (self.tmp / "config.json").unlink()
        self.assertEqual(i18n.lang({}), "en")

    def test_config_with_a_byte_order_mark_is_read(self):
        """Windows editors (Notepad, PowerShell Set-Content -Encoding utf8) prepend a BOM: the choice must not vanish."""
        (self.tmp / "config.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"lang": "tr"}).encode("utf-8"))
        self.assertEqual(i18n.lang({"LANG": "en_US.UTF-8"}), "tr")
        from orkestra_core import paths
        (self.tmp / "config.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"workspace_root": "/x/y"}).encode("utf-8"))
        self.assertEqual(paths.global_config().get("workspace_root"), "/x/y")

    def test_config_change_is_picked_up(self):
        self.config({"lang": "tr"})
        self.assertEqual(i18n.lang({}), "tr")
        self.config({"lang": "en", "padding": "x" * 5})              # new size/mtime → cache miss
        self.assertEqual(i18n.lang({}), "en")


class TestWindowsLocale(unittest.TestCase):
    """Windows sets none of LC_ALL / LANG: its user locale name is the fallback, and only for the real environment."""

    def setUp(self):
        self.tmp = Path(__import__("tempfile").mkdtemp(prefix="orkestra-i18n-win-"))
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)
        self.env = mock.patch.dict(os.environ, {"ORKESTRA_HOME": str(self.tmp)}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        i18n._windows_locale_name.cache_clear()
        self.addCleanup(i18n._windows_locale_name.cache_clear)

    def test_turkish_windows_gets_turkish_when_no_posix_variable_exists(self):
        with mock.patch.object(i18n, "_windows_locale_name", return_value="tr-TR"):
            self.assertEqual(i18n.lang(), "tr")
            self.assertEqual(i18n.lang_source(), ("tr", "locale"))
        with mock.patch.object(i18n, "_windows_locale_name", return_value="en-US"):
            self.assertEqual(i18n.lang(), "en")
        with mock.patch.object(i18n, "_windows_locale_name", return_value="de-DE"):
            self.assertEqual(i18n.lang_source(), ("en", "default"))

    def test_precedence_is_unchanged(self):
        with mock.patch.object(i18n, "_windows_locale_name", return_value="tr-TR"):
            with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"}):
                self.assertEqual(i18n.lang(), "en")
            with mock.patch.dict(os.environ, {"LANG": "en_US.UTF-8"}):
                self.assertEqual(i18n.lang(), "en")                       # a POSIX variable beats the Windows locale
            (self.tmp / "config.json").write_text(json.dumps({"lang": "en"}), encoding="utf-8")
            self.assertEqual(i18n.lang(), "en")                           # and so does config.json

    def test_any_defined_posix_locale_variable_means_a_posix_world(self):
        """Even an empty `LANG=` (the test-suite's neutral environment, `LC_ALL=C` shells) skips the Windows lookup."""
        for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
            with mock.patch.dict(os.environ, {var: ""}), \
                    mock.patch.object(i18n, "_windows_locale_name", return_value="tr-TR") as probe:
                self.assertEqual(i18n.lang_source(), ("en", "default"), var)
            probe.assert_not_called()

    def test_an_explicit_env_argument_never_asks_the_operating_system(self):
        with mock.patch.object(i18n, "_windows_locale_name", return_value="tr-TR") as probe:
            self.assertEqual(i18n.lang({}), "en")
            self.assertEqual(i18n.lang_source({}), ("en", "default"))
        probe.assert_not_called()

    def test_the_lookup_is_windows_only_and_never_raises(self):
        with mock.patch.object(i18n.sys, "platform", "linux"):
            self.assertEqual(i18n._windows_locale_name(), "")
        i18n._windows_locale_name.cache_clear()

        class Kernel:
            @staticmethod
            def GetUserDefaultLocaleName(buf, size):
                buf.value = "tr-TR"
                return 7

        class Boom:
            @property
            def windll(self):
                raise OSError("no kernel32")

        fake = mock.MagicMock()
        fake.create_unicode_buffer = lambda n: type("Buf", (), {"value": "", "__len__": lambda self: 85})()
        fake.windll.kernel32 = Kernel
        with mock.patch.object(i18n.sys, "platform", "win32"), mock.patch.dict(sys.modules, {"ctypes": fake}):
            self.assertEqual(i18n._windows_locale_name(), "tr-TR")
        i18n._windows_locale_name.cache_clear()
        with mock.patch.object(i18n.sys, "platform", "win32"), mock.patch.dict(sys.modules, {"ctypes": Boom()}):
            self.assertEqual(i18n._windows_locale_name(), "")
        i18n._windows_locale_name.cache_clear()


class TestTranslate(unittest.TestCase):
    def test_lookup_fallbacks_and_format_safety(self):
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            self.assertEqual(i18n.t("common.ago", dur="3m"), "3m önce")
            self.assertEqual(i18n.t("no.such.key"), "no.such.key")
            self.assertEqual(i18n.t("common.ago"), "{dur} önce")               # missing field: returned unformatted
            self.assertEqual(i18n.t("common.ago", other=1), "{dur} önce")      # wrong field: never raises
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"}):
            self.assertEqual(i18n.t("common.ago", dur="3m"), "3m ago")

    def test_missing_translation_falls_back_to_english(self):
        cats = {"en": {"x.k": "english {a}"}, "tr": {}}
        with mock.patch.object(i18n, "_catalogs", cats), mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            self.assertEqual(i18n.t("x.k", a=1), "english 1")

    def test_pick_plural_duration_ago(self):
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            self.assertEqual(i18n.pick("a", "b"), "b")
            self.assertEqual(i18n.ago(125), "2 dk önce")
            # Turkish keeps the format `orkestra watch` always printed (the old tempo.dur_tr), seconds below 90 s
            self.assertEqual([i18n.duration(x) for x in (None, -5, 0, 59, 60, 89, 90, 3599, 3600, 7500)],
                             ["?", "0 sn", "0 sn", "59 sn", "60 sn", "89 sn", "1 dk", "59 dk", "1 sa 0 dk", "2 sa 5 dk"])
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "en"}):
            self.assertEqual(i18n.pick("a", "b"), "a")
            self.assertEqual(i18n.ago(125), "2m ago")
            self.assertEqual([i18n.duration(x) for x in (None, -5, 0, 59, 60, 89, 90, 3599, 3600, 7500)],
                             ["?", "0s", "0s", "59s", "60s", "89s", "1m", "59m", "1h 0m", "2h 5m"])
        self.assertEqual((i18n.plural(1, "task", "tasks"), i18n.plural(0, "task", "tasks")), ("task", "tasks"))
        self.assertEqual(i18n.ago(None), "?")

    def test_headings(self):
        self.assertEqual(i18n.heading("classification", "en"), "Classification")
        self.assertEqual(i18n.heading("classification", "tr"), "Sınıflandırma")
        with mock.patch.dict(os.environ, {"ORKESTRA_LANG": "tr"}):
            self.assertEqual(i18n.heading("clarifications"), "Netleştirmeler")
        self.assertEqual(i18n.heading_aliases("exceptions"), ("Exceptions", "İstisnalar"))

    def test_template_dir_prefers_language_then_english_then_root(self):
        root = Path(__import__("tempfile").mkdtemp(prefix="orkestra-tpl-"))
        self.addCleanup(__import__("shutil").rmtree, root, True)
        self.assertEqual(i18n.template_dir(root, "tr"), root)
        (root / "en").mkdir()
        self.assertEqual(i18n.template_dir(root, "tr"), root / "en")
        (root / "tr").mkdir()
        self.assertEqual(i18n.template_dir(root, "tr"), root / "tr")
        self.assertEqual(i18n.template_dir(root, "en"), root / "en")


class TestCatalogIntegrity(unittest.TestCase):
    def test_every_area_loads_and_keys_are_namespaced(self):
        for area in i18n.AREAS:
            mod = __import__(f"orkestra_core.messages.{area}", fromlist=["EN", "TR"])
            if area == "protocol":
                continue
            for code in ("EN", "TR"):
                for key in getattr(mod, code):
                    self.assertTrue(key.startswith(area + "."), f"{area}.py {code}: {key} is not '{area}.*'")

    def test_both_languages_have_the_same_keys_and_fields(self):
        en, tr = i18n.catalog("en"), i18n.catalog("tr")
        self.assertEqual(sorted(set(en) ^ set(tr)), [], "keys present in one language only")
        for key in en:
            self.assertEqual(i18n.fields(en[key]), i18n.fields(tr[key]), f"{key}: {{fields}} differ between en and tr")
            for code, text in (("en", en[key]), ("tr", tr[key])):
                self.assertIsInstance(text, str)
                self.assertTrue(text.strip(), f"{key} ({code}) is empty")
                try:
                    text.format(**{f: "x" for f in i18n.fields(text)})
                except Exception as exc:                                           # a stray brace
                    self.fail(f"{key} ({code}) is not a valid format string: {exc}")

    def test_english_catalog_has_no_turkish_letters(self):
        bad = [k for k, v in i18n.catalog("en").items() if TR_LETTERS.search(v)]
        self.assertEqual(bad, [])

    def test_turkish_catalog_is_actually_translated(self):
        """A copy-pasted English value in `TR` is a missing translation (short or symbol-only texts are exempt)."""
        en, tr = i18n.catalog("en"), i18n.catalog("tr")
        same = [k for k in en if en[k] == tr[k] and len(re.sub(r"\{\w+\}|[^A-Za-z]", "", en[k])) > 12]
        self.assertEqual(same, [], "identical en/tr text (untranslated?)")

    def test_protocol_sections(self):
        seen = set()
        for sid, pair in protocol.SECTIONS.items():
            self.assertEqual(len(pair), 2, sid)
            en, tr = pair
            self.assertFalse(TR_LETTERS.search(en), f"{sid}: English heading has Turkish letters")
            for h in pair:
                self.assertEqual(h, h.strip(), sid)
                self.assertNotIn(h.lower(), seen, f"heading {h!r} is ambiguous between sections")
                seen.add(h.lower())


# Files that may hold Turkish letters for a reason other than display text:
#   intent.py   user-intent vocabulary ("commitle", "onaylıyorum"): bilingual on purpose
#   util.py     Turkish-aware case folding
#   i18n.py     language names in the code table ("türkçe")
#   guards.py / engines.py   example phrases a hint tells the model it may expect from a Turkish-speaking user
VOCABULARY_FILES = {"i18n.py", "intent.py", "util.py", "guards.py", "engines.py"}
# Files still to be migrated to catalogs. Empty on purpose: every display string lives in messages/*. A new entry
# here needs a reason; the lock below fails when a listed file is already clean.
PENDING_MIGRATION: set = set()


class TestNoStrayTurkishInCode(unittest.TestCase):
    def python_sources(self):
        roots = [LIB, helpers.PLUGIN / "hooks", helpers.PLUGIN / "bin"]
        for root in roots:
            for p in sorted(root.rglob("*")):
                if p.is_file() and (p.suffix == ".py" or p.name == "orkestra") and "__pycache__" not in p.parts \
                        and "messages" not in p.relative_to(root).parts[:1] and "vendor" not in p.parts:
                    yield p

    def test_only_catalogs_and_vocabulary_files_contain_turkish_letters(self):
        dirty = {p.name for p in self.python_sources() if TR_LETTERS.search(p.read_text(encoding="utf-8"))}
        stray = sorted(dirty - VOCABULARY_FILES - PENDING_MIGRATION)
        self.assertEqual(stray, [], "Turkish text outside messages/*: move it to a catalog (i18n.t)")
        done = sorted((PENDING_MIGRATION - dirty))
        self.assertEqual(done, [], "already clean: remove from PENDING_MIGRATION")

    def test_vocabulary_files_really_hold_vocabulary(self):
        """A file named in VOCABULARY_FILES with no Turkish letters is a stale exemption: drop it from the set."""
        dirty = {p.name for p in self.python_sources() if TR_LETTERS.search(p.read_text(encoding="utf-8"))}
        self.assertEqual(sorted(VOCABULARY_FILES - dirty), [], "no Turkish letters left: remove from VOCABULARY_FILES")


if __name__ == "__main__":
    unittest.main()
