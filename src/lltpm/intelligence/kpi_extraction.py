"""KPI claim extraction from communication evidence (Commit 16).

A deterministic extractor that recognizes known KPI names/aliases in
meeting/chat evidence and emits claims: current values, targets with
milestone links, measurement delays, and cause *hypotheses*. A cause
said in a meeting stays a claim — it is never upgraded to fact here.

An LLM-backed extractor can implement the same KPIClaimExtractor
protocol; the domain layer never hardwires an SDK.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Protocol

from lltpm.intelligence.kpi import (
    KPIClaim,
    KPIClaimType,
    KPIDefinition,
)
from lltpm.intelligence.project_understanding import ProjectEvidence


class KPIClaimExtractor(Protocol):
    def extract_kpi_claims(
        self,
        *,
        project_id: str,
        evidence: list[ProjectEvidence],
        known_kpis: list[KPIDefinition],
    ) -> list[KPIClaim]:
        ...


_VALUE_PATTERN = re.compile(
    r"\b(?:is|at|=|still at|currently(?: at)?|sits at)\s*(\d+(?:\.\d+)?)\s*(%|percent)?",
    re.IGNORECASE,
)
_TARGET_PATTERN = re.compile(
    r"\b(?:need|target(?: is)?|require|must (?:reach|hit))\s*(\d+(?:\.\d+)?)\s*(%|percent)?"
    r"(?:\s+(?:before|by|for)\s+([a-z0-9-]+))?",
    re.IGNORECASE,
)
_DELAY_PATTERN = re.compile(
    r"\b(?:won'?t finish until|not until|delayed until|finishes?\s+(?:on\s+)?)\s*"
    r"([A-Z][a-z]+day|[A-Z][a-z]+ \d{1,2})",
)
_CAUSE_PATTERN = re.compile(
    r"\b([A-Za-z][A-Za-z ]{2,40}?)\s+is\s+(?:dragging|hurting|pulling|holding)\b",
)


def _kpi_mentioned(line: str, definition: KPIDefinition) -> bool:
    lowered = line.lower()
    if definition.canonical_name.lower() in lowered:
        return True
    return any(alias.lower() in lowered for alias in definition.aliases)


class DeterministicKPIClaimExtractor:
    """Regex/alias-based extractor. Tracks the last-mentioned KPI within
    one evidence item so follow-up lines ("We need 70% before beta")
    attach to the KPI under discussion."""

    def extract_kpi_claims(
        self,
        *,
        project_id: str,
        evidence: list[ProjectEvidence],
        known_kpis: list[KPIDefinition],
    ) -> list[KPIClaim]:
        claims: list[KPIClaim] = []
        counter = 0

        def add(
            kpi_name: str,
            claim_type: KPIClaimType,
            statement: str,
            observed_at: datetime,
            evidence_id: str,
            value: float | None = None,
            unit: str | None = None,
            target: float | None = None,
            confidence: float = 0.6,
        ) -> None:
            nonlocal counter
            counter += 1
            claims.append(
                KPIClaim(
                    id=f"kpiclaim-{counter}",
                    project_id=project_id,
                    candidate_kpi_name=kpi_name,
                    claim_type=claim_type,
                    statement=statement.strip(),
                    parsed_numeric_value=value,
                    parsed_unit=unit,
                    target_candidate=target,
                    observed_at=observed_at,
                    evidence_ids=[evidence_id],
                    confidence=confidence,
                )
            )

        for item in evidence:
            current_kpi: KPIDefinition | None = None
            for line in item.content.splitlines():
                line = line.strip()
                if not line:
                    continue

                mentioned = next(
                    (d for d in known_kpis if _kpi_mentioned(line, d)), None
                )
                if mentioned is not None:
                    current_kpi = mentioned
                if current_kpi is None:
                    continue

                value_match = _VALUE_PATTERN.search(line)
                if value_match and mentioned is not None:
                    add(
                        current_kpi.canonical_name,
                        KPIClaimType.CURRENT_VALUE,
                        line,
                        item.source.observed_at,
                        item.id,
                        value=float(value_match.group(1)),
                        unit="%" if value_match.group(2) else current_kpi.unit,
                    )

                target_match = _TARGET_PATTERN.search(line)
                if target_match:
                    add(
                        current_kpi.canonical_name,
                        KPIClaimType.TARGET,
                        line,
                        item.source.observed_at,
                        item.id,
                        target=float(target_match.group(1)),
                        unit="%" if target_match.group(2) else current_kpi.unit,
                    )
                    if target_match.group(3):
                        add(
                            current_kpi.canonical_name,
                            KPIClaimType.MILESTONE_LINK,
                            line,
                            item.source.observed_at,
                            item.id,
                        )

                delay_match = _DELAY_PATTERN.search(line)
                if delay_match:
                    add(
                        current_kpi.canonical_name,
                        KPIClaimType.MEASUREMENT_LAG,
                        line,
                        item.source.observed_at,
                        item.id,
                        confidence=0.5,
                    )

                cause_match = _CAUSE_PATTERN.search(line)
                if cause_match:
                    # A spoken cause is a hypothesis, never a fact.
                    add(
                        current_kpi.canonical_name,
                        KPIClaimType.CAUSE,
                        line,
                        item.source.observed_at,
                        item.id,
                        confidence=0.35,
                    )

        return claims
