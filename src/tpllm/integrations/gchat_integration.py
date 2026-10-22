"""Google Chat integration — send updates, request info, post cards.

Supports both Google Workspace Chat (via REST API with service account
or OAuth) and webhook-based posting for simpler setups.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from tpllm.integrations.base import Integration, IntegrationError


@dataclass
class ChatCard:
    """A Google Chat card message."""

    title: str
    subtitle: str = ""
    sections: list[dict[str, Any]] | None = None


class GoogleChatIntegration(Integration):
    """Google Chat integration for async communication.

    Supports two modes:
    - **Webhook mode**: Set api_key to the webhook URL. Simple, no auth needed.
    - **API mode**: Set api_key to service account JSON path or OAuth token,
      base_url to the Chat API endpoint.
    """

    def name(self) -> str:
        return "Google Chat"

    @property
    def _is_webhook_mode(self) -> bool:
        return self._config.api_key.startswith("https://")

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            if self._is_webhook_mode:
                # Webhook mode — just verify the URL looks valid
                return "chat.googleapis.com" in self._config.api_key
            # API mode — try a simple auth check
            client = self._get_api_client()
            client.spaces().list(pageSize=1).execute()
            return True
        except Exception:
            return False

    # ── Webhook mode ──────────────────────────────────────────

    def send_webhook_message(self, text: str) -> dict[str, Any]:
        """Post a simple text message via webhook."""
        self._require_enabled()
        if not self._is_webhook_mode:
            raise IntegrationError("send_webhook_message requires webhook mode (api_key is a URL).")
        import urllib.request

        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            self._config.api_key,
            data=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise IntegrationError(f"Google Chat webhook send failed: {e}") from e

    def send_webhook_card(self, card: ChatCard) -> dict[str, Any]:
        """Post a card message via webhook."""
        self._require_enabled()
        if not self._is_webhook_mode:
            raise IntegrationError("send_webhook_card requires webhook mode.")
        import urllib.request

        card_payload = self._build_card_payload(card)
        payload = json.dumps({"cardsV2": [{"cardId": "tpllm-card", "card": card_payload}]}).encode("utf-8")
        req = urllib.request.Request(
            self._config.api_key,
            data=payload,
            headers={"Content-Type": "application/json; charset=UTF-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise IntegrationError(f"Google Chat card send failed: {e}") from e

    # ── API mode ──────────────────────────────────────────────

    def send_message(self, space: str, text: str, thread_key: str = "") -> dict[str, Any]:
        """Send a message to a Google Chat space via the API.

        Args:
            space: Space resource name (e.g. "spaces/AAAA...")
            text: Message text
            thread_key: Optional thread key for threading
        """
        self._require_enabled()
        try:
            client = self._get_api_client()
            body: dict[str, Any] = {"text": text}
            if thread_key:
                body["thread"] = {"threadKey": thread_key}
            resp = client.spaces().messages().create(
                parent=space,
                body=body,
                messageReplyOption="REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" if thread_key else None,
            ).execute()
            return {"name": resp.get("name", ""), "thread_name": resp.get("thread", {}).get("name", "")}
        except Exception as e:
            raise IntegrationError(f"Google Chat send failed: {e}") from e

    def send_card_message(self, space: str, card: ChatCard, thread_key: str = "") -> dict[str, Any]:
        """Send a rich card message to a Google Chat space."""
        self._require_enabled()
        try:
            client = self._get_api_client()
            card_payload = self._build_card_payload(card)
            body: dict[str, Any] = {
                "cardsV2": [{"cardId": "tpllm-card", "card": card_payload}],
            }
            if thread_key:
                body["thread"] = {"threadKey": thread_key}
            resp = client.spaces().messages().create(
                parent=space,
                body=body,
                messageReplyOption="REPLY_MESSAGE_FALLBACK_TO_NEW_THREAD" if thread_key else None,
            ).execute()
            return {"name": resp.get("name", ""), "thread_name": resp.get("thread", {}).get("name", "")}
        except Exception as e:
            raise IntegrationError(f"Google Chat card send failed: {e}") from e

    def send_structured_update(
        self,
        space: str,
        title: str,
        sections: list[dict[str, str]],
        thread_key: str = "",
    ) -> dict[str, Any]:
        """Send a structured TPM update as a card.

        Args:
            space: Space resource name or webhook URL
            title: Card header title
            sections: List of dicts with 'header' and 'text' keys
            thread_key: Optional thread key
        """
        card = ChatCard(
            title=title,
            subtitle="TPLLM Status Update",
            sections=[
                {"header": s.get("header", ""), "widgets": [{"textParagraph": {"text": s.get("text", "")}}]}
                for s in sections
            ],
        )
        if self._is_webhook_mode:
            return self.send_webhook_card(card)
        return self.send_card_message(space, card, thread_key)

    def request_update(
        self,
        space: str,
        owner: str,
        topic: str,
        deadline: str,
        thread_key: str = "",
    ) -> dict[str, Any]:
        """Send a structured update request to an owner."""
        text = (
            f"*Update request for {owner}*\n"
            f"Topic: {topic}\n"
            f"Please respond by: {deadline}\n\n"
            f"Format:\n"
            f"- *Yesterday*: What was done\n"
            f"- *Today*: What's planned\n"
            f"- *Blockers*: Anything blocking progress\n"
            f"- *ETA confidence*: On track / At risk / Blocked"
        )
        if self._is_webhook_mode:
            return self.send_webhook_message(text)
        return self.send_message(space, text, thread_key)

    def list_spaces(self, page_size: int = 20) -> list[dict[str, str]]:
        """List available Google Chat spaces."""
        self._require_enabled()
        try:
            client = self._get_api_client()
            resp = client.spaces().list(pageSize=page_size).execute()
            return [
                {"name": s.get("name", ""), "display_name": s.get("displayName", ""), "type": s.get("type", "")}
                for s in resp.get("spaces", [])
            ]
        except Exception as e:
            raise IntegrationError(f"Google Chat list spaces failed: {e}") from e

    # ── internal helpers ──────────────────────────────────────

    def _build_card_payload(self, card: ChatCard) -> dict[str, Any]:
        """Build a Google Chat Card v2 payload."""
        payload: dict[str, Any] = {
            "header": {
                "title": card.title,
            },
        }
        if card.subtitle:
            payload["header"]["subtitle"] = card.subtitle
        if card.sections:
            payload["sections"] = card.sections
        return payload

    def _get_api_client(self):
        """Get an authenticated Google Chat API client."""
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError:
            raise IntegrationError(
                "Google Chat API mode requires google-auth and google-api-python-client. "
                "Install with: pip install google-auth google-api-python-client"
            )

        sa_key_path = self._config.extra.get("service_account_key", self._config.api_key)
        scopes = ["https://www.googleapis.com/auth/chat.bot"]

        credentials = service_account.Credentials.from_service_account_file(
            sa_key_path, scopes=scopes
        )
        return build("chat", "v1", credentials=credentials)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Google Chat integration is not configured.")
