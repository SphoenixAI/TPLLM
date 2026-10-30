"""Server-only settings; absence and invalid configuration fail closed."""
from __future__ import annotations

import os
from pydantic import Field, SecretStr
from tpllm.decisions.models import FrozenModel, Mode


class DecisionConfig(FrozenModel):
    enabled: bool = False
    mode: Mode = "off"
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), exclude=True)
    model: str = Field(default="jev-latest", min_length=1, max_length=160, pattern=r"^[A-Za-z0-9._:/-]+$")
    timeout_seconds: float = Field(default=8, gt=0, le=30)
    max_attempts: int = Field(default=2, ge=1, le=3)
    include_source_excerpts: bool = False
    policy_path: str = ""

    @property
    def active(self) -> bool:
        return self.enabled and self.mode != "off"

    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv("TYPESAFE_ENABLED", "false").lower() == "true",
            mode=os.getenv("JEV_MODE", "off"), api_key=SecretStr(os.getenv("TYPESAFE_API_KEY", "")),
            model=os.getenv("TYPESAFE_MODEL", "jev-latest"),
            timeout_seconds=float(os.getenv("TYPESAFE_TIMEOUT_SECONDS", "8")),
            max_attempts=int(os.getenv("TYPESAFE_MAX_ATTEMPTS", "2")),
            include_source_excerpts=os.getenv("JEV_INCLUDE_SOURCE_EXCERPTS", "false").lower() == "true",
            policy_path=os.getenv("JEV_POLICY_PATH", ""))
