import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai.client import AiClient, AiNotConfiguredError


def run(coro):
    return asyncio.run(coro)


def test_no_key_raises_not_configured():
    c = AiClient(api_key="")
    with pytest.raises(AiNotConfiguredError):
        run(c.analyze(system="s", prompt="p"))


def test_explicit_key_overrides_settings():
    c = AiClient(api_key="explicit-key", model="claude-haiku-4-5")
    assert c.api_key == "explicit-key"
    assert c.model == "claude-haiku-4-5"


def test_analyze_returns_response_from_sdk():
    pytest.importorskip("anthropic")

    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(text="hello world")]
    fake_msg.model = "claude-sonnet-4-6"
    fake_msg.usage = MagicMock(input_tokens=42, output_tokens=17)

    fake_async_client = MagicMock()
    fake_async_client.messages.create = AsyncMock(return_value=fake_msg)

    with patch("anthropic.AsyncAnthropic", return_value=fake_async_client) as MockAnthropic:
        c = AiClient(api_key="test-key", model="claude-sonnet-4-6")
        result = run(c.analyze(system="sys", prompt="p", max_tokens=512))

    MockAnthropic.assert_called_once_with(api_key="test-key")
    create_kwargs = fake_async_client.messages.create.await_args.kwargs
    assert create_kwargs["model"] == "claude-sonnet-4-6"
    assert create_kwargs["system"] == "sys"
    assert create_kwargs["messages"] == [{"role": "user", "content": "p"}]
    assert create_kwargs["max_tokens"] == 512

    assert result.text == "hello world"
    assert result.model == "claude-sonnet-4-6"
    assert result.input_tokens == 42
    assert result.output_tokens == 17
