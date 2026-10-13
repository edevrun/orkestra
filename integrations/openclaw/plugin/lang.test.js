// integrations/openclaw/plugin/lang.test.js — node --test lang.test.js
// The resolver must behave like orkestra_core/i18n.py (the Python side of the same table lives in
// plugins/orkestra/tests/test_i18n.py; bridge/tests/test_bridge_i18n.py runs both on one case list).
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";
import { DEFAULT_LANG, SUPPORTED, langFromConfig, langFromLocale, normalizeLang, resolveLang } from "./lang.js";

let home;
before(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "orkestra-lang-"));
});
after(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

const config = (value) => fs.writeFileSync(path.join(home, "config.json"), typeof value === "string" ? value : JSON.stringify(value));
const noConfig = () => fs.rmSync(path.join(home, "config.json"), { force: true });
const env = (extra = {}) => ({ ORKESTRA_HOME: home, ...extra });

test("English is the default and only en/tr are supported", () => {
  noConfig();
  assert.equal(DEFAULT_LANG, "en");
  assert.deepEqual([...SUPPORTED], ["en", "tr"]);
  assert.equal(resolveLang(env()), "en");
});

test("normalizeLang accepts codes, locales and names, and nothing else", () => {
  const table = {
    tr: "tr", TR: "tr", "tr_TR.UTF-8": "tr", "tr-TR": "tr", "tr_TR@euro": "tr", Turkish: "tr", "türkçe": "tr", turkce: "tr",
    en: "en", EN: "en", "en_US.UTF-8": "en", "en-GB": "en", English: "en", ingilizce: "en",
    auto: null, "": null, C: null, POSIX: null, de: null, "de_DE.UTF-8": null, trx: null, "  tr  ": "tr",
  };
  for (const [raw, want] of Object.entries(table)) {
    assert.equal(normalizeLang(raw), want, JSON.stringify(raw));
  }
  for (const odd of [undefined, null, 7, {}, []]) {
    assert.equal(normalizeLang(odd), null);
  }
});

test("ORKESTRA_LANG beats the config file, which beats the OS locale", () => {
  config({ lang: "tr" });
  assert.equal(resolveLang(env({ ORKESTRA_LANG: "en", LANG: "tr_TR.UTF-8" })), "en");
  assert.equal(resolveLang(env({ ORKESTRA_LANG: "tr", LANG: "en_US.UTF-8" })), "tr");
  assert.equal(resolveLang(env({ LANG: "en_US.UTF-8" })), "tr");
  config({ lang: "en" });
  assert.equal(resolveLang(env({ LANG: "tr_TR.UTF-8" })), "en");
});

test("auto falls through to the locale; an unsupported explicit value means English", () => {
  config({ lang: "auto" });
  assert.equal(resolveLang(env({ LANG: "tr_TR.UTF-8" })), "tr");
  assert.equal(resolveLang(env({ ORKESTRA_LANG: "auto", LC_ALL: "tr_TR.UTF-8" })), "tr");
  assert.equal(resolveLang(env({ LANG: "de_DE.UTF-8" })), "en");
  assert.equal(resolveLang(env({ ORKESTRA_LANG: "de", LANG: "tr_TR.UTF-8" })), "en");
});

test("locale variables: order, skipping C/POSIX, and the LANGUAGE colon list", () => {
  noConfig();
  assert.equal(langFromLocale({ LC_ALL: "tr_TR.UTF-8", LANG: "en_US.UTF-8" }), "tr");
  assert.equal(langFromLocale({ LC_ALL: "C", LANG: "tr_TR.UTF-8" }), "tr");
  assert.equal(langFromLocale({ LANGUAGE: "de:tr:en" }), "tr");
  assert.equal(langFromLocale({ LANGUAGE: "de:fr" }), null);
  assert.equal(langFromLocale({}), null);
});

test("a broken, odd or missing config file is ignored", () => {
  for (const bad of ["{not json", "[]", '"tr"', JSON.stringify({ lang: 5 }), JSON.stringify({ lang: "klingon" }), ""]) {
    config(bad);
    assert.equal(resolveLang(env({ LANG: "tr_TR.UTF-8" })), "tr", bad);
  }
  noConfig();
  assert.equal(langFromConfig(env()), null);
  assert.equal(resolveLang(env()), "en");
  assert.equal(langFromConfig({ ORKESTRA_HOME: path.join(home, "does-not-exist") }), null);
});

test("a changed config file is picked up (the cache keys on mtime and size)", () => {
  config({ lang: "tr" });
  assert.equal(resolveLang(env()), "tr");
  config({ lang: "en", padding: "x".repeat(5) });
  assert.equal(resolveLang(env()), "en");
});

test("a config file with a UTF-8 BOM is read like i18n.py reads it (utf-8-sig)", () => {
  // Windows PowerShell 5.1 `Set-Content -Encoding UTF8` writes the BOM; the CLI and the bridge honour it, so must this
  config("﻿" + JSON.stringify({ lang: "tr" }));
  assert.equal(langFromConfig(env()), "tr");
  assert.equal(resolveLang(env({ LANG: "en_US.UTF-8" })), "tr");
  config("﻿" + JSON.stringify({ lang: "en" }));
  assert.equal(resolveLang(env({ LANG: "tr_TR.UTF-8" })), "en");
});

test("the config lives under ORKESTRA_HOME, or ~/.orkestra when it is not set", () => {
  config({ lang: "tr" });
  assert.equal(langFromConfig({ ORKESTRA_HOME: home }), "tr");
  // "~" is expanded from the home directory of the process (HOME / USERPROFILE), like Path.expanduser()
  const fakeUser = fs.mkdtempSync(path.join(os.tmpdir(), "orkestra-lang-home-"));
  fs.mkdirSync(path.join(fakeUser, ".orkestra"));
  fs.writeFileSync(path.join(fakeUser, ".orkestra", "config.json"), JSON.stringify({ lang: "tr" }));
  const saved = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE };
  process.env.HOME = fakeUser;
  process.env.USERPROFILE = fakeUser;
  try {
    assert.equal(langFromConfig({}), "tr");
    assert.equal(langFromConfig({ ORKESTRA_HOME: "~/.orkestra" }), "tr");
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.rmSync(fakeUser, { recursive: true, force: true });
  }
});

test("resolveLang reads process.env when no env is given", () => {
  noConfig();
  const saved = {};
  for (const name of ["ORKESTRA_LANG", "ORKESTRA_HOME", "LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"]) {
    saved[name] = process.env[name];
    delete process.env[name];
  }
  try {
    process.env.ORKESTRA_HOME = home;
    assert.equal(resolveLang(), "en");
    process.env.ORKESTRA_LANG = "tr";
    assert.equal(resolveLang(), "tr");
  } finally {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});
