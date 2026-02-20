"""Base integration interface.

All integrations share a common pattern:
- Configured via IntegrationConfig
- Can test connectivity
- Return structured data that the agent can reason about
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lltpm.config import IntegrationConfig


class IntegrationError(Exception):
    """Raised when an integration call fails."""


class Integration(ABC):
    """Base class for all external integrations."""

    def __init__(self, config: IntegrationConfig) -> None:
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    @abstractmethod
    def test_connection(self) -> bool:
        """Verify the integration is reachable and credentials work."""

    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this integration."""
