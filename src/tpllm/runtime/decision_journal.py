"""Decision persistence adapter over the existing immutable hash chain."""
from __future__ import annotations

from datetime import datetime

from tpllm.decisions.models import CalibrationLabel, DecisionTrace, utc
from tpllm.persistence.journal import build_journal_record
from tpllm.persistence.models import JournalEventType


class JournalDecisionStore:
    def __init__(self, journal, organization_id):
        self.journal, self.organization_id = journal, organization_id
        self.stream = f"bounded-decisions:{organization_id}"
        first = journal.read_from(global_sequence=1, limit=1)
        # The product adapter journals naive UTC; the durable runtime also
        # supports aware UTC. Preserve the containing journal's convention so
        # existing replay clocks can compare event timestamps without mixing.
        self._naive_utc = bool(first and first[0].recorded_at.tzinfo is None)

    def _append(self, event_type, identity, project_id, occurred_at, recorded_at, payload, evidence_refs):
        occurred_at, recorded_at = utc(occurred_at), utc(recorded_at)
        if self._naive_utc:
            occurred_at, recorded_at = occurred_at.replace(tzinfo=None), recorded_at.replace(tzinfo=None)
        self.journal.append(record=build_journal_record(event_type=event_type, stream_id=self.stream,
            organization_id=self.organization_id, project_ids=[project_id], occurred_at=occurred_at,
            recorded_at=recorded_at, source_key=identity, payload=payload, evidence_ids=list(evidence_refs)))

    def append(self, trace: DecisionTrace) -> None:
        if trace.supersedes:
            previous = self.get(trace.supersedes)
            if (previous is None or previous.project_id != trace.project_id or previous.entity_id != trace.entity_id
                    or previous.created_at > trace.created_at):
                raise ValueError("Invalid decision lineage")
        self._append(JournalEventType.BOUNDED_DECISION_RECORDED, trace.id, trace.project_id,
            trace.temporal.valid_from, trace.temporal.known_from, {"trace": trace.model_dump(mode="json")}, trace.evidence_refs)

    def traces(self, *, project_id=None, known_at=None, valid_at=None):
        rows = []
        for r in self.journal.read_stream(stream_id=self.stream):
            if r.event_type != JournalEventType.BOUNDED_DECISION_RECORDED:
                continue
            if project_id and project_id not in r.project_ids:
                continue
            t = DecisionTrace.model_validate(r.payload["trace"])
            if known_at and utc(t.temporal.known_from) > utc(known_at):
                continue
            if valid_at and utc(t.temporal.valid_from) > utc(valid_at):
                continue
            rows.append(t)
        return rows

    def get(self, trace_id):
        return next((t for t in self.traces() if t.id == trace_id), None)

    def label(self, label: CalibrationLabel):
        trace = self.get(label.trace_id)
        if trace is None or trace.result is None:
            raise ValueError("A label needs an available recorded judgment")
        q, v = trace.definition, label.value
        if (q.question_type == "noul" and type(v) is not bool
                or q.question_type == "choice" and v not in q.criteria
                or q.question_type == "score" and (type(v) not in (int, float) or not 0 <= v <= len(q.criteria)-1)):
            raise ValueError("Label does not match decision definition")
        if utc(label.recorded_at) < utc(label.observed_at) or utc(label.observed_at) < utc(trace.created_at):
            raise ValueError("Label cannot predate its prediction or be known before observation")
        existing = [l for l in self.labels() if l.trace_id == trace.id]
        if existing and label.supersedes != existing[-1].id:
            raise ValueError("Corrections must supersede the latest label")
        if not existing and label.supersedes:
            raise ValueError("No label to supersede")
        if not label.reviewer.strip() or not label.evidence_refs:
            raise ValueError("Labels require reviewer and outcome evidence")
        self._append(JournalEventType.BOUNDED_DECISION_LABELLED, label.id, trace.project_id,
            label.observed_at, label.recorded_at, {"label": label.model_dump(mode="json")}, label.evidence_refs)

    def labels(self, *, known_at=None):
        return [CalibrationLabel.model_validate(r.payload["label"]) for r in self.journal.read_stream(stream_id=self.stream)
                if r.event_type == JournalEventType.BOUNDED_DECISION_LABELLED
                and (known_at is None or utc(r.recorded_at) <= utc(known_at))]

    def effect(self, *, traces, insight, validation, recommendation, at: datetime, priority="normal"):
        if not traces:
            return
        # Structured downstream record with no execution payload. Append first.
        payload = {"trace_ids": [t.id for t in traces], "insight_id": insight.id,
            "insight": insight.model_dump(mode="json"), "validation": validation.model_dump(mode="json"),
            "recommendation": recommendation, "priority": priority,
            "downstream_effect": "review_priority" if priority == "review" else "none",
            "authority": "No action proposed or authorized by bounded decisions."}
        self._append(JournalEventType.BOUNDED_DECISION_EFFECT_RECORDED,
            insight.id + ":" + ":".join(t.id for t in traces), traces[0].project_id,
            traces[0].temporal.valid_from, at, payload, traces[0].evidence_refs)

    def effects(self, *, project_id=None):
        return [r.payload for r in self.journal.read_stream(stream_id=self.stream)
            if r.event_type == JournalEventType.BOUNDED_DECISION_EFFECT_RECORDED
            and (project_id is None or project_id in r.project_ids)]
