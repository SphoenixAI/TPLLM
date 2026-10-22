"""KPI claim reconciliation priority."""

from __future__ import annotations

from datetime import timedelta

from tpllm.intelligence import (
    KPIClaim,
    KPIClaimType,
    KPIReconciliationStatus,
    reconcile_kpi_claims,
)
from tests._kpi_fixtures import AS_OF, PROJECT, definition, observation, target


def _claim(
    cid: str,
    claim_type: KPIClaimType = KPIClaimType.CURRENT_VALUE,
    value: float | None = None,
    target_value: float | None = None,
    name: str = "activation",
    days_ago: int = 3,
) -> KPIClaim:
    return KPIClaim(
        id=cid,
        project_id=PROJECT,
        candidate_kpi_name=name,
        claim_type=claim_type,
        statement=f"claim {cid}",
        parsed_numeric_value=value,
        target_candidate=target_value,
        observed_at=AS_OF - timedelta(days=days_ago),
        evidence_ids=[f"ev-{cid}"],
        confidence=0.6,
    )


def _reconcile(claims, observations=(), targets=()):
    return reconcile_kpi_claims(
        claims=claims,
        known_kpis=[definition()],
        observations=list(observations),
        targets=list(targets),
        as_of=AS_OF,
    )


class TestReconciliation:
    def test_alias_resolves_to_canonical_kpi(self):
        results = _reconcile([_claim("c1", value=61.0)])
        assert results[0].kpi_id == "kpi-activation"

    def test_fresh_metric_outranks_stale_meeting_claim(self):
        results = _reconcile(
            [_claim("c-meeting", value=68.0, days_ago=6)],
            observations=[observation("obs-fresh", value=61.0, days_ago=1)],
        )
        result = results[0]
        assert result.status == KPIReconciliationStatus.CONFIRMED
        assert result.resolved_observation_id == "obs-fresh"
        # The differing meeting claim is recorded as conflicting, not
        # allowed to override the metric.
        assert "c-meeting" in result.conflicting_claim_ids
        assert "authoritative" in result.explanation

    def test_agreeing_meeting_claim_supports_metric(self):
        results = _reconcile(
            [_claim("c-agree", value=61.0)],
            observations=[observation("obs-fresh", value=61.0, days_ago=1)],
        )
        assert results[0].supporting_claim_ids == ["c-agree"]
        assert results[0].conflicting_claim_ids == []

    def test_conflicting_values_without_metric_stay_contested(self):
        results = _reconcile([
            _claim("c-a", value=61.0),
            _claim("c-b", value=74.0),
        ])
        result = results[0]
        assert result.status == KPIReconciliationStatus.CONTESTED
        assert set(result.conflicting_claim_ids) == {"c-a", "c-b"}
        assert "no value is silently chosen" in result.explanation.lower()

    def test_target_claim_matches_formal_target(self):
        results = _reconcile(
            [_claim("c-t", claim_type=KPIClaimType.TARGET, target_value=70.0)],
            targets=[target()],
        )
        assert results[0].status == KPIReconciliationStatus.CONFIRMED
        assert results[0].resolved_target_id == "tgt-1"

    def test_new_target_candidate_does_not_overwrite(self):
        results = _reconcile(
            [_claim("c-t", claim_type=KPIClaimType.TARGET, target_value=75.0)],
            targets=[target()],
        )
        assert results[0].status == (
            KPIReconciliationStatus.PARTIALLY_CONFIRMED
        )
        assert "not an automatic overwrite" in results[0].explanation

    def test_cause_claims_remain_hypotheses(self):
        results = _reconcile([
            _claim("c-cause", claim_type=KPIClaimType.CAUSE),
        ])
        assert results[0].status == KPIReconciliationStatus.UNRESOLVED
        assert "hypotheses" in results[0].explanation

    def test_unknown_kpi_name_unresolved(self):
        results = _reconcile([_claim("c-x", value=5.0, name="mystery metric")])
        assert results[0].kpi_id is None
        assert results[0].status == KPIReconciliationStatus.UNRESOLVED
