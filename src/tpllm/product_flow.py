"""Collected evidence -> relational context -> prepared coordination.

Deterministic extraction is labelled throughout. Collection, source-derived
state, human commitments and provider receipts have distinct ownership.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from tpllm.cli_support import now
from tpllm.integrations.envelopes import make_envelope, envelope_to_project_evidence
from tpllm.product_sources import SourceRecord, Collection, COLLECTORS, digest, utc
from tpllm.product_authority import eligible, execute_prepared


def source_ref(record):
    return {"key": record["key"], "version": record["version"]}


def current_records(ws):
    return list(ws.get("source_records", {}).values())


def accept_collection(ws, batch: Collection, *, at=None):
    at = at or now()
    current = ws.get("source_records", {})
    envelopes, updates = [], []
    for r in batch.records:
        if r.observed_at > at:
            batch.limitations.append(f"{r.source_id}: future source update excluded.")
            continue
        if current.get(r.key, {}).get("version") == r.version:
            continue
        value = {**r.model_dump(mode="json"), "key": r.key, "version": r.version, "collected_at": at.isoformat()}
        envelope = make_envelope(source_system=r.provider, source_type="meeting" if r.kind == "meeting_notes" else "document",
            source_id=r.key, event_type="meeting_transcript" if r.kind == "meeting_notes" else "architecture_evidence",
            observed_at=r.observed_at, received_at=at, project_ids=[r.project_id], title=r.title, content=r.text,
            source_pointer=r.url or None, idempotency_key=f"collected:{r.key}:{r.version}", metadata={"collected_record": value})
        value["evidence_id"] = envelope.id
        envelopes.append(envelope)
        # A late older version is retained in the journal without rolling back
        # current state. Equal-time distinct claims remain in history as well.
        old = current.get(r.key)
        if old is None or utc(old["observed_at"]) <= r.observed_at:
            updates.append(value)
    if envelopes:
        ws.process_envelopes(envelopes, at)
        for value in updates:
            current[value["key"]] = value
        ws.put("source_records", current)
    derive(ws, at=at)
    if envelopes:
        ws.analyze()
    return {"changed": len(envelopes), "records_seen": len(batch.records), "limitations": batch.limitations}


def recover_sources(ws):
    """Rebuild the source projection after a journal-before-projection crash."""
    current = {}
    for evidence in ws.evidence():
        value = evidence.get("metadata", {}).get("collected_record")
        if not value:
            continue
        value = {**value, "evidence_id": evidence["evidence_id"]}
        old = current.get(value["key"])
        if old is None or utc(old["observed_at"]) <= utc(value["observed_at"]):
            current[value["key"]] = value
    if current:
        ws.put("source_records", current)
    return current


def kpi_inputs(ws, records, at):
    from tpllm.intelligence.kpi import KPIDefinition, KPITarget, KPIObservation, KPIHealthStatus, assess_kpi_health
    from tpllm.intelligence.kpi_extraction import DeterministicKPIClaimExtractor
    from tpllm.intelligence.kpi_reconciliation import reconcile_kpi_claims, KPIReconciliationStatus
    from tpllm.intelligence.kpi_stewardship import assess_kpi_stewardship
    definitions, targets, observations, views = [], {}, [], []
    for pid in ws.store.list_programs():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", pid):
            continue
        artifacts = ws.artifacts(pid)
        program = artifacts.get("program", {})
        evidence = []
        for r in records:
            if r["project_id"] != pid:
                continue
            envelope = make_envelope(source_system=r["provider"], source_type="document", source_id=r["key"], event_type="supporting_evidence",
                observed_at=utc(r["observed_at"]), project_ids=[pid], content=r["text"], title=r["title"], received_at=utc(r["collected_at"]))
            item = envelope_to_project_evidence(envelope, project_id=pid)
            if item:
                item.source.id = r["evidence_id"]
                evidence.append(item)
        for metric in program.get("success_metrics", []):
            kid = pid + ":" + digest(metric["name"])
            unit = "%" if "%" in str(metric.get("target", "")) else "value"
            definition = KPIDefinition(id=kid, project_id=pid, canonical_name=metric["name"], description=program.get("goal", "Project outcome"),
                kpi_type="product", indicator_role="unknown", unit=unit, why_it_matters=program.get("goal") or "Declared project success criterion",
                expected_refresh_interval_seconds=86400, source_systems=sorted({r["provider"] for r in records if r["project_id"] == pid}), confidence=1)
            definitions.append(definition)
            match = re.fullmatch(r"\s*(?:>=?|at least)?\s*(\d+(?:\.\d+)?)\s*%?\s*", str(metric.get("target", "")), re.I)
            target = KPITarget(id=kid + ":committed", kpi_id=kid, operator="greater_than_or_equal", target_value=float(match[1]),
                effective_from=datetime(2000, 1, 1), rationale="Declared program target; conversation claims cannot overwrite it.", evidence_ids=[], confidence=1) if match else None
            if target:
                targets[kid] = target
            claims = DeterministicKPIClaimExtractor().extract_kpi_claims(project_id=pid, evidence=evidence, known_kpis=[definition])
            metric_obs = [KPIObservation(id=c.id + ":" + digest(c.evidence_ids), kpi_id=kid, value=c.parsed_numeric_value, unit=unit,
                observed_at=utc(c.observed_at), reported_at=at, source_system="communication_claim", source_id=c.evidence_ids[0],
                evidence_ids=c.evidence_ids, confidence=c.confidence) for c in claims if c.claim_type.value == "current_value" and c.parsed_numeric_value is not None]
            observations.extend(metric_obs)
            trend = assess_kpi_health(definition=definition, target=target, observations=metric_obs, profile=None, as_of=at)
            # A parsed statement cannot independently corroborate itself.
            reconciliation = reconcile_kpi_claims(claims=claims, known_kpis=[definition], observations=[], targets=[target] if target else [], as_of=at)
            value_claims = {c.id for c in claims if c.claim_type.value == "current_value"}
            for result in reconciliation:
                if value_claims.intersection(result.supporting_claim_ids) and result.status == KPIReconciliationStatus.CONFIRMED:
                    result.status = KPIReconciliationStatus.PARTIALLY_CONFIRMED
                    result.explanation = "The collected value statements agree, but no independent metric source has verified them."
                if value_claims.intersection(result.conflicting_claim_ids) and result.status == KPIReconciliationStatus.CONTESTED:
                    trend.latest_value = None
                    trend.health = KPIHealthStatus.UNKNOWN
                    trend.direction = "Unknown — conflicting source claims"
            if len({o.observed_at for o in metric_obs}) < 2:
                trend.direction = "Unknown — insufficient observations"
            stewardship = assess_kpi_stewardship(definition=definition, trend=trend, plan=None, as_of=at)
            views.append({"id": kid, "project_id": pid, "name": metric["name"], "unit": unit, "committed_target": metric.get("target", "Unknown"),
                "meaning": definition.why_it_matters, "trend": trend.model_dump(mode="json"), "response": stewardship.model_dump(mode="json"),
                "claims": [c.model_dump(mode="json") for c in claims], "reconciliation": [r.model_dump(mode="json") for r in reconciliation],
                "measurement_basis": "Extracted source claims; instrumentation has not independently verified these values.",
                "source_keys": [r["key"] for r in records if r["project_id"] == pid and metric["name"].lower() in r["text"].lower()]})
    ws.put("collected_kpi_inputs", {"definitions": [d.model_dump(mode="json") for d in definitions],
        "observations": [o.model_dump(mode="json") for o in observations], "targets": {k: t.model_dump(mode="json") for k, t in targets.items()}})
    return views


def relations(records):
    edges = []
    for r in records:
        data = r["data"]
        # Source roles remain typed; authorship is never upgraded to authority.
        for role, actors in (("authored", [data.get("author")]), ("assigned", data.get("assignees", [])),
                             ("review_requested", data.get("requested_reviewers", [])), ("organizes", [data.get("organizer")])):
            for actor in filter(None, actors):
                edges.append({"project_id": r["project_id"], "from": f"{r['provider']}:{actor}", "relation": role, "to": r["key"], "label": r["title"],
                    "basis": "explicit source field", "source_keys": [r["key"]]})
        for other in records:
            if r["key"] == other["key"] or r["project_id"] != other["project_id"]:
                continue
            # Exact source links, thread IDs, or title mentions. Topic similarity
            # alone never establishes a dependency or an identity merge.
            linked = other["url"] and other["url"] in r["text"]
            named = len(other["title"]) > 14 and other["title"].casefold() in r["text"].casefold()
            if linked or named:
                edges.append({"project_id": r["project_id"], "from": r["key"], "relation": "references", "to": other["key"], "label": other["title"],
                    "basis": "source URL" if linked else "explicit title mention; identity remains provisional", "source_keys": [r["key"], other["key"]]})
    return edges


def engineering(records):
    result = []
    for r in records:
        if r["kind"] != "pull_request":
            continue
        data = r["data"]
        related = [i for i in records if i["project_id"] == r["project_id"] and i["kind"] == "issue"
                   and (r["url"] and r["url"] in i["text"] or i["url"] and i["url"] in r["text"])]
        current_reviews = [v for v in data.get("reviews", []) if v.get("commit") == data.get("head")]
        current_checks = [c for c in data.get("checks", []) if c.get("head_sha") == data.get("head")]
        failed = [c for c in current_checks if c.get("conclusion") in {"failure", "timed_out", "action_required", "cancelled"}]
        open_bugs = [i for i in related if i["data"].get("state") == "open" and (i["data"].get("bug_bash") or i["data"].get("is_bug"))]
        reasons = []
        if data.get("state") == "open": reasons.append("Implementation is still in an open PR.")
        if any(v.get("state") == "CHANGES_REQUESTED" for v in current_reviews): reasons.append("Changes requested on the current revision.")
        if failed: reasons.append("Checks failed on the current revision.")
        if open_bugs:
            label = "bug-bash" if all(i["data"].get("bug_bash") for i in open_bugs) else "bug"
            reasons.append(f"{len(open_bugs)} linked {label} finding(s) remain open.")
        if not current_checks: reasons.append("No check evidence for the current revision.")
        elif any(c.get("status") != "completed" or not c.get("conclusion") for c in current_checks): reasons.append("Checks have not completed on the current revision.")
        if data.get("limitations"): reasons.append("Collected technical evidence is incomplete.")
        result.append({**r, "linked_issues": [i["key"] for i in related], "delivery_signal": "Needs resolution" if reasons else "Technical evidence updated",
            "estimate_basis": reasons or ["No blocking signal in this collected slice; delivery readiness still requires product and approval evidence."],
            "estimate": "No calibrated completion date", "current_reviews": current_reviews})
    return result


def sync_commitments(ws, records):
    from tpllm.workflows.task_board_engine import TaskBoardEngine
    engine = TaskBoardEngine(ws.store)
    links = ws.get("collected_task_links", {})
    changed = 0
    for r in records:
        # Mirror an existing assigned issue; never invent a new assignment.
        if r["kind"] != "issue":
            continue
        old = links.get(r["key"])
        if not old and not r["data"].get("assignees"):
            continue
        if old and old["version"] == r["version"]:
            continue
        if old:
            task_id = old["task_id"]
            board = engine._load(r["project_id"])
            task = board.get_task(task_id)
            if task:
                from tpllm.models.task_board import TaskStatus
                task.title, task.description = r["title"], r["text"]
                task.assignee = ", ".join(r["data"].get("assignees", []))
                task.source_context = r["text"]
                task.status = TaskStatus.DONE if r["data"].get("state") == "closed" else TaskStatus.TODO
                task.completed_at = utc(r["observed_at"]) if task.status == TaskStatus.DONE else None
                task.updated_at = now()
                engine._save(r["project_id"], board)
        else:
            task = engine.create_task(r["project_id"], r["title"], description=r["text"], assignee=", ".join(r["data"]["assignees"]),
                source="GITHUB_ISSUE", source_id=r["key"], source_context=r["text"], confidence=1,
                priority_rationale="Imported source commitment; priority has not been inferred from activity volume.")
            task_id = task.id
            if r["data"].get("state") == "closed":
                engine.update_task(r["project_id"], task_id, status="DONE")
        links[r["key"]] = {"version": r["version"], "task_id": task_id}
        changed += 1
    ws.put("collected_task_links", links)
    imported = ws.get("collected_notes", {})
    for r in records:
        if r["kind"] != "meeting_notes" or imported.get(r["key"]) == r["version"]:
            continue
        before = engine._load(r["project_id"])
        known = {(t.source_id, t.title.casefold(), t.assignee.casefold()) for t in before.tasks}
        created = engine.extract_tasks_from_zoom_notes(r["project_id"], meeting_id=r["key"], raw_notes=r["text"], meeting_topic=r["title"])
        board = engine._load(r["project_id"])
        duplicates = {t.id for t in created if (t.source_id, t.title.casefold(), t.assignee.casefold()) in known}
        board.tasks = [t for t in board.tasks if t.id not in duplicates]
        engine._save(r["project_id"], board)
        changed += len(created) - len(duplicates)
        imported[r["key"]] = r["version"]
    ws.put("collected_notes", imported)
    return changed


def derive(ws, *, at=None):
    at = at or now()
    records = current_records(ws)
    kpis = kpi_inputs(ws, records, at)
    edges = relations(records)
    prs = engineering(records)
    meetings, actions, briefs = [], [], []
    for r in [r for r in records if r["kind"] == "meeting" and r["data"].get("status") != "cancelled"]:
        related_keys = {e["from"] for e in edges if e["to"] == r["key"]}
        context = [x for x in records if x["key"] in related_keys]
        requests = [x for x in context if x["kind"] == "conversation" and re.search(r"launch date|status update|rollout status", x["text"], re.I)]
        focused = bool(re.search(r"engineering|design review|technical review", r["title"], re.I))
        # A technical title does not establish that a launch question is out of
        # scope. Automatic routing also needs an explicit boundary in the agenda.
        agenda_boundary = bool(re.search(r"(?:launch|rollout|status).{0,35}(?:belongs in|separate|outside|out of scope|asynchron)", r["text"], re.I))
        start = r["data"].get("start", {}).get("dateTime")
        try:
            upcoming = bool(start and utc(start) > at)
        except (ValueError, TypeError):
            upcoming = False
        supported_routing = focused and requests and agenda_boundary and upcoming
        project_prs = [p for p in prs if p["project_id"] == r["project_id"]]
        source_records = [r] + context + [x for x in records if x["project_id"] == r["project_id"] and x["kind"] in {"pull_request", "issue", "meeting_notes"}]
        source_records = list({s["key"]: s for s in source_records}.values())
        recommendation = "Review the collected context"
        reason = "Insufficient evidence to decide whether synchronous discussion is needed."
        if supported_routing:
            recommendation = "Keep the design review focused; answer the launch update asynchronously"
            reason = "A launch/status request explicitly references this technical review, whose agenda assigns launch status to a separate channel. The collected delivery evidence supports a shorter update there."
        elif focused and requests:
            recommendation = "Consider an asynchronous status update"
            reason = "The request references this technical review, but its title alone does not establish the agenda boundary. Review relevance before routing a message."
        if not upcoming:
            recommendation = "Retain meeting context"
            reason = "The source does not establish a future timed meeting. No automatic agenda intervention is justified."
        notes = [s for s in source_records if s["kind"] == "meeting_notes"]
        signals = [p["title"] + ": " + " ".join(p["estimate_basis"]) for p in project_prs]
        body = "Project update: " + r["project_id"] + "\n\n" + "\n".join(signals or ["The collected sources do not yet establish delivery readiness."])
        body += "\n\nThe committed project target has not changed. "
        body += "Keep the technical review focused on its existing agenda. Resolve the launch/status question in this thread using the linked evidence."
        if notes:
            body += "\n\nAI meeting context is available in the project evidence; it does not establish launch approval."
        links = [s["url"] for s in source_records if s["url"]]
        if links: body += "\n\nSources:\n" + "\n".join(dict.fromkeys(links))
        mid = digest(r["key"])
        meetings.append({"id": mid, "project_id": r["project_id"], "title": r["title"], "source_key": r["key"],
            "start": r["data"].get("start"), "recommendation": recommendation, "reason": reason,
            "assessment": "inferred" if supported_routing else "unknown", "source_keys": [s["key"] for s in source_records],
            "draft": body if requests else None, "calendar_action": "No calendar change proposed by this assessment."})
        if focused and requests:
            destination = requests[-1]["scope"]
            evidence_versions = [source_ref(s) for s in source_records]
            coverage_gap = any(ws.get("collection:" + s["provider"], {}).get("status") in {"partial", "unavailable", "needs_setup"} for s in source_records)
            action = {"id": "update:" + digest([r["key"], evidence_versions]), "project_id": r["project_id"], "type": "internal_update",
                "provider": "slack", "destination": destination, "purpose": "project_delivery_status", "title": "Send the prepared async update", "body": body,
                "source_versions": evidence_versions, "reserved": False, "uncertainty": not supported_routing or coverage_gap or any(p["data"].get("limitations") for p in project_prs)}
            grant, rationale = eligible(ws, action, at)
            action.update(status="ready" if grant else "held", authority_reason=rationale, grant_id=grant.id if grant else None)
            receipt = next((v for v in reversed(ws.get("action_receipts", [])) if v["action_id"] == action["id"]), None)
            if receipt: action.update(status=receipt["status"], receipt=receipt)
            actions.append(action)
    for pid in sorted({r["project_id"] for r in records}):
        project_records = [r for r in records if r["project_id"] == pid]
        project_prs = [p for p in prs if p["project_id"] == pid]
        project_kpis = [k for k in kpis if k["project_id"] == pid]
        briefs.append({"project_id": pid, "updated_at": max(r["collected_at"] for r in project_records),
            "summary": "\n".join([f"{p['title']}: {' '.join(p['estimate_basis'])}" for p in project_prs] +
                [f"{k['name']}: {k['trend']['health']}; committed target {k['committed_target']}." for k in project_kpis]) or "Source context collected; no delivery conclusion is supported yet.",
            "source_keys": [r["key"] for r in project_records], "method": "Automatically maintained from source records; deterministic synthesis."})
    changed_tasks = sync_commitments(ws, records)
    projection = {"at": at.isoformat(), "records": records, "kpis": kpis, "engineering": prs, "relationships": edges,
        "meetings": meetings, "actions": actions, "briefs": briefs, "method": "Deterministic extraction and structural inference; no calibrated delivery prediction."}
    ws.put("coordination", projection)
    if changed_tasks:
        ws.audit("Source commitments synchronized", {"updated": changed_tasks}, actor="tpllm")
    return projection


def cycle(ws, *, collectors=None, force=False, at=None):
    at = at or now()
    collectors = collectors or COLLECTORS
    reports = []
    with ws.lock:
        if ws.closed:
            return {}
        collection_settings = ws.get("collection_settings", {})
    if not ws.demo:
        for provider, settings in collection_settings.items():
            with ws.lock:
                if ws.closed:
                    return {}
                if not settings.get("enabled"):
                    continue
                last = ws.get("collection:" + provider, {})
                if not force and last.get("attempted_at") and (at - utc(last["attempted_at"])).total_seconds() < settings.get("interval_seconds", 300):
                    continue
                mapping = ws.get("connection:" + provider, {})
                if not mapping.get("scope") or not mapping.get("program_id") or not getattr(ws.cfg, provider).enabled:
                    ws.put("collection:" + provider, {"status": "needs_setup", "attempted_at": at.isoformat(), "limitations": ["Credentials and an authorized source mapping are required."]})
                    continue
                ws.put("collection:" + provider, {**last, "status": "collecting", "attempted_at": at.isoformat()})
            try:
                batch = collectors[provider](getattr(ws.cfg, provider), mapping["scope"], mapping["program_id"])
                with ws.lock:
                    if ws.closed:
                        return {}
                    if ws.get("connection:" + provider) != mapping or not ws.get("collection_settings", {}).get(provider, {}).get("enabled"):
                        continue
                    result = accept_collection(ws, batch, at=now())
                    report = {**result, "status": "partial" if batch.limitations else "current", "attempted_at": at.isoformat(), "last_success": now().isoformat()}
                    ws.put("collection:" + provider, report)
            except Exception as exc:
                with ws.lock:
                    if ws.closed:
                        return {}
                    report = {"status": "unavailable", "attempted_at": at.isoformat(), "last_success": last.get("last_success"),
                        "limitations": [f"Source read failed ({type(exc).__name__}); last accepted evidence retained."]}
                    ws.put("collection:" + provider, report)
            reports.append(report)
    with ws.lock:
        if ws.closed:
            return {}
        derive(ws, at=at)
        for action in ws.get("coordination", {}).get("actions", []):
            if action["status"] == "ready":
                execute_prepared(ws, action, at=now())
        derive(ws, at=at)
        ws.put("coordination_cycle", {"at": at.isoformat(), "reports": reports})
        return ws.get("coordination")


def snapshot(ws):
    data = ws.get("coordination", {})
    return {**data, "collection": [{"provider": p, "configured": not ws.demo and getattr(ws.cfg, p).enabled,
        "mapping": ws.get("connection:" + p, {}), "settings": ws.get("collection_settings", {}).get(p, {}),
        "report": ws.get("collection:" + p, {})} for p in COLLECTORS],
        "delegations": ws.get("delegations", []), "receipts": ws.get("action_receipts", []),
        "paused": ws.get("coordination_paused", False), "last_cycle": ws.get("coordination_cycle"),
        "reserved": ["Committed scope or launch dates", "Customer and partner promises", "Security and access", "Legal, personnel and material spending"],
        "execution_supported": ["slack.internal_update"], "pending_adapters": ["External work-item changes", "Calendar modifications", "Resource requests", "Operational decision finalization"]}
