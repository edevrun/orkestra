// integrations/openclaw/plugin/logic.js
// Pure helpers for the OpenClaw ↔ orkestra bridge plugin: Telegram reply routing, option-letter
// expansion, notification text and change detection. No I/O here; see index.js / client.js.

const PROJECT = String.raw`[A-Za-z0-9._-]{1,80}`;
const JOB_ID = String.raw`[a-f0-9]{12}`;
// Matches the bridge notification header `[proj] <status> · <phase> · iş <id>` and the
// start confirmation `Başladım: [proj] iş <id>`, wherever they sit in the quoted message.
const JOB_REF = new RegExp(String.raw`\[(${PROJECT})\][^\n]*?\biş\s+(${JOB_ID})\b`);

/** Statuses that need no notification (the job is still working). */
export const RUNNING_STATUSES = new Set(["running", "queued"]);
/** Open run statuses, same as orkestra's state.OPEN_STATUSES. */
export const OPEN_RUN_STATUSES = new Set(["active", "awaiting_user", "blocked", "paused"]);
const OPEN_JOB_STATUSES = new Set(["running", "queued", "awaiting_user"]);

const STATUS_LABELS = {
  running: "▶️ Çalışıyor",
  queued: "⏳ Sırada",
  awaiting_user: "⏸ Yanıtını bekliyor",
  done: "✅ Tamamlandı",
  blocked: "⛔ Engellendi",
  error: "❌ Hata",
  cancelled: "🛑 İptal",
  paused: "⏸ Duraklatıldı",
};

/**
 * Finds the orkestra job a Telegram reply points at, from the quoted bot message text.
 * @param {string | undefined} quoted
 * @returns {{ project: string, jobId: string } | null}
 */
export function parseJobRef(quoted) {
  if (typeof quoted !== "string" || quoted === "") {
    return null;
  }
  const m = JOB_REF.exec(quoted);
  return m ? { project: m[1], jobId: m[2] } : null;
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// orkestra records a plan approval only for a whole-message approval word (intent.approval), so a
// chosen "a) Onayla — uygulamaya başla" option is forwarded as the canonical "onayla". Options with
// conditions or change requests stay verbatim (orkestra then treats them as a change request).
const APPROVAL_OPTION = /^(onayla|onay|evet|başla|uygula|kabul|approve|yes)\b/u;
const APPROVAL_HEDGE = /(\?|\bdeğil|\bama\b|\bfakat\b|\bancak\b|\bbut\b|\bnot\b|\bno\b|hayır|iptal|vazgeç|değiştir|revize|düzelt|ekle|çıkar|reject|cancel|change|sonra|bekle)/u;

function isApprovalOption(option) {
  const text = option.toLocaleLowerCase("tr").trim();
  return APPROVAL_OPTION.test(text) && !APPROVAL_HEDGE.test(text);
}

/**
 * A bare option letter ("b", "B", "b)", "(b)") becomes "b) <option text>" when the quoted
 * question lists that option, so the answer stays unambiguous. Anything else is sent as typed.
 * @param {string} text the user's reply
 * @param {string | undefined} quoted the orkestra message being replied to
 * @returns {string}
 */
export function expandOptionReply(text, quoted) {
  const reply = String(text ?? "").trim();
  const letterMatch = /^\(?([a-hA-H])[).:]?$/.exec(reply);
  if (!letterMatch || typeof quoted !== "string") {
    return reply;
  }
  const letter = letterMatch[1].toLowerCase();
  const l = escapeRegExp(letter);
  const patterns = [
    // Line-start options: "b) text", "b. text", "(b) text", "- b) text".
    new RegExp(String.raw`^\s*(?:[-*•]\s*)?\(?${l}[).:]\s+(.+?)\s*$`, "im"),
    // Inline options: "... (a) medium (Önerilen) (b) high".
    new RegExp(String.raw`\(${l}\)\s+(.+?)(?=\s+\([a-h]\)\s|\n|$)`, "i"),
  ];
  for (const re of patterns) {
    const m = re.exec(quoted);
    if (m && m[1].trim() !== "") {
      const option = m[1].trim().slice(0, 300);
      return isApprovalOption(option) ? "onayla" : `${letter}) ${option}`;
    }
  }
  return reply;
}

/**
 * Same text the bridge would send itself, plus a reply hint while the job waits for the user.
 * @param {{ id: string, project: string, status: string, phase?: string | null, last_message?: string | null }} job
 * @returns {string}
 */
export function formatNotification(job) {
  const label = STATUS_LABELS[job.status] ?? job.status;
  const header = `[${job.project}] ${label}` + (job.phase ? ` · ${job.phase}` : "") + ` · iş ${job.id}`;
  const body = (job.last_message ?? "").trim();
  const hint = job.status === "awaiting_user" ? "\n\n↩️ Cevap için bu mesajı Yanıtla (seçenekliyse yalnız harfi yaz)." : "";
  return header + (body ? `\n\n${body}` : "") + hint;
}

/**
 * The open orkestra run of a project, if any. A second run would make orkestra's run skill resume
 * (take over) the existing one, so /yap must refuse while this returns a run.
 * @param {any[]} runs `orkestra watch --json`
 * @param {string} project folder name under the workspace
 */
export function findOpenRun(runs, project) {
  return (runs ?? []).find((r) => OPEN_RUN_STATUSES.has(r.status) &&
    (r.project_name === project || String(r.project ?? "").split("/").pop() === project)) ?? null;
}

/** Why /yap did not start: the project already has an open run. */
export function formatBusyProject(project, run) {
  const where = run.mode === "headless" ? "Telegram /yap" : "masaüstü";
  const tasks = run.tasks_total ? ` · görev ${run.tasks_done ?? 0}/${run.tasks_total}` : "";
  return (
    `⛔ Başlatmadım: [${project}] projesinde zaten açık bir orkestra run var — "${run.title}"\n` +
    `${run.phase_tr || run.phase}${tasks} · ${where} · ${run.live?.text || run.status_tr || run.status}\n` +
    "Aynı projede ikinci run, açık olanı devralıp bozabilir. Bitmesini bekle (/durum) ya da o run'ı kendi oturumundan kapat."
  );
}

/**
 * The truthful `/durum`: every open orkestra run in the workspace (`orkestra watch --json`, including
 * runs started on the desktop) plus the bridge jobs started from Telegram.
 * @param {{ runs?: any[] | null, runsError?: string | null, bridgeJobs?: any[] | null, bridgeError?: string | null }} input
 * @returns {string}
 */
export function formatStatus({ runs, runsError, bridgeJobs, bridgeError }) {
  const lines = ["🎼 Orkestra durumu"];
  const open = (runs ?? []).filter((r) => OPEN_RUN_STATUSES.has(r.status));
  const closed = (runs ?? []).filter((r) => !OPEN_RUN_STATUSES.has(r.status));
  if (runsError) {
    lines.push(`⚠️ Run listesi alınamadı: ${runsError}`);
  } else if (open.length === 0) {
    lines.push("Açık run yok.");
  } else {
    lines.push(`Açık run'lar (${open.length}):`);
    for (const r of open) {
      const tasks = r.tasks_total ? ` · görev ${r.tasks_done ?? 0}/${r.tasks_total}` : "";
      const where = r.mode === "headless" ? "Telegram işi" : "masaüstü";
      lines.push(`• ${r.project_name} — ${r.title}`);
      lines.push(`  ${r.phase_tr || r.phase}${tasks} · ${where} · ${r.live?.text || r.status_tr || r.status}`);
    }
  }
  if (closed.length > 0) {
    lines.push(`Son 12 saatte kapanan: ${closed.map((r) => `${r.project_name} (${r.status_tr || r.status})`).join(", ")}`);
  }
  const jobs = (bridgeJobs ?? []).filter((j) => OPEN_JOB_STATUSES.has(j.status));
  if (bridgeError) {
    lines.push(`⚠️ Telegram işleri alınamadı: ${bridgeError}`);
  } else if (jobs.length === 0) {
    lines.push("Telegram'dan başlatılmış açık iş yok.");
  } else {
    lines.push(`Telegram'dan başlatılan açık işler (${jobs.length}):`);
    for (const j of jobs) {
      lines.push(`• ${j.id} · ${j.project} · ${STATUS_LABELS[j.status] ?? j.status}` + (j.phase ? ` · ${j.phase}` : ""));
    }
    lines.push("↩️ Bu işlerin bildirim mesajını Yanıtla ile cevaplayabilirsin.");
  }
  if (open.some((r) => r.mode !== "headless")) {
    lines.push("ℹ️ Masaüstünde başlatılan run'lar buradan izlenir; soruları o projenin Claude penceresinden cevaplanır.");
  }
  return lines.join("\n");
}

/**
 * Decides which jobs to announce. Without prior state everything currently known is marked as
 * seen (no flood of old jobs on first start); later, every stopped job whose `updated` changed
 * is announced exactly once.
 * @param {Array<{ id: string, status: string, updated?: string }>} jobs
 * @param {Record<string, string> | null} seen jobId → last announced `updated`
 * @returns {{ announce: typeof jobs, seen: Record<string, string> }}
 */
export function diffJobs(jobs, seen) {
  const next = { ...(seen ?? {}) };
  const announce = [];
  for (const job of jobs) {
    if (!job?.id || !job.updated) {
      continue;
    }
    if (seen === null) {
      next[job.id] = job.updated;
      continue;
    }
    if (RUNNING_STATUSES.has(job.status) || next[job.id] === job.updated) {
      continue;
    }
    announce.push(job);
  }
  return { announce, seen: next };
}

/**
 * Collects message ids from `openclaw message send --json` output (shape-agnostic).
 * @param {string} stdout
 * @returns {string[]}
 */
export function parseSentMessageIds(stdout) {
  const ids = new Set();
  const visit = (node) => {
    if (Array.isArray(node)) {
      node.forEach(visit);
    } else if (node && typeof node === "object") {
      for (const [key, value] of Object.entries(node)) {
        if (/^(message_?id|messageIds?)$/i.test(key)) {
          (Array.isArray(value) ? value : [value]).forEach((v) => {
            if (typeof v === "string" || typeof v === "number") ids.add(String(v));
          });
        } else {
          visit(value);
        }
      }
    }
  };
  try {
    visit(JSON.parse(stdout));
  } catch {
    // Not JSON: no ids; routing still works through the quoted header.
  }
  return [...ids];
}
