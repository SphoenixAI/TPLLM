"use strict";
const IntelligenceUI = {
  section: "overview",
  data: null,
  error: "",
  async load() {
    this.data = null;
    this.error = "";
    if (S.workspace !== "demo" && !S.token) {
      this.error = "Sign in with a project-scoped credential to inspect intelligence artifacts.";
      return;
    }
    try {
      this.data = await api(`/intelligence${S.program ? `?program_id=${encodeURIComponent(S.program)}` : ""}`);
    } catch (e) { this.error = e.message || "Intelligence history unavailable."; }
  },
  records() { return (this.data?.records || []).filter(r => !S.program || r.project_id === S.program); },
  title(r) { return titleCase(r.task.task_id.replaceAll(".", " ").replace("reasoning ", "")); },
  artifact(value) {
    if (Array.isArray(value)) return value.length ? `<ul>${value.map(v => `<li>${this.artifact(v)}</li>`).join("")}</ul>` : '<span class="muted">None recorded</span>';
    if (value && typeof value === "object") {
      const first = ["primary_hypothesis", "situation", "summary", "options", "supporting_evidence_refs", "contradictory_evidence_refs", "alternative_hypotheses", "unknowns"];
      const order = key => first.includes(key) ? first.indexOf(key) : first.length;
      return `<dl class="facts intelligence-fields">${Object.entries(value).sort((a,b) => order(a[0])-order(b[0])).map(([k,v]) => `<dt>${esc(titleCase(k.replaceAll("_", " ")))}</dt><dd>${this.artifact(v)}</dd>`).join("")}</dl>`;
    }
    return esc(String(value ?? "Unknown"));
  },
  tile(r) {
    const output = r.reasoning?.structured_output;
    const alternatives = Object.entries(r.decision?.result?.probability_distribution || {}).sort((a,b) => b[1]-a[1]).slice(0,2);
    const probabilities = alternatives.map(([key,p]) => `${key}: ${(p*100).toFixed(1)}%`).join(" · ");
    return `<article class="evidence-card"><div class="eyebrow">${esc(r.task.lane)} · ${esc(r.cohort.cohort_id)} · ${esc(r.execution.provenance)}</div><h3>${esc(this.title(r))}</h3><p>${esc(r.decision ? DecisionUI.value(r.decision) : output?.primary_hypothesis || output?.situation || output?.summary || titleCase(r.status))}</p>${probabilities ? `<p>${esc(probabilities)}</p>` : ""}<p class="muted">${esc(r.resolved_model || "Model unavailable")} · v${r.task.version} · ${esc(r.reason || r.status)}</p>${button("Inspect and review", "intelligence-record", `data-id="${esc(r.id)}"`)}</article>`;
  },
  panel() {
    const d = this.data, records = this.records();
    const tabs = ["overview", "decisions", "reasoning", "comparisons", "cohorts", "evaluation"];
    const nav = `<div class="intelligence-tabs" role="group" aria-label="Intelligence views">${tabs.map(s => `<button class="button ${s === this.section ? "primary" : ""}" data-action="intelligence-tab" data-section="${s}" aria-pressed="${s === this.section}">${titleCase(s)}</button>`).join("")}</div>`;
    let body = "";
    if (this.error) body = `<p class="notice">${esc(this.error)}</p>`;
    else if (this.section === "overview") {
      body = `<p>Jev judges. Reasoning models synthesize. TPLLM governs.</p><p class="muted">Evaluation mode: ${esc(d?.mode || "off")} · ${d?.queue_depth || 0} queued snapshots. Artifacts and review labels grant no action permissions.</p><div class="decision-grid">${["decision", "reasoning"].map(l => `<div class="decision-tile"><span>${titleCase(l)} records</span><strong>${records.filter(r => r.task.lane === l).length}</strong></div>`).join("")}</div><details><summary class="text-button">Versioned task catalog</summary><div class="intelligence-table"><table><thead><tr><th>Task</th><th>Lane</th><th>Output</th><th>Evaluation</th></tr></thead><tbody>${(d?.catalog || []).map(t => `<tr><td>${esc(t.task_id)}.v${t.version}</td><td>${esc(t.lane)}</td><td>${esc(t.output_contract)}</td><td>${esc(t.evaluation_policy)}</td></tr>`).join("")}</tbody></table></div></details>`;
    } else if (this.section === "decisions") {
      body = DecisionUI.panel() + `<div class="card-grid">${records.filter(r => r.task.lane === "decision").map(r => this.tile(r)).join("")}</div>`;
    } else if (this.section === "reasoning") {
      body = `<p class="muted">Typed synthesis with locally checked schemas and references. A valid reference does not prove a claim. These artifacts are not Jev reasoning or hidden chain-of-thought.</p><div class="card-grid">${records.filter(r => r.task.lane === "reasoning").map(r => this.tile(r)).join("") || '<p class="muted">No reasoning artifacts for this selection.</p>'}</div>`;
    } else if (this.section === "comparisons") {
      body = (d?.comparisons || []).filter(c => c.kind === "cross_lane").map(c => {
        const left = records.find(r => r.id === c.left_id), right = records.find(r => r.id === c.right_id);
        if (!left || !right) return "";
        const human = (d.reviews || []).filter(r => [left.id, right.id].includes(r.record_id)).at(-1);
        const outcome = (d.outcomes || []).filter(r => [left.id, right.id].includes(r.record_id)).at(-1);
        return `<article class="evidence-card">${badge(titleCase(c.state))}<p>${esc(c.explanation)}</p><div class="card-grid">${this.tile(left)}${this.tile(right)}</div><p class="muted">Human review: ${esc(human?.label || "Pending")} · Outcome: ${esc(outcome?.outcome || "Pending")}</p></article>`;
      }).join("") || '<p class="muted">No selective overlap comparisons recorded. Enable explicit fixture or sampled evaluation on the server.</p>';
    } else if (this.section === "cohorts") {
      body = `<p class="muted">Segments retain provider, requested and reported model, task version, and cohort version. Small samples do not justify routing.</p>` + (d?.evaluation?.segments || []).map(s => `<details class="evidence-card"><summary class="text-button">${esc(s.cohort)} · ${esc(s.task)} · ${s.samples} samples · ${esc(s.status)}</summary><p>${esc(s.provider)} / ${esc(s.resolved_model || "unresolved")} (${esc(s.provenance)})</p>${this.artifact(s.lane === "decision" ? s.metrics : {schema_valid_rate:s.schema_valid_rate, evidence_reference_valid_rate:s.evidence_reference_valid_rate, human_acceptance:s.human_acceptance, human_samples:s.human_samples})}</details>`).join("");
    } else {
      const ev = d?.evaluation;
      body = `<p class="notice">Mock latency, usage and costs are excluded from provider performance. Bounded decisions and rich synthesis are different workloads.</p>${this.artifact(ev?.coverage || {})}`;
      body += (ev?.efficiency || []).map(e => `<article class="evidence-card"><h3>${esc(titleCase(e.lane))} · ${esc(e.provider)} · ${esc(e.resolved_model || "unresolved")}</h3><p class="eyebrow">${esc(e.provenance)}</p>${this.artifact({requests:e.requests, median_latency_ms:e.median_latency_ms, median_total_tokens:e.median_total_tokens, cost_usd:e.cost_usd, questions_per_request:e.questions_per_request, schema_valid_rate:e.schema_valid_rate, failure_rate:e.failure_rate})}</article>`).join("");
      body += `<h3>Cases to review</h3>${(ev?.valuable_cases || []).map(v => `<p>${button(titleCase(v.kind.replaceAll("_", " ")), "intelligence-record", `data-id="${esc(v.record_id)}"`)}</p>`).join("") || '<p class="muted">No flagged cases.</p>'}`;
    }
    return panel("Intelligence Review", nav + body);
  },
  async open(id) {
    const detail = await api(`/intelligence/${encodeURIComponent(id)}`), r = detail.record, p = detail.packet;
    const output = r.reasoning?.structured_output;
    let payload = output ? this.artifact(output) : `<p>${esc(r.reason || "Unavailable")}</p>`;
    if (r.decision?.result) {
      const result = r.decision.result;
      payload = `<p class="decision-value">${esc(DecisionUI.value(r.decision))}</p>${Object.entries(result.probability_distribution).map(([k,v]) => `<div class="decision-probability"><span>${esc(result.legend[k] || k)}</span><meter min="0" max="1" value="${v}" aria-label="${esc(k)} probability"></meter><strong>${(v*100).toFixed(1)}%</strong></div>`).join("")}<p>Policy: ${esc(r.decision.policy_evaluation.status)} · ${esc(r.decision.policy_evaluation.reasons.join(" "))}</p>`;
    }
    const labels = r.task.lane === "decision" ? ["agree", "disagree", "ambiguous", "insufficient_evidence", "correct_label"] : ["accept", "partially_correct", "reject", "missing_evidence", "unsupported_inference", "corrected_interpretation"];
    const q = r.decision?.definition;
    const options = !q ? [] : q.question_type === "noul" ? [["true","True"],["false","False"]] : q.question_type === "choice" ? Object.keys(q.criteria).map(k => [k,k]) : q.criteria.map((k,i) => [i,k]);
    openDrawer("Intelligence Review", this.title(r), `<p class="eyebrow">${esc(r.task.task_id)}.v${r.task.version} · ${esc(r.cohort.cohort_id)} · ${esc(r.cohort.assignment_method)}</p><p>${esc(r.provider)} / ${esc(r.resolved_model || "Unavailable")} · ${esc(r.execution.provenance)} · ${esc(r.status)}</p>${payload}<h3>Grounding</h3><p>${esc(r.evidence_refs.join(" · "))}</p>${r.reasoning ? `<p>Schema: ${esc(String(r.reasoning.schema_valid))} · References: ${esc(String(r.reasoning.evidence_refs_valid))}</p><p>${esc(r.reasoning.unsupported_claim_flags.join(" · "))}</p>` : ""}<details><summary class="text-button">Evidence packet and temporal lineage</summary><p class="trace-hash">${esc(r.evidence_packet_id)}</p><p>Valid time: ${dt(r.valid_time)} · Known at: ${dt(r.transaction_time)} · Evaluated: ${dt(r.created_at)}</p><p>${esc(p.limitations.join(" "))}</p>${p.excerpts.map(e => `<p><strong>${esc(e.evidence_ref)}</strong><br>${esc(e.text)}</p>`).join("") || '<p class="muted">Source excerpts were omitted.</p>'}</details><h3>Review history</h3>${detail.reviews.map(v => `<p>${esc(v.label)} · ${esc(v.reviewer)} · ${dt(v.recorded_at)}<br>${esc(v.interpretation)}</p>`).join("") || '<p class="muted">Pending human review</p>'}<h3>Outcomes</h3>${detail.outcomes.map(v => `<p>${esc(v.outcome)} · ${dt(v.observed_at)}</p>`).join("") || '<p class="muted">No structured outcome attached.</p>'}<p class="notice">Review events append to history. The original model artifact, authority, and kill switch remain unchanged.</p><form id="intelligence-review-form" class="form-stack"><label>Review<select name="label">${labels.map(l => `<option value="${l}">${esc(titleCase(l.replaceAll("_", " ")))}</option>`).join("")}</select></label>${options.length ? `<label>Reference label (optional; required for Correct label)<select name="decision_value"><option value="">No calibration label</option>${options.map(([v,t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("")}</select></label>` : ""}<label>Correction or explanation<textarea name="interpretation" maxlength="3000"></textarea></label><button class="button primary" type="submit">Append review</button></form>`);
    $("#intelligence-review-form").onsubmit = e => {
      e.preventDefault();
      busy(e.submitter, async () => {
        const f = new FormData(e.target), raw = f.get("decision_value");
        await api(`/intelligence/${encodeURIComponent(id)}/reviews`, {label:f.get("label"), interpretation:f.get("interpretation"),
          decision_value: raw == null || raw === "" ? null : q.question_type === "noul" ? raw === "true" : q.question_type === "score" ? Number(raw) : raw,
          supersedes:detail.reviews.at(-1)?.id || null});
        await refresh(); await this.open(id); toast("Review appended. No action authorized.");
      });
    };
  },
};
