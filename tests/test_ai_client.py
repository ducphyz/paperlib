from __future__ import annotations

from types import SimpleNamespace

import pytest

from paperlib.ai.client import AIError, call_anthropic
from paperlib.ai import client as ai_client


def test_missing_anthropic_api_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    with pytest.raises(AIError, match="ANTHROPIC_API_KEY"):
        call_anthropic(
            "summarize",
            model="claude-test",
            max_tokens=100,
            temperature=0.2,
        )


def test_successful_response_returns_first_text_block(monkeypatch):
    created = {}

    class FakeMessages:
        def create(self, **kwargs):
            created.update(kwargs)
            return SimpleNamespace(
                content=[
                    {"type": "image", "source": "ignored"},
                    SimpleNamespace(type="text", text='{"ok": true}'),
                ]
            )

    class FakeAnthropic:
        def __init__(self, **kwargs):
            created["client_kwargs"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai_client.anthropic, "Anthropic", FakeAnthropic)

    result = call_anthropic(
        "summarize",
        model="claude-test",
        max_tokens=100,
        temperature=0.2,
        timeout_s=12,
    )

    assert result == '{"ok": true}'
    assert created["client_kwargs"]["api_key"] == "test-key"
    assert created["client_kwargs"]["timeout"] == 12
    assert created["model"] == "claude-test"
    assert created["max_tokens"] == 100
    assert created["temperature"] == 0.2
    assert created["messages"] == [{"role": "user", "content": "summarize"}]


def test_sdk_exception_is_wrapped_as_ai_error(monkeypatch):
    class FakeMessages:
        def create(self, **kwargs):
            raise RuntimeError("sdk exploded")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai_client.anthropic, "Anthropic", FakeAnthropic)

    with pytest.raises(AIError, match="Anthropic request failed"):
        call_anthropic(
            "summarize",
            model="claude-test",
            max_tokens=100,
            temperature=0.2,
        )


def test_response_without_text_block_raises(monkeypatch):
    class FakeMessages:
        def create(self, **kwargs):
            return {"content": [{"type": "tool_use", "name": "ignored"}]}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai_client.anthropic, "Anthropic", FakeAnthropic)

    with pytest.raises(AIError, match="text block"):
        call_anthropic(
            "summarize",
            model="claude-test",
            max_tokens=100,
            temperature=0.2,
        )


def test_client_falls_back_when_timeout_is_not_supported(monkeypatch):
    created = {}

    class FakeMessages:
        def create(self, **kwargs):
            return {"content": [{"type": "text", "text": "ok"}]}

    class FakeAnthropic:
        def __init__(self, **kwargs):
            if "timeout" in kwargs:
                raise TypeError("unexpected timeout")
            created["client_kwargs"] = kwargs
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai_client.anthropic, "Anthropic", FakeAnthropic)

    assert call_anthropic(
        "summarize",
        model="claude-test",
        max_tokens=100,
        temperature=0.2,
    ) == "ok"
    assert created["client_kwargs"] == {"api_key": "test-key"}
