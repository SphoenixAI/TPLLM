"""Action request helpers."""

from __future__ import annotations

from datetime import timedelta

from lltpm.coordination import (
    ActionRequestStatus,
    ActionRequestType,
    ActorRelationType,
    is_authority_request,
    is_open_request,
    is_overdue,
    open_requests_for_workstream,
    blocked_requests_for_workstream,
)
from tests._coordination_fixtures import AS_OF, WS, request


class TestRequestClassification:
    def test_open_and_closed(self):
        assert is_open_request(request("r1")) is True
        assert is_open_request(
            request("r2", status=ActionRequestStatus.RESOLVED)
        ) is False
        assert is_open_request(
            request("r3", status=ActionRequestStatus.CANCELLED)
        ) is False

    def test_overdue(self):
        overdue = request("r1", due_at=AS_OF - timedelta(days=2))
        future = request("r2", due_at=AS_OF + timedelta(days=2))
        resolved = request(
            "r3", status=ActionRequestStatus.RESOLVED,
            due_at=AS_OF - timedelta(days=2),
        )
        assert is_overdue(overdue, AS_OF) is True
        assert is_overdue(future, AS_OF) is False
        assert is_overdue(resolved, AS_OF) is False

    def test_authority_request_types(self):
        assert is_authority_request(
            request("r1", request_type=ActionRequestType.DECISION)
        ) is True
        assert is_authority_request(
            request("r2", request_type=ActionRequestType.APPROVAL)
        ) is True
        assert is_authority_request(
            request("r3", request_type=ActionRequestType.DATA)
        ) is False

    def test_workstream_filters(self):
        mine = request("r1")
        other = request("r2", workstream_id="ws-search")
        blocked = request("r3", status=ActionRequestStatus.BLOCKED)
        assert [r.id for r in open_requests_for_workstream(
            [mine, other, blocked], WS,
        )] == ["r1", "r3"]
        assert [r.id for r in blocked_requests_for_workstream(
            [mine, other, blocked], WS,
        )] == ["r3"]

    def test_request_preserves_authority_relation_needed(self):
        req = request("r1")
        assert req.required_authority_relation == ActorRelationType.MAY_APPROVE
        # The requester's own relations are not the required relation —
        # requester context and required authority stay distinct fields.
        assert req.requested_by.role == "Payments Integration Lead"
