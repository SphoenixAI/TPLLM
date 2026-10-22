"""Tests for Zoom Integration (real OAuth implementation)."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

from tpllm.config import IntegrationConfig
from tpllm.integrations.zoom_integration import ZoomIntegration, _ZoomClient
from tpllm.integrations.base import IntegrationError

import pytest


def _make_config(**overrides):
    defaults = {
        "enabled": True,
        "api_key": "test-client-id",
        "extra": {
            "account_id": "test-account-id",
            "client_secret": "test-client-secret",
        },
    }
    defaults.update(overrides)
    return IntegrationConfig(**defaults)


def test_integration_name():
    zoom = ZoomIntegration(_make_config())
    assert zoom.name() == "Zoom"


def test_integration_enabled():
    zoom = ZoomIntegration(_make_config())
    assert zoom.enabled is True

    disabled = ZoomIntegration(IntegrationConfig(enabled=False))
    assert disabled.enabled is False


def test_require_enabled_raises():
    zoom = ZoomIntegration(IntegrationConfig(enabled=False))
    with pytest.raises(IntegrationError, match="not configured"):
        zoom.get_meeting("123")


def test_requires_client_id_and_account():
    zoom = ZoomIntegration(IntegrationConfig(
        enabled=True, api_key="", extra={},
    ))
    with pytest.raises(IntegrationError, match="ZOOM_API_KEY"):
        zoom._get_client()


def test_requires_client_secret():
    zoom = ZoomIntegration(IntegrationConfig(
        enabled=True, api_key="cid", extra={"account_id": "aid"},
    ))
    with pytest.raises(IntegrationError, match="ZOOM_CLIENT_SECRET"):
        zoom._get_client()


def test_get_client_returns_zoom_client():
    zoom = ZoomIntegration(_make_config())
    client = zoom._get_client()
    assert isinstance(client, _ZoomClient)


def test_zoom_client_token_url():
    client = _ZoomClient("cid", "secret", "aid")
    assert client.TOKEN_URL == "https://zoom.us/oauth/token"
    assert client.API_BASE == "https://api.zoom.us/v2"


def test_test_connection_disabled():
    zoom = ZoomIntegration(IntegrationConfig(enabled=False))
    assert zoom.test_connection() is False


def test_test_connection_success():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {"id": "user-1", "email": "test@example.com"}
    with patch.object(zoom, "_get_client", return_value=mock_client):
        assert zoom.test_connection() is True


def test_test_connection_failure():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.side_effect = IntegrationError("fail")
    with patch.object(zoom, "_get_client", return_value=mock_client):
        assert zoom.test_connection() is False


def test_get_meeting():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {"id": "12345", "topic": "Sprint Planning"}
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.get_meeting("12345")
    assert result["topic"] == "Sprint Planning"
    mock_client.get.assert_called_once_with("/meetings/12345")


def test_list_meetings():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "meetings": [
            {"id": "1", "topic": "Meeting A"},
            {"id": "2", "topic": "Meeting B"},
        ]
    }
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.list_meetings()
    assert len(result) == 2


def test_list_recordings():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "meetings": [{"uuid": "rec-1", "topic": "Recorded Meeting"}]
    }
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.list_recordings(from_date="2026-01-01")
    assert len(result) == 1


def test_get_transcript():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "recording_files": [
            {"file_type": "MP4", "download_url": "https://zoom.us/mp4"},
            {"file_type": "TRANSCRIPT", "download_url": "https://zoom.us/vtt"},
        ]
    }
    mock_client.get_download.return_value = "Hello, this is the transcript."
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.get_transcript("12345")
    assert result == "Hello, this is the transcript."
    mock_client.get_download.assert_called_once_with("https://zoom.us/vtt")


def test_get_transcript_no_transcript():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "recording_files": [{"file_type": "MP4", "download_url": "https://zoom.us/mp4"}]
    }
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.get_transcript("12345")
    assert result == ""


def test_create_meeting():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.post.return_value = {
        "id": "99999",
        "topic": "New Meeting",
        "join_url": "https://zoom.us/j/99999",
    }
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.create_meeting(
            topic="New Meeting",
            start_time="2026-03-15T10:00:00Z",
            duration=60,
            attendees=["alice@example.com"],
            agenda="Discuss roadmap",
        )
    assert result["id"] == "99999"
    call_kwargs = mock_client.post.call_args
    assert call_kwargs[0][0] == "/users/me/meetings"


def test_delete_meeting():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.delete.return_value = None
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.delete_meeting("12345")
    assert result is True
    mock_client.delete.assert_called_once_with("/meetings/12345")


def test_list_participants():
    zoom = ZoomIntegration(_make_config())
    mock_client = MagicMock()
    mock_client.get.return_value = {
        "participants": [
            {"name": "Alice", "email": "alice@example.com"},
            {"name": "Bob", "email": "bob@example.com"},
        ]
    }
    with patch.object(zoom, "_get_client", return_value=mock_client):
        result = zoom.list_participants("12345")
    assert len(result) == 2


def test_zoom_client_token_caching():
    """Verify token is cached and not re-requested."""
    import time
    client = _ZoomClient("cid", "secret", "aid")
    client._access_token = "cached-token"
    client._token_expires_at = time.time() + 3600  # valid for 1 hour

    token = client._ensure_token()
    assert token == "cached-token"


def test_zoom_client_token_expired():
    """When token is expired, _refresh_token should be called."""
    import time
    client = _ZoomClient("cid", "secret", "aid")
    client._access_token = "old-token"
    client._token_expires_at = time.time() - 10  # expired

    with patch.object(client, "_refresh_token", return_value="new-token") as mock:
        token = client._ensure_token()
    assert token == "new-token"
    mock.assert_called_once()
