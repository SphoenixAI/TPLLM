"""KPI claim extraction from communication evidence.

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

from tpllm.intelligence.kpi import (
    KPIClaim,
    KPIClaimType,
    KPIDefinition,
)
from tpllm.intelligence.project_understanding import ProjectEvidence


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


def _claim_segments(content: str, definitions: list[KPIDefinition]):
    """Keep each named metric's claims inside its own sentence or clause."""
    # Require whitespace after sentence punctuation so decimals stay intact.
    for sentence in re.split(r"(?:\r?\n|(?<=[.!?;])\s+)", content):
        mentions = []
        for definition in definitions:
            for name in [definition.canonical_name, *definition.aliases]:
                if not name.strip():
                    continue
                for match in re.finditer(r"(?<!\w)" + re.escape(name) + r"(?!\w)", sentence, re.I):
                    mentions.append((match.start(), match.end(), definition))
        # Prefer the full name over an overlapping shorter alias.
        selected = []
        for mention in sorted(mentions, key=lambda m: (m[0], -m[1])):
            if not selected or mention[0] >= selected[-1][1]:
                selected.append(mention)
        if not selected:
            yield sentence.strip(), None
            continue
        if selected[0][0]:
            yield sentence[:selected[0][0]].strip(), None
        for index, (start, _, definition) in enumerate(selected):
            end = selected[index + 1][0] if index + 1 < len(selected) else len(sentence)
            yield sentence[start:end].strip(), definition


class DeterministicKPIClaimExtractor:
    """Regex/alias-based extractor. Tracks the last-mentioned KPI within
    one evidence item so follow-up sentences ("We need 70% before beta")
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
            for line, mentioned in _claim_segments(item.content, known_kpis):
                if not line:
                    continue
                if mentioned is not None:
                    current_kpi = mentioned
                if current_kpi is None:
                    continue

                target_matches = list(_TARGET_PATTERN.finditer(line))
                # "Recognition accuracy target is 98%" is a target only.
                value_match = next((m for m in _VALUE_PATTERN.finditer(line)
                    if not any(t.start() <= m.start() < t.end() for t in target_matches)), None)
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

                target_match = next(iter(target_matches), None)
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
