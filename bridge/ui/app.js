"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  token: "",
  timer: null,
  flowTimer: null,
  polling: false,
  flowPolling: false,
  enginesLoading: false,
  engineRequestId: 0,
  messages: new Map(),
};
const flowPhases = [
  ["intake", "İstek"],
  ["discover", "Keşif"],
  ["clarify", "Netleştir"],
  ["plan", "Plan"],
  ["approve", "Onay"],
  ["implement", "Uygula"],
  ["verify", "Doğrula"],
  ["review", "İncele"],
  ["deliver", "Teslim"],
  ["done", "Bitti"],
];
const flowStatusLabels = {
  active: "Akış sürüyor",
  awaiting_user: "Senden yanıt bekliyor",
  blocked: "Dikkat gerekiyor",
  paused: "Duraklatıldı",
  done: "Tamamlandı",
  error: "Hata",
  aborted: "Durduruldu",
};
const agentProfiles = {
  adversary: ["Karşıt inceleme", "Risk ve zayıf noktaları arar"],
  architect: ["Mimar", "Çözüm ve uygulama planı"],
  implementer: ["Uygulayıcı", "Onaylı görevleri uygular"],
  main: ["Orkestra", "Ana akış yöneticisi"],
  "plan-critic": ["Plan eleştirmeni", "Planı uygulamadan önce zorlar"],
  researcher: ["Araştırmacı", "Kod ve teknik kaynak keşfi"],
  reviewer: ["Kod inceleyici", "Değişiklikleri bağımsız inceler"],
  scout: ["Keşif ajanı", "Proje yapısını ve ilgili kodu bulur"],
  verifier: ["Doğrulayıcı", "Test ve kapı kanıtlarını kontrol eder"],
  ana: ["Orkestra", "Ana akış yöneticisi"],
};
const activityLabels = {
  agent_done: "Ajan raporu",
  answer: "Ajan yanıtı",
  bind: "Oturum bağlandı",
  deny: "Guard engeli",
  handback: "Kontrol devri",
  idle: "Bekleme",
  nudge: "Devam sinyali",
  result: "Sonuç",
  tool: "Araç kullanımı",
  user: "Kullanıcı",
  wait_agents: "Ajanlar bekleniyor",
};
const statusLabels = {
  queued: "Sırada",
  running: "Çalışıyor",
  awaiting_user: "Yanıtın bekleniyor",
  blocked: "Bloke",
  paused: "Duraklatıldı",
  stalled: "Devam gerekiyor",
  done: "Tamamlandı",
  error: "Hata",
  cancelled: "İptal edildi",
};

function setStatus(message, connected = false) {
  const node = $("connection-status");
  node.textContent = message;
  node.classList.toggle("connected", connected);
}

async function api(path, options = {}) {
  const headers = new Headers({ Authorization: `Bearer ${state.token}` });
  if (options.body !== undefined) headers.set("Content-Type", "application/json");
  const response = await fetch(path, {
    method: options.method || "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
    credentials: "same-origin",
  });
  let payload = {};
  try {
    payload = await response.json();
  } catch {
    payload = {};
  }
  if (!response.ok) throw new Error(payload.error || `İstek başarısız (HTTP ${response.status})`);
  return payload;
}

function fillProjects(data) {
  const select = $("project-select");
  select.replaceChildren(new Option("Proje seç…", ""));
  for (const project of data.projects || []) {
    select.add(new Option(project.name, project.name));
  }
  $("project-scope").textContent = data.restricted
    ? `${(data.projects || []).length} izinli proje gösteriliyor.`
    : `${(data.projects || []).length} workspace klasörü gösteriliyor; bridge allowlist’i boş.`;
  if (!data.projects || data.projects.length === 0) {
    $("project-scope").textContent = "Seçilebilir proje yok. Workspace veya allowed_projects ayarını kontrol et.";
  }
  updateStartButton();
}

function fillEngines(data) {
  const select = $("engine-select");
  const available = (data.engines || []).filter((engine) => engine.available);
  select.replaceChildren();
  for (const engine of data.engines || []) {
    const label = engine.available ? engine.label : `${engine.label} — kullanılamıyor`;
    const option = new Option(label, engine.id);
    option.disabled = !engine.available;
    if (engine.reason) option.title = engine.reason;
    select.add(option);
  }
  const selected = available.find((engine) => engine.id === data.default) || available[0];
  select.value = selected ? selected.id : "";
  const selectedEngine = (data.engines || []).find((engine) => engine.id === select.value);
  $("engine-select").title = selectedEngine && selectedEngine.reason ? selectedEngine.reason : "";
  const unavailable = (data.engines || []).filter((engine) => !engine.available && engine.reason);
  $("engine-hint").textContent = "Alt-agent ekibini Orkestra aşamaya göre seçer."
    + (unavailable.length ? ` Kullanılamayan motor: ${unavailable.map((engine) => `${engine.label} — ${engine.reason}`).join("; ")}` : "");
  updateStartButton();
}

async function loadEngines() {
  const project = $("project-select").value;
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  const requestId = ++state.engineRequestId;
  state.enginesLoading = true;
  updateStartButton();
  try {
    const data = await api(`/engines${query}`);
    if (requestId === state.engineRequestId) fillEngines(data);
  } finally {
    if (requestId === state.engineRequestId) {
      state.enginesLoading = false;
      updateStartButton();
    }
  }
}

async function connect(event) {
  event.preventDefault();
  const entered = $("bridge-token").value.trim();
  $("bridge-token").value = "";
  $("connect-error").hidden = true;
  if (!entered) return;
  state.token = entered;
  try {
    const projects = await api("/projects");
    fillProjects(projects);
    await loadEngines();
    $("connect-panel").hidden = true;
    $("workbench").hidden = false;
    $("disconnect-button").hidden = false;
    setStatus("Yerel bridge’e bağlı", true);
    await Promise.all([refreshJobs(), refreshFlows()]);
    schedulePoll();
    scheduleFlowPoll();
  } catch (error) {
    state.token = "";
    $("connect-error").textContent = error.message;
    $("connect-error").hidden = false;
    setStatus("Bağlantı kurulamadı");
  }
}

function updateStartButton() {
  const engine = [...$("engine-select").options].find((option) => option.value === $("engine-select").value);
  $("start-button").disabled = !state.token
    || state.enginesLoading
    || !$("project-select").value
    || !engine
    || engine.disabled
    || !$("consent-check").checked
    || $("request-input").value.trim().length < 3;
}

function statusClass(status) {
  return String(status || "").replace(/[^a-z0-9_-]/gi, "");
}

function makeJobCard(job) {
  const card = document.createElement("article");
  card.className = "job-card";
  card.dataset.jobId = job.id;

  const main = document.createElement("div");
  main.className = "job-main";
  const titleRow = document.createElement("div");
  titleRow.className = "job-title-row";
  const project = document.createElement("span");
  project.className = "job-project";
  const status = document.createElement("span");
  status.className = "job-status";
  status.setAttribute("role", "status");
  titleRow.append(project, status);
  const meta = document.createElement("p");
  meta.className = "job-meta";
  main.append(titleRow, meta);

  const actions = document.createElement("div");
  actions.className = "job-actions";
  const cancel = document.createElement("button");
  cancel.className = "button button-quiet";
  cancel.type = "button";
  cancel.textContent = "İşi durdur";
  cancel.addEventListener("click", () => cancelJob(job.id, cancel));
  actions.append(cancel);

  const conversation = document.createElement("div");
  conversation.className = "chat-log";
  conversation.setAttribute("aria-live", "polite");
  const reply = document.createElement("form");
  reply.className = "reply-form";
  reply.hidden = true;
  const replyInput = document.createElement("textarea");
  replyInput.required = true;
  replyInput.maxLength = 10000;
  replyInput.rows = 2;
  replyInput.setAttribute("aria-label", "Orkestra'nın sorusuna yanıt");
  replyInput.placeholder = "Yanıtını yaz…";
  const replyButton = document.createElement("button");
  replyButton.className = "button button-primary";
  replyButton.type = "submit";
  replyButton.textContent = "Yanıtla";
  reply.append(replyInput, replyButton);
  reply.addEventListener("submit", (event) => sendReply(event, job.id, replyInput, replyButton));

  const error = document.createElement("p");
  error.className = "job-error";
  error.hidden = true;
  card.append(main, actions, conversation, reply, error);
  return card;
}

function renderConversation(card, job) {
  const messages = state.messages.get(job.id) || [];
  const lastMessage = typeof job.last_message === "string" ? job.last_message : "";
  if (lastMessage && lastMessage !== card.dataset.lastAgentMessage) {
    messages.push({ role: "agent", text: lastMessage });
    card.dataset.lastAgentMessage = lastMessage;
  }
  const content = document.createDocumentFragment();
  for (const item of messages.slice(-30)) {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${item.role === "user" ? "user" : "agent"}`;
    const label = document.createElement("b");
    label.textContent = item.role === "user" ? "Sen" : "Orkestra";
    const text = document.createElement("p");
    text.textContent = item.text;
    bubble.append(label, text);
    content.append(bubble);
  }
  const log = card.querySelector(".chat-log");
  log.replaceChildren(content);
  log.hidden = messages.length === 0;
  state.messages.set(job.id, messages.slice(-30));
}

function updateJobCard(card, job) {
  const status = statusLabels[job.status] || job.status || "Durum bilinmiyor";
  card.querySelector(".job-project").textContent = job.project || "Proje";
  const badge = card.querySelector(".job-status");
  badge.textContent = status;
  badge.className = `job-status ${statusClass(job.status)}`;
  const engine = job.engine === "cursor" ? "Cursor" : "Claude Code";
  const run = job.run_id ? ` · run ${job.run_id}` : "";
  card.querySelector(".job-meta").textContent = `${engine} · iş ${String(job.id || "").slice(0, 8)}${run}`;
  renderConversation(card, job);
  const canReply = ["awaiting_user", "blocked", "paused", "stalled"].includes(job.status);
  card.querySelector(".reply-form").hidden = !canReply;
  card.querySelector(".job-actions button").hidden = ["done", "error", "cancelled"].includes(job.status);
}

function renderJobs(jobs) {
  const list = $("jobs-list");
  const recent = (jobs || []).slice(-12).reverse();
  const existing = new Map([...list.querySelectorAll(".job-card")].map((card) => [card.dataset.jobId, card]));
  const keep = new Set();
  if (recent.length === 0) {
    list.replaceChildren();
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.textContent = "Henüz bridge işi yok. Yeni bir istek başlattığında burada görünür.";
    list.append(empty);
    return;
  }
  for (const job of recent) {
    if (!job || !job.id) continue;
    keep.add(job.id);
    const card = existing.get(job.id) || makeJobCard(job);
    updateJobCard(card, job);
    list.append(card);
  }
  for (const [id, card] of existing) {
    if (!keep.has(id)) card.remove();
  }
}

function flowElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function flowPhaseIndex(run) {
  const index = Number(run.phase_index);
  if (Number.isInteger(index) && index >= 0 && index < flowPhases.length) return index;
  const found = flowPhases.findIndex(([id]) => id === run.phase);
  return Math.max(0, found);
}

function flowUpdatedText(run) {
  const date = run.updated ? new Date(run.updated) : null;
  return date && !Number.isNaN(date.valueOf())
    ? `Son hareket ${date.toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit" })}`
    : `Run ${String(run.run_id || "").slice(0, 10)}`;
}

function updatePhaseTimers() {
  for (const timer of document.querySelectorAll(".flow-current-duration")) {
    const duration = phaseDuration(timer.dataset.phaseSince);
    timer.textContent = duration ? `${duration} bu fazda` : "mevcut adım";
  }
}

function phaseDuration(phaseSince) {
  const start = Number(phaseSince);
  if (!Number.isFinite(start) || start <= 0) return "";
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - start));
  if (elapsed < 60) return `${elapsed} sn`;
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} dk`;
  return `${Math.floor(elapsed / 3600)} sa ${Math.floor((elapsed % 3600) / 60)} dk`;
}

function agentProfile(name) {
  const key = String(name || "agent").toLowerCase().split(":").pop();
  return agentProfiles[key] || [key.replace(/[-_]/g, " "), "Orkestra ajanı"];
}

function flowGate(label, value) {
  const gate = flowElement("div", "flow-gate");
  gate.append(flowElement("span", "", label), flowElement("b", "", value || "—"));
  return gate;
}

function renderAgentNetwork(run) {
  const panel = flowElement("section", "flow-subpanel");
  panel.setAttribute("aria-label", "Ajan ekibi ve görev durumu");
  const agents = Array.isArray(run.agents)
    ? [...new Set(run.agents.filter((agent) => typeof agent === "string" && agent.trim()).map((agent) => agent.trim()))]
    : [];
  const tasks = Array.isArray(run.current_tasks) ? run.current_tasks.slice(0, 4) : [];
  const tasksTotal = Number(run.tasks_total) || 0;
  const tasksDone = Number(run.tasks_done) || 0;
  const heading = flowElement("div", "flow-subheading");
  heading.append(flowElement("h3", "", "Ajan ağı"));
  heading.append(flowElement("span", "", agents.length ? `${agents.length} açık ajan kaydı` : "güncel ekip"));
  panel.append(heading);

  const network = flowElement("div", "agent-network");
  const orchestrator = flowElement("div", "orchestrator-node");
  orchestrator.append(flowElement("span", "orchestrator-glyph", "O"));
  const orchestratorCopy = flowElement("div", "orchestrator-copy");
  orchestratorCopy.append(
    flowElement("b", "", "Orkestra"),
    flowElement("span", "", "Akış ve kalite kapıları"),
  );
  orchestrator.append(orchestratorCopy);
  network.append(orchestrator, flowElement("div", "agent-connector"));

  const agentGrid = flowElement("div", "flow-agent-grid");
  if (agents.length) {
    for (const name of agents.slice(0, 8)) {
      const [label, description] = agentProfile(name);
      const task = tasks.find((item) => String(item.agent || "").split(":").pop() === name.split(":").pop());
      const card = flowElement("div", "flow-agent-card");
      const initials = label.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toLocaleUpperCase("tr-TR");
      card.append(flowElement("span", "agent-glyph", initials || "A"));
      const copy = flowElement("div", "agent-copy");
      copy.append(
        flowElement("b", "", label),
        flowElement("span", "", task ? `${task.id || "Görev"} · ${task.title || description}` : description),
      );
      card.append(copy);
      agentGrid.append(card);
    }
  } else {
    const detail = run.status === "awaiting_user"
      ? "Ajanlar, Orkestra'nın sana yönelttiği yanıtı bekliyor."
      : "Bu kayıtta şu anda etkin alt-agent görünmüyor.";
    agentGrid.append(flowElement("div", "flow-agent-empty", detail));
  }
  network.append(agentGrid);
  if (agents.length) {
    network.append(flowElement("p", "flow-agent-note", "Ajanlar Orkestra'nın görev ve güvenlik kapıları içinde çalışır."));
  }
  panel.append(network);

  if (tasks.length) {
    const taskList = flowElement("div", "flow-current-tasks");
    for (const task of tasks) {
      const row = flowElement("div", "flow-task");
      row.append(flowElement("span", "flow-task-id", task.id || "Görev"));
      const copy = flowElement("div", "flow-task-copy");
      copy.append(
        flowElement("b", "", task.title || "Etkin görev"),
        flowElement("span", "", task.agent ? `Atanan ajan: ${agentProfile(task.agent)[0]}` : "Görev atanmış"),
      );
      row.append(copy);
      taskList.append(row);
    }
    panel.append(taskList);
  } else if (tasksTotal) {
    panel.append(flowElement("p", "flow-agent-note", `Görev ilerlemesi: ${tasksDone}/${tasksTotal} tamamlandı.`));
  }
  return panel;
}

function renderFlowActivity(run) {
  const panel = flowElement("section", "flow-subpanel");
  panel.setAttribute("aria-label", "Son ajan ve araç hareketleri");
  const heading = flowElement("div", "flow-subheading");
  heading.append(
    flowElement("h3", "", "Son hareketler"),
    flowElement("span", "", "gerçek hook kaydı"),
  );
  panel.append(heading);

  const entries = Array.isArray(run.activity) ? run.activity.slice(-6).reverse() : [];
  if (!entries.length) {
    panel.append(flowElement("div", "flow-agent-empty", "Bu run için görünür hareket kaydı henüz oluşmadı."));
    return panel;
  }
  const list = flowElement("ol", "flow-event-list flow-activity");
  list.setAttribute("aria-label", "Çalışmanın son ajan ve araç olayları");
  for (const entry of entries) {
    const kind = typeof entry.kind === "string" ? entry.kind : "";
    const row = flowElement("li", `flow-event${kind === "deny" ? " is-deny" : ""}${["user", "answer"].includes(kind) ? ` is-${kind}` : ""}`);
    const time = flowElement("time", "flow-event-time", entry.time || "");
    if (entry.time) time.dateTime = entry.time;
    const body = flowElement("div", "flow-event-body");
    const meta = flowElement("div", "flow-event-meta");
    const agent = flowElement("span", "flow-event-agent", agentProfile(entry.agent || "main")[0]);
    meta.append(agent, flowElement("span", "flow-event-kind", activityLabels[kind] || kind || "Hareket"));
    if (entry.tool) meta.append(flowElement("span", "flow-event-tool", entry.tool));
    const description = flowElement("p", "flow-event-description", entry.what || entry.tool || activityLabels[kind] || "Hareket kaydedildi.");
    description.title = description.textContent;
    body.append(meta, description);
    row.append(time, body);
    list.append(row);
  }
  panel.append(list);
  return panel;
}

function buildFlowCard(run) {
  const card = flowElement("article", "flow-card");
  card.dataset.status = statusClass(run.status);
  card.dataset.runId = String(run.run_id || "");
  const inner = flowElement("div", "flow-card-inner");
  const header = flowElement("div", "flow-card-header");
  const identity = flowElement("div", "flow-identity");
  const projectLine = flowElement("div", "flow-project-line");
  projectLine.append(flowElement("span", "flow-project-name", run.project_name || "Proje"));
  projectLine.append(flowElement("span", "flow-run-id", `run ${String(run.run_id || "").slice(0, 10)}`));
  projectLine.append(flowElement("span", "flow-engine-tag", run.engine === "cursor" ? "Cursor" : "Claude Code"));
  identity.append(projectLine, flowElement("p", "flow-title", run.title || "Başlıksız çalışma"));
  header.append(identity, flowElement("span", "flow-status-pill", flowStatusLabels[run.status] || run.status || "Durum bilinmiyor"));
  inner.append(header);

  const completed = run.status === "done" || run.phase === "done";
  const phaseIndex = completed ? flowPhases.length - 1 : flowPhaseIndex(run);
  const progress = completed ? 100 : Math.round((phaseIndex / (flowPhases.length - 1)) * 100);
  const currentPhase = flowPhases[phaseIndex] || flowPhases[0];
  const progressRow = flowElement("div", "flow-progress-row");
  progressRow.append(
    flowElement("span", "", `Şu an: ${currentPhase[1]}`),
    flowElement("strong", "", `${completed ? flowPhases.length : phaseIndex + 1} / ${flowPhases.length} faz`),
  );
  inner.append(progressRow);
  const progressTrack = flowElement("progress", "flow-progress-track");
  progressTrack.max = 100;
  progressTrack.value = progress;
  progressTrack.setAttribute("aria-label", "Orkestra faz ilerlemesi");
  inner.append(progressTrack);

  const scroll = flowElement("div", "flow-stage-scroll");
  scroll.tabIndex = 0;
  scroll.setAttribute("role", "group");
  scroll.setAttribute("aria-label", "Orkestra akış fazları. Dar ekranlarda yatay kaydır.");
  const stageList = flowElement("ol", "flow-stage-list");
  for (let index = 0; index < flowPhases.length; index += 1) {
    const [id, label] = flowPhases[index];
    const step = flowElement("li", "flow-stage");
    step.setAttribute("role", "listitem");
    if (index < phaseIndex || (completed && index <= phaseIndex)) step.classList.add("is-complete");
    else if (index === phaseIndex) {
      step.classList.add("is-current");
      step.setAttribute("aria-current", "step");
    }
    const marker = index < phaseIndex || (completed && index <= phaseIndex) ? "✓" : String(index + 1).padStart(2, "0");
    step.append(flowElement("span", "flow-stage-dot", marker), flowElement("span", "flow-stage-label", label));
    step.title = `${label}${index === phaseIndex ? " · şu anki faz" : ""}`;
    stageList.append(step);
  }
  scroll.append(stageList);
  inner.append(scroll);

  const current = flowElement("div", "flow-current");
  current.append(flowElement("span", "flow-current-mark", completed ? "✓" : String(phaseIndex + 1).padStart(2, "0")));
  const currentCopy = flowElement("div", "flow-current-copy");
  const liveText = run.live && typeof run.live.text === "string" ? run.live.text : "";
  const currentHeading = flowElement("b", "", currentPhase[1]);
  const duration = flowElement("span", "flow-current-duration");
  duration.dataset.phaseSince = String(run.phase_since || "");
  currentHeading.append(document.createTextNode(" · "), duration);
  currentCopy.append(currentHeading, flowElement("p", "", liveText || (completed ? "Bu çalışma tamamlandı." : "Aşama güncellendiğinde canlı durum burada görünür.")));
  current.append(currentCopy);
  inner.append(current);

  const lower = flowElement("div", "flow-lower-grid");
  lower.append(renderAgentNetwork(run), renderFlowActivity(run));
  inner.append(lower);

  const gateRow = flowElement("div", "flow-gates");
  const approveIndex = flowPhases.findIndex(([id]) => id === "approve");
  const approval = run.approved
    ? "Onaylandı"
    : phaseIndex < approveIndex
      ? "Henüz istenmedi"
      : run.status === "awaiting_user"
        ? "Yanıtın bekleniyor"
        : "Bekleniyor";
  const gates = run.gates || {};
  gateRow.append(
    flowGate("Plan onayı", approval),
    flowGate("Başlangıç kapısı", gates.baseline || "—"),
    flowGate("Regresyon kontrolü", gates.verify || "—"),
  );
  inner.append(gateRow);

  const footer = flowElement("div", "flow-run-foot");
  footer.append(
    flowElement("span", "", flowUpdatedText(run)),
    flowElement("span", "", Number(run.tasks_total)
      ? `${Number(run.tasks_done) || 0}/${Number(run.tasks_total)} görev tamamlandı`
      : "Görev planı henüz oluşmadı"),
  );
  card.append(inner, footer);
  return card;
}

function flowSignature(run) {
  return JSON.stringify([
    run.updated, run.status, run.phase, run.phase_index, run.approved, run.live,
    run.agents, run.current_tasks, run.tasks_done, run.tasks_total, run.gates, run.activity,
  ]);
}

function renderFlows(runs) {
  const openStatuses = new Set(["active", "awaiting_user", "blocked", "paused"]);
  const finishedStatuses = new Set(["done", "error", "aborted"]);
  const timeValue = (run) => {
    const value = Date.parse(run.updated || "");
    return Number.isNaN(value) ? 0 : value;
  };
  const valid = (Array.isArray(runs) ? runs : []).filter((run) => run && typeof run === "object");
  const open = valid.filter((run) => openStatuses.has(run.status))
    .sort((left, right) => timeValue(right) - timeValue(left));
  const recent = valid.filter((run) => finishedStatuses.has(run.status))
    .sort((left, right) => timeValue(right) - timeValue(left))
    .slice(0, 4);
  const visible = [...open, ...recent];
  const summary = $("flow-summary");
  summary.textContent = open.length
    ? `${open.length} açık akış${recent.length ? ` · ${recent.length} son run` : ""}`
    : recent.length
      ? `Aktif akış yok · ${recent.length} son run`
      : "Şu an açık akış yok";
  summary.parentElement.classList.toggle("has-active", open.length > 0);

  const list = $("flows-list");
  const existing = new Map([...list.querySelectorAll(".flow-card")].map((card) => [card.dataset.runId, card]));
  const retained = new Set();
  if (!visible.length) {
    list.replaceChildren();
    const empty = flowElement("div", "empty-state flow-empty");
    const orbit = flowElement("span", "empty-orbit");
    orbit.setAttribute("aria-hidden", "true");
    orbit.append(document.createElement("i"), document.createElement("i"), document.createElement("i"));
    empty.append(orbit, flowElement("b", "", "Orkestra hazır"));
    empty.append(flowElement("span", "", "Bir çalışma başladığında fazları, ajanları ve son hareketleri burada canlı izleyeceksin."));
    list.append(empty);
    return;
  }

  for (const run of visible) {
    const runId = String(run.run_id || `${run.project_name || "run"}-${run.updated || ""}`);
    retained.add(runId);
    const previous = existing.get(runId);
    const signature = flowSignature(run);
    if (previous && previous.dataset.signature === signature) {
      list.append(previous);
      continue;
    }
    const card = buildFlowCard(run);
    card.dataset.runId = runId;
    card.dataset.signature = signature;
    if (previous) {
      const oldScroll = previous.querySelector(".flow-stage-scroll");
      const newScroll = card.querySelector(".flow-stage-scroll");
      if (oldScroll && newScroll) newScroll.scrollLeft = oldScroll.scrollLeft;
      previous.replaceWith(card);
    } else {
      list.append(card);
    }
  }
  for (const [runId, card] of existing) {
    if (!retained.has(runId)) card.remove();
  }
}

async function refreshJobs() {
  if (!state.token || state.polling) return;
  state.polling = true;
  try {
    const data = await api("/jobs");
    renderJobs(data.jobs);
    $("jobs-updated").textContent = `Güncellendi ${new Date().toLocaleTimeString("tr-TR")}`;
  } catch (error) {
    $("jobs-updated").textContent = error.message;
    if (error.message.includes("unauthorized")) setStatus("Token geçersiz; yeniden bağlan");
  } finally {
    state.polling = false;
  }
}

async function refreshFlows() {
  if (!state.token || state.flowPolling) return;
  state.flowPolling = true;
  try {
    const data = await api("/runs");
    renderFlows(data.runs);
    updatePhaseTimers();
    $("flows-updated").textContent = `Gerçek run verisi · ${new Date().toLocaleTimeString("tr-TR", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}`;
  } catch (error) {
    $("flows-updated").textContent = `Akış yenilenemedi: ${error.message}`;
    if (error.message.includes("unauthorized")) setStatus("Token geçersiz; yeniden bağlan");
  } finally {
    state.flowPolling = false;
  }
}

function schedulePoll() {
  clearTimeout(state.timer);
  if (!state.token) return;
  state.timer = setTimeout(async () => {
    await refreshJobs();
    schedulePoll();
  }, 2500);
}

function scheduleFlowPoll() {
  clearTimeout(state.flowTimer);
  if (!state.token) return;
  state.flowTimer = setTimeout(async () => {
    await refreshFlows();
    scheduleFlowPoll();
  }, 6000);
}

async function startJob(event) {
  event.preventDefault();
  $("job-error").hidden = true;
  const request = $("request-input").value.trim();
  const button = $("start-button");
  button.disabled = true;
  try {
    const job = await api("/jobs", {
      method: "POST",
      body: {
        project: $("project-select").value,
        request,
        engine: $("engine-select").value,
        source: "web-ui",
      },
    });
    state.messages.set(job.id, [{ role: "user", text: request }]);
    $("request-input").value = "";
    $("consent-check").checked = false;
    $("request-count").textContent = "0 / 10.000";
    await Promise.all([refreshJobs(), refreshFlows()]);
  } catch (error) {
    $("job-error").textContent = error.message;
    $("job-error").hidden = false;
  } finally {
    updateStartButton();
  }
}

async function sendReply(event, id, input, button) {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  button.disabled = true;
  const card = button.closest(".job-card");
  const error = card.querySelector(".job-error");
  error.hidden = true;
  try {
    await api(`/jobs/${encodeURIComponent(id)}/reply`, { method: "POST", body: { text } });
    const messages = state.messages.get(id) || [];
    messages.push({ role: "user", text });
    state.messages.set(id, messages.slice(-30));
    input.value = "";
    await Promise.all([refreshJobs(), refreshFlows()]);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function cancelJob(id, button) {
  if (!window.confirm("Bu Orkestra işini durdurmak istediğine emin misin?")) return;
  button.disabled = true;
  const card = button.closest(".job-card");
  const error = card.querySelector(".job-error");
  error.hidden = true;
  try {
    await api(`/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST", body: {} });
    await Promise.all([refreshJobs(), refreshFlows()]);
  } catch (err) {
    error.textContent = err.message;
    error.hidden = false;
  } finally {
    button.disabled = false;
  }
}

function disconnect() {
  state.token = "";
  clearTimeout(state.timer);
  clearTimeout(state.flowTimer);
  state.flowPolling = false;
  state.engineRequestId += 1;
  state.enginesLoading = false;
  state.messages.clear();
  $("workbench").hidden = true;
  $("connect-panel").hidden = false;
  $("disconnect-button").hidden = true;
  $("jobs-list").replaceChildren();
  $("flows-list").replaceChildren();
  $("bridge-token").value = "";
  $("connect-error").hidden = true;
  setStatus("Bağlı değil");
}

$("connect-form").addEventListener("submit", connect);
$("job-form").addEventListener("submit", startJob);
$("project-select").addEventListener("change", () => {
  loadEngines().catch((error) => {
    $("job-error").textContent = error.message;
    $("job-error").hidden = false;
  });
});
$("request-input").addEventListener("input", () => {
  $("request-count").textContent = `${$("request-input").value.length.toLocaleString("tr-TR")} / 10.000`;
  updateStartButton();
});
$("engine-select").addEventListener("change", updateStartButton);
$("consent-check").addEventListener("change", updateStartButton);
$("refresh-button").addEventListener("click", () => Promise.all([refreshJobs(), refreshFlows()]));
$("disconnect-button").addEventListener("click", disconnect);
