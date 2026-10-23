"""Provider selection stays explicit while both API backends remain usable."""

from unittest.mock import MagicMock, patch

import pytest

from tpllm.agent import TPMAgent
from tpllm.cli import run_config_wizard, save_json_config
from tpllm.config import TPLLMConfig


@pytest.mark.parametrize("provider,model", [("", ""), ("unknown", "model"), ("anthropic", " ")])
def test_unconfigured_agent_fails_before_creating_local_state(tmp_path, provider, model):
    data_dir = tmp_path / "state"
    with pytest.raises(ValueError, match="TPLLM_PROVIDER|TPLLM_MODEL"):
        TPMAgent(TPLLMConfig(provider=provider, model=model, data_dir=data_dir))
    assert not data_dir.exists()


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_explicit_provider_uses_requested_model_without_network(tmp_path, provider):
    cfg = TPLLMConfig(provider=provider, model="user-chosen-model", data_dir=tmp_path)
    client = MagicMock()
    if provider == "anthropic":
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(type="text", text="ready")], stop_reason="end_turn"
        )
        target = "tpllm.agent.anthropic.Anthropic"
    else:
        # The backend is optional; the core-only CI job still tests Anthropic.
        pytest.importorskip("openai")
        message = MagicMock(content="ready", tool_calls=[])
        client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=message, finish_reason="stop")]
        )
        target = "openai.OpenAI"
    with patch(target, return_value=client):
        assert TPMAgent(cfg).run_to_completion("hello") == "ready"
    request = client.messages.create if provider == "anthropic" else client.chat.completions.create
    assert request.call_args.kwargs["model"] == "user-chosen-model"


@pytest.mark.parametrize("provider", ["anthropic", "openai"])
def test_wizard_accepts_unlisted_model_and_saves_provider(tmp_path, monkeypatch, provider):
    answers = [str(tmp_path), provider, "test-key"]
    if provider == "openai":
        answers.append("https://example.invalid/v1")
    answers.extend(["", "new-custom-model"])
    with patch("tpllm.cli.Prompt.ask", side_effect=answers), patch("tpllm.cli.Confirm.ask", return_value=False):
        values = run_config_wizard(tmp_path)
    assert values["TPLLM_MODEL"] == "new-custom-model"
    save_json_config(values, tmp_path)
    monkeypatch.setenv("TPLLM_DATA_DIR", str(tmp_path))
    config = TPLLMConfig.from_env()
    assert config.provider == provider
    assert config.model == "new-custom-model"
