"""Slack integration — send updates, request info, nudge owners."""

from __future__ import annotations

from dataclasses import dataclass

from tpllm.integrations.base import Integration, IntegrationError


@dataclass
class SlackMessage:
    channel: str
    text: str
    thread_ts: str = ""


class SlackIntegration(Integration):
    """Slack integration for async communication."""

    def name(self) -> str:
        return "Slack"

    def test_connection(self) -> bool:
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            resp = client.auth_test()
            return resp["ok"]
        except Exception:
            return False

    def send_message(self, channel: str, text: str, thread_ts: str = "") -> str:
        """Send a message to a Slack channel. Returns the message timestamp."""
        self._require_enabled()
        try:
            client = self._get_client()
            kwargs = {"channel": channel, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            resp = client.chat_postMessage(**kwargs)
            return resp["ts"]
        except Exception as e:
            raise IntegrationError(f"Slack send failed: {e}") from e

    def send_structured_update(
        self,
        channel: str,
        title: str,
        sections: list[dict[str, str]],
    ) -> str:
        """Send a structured update with blocks."""
        self._require_enabled()
        blocks = [
            {"type": "header", "text": {"type": "plain_text", "text": title}},
        ]
        for section in sections:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": section.get("text", "")},
            })
        try:
            client = self._get_client()
            resp = client.chat_postMessage(
                channel=channel,
                text=title,
                blocks=blocks,
            )
            return resp["ts"]
        except Exception as e:
            raise IntegrationError(f"Slack structured update failed: {e}") from e

    def request_update(self, channel: str, owner: str, topic: str, deadline: str) -> str:
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
        return self.send_message(channel, text)

    def _get_client(self):
        from slack_sdk import WebClient
        return WebClient(token=self._config.api_key)

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise IntegrationError("Slack integration is not configured.")
