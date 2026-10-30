"""Synthetic source events for testing the actual collection-to-action loop."""
from datetime import timedelta

from tpllm.cli_support import now
from tpllm.product_sources import SourceRecord, Collection
from tpllm.product_flow import accept_collection, cycle
from tpllm.product_authority import Delegation


def seed_projects(ws):
    """One fictional AI feature, with its model, data and cloud workstreams."""
    if ws.get("seeded"):
        return
    from tpllm.artifacts.decision_log import DecisionLogManager
    from tpllm.artifacts.raid_log import RAIDLogManager
    from tpllm.artifacts.milestone_plan import MilestonePlanManager
    from tpllm.models.program import Program, SuccessMetric, Stakeholder, ProgramHealth
    from tpllm.workflows.task_board_engine import TaskBoardEngine

    people = [Stakeholder(name=n, role=r, team=t) for n, r, t in [
        ("Local operator", "Release decision owner", "Product engineering"),
        ("Alex Chen", "Computer vision engineer", "Vision model"),
        ("Riley Shah", "Data package owner", "Data & evaluations"),
        ("Jamie Park", "Evaluation lead", "Data & evaluations"),
        ("Sam Rivera", "Cloud engineer", "Web platform"),
    ]]
    metrics = [SuccessMetric(name=n, target=t, current=c, measurement_method=m) for n, t, c, m in [
        ("Request match accuracy", "95%", "89%", "Synthetic held-out photo + user-request cases; correct object AND requested attributes/exclusions."),
        ("Recognition accuracy", "98%", "96%", "Synthetic labeled object cases, including visually similar backpacks and handbags."),
        ("Package alignment", "100%", "75%", "Synthetic release check: matching model, catalog data, eval suite and cloud serving manifest; 3 of 4 currently align."),
    ]]
    projects = [
        ("visual-search", "AI visual search", "Add photo + text search to a cloud-hosted shopping website. A shopper asks 'Find a red backpack, not a handbag'; results must recognize the object and respect that request.", "YELLOW"),
        ("vision-model", "Vision model & inference", "Integrate vision model v0.8.2 with the website's inference API. Preserve object labels, requested attributes and exclusions; return uncertainty when the image is insufficient.", "YELLOW"),
        ("data-evals", "Data packages & evaluations", "Align catalog-data v3.2, label schema v3.2 and request-evals v2.1. Test look-alike objects, negated requests, poor lighting and unknown items on a held-out set.", "YELLOW"),
        ("cloud-serving", "Cloud serving & observability", "Serve the feature behind the existing website API using a versioned inference container and object storage. Verify package parity, latency, error monitoring and rollback before a canary release.", "YELLOW"),
    ]
    for pid, name, goal, health in projects:
        ws.store.save(pid, "program", Program(id=pid, name=name, goal=goal, health=ProgramHealth(health),
            stakeholders=people, success_metrics=metrics if pid == "visual-search" else [],
            non_goals=["Training a general-purpose foundation model", "Rebuilding the website's hosting platform"]))
        ws.put(f"setup:{pid}", {"authority": "local-operator", "scope": goal, "permission": "Human review", "outlook_confirmed": True,
            "health_rationale": "Synthetic release evidence: cloud data package mismatch and incomplete request evaluations remain open."})
    decision = DecisionLogManager(ws.store).propose_decision("visual-search", "Align the model, data, and eval release bundle",
        "Vision model v0.8.2 was evaluated with catalog-data v3.2 and request-evals v2.1, but cloud staging still serves data v3.1. Object recognition alone does not prove results obey the shopper's request. The team needs one reproducible release bundle and a full held-out evaluation before beta. These are synthetic demo observations.",
        [{"label": "Pin one bundle and re-run the full request evals", "pros": ["Model, data and serving results become comparable", "Preserves the approved acceptance gate"], "cons": ["Beta waits for package parity and the complete evaluation"]},
         {"label": "Wait for the expanded dataset", "pros": ["Adds more look-alike and exclusion examples"], "cons": ["Requires a new versioned baseline and more evaluation time"]}])
    ws.put(f"decision-meta:{decision.id}", {"recommendation": "Pin one bundle and re-run the full request evals", "basis": "Synthetic demo recommendation; not an executed deployment decision", "projects": [p[0] for p in projects]})
    engine = TaskBoardEngine(ws.store)
    for pid, title, owner, status in [
        ("visual-search", "Agree the request-accuracy gate for beta", "Jamie Park", "BLOCKED"),
        ("vision-model", "Preserve requested object type and exclusions", "Alex Chen", "IN_PROGRESS"),
        ("data-evals", "Freeze the held-out photo and request evaluation set", "Riley Shah", "IN_REVIEW"),
        ("cloud-serving", "Match the cloud serving manifest to the evaluated bundle", "Sam Rivera", "BLOCKED"),
    ]:
        task = engine.create_task(pid, title, description="Synthetic engineering commitment in the AI visual search release.", assignee=owner, priority="HIGH" if status == "BLOCKED" else "MEDIUM")
        engine.update_task(pid, task.id, status=status)
    raid = RAIDLogManager(ws.store)
    for upstream, title, owner in [
        ("Vision model & inference", "Model and preprocessing must use the same label schema as the data package", "Alex Chen"),
        ("Data packages & evaluations", "One held-out eval package must test object recognition and the full user request", "Jamie Park"),
        ("Cloud serving & observability", "The cloud container must serve the exact evaluated model/data/eval bundle", "Sam Rivera"),
    ]:
        raid.add_dependency("visual-search", title, upstream_team=upstream, downstream_team="AI visual search", owner=owner)
    raid.add_risk("visual-search", "Correct object recognition can still violate the user's request",
        description="A red handbag may resemble a red backpack. Color similarity must not override the requested object type or the explicit exclusion.",
        severity="HIGH", impact="Users receive plausible but unwanted results.", mitigation="Evaluate photo + request pairs, hard negatives and abstention on uncertain images.", owner="Jamie Park")
    plan = MilestonePlanManager(ws.store)
    for title, description, owner in [
        ("Freeze the model, data and eval bundle", "Pin vision v0.8.2, catalog-data v3.2, label schema v3.2 and request-evals v2.1; record the versions in the cloud manifest.", "Riley Shah"),
        ("Pass the full user-request evaluation", "Meet the approved request-match and recognition gates on the held-out set, including exclusions and unknown objects. A single regression fix is insufficient.", "Jamie Park"),
        ("Verify the cloud canary", "Deploy the evaluated bundle to staging, check latency and errors, and verify rollback before broad release.", "Sam Rivera"),
    ]:
        milestone = plan.add_milestone("visual-search", title, description=description, owner=owner, on_critical_path=True)
        plan.update_milestone_status("visual-search", milestone.id, "AT_RISK")
    stamp = (now() - timedelta(hours=1)).isoformat()
    events = [
        ("github", "release-bundle", "architecture_evidence", [p[0] for p in projects], "Shared AI release contract", "The website, vision model, data package and held-out eval suite must identify the same release bundle. Vision v0.8.2 uses catalog-data v3.2, label schema v3.2 and request-evals v2.1."),
        ("github", "EVAL-73", "blocker_opened", ["visual-search", "vision-model", "data-evals"], "Request eval and serving package disagree", "Cloud staging still uses catalog-data v3.1. The bug bash returned a handbag for 'Find a red backpack, not a handbag'. The release gate is not met."),
        ("gdrive", "release-brief", "architecture_evidence", ["visual-search"], "AI feature release brief", "The approved Request match accuracy gate is 95%. Recognition accuracy must reach 98%; Package alignment must reach 100%."),
        ("meeting", "eval-review-notes", "meeting_transcript", ["visual-search", "data-evals"], "Evaluation review notes", "Review notes propose a 97% Request match accuracy gate. The release owner has not approved a change from 95%."),
        ("github", "cloud-smoke", "architecture_evidence", ["cloud-serving"], "Cloud website staging check", "The existing website and upload API pass health checks. The inference container still needs catalog-data v3.2; healthy infrastructure does not establish AI answer quality."),
    ]
    ws.ingest([{"source_system": s, "source_id": i, "event_type": t, "project_ids": ids,
                "title": title, "content": content, "observed_at": stamp} for s, i, t, ids, title, content in events])
    ws.put("clarifications", [{"id": "request-accuracy-target", "program_id": "visual-search", "metric_name": "Request match accuracy",
        "title": "Which request-accuracy gate is approved?", "found": "The release brief says 95%. Evaluation notes propose 97%.",
        "impact": "Your answer records the authorized beta gate; it does not change the observed evaluation result.",
        "options": ["95%", "97%", "Not sure"], "status": "open", "source_ids": ["release-brief", "eval-review-notes"]}])
    ws.put("seeded", True)


def seed_coordination(ws):
    if ws.get("coordination_demo_seeded"):
        return
    stamp = now() - timedelta(minutes=10)
    specs = [
        dict(provider="github", scope="example/visual-search", source_id="pr:42", kind="pull_request", title="Align vision inference with the data and request-eval packages",
            text="Pin vision v0.8.2, catalog-data v3.2, label schema v3.2 and request-evals v2.1. Preserve the shopper's requested object type and exclusions in the website API. Cloud staging currently serves data v3.1. Bug-bash finding: https://github.com/example/visual-search/issues/73",
            url="https://github.com/example/visual-search/pull/42", data={"number": 42, "state": "open", "author": "alex", "assignees": ["alex"],
                "requested_reviewers": ["jamie"], "requested_teams": ["vision-and-evals"], "head": "demo-head-a", "base": "demo-base",
                "files": [
                    {"filename": "web/api/visual_search.py", "status": "modified", "additions": 42, "deletions": 11, "patch": "@@ synthetic request-filter example @@\n+ candidates = recognize(image, model=bundle.model)\n+ results = enforce_request(candidates, object_type=request.object_type, exclusions=request.exclusions)\n+ return clarify_if_uncertain(results)"},
                    {"filename": "packages/release-bundle.json", "status": "modified", "additions": 4, "deletions": 2, "patch": '- "catalog_data": "3.1"\n+ "vision_model": "0.8.2"\n+ "catalog_data": "3.2"\n+ "label_schema": "3.2"\n+ "request_evals": "2.1"'},
                    {"filename": "evals/request_cases.jsonl", "status": "modified", "additions": 18, "deletions": 0, "patch": '+ {"request": "Find a red backpack, not a handbag", "expected": "backpack", "excluded": ["handbag"], "split": "regression"}'},
                ],
                "reviews": [{"reviewer": "jamie", "state": "CHANGES_REQUESTED", "commit": "demo-head-a"}],
                "checks": [{"name": "release-bundle-contract", "status": "completed", "conclusion": "failure", "head_sha": "demo-head-a"},
                           {"name": "request-exclusion-regression", "status": "completed", "conclusion": "failure", "head_sha": "demo-head-a"}],
                "limitations": [], "code_ownership": "Source author and assignee identified; CODEOWNERS not resolved."}),
        dict(provider="github", scope="example/visual-search", source_id="issue:73", kind="issue", title="Bug bash: a handbag is returned for a backpack-only request",
            text="Photo + request: 'Find a red backpack, not a handbag'. Staging returns a red handbag despite the exclusion. Reproduced with the older cloud data package; regression case requires an object-type and exclusion check. Related implementation: https://github.com/example/visual-search/pull/42",
            url="https://github.com/example/visual-search/issues/73", data={"number": 73, "state": "open", "labels": ["bug", "bug-bash", "request-fidelity"], "bug_bash": True, "assignees": ["alex"]}),
        dict(provider="calendar", scope="engineering-demo", source_id="design-review", kind="meeting", title="Visual search engineering review",
            text="Resolve model preprocessing, label-schema parity, data package versions and user-request evaluation cases. PR https://github.com/example/visual-search/pull/42. Launch status belongs in the launch coordination channel.",
            data={"start": {"dateTime": (stamp + timedelta(hours=2)).isoformat() + "Z"}, "status": "confirmed", "organizer": "engineering@example.test", "organizer_self": False,
                "attendees": ["alex@example.test", "riley@example.test", "jamie@example.test", "sam@example.test"]}),
        dict(provider="slack", scope="visual-search-launch-demo", source_id="launch-question", kind="conversation", title="AI website feature launch status",
            text="Can we use the Visual search engineering review for a launch date update? The website feature depends on https://github.com/example/visual-search/pull/42 and the full request-evals run.",
            data={"author": "program-coordination", "thread": "launch-question", "channel": "visual-search-launch-demo"}),
        dict(provider="zoom", scope="engineering@example.test", source_id="review-notes-auto", kind="meeting_notes", title="AI visual search readiness · AI meeting summary",
            text="Request match accuracy is 89%. Target is 97% before beta. The approved release brief still records 95%; this gate needs the release owner's decision.\nRecognition accuracy is 96% on labeled object cases.\nPackage alignment is 75%: the model, data and eval references agree, but the fourth artifact, the cloud serving manifest, still points to data v3.1.\nThese are synthetic results from the last complete held-out run, not a production measurement. Recognition alone does not prove the answer follows the requested attributes and exclusions.\nAlex will fix the backpack versus handbag exclusion case. Riley will freeze the catalog data and label schema. Jamie will run the full held-out request evaluation. Sam will align the cloud inference container with the evaluated bundle. No beta release approval was recorded.",
            data={"meeting_id": "demo-ai-readiness", "next_steps": [], "evaluation_bundle": {"model": "vision-0.8.2", "data": "catalog-data-3.2", "labels": "3.2", "eval_suite": "request-evals-2.1", "cloud_data": "catalog-data-3.1"}}),
    ]
    scopes = []
    batch = Collection()
    for spec in specs:
        spec["data"].update(synthetic=True, observation_basis="simulated_observation")
        record = SourceRecord(project_id="visual-search", observed_at=stamp, **spec)
        batch.records.append(record)
        scopes.append(f"{record.provider}:{record.scope}")
        ws.put("connection:" + record.provider, {"scope": record.scope, "program_id": "visual-search"})
        ws.put("collection:" + record.provider, {"status": "demo", "last_success": now().isoformat(), "limitations": ["Synthetic source events; no external account accessed."]})
    grant = Delegation(project_id="visual-search", destinations=["visual-search-launch-demo"], source_scopes=sorted(set(scopes)),
        purpose="Share source-backed internal launch status and preserve the technical review agenda.", expires_at=now() + timedelta(days=7), granted_by="demo-admin")
    ws.put("delegations", [grant.model_dump(mode="json")])
    accept_collection(ws, batch)
    cycle(ws)
    ws.put("coordination_demo_seeded", True)


def advance_coordination(ws):
    records = ws.get("source_records", {})
    if ws.get("coordination_demo_advanced"):
        return {"already_applied": True}
    stamp = now()
    batch = Collection()
    for raw in records.values():
        r = SourceRecord.model_validate(raw)
        if r.provider == "github" and r.kind == "pull_request":
            r.data.update(state="merged", head="demo-head-b", reviews=[{"reviewer": "jamie", "state": "APPROVED", "commit": "demo-head-b"}],
                checks=[{"name": "release-bundle-contract", "status": "completed", "conclusion": "success", "head_sha": "demo-head-b"},
                        {"name": "request-exclusion-regression", "status": "completed", "conclusion": "success", "head_sha": "demo-head-b"},
                        {"name": "full-held-out-request-evals", "status": "queued", "conclusion": None, "head_sha": "demo-head-b"}])
            r.text += "\nThe bundle contract and the backpack-exclusion regression pass on this revision. The full held-out eval run and cloud canary are still pending; merging this PR does not update the deployed package or prove release readiness."
            r.observed_at = stamp
            batch.records.append(r)
        elif r.provider == "github" and r.kind == "issue":
            r.data["state"] = "closed"
            r.text += "\nRe-tested the backpack-only request against the pinned candidate bundle; this handbag false match no longer reproduces. The full held-out request evaluation and cloud deployment remain pending."
            r.observed_at = stamp
            batch.records.append(r)
    result = accept_collection(ws, batch, at=stamp)
    report = ws.get("collection:github", {})
    ws.put("collection:github", {**report, "last_success": stamp.isoformat()})
    cycle(ws, at=stamp)
    ws.put("coordination_demo_advanced", True)
    return result
