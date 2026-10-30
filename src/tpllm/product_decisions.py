"""Small product seam: trends -> bounded judgments -> unchanged validation.

Existing coordination/actions are never inputs to, or outputs from, this seam.
Shadow output lives only in the diagnostic decision projection.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from tpllm.application.decision_calibration import decision_telemetry, measured_profiles, quality_reports
from tpllm.application.registry import ActiveCalibrationRegistry
from tpllm.decisions.config import DecisionConfig
from tpllm.decisions.engine import DecisionEngine
from tpllm.decisions.models import DecisionCalibrationProfile, fingerprint, utc
from tpllm.decisions.policy import DecisionAcceptancePolicy, DecisionPolicyGate
from tpllm.decisions.providers import TypeSafeJevProvider
from tpllm.decisions.registry import default_registry
from tpllm.decisions.state import DecisionStateBuilder
from tpllm.integrations.envelopes import make_envelope, envelope_to_project_evidence
from tpllm.ontology.core import EvidenceClaim
from tpllm.ontology.entities import AgentRecommendation
from tpllm.ontology.measurement import KPIInsight, KPIObservation
from tpllm.ontology.validation import validate_insight
from tpllm.runtime.decision_journal import JournalDecisionStore

logger = logging.getLogger(__name__)


def _evidence_aliases(records):
    """Bridge the deterministic KPI extractor's IDs to journal source IDs.

    Reconstruct exactly the existing extraction envelope. This does not invent
    supporting evidence, rewrite the journal, or alter the baseline pipeline.
    """
    aliases = {}
    for r in records:
        aliases[r["evidence_id"]] = r["evidence_id"]
        envelope = make_envelope(source_system=r["provider"], source_type="document", source_id=r["key"],
            event_type="supporting_evidence", observed_at=utc(datetime.fromisoformat(r["observed_at"])).replace(tzinfo=None),
            project_ids=[r["project_id"]], content=r["text"], title=r["title"],
            received_at=utc(datetime.fromisoformat(r["collected_at"])).replace(tzinfo=None))
        evidence = envelope_to_project_evidence(envelope, project_id=r["project_id"])
        if evidence:
            aliases[evidence.id] = r["evidence_id"]
    return aliases


def _settings(ws):
    # Demo never receives a production credential. Tests inject a provider and
    # config explicitly; ordinary demo usage stays off.
    injected = getattr(ws, "decision_config", None)
    return injected or (DecisionConfig() if ws.demo else DecisionConfig.from_env())


def assess(ws, projection, *, at, force=False):
    try:
        cfg = _settings(ws)
        if not cfg.active:
            return
        _assess(ws, projection, cfg=cfg, at=at, force=force)
    except Exception:
        # Optional inference/persistence/config failures cannot interrupt the
        # baseline collection flow. No result is applied on this path.
        logger.warning("bounded decision assessment unavailable", extra={"outcome": "unavailable"})
        try:
            ws.put("decision_intelligence", {"mode": "unavailable", "insights": [], "trace_ids": [],
                "error": "Decision assessment unavailable; existing TPLLM behavior retained."})
        except Exception:
            # A diagnostic-store failure must not erase the baseline result.
            return


def _governance(ws, cfg):
    policy, registry = DecisionAcceptancePolicy(), ActiveCalibrationRegistry()
    if cfg.policy_path:
        content = json.loads(Path(cfg.policy_path).read_text())
        policy = DecisionAcceptancePolicy.model_validate(content["policy"])
        for raw in content.get("calibration_approvals", []):
            approval = DecisionCalibrationProfile.model_validate(raw)
            registry.decision_profiles[approval.key] = approval
    paused = bool(ws.get("coordination_paused", False))
    runtime_killed = False
    # Also respect the durable runtime kill switch when that control is used.
    from tpllm.persistence.models import JournalEventType
    for record in ws.db.journal.read_from(global_sequence=1):
        if record.event_type == JournalEventType.AUTONOMY_KILL_SWITCH_CHANGED and record.organization_id == ws.organization:
            runtime_killed = bool(record.payload.get("engaged"))
    registry.kill_switch_engaged = paused or runtime_killed
    return policy, registry


def _assess(ws, projection, *, cfg, at, force):
    store = JournalDecisionStore(ws.db.journal, ws.organization)
    policy, registry = _governance(ws, cfg)
    profiles = measured_profiles(registry=registry, traces=store.traces(), labels=store.labels(), at=at)
    provider = getattr(ws, "decision_provider", None) or TypeSafeJevProvider(cfg)
    provider_name = getattr(ws, "decision_provider_name", "typesafe")
    engine = DecisionEngine(providers={provider_name: provider}, registry=default_registry(), store=store,
        mode=cfg.mode, policy_gate=DecisionPolicyGate(policy), clock=lambda: at)
    observations = ws.get("collected_kpi_inputs", {}).get("observations", [])
    aliases = _evidence_aliases(projection["records"])
    observations = [{**o, "evidence_ids": [aliases.get(e, e) for e in o["evidence_ids"]]} for o in observations]
    source_knowledge = {r["evidence_id"]: r["collected_at"] for r in projection["records"]}
    # The deterministic extractor re-emits these same source assertions on
    # each pass. Their knowledge cutoff is when the underlying source arrived,
    # not the latest refresh time of a rebuildable projection.
    observations = [{**o, "reported_at": max(source_knowledge[e] for e in o["evidence_ids"])}
        for o in observations if o["evidence_ids"] and all(e in source_knowledge for e in o["evidence_ids"])]
    builder = DecisionStateBuilder(include_source_excerpts=cfg.include_source_excerpts)
    insights, trace_ids = [], []
    for kpi in projection["kpis"]:
        relevant = [r for r in projection["records"] if r["project_id"] == kpi["project_id"]]
        if not relevant:
            continue
        # A stable evidence cutoff avoids calling the model on every dashboard
        # read or collection tick. A new source version creates a new state.
        cutoff = max(utc(datetime.fromisoformat(r["collected_at"])) for r in relevant)
        state = builder.build(project_id=kpi["project_id"], entity_id=kpi["id"], valid_at=cutoff, known_at=cutoff,
            records=relevant, observations=observations, kpi=kpi, relationships=projection["relationships"])
        health = kpi["trend"]["health"]
        baseline = {"risk.present": health in {"watch", "breached", "critical"}} if health not in {"unknown", "stale"} else {}
        direction = str(kpi["trend"]["direction"]).lower()
        if direction in {"improving", "stable", "deteriorating"}:
            baseline["trend.direction"] = direction
        traces = engine.evaluate(provider=provider_name, model=cfg.model, state=state, profiles=profiles,
            baseline=baseline, kill_switch=registry.kill_switch_engaged, force=force)
        trace_ids.extend(t.id for t in traces)
        obs = [o for o in observations if o["id"] in state.observation_refs]
        # Adapt existing source claims to the ontology validator; do not turn
        # evidence IDs into invented claims or independent corroboration.
        claims = [EvidenceClaim(id=c["id"], confidence=c.get("confidence", 0),
            contradicted_by_claim_ids=[other for rec in kpi.get("reconciliation", [])
                if rec.get("status") == "contested" and c["id"] in rec.get("conflicting_claim_ids", [])
                for other in rec["conflicting_claim_ids"] if other != c["id"]])
            for c in kpi.get("claims", []) if c.get("evidence_ids")
                and {aliases.get(e, e) for e in c["evidence_ids"]} <= set(state.evidence_refs)]
        candidate = KPIInsight(id="bounded-insight-" + fingerprint([kpi["id"], state.state_hash])[:24],
            title=f"Review {kpi['name']}", summary=f"Existing KPI assessment: {health}. Inspect source claims and measurement coverage.",
            severity="high" if health == "critical" else "medium" if health in {"breached", "watch"} else "low",
            confidence=min((o["confidence"] for o in obs), default=0),
            related_kpi_observation_ids=[o["id"] for o in obs], supporting_evidence_claim_ids=[c.id for c in claims],
            affected_object_ids=[kpi["id"]], detected_at=utc(at).replace(tzinfo=None), human_review_required=True)
        ontology_obs = [KPIObservation(id=o["id"], kpi_definition_id=o["kpi_id"], value=o["value"],
            confidence=o["confidence"], observed_at=utc(datetime.fromisoformat(o["observed_at"])).replace(tzinfo=None)) for o in obs]
        validation = validate_insight(candidate, observations=ontology_obs, evidence_claims=claims)
        priority = "normal"
        contributors = []
        # The only advisory change is review ordering, never confidence,
        # validation, committed targets, authority, or execution inputs.
        for t in traces:
            if (validation.passed and cfg.mode == "advisory" and not registry.kill_switch_engaged and t.policy_evaluation.influence_allowed
                    and t.definition.decision_id in {"review.human_recommended", "evidence.contradictory"}
                    and t.result.probability >= policy.min_probability):
                priority = "review"
                contributors.append(t.id)
        recommendation = None
        if validation.passed:
            recommendation = AgentRecommendation(id=candidate.id + "-review", insight_id=candidate.id,
                title=candidate.title, recommendation_text="Review the linked source evidence and reconcile measurements before choosing an intervention.",
                recommendation_type="flag_for_review", approval_required=True,
                target_object_ids=[kpi["id"]], confidence=validation.adjusted_confidence,
                created_at=utc(at).replace(tzinfo=None), last_updated_at=utc(at).replace(tzinfo=None)).model_dump(mode="json")
        existing_effect = next((e for e in reversed(store.effects(project_id=state.project_id))
            if e["insight_id"] == candidate.id and e["trace_ids"] == [t.id for t in traces]), None)
        if existing_effect is None:
            store.effect(traces=traces, insight=candidate, validation=validation,
                recommendation=recommendation, priority=priority, at=at)
        current = (utc(at)-cutoff).total_seconds() <= policy.max_evidence_age_hours*3600
        if not current or registry.kill_switch_engaged:
            priority, contributors = "normal", []
        insights.append({"id": candidate.id, "project_id": state.project_id, "entity_id": state.entity_id,
            "title": candidate.title, "validation": existing_effect["validation"] if existing_effect else validation.model_dump(mode="json"),
            "recommendation": existing_effect["recommendation"] if existing_effect else recommendation,
            "priority": priority, "contributing_trace_ids": contributors, "trace_ids": [t.id for t in traces],
            "mode": cfg.mode, "current": current, "source_keys": [r["key"] for r in relevant],
            "explanation_origin": "Deterministic description of evidence and validation; not model reasoning."})
    ws.put("decision_intelligence", {"mode": cfg.mode, "insights": sorted(insights, key=lambda x: x["priority"] != "review"),
        "trace_ids": trace_ids, "error": None})


def snapshot(ws, *, at=None):
    store = JournalDecisionStore(ws.db.journal, ws.organization)
    projection = ws.get("decision_intelligence", {"mode": "off", "insights": [], "trace_ids": []})
    try:
        cfg = _settings(ws)
        mode = cfg.mode if cfg.active else "off"
    except ValueError:
        mode = "unavailable"
    # Historical acceptance never survives disabling the feature or pausing
    # coordination as a current advisory effect in the read projection.
    if mode != "advisory" or ws.get("coordination_paused", False):
        projection = {**projection, "mode": mode, "insights": [
            {**i, "priority": "normal", "contributing_trace_ids": []} for i in projection.get("insights", [])]}
    traces, labels = store.traces(), store.labels()
    if mode == "advisory":
        # Read-time revocation only: never grant new influence from a read or
        # call a provider. A changed approval/quality profile needs assessment.
        from datetime import timezone
        at = utc(at) if at else datetime.now(timezone.utc)
        allowed = set()
        try:
            policy, registry = _governance(ws, cfg)
            profiles = measured_profiles(registry=registry, traces=traces, labels=labels, at=at)
            for t in traces:
                profile = profiles.get(t.policy_evaluation.calibration_key)
                if (not registry.kill_switch_engaged and t.policy_evaluation.influence_allowed
                        and t.policy_parameters == policy.model_dump(mode="json")
                        and profile and profile == t.calibration_profile and profile.approved
                        and profile.expires_at and utc(profile.expires_at) > at
                        and (at-utc(t.evidence_cutoff)).total_seconds() <= policy.max_evidence_age_hours*3600):
                    allowed.add(t.id)
        except (ValueError, OSError, KeyError):
            pass
        projection = {**projection, "insights": [{**i,
            "contributing_trace_ids": [identity for identity in i.get("contributing_trace_ids", []) if identity in allowed],
            "priority": "review" if allowed.intersection(i.get("contributing_trace_ids", [])) else "normal"}
            for i in projection.get("insights", [])]}
    selected = set(projection.get("trace_ids", []))
    return {**projection, "traces": [t.model_dump(mode="json") for t in traces if t.id in selected],
        "telemetry": decision_telemetry(traces, labels), "calibration": quality_reports(traces, labels),
        "authority": "Bounded judgments cannot authorize or execute actions."}
