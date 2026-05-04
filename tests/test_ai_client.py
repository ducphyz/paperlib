from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from paperlib.ai.client import (
    AIError,
    OPENROUTER_DEFAULT_BASE_URL,
    call_ai,
    call_anthropic,
    call_openai_compatible,
    split_model_string,
)
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


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-4-5", ("anthropic", "claude-sonnet-4-5")),
        ("anthropic:claude-sonnet-4-5", ("anthropic", "claude-sonnet-4-5")),
        ("openai:gpt-4o", ("openai", "gpt-4o")),
        (
            "openrouter:meta-llama/llama-3.3-70b-instruct",
            ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
        ),
        ("openai-compat:local-model", ("openai-compat", "local-model")),
    ],
)
def test_split_model_string(model, expected):
    assert split_model_string(model) == expected


def test_split_model_string_rejects_unknown_prefix():
    with pytest.raises(AIError, match="Unknown AI model provider prefix"):
        split_model_string("other:model")


def test_call_ai_dispatches_to_anthropic(monkeypatch):
    captured = {}

    def fake_call_anthropic(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "anthropic response"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(ai_client, "call_anthropic", fake_call_anthropic)

    result = call_ai(
        "prompt",
        SimpleNamespace(
            model="claude-test",
            max_tokens=100,
            temperature=0.2,
            api_key_env="ANTHROPIC_API_KEY",
            base_url=None,
        ),
    )

    assert result == "anthropic response"
    assert captured["prompt"] == "prompt"
    assert captured["kwargs"] == {
        "model": "claude-test",
        "max_tokens": 100,
        "temperature": 0.2,
        "api_key_env": "ANTHROPIC_API_KEY",
    }


def test_call_ai_dispatches_to_openai_compatible(monkeypatch):
    captured = {}

    def fake_call_openai_compatible(prompt, **kwargs):
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return "openai response"

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        ai_client, "call_openai_compatible", fake_call_openai_compatible
    )

    result = call_ai(
        "prompt",
        SimpleNamespace(
            model="openai:gpt-4o",
            max_tokens=100,
            temperature=0.2,
            api_key_env="OPENAI_API_KEY",
            base_url=None,
        ),
    )

    assert result == "openai response"
    assert captured["prompt"] == "prompt"
    assert captured["kwargs"] == {
        "model": "gpt-4o",
        "base_url": None,
        "api_key": "test-key",
        "max_tokens": 100,
        "temperature": 0.2,
    }


def test_call_ai_dispatches_openrouter_with_default_base_url(monkeypatch):
    captured = {}

    def fake_call_openai_compatible(prompt, **kwargs):
        captured.update(kwargs)
        return "openrouter response"

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(
        ai_client, "call_openai_compatible", fake_call_openai_compatible
    )

    assert (
        call_ai(
            "prompt",
            SimpleNamespace(
                model="openrouter:meta-llama/test",
                max_tokens=100,
                temperature=0.2,
                api_key_env="OPENROUTER_API_KEY",
                base_url=None,
            ),
        )
        == "openrouter response"
    )
    assert captured["model"] == "meta-llama/test"
    assert captured["base_url"] == OPENROUTER_DEFAULT_BASE_URL


def test_call_ai_missing_api_key_raises_ai_error(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(AIError, match="OPENAI_API_KEY"):
        call_ai(
            "prompt",
            SimpleNamespace(
                model="openai:gpt-4o",
                max_tokens=100,
                temperature=0.2,
                api_key_env="OPENAI_API_KEY",
                base_url=None,
            ),
        )


def test_call_ai_openai_compat_requires_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with pytest.raises(AIError, match="base_url"):
        call_ai(
            "prompt",
            SimpleNamespace(
                model="openai-compat:local-model",
                max_tokens=100,
                temperature=0.2,
                api_key_env="OPENAI_API_KEY",
                base_url=None,
            ),
        )


def test_openai_compatible_response_returns_text(monkeypatch):
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["request"] = kwargs
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"ok": true}')
                    )
                ]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(
                completions=FakeCompletions(),
            )

    monkeypatch.setattr(ai_client, "OpenAI", FakeOpenAI)

    result = call_openai_compatible(
        "prompt",
        model="gpt-4o",
        base_url="https://example.test/v1",
        api_key="test-key",
        max_tokens=100,
        temperature=0.2,
        timeout_s=12,
    )

    assert result == '{"ok": true}'
    assert captured["client"] == {
        "api_key": "test-key",
        "timeout": 12,
        "base_url": "https://example.test/v1",
    }
    assert captured["request"]["messages"] == [
        {"role": "user", "content": "prompt"}
    ]


@pytest.mark.skipif(
    os.getenv("PAPERLIB_TEST_OPENAI") != "1",
    reason="PAPERLIB_TEST_OPENAI not enabled",
)
def test_openai_live_smoke():
    result = call_ai(
        "Return only {\"ok\": true}",
        SimpleNamespace(
            model=os.getenv("PAPERLIB_TEST_OPENAI_MODEL", "openai:gpt-4o"),
            max_tokens=30,
            temperature=0.0,
            api_key_env="OPENAI_API_KEY",
            base_url=None,
        ),
    )

    assert result.strip()


@pytest.mark.skipif(
    os.getenv("PAPERLIB_TEST_OPENROUTER") != "1",
    reason="PAPERLIB_TEST_OPENROUTER not enabled",
)
def test_openrouter_live_smoke():
    result = call_ai(
        "Return only {\"ok\": true}",
        SimpleNamespace(
            model=os.getenv(
                "PAPERLIB_TEST_OPENROUTER_MODEL",
                "openrouter:meta-llama/llama-3.3-70b-instruct:free",
            ),
            max_tokens=30,
            temperature=0.0,
            api_key_env="OPENROUTER_API_KEY",
            base_url=None,
        ),
    )

    assert result.strip()
