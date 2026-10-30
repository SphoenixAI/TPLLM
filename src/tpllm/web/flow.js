"use strict";
const Flow = {
  data: () => S.data.coordination || {},
  rows(name) {
    return (this.data()[name] || []).filter(
      (r) => !S.program || r.project_id === S.program,
    );
  },
  source(key) {
    return (this.data().records || []).find((r) => r.key === key);
  },
  sources(keys = []) {
    return `<div class="action-row">${keys
      .map((key) => {
        const r = this.source(key);
        return r
          ? button(
              `${icon("link")}${esc(r.provider)} · ${esc(compact(r.title))}`,
              "flow-source",
              `data-key="${esc(key)}"`,
              "source-chip",
            )
          : "";
      })
      .join("")}</div>`;
  },
  link(page, label) {
    return button(label, "navigate", `data-page="${page}"`);
  },
  controls() {
    return (
      programSelect() +
      button(icon("history") + "Refresh connected context", "flow-refresh")
    );
  },
  status(value) {
    return badge(
      titleCase(value || "Awaiting setup"),
      ["ready", "accepted", "current", "healthy", "merged"].includes(value)
        ? "success"
        : ["held", "unavailable", "breached", "needs_reconciliation"].includes(
              value,
            )
          ? "warning"
          : "",
    );
  },
  collectionStrip() {
    return `<div class="flow-sources">${(this.data().collection || []).map((c) => `<button class="panel flow-source-status" data-action="flow-collection" data-provider="${esc(c.provider)}"><span>${icon(c.provider === "github" ? "file" : c.provider === "calendar" ? "clock" : "link")}<strong>${esc(c.provider === "calendar" ? "Google Calendar" : c.provider === "github" ? "GitHub" : titleCase(c.provider))}</strong></span><span class="muted">${S.workspace === "demo" ? "Demo source replay" : esc(c.report.status || (c.settings.enabled ? "Waiting for first read" : "Collection off"))}</span><small>${c.report.last_success ? "Collected " + dt(c.report.last_success) : "No successful collection yet"}</small></button>`).join("")}</div>`;
  },
  actionCard(a) {
    return `<article class="flow-action"><div class="list-row"><div class="grow"><div class="eyebrow">${esc(a.project_id)} · ${esc(a.provider)} → ${esc(a.destination)}</div><h3>${esc(a.title)}</h3></div>${this.status(a.status)}</div><p class="muted">${esc(a.receipt?.result || a.authority_reason)}</p><div class="action-row">${button("Inspect prepared action", "flow-action", `data-id="${esc(a.id)}"`)}</div></article>`;
  },
  dashboard() {
    const f = this.data(),
      records = this.rows("records"),
      actions = this.rows("actions"),
      ps = this.rows("engineering");
    return (
      heading(
        "Dashboard",
        "TPLLM connects the evidence and carries the next step forward.",
        this.controls(),
      ) +
      this.collectionStrip() +
      `<div class="toolbar"><span class="muted">${records.length} collected records · ${this.rows("relationships").length} typed connections</span><span class="spacer"></span>${S.workspace === "demo" ? button("Replay next source updates", "flow-advance") : this.link("settings", "Standing authority")}</div>` +
      `<div class="dashboard-grid"><div class="dashboard-left">${panel("TPLLM is handling", actions.map((a) => this.actionCard(a)).join("") || empty("Waiting for connected evidence", S.data.settings.hosted ? "Connect authorized sources once. Enabled collection and reassessment run on the hosted schedule." : "Connect authorized sources once. Collection and reassessment continue while the server is running.", this.link("connections", "Connect sources")))}${panel("Engineering changes", ps.map((p) => `<div class="list-row"><span class="item-icon">${icon("file")}</span><div class="grow"><h3>${esc(p.title)}</h3><p>${esc(p.estimate_basis.join(" "))}</p><div class="action-row">${this.status(p.data.state)}${button("Inspect diff & context", "flow-pr", `data-key="${esc(p.key)}"`)}</div></div></div>`).join("") || '<p class="muted">PRs, reviews, checks and bug-bash findings appear from connected repositories.</p>')}</div><div class="dashboard-right">${panel(
        "Needs your judgment",
        pendingDecisions()
          .map(
            (d) =>
              `<div class="list-row"><div class="grow"><div class="eyebrow">${esc(d.program_name)}</div><h3>${esc(d.title)}</h3><p>${esc(compact(d.context))}</p><div class="action-row">${button("Review prepared decision", "decision", `data-program="${esc(d.program_id)}" data-id="${esc(d.id)}"`)}</div></div></div>`,
          )
          .join("") +
          questions()
            .map(
              (q) =>
                `<div class="list-row"><div class="grow"><h3>${esc(q.title)}</h3><p>${esc(q.found)}</p>${button("Review source conflict", "review-queue")}</div></div>`,
            )
            .join("") ||
          '<p class="muted">No prepared human decision is waiting.</p>',
      )}${panel(
        "KPI outlook",
        this.rows("kpis")
          .map(
            (k) =>
              `<button class="outlook-row" data-action="flow-kpi" data-id="${esc(k.id)}"><strong>${esc(k.name)}</strong><span>${k.trend.latest_value ?? "Unknown"}${k.trend.latest_value == null ? "" : esc(k.unit)} · ${esc(k.trend.health)}</span></button>`,
          )
          .join("") || '<p class="muted">No sourced measurements yet.</p>',
      )}${panel("Context stays attached", '<p class="muted">Open a source, relationship, or prepared action to see what supports it. Estimates remain separate from committed scope and dates.</p>' + this.link("relationships", "Explore connected context"))}</div></div>`
    );
  },
  meetings() {
    const meetings = this.rows("meetings"),
      notes = this.rows("records").filter((r) => r.kind === "meeting_notes");
    return (
      heading(
        "Meetings & briefs",
        "Collected conversations become focused decisions and maintained context.",
        this.controls(),
      ) +
      `<div class="flow-summary"><span>${icon("clock")} ${meetings.length} calendar events assessed</span><span>${icon("file")} ${notes.length} AI note records collected</span><span>${icon("sparkles")} ${this.rows("briefs").length} briefs maintained</span></div>` +
      `<div class="stack">${meetings.map((m) => panel(m.title, `<div class="action-row">${this.status(m.assessment)}<span class="muted">${esc(m.project_id)}</span></div><h3 class="section-space">${esc(m.recommendation)}</h3><p class="description">${esc(m.reason)}</p>${this.sources(m.source_keys)}<div class="action-row">${button("Review context & async draft", "flow-meeting", `data-id="${esc(m.id)}"`)}</div>`)).join("") || panel("Meeting intelligence", empty("No collected meetings", "Authorize a calendar and AI notes source in Connections. TPLLM evaluates their context as new evidence arrives.", this.link("connections", "Review connections")))}</div>` +
      `<div class="section-space card-grid">${this.rows("briefs")
        .map((b) =>
          panel(
            `${b.project_id} · current brief`,
            `<div class="eyebrow">Maintained ${dt(b.updated_at)}</div><p class="flow-prose">${esc(b.summary)}</p>${this.sources(b.source_keys)}<small class="muted">${esc(b.method)}</small>`,
          ),
        )
        .join("")}</div>` +
      panel(
        "Collected AI meeting notes",
        notes
          .map(
            (n) =>
              `<div class="list-row"><span class="item-icon">${icon("file")}</span><div class="grow"><h3>${esc(n.title)}</h3><p>${esc(compact(n.text))}</p><span class="muted">${esc(n.provider)} · ${dt(n.observed_at)}</span></div>${button("Read source", "flow-source", `data-key="${esc(n.key)}"`)}</div>`,
          )
          .join("") ||
          '<p class="muted">Notes will appear here when the provider makes them available. Missing summaries remain a collection gap.</p>',
      )
    );
  },
  kpis() {
    return (
      heading(
        "KPIs",
        "Measurement, meaning, response, and the decisions that affect them.",
        this.controls(),
      ) +
      `<div class="card-grid">${
        this.rows("kpis")
          .map((k) =>
            panel(
              k.name,
              `<div class="eyebrow">${esc(k.project_id)}</div><div class="flow-kpi-value">${k.trend.latest_value ?? "Unknown"}<small>${k.trend.latest_value == null ? "" : esc(k.unit)}</small>${this.status(k.trend.health)}</div><p class="muted">${esc(k.observation_label || "Source claims")}</p><p>Committed target: <strong>${esc(k.committed_target)}</strong></p><p class="muted">${esc(k.meaning)}</p><dl class="facts section-space"><dt>Response</dt><dd>${esc(titleCase(k.response.response_state))}</dd><dt>Freshness</dt><dd>${k.trend.freshness_seconds == null ? "No measurement" : Math.round(k.trend.freshness_seconds / 60) + " minutes since source observation"}</dd><dt>Direction</dt><dd>${esc(k.trend.direction)}</dd></dl><div class="action-row">${button("Inspect measurement & response", "flow-kpi", `data-id="${esc(k.id)}"`)}</div>`,
            ),
          )
          .join("") ||
        empty(
          "No KPI definitions found",
          "TPLLM uses the project’s declared success criteria and reconciles values and target claims from connected evidence. Missing definitions stay explicit.",
        )
      }</div>`
    );
  },
  engineering() {
    return (
      heading(
        "Engineering",
        "Changes, ownership, review state, checks, and linked findings.",
        this.controls(),
      ) +
      `<div class="stack">${
        this.rows("engineering")
          .map((p) =>
            panel(
              p.title,
              `<div class="action-row">${this.status(p.data.state)}<span class="muted">${esc(p.scope)} #${esc(p.data.number)}</span></div><dl class="facts section-space"><dt>Author</dt><dd>${esc(p.data.author || "Unknown")}</dd><dt>Assigned</dt><dd>${esc((p.data.assignees || []).join(", ") || "Unassigned")}</dd><dt>Review requested</dt><dd>${esc((p.data.requested_reviewers || []).join(", ") || "None recorded")}</dd><dt>Changed files</dt><dd>${p.data.files?.length || 0} collected</dd></dl><p class="description">${esc(p.estimate_basis.join(" "))}</p><p class="muted">${esc(p.estimate)}. Merged code alone does not establish launch readiness.</p><div class="action-row">${button("Inspect diff & connected work", "flow-pr", `data-key="${esc(p.key)}"`)}</div>`,
            ),
          )
          .join("") ||
        empty(
          "No repository changes collected",
          "Connect a repository to bring PRs, diffs, review requests, checks and assigned issues into context.",
          this.link("connections", "Connect GitHub"),
        )
      }</div>`
    );
  },
  relationships() {
    const dependencies = programs().flatMap((p) =>
      (p.artifacts.raid_log?.dependencies || []).map((d) => ({
        ...d,
        program_id: p.id,
      })),
    );
    const edges = (this.data().relationships || []).filter(
      (e) =>
        !S.program ||
        e.source_keys.some((k) => this.source(k)?.project_id === S.program),
    );
    return (
      heading(
        "Relationships",
        "How source interactions connect work, context, ownership, and decisions.",
        programSelect(),
      ) +
      `<div class="notice">Authorship, assignment, review responsibility and decision authority stay distinct. An explicit reference shows a connection; it does not establish causality.</div>${dependencies.length ? panel("Delivery dependencies", dependencies.map((d) => `<div class="list-row"><div class="grow"><div class="eyebrow">Declared project dependency</div><h3>${esc(d.upstream_team)} → ${esc(d.downstream_team)}</h3><p>${esc(d.title)}</p><p class="muted">Owner: ${esc(d.owner || "Unassigned")}</p><div class="action-row">${button("Inspect project context", "project", `data-program="${esc(d.program_id)}"`)}${sourceButton(d.program_id)}</div></div></div>`).join("")) : ""}<div class="stack section-space">${edges.map((e) => `<article class="panel panel-inner"><div class="flow-edge"><strong>${esc(this.source(e.from)?.title || e.from)}</strong><span>${esc(titleCase(e.relation))} →</span><strong>${esc(this.source(e.to)?.title || e.to)}</strong></div><p class="muted">${esc(e.basis)}</p>${this.sources(e.source_keys)}</article>`).join("") || empty("No supported relationships yet", "Collect linked work and conversations to build a traceable relationship view.")}</div>`
    );
  },
  connections() {
    return (
      heading(
        "Connections",
        "Authorize source access once. TPLLM collects and reassesses as work changes.",
        button("Refresh authorized sources", "flow-refresh"),
      ) +
      this.collectionStrip() +
      panel(
        "Collection is scoped",
        `<p class="description">Each connection names the source and project it may read. Configure provider credentials on the server, then enable collection here. Changing a mapping switches collection off until it is authorized again.</p><p class="muted">${esc(S.data.settings.scheduler)}. Read access does not grant permission to send messages or change calendars.</p>`,
      ) +
      `<div class="card-grid section-space">${(this.data().collection || []).map((c) => panel(c.provider === "calendar" ? "Google Calendar" : c.provider === "github" ? "GitHub" : titleCase(c.provider), `<p class="muted">${esc(c.mapping.scope || "No source mapped")} · ${esc(c.mapping.program_id || "No project mapped")}</p><p>${c.settings.enabled ? "Automatic collection enabled" : "Collection not enabled"}${c.settings.interval_seconds ? " · every " + c.settings.interval_seconds / 60 + " minutes" : ""}</p>${(c.report.limitations || []).map((l) => `<p class="muted">${esc(l)}</p>`).join("")}<div class="action-row">${button("Configure collection", "flow-collection", `data-provider="${esc(c.provider)}"`)}</div>`)).join("")}</div>`
    );
  },
  settings() {
    const f = this.data();
    return (
      panel(
        "Standing coordination authority",
        `<p class="description">Grant a class of actions for a named project, destination, source visibility, frequency, and expiry. Covered actions proceed without another approval.</p><div class="action-row">${button("Grant internal update authority", "flow-grant")}${button(f.paused ? "Resume permitted actions" : "Pause all autonomous actions", "flow-pause")}</div><p class="muted">${f.paused ? "Autonomous execution is paused. Collection and internal reasoning continue." : "Authority is checked immediately before every execution."}</p>${(f.delegations || []).map((g) => `<div class="list-row"><div class="grow"><h3>${esc(g.project_id)} → ${esc(g.destinations.join(", "))}</h3><p>${esc(g.purpose)}</p><p class="muted">${g.max_per_day}/day · expires ${dt(g.expires_at)} · ${g.enabled ? "Enabled" : "Revoked"}</p></div>${g.enabled ? button("Revoke", "flow-revoke", `data-id="${esc(g.id)}"`) : ""}</div>`).join("")}<details><summary class="text-button">Execution coverage and reserved decisions</summary><p>Current outbound adapter: internal Slack updates. Calendar changes, external task updates, resource requests, and operational decision execution remain unavailable until their scoped adapters are implemented.</p><p>${esc((f.reserved || []).join(" · "))}</p></details>`,
      ) +
      panel(
        "Execution receipts",
        (f.receipts || [])
          .slice()
          .reverse()
          .map(
            (r) =>
              `<div class="list-row"><div class="grow"><h3>${esc(r.destination)} · ${esc(r.status)}</h3><p>${esc(r.result)}</p><small>${dt(r.at)} · authority ${esc(r.grant_id)}</small></div></div>`,
          )
          .join("") ||
          '<p class="muted">No external action has been attempted.</p>',
      )
    );
  },
  openSource(key) {
    const r = this.source(key);
    if (!r) return;
    openDrawer(
      r.title,
      `${r.provider} · collected evidence`,
      `<p class="flow-prose">${esc(r.text)}</p><dl class="facts"><dt>Source scope</dt><dd>${esc(r.scope)}</dd><dt>Occurred / updated</dt><dd>${dt(r.observed_at)}</dd><dt>Collected</dt><dd>${dt(r.collected_at)}</dd><dt>Source ID</dt><dd>${esc(r.source_id)}</dd></dl>${safeSourceUrl(r.url) ? `<a class="button" target="_blank" rel="noopener noreferrer" href="${esc(r.url)}">Open source</a>` : ""}<details class="section-space"><summary class="text-button">Source fields</summary><pre class="code-detail">${esc(JSON.stringify(r.data, null, 2))}</pre></details>`,
    );
  },
  async handle(action, el) {
    const d = el.dataset,
      f = this.data();
    if (action === "flow-source") return this.openSource(d.key);
    if (action === "flow-refresh")
      return busy(el, async () => {
        await api("/coordination/run", {});
        await refresh();
        toast(
          "Connected context refreshed. Authority and communication limits were rechecked.",
        );
      });
    if (action === "flow-advance")
      return busy(el, async () => {
        await api("/demo/advance", {});
        await refresh();
        toast(
          "Source replay advanced: PR merged and bug-bash finding closed. Commitments and context reassessed.",
        );
      });
    if (action === "flow-pause")
      return busy(el, async () => {
        await api("/coordination/pause", { paused: !f.paused });
        await refresh();
      });
    if (action === "flow-revoke")
      return busy(el, async () => {
        await api("/delegations/" + d.id + "/revoke", {});
        await refresh();
      });
    if (action === "flow-pr") {
      const p = f.engineering.find((p) => p.key === d.key);
      if (!p) return;
      openDrawer(
        p.title,
        "Engineering evidence",
        `<dl class="facts"><dt>Author</dt><dd>${esc(p.data.author)}</dd><dt>Assigned</dt><dd>${esc(p.data.assignees.join(", ") || "Unknown")}</dd><dt>Review requested</dt><dd>${esc(p.data.requested_reviewers.join(", ") || "None recorded")}</dd><dt>Head revision</dt><dd>${esc(p.data.head)}</dd></dl><p class="description">${esc(p.estimate_basis.join(" "))}</p><p class="muted">${esc(p.data.code_ownership)}</p>${(p.data.limitations || []).map((v) => `<p class="notice">${esc(v)}</p>`).join("")}<h3 class="subheading">Changed files</h3>${p.data.files.map((file) => `<details class="evidence-card"><summary>${esc(file.filename)} · +${file.additions || 0} / −${file.deletions || 0}</summary><pre class="code-detail">${esc(file.patch || "Patch unavailable; binary or truncated file.")}</pre></details>`).join("") || "<p>No diffs collected.</p>"}<h3 class="subheading">Reviews & checks</h3>${p.current_reviews.map((r) => `<p>${esc(r.reviewer)} · ${esc(r.state)}</p>`).join("")}${p.data.checks.map((c) => `<p>${esc(c.name)} · ${esc(c.conclusion || c.status)}</p>`).join("")}${this.sources([p.key, ...p.linked_issues])}`,
      );
      return;
    }
    if (action === "flow-kpi") {
      const k = f.kpis.find((k) => k.id === d.id);
      if (!k) return;
      openDrawer(
        k.name,
        "KPI measurement & response",
        `<p class="drawer-intro">${esc(k.meaning)}</p><div class="flow-kpi-value">${k.trend.latest_value ?? "Unknown"}${k.trend.latest_value == null ? "" : esc(k.unit)} ${this.status(k.trend.health)}</div><p>Committed target: ${esc(k.committed_target)}</p><div class="notice">${esc(k.measurement_basis)}</div><h3 class="subheading">Response</h3><p>${esc(k.response.explanation)}</p><h3 class="subheading">Source reconciliation</h3>${k.reconciliation.map((r) => `<div class="evidence-card">${this.status(r.status)}<p>${esc(r.explanation)}</p></div>`).join("") || "<p>No source claims to reconcile.</p>"}<h3 class="subheading">Reported claims</h3>${Array.from(
          new Map(k.claims.map((c) => [c.statement, c])).values(),
        )
          .map(
            (c) =>
              `<p>${esc(c.statement)} <small class="muted">${dt(c.observed_at)}</small></p>`,
          )
          .join(
            "",
          )}${(k.excluded_sources || []).length ? `<h3 class="subheading">Excluded from KPI observations</h3>${k.excluded_sources.map((r) => `<div class="evidence-card"><h4>${esc(r.title)}</h4><p>${esc(r.reason)}</p>${this.sources([r.key])}</div>`).join("")}` : ""}${this.sources(k.source_keys)}`,
      );
      return;
    }
    if (action === "flow-meeting") {
      const m = f.meetings.find((m) => m.id === d.id);
      if (!m) return;
      openDrawer(
        m.title,
        "Automatically prepared meeting assessment",
        `<h3>${esc(m.recommendation)}</h3><p class="description">${esc(m.reason)}</p>${this.sources(m.source_keys)}${m.draft ? `<h3 class="subheading">Prepared async update</h3><pre class="markdown-output">${esc(m.draft)}</pre>` : ""}<p class="muted">${esc(m.calendar_action)}</p>`,
      );
      return;
    }
    if (action === "flow-action") {
      const a = f.actions.find((a) => a.id === d.id);
      if (!a) return;
      openDrawer(
        a.title,
        "Prepared coordination",
        `${this.status(a.status)}<dl class="facts section-space"><dt>Destination</dt><dd>${esc(a.provider)} · ${esc(a.destination)}</dd><dt>Authority</dt><dd>${esc(a.receipt?.grant_id || a.grant_id || a.authority_reason)}</dd></dl><pre class="markdown-output">${esc(a.body)}</pre><p class="notice">${esc(a.receipt?.result || a.authority_reason)}</p>${this.sources(a.source_versions.map((r) => r.key))}<div class="action-row">${this.link("settings", "Inspect standing authority")}</div>`,
      );
      return;
    }
    if (action === "flow-collection") return this.openCollection(d.provider);
    if (action === "flow-grant") return this.openGrant();
  },
  openCollection(provider) {
    const c = this.data().collection.find((c) => c.provider === provider);
    if (!c) return;
    openDrawer(
      "Connect " +
        (provider === "calendar" ? "Google Calendar" : titleCase(provider)),
      "One-time scoped setup",
      `<p class="description">${provider === "github" ? "Read PR metadata, diffs, reviewers, checks and linked issues." : provider === "slack" ? "Read this channel and its bounded thread history." : provider === "zoom" ? "Collect AI meeting summaries belonging to this host." : "Read this calendar’s events and descriptions."}</p><div class="notice">${S.workspace === "demo" ? "Demo mode uses synthetic source events. No account access occurs." : c.configured ? "Provider credentials are configured on the server." : S.data.settings.hosted ? "Provider credentials are not configured. Configure the hosted server environment first." : "Provider credentials are not configured. Run tpllm init on the server first."}</div><form id="collection-form" class="form-stack"><label>Project<select name="program_id" required>${programOptions(c.mapping.program_id || S.program)}</select></label><label>${provider === "github" ? "Repository (owner/name)" : provider === "slack" ? "Slack channel ID" : provider === "zoom" ? "Zoom host ID or exact email" : "Google Calendar ID"}<input name="scope" required value="${esc(c.mapping.scope || "")}"></label><label>Collection interval (minutes)<input type="number" min="1" max="1440" name="interval" value="${(c.settings.interval_seconds || 300) / 60}"></label><label class="checkbox-label"><input type="checkbox" name="enabled" ${c.settings.enabled ? "checked" : ""}> I authorize recurring reads from this source for this project.</label><div class="drawer-actions"><button type="submit" class="button primary">Save collection scope</button></div></form>`,
    );
    $("#collection-form").onsubmit = (e) => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const v = Object.fromEntries(new FormData(e.target));
        await api("/connections/" + provider, {
          arguments: { program_id: v.program_id, scope: v.scope },
        });
        await api("/collection/" + provider, {
          enabled: v.enabled === "on",
          interval_seconds: Number(v.interval) * 60,
        });
        await refresh();
        $("#drawer").close();
        toast("Collection scope saved.");
      });
    };
  },
  openGrant() {
    const scopes = (this.data().collection || [])
      .filter((c) => c.mapping.scope)
      .map((c) => `${c.provider}:${c.mapping.scope}`);
    openDrawer(
      "Delegate internal updates",
      "Standing authority · exact scope",
      `<p class="description">Permit prepared project delivery status updates in Slack for the selected project. This does not authorize new commitments, calendar changes, security changes, or arbitrary messages.</p><form id="delegation-form" class="form-stack"><label>Project<select name="project_id" required>${programOptions(S.program)}</select></label><label>Approved Slack channel ID<input name="destination" required value="${esc((this.data().collection || []).find((c) => c.provider === "slack")?.mapping.scope || "")}"></label><label>Reason for this delegation<textarea name="purpose" required minlength="8">Share source-backed project status and keep technical reviews focused.</textarea></label><fieldset><legend>Source context permitted in this destination</legend>${scopes.map((s) => `<label class="checkbox-label"><input type="checkbox" name="source_scope" value="${esc(s)}"> ${esc(s)}</label>`).join("") || "<p>Map sources before granting authority.</p>"}</fieldset><div class="field-grid"><label>Maximum updates per day<input name="max_per_day" type="number" min="1" max="20" value="3" required></label><label>Minutes between messages<input name="minimum_interval_minutes" type="number" min="1" max="1440" value="60" required></label><label>Maximum evidence age (hours)<input name="max_evidence_age_hours" type="number" min="1" max="168" value="24" required></label><label>Expires after (days)<input name="days" type="number" min="1" max="30" value="7" required></label></div><label class="checkbox-label"><input type="checkbox" required> I delegate this class of actions within these limits. Covered actions may proceed without another approval.</label><button class="button primary" type="submit">Grant scoped authority</button></form>`,
    );
    $("#delegation-form").onsubmit = (e) => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const fd = new FormData(e.target),
          v = Object.fromEntries(fd);
        await api("/delegations", {
          project_id: v.project_id,
          destinations: [v.destination],
          source_scopes: fd.getAll("source_scope"),
          purpose: v.purpose,
          max_per_day: Number(v.max_per_day),
          minimum_interval_minutes: Number(v.minimum_interval_minutes),
          max_evidence_age_hours: Number(v.max_evidence_age_hours),
          expires_at: new Date(
            Date.now() + Number(v.days) * 86400000,
          ).toISOString(),
        });
        await refresh();
        $("#drawer").close();
        toast("Standing delegation saved.");
      });
    };
  },
};
