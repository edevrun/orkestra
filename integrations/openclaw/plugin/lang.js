// integrations/openclaw/plugin/lang.js
// Which language the plugin talks in (English or Turkish). Same precedence as orkestra_core/i18n.py, so the plugin,
// the bridge and the CLI agree on one machine:
//   1. env  ORKESTRA_LANG                 (en | tr | auto)
//   2. file <ORKESTRA_HOME or ~/.orkestra>/config.json  "lang"   (en | tr | auto)
//   3. the OS locale: LC_ALL, LC_MESSAGES, LANG, LANGUAGE (a `tr...` locale selects Turkish)
//   4. English
// An explicit but unsupported ORKESTRA_LANG (for example "de") means English and never falls through to the locale.
// Node built-ins only; nothing here touches the disk until resolveLang() runs, and a broken or missing config file
// is ignored.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const SUPPORTED = Object.freeze(["en", "tr"]);
export const DEFAULT_LANG = "en";

const NAMES = {
  en: "en",
  english: "en",
  ingilizce: "en",
  tr: "tr",
  turkish: "tr",
  turkce: "tr",
  "türkçe": "tr",
};
const LOCALE_VARS = ["LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"];

/** `tr`, `TR`, `tr_TR.UTF-8`, `tr-TR`, `Turkish` -> "tr"; `en_US.UTF-8` -> "en"; anything else (`auto`, `C`, `de`) -> null. */
export function normalizeLang(code) {
  if (typeof code !== "string") {
    return null;
  }
  const raw = code.trim().toLowerCase();
  if (raw === "") {
    return null;
  }
  if (Object.hasOwn(NAMES, raw)) {
    return NAMES[raw];
  }
  const head = raw.split(/[._@:-]/, 1)[0];
  return head === "en" || head === "tr" ? head : null;
}

/** The first supported language among the locale variables (LANGUAGE is a colon list: "tr:en"). */
export function langFromLocale(env) {
  for (const name of LOCALE_VARS) {
    for (const part of String(env[name] ?? "").split(":")) {
      const code = normalizeLang(part);
      if (code) {
        return code;
      }
    }
  }
  return null;
}

function expandHome(p) {
  if (p === "~") {
    return os.homedir();
  }
  if (p.startsWith("~/") || p.startsWith("~\\")) {
    return path.join(os.homedir(), p.slice(2));
  }
  return p;
}

const configCache = new Map();

/** `lang` of the global config.json, tolerant of a missing or broken file; cached per file mtime and size. */
export function langFromConfig(env) {
  const file = path.join(expandHome(env.ORKESTRA_HOME || "~/.orkestra"), "config.json");
  let stamp;
  try {
    const st = fs.statSync(file);
    stamp = `${st.mtimeMs}:${st.size}`;
  } catch {
    return null;
  }
  const hit = configCache.get(file);
  if (hit && hit.stamp === stamp) {
    return hit.value;
  }
  let value = null;
  try {
    // i18n.py reads the file as utf-8-sig: Windows editors (PowerShell 5.1 Set-Content -Encoding UTF8) write a BOM
    const data = JSON.parse(fs.readFileSync(file, "utf8").replace(/^﻿/, ""));
    value = data && typeof data === "object" && !Array.isArray(data) ? normalizeLang(data.lang) : null;
  } catch {
    value = null;
  }
  configCache.set(file, { stamp, value });
  return value;
}

/** The language for human-facing text right now (see the header for the order). */
export function resolveLang(env = process.env) {
  const forced = normalizeLang(env.ORKESTRA_LANG);
  if (forced) {
    return forced;
  }
  if (!["", "auto"].includes(String(env.ORKESTRA_LANG ?? "").trim().toLowerCase())) {
    return DEFAULT_LANG;
  }
  return langFromConfig(env) ?? langFromLocale(env) ?? DEFAULT_LANG;
}
