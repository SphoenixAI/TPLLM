"""Tests for Google Chat integration."""

from lltpm.config import IntegrationConfig
from lltpm.integrations.gchat_integration import GoogleChatIntegration, ChatCard


def test_gchat_webhook_mode_detection():
    webhook_config = IntegrationConfig(
        enabled=True,
        api_key="https://chat.googleapis.com/v1/spaces/AAAA/messages?key=xyz",
    )
    gchat = GoogleChatIntegration(webhook_config)
    assert gchat._is_webhook_mode is True
    assert gchat.name() == "Google Chat"


def test_gchat_api_mode_detection():
    api_config = IntegrationConfig(
        enabled=True,
        api_key="/path/to/service-account.json",
    )
    gchat = GoogleChatIntegration(api_config)
    assert gchat._is_webhook_mode is False


def test_gchat_disabled():
    config = IntegrationConfig(enabled=False)
    gchat = GoogleChatIntegration(config)
    assert gchat.test_connection() is False


def test_gchat_card_payload():
    config = IntegrationConfig(enabled=True, api_key="test")
    gchat = GoogleChatIntegration(config)

    card = ChatCard(
        title="Daily Brief",
        subtitle="LLTPM Update",
        sections=[
            {"header": "Health", "widgets": [{"textParagraph": {"text": "GREEN"}}]},
        ],
    )
    payload = gchat._build_card_payload(card)
    assert payload["header"]["title"] == "Daily Brief"
    assert payload["header"]["subtitle"] == "LLTPM Update"
    assert len(payload["sections"]) == 1


def test_gchat_webhook_test_connection():
    # Valid webhook URL
    config = IntegrationConfig(
        enabled=True,
        api_key="https://chat.googleapis.com/v1/spaces/AAAA/messages?key=xyz",
    )
    gchat = GoogleChatIntegration(config)
    assert gchat.test_connection() is True

    # Invalid webhook URL
    config2 = IntegrationConfig(
        enabled=True,
        api_key="https://example.com/webhook",
    )
    gchat2 = GoogleChatIntegration(config2)
    assert gchat2.test_connection() is False
