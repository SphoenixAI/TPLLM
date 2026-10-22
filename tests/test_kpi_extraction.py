"""Deterministic KPI claim extraction from
meeting transcripts (the spec's activation example)."""

from __future__ import annotations

from lltpm.intelligence import (
    DeterministicKPIClaimExtractor,
    KPIClaimType,
)
from tests._kpi_fixtures import PROJECT, definition, meeting_evidence

_TRANSCRIPT = """Activation is still at 61%.
We need 70% before beta.
The experiment won't finish until Wednesday.
Mobile onboarding is dragging the number down."""


def _claims():
    extractor = DeterministicKPIClaimExtractor()
    return extractor.extract_kpi_claims(
        project_id=PROJECT,
        evidence=[meeting_evidence("meet-1", _TRANSCRIPT)],
        known_kpis=[definition()],
    )


class TestTranscriptExtraction:
    def test_current_value_extracted(self):
        values = [
            c for c in _claims()
            if c.claim_type == KPIClaimType.CURRENT_VALUE
        ]
        assert len(values) == 1
        assert values[0].parsed_numeric_value == 61.0
        assert values[0].candidate_kpi_name == "Activation rate"
        assert values[0].evidence_ids == ["meet-1"]

    def test_target_extracted_with_milestone_link(self):
        claims = _claims()
        targets = [c for c in claims if c.claim_type == KPIClaimType.TARGET]
        milestones = [
            c for c in claims if c.claim_type == KPIClaimType.MILESTONE_LINK
        ]
        assert len(targets) == 1
        assert targets[0].target_candidate == 70.0
        assert len(milestones) == 1
        assert "beta" in milestones[0].statement

    def test_measurement_delay_extracted(self):
        delays = [
            c for c in _claims()
            if c.claim_type == KPIClaimType.MEASUREMENT_LAG
        ]
        assert len(delays) == 1
        assert "Wednesday" in delays[0].statement

    def test_cause_remains_low_confidence_claim(self):
        causes = [c for c in _claims() if c.claim_type == KPIClaimType.CAUSE]
        assert len(causes) == 1
        assert "Mobile onboarding" in causes[0].statement
        # A spoken cause is a hypothesis: low confidence, never fact.
        assert causes[0].confidence < 0.5

    def test_unknown_kpi_lines_ignored(self):
        extractor = DeterministicKPIClaimExtractor()
        claims = extractor.extract_kpi_claims(
            project_id=PROJECT,
            evidence=[meeting_evidence(
                "meet-2", "Revenue is at 12M this quarter.",
            )],
            known_kpis=[definition()],
        )
        assert claims == []

    def test_extraction_deterministic(self):
        assert [c.model_dump() for c in _claims()] == [
            c.model_dump() for c in _claims()
        ]
