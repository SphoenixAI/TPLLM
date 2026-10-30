"""Explicit sampling configuration, never a confidence-based model router."""
import os

from pydantic import Field

from tpllm.decisions.models import FrozenModel, fingerprint
from tpllm.intelligence_tasks.models import COHORTS


class IntelligenceConfig(FrozenModel):
    mode: str = Field(default="off", pattern=r"^(off|sampled|fixtures)$")
    sample_rate: float = Field(default=0.1, ge=0, le=1)
    cohorts: tuple[str, ...] = ("software.deployments",)
    decision_tasks: tuple[str, ...] = ("risk.present.v1", "risk.family.v1", "risk.severity.v1")
    reasoning_tasks: tuple[str, ...] = ("reasoning.root_cause_analysis.v1",)
    include_excerpts: bool = False
    timeout_seconds: float = Field(default=10, gt=0, le=30)
    max_attempts: int = Field(default=2, ge=1, le=3)
    max_output: int = Field(default=2048, ge=128, le=8192)
    sample_seed: str = "phase2.v1"

    def selection(self, packet, *, fixture=False):
        if self.mode == "off":
            return False, "provider_disabled"
        if self.mode == "fixtures":
            return (True, None) if fixture else (False, "fixtures_only")
        if any(c not in COHORTS for c in self.cohorts):
            return False, "unsupported_cohort"
        if packet.cohort.cohort_id not in self.cohorts:
            return False, "cohort_excluded"
        fraction = int(fingerprint([self.sample_seed, packet.id])[:16], 16) / 2**64
        return (True, None) if fraction < self.sample_rate else (False, "sampling_excluded")

    @classmethod
    def from_env(cls):
        return cls(mode=os.getenv("INTELLIGENCE_EVAL_MODE", "off"),
            sample_rate=float(os.getenv("INTELLIGENCE_EVAL_SAMPLE_RATE", "0.1")),
            cohorts=tuple(filter(None, os.getenv("INTELLIGENCE_EVAL_COHORTS", "software.deployments").split(","))),
            decision_tasks=tuple(filter(None, os.getenv("INTELLIGENCE_DECISION_TASKS", "risk.present.v1,risk.family.v1,risk.severity.v1").split(","))),
            reasoning_tasks=tuple(filter(None, os.getenv("INTELLIGENCE_REASONING_TASKS", "reasoning.root_cause_analysis.v1").split(","))),
            include_excerpts=os.getenv("INTELLIGENCE_INCLUDE_EXCERPTS", "false").lower() == "true",
            timeout_seconds=float(os.getenv("REASONING_TIMEOUT_SECONDS", "10")),
            max_attempts=int(os.getenv("REASONING_MAX_ATTEMPTS", "2")),
            max_output=int(os.getenv("REASONING_MAX_OUTPUT", "2048")))
