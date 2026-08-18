"""Runtime projections and the derived artifact cache (Commit 31).

Everything in this module is rebuildable. That is not a caveat, it is
the design: the acceptance test for this commit deletes all of it and
expects the journal to produce it again.

The one invariant that must hold even across a crash is atomic revision
promotion. Commit 30 already separates candidate from current; this
layer makes that survive process death. Artifacts are written first, the
candidate is validated, and only then does the current-revision pointer
move. A crash before the move leaves the previous revision answering; a
crash after leaves the new one. There is no moment at which half a
revision is current.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from lltpm.persistence.models import (
    IntelligenceArtifactRecord,
    PortfolioRuntimeStateRecord,
    ProjectRuntimeStateRecord,
    RuntimeRevisionRecord,
)
from lltpm.persistence.protocols import RevisionPromotionError


class InMemoryRuntimeStateStore:
    """Reference projection store.

    Copy-in/copy-out: callers cannot hold a reference and mutate stored
    state behind the store's back.
    """

    def __init__(self) -> None:
        self._current_revision: dict[str, str] = {}
        self._project_states: dict[str, ProjectRuntimeStateRecord] = {}
        self._portfolio_states: dict[str, PortfolioRuntimeStateRecord] = {}
        self._revisions: dict[str, RuntimeRevisionRecord] = {}
        self._lock = threading.Lock()

    def get_current_revision(self, organization_id: str) -> str | None:
        return self._current_revision.get(organization_id)

    def get_project_state(
        self, project_id: str,
    ) -> ProjectRuntimeStateRecord | None:
        found = self._project_states.get(project_id)
        return found.model_copy(deep=True) if found else None

    def put_project_state(self, state: ProjectRuntimeStateRecord) -> None:
        with self._lock:
            self._project_states[state.project_id] = state.model_copy(deep=True)

    def get_portfolio_state(
        self, organization_id: str,
    ) -> PortfolioRuntimeStateRecord | None:
        found = self._portfolio_states.get(organization_id)
        return found.model_copy(deep=True) if found else None

    def put_portfolio_state(
        self, state: PortfolioRuntimeStateRecord,
    ) -> None:
        with self._lock:
            self._portfolio_states[state.organization_id] = (
                state.model_copy(deep=True)
            )

    def put_revision(self, revision: RuntimeRevisionRecord) -> None:
        with self._lock:
            self._revisions[revision.revision_id] = revision.model_copy(
                deep=True,
            )

    def get_revision(
        self, revision_id: str,
    ) -> RuntimeRevisionRecord | None:
        found = self._revisions.get(revision_id)
        return found.model_copy(deep=True) if found else None

    def list_revisions(
        self, organization_id: str,
    ) -> list[RuntimeRevisionRecord]:
        """Every revision, including the ones that never became current.

        A failed candidate stays inspectable; "why did last night's
        recompute not publish?" is a question with an answer.
        """
        return [
            r.model_copy(deep=True)
            for r in sorted(
                self._revisions.values(), key=lambda r: r.revision_id,
            )
            if r.organization_id == organization_id
        ]

    def promote_revision(
        self,
        *,
        organization_id: str,
        revision_id: str,
        expected_current_revision_id: str | None,
    ) -> None:
        """Move the pointer, under a compare-and-set.

        Refuses an incomplete candidate, and refuses to move a pointer
        that has shifted since the caller read it.
        """
        with self._lock:
            revision = self._revisions.get(revision_id)
            if revision is None:
                raise RevisionPromotionError(
                    f"Revision {revision_id} has not been persisted; there "
                    f"is nothing to promote."
                )
            if not revision.complete:
                raise RevisionPromotionError(
                    f"Revision {revision_id} is not complete and must not "
                    f"become current; the previous revision keeps answering."
                )
            current = self._current_revision.get(organization_id)
            if current != expected_current_revision_id:
                raise RevisionPromotionError(
                    f"Current revision for '{organization_id}' is {current!r}, "
                    f"not {expected_current_revision_id!r}; another writer "
                    f"promoted first."
                )
            for other_id, other in self._revisions.items():
                if (
                    other.organization_id == organization_id
                    and other.is_current
                ):
                    self._revisions[other_id] = other.model_copy(update={
                        "is_current": False,
                    })
            self._revisions[revision_id] = revision.model_copy(update={
                "is_current": True,
            })
            self._current_revision[organization_id] = revision_id

    # ── Administrative ───────────────────────────────────────────────

    def clear_projections(self, organization_id: str) -> None:
        """Throw away every derived projection for an organization.

        Deliberately destructive and deliberately safe: the journal is
        untouched, so a rebuild restores all of it.
        """
        with self._lock:
            self._current_revision.pop(organization_id, None)
            self._portfolio_states.pop(organization_id, None)
            self._revisions = {
                k: v for k, v in self._revisions.items()
                if v.organization_id != organization_id
            }
            self._project_states = {
                k: v for k, v in self._project_states.items()
                if v.organization_id not in (organization_id, None)
            }

    def all_project_states(self) -> list[ProjectRuntimeStateRecord]:
        return [
            self._project_states[k].model_copy(deep=True)
            for k in sorted(self._project_states)
        ]


class InMemoryIntelligenceArtifactStore:
    """Cache of derived artifacts, keyed by type and id.

    Historical agent output lives here on purpose. "What did TPLLM
    recommend on August 18?" has one true answer, and regenerating it
    under a newer engine would answer a different question.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, IntelligenceArtifactRecord] = {}
        self._lock = threading.Lock()

    def put(
        self,
        *,
        artifact_type: str,
        artifact_id: str,
        revision_id: str,
        payload: dict[str, Any],
        fingerprint: str,
        derivation_version: str | None = None,
        policy_fingerprint: str | None = None,
        created_at: datetime | None = None,
    ) -> None:
        record = IntelligenceArtifactRecord(
            artifact_type=artifact_type,
            artifact_id=artifact_id,
            revision_id=revision_id,
            fingerprint=fingerprint,
            payload=dict(payload),
            derivation_version=derivation_version,
            policy_fingerprint=policy_fingerprint,
            created_at=created_at,
        )
        with self._lock:
            self._artifacts[f"{artifact_type}:{artifact_id}:{revision_id}"] = (
                record
            )

    def get(
        self, artifact_type: str, artifact_id: str,
    ) -> IntelligenceArtifactRecord | None:
        """The most recent revision's copy of this artifact."""
        matches = [
            record for key, record in self._artifacts.items()
            if key.startswith(f"{artifact_type}:{artifact_id}:")
        ]
        if not matches:
            return None
        return sorted(matches, key=lambda r: r.revision_id)[-1].model_copy(
            deep=True,
        )

    def get_at_revision(
        self, artifact_type: str, artifact_id: str, revision_id: str,
    ) -> IntelligenceArtifactRecord | None:
        """What this artifact said at a particular revision.

        The historical query: not what the engine would say now.
        """
        found = self._artifacts.get(
            f"{artifact_type}:{artifact_id}:{revision_id}"
        )
        return found.model_copy(deep=True) if found else None

    def get_revision_artifacts(
        self, revision_id: str,
    ) -> list[IntelligenceArtifactRecord]:
        return [
            record.model_copy(deep=True)
            for _, record in sorted(self._artifacts.items())
            if record.revision_id == revision_id
        ]

    def clear(self) -> None:
        with self._lock:
            self._artifacts = {}

    def __len__(self) -> int:
        return len(self._artifacts)
