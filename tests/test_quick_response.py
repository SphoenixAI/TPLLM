"""Tests for Quick Response Protocol."""

from tpllm.workflows.quick_response import QuickResponseProtocol


def test_status_check():
    qr = QuickResponseProtocol()
    result = qr.status_check("Auth migration", "2026-03-01")

    assert result.request_type == "status_check"
    assert len(result.options) == 4
    assert any("On track" in o.label for o in result.options)
    assert any("Blocked" in o.label for o in result.options)


def test_review_request():
    qr = QuickResponseProtocol()
    result = qr.review_request("API spec v2", "Thursday")

    assert result.request_type == "review_request"
    assert any("Will review" in o.label for o in result.options)


def test_decision_needed():
    qr = QuickResponseProtocol()
    result = qr.decision_needed("Auth provider", options=["Auth0", "Okta"])

    assert result.request_type == "decision_needed"
    assert any("Auth0" in o.label for o in result.options)
    assert any("Okta" in o.label for o in result.options)
    assert any("Need more info" in o.label for o in result.options)


def test_timeline_confirmation():
    qr = QuickResponseProtocol()
    result = qr.timeline_confirmation("Beta release", "2026-03-15")

    assert any("Confirmed" in o.label for o in result.options)
    assert any("Need more time" in o.label for o in result.options)


def test_commitment_check():
    qr = QuickResponseProtocol()
    result = qr.commitment_check("Write RFC", "Alice")

    assert any("Done" in o.label for o in result.options)
    assert any("In progress" in o.label for o in result.options)


def test_render_inline():
    qr = QuickResponseProtocol()
    result = qr.status_check("Task X")

    inline = result.render_inline()
    assert "[On track]" in inline
    assert "[Other...]" in inline


def test_render_numbered():
    qr = QuickResponseProtocol()
    result = qr.status_check("Task X")

    numbered = result.render_numbered()
    assert "1." in numbered
    assert "Other" in numbered


def test_generate_for_unknown_type():
    qr = QuickResponseProtocol()
    result = qr.generate_for_request("unknown_type")

    assert len(result.options) == 4
    assert any("Acknowledged" in o.label for o in result.options)
