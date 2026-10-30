"""Additive task history over the existing journal, with no Phase 1 migration."""
from __future__ import annotations

from tpllm.decisions.models import utc
from tpllm.intelligence_tasks.models import ComparisonRecord, EvidencePacket, IntelligenceRecord, OutcomeEvent, ReviewEvent
from tpllm.persistence.journal import build_journal_record
from tpllm.persistence.models import JournalEventType as Event
from tpllm.runtime.decision_journal import JournalDecisionStore


class JournalIntelligenceStore:
    def __init__(self, journal, organization_id):
        self.journal, self.organization_id = journal, organization_id
        self.writer = JournalDecisionStore(journal, organization_id)
        self.writer.stream = "intelligence-tasks:"+organization_id

    def _write(self, event, identity, project, occurred, recorded, payload, refs=(), expected=None):
        occurred, recorded = utc(occurred), utc(recorded)
        if self.writer._naive_utc:
            occurred, recorded = occurred.replace(tzinfo=None), recorded.replace(tzinfo=None)
        self.journal.append(record=build_journal_record(event_type=event, source_key=identity,
            stream_id=self.writer.stream, organization_id=self.organization_id, project_ids=[project],
            occurred_at=occurred, recorded_at=recorded, payload=payload, evidence_ids=list(refs)),
            expected_stream_sequence=expected)

    def _rows(self, event, key, model, known_at=None):
        return [model.model_validate(r.payload[key]) for r in self.journal.read_stream(stream_id=self.writer.stream)
            if r.event_type == event and (known_at is None or utc(r.recorded_at) <= utc(known_at))]

    def packet(self, packet):
        packet = EvidencePacket.model_validate_json(packet.model_dump_json())
        self._write(Event.INTELLIGENCE_PACKET_RECORDED, packet.id, packet.state.project_id,
            packet.state.valid_at, packet.state.known_at, {"packet_id": packet.id, "packet": packet.model_dump(mode="json")}, packet.state.evidence_refs)

    def packets(self):
        return self._rows(Event.INTELLIGENCE_PACKET_RECORDED, "packet", EvidencePacket)

    def append(self, record):
        record = IntelligenceRecord.model_validate_json(record.model_dump_json())
        packet = next((p for p in self.packets() if p.id == record.evidence_packet_id), None)
        if (packet is None or record.project_id != packet.state.project_id or record.entity_id != packet.state.entity_id
                or record.cohort != packet.cohort or record.evidence_refs != packet.state.evidence_refs
                or record.valid_time != packet.state.valid_at or record.transaction_time != packet.state.known_at):
            raise ValueError("Envelope must match an immutable recorded packet")
        if record.decision and (record.decision.state_hash != packet.state.state_hash
                or record.decision.evidence_refs != packet.state.evidence_refs):
            raise ValueError("Decision does not originate from this packet")
        if record.reasoning:
            from tpllm.intelligence_tasks.evidence import ReasoningContextBuilder
            if (record.context_hash != ReasoningContextBuilder().build(packet)[1]
                    or record.reasoning.schema_id != record.task.definition_id
                    or record.reasoning.schema_version != record.task.schema_version):
                raise ValueError("Reasoning context/schema lineage mismatch")
        self._write(Event.INTELLIGENCE_TASK_RECORDED, record.id, record.project_id, record.valid_time,
            record.created_at, {"record": record.model_dump(mode="json")}, record.evidence_refs)

    def records(self, *, project_id=None, known_at=None):
        rows = self._rows(Event.INTELLIGENCE_TASK_RECORDED, "record", IntelligenceRecord, known_at)
        return [r for r in rows if not project_id or r.project_id == project_id]

    def get(self, identity):
        return next((r for r in self.records() if r.id == identity), None)

    def reviews(self, *, known_at=None):
        return self._rows(Event.INTELLIGENCE_REVIEW_RECORDED, "review", ReviewEvent, known_at)

    def outcomes(self, *, known_at=None):
        return self._rows(Event.INTELLIGENCE_OUTCOME_ATTACHED, "outcome", OutcomeEvent, known_at)

    def comparisons(self, *, known_at=None):
        return self._rows(Event.INTELLIGENCE_COMPARISON_RECORDED, "comparison", ComparisonRecord, known_at)

    def _correction(self, event, history):
        record = self.get(event.record_id)
        if record is None:
            raise ValueError("Unknown intelligence record")
        if utc(event.observed_at) < utc(record.created_at) or utc(event.recorded_at) < utc(event.observed_at):
            raise ValueError("Review/outcome cannot predate inference")
        previous = [e for e in history if e.record_id == record.id]
        if event.supersedes != (previous[-1].id if previous else None):
            raise ValueError("Correction must supersede the latest event")
        if previous and utc(event.recorded_at) < utc(previous[-1].recorded_at):
            raise ValueError("Correction cannot precede the latest event")
        return record

    @staticmethod
    def _decision_value(record, value):
        if value is None:
            return
        if record.decision is None or record.decision.result is None:
            raise ValueError("Only available probabilistic decisions accept calibration labels")
        q = record.decision.definition
        if (q.question_type == "noul" and type(value) is not bool
                or q.question_type == "choice" and value not in q.criteria
                or q.question_type == "score" and (type(value) not in (float, int) or not 0 <= value <= len(q.criteria)-1)):
            raise ValueError("Reference label does not match decision support")

    def review(self, review):
        head = self.journal.read_stream(stream_id=self.writer.stream)
        record = self._correction(review, self.reviews())
        allowed = ({"agree", "disagree", "ambiguous", "insufficient_evidence", "correct_label"} if record.task.lane == "decision"
            else {"accept", "partially_correct", "reject", "missing_evidence", "unsupported_inference", "corrected_interpretation"})
        if review.label not in allowed:
            raise ValueError("Review label belongs to a different lane")
        if review.label == "correct_label" and review.decision_value is None:
            raise ValueError("Correct label requires a reference value")
        self._decision_value(record, review.decision_value)
        self._write(Event.INTELLIGENCE_REVIEW_RECORDED, review.id, record.project_id, review.observed_at,
            review.recorded_at, {"review": review.model_dump(mode="json")}, review.evidence_refs,
            expected=head[-1].position.stream_sequence if head else 0)

    def attach_outcome(self, outcome):
        head = self.journal.read_stream(stream_id=self.writer.stream)
        record = self._correction(outcome, self.outcomes())
        # Only an existing structured outcome event can supply an outcome.
        # Free-form connector prose and model-generated outcomes are not parsed.
        sources = [r for r in self.journal.read_from(global_sequence=1)
            if r.event_type == Event.OUTCOME_RECORDED and r.organization_id == self.organization_id
            and record.project_id in r.project_ids and r.id == outcome.source_evidence_ref]
        if not sources or sources[0].payload.get("outcome_type") != outcome.outcome:
            raise ValueError("A matching structured outcome journal event is required")
        signal = sources[0]
        if utc(signal.occurred_at) != utc(outcome.observed_at) or utc(signal.recorded_at) > utc(outcome.recorded_at):
            raise ValueError("Outcome temporal lineage mismatch")
        self._decision_value(record, outcome.decision_value)
        self._write(Event.INTELLIGENCE_OUTCOME_ATTACHED, outcome.id, record.project_id, outcome.observed_at,
            outcome.recorded_at, {"outcome": outcome.model_dump(mode="json")}, (outcome.source_evidence_ref,),
            expected=head[-1].position.stream_sequence if head else 0)

    def compare(self, comparison):
        left = self.get(comparison.left_id)
        if left is None:
            raise ValueError("Unknown comparison record")
        if comparison.kind == "cross_lane":
            right = self.get(comparison.right_id)
            if (right is None or right.evidence_packet_id != left.evidence_packet_id
                    or right.task.lane == left.task.lane or utc(comparison.created_at) < max(utc(left.created_at), utc(right.created_at))):
                raise ValueError("Cross-lane comparison requires two contemporaneous outputs from one packet")
        elif comparison.kind in {"human", "outcome"}:
            events = self.reviews() if comparison.kind == "human" else self.outcomes()
            event = next((e for e in events if e.id == comparison.right_id and e.record_id == left.id), None)
            if event is None or utc(comparison.created_at) < utc(event.recorded_at):
                raise ValueError("Comparison needs a recorded reference event")
        elif comparison.right_id != "existing_tpllm" or not left.decision or left.decision.baseline_value is None:
            raise ValueError("Missing baseline comparison")
        self._write(Event.INTELLIGENCE_COMPARISON_RECORDED, comparison.id, left.project_id, comparison.created_at,
            comparison.created_at, {"comparison": comparison.model_dump(mode="json")}, left.evidence_refs)

    def coverage(self, *, packet_id, project_id, task_ids, reason, at):
        from tpllm.decisions.models import fingerprint
        payload = {"packet_id": packet_id, "task_ids": list(task_ids), "reason": reason, "project_id": project_id}
        self._write(Event.INTELLIGENCE_COVERAGE_RECORDED, fingerprint([payload, at.isoformat()]), project_id, at, at, payload)

    def coverage_events(self):
        return [r.payload for r in self.journal.read_stream(stream_id=self.writer.stream) if r.event_type == Event.INTELLIGENCE_COVERAGE_RECORDED]
