"use strict";
const DecisionUI = {
  data: () => S.data.decision_intelligence || { mode: "off", insights: [], traces: [] },
  definitionId: t => `${t.definition.decision_id}.v${t.definition.version}`,
  value(t) {
    const r = t.result;
    if (!r) return "Unavailable";
    if (r.question_type === "noul") return `${(r.probability * 100).toFixed(1)}% probability`;
    if (r.question_type === "score") return `${Number(r.selected_value).toFixed(2)} / ${Object.keys(r.legend).length - 1}`;
    return titleCase(r.selected_value);
  },
  judgment(t) {
    const p = t.policy_evaluation;
    const tone = p.status === "unavailable" || p.strength !== "strong" ? "warning" : "";
    return badge(`${titleCase(p.strength)} judgment · ${titleCase(p.status)}`, tone);
  },
  panel(entityId = null) {
    const d = this.data();
    const insights = (d.insights || []).filter(i => (!S.program || i.project_id === S.program) && (!entityId || i.entity_id === entityId));
    const rows = insights.map(i => {
      const traces = (d.traces || []).filter(t => i.trace_ids.includes(t.id));
      return `<article class="evidence-card"><div class="eyebrow">${esc(i.project_id)} · ${esc(i.mode)} ${i.current ? "" : "· Historical evidence"}</div><details><summary class="text-button">${esc(i.title)}</summary><p class="muted">Validation: ${esc(titleCase(i.validation.outcome))} · ${esc(i.priority === "review" ? "Prioritized for review" : "Existing review order")}</p><div class="decision-grid">${traces.map(t => `<button class="decision-tile" data-action="bounded-trace" data-id="${esc(t.id)}"><span>${esc(titleCase(t.definition.decision_id.replaceAll(".", " ")))}</span><strong>${esc(this.value(t))}</strong>${this.judgment(t)}${t.result?.confidence != null ? `<small>${(t.result.confidence * 100).toFixed(1)}% model confidence</small>` : ""}</button>`).join("")}</div></details></article>`;
    }).join("");
    return panel("Decision Intelligence", `<div class="action-row">${badge(titleCase(d.mode))}<span class="muted">${esc(d.authority || "Bounded judgments cannot authorize or execute actions.")}</span></div>${d.error ? `<p class="notice">${esc(d.error)}</p>` : ""}<p class="muted">${d.mode === "shadow" ? "Shadow judgments are recorded for comparison. Existing outcomes and action permissions are unchanged." : "Probability expresses a model judgment. Policy and existing validation determine whether it can influence review priority."}</p>${rows || '<p class="muted">No bounded judgments for this selection. The server starts with Jev off; enable shadow mode to collect evidence for evaluation.</p>'}`);
  },
  async open(id) {
    const d = this.data(), t = (d.traces || []).find(t => t.id === id);
    if (!t) return;
    let detail = null;
    if (S.workspace === "demo" || S.token) {
      try { detail = await api(`/decision-intelligence/${encodeURIComponent(id)}`); } catch (_) { /* Snapshot remains inspectable. */ }
    }
    const r = t.result, p = t.policy_evaluation;
    const insight = (d.insights || []).find(i => i.trace_ids.includes(id));
    const dist = r?.probability_distribution || {};
    const alternatives = Object.entries(dist).sort((a, b) => b[1] - a[1]);
    openDrawer("Decision Intelligence", this.definitionId(t),
      `<p class="eyebrow">${esc(this.definitionId(t))} · ${esc(t.definition.domain)}</p><div class="action-row">${badge(titleCase(t.mode))}${this.judgment(t)}</div><h3 class="subheading">Decision</h3><p class="decision-value">${esc(this.value(t))}</p>${alternatives.map(([k,v]) => `<div class="decision-probability"><span>${esc(r.legend[k] || titleCase(k))}</span><meter min="0" max="1" value="${v}" aria-label="${esc(k)} probability"></meter><strong>${(v*100).toFixed(1)}%</strong></div>`).join("")}<dl class="facts section-space"><dt>Model requested</dt><dd>${esc(t.model_requested)}</dd><dt>Model reported</dt><dd>${esc(t.model_resolved || "Unavailable")}</dd><dt>Provider</dt><dd>${esc(t.provider)}</dd><dt>Confidence</dt><dd>${r?.confidence == null ? "Not supplied for Noul" : (r.confidence*100).toFixed(1)+"%"}</dd><dt>Existing comparison</dt><dd>${t.disagreement == null ? "No comparable baseline" : t.disagreement ? "Disagrees with baseline" : "Agrees with baseline"}</dd></dl><h3 class="subheading">Evidence</h3><p class="muted">${esc(t.evidence_refs.join(" · ") || "No evidence references")}</p><p class="muted">Observations: ${esc(t.observation_refs.join(" · ") || "None")}</p>${insight ? Flow.sources(insight.source_keys) : ""}<h3 class="subheading">Policy outcome</h3><p>${esc(p.reasons.join(" "))}</p><p class="muted">${esc(p.policy_id)} · ${p.calibration_samples} calibration samples</p><h3 class="subheading">Downstream effect</h3><p>${esc(insight?.contributing_trace_ids.includes(id) ? "Contributes to review priority." : "No advisory influence from this judgment.")}</p><p>Validation: ${esc(insight?.validation.outcome || "Not attached")}. No external action is proposed or permitted by this decision layer.</p><details><summary class="text-button">Version and temporal trace</summary><dl class="facts"><dt>Definition hash</dt><dd class="trace-hash">${esc(t.definition_hash)}</dd><dt>State hash</dt><dd class="trace-hash">${esc(t.state_hash)}</dd><dt>Evidence cutoff</dt><dd>${dt(t.evidence_cutoff)}</dd><dt>Valid from</dt><dd>${dt(t.temporal.valid_from)}</dd><dt>Known from</dt><dd>${dt(t.temporal.known_from)}</dd><dt>Supersedes</dt><dd>${esc(t.supersedes || "None")}</dd></dl><p>${esc(t.definition.instructions)}</p></details><h3 class="subheading">Calibration history</h3>${(detail?.labels || []).map(l => `<p class="muted">${esc(l.source)} · ${esc(String(l.value))}<br>Label ID: ${esc(l.id)}</p>`).join("") || "<p class=\"muted\">No reviewed outcome loaded.</p>"}<p class="notice">This is an evidence and policy trace. Jev supplies probabilities, not an internal reasoning narrative.</p>${r ? `<div class="action-row">${button("Record reviewed outcome", "bounded-label", `data-id="${esc(id)}"`)}</div>` : ""}`);
  },
  label(id) {
    const t = (this.data().traces || []).find(t => t.id === id);
    if (!t?.result) return;
    const q = t.definition;
    const options = q.question_type === "noul" ? [["true", "Supported"], ["false", "Not supported"]] : q.question_type === "choice" ? Object.keys(q.criteria).map(k => [k, titleCase(k)]) : q.criteria.map((v,i) => [i,v]);
    openDrawer("Record reviewed outcome", this.definitionId(t),
      `<p class="notice">A label records your review for calibration. It grants no authority and does not promote a model. Demo labels remain fixture data.</p><form id="bounded-label-form" class="form-stack"><label>Reviewed result<select name="value">${options.map(([v,label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join("")}</select></label><label>Outcome evidence reference<input name="reference" required placeholder="Evidence or outcome record ID"></label><label>Previous label ID (required for a correction)<input name="supersedes"></label><div class="drawer-actions"><button class="button primary" type="submit">Save calibration label</button></div></form>`);
    $("#bounded-label-form").onsubmit = e => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const f = new FormData(e.target), raw = f.get("value");
        await api(`/decision-intelligence/${encodeURIComponent(id)}/labels`, {
          value: q.question_type === "noul" ? raw === "true" : q.question_type === "score" ? Number(raw) : raw,
          source: "human_review", evidence_refs: [f.get("reference")], observed_at: new Date().toISOString(),
          supersedes: f.get("supersedes") || null,
        });
        await refresh();
        await this.open(id);
        toast("Calibration label recorded. Model approval is unchanged.");
      });
    };
  },
};
