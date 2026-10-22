"""Zoom integration — meeting management and AI notes ingestion.

Connects to the Zoom API for:
- Meeting scheduling and management
- Recording and transcript retrieval
- Webhook event handling
- AI Companion notes access

Uses Zoom Server-to-Server OAuth for authentication:
- ZOOM_API_KEY = Client ID
- ZOOM_CLIENT_SECRET = Client Secret
- ZOOM_ACCOUNT_ID = Account ID
"""

from __future__ import annotations

import json
import time
import threading
from dataclasses import dataclass

from tpllm.integrations.base import Integration, IntegrationError


class ZoomIntegration(Integration):
    """Zoom API integration for meeting management and AI notes."""

    def name(self) -> str:
        return "Zoom"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            client.get("/users/me")
            return True
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

    def list_meetings(self, user_id: str = "me", meeting_type: str = "scheduled") -> list[dict]:
        """List meetings for a user."""
        self._require_enabled()
        try:
            client = self._get_client()
            resp = client.get(f"/users/{user_id}/meetings", params={"type": meeting_type})
            return resp.get("meetings", [])
        except Exception as e:
            raise IntegrationError(f"Zoom list meetings failed: {e}") from e

    def list_recordings(self, user_id: str = "me", from_date: str = "") -> list[dict]:
        """List cloud recordings for a user."""
        self._require_enabled()
        try:
            client = self._get_client()
            params: dict[str, str] = {}
            if from_date:
                params["from"] = from_date
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
        agenda: str = "",
    ) -> dict:
        """Create a Zoom meeting."""
        self._require_enabled()
        try:
            client = self._get_client()
            data: dict = {
                "topic": topic,
                "type": 2,  # scheduled meeting
                "start_time": start_time,
                "duration": duration,
                "settings": {
                    "join_before_host": True,
                    "auto_recording": "cloud",
                    "meeting_authentication": True,
                },
            }
            if agenda:
                data["agenda"] = agenda
            if attendees:
                data["settings"]["meeting_invitees"] = [
                    {"email": e} for e in attendees
                ]
            return client.post("/users/me/meetings", json_data=data)
        except Exception as e:
            raise IntegrationError(f"Zoom create meeting failed: {e}") from e

    def delete_meeting(self, meeting_id: str) -> bool:
        """Delete/cancel a Zoom meeting."""
        self._require_enabled()
        try:
            client = self._get_client()
            client.delete(f"/meetings/{meeting_id}")
            return True
        except Exception as e:
            raise IntegrationError(f"Zoom delete meeting failed: {e}") from e

    def list_participants(self, meeting_id: str) -> list[dict]:
        """List participants of a past meeting."""
        self._require_enabled()
        try:
            client = self._get_client()
            resp = client.get(f"/past_meetings/{meeting_id}/participants")
            return resp.get("participants", [])
        except Exception as e:
            raise IntegrationError(f"Zoom list participants failed: {e}") from e

    def _get_client(self):
        """Get a Zoom API client using Server-to-Server OAuth."""
        client_id = self._config.api_key
        account_id = self._config.extra.get("account_id", "")
        client_secret = self._config.extra.get("client_secret", "")
        if not client_id or not account_id:
            raise IntegrationError(
                "Zoom integration requires ZOOM_API_KEY (Client ID) and ZOOM_ACCOUNT_ID"
            )
        if not client_secret:
            raise IntegrationError(
                "Zoom integration requires ZOOM_CLIENT_SECRET for Server-to-Server OAuth"
            )
        return _ZoomClient(client_id, client_secret, account_id)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Zoom integration is not configured.")


class _ZoomClient:
    """Zoom API client with Server-to-Server OAuth token management."""

    TOKEN_URL = "https://zoom.us/oauth/token"
    API_BASE = "https://api.zoom.us/v2"

    def __init__(self, client_id: str, client_secret: str, account_id: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._account_id = account_id
        self._access_token: str = ""
        self._token_expires_at: float = 0.0
        self._lock = threading.Lock()

    def _ensure_token(self) -> str:
        """Get a valid access token, refreshing if expired."""
        with self._lock:
            if self._access_token and time.time() < self._token_expires_at - 60:
                return self._access_token
            return self._refresh_token()

    def _refresh_token(self) -> str:
        """Request a new access token from Zoom OAuth."""
        import urllib.request
        import urllib.parse
        import base64

        credentials = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        data = urllib.parse.urlencode({
            "grant_type": "account_credentials",
            "account_id": self._account_id,
        }).encode()

        req = urllib.request.Request(
            self.TOKEN_URL,
            data=data,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                token_data = json.loads(resp.read())
        except Exception as e:
            raise IntegrationError(f"Zoom OAuth token request failed: {e}") from e

        self._access_token = token_data.get("access_token", "")
        expires_in = token_data.get("expires_in", 3600)
        self._token_expires_at = time.time() + expires_in

        if not self._access_token:
            raise IntegrationError("Zoom OAuth returned empty access token.")
        return self._access_token

    def get(self, path: str, params: dict[str, str] | None = None) -> dict:
        import urllib.request
        import urllib.parse

        token = self._ensure_token()
        url = f"{self.API_BASE}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)

        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def post(self, path: str, json_data: dict | None = None) -> dict:
        import urllib.request

        token = self._ensure_token()
        url = f"{self.API_BASE}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(json_data or {}).encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def delete(self, path: str) -> None:
        import urllib.request

        token = self._ensure_token()
        url = f"{self.API_BASE}{path}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            method="DELETE",
        )
        with urllib.request.urlopen(req) as resp:
            resp.read()  # consume response

    def get_download(self, url: str) -> str:
        import urllib.request

        token = self._ensure_token()
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req) as resp:
            return resp.read().decode("utf-8", errors="replace")
