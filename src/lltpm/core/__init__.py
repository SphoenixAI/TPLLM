"""Core domain contracts.

Shared semantic shapes that two or more peer domains need and that no
single peer domain owns. Core contains contracts only — never domain
decision logic, and never a general utilities dump.

Core imports no other lltpm package. That is what makes the dependency
inversion real:

                 core
                /    \\
               v      v
       coordination  intelligence
"""

from lltpm.core.actors import ActorReference
from lltpm.core.evidence import EvidenceBearing, ProvenanceStamp
from lltpm.core.references import (
    EvidenceReferenceView,
    ProjectReference,
    TemporalReference,
    WorkstreamReference,
)
from lltpm.core.views import (
    ActorView,
    ReadinessSummaryView,
    ReadinessView,
    summarize_readiness,
)

__all__ = [
    "ActorReference",
    "ActorView",
    "EvidenceBearing",
    "EvidenceReferenceView",
    "ProjectReference",
    "ProvenanceStamp",
    "ReadinessSummaryView",
    "ReadinessView",
    "TemporalReference",
    "WorkstreamReference",
    "summarize_readiness",
]
