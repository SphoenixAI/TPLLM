"""Shared fixtures for KPI tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from tpllm.intelligence import (
    EvidenceReference,
    EvidenceSourceType,
    KPIDefinition,
    KPIIndicatorRole,
    KPIObservation,
    KPITarget,
    KPITargetOperator,
    KPIType,
    ProjectEvidence,
)

AS_OF = datetime(2026, 8, 10, 12, 0, 0)
PROJECT = "atlas"


def definition(**overrides) -> KPIDefinition:
    defaults = dict(
        id="kpi-activation",
        project_id=PROJECT,
        canonical_name="Activation rate",
        aliases=["activation", "activation %"],
        description="Share of new accounts completing activation",
        kpi_type=KPIType.PRODUCT,
        indicator_role=KPIIndicatorRole.LAGGING,
        unit="%",
        why_it_matters="Beta gate: activation must clear 70% before beta.",
        source_systems=["analytics"],
        expected_refresh_interval_seconds=24 * 3600,
        evidence_ids=["ev-kpi-def"],
        confidence=0.8,
    )
    defaults.update(overrides)
    return KPIDefinition(**defaults)


def target(**overrides) -> KPITarget:
    defaults = dict(
        id="tgt-1",
        kpi_id="kpi-activation",
        operator=KPITargetOperator.GREATER_THAN_OR_EQUAL,
        target_value=70.0,
        effective_from=AS_OF - timedelta(days=60),
        evidence_ids=["ev-target-doc"],
        confidence=0.8,
    )
    defaults.update(overrides)
    return KPITarget(**defaults)


def observation(
    oid: str = "obs-1",
    value: float = 61.0,
    days_ago: float = 1,
    kpi_id: str = "kpi-activation",
    **overrides,
) -> KPIObservation:
    defaults = dict(
        id=oid,
        kpi_id=kpi_id,
        value=value,
        unit="%",
        observed_at=AS_OF - timedelta(days=days_ago),
        reported_at=AS_OF - timedelta(days=days_ago) + timedelta(hours=2),
        source_system="analytics",
        source_id=f"metric-{oid}",
        evidence_ids=[f"ev-{oid}"],
        confidence=0.9,
    )
    defaults.update(overrides)
    return KPIObservation(**defaults)


def meeting_evidence(
    eid: str, content: str, days_ago: int = 1,
) -> ProjectEvidence:
    reference = EvidenceReference(
        id=f"ref-{eid}",
        project_id=PROJECT,
        source_type=EvidenceSourceType.MEETING,
        source_id=eid,
        observed_at=AS_OF - timedelta(days=days_ago),
        ingested_at=AS_OF,
        reliability=0.7,
        relevance=0.8,
    )
    return ProjectEvidence(
        id=eid, project_id=PROJECT, content=content, source=reference,
        normalized_at=AS_OF,
    )
