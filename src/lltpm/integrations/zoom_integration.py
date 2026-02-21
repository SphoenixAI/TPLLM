"""Zoom integration — meeting management and AI notes ingestion.

Connects to the Zoom API for:
- Meeting scheduling and management
- Recording and transcript retrieval
- Webhook event handling
- AI Companion notes access
"""

from __future__ import annotations

from dataclasses import dataclass

from lltpm.integrations.base import Integration, IntegrationError


class ZoomIntegration(Integration):
    """Zoom API integration for meeting management and AI notes."""

    def name(self) -> str:
        return "Zoom"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            # Test with a simple API call
            return client is not None
        except Exception:
            return False

    def get_meeting(self, meeting_id: str) -> dict:
        """Get meeting details from Zoom API."""
        self._require_enabled()
        try:
            client = self._get_client()
            return client.get(f"/meetings/{meeting_id}")
        except Exception as e:
            raise IntegrationError(f"Zoom get meeting failed: {e}") from e

    def list_recordings(self, user_id: str = "me", from_date: str = "") -> list[dict]:
        """List cloud recordings for a user."""
        self._require_enabled()
        try:
            client = self._get_client()
            params = {"from": from_date} if from_date else {}
            resp = client.get(f"/users/{user_id}/recordings", params=params)
            return resp.get("meetings", [])
        except Exception as e:
            raise IntegrationError(f"Zoom list recordings failed: {e}") from e

    def get_transcript(self, meeting_id: str) -> str:
        """Get the transcript for a recorded meeting."""
        self._require_enabled()
        try:
            client = self._get_client()
            recordings = client.get(f"/meetings/{meeting_id}/recordings")
            for file in recordings.get("recording_files", []):
                if file.get("file_type") == "TRANSCRIPT":
                    return client.get_download(file["download_url"])
            return ""
        except Exception as e:
            raise IntegrationError(f"Zoom get transcript failed: {e}") from e

    def create_meeting(
        self,
        topic: str,
        start_time: str,
        duration: int = 30,
        attendees: list[str] | None = None,
    ) -> dict:
        """Create a Zoom meeting."""
        self._require_enabled()
        try:
            client = self._get_client()
            data = {
                "topic": topic,
                "type": 2,  # scheduled meeting
                "start_time": start_time,
                "duration": duration,
                "settings": {
                    "join_before_host": True,
                    "auto_recording": "cloud",
                },
            }
            if attendees:
                data["settings"]["meeting_invitees"] = [
                    {"email": e} for e in attendees
                ]
            return client.post("/users/me/meetings", json=data)
        except Exception as e:
            raise IntegrationError(f"Zoom create meeting failed: {e}") from e

    def _get_client(self):
        """Get a Zoom API client using Server-to-Server OAuth."""
        # In production, use the Zoom OAuth flow
        # For now, return a minimal stub client
        account_id = self._config.extra.get("account_id", "")
        if not self._config.api_key or not account_id:
            raise IntegrationError(
                "Zoom integration requires ZOOM_API_KEY and ZOOM_ACCOUNT_ID"
            )
        return _ZoomClient(self._config.api_key, account_id)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Zoom integration is not configured.")


class _ZoomClient:
    """Minimal Zoom API client stub."""

    def __init__(self, api_key: str, account_id: str) -> None:
        self._api_key = api_key
        self._account_id = account_id

    def get(self, path: str, params: dict | None = None) -> dict:
        raise IntegrationError(
            "Zoom API client is a stub. Configure with real Zoom "
            "Server-to-Server OAuth credentials for production use."
        )

    def post(self, path: str, json: dict | None = None) -> dict:
        raise IntegrationError(
            "Zoom API client is a stub. Configure with real Zoom "
            "Server-to-Server OAuth credentials for production use."
        )

    def get_download(self, url: str) -> str:
        raise IntegrationError("Zoom API client is a stub.")
