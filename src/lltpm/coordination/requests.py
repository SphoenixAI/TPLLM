"""Action request helpers — open/overdue/blocking classification."""

from __future__ import annotations

from datetime import datetime

from lltpm.coordination.models import (
    ActionRequest,
    ActionRequestStatus,
    ActionRequestType,
)

_CLOSED_STATUSES = {
    ActionRequestStatus.RESOLVED,
    ActionRequestStatus.CANCELLED,
}

_AUTHORITY_REQUEST_TYPES = {
    ActionRequestType.DECISION,
    ActionRequestType.APPROVAL,
}


def is_open_request(request: ActionRequest) -> bool:
    return request.status not in _CLOSED_STATUSES


def is_overdue(request: ActionRequest, as_of: datetime) -> bool:
    return (
        is_open_request(request)
        and request.due_at is not None
        and request.due_at < as_of
    )


def is_authority_request(request: ActionRequest) -> bool:
    return request.request_type in _AUTHORITY_REQUEST_TYPES


def request_touches_workstream(request: ActionRequest, workstream_id: str) -> bool:
    return request.workstream_id == workstream_id


def open_requests_for_workstream(
    requests: list[ActionRequest], workstream_id: str
) -> list[ActionRequest]:
    return [
        r
        for r in requests
        if is_open_request(r) and request_touches_workstream(r, workstream_id)
    ]


def blocked_requests_for_workstream(
    requests: list[ActionRequest], workstream_id: str
) -> list[ActionRequest]:
    return [
        r
        for r in open_requests_for_workstream(requests, workstream_id)
        if r.status == ActionRequestStatus.BLOCKED
    ]
