"use strict";
function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
const ICONS = {
  home: "M3 10l9-7 9 7v10H3z M9 20v-7h6v7",
  folder: "M3 5h7l2 3h9v12H3z",
  tasks:
    "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2 M7 11l3 3 7-7",
  file: "M5 2h9l5 5v15H5z M14 2v6h5 M8 12h7 M8 16h7",
  chart: "M5 20V11 M12 20V4 M19 20V8",
  network:
    "M12 8v4 M12 12l-7 5 M12 12l7 5 M9 5a3 3 0 1 0 6 0 3 3 0 1 0-6 0 M2 19a3 3 0 1 0 6 0 3 3 0 1 0-6 0 M16 19a3 3 0 1 0 6 0 3 3 0 1 0-6 0",
  people:
    "M8 11a4 4 0 1 0 0-8 4 4 0 1 0 0 8 M1 21v-3a7 7 0 0 1 14 0v3z M16 3a4 4 0 0 1 0 8 M17 14a6 6 0 0 1 6 6v1h-5",
  link: "M10 13a5 5 0 0 0 7 0l4-4a5 5 0 0 0-7-7l-2 2 M14 11a5 5 0 0 0-7 0l-4 4a5 5 0 0 0 7 7l2-2",
  history: "M3 8a10 10 0 1 1-1 8 M3 2v6h6 M12 6v7l4 2",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 1 0 0-8 M9 2h6l1 3 3 1 3 5-2 3v4l-5 3-3-1-3 1-5-3v-4l-2-3 3-5 3-1z",
  search: "M10 3a7 7 0 1 0 0 14 7 7 0 1 0 0-14 M15 15l6 6",
  bell: "M5 17h14l-2-4V9a5 5 0 0 0-10 0v4z M10 21h4 M12 2v2",
  sparkles:
    "M12 2l2.5 7.5L22 12l-7.5 2.5L12 22l-2.5-7.5L2 12l7.5-2.5z M21 2v4 M19 4h4",
  play: "M7 3l13 9-13 9z",
  user: "M8 7a4 4 0 1 0 8 0 4 4 0 1 0-8 0 M4 22v-4a8 8 0 0 1 16 0v4",
  warning: "M12 3L2 21h20z M12 9v5 M12 17v1",
  check: "M5 12l4 4 10-10",
  chevron: "M9 5l7 7-7 7",
  database:
    "M3 6c0-5 18-5 18 0s-18 5-18 0 M3 6v12c0 5 18 5 18 0V6 M3 12c0 5 18 5 18 0",
  plus: "M12 4v16 M4 12h16",
  download: "M12 3v12 M7 10l5 5 5-5 M4 17v4h16v-4",
  arrow: "M4 12h16 M14 6l6 6-6 6",
  menu: "M3 6h18 M3 12h18 M3 18h18",
  shield: "M12 2l9 4v6c0 5-9 10-9 10S3 17 3 12V6z M8 12l3 3 6-6",
  clock: "M12 2a10 10 0 1 0 0 20 10 10 0 1 0 0-20 M12 6v6l4 3",
};
const icon = (name) =>
  `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICONS[name] || ICONS.file}"/></svg>`;
const $ = (q, root = document) => root.querySelector(q);
const titleCase = (s) =>
  String(s || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
const dt = (value) =>
  value
    ? new Date(
        /(Z|[+-]\d{2}:\d{2})$/.test(value) ? value : value + "Z",
      ).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Not yet";
const dateInput = () =>
  new Date(Date.now() - new Date().getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
const compact = (s) =>
  String(s || "").length > 170
    ? String(s).slice(0, 167) + "…"
    : String(s || "");
const S = {
  workspace: localStorage.getItem("tpllm-workspace") || "live",
  page: "dashboard",
  program: "",
  data: null,
  catalogue: { tools: [], groups: [] },
  token: "",
  query: "",
  taskStatus: "",
  toolQuery: "",
  busy: false,
};
const NAV = [
  ["dashboard", "Dashboard", "home"],
  ["projects", "Projects", "folder"],
  ["tasks", "Tasks", "tasks"],
  ["decisions", "Decisions", "file"],
  ["kpis", "KPIs", "chart"],
  ["engineering", "Engineering", "file"],
  ["intelligence", "Intelligence", "sparkles"],
  ["relationships", "Relationships", "network"],
  ["meetings", "Meetings & briefs", "people"],
  ["connections", "Connections", "link"],
];
const SECONDARY = [
  ["history", "History", "history"],
  ["settings", "Settings", "settings"],
];
async function api(path, body, method) {
  const url =
    "/api/product" +
    path +
    (path.includes("?") ? "&" : "?") +
    "workspace=" +
    S.workspace;
  const response = await fetch(url, {
    method: method || (body ? "POST" : "GET"),
    headers: {
      "Content-Type": "application/json",
      "X-TPLLM-Client": "dashboard",
      ...(S.token && S.workspace === "live" && !path.startsWith("/demo")
        ? { Authorization: "Bearer " + S.token }
        : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  let result;
  try {
    result = await response.json();
  } catch {
    throw new Error(
      "The server returned an unreadable response. Please try again.",
    );
  }
  if (!response.ok) {
    const detail = result.detail;
    throw new Error(
      typeof detail === "string" ? detail : JSON.stringify(detail || result),
    );
  }
  return result;
}
function toast(message, error = false) {
  const el = $("#toast");
  el.textContent = message;
  el.className = "visible" + (error ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.className = ""), error ? 7000 : 4500);
}
async function busy(button, work) {
  if (S.busy) return;
  S.busy = true;
  const old = button?.innerHTML;
  if (button) {
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span> Working…';
  }
  try {
    return await work();
  } catch (e) {
    toast(e.message, true);
    const existing = $(".error-message", $("#drawer-body"));
    existing?.remove();
    if ($("#drawer").open) {
      const error = document.createElement("div");
      error.className = "error-message";
      error.setAttribute("role", "alert");
      error.textContent = e.message;
      $("#drawer-body").prepend(error);
    }
  } finally {
    S.busy = false;
    if (button?.isConnected) {
      button.disabled = false;
      button.innerHTML = old;
    }
  }
}
async function refresh() {
  const [data, catalogue] = await Promise.all([
    api("/state"),
    api("/catalogue"),
  ]);
  S.data = data;
  S.catalogue = catalogue;
  if (S.program && !data.programs.some((p) => p.id === S.program))
    S.program = "";
  render();
}
function programs() {
  return S.data.programs.filter((p) => !S.program || p.id === S.program);
}
function tasks() {
  return programs().flatMap((p) =>
    (p.artifacts.task_board?.tasks || []).map((t) => ({
      ...t,
      program_id: p.id,
      program_name: p.name,
    })),
  );
}
function decisions() {
  return programs().flatMap((p) =>
    (p.artifacts.decision_log?.decisions || []).map((d) => ({
      ...d,
      program_id: p.id,
      program_name: p.name,
      review: p.decision_reviews[d.id],
    })),
  );
}
function pendingDecisions() {
  return decisions().filter(
    (d) =>
      d.status === "PROPOSED" &&
      !["reject", "defer"].includes(d.review?.action),
  );
}
function questions() {
  return S.data.clarifications.filter(
    (q) => (!S.program || q.program_id === S.program) && q.status === "open",
  );
}
function programOptions(value = "", all = false) {
  return (
    (all
      ? '<option value="">All programs</option>'
      : '<option value="">Select a project</option>') +
    S.data.programs
      .map(
        (p) =>
          `<option value="${esc(p.id)}" ${p.id === value ? "selected" : ""}>${esc(p.name)}</option>`,
      )
      .join("")
  );
}
function badge(text, type = "") {
  return `<span class="badge ${esc(type)}">${esc(text)}</span>`;
}
function button(text, action, args = "", type = "") {
  return `<button class="button ${type}" data-action="${action}" ${args}>${text}</button>`;
}
function toolButton(name, label, program = "", type = "") {
  return button(
    label || titleCase(name),
    "tool",
    `data-tool="${name}" data-program="${esc(program)}"`,
    type,
  );
}
function empty(title, description, action = "") {
  return `<div class="empty">${icon("folder")}<h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;
}
function heading(name, subtitle, actions = "") {
  return `<div class="page-head"><div><h1>${name}</h1><p class="subtitle">${esc(subtitle)}</p></div><div class="head-actions">${actions}</div></div>`;
}
function programSelect() {
  return `<select id="program-filter" aria-label="Filter by program">${programOptions(S.program, true)}</select>`;
}
function panel(title, body, cls = "") {
  return `<section class="panel ${cls}"><div class="panel-inner"><div class="panel-title"><h2>${title}</h2></div>${body}</div></section>`;
}
function sourceButton(pid, label = "View evidence") {
  return `<button class="text-button" data-action="evidence" data-program="${esc(pid)}">${esc(label)}</button>`;
}
function render() {
  const page = [...NAV, ...SECONDARY].find((x) => x[0] === S.page);
  document.title = `${page?.[1] || "Dashboard"} · TPLLM`;
  $("#navigation").innerHTML = NAV.map(
    ([id, name, i]) =>
      `<a href="#${id}" class="nav-item ${S.page === id ? "active" : ""}" ${S.page === id ? 'aria-current="page"' : ""}>${icon(i)}<span>${name}</span>${id === "decisions" && pendingDecisions().length ? `<span class="nav-count">${pendingDecisions().length}</span>` : ""}</a>`,
  ).join("");
  $("#secondary-navigation").innerHTML = SECONDARY.map(
    ([id, name, i]) =>
      `<a href="#${id}" class="nav-item ${S.page === id ? "active" : ""}" ${S.page === id ? 'aria-current="page"' : ""}>${icon(i)}<span>${name}</span></a>`,
  ).join("");
  $("#workspace-label").textContent =
    S.workspace === "demo" ? "Engineering" : "My workspace";
  $("#session-label").textContent =
    S.workspace === "demo"
      ? "Demo workspace"
      : S.token
        ? "Authenticated session"
        : "Read-only session";
  const pages = {
    dashboard: renderDashboard,
    kpis: () => Flow.kpis(),
    engineering: () => Flow.engineering(),
    projects: renderProjects,
    tasks: renderTasks,
    decisions: renderDecisions,
    intelligence: renderIntelligence,
    relationships: renderRelationships,
    meetings: renderMeetings,
    connections: renderConnections,
    history: renderHistory,
    settings: renderSettings,
  };
  $("#main").innerHTML = (pages[S.page] || renderDashboard)();
  const count = S.data.evidence.length;
  $("#footer").innerHTML =
    `${icon("database")}<span>${count} evidence record${count === 1 ? "" : "s"}</span><span>·</span><span>${S.data.revision ? esc(S.data.revision) : "No analysis yet"}</span><span>│</span><button data-action="navigate" data-page="connections">Review connections</button><span class="footer-right">${S.workspace === "demo" ? "DEMO DATA · ISOLATED WORKSPACE" : S.data.settings.hosted ? "HOSTED WORKSPACE" : "LOCAL WORKSPACE"} &nbsp; · &nbsp; SCOPED AUTHORITY</span>`;
  const filter = $("#program-filter");
  if (filter)
    filter.onchange = () => {
      S.program = filter.value;
      render();
    };
  const status = $("#task-status");
  if (status)
    status.onchange = () => {
      S.taskStatus = status.value;
      render();
    };
  const taskSearch = $("#task-search");
  if (taskSearch) taskSearch.oninput = () => filterRows(taskSearch.value);
  const toolSearch = $("#tool-search");
  if (toolSearch)
    toolSearch.oninput = () => {
      S.toolQuery = toolSearch.value;
      renderToolCards();
    };
}
function renderDashboard() {
  return Flow.dashboard();
}
function renderProjects() {
  return (
    heading(
      "Projects",
      "Purpose, delivery, and the evidence behind every project.",
      programSelect() +
        toolButton(
          "create_program",
          icon("plus") + "New project",
          "",
          "primary",
        ),
    ) +
    `<div class="toolbar"><span class="muted">${programs().length} projects in this view</span><span class="spacer"></span>${button("Project actions", "catalogue", 'data-section="Projects"')}</div><div class="card-grid">${programs()
      .map(
        (p) =>
          `<article class="panel project-card"><div class="project-top"><span class="item-icon">${icon("folder")}</span>${badge(p.outlook, p.outlook === "At risk" ? "warning" : p.outlook === "On track" ? "success" : "muted")}</div><h3>${esc(p.name)}</h3><p>${esc(p.goal)}</p><div class="project-meta"><span>${p.evidence_count} source records</span><span>${p.artifacts.task_board?.tasks?.length || 0} tasks</span><span>${p.target_date ? esc(p.target_date) : "No target date"}</span></div><div class="action-row">${button("Open project", "project", `data-program="${esc(p.id)}"`)}${sourceButton(p.id)}</div></article>`,
      )
      .join(
        "",
      )}</div>${!programs().length ? panel("Your projects", empty("Give the work a clear outcome", "Start with purpose, success criteria, ownership, and scope.", toolButton("create_program", "Create a project"))) : ""}`
  );
}
function renderTasks() {
  const ts = tasks().filter((t) => !S.taskStatus || t.status === S.taskStatus);
  return (
    heading(
      "Tasks",
      "Source commitments and collected meeting actions, with their context attached.",
      programSelect() +
        toolButton(
          "create_board_task",
          icon("plus") + "Add exception task",
          S.program,
          "primary",
        ),
    ) +
    `<div class="toolbar"><input id="task-search" placeholder="Find a task or owner…" aria-label="Search tasks"><select id="task-status" aria-label="Filter task status"><option value="">All statuses</option>${["BACKLOG", "TODO", "IN_PROGRESS", "IN_REVIEW", "BLOCKED", "DONE", "CANCELLED"].map((v) => `<option value="${v}" ${v === S.taskStatus ? "selected" : ""}>${esc(titleCase(v))}</option>`).join("")}</select><span class="spacer"></span>${button("Sprints & workload", "catalogue", 'data-section="Tasks"')}</div><section class="panel table-wrap"><table class="data-table"><thead><tr><th>TASK</th><th>STATUS</th><th>PRIORITY</th><th>OWNER</th><th>DUE</th></tr></thead><tbody>${ts.map((t) => `<tr data-search="${esc((t.title + " " + t.assignee).toLowerCase())}"><td><button class="task-title" data-action="task" data-id="${esc(t.id)}" data-program="${esc(t.program_id)}">${esc(t.title)}</button><small>${esc(t.program_name)} · ${esc(t.id)}</small></td><td>${badge(titleCase(t.status), t.status === "BLOCKED" ? "warning" : t.status === "DONE" ? "success" : "")}</td><td>${badge(t.priority, t.priority === "HIGH" || t.priority === "CRITICAL" ? "warning" : "")}</td><td>${esc(t.assignee || "Unassigned")}</td><td>${esc(t.due_date || "Not set")}</td></tr>`).join("")}</tbody></table>${!ts.length ? empty("No tasks in this view", "Create a task or extract commitments from meeting notes.", toolButton("create_board_task", "Create a task", S.program)) : ""}</section><p id="task-no-results" class="empty" hidden>No matching tasks.</p>`
  );
}
function filterRows(query) {
  const rows = [...document.querySelectorAll("[data-search]")];
  rows.forEach(
    (r) => (r.hidden = !r.dataset.search.includes(query.toLowerCase())),
  );
  $("#task-no-results").hidden = rows.some((r) => !r.hidden);
}
function renderDecisions() {
  const ds = decisions();
  return (
    heading(
      "Decisions",
      "Make the call with context, alternatives, and authority.",
      programSelect() +
        toolButton(
          "propose_decision",
          icon("plus") + "Propose decision",
          S.program,
          "primary",
        ),
    ) +
    `<div class="toolbar"><span class="muted">${pendingDecisions().length} awaiting review</span><span class="spacer"></span>${button("Approvals & scope", "catalogue", 'data-section="Decisions"')}</div><div class="stack">${ds.map((d) => `<section class="panel panel-inner"><div class="list-row"><span class="item-icon ${d.status === "PROPOSED" ? "warning" : ""}">${icon(d.status === "DECIDED" ? "check" : "file")}</span><div class="grow"><div class="eyebrow">${esc(d.program_name)} · ${esc(d.id)}</div><h3>${esc(d.title)}</h3><p>${esc(compact(d.context))}</p><div class="action-row">${button("Review decision", "decision", `data-id="${esc(d.id)}" data-program="${esc(d.program_id)}"`)}${sourceButton(d.program_id)}</div></div>${badge(d.review ? titleCase(d.review.action) : titleCase(d.status), d.status === "DECIDED" ? "success" : "")}</div></section>`).join("") || panel("Decision log", empty("Keep a record of the reasoning", "Propose a question and its options before recording the decision.", toolButton("propose_decision", "Propose a decision", S.program)))}</div>`
  );
}
function renderIntelligence() {
  const ps = programs(),
    ev = S.data.evidence.filter(
      (e) => !S.program || e.project_ids.includes(S.program),
    );
  const arts = S.data.runtime_artifacts.filter(
    (a) =>
      !S.program ||
      (a.project_ids || a.payload?.project_ids || []).includes(S.program),
  );
  return (
    heading(
      "Intelligence",
      "Measurements, uncertainty, and the next useful intervention.",
      programSelect() +
        button(icon("play") + "Run analysis", "analyze", "", "primary"),
    ) + DecisionUI.panel() +
    `<div class="stat-grid"><div class="panel stat"><div class="eyebrow">Evidence available</div><div class="value">${ev.length}</div><div class="muted">Source observations</div></div><div class="panel stat"><div class="eyebrow">Analysis coverage</div><div class="value">${S.data.coverage.engine_backed.length} <span class="muted">engines</span></div><div class="muted">${S.data.coverage.derived.length} derived projections</div></div><div class="panel stat"><div class="eyebrow">Estimation method</div><div class="value" style="font-size:22px">Structural</div><div class="muted">Baseline · not a calibrated forecast</div></div></div><div class="notice">Probability, confidence, and impact are separate. Baseline scenarios describe structural possibilities. They do not establish a reliable likelihood.</div>${panel("Success criteria", ps.flatMap((p) => (p.success_metrics || []).map((m) => `<div class="list-row"><span class="item-icon">${icon("chart")}</span><div class="grow"><div class="eyebrow">${esc(p.name)}</div><h3>${esc(m.name)}</h3><p>Current: ${esc(m.current || "Unknown")} &nbsp; · &nbsp; Target: ${esc(m.target)}</p><p class="muted">${esc(m.measurement_method || "Measurement source and observation window not defined.")}</p></div>${sourceButton(p.id)}</div>`)).join("") || empty("Define what success means", "Add success metrics when you create a project. A target alone does not establish health."))}<div class="toolbar"><h2>Analysis outputs</h2><span class="spacer"></span>${button("Intelligence actions", "catalogue", 'data-section="Intelligence"')}</div><div class="card-grid">${arts.map((a) => `<article class="panel tool-card"><div class="eyebrow">${esc(a.artifact_id)}</div><h3>${esc(titleCase(a.artifact_type))}</h3><p>${a.current === false ? "Previous revision · not recomputed in the latest pass" : S.data.coverage.engine_backed.includes(a.artifact_type) ? "Dedicated engine output" : "Derived projection · full recommendation engine has not run for this output"}</p>${button("Inspect output", "artifact", `data-id="${esc(a.artifact_id)}" data-type="${esc(a.artifact_type)}"`)}</article>`).join("") || empty("No published analysis", "Add evidence and run analysis to build a coherent revision.")}</div>`
  );
}
function renderRelationships() {
  return Flow.relationships();
}
function renderMeetings() {
  return Flow.meetings();
}
function renderConnections() {
  return Flow.connections();
}
function renderHistory() {
  const activity = S.data.activity.filter(
    (a) => !S.program || a.program_id === S.program,
  );
  return (
    heading(
      "History",
      "What changed, what you decided, and what happened afterward.",
      programSelect() +
        button(icon("database") + "Evidence journal", "journal"),
    ) +
    `<div class="toolbar"><span class="muted">Browser actions and the durable evidence journal retain separate provenance.</span><span class="spacer"></span>${button("Record outcome", "outcome")}${button("Memory actions", "catalogue", 'data-section="History"')}</div><section class="panel timeline">${activity.map((a) => `<article class="timeline-item"><time>${dt(a.at)}</time><div><h3>${esc(a.title)}</h3><p>${esc(a.program_id || "Workspace")} · ${esc(a.actor)}</p><button class="text-button" data-action="activity" data-id="${esc(a.id)}">Inspect result</button></div></article>`).join("") || empty("Your decisions leave a trail", "Completed actions will appear here. Open the evidence journal to inspect source history.")}</section>`
  );
}
function renderSettings() {
  return (
    heading(
      "Settings",
      "Source access, standing authority, and execution accountability.",
      button("Advanced actions", "catalogue"),
    ) +
    Flow.settings() +
    panel(
      "Workspace",
      `<dl class="facts"><dt>Mode</dt><dd>${S.workspace === "demo" ? "Isolated source replay" : S.data.settings.hosted ? "Hosted workspace" : "Local workspace"}</dd><dt>Collection</dt><dd>${esc(S.data.settings.scheduler)}</dd><dt>Model</dt><dd>${esc(S.data.settings.model || "Not configured; deterministic inference")}</dd></dl><div class="action-row">${button("Switch workspace", "workspace")}${button(S.token ? "Sign out" : "Authenticate", "login")}</div>`,
    ) +
    panel(
      "Advanced workflow controls",
      `<p class="muted">${S.catalogue.tools.length} registered operations. Manual entry is available here for corrections, exceptional inputs, and debugging. External changes cannot bypass the scoped execution gateway.</p>${button("Explore action catalogue", "catalogue")}`,
    )
  );
}
function openDrawer(title, eyebrow, html) {
  $("#drawer-title").textContent = title;
  $("#drawer-eyebrow").textContent = eyebrow;
  $("#drawer-body").innerHTML = html;
  $("#drawer").scrollTop = 0;
  if (!$("#drawer").open) $("#drawer").showModal();
}
function evidenceCards(records) {
  return (
    records
      .map(
        (e) =>
          `<article class="evidence-card"><div class="evidence-top"><div class="eyebrow">${esc(e.source_system)} · ${esc(e.source_id)}</div>${badge("Source observation")}</div><h3>${esc(e.title || titleCase(e.event_type))}</h3><blockquote>${esc(e.normalized_content || "Source content was not retained. Historical provenance remains available.")}</blockquote><div class="times"><span>Event happened<strong>${dt(e.observed_at)}</strong></span><span>TPLLM learned it<strong>${dt(e.received_at)}</strong></span></div><div class="tags">${e.project_ids.map((p) => `<span class="tag">${esc(titleCase(p))}</span>`).join("")}</div>${safeSourceUrl(e.source_pointer) ? `<div class="action-row"><a href="${esc(e.source_pointer)}" target="_blank" rel="noopener noreferrer">Open source ${icon("arrow")}</a></div>` : ""}</article>`,
      )
      .join("") ||
    empty(
      "No supporting evidence",
      "Add a source before treating this conclusion as supported.",
    )
  );
}
function safeSourceUrl(value) {
  try {
    const u = new URL(value);
    return ["https:", "http:"].includes(u.protocol);
  } catch {
    return false;
  }
}
function openEvidence(pid = "", id = "") {
  const records = S.data.evidence.filter(
    (e) =>
      (!pid || e.project_ids.includes(pid)) && (!id || e.evidence_id === id),
  );
  openDrawer(
    "Evidence",
    "Sources & provenance",
    `<p class="drawer-intro">These are the recorded observations. Conflicting observations remain visible; arrival order does not establish authority.</p>${evidenceCards(records)}`,
  );
}
function openProject(pid) {
  const p = S.data.programs.find((p) => p.id === pid);
  if (!p) return;
  openDrawer(
    p.name,
    "Project overview",
    `<p class="drawer-intro">${esc(p.goal)}</p><dl class="facts"><dt>Outlook</dt><dd>${esc(p.outlook)} · ${esc(p.outlook_basis)}</dd><dt>Target date</dt><dd>${esc(p.target_date || "Not set")}</dd><dt>Decision authority</dt><dd>${esc(p.setup.authority || "Not configured")}</dd><dt>Non-goals</dt><dd>${esc(p.non_goals.join("; ") || "Not defined")}</dd><dt>Identity mapping</dt><dd>Program ${esc(p.id)} ↔ runtime project ${esc(p.runtime_project_id)}</dd></dl><h3 class="subheading">Success criteria</h3>${(p.success_metrics || []).map((m) => `<div class="list-row"><div class="grow"><h3>${esc(m.name)}</h3><p>${esc(m.current || "Unknown")} current · ${esc(m.target)} target</p></div></div>`).join("") || '<p class="muted">No success criteria have been defined.</p>'}<h3 class="subheading">Milestones</h3>${(p.artifacts.milestone_plan?.milestones || []).map((m) => `<div class="list-row"><div class="grow"><h3>${esc(m.name)}</h3><p>${esc(m.owner)} · ${esc(m.target_date || "Date not set")} · ${esc(titleCase(m.status))}</p><p>${esc(m.description || "")}</p></div></div>`).join("") || '<p class="muted">No milestones yet.</p>'}<h3 class="subheading">Dependencies</h3>${(p.artifacts.raid_log?.dependencies || []).map((d) => `<div class="list-row"><div class="grow"><h3>${esc(d.title)}</h3><p>${esc(d.upstream_team)} → ${esc(d.downstream_team)} · ${esc(d.owner)}</p></div></div>`).join("") || '<p class="muted">No declared dependencies.</p>'}<h3 class="subheading">Risks & issues</h3>${[...(p.artifacts.raid_log?.risks || []), ...(p.artifacts.raid_log?.issues || [])].map((r) => `<div class="list-row"><div class="grow"><h3>${esc(r.title)}</h3><p>${esc(r.description || r.impact || "")}</p></div>${badge(r.severity, "warning")}</div>`).join("") || '<p class="muted">No recorded risks or issues. This does not establish an absence of risk.</p>'}<div class="drawer-actions">${button("Edit project", "edit-project", `data-program="${esc(pid)}"`)}${sourceButton(pid)}${toolButton("add_milestone", "Add milestone", pid)}${toolButton("add_risk", "Add risk", pid)}${toolButton("add_scope_item", "Manage scope", pid)}</div>`,
  );
}
function openTask(pid, id) {
  const t = S.data.programs
    .find((p) => p.id === pid)
    ?.artifacts.task_board?.tasks.find((t) => t.id === id);
  if (!t) return;
  openDrawer(
    t.title,
    "Task · " + id,
    `<p class="drawer-intro">${esc(t.description || "No description has been added.")}</p><div class="action-row">${badge(titleCase(t.status), t.status === "BLOCKED" ? "warning" : "")}${badge(t.priority)}</div><h3 class="subheading">Why this task exists</h3><p class="description">${esc(t.source_context || "No originating passage recorded.")}</p><dl class="facts section-space"><dt>Source</dt><dd>${esc(t.source === "ZOOM_AI_NOTES" ? "Meeting notes" : titleCase(t.source))} · ${esc(t.source_id || "Created directly")}</dd><dt>Confidence</dt><dd>${t.source === "MANUAL" ? "Manual entry" : Math.round(t.confidence * 100) + "% extraction confidence"}${t.confidence < 0.8 ? " · Review this commitment" : ""}</dd><dt>Priority rationale</dt><dd>${esc(t.priority_rationale || "No rationale recorded")}</dd><dt>Blocked by</dt><dd>${esc(t.blocked_by.join(", ") || "No recorded blockers")}</dd><dt>Finishing enables</dt><dd>${esc(t.blocks.join(", ") || "No downstream tasks linked")}</dd><dt>Reviewer</dt><dd>${esc(t.reviewer || "Not assigned")}</dd></dl><h3 class="subheading">Progress the work</h3><form id="task-form" class="form-stack"><label>Task title<input name="title" required value="${esc(t.title)}"></label><label>Description<textarea name="description">${esc(t.description)}</textarea></label><div class="field-grid"><label>Status<select name="status">${["BACKLOG", "TODO", "IN_PROGRESS", "IN_REVIEW", "BLOCKED", "DONE", "CANCELLED"].map((v) => `<option ${v === t.status ? "selected" : ""} value="${v}">${titleCase(v)}</option>`).join("")}</select></label><label>Owner<input name="assignee" value="${esc(t.assignee)}"></label><label>Due date<input type="date" name="due_date" value="${esc(t.due_date || "")}"></label><label>Priority<select name="priority">${["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((v) => `<option ${v === t.priority ? "selected" : ""}>${v}</option>`).join("")}</select></label></div><label>Blocking task IDs<input name="blocked_by" value="${esc(t.blocked_by.join(", "))}" placeholder="TASK-123, TASK-456"><small>Comma-separated task identifiers. Clear this field when blockers are resolved.</small></label><label>Reviewer<input name="reviewer" value="${esc(t.reviewer)}"></label>${t.source_id ? `<label class="checkbox-label"><input type="checkbox" name="review_confirmed" ${t.review ? "checked" : ""}> I reviewed the commitment, owner, and date.</label>${t.review ? `<p class="muted">Reviewed by ${esc(t.review.actor)} · ${dt(t.review.at)}</p>` : ""}` : ""}<div class="drawer-actions"><button class="button primary" type="submit">Save task</button>${sourceButton(pid)}</div></form>`,
  );
  $("#task-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const fd = new FormData(e.target);
      const arguments_ = {
        program_id: pid,
        task_id: id,
        title: fd.get("title"),
        description: fd.get("description"),
        reviewer: fd.get("reviewer"),
        review_confirmed: fd.get("review_confirmed") === "on",
        status: fd.get("status"),
        priority: fd.get("priority"),
        assignee: fd.get("assignee"),
        due_date: fd.get("due_date"),
        blocked_by: String(fd.get("blocked_by"))
          .split(",")
          .map((x) => x.trim())
          .filter(Boolean),
      };
      await api("/actions/update_board_task", { arguments: arguments_ });
      await refresh();
      openTask(pid, id);
      toast("Task saved. Its source and reasoning are retained.");
    });
  };
}
async function openDecision(pid, id) {
  const detail = await api(
    `/decisions/${encodeURIComponent(id)}?program_id=${encodeURIComponent(pid)}`,
  );
  const d = detail.decision,
    closed = d.status === "DECIDED" || detail.review?.action === "reject";
  openDrawer(
    d.title,
    "Decision review · " + id,
    `<p class="drawer-intro">${esc(d.context || "No context recorded.")}</p><dl class="facts"><dt>Decision owner</dt><dd>${esc(detail.authority || "Not configured · finalization unavailable")}</dd><dt>Current state</dt><dd>${esc(detail.review ? titleCase(detail.review.action) : titleCase(d.status))}</dd><dt>Recommendation</dt><dd>${esc(detail.meta.recommendation || "No engine recommendation attached")}</dd><dt>Provenance</dt><dd>${esc(detail.meta.basis || "Program decision log · human-authored options")}</dd></dl>${detail.review ? `<div class="notice">${esc(detail.review.actor)} · ${esc(detail.review.rationale)}<br>${dt(detail.review.at)}</div>` : ""}<form id="decision-form"><h3 class="subheading">Options & tradeoffs</h3>${d.options.map((o, i) => `<label class="option-card"><span class="option-title"><input type="radio" name="option" value="${esc(o.label)}" ${i === 0 ? "checked" : ""} ${closed ? "disabled" : ""}>${esc(o.label)}</span><div class="tradeoff">${o.pros.map((p) => `<div>+ ${esc(p)}</div>`).join("")}${o.cons.map((c) => `<div class="con">− ${esc(c)}</div>`).join("")}</div></label>`).join("")}<h3 class="subheading">What supports the decision</h3>${evidenceCards(detail.evidence)}${!closed ? `<h3 class="subheading">Your review</h3><div class="form-stack"><label>Rationale or missing information<textarea name="rationale" required placeholder="Explain the choice, or what needs to be researched before deciding."></textarea></label><label>Modified option (only for Modify)<input name="modified" placeholder="Describe the option you want to use"></label><label>Effective date (required for Modify)<input type="datetime-local" name="effective_at"></label></div><div class="notice">Validate or Modify records a conclusive program decision. Reject closes this proposal. Defer retains the open question and records the missing information. No message is sent externally.</div><div class="drawer-actions"><button type="submit" class="button primary" value="validate">Validate</button><button type="submit" class="button" value="modify">Modify</button><button type="submit" class="button danger" value="reject">Reject</button><button type="submit" class="button" value="defer">Defer</button></div>` : `<div class="notice">Recorded conclusion: ${esc(d.decision || "Proposal rejected")}<br>${esc(d.rationale || detail.review?.rationale || "")}</div>`}</form>`,
  );
  $("#decision-form").onsubmit = (e) => {
    e.preventDefault();
    const action = e.submitter.value;
    busy(e.submitter, async () => {
      const fd = new FormData(e.target);
      await api(`/decisions/${encodeURIComponent(id)}/review`, {
        program_id: pid,
        action,
        option:
          action === "modify" ? fd.get("modified") : fd.get("option") || "",
        rationale: fd.get("rationale"),
        effective_at: fd.get("effective_at")
          ? new Date(fd.get("effective_at")).toISOString()
          : null,
      });
      await refresh();
      await openDecision(pid, id);
      toast(
        action === "defer"
          ? "Decision deferred. Missing information recorded."
          : "Decision review saved.",
      );
    });
  };
}
function fieldHtml(name, spec, value, required) {
  const label = titleCase(name);
  let input = "";
  const common = `name="${esc(name)}" ${required ? "required" : ""}`;
  if (name === "program_id")
    input = `<select ${common}>${programOptions(value || S.program)}</select>`;
  else if (spec.enum)
    input = `<select ${common}>${!required ? '<option value="">Use default</option>' : ""}${spec.enum.map((v) => `<option value="${esc(v)}" ${v === value ? "selected" : ""}>${esc(titleCase(v))}</option>`).join("")}</select>`;
  else if (spec.type === "boolean")
    input = `<select ${common}><option value="false">No</option><option value="true">Yes</option></select>`;
  else if (["array", "object"].includes(spec.type))
    input = `<textarea ${common} data-json="true" placeholder="${esc(spec.type === "array" ? "[]" : "{}")}">${value ? esc(JSON.stringify(value, null, 2)) : ""}</textarea>`;
  else if (
    /description|notes|context|rationale|content|goal|text|summary/.test(name)
  )
    input = `<textarea ${common} placeholder="${esc(spec.description || label)}">${esc(value || "")}</textarea>`;
  else
    input = `<input ${common} type="${spec.type === "number" || spec.type === "integer" ? "number" : name.includes("date") ? "date" : "text"}" ${spec.type === "number" ? 'step="any"' : ""} value="${esc(value ?? "")}" placeholder="${esc(spec.description || label)}">`;
  return `<label>${esc(label)} ${required ? '<span class="required">REQUIRED</span>' : ""}${input}${spec.type === "array" || spec.type === "object" ? `<small>Structured JSON · ${esc(JSON.stringify(spec.items?.properties || spec.items || spec.properties || {}))}</small>` : spec.description ? `<small>${esc(spec.description)}</small>` : ""}</label>`;
}
function openTool(name, pid = "", defaults = {}, advanced = false) {
  if (!advanced && name === "create_program") return openNewProject();
  if (!advanced && name === "propose_decision") return openProposal(pid);
  if (!advanced && name === "create_board_task") return openNewTask(pid);
  const t = S.catalogue.tools.find((t) => t.name === name);
  if (!t) {
    toast("This action needs a configured integration.", true);
    return;
  }
  openDrawer(
    titleCase(name),
    t.section + " · " + titleCase(t.group),
    `<p class="drawer-intro">${esc(t.description)}</p>${
      t.blocked_reason
        ? `<div class="notice warning">${esc(t.blocked_reason)}</div>`
        : `<form id="tool-form" class="form-stack">${Object.entries(
            t.input_schema.properties || {},
          )
            .map(([n, s]) =>
              fieldHtml(
                n,
                s,
                defaults[n] ?? (n === "program_id" ? pid || S.program : ""),
                (t.input_schema.required || []).includes(n),
              ),
            )
            .join(
              "",
            )}${t.external ? '<div class="notice warning">This action calls a configured external service. Review the destination and all fields before confirming.</div><label><input type="checkbox" name="confirm_external" required> I confirm this external action and destination.</label>' : ""}<div class="drawer-actions"><button type="submit" class="button primary">${t.external ? "Confirm & run" : "Run action"}</button></div></form>`
    }`,
  );
  const form = $("#tool-form");
  if (form)
    form.onsubmit = (e) => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const fd = new FormData(form),
          args = {};
        for (const [n, s] of Object.entries(t.input_schema.properties || {})) {
          const value = fd.get(n);
          if (value === null || value === "") continue;
          args[n] = ["array", "object"].includes(s.type)
            ? JSON.parse(value)
            : s.type === "number" || s.type === "integer"
              ? Number(value)
              : s.type === "boolean"
                ? value === "true"
                : value;
        }
        const result = await api("/actions/" + name, {
          arguments: args,
          confirm_external: fd.get("confirm_external") === "on",
        });
        await refresh();
        openResult(titleCase(name), result.result, args.program_id);
      });
    };
}
function openResult(title, result, pid = "") {
  const text =
    typeof result === "string" ? result : JSON.stringify(result, null, 2);
  openDrawer(
    title,
    "Action result",
    `<div class="notice">The workflow returned the following result. A routed or proposed action is not proof of external delivery.</div><pre class="markdown-output">${esc(text)}</pre><div class="drawer-actions">${button(icon("download") + "Download result", "download")}${pid ? button("Open project", "project", `data-program="${esc(pid)}"`) : ""}${button("View history", "navigate", 'data-page="history"')}</div>`,
  );
  S.download = {
    name: title.toLowerCase().replace(/[^a-z0-9]+/g, "-") + ".txt",
    text,
  };
}
function openAnalysisResult(result) {
  const complete = result.complete && result.published;
  const concerns = [
    ...(result.evidence_limitations || []),
    ...(result.warnings || []),
  ];
  openDrawer(
    complete ? "Analysis complete" : "Analysis needs attention",
    "Evidence reviewed",
    `<p class="drawer-intro">${complete ? "The evidence journal has been replayed and the analysis outputs updated." : "The analysis did not publish a complete result. Review the details before relying on it."}</p><dl class="facts"><dt>Records reviewed</dt><dd>${esc(result.records_replayed)}</dd><dt>Analysis state</dt><dd>${esc(result.reconstructed_revision_id || "Not published")}</dd><dt>External actions</dt><dd>None performed by analysis</dd></dl>${concerns.length ? `<div class="notice section-space">${concerns.map(esc).join("<br>")}</div>` : ""}<div class="notice section-space">Inspect the output's evidence, estimation method, and confidence before making a decision.</div><div class="drawer-actions">${button("Explore analysis outputs", "navigate", 'data-page="intelligence"')}${button("View history", "navigate", 'data-page="history"')}</div><details class="section-space"><summary class="text-button">Analysis details</summary><pre class="code-detail">${esc(JSON.stringify(result, null, 2))}</pre></details>`,
  );
}
function openCatalogue(section = "") {
  S.catalogueSection = section;
  S.toolQuery = "";
  openDrawer(
    section ? section + " actions" : "Action catalogue",
    "Connected Python workflows",
    `<p class="drawer-intro">Every available action runs the existing TPLLM workflow. Configuration and authority limits are shown before execution.</p><input class="search-field" id="tool-search" placeholder="Find an action…" aria-label="Search actions"><div class="section-space" id="tool-cards"></div>`,
  );
  $("#tool-search").oninput = (e) => {
    S.toolQuery = e.target.value;
    renderToolCards();
  };
  renderToolCards();
}
function renderToolCards() {
  const list = S.catalogue.tools.filter(
    (t) =>
      (!S.catalogueSection || t.section === S.catalogueSection) &&
      (t.name + " " + t.description)
        .toLowerCase()
        .includes(S.toolQuery.toLowerCase()),
  );
  $("#tool-cards").innerHTML =
    list
      .map(
        (t) =>
          `<div class="list-row"><div class="grow"><div class="eyebrow">${esc(titleCase(t.group))} ${t.external ? "· External service" : ""}</div><h3>${esc(titleCase(t.name))}</h3><p>${esc(compact(t.description))}</p>${t.blocked_reason ? `<p class="small-label">${esc(t.blocked_reason)}</p>` : ""}</div>${button(t.blocked_reason ? "Details" : "Open", "tool", `data-tool="${esc(t.name)}"`)}</div>`,
      )
      .join("") +
    S.catalogue.groups
      .filter(
        (g) =>
          !g.available &&
          (!S.catalogueSection || g.section === S.catalogueSection),
      )
      .map(
        (g) =>
          `<div class="list-row"><div class="grow"><h3>${esc(titleCase(g.id))}</h3><p>${esc(g.reason)}</p></div>${badge("Setup required")}</div>`,
      )
      .join("");
}
function openEvidenceForm() {
  openDrawer(
    "Add evidence",
    "Source intake",
    `<p class="drawer-intro">Keep the event time and source identity. Evidence is journaled before analysis, and repeated source events are deduplicated.</p><form id="evidence-form" class="form-stack"><label>Project<select name="program_id" required>${programOptions(S.program)}</select></label><label>Title<input name="title" required placeholder="What did the source tell us?"></label><div class="field-grid"><label>Source system<select name="source_system"><option>manual</option><option>meeting</option><option>github</option><option>slack</option><option>jira</option></select></label><label>Source record ID<input name="source_id" required placeholder="brief-v1, ISSUE-42, meeting-01"></label></div><label>When it happened<input name="observed_at" type="datetime-local" value="${dateInput()}" required><small>Your local time; stored as UTC.</small></label><label>Observation type<select name="event_type"><option value="architecture_evidence">Project / architecture evidence</option><option value="blocker_opened">Blocker opened</option><option value="blocker_resolved">Blocker resolved</option><option value="meeting_transcript">Meeting transcript</option><option value="chat_message">Message</option></select></label><label>Source passage<textarea name="content" required rows="7" placeholder="Paste the actual observation, preserving disagreements and uncertainty."></textarea></label><div class="drawer-actions"><button class="button primary" type="submit">Add & analyze evidence</button></div></form>`,
  );
  $("#evidence-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const fd = new FormData(e.target),
        body = Object.fromEntries(fd);
      body.observed_at = new Date(body.observed_at).toISOString();
      const result = await api("/evidence", body);
      await refresh();
      openResult("Evidence intake", result, body.program_id);
    });
  };
}
function openMeetingIntake() {
  openDrawer(
    "Add meeting notes",
    "Capture → review → follow through",
    `<p class="drawer-intro">The existing extractor creates source-linked tasks immediately. Inspect its confidence and originating passage before progressing each commitment.</p><form id="meeting-form" class="form-stack"><label>Project<select name="program_id" required>${programOptions(S.program)}</select></label><label>Meeting topic<input name="meeting_topic" required placeholder="Launch review"></label><label>Source meeting ID<input name="meeting_id" required placeholder="launch-review-2026-10-27"></label><label>Original notes<textarea name="raw_notes" required rows="8" placeholder="Alex will review the contract by Friday.&#10;Sam to verify the cloud serving bundle."></textarea></label><div class="drawer-actions"><button class="button primary" type="submit">Extract commitments</button></div></form>`,
  );
  $("#meeting-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const args = Object.fromEntries(new FormData(e.target));
      const result = await api("/actions/extract_tasks_from_meeting", {
        arguments: args,
      });
      await refresh();
      const created = result.result.tasks || [];
      openDrawer(
        "Review extracted commitments",
        "Meeting intake result",
        `<div class="notice">${created.length} task${created.length === 1 ? " was" : "s were"} created by the existing extractor. Review attribution and confidence before changing status.</div>${created.map((t) => `<div class="list-row"><div class="grow"><h3>${esc(t.title)}</h3><p>${esc(t.assignee || "Unassigned")} · ${Math.round(t.confidence * 100)}% confidence</p></div>${button("Review task", "task", `data-id="${esc(t.id)}" data-program="${esc(args.program_id)}"`)}</div>`).join("") || '<p class="muted">No commitments were detected. Add explicit action items through the advanced intake action.</p>'}<div class="drawer-actions">${button("Open task board", "navigate", 'data-page="tasks"')}${toolButton("extract_tasks_from_meeting", "Advanced intake", args.program_id)}</div>`,
      );
    });
  };
}
function openConnection(id) {
  const c = S.data.connections.find((c) => c.id === id);
  openDrawer(
    titleCase(id),
    "Connection scope & capability",
    `<div class="notice">${esc(c.status)}. Saving a mapping does not fetch evidence, verify access, or enable external delivery.</div><dl class="facts"><dt>Read capability</dt><dd>${esc(c.descriptor?.capabilities.join(", ").replaceAll("_", " ") || "Application tool interface")}</dd><dt>Verification</dt><dd>Not verified live in this workspace</dd><dt>Write capability</dt><dd>${esc(c.write)}</dd></dl>${c.descriptor ? `<h3 class="subheading">Implementation provenance</h3><p class="muted">${esc(c.descriptor.notes)}</p>` : ""}<h3 class="subheading">Map a source to a project</h3><form id="connection-form" class="form-stack"><label>Source scope<input name="scope" required value="${esc(c.mapping.scope || "")}" placeholder="${esc(c.descriptor?.scope_label || "Channel, project, or destination")}"></label><label>Project<select name="program_id" required>${programOptions(c.mapping.program_id || S.program)}</select></label><div class="drawer-actions"><button class="button primary" type="submit">Save mapping</button>${button("Integration actions", "catalogue", 'data-section="Connections"')}${id === "github" && c.configured ? button("Read mapped repository", "github-read") : ""}</div></form><div class="notice">Configure credentials with <code>tpllm init</code>. Use <code>tpllm integrations ingest --help</code> for live evidence reads. The browser imports supplied observations; provider reads require a configured integration and explicit confirmation.</div>`,
  );
  $("#connection-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const result = await api("/connections/" + id, {
        arguments: Object.fromEntries(new FormData(e.target)),
      });
      await refresh();
      toast(result.status);
      openConnection(id);
    });
  };
}
function openSearch(ask = false) {
  openDrawer(
    ask ? "Ask your evidence" : "Search workspace",
    ask
      ? "Evidence lookup · no model inference"
      : "Projects · tasks · decisions · evidence",
    `<p class="drawer-intro">${ask ? "Search what your sources actually say. Results quote stored evidence; a generated answer is not substituted for missing context." : "Find a source or jump straight to the work."}</p><input class="search-field" id="global-search" placeholder="${ask ? "What is blocking the launch?" : "Search your workspace…"}" aria-label="Search workspace">${ask ? `<div class="notice">${S.data.settings.model_available ? "A configured model is available. You can explicitly send workspace context for a read-only answer." : "No model is configured for this workspace. Evidence lookup works locally."}</div>${S.data.settings.model_available ? button("Ask the configured model", "ask-model") : ""}` : ""}<div id="search-results" class="section-space"></div>`,
  );
  $("#global-search").oninput = (e) => search(e.target.value, ask);
  $("#global-search").focus();
  search("", ask);
}
function search(query, ask) {
  const words = query
    .toLowerCase()
    .split(/\W+/)
    .filter(
      (w) =>
        w.length > 2 &&
        ![
          "what",
          "the",
          "does",
          "why",
          "are",
          "and",
          "this",
          "for",
          "with",
        ].includes(w),
    );
  let records = S.data.evidence.map((e) => ({
    kind: "Evidence",
    title: e.title || e.source_id,
    text: e.normalized_content || "",
    action: "evidence",
    id: e.evidence_id,
    program: e.project_ids[0],
  }));
  if (!ask)
    records = [
      ...S.data.programs.map((p) => ({
        kind: "Project",
        title: p.name,
        text: p.goal,
        action: "project",
        program: p.id,
      })),
      ...tasks().map((t) => ({
        kind: "Task",
        title: t.title,
        text: t.assignee,
        action: "task",
        id: t.id,
        program: t.program_id,
      })),
      ...decisions().map((d) => ({
        kind: "Decision",
        title: d.title,
        text: d.context,
        action: "decision",
        id: d.id,
        program: d.program_id,
      })),
      ...records,
    ];
  records = records
    .map((r) => ({
      ...r,
      score: words.filter((w) =>
        (r.title + " " + r.text).toLowerCase().includes(w),
      ).length,
    }))
    .filter((r) => !words.length || r.score)
    .sort((a, b) => b.score - a.score);
  $("#search-results").innerHTML =
    records
      .slice(0, 25)
      .map(
        (r) =>
          `<button class="search-result" data-action="${r.action}" data-program="${esc(r.program || "")}" data-id="${esc(r.id || "")}" ${r.kind === "Evidence" ? `data-evidence="${esc(r.id)}"` : ""}>${icon(r.kind === "Project" ? "folder" : r.kind === "Task" ? "tasks" : "file")}<span><strong>${esc(r.title)}</strong><small>${esc(r.kind)} · ${esc(compact(r.text))}</small></span>${icon("chevron")}</button>`,
      )
      .join("") ||
    empty(
      "No matching evidence",
      "Try a source title or key term. Missing evidence stays unknown.",
    );
}
function openWorkspace() {
  openDrawer(
    "Choose your workspace",
    "Local product testing",
    `<div class="stack">${panel("My workspace", '<p class="muted">Use your configured artifact store and durable runtime. Live writes require an authenticated session.</p><div class="action-row">' + button("Open my workspace", "live", "", "primary") + "</div>")}${panel("AI visual search demo", '<p class="muted">A cloud-hosted website feature that recognizes objects in photos and respects the user’s request. Follow the model, data packages, eval gates and cloud deployment through connected evidence. Isolated storage; no provider credentials.</p><div class="action-row">' + button("Explore demo workspace", "demo", "", "primary") + button("Restart demo", "restart-demo") + "</div>")}</div>`,
  );
}
function openLogin() {
  if (S.token) {
    S.token = "";
    render();
    toast("Signed out.");
    return;
  }
  openDrawer(
    "Authenticate",
    "Local workspace access",
    `<p class="drawer-intro">Create a dashboard key with: tpllm dashboard-key ${S.data.settings.hosted ? "--hosted " : ""}--owner your-name. Then enter it here. Your key is exchanged for a short-lived token; this page keeps that token only in memory.</p><form id="login-form" class="form-stack"><label>API key<input type="password" name="api_key" autocomplete="off" required placeholder="tpllm_…"></label><div class="drawer-actions"><button class="button primary" type="submit">Authenticate</button></div></form><div class="notice">For product testing without credentials, use the isolated demo workspace.</div>${button("Explore demo workspace", "demo")}`,
  );
  $("#login-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const response = await fetch("/api/auth/token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(e.target))),
      });
      const r = await response.json();
      e.target.reset();
      if (!response.ok) throw new Error(r.detail || "Authentication failed");
      S.token = r.access_token;
      $("#drawer").close();
      render();
      toast("Authenticated. Workspace permissions are enforced by the server.");
    });
  };
}
async function handle(action, el) {
  if (action.startsWith("flow-")) return Flow.handle(action, el);
  const d = el.dataset;
  switch (action) {
    case "navigate":
      $("#drawer").close();
      location.hash = d.page;
      break;
    case "workspace":
      openWorkspace();
      break;
    case "login":
      openLogin();
      break;
    case "restart-demo":
      openDrawer(
        "Restart the demo",
        "A fresh test run",
        `<p class="drawer-intro">The current demo will be archived locally so it can be recovered. A fresh set of sample projects, evidence, and review items will replace it. Your live workspace is unaffected.</p>${button("Archive & restart demo", "confirm-restart", "", "primary")}`,
      );
      break;
    case "confirm-restart":
      await busy(el, async () => {
        await api("/demo/restart", {});
        S.workspace = "demo";
        S.program = "";
        localStorage.setItem("tpllm-workspace", "demo");
        await refresh();
        $("#drawer").close();
        toast("Fresh demo ready. Previous run archived locally.");
      });
      break;
    case "demo":
      await busy(el, async () => {
        await api("/demo", {});
        S.workspace = "demo";
        S.program = "";
        localStorage.setItem("tpllm-workspace", "demo");
        await refresh();
        $("#drawer").close();
        toast(
          "Demo workspace ready. Changes are isolated from your live data.",
        );
      });
      break;
    case "live":
      S.workspace = "live";
      S.program = "";
      localStorage.setItem("tpllm-workspace", "live");
      await refresh();
      $("#drawer").close();
      break;
    case "evidence":
      openEvidence(d.program || "", d.evidence || "");
      break;
    case "project":
      openProject(d.program);
      break;
    case "edit-project":
      openProjectEdit(d.program);
      break;
    case "outcome":
      openOutcome();
      break;
    case "task":
      openTask(d.program, d.id);
      break;
    case "decision":
      await openDecision(d.program, d.id);
      break;
    case "bounded-trace":
      await DecisionUI.open(d.id);
      break;
    case "bounded-label":
      DecisionUI.label(d.id);
      break;
    case "advanced-tool":
      openTool(d.tool, d.program || "", {}, true);
      break;
    case "tool":
      openTool(d.tool, d.program || "");
      break;
    case "catalogue":
      openCatalogue(d.section || "");
      break;
    case "add-evidence":
      openEvidenceForm();
      break;
    case "meeting-intake":
      openMeetingIntake();
      break;
    case "connection":
      openConnection(d.id);
      break;
    case "github-read":
      openGithubRead();
      break;
    case "ask-model":
      openModelAsk();
      break;
    case "analyze":
      await busy(el, async () => {
        const r = await api("/analyze", {});
        await refresh();
        openAnalysisResult(r);
      });
      break;
    case "answer":
      await busy(el, async () => {
        const r = await api("/clarifications/" + d.id, {
          arguments: { answer: d.answer },
        });
        await refresh();
        if ($("#drawer").open) $("#drawer").close();
        toast(
          r.status === "resolved"
            ? "Target confirmed. Evidence and program metric updated."
            : "Uncertainty retained. Research is still needed.",
        );
      });
      break;
    case "review-queue":
      openDrawer(
        "Needs your review",
        "Human input",
        pendingDecisions()
          .map(
            (d) =>
              `<div class="list-row"><span class="item-icon warning">${icon("warning")}</span><div class="grow"><h3>${esc(d.title)}</h3><p>${esc(d.program_name)}</p></div>${button("Review", "decision", `data-id="${esc(d.id)}" data-program="${esc(d.program_id)}"`)}</div>`,
          )
          .join("") +
          questions()
            .map(
              (q) =>
                `<div class="list-row"><div class="grow"><h3>${esc(q.title)}</h3><p>${esc(q.found)}</p><div class="action-row">${q.options.map((o) => button(esc(o), "answer", `data-id="${esc(q.id)}" data-answer="${esc(o)}"`)).join("")}</div></div></div>`,
            )
            .join("") ||
          empty(
            "Nothing needs your review",
            "Open decisions and factual clarifications appear here.",
          ),
      );
      break;
    case "explanation": {
      const related = S.data.runtime_artifacts.filter((a) =>
        (a.project_ids || a.payload?.project_ids || []).includes(d.program),
      );
      openDrawer(
        "Why this changed",
        "Evidence → interpretation → impact",
        `<div class="notice">These sources were ingested into the durable journal. The current runtime revision is ${esc(S.data.revision || "not published")}. Detailed engine outputs remain labeled by their derivation method.</div>${evidenceCards(S.data.evidence.filter((e) => e.project_ids.includes(d.program)))}<h3 class="subheading">Related analysis</h3>${related.map((a) => `<div class="list-row"><div class="grow"><h3>${esc(titleCase(a.artifact_type))}</h3><p>${S.data.coverage.engine_backed.includes(a.artifact_type) ? "Dedicated engine" : "Derived projection"}</p></div>${button("Inspect", "artifact", `data-id="${esc(a.artifact_id)}" data-type="${esc(a.artifact_type)}"`)}</div>`).join("")}`,
      );
      break;
    }
    case "artifact": {
      const a = S.data.runtime_artifacts.find(
        (a) => a.artifact_id === d.id && a.artifact_type === d.type,
      );
      if (a)
        openDrawer(
          titleCase(a.artifact_type),
          "Runtime output · " + a.revision_id,
          `<div class="notice">${S.data.coverage.engine_backed.includes(a.artifact_type) ? "Computed by the dedicated engine." : "Derived projection; the complete recommendation engine did not run."} Structural baseline estimates retain their confidence and method.</div><p class="drawer-intro">${esc(a.payload.after_summary || "Persisted runtime state reference.")}</p>${
            a.payload.outcome_probabilities
              ? Object.entries(a.payload.outcome_probabilities)
                  .map(
                    ([name, p]) =>
                      `<div class="list-row"><div class="grow"><h3>${esc(titleCase(name))}</h3><p>Probability ${(p * 100).toFixed(0)}% · Confidence ${((a.payload.probability_confidence || 0) * 100).toFixed(0)}% · structural baseline</p></div></div>`,
                  )
                  .join("")
              : ""
          }<details><summary class="text-button">Inspect full engine output</summary><pre class="code-detail">${esc(JSON.stringify(a.payload, null, 2))}</pre></details>`,
        );
      break;
    }
    case "journal": {
      const data = await api("/history");
      openDrawer(
        "Evidence journal",
        "Immutable source and decision history",
        data.records
          .slice()
          .reverse()
          .map(
            (r) =>
              `<article class="evidence-card"><div class="eyebrow">#${r.position.global_sequence} · ${esc(r.category)}</div><h3>${esc(titleCase(r.event_type))}</h3><div class="times section-space"><span>Occurred<strong>${dt(r.occurred_at)}</strong></span><span>Recorded<strong>${dt(r.recorded_at)}</strong></span></div><details><summary class="text-button">Inspect record</summary><pre class="code-detail">${esc(JSON.stringify(r.payload, null, 2))}</pre></details></article>`,
          )
          .join("") ||
          empty(
            "No journal records",
            "Source ingestion and decision reviews create durable records.",
          ),
      );
      break;
    }
    case "activity": {
      const a = S.data.activity.find((a) => a.id === d.id);
      if (a) openResult(a.title, a.detail, a.program_id);
      break;
    }
    case "download": {
      if (!S.download) break;
      const blob = new Blob([S.download.text], {
        type: "text/plain;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = S.download.name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      break;
    }
  }
}
function openProjectEdit(pid) {
  const p = S.data.programs.find((p) => p.id === pid);
  openDrawer(
    "Edit " + p.name,
    "Project definition",
    `<form id="project-edit-form" class="form-stack"><label>Name<input name="name" required value="${esc(p.name)}"></label><label>Intended outcome<textarea name="goal" required>${esc(p.goal)}</textarea></label><label>Non-goals (one per line)<textarea name="non_goals">${esc(p.non_goals.join("\n"))}</textarea></label><label>Target date<input type="date" name="target_date" value="${esc(p.target_date || "")}"></label><label>Decision authority<input name="authority" required value="${esc(p.setup.authority || "")}"><small>Must match the authenticated account identifier. Changing live authority requires an administrator.</small></label><label>Reported health<select name="health">${[
      ["UNKNOWN", "Unknown"],
      ["GREEN", "On track"],
      ["YELLOW", "At risk"],
      ["RED", "Off track"],
    ]
      .map(
        ([v, n]) =>
          `<option value="${v}" ${(p.outlook === "Unknown" ? "UNKNOWN" : p.health) === v ? "selected" : ""}>${n}</option>`,
      )
      .join(
        "",
      )}</select></label><label>Health rationale<textarea name="health_rationale">${esc(p.setup.health_rationale || "")}</textarea><small>A health assessment needs a reason. Source evidence remains separately inspectable.</small></label><div class="drawer-actions"><button class="button primary" type="submit">Save project</button></div></form>`,
  );
  $("#project-edit-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const body = Object.fromEntries(new FormData(e.target));
      body.non_goals = body.non_goals
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean);
      await api("/projects/" + pid, body);
      await refresh();
      openProject(pid);
      toast("Project definition saved.");
    });
  };
}
function openOutcome() {
  openDrawer(
    "Record an outcome",
    "Close the evidence loop",
    `<p class="drawer-intro">Capture what actually happened and link it to the decision. An outcome observation does not by itself establish causality.</p><form id="outcome-form" class="form-stack"><label>Project<select name="program_id" required>${programOptions(S.program)}</select></label><label>Related decision (optional)<select name="decision_id"></select></label><label>Observed outcome<textarea name="observation" required rows="7" placeholder="What happened after the decision? Include the measurement or source."></textarea></label><div class="drawer-actions"><button class="button primary" type="submit">Record outcome</button></div></form>`,
  );
  const form = $("#outcome-form");
  const updateDecisions = () => {
    const p = S.data.programs.find(
      (p) => p.id === form.elements.program_id.value,
    );
    form.elements.decision_id.innerHTML =
      '<option value="">Project-level observation</option>' +
      (p?.artifacts.decision_log?.decisions || [])
        .map(
          (d) =>
            `<option value="${esc(d.id)}">${esc(d.title)} · ${esc(d.id)}</option>`,
        )
        .join("");
  };
  form.elements.program_id.onchange = updateDecisions;
  updateDecisions();
  $("#outcome-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const result = await api("/outcomes", {
        arguments: Object.fromEntries(new FormData(e.target)),
      });
      await refresh();
      const p = S.data.programs.find((p) => p.id === result.payload.project_id);
      const d = p?.artifacts.decision_log?.decisions.find(
        (d) => d.id === result.payload.decision_id,
      );
      openDrawer(
        "Outcome recorded",
        "Observation saved to the evidence journal",
        `<p class="drawer-intro">${esc(result.payload.observed_value)}</p><dl class="facts"><dt>Project</dt><dd>${esc(p?.name || result.payload.project_id)}</dd><dt>Related decision</dt><dd>${esc(d?.title || "Project-level observation")}</dd><dt>Observed</dt><dd>${dt(result.occurred_at)}</dd></dl><div class="drawer-actions">${button("View history", "navigate", 'data-page="history"')}${button("Open evidence journal", "journal")}</div><details class="section-space"><summary class="text-button">Recorded details</summary><pre class="code-detail">${esc(JSON.stringify(result, null, 2))}</pre></details>`,
      );
    });
  };
}

function openGithubRead() {
  const c = S.data.connections.find((c) => c.id === "github");
  openDrawer(
    "Read GitHub evidence",
    "Explicit source read",
    `<p class="drawer-intro">Read up to 30 records per supported source category from ${esc(c.mapping.scope || "the mapped repository")} into ${esc(c.mapping.program_id || "the mapped project")}. This uses the server's configured credential. It does not modify GitHub.</p><form id="github-read-form"><label><input name="confirm" type="checkbox" required> I confirm this repository and project mapping.</label><div class="drawer-actions"><button class="button primary" type="submit">Read & analyze</button></div></form>`,
  );
  $("#github-read-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const r = await api("/connections/github/read", {
        arguments: {},
        confirm_external: true,
      });
      await refresh();
      openResult("GitHub read", r);
    });
  };
}
function openModelAsk() {
  openDrawer(
    "Ask TPLLM",
    "Read-only model assistance",
    `<p class="drawer-intro">The configured ${esc(S.data.settings.provider)} model (${esc(S.data.settings.model)}) can answer using stored projects and evidence. It cannot change records or send messages.</p><form id="ask-form" class="form-stack"><label>Your question<textarea name="question" required rows="5"></textarea></label><label><input type="checkbox" name="consent" required> Send workspace context to the configured model provider.</label><div class="drawer-actions"><button class="button primary" type="submit">Ask TPLLM</button></div></form>`,
  );
  $("#ask-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const r = await api("/ask", {
        question: new FormData(e.target).get("question"),
        confirm_external: true,
      });
      openDrawer(
        "TPLLM response",
        r.provider + " · " + r.model,
        `<div class="notice">Model-generated interpretation. Inspect the cited sources before making a decision.</div><pre class="markdown-output">${esc(r.answer)}</pre><div class="drawer-actions">${button("Inspect evidence", "evidence")}${button("Open action catalogue", "catalogue")}</div>`,
      );
    });
  };
}

function openNewProject() {
  openDrawer(
    "Create a project",
    "Start with a useful outcome",
    `<form id="new-project-form" class="form-stack"><label>Project name<input name="name" required placeholder="AI visual search"></label><label>Intended outcome<textarea name="goal" required placeholder="What should be measurably better when this project succeeds?"></textarea></label><label>Project identifier<input name="program_id" required pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,95}" placeholder="visual-search"><small>Used to connect this project to its source evidence.</small></label><label>Non-goals (one per line)<textarea name="non_goals" placeholder="What is outside the scope?"></textarea></label><div class="field-grid"><label>Target date<input name="target_date" type="date"></label><label>Work owner<input name="owner" placeholder="Name"></label><label>Success metric<input name="metric" placeholder="Request match accuracy"></label><label>Target<input name="target" placeholder="95%"></label></div><div class="notice">The authenticated account becomes the initial decision authority. Missing source evidence and unassessed health remain Unknown.</div><div class="drawer-actions"><button class="button primary" type="submit">Create project</button>${button("Advanced fields", "advanced-tool", 'data-tool="create_program"')}</div></form>`,
  );
  const form = $("#new-project-form");
  form.elements.name.oninput = () => {
    if (!form.elements.program_id.dataset.edited)
      form.elements.program_id.value = form.elements.name.value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-|-$/g, "");
  };
  form.elements.program_id.oninput = () =>
    (form.elements.program_id.dataset.edited = "true");
  form.onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const f = Object.fromEntries(new FormData(form));
      if (Boolean(f.metric) !== Boolean(f.target))
        throw new Error("Give the success metric both a name and a target.");
      const args = {
        program_id: f.program_id,
        name: f.name,
        goal: f.goal,
        non_goals: f.non_goals
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
      };
      if (f.target_date) args.target_date = f.target_date;
      if (f.owner) args.stakeholders = [{ name: f.owner, role: "Work owner" }];
      if (f.metric)
        args.success_metrics = [{ name: f.metric, target: f.target }];
      await api("/actions/create_program", { arguments: args });
      await refresh();
      openProject(f.program_id);
      toast("Project created. Add source evidence to build its context.");
    });
  };
}
function openProposal(pid) {
  openDrawer(
    "Propose a decision",
    "A question, options, and tradeoffs",
    `<form id="proposal-form" class="form-stack"><label>Project<select name="program_id" required>${programOptions(pid || S.program)}</select></label><label>Decision question<input name="title" required placeholder="Which approval path should we take?"></label><label>Context<textarea name="context" required placeholder="What changed, and why does a decision matter now?"></textarea></label>${[1, 2].map((i) => `<div class="evidence-card"><div class="eyebrow">Option ${i}</div><div class="form-stack"><label>Option ${i} name<input name="option_${i}" required></label><label>Benefits (one per line)<textarea name="pros_${i}" rows="2"></textarea></label><label>Tradeoffs (one per line)<textarea name="cons_${i}" rows="2"></textarea></label></div></div>`).join("")}<div class="drawer-actions"><button class="button primary" type="submit">Propose decision</button>${button("Advanced fields", "advanced-tool", `data-tool="propose_decision" data-program="${esc(pid || S.program)}"`)}</div></form>`,
  );
  $("#proposal-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const f = Object.fromEntries(new FormData(e.target));
      const lines = (s) =>
        s
          .split("\n")
          .map((v) => v.trim())
          .filter(Boolean);
      const args = {
        program_id: f.program_id,
        title: f.title,
        context: f.context,
        options: [1, 2].map((i) => ({
          label: f["option_" + i],
          pros: lines(f["pros_" + i]),
          cons: lines(f["cons_" + i]),
        })),
      };
      const r = await api("/actions/propose_decision", { arguments: args });
      await refresh();
      await openDecision(f.program_id, r.result.id);
      toast(
        "Decision proposed. Review the evidence and authority before finalizing.",
      );
    });
  };
}
function openNewTask(pid) {
  openDrawer(
    "Create a task",
    "Keep the purpose attached",
    `<form id="new-task-form" class="form-stack"><label>Project<select name="program_id" required>${programOptions(pid || S.program)}</select></label><label>Task title<input name="title" required placeholder="What needs to happen?"></label><label>Description<textarea name="description" placeholder="Why this work exists and what finishing it enables."></textarea></label><div class="field-grid"><label>Owner<input name="assignee"></label><label>Due date<input name="due_date" type="date"></label><label>Priority<select name="priority"><option>MEDIUM</option><option>HIGH</option><option>CRITICAL</option><option>LOW</option></select></label><label>Estimated hours<input name="effort_hours" type="number" min="0" step="0.5"></label></div><label>Source reference (optional)<input name="source_id" placeholder="Meeting, decision, or issue ID"></label><div class="drawer-actions"><button class="button primary" type="submit">Create task</button>${button("Advanced fields", "advanced-tool", `data-tool="create_board_task" data-program="${esc(pid || S.program)}"`)}</div></form>`,
  );
  $("#new-task-form").onsubmit = (e) => {
    e.preventDefault();
    busy(e.submitter, async () => {
      const args = Object.fromEntries(new FormData(e.target));
      args.effort_hours = Number(args.effort_hours || 0);
      const r = await api("/actions/create_board_task", { arguments: args });
      await refresh();
      openTask(args.program_id, r.result.task_id);
      toast("Task created.");
    });
  };
}

// Browser bootstrap
document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-action]");
  if (el) {
    e.preventDefault();
    Promise.resolve(handle(el.dataset.action, el)).catch((err) =>
      toast(err.message, true),
    );
  }
});
$("#search-icon").innerHTML = icon("search");
$("#notifications").innerHTML = icon("bell");
$("#ask-icon").innerHTML = icon("sparkles");
$("#menu-toggle").innerHTML = icon("menu");
$("#search-trigger").onclick = () => openSearch();
$("#ask-button").onclick = () => openSearch(true);
$("#notifications").onclick = () => handle("review-queue", $("#notifications"));
$("#workspace-switch").onclick = openWorkspace;
$("#profile").onclick = openLogin;
$("#close-drawer").onclick = () => $("#drawer").close();
const mobileNavigation = window.matchMedia("(max-width:700px)");
function syncNavigation() {
  const collapsed =
    mobileNavigation.matches && !$("#sidebar").classList.contains("open");
  $("#sidebar").inert = collapsed;
  $("#menu-toggle").setAttribute("aria-expanded", String(!collapsed));
}
$("#menu-toggle").onclick = () => {
  $("#sidebar").classList.toggle("open");
  syncNavigation();
};
mobileNavigation.addEventListener("change", syncNavigation);
syncNavigation();
$("#drawer").addEventListener("click", (e) => {
  if (
    e.target === $("#drawer") &&
    e.clientX < $("#drawer").getBoundingClientRect().left
  )
    $("#drawer").close();
});
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    openSearch();
  }
  if (e.key === "Escape") {
    $("#sidebar").classList.remove("open");
    syncNavigation();
  }
});
window.addEventListener("hashchange", () => {
  S.page = location.hash.slice(1).split("?")[0] || "dashboard";
  $("#sidebar").classList.remove("open");
  syncNavigation();
  if (S.data) render();
  window.scrollTo(0, 0);
});
S.page = location.hash.slice(1).split("?")[0] || "dashboard";
refresh().catch((e) => {
  $("#main").innerHTML = empty(
    "Could not open the workspace",
    e.message,
    button("Try again", "live"),
  );
  toast(e.message, true);
});

// Refresh the view without interrupting a review or someone editing a field.
setInterval(() => {
  if (
    document.visibilityState === "visible" &&
    !S.busy &&
    !$("#drawer").open &&
    !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement?.tagName)
  )
    refresh().catch(() => {});
}, 20000);
