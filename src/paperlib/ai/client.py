from __future__ import annotations

import logging
import os
from typing import Any

import anthropic


ANTHROPIC_PROVIDER = "anthropic"
OPENAI_PROVIDER = "openai"
OPENROUTER_PROVIDER = "openrouter"
OPENAI_COMPAT_PROVIDER = "openai-compat"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

_PROVIDER_PREFIXES = {
    ANTHROPIC_PROVIDER,
    OPENAI_PROVIDER,
    OPENROUTER_PROVIDER,
    OPENAI_COMPAT_PROVIDER,
}

logger = logging.getLogger("paperlib.ai.client")


class AIError(Exception):
    pass


def split_model_string(model: str) -> tuple[str, str]:
    value = model.strip()
    if not value:
        raise AIError("AI model must not be empty")

    if ":" not in value:
        return ANTHROPIC_PROVIDER, value

    provider, provider_model = value.split(":", 1)
    provider = provider.strip().lower()
    provider_model = provider_model.strip()
    if provider not in _PROVIDER_PREFIXES:
        supported = ", ".join(sorted(_PROVIDER_PREFIXES))
        raise AIError(
            f"Unknown AI model provider prefix: {provider!r}. "
            f"Supported prefixes: {supported}."
        )
    if not provider_model:
        raise AIError(f"AI model missing after provider prefix: {provider}:")
    return provider, provider_model


def default_api_key_env(provider: str) -> str:
    if provider == ANTHROPIC_PROVIDER:
        return "ANTHROPIC_API_KEY"
    if provider == OPENAI_PROVIDER:
        return "OPENAI_API_KEY"
    if provider == OPENROUTER_PROVIDER:
        return "OPENROUTER_API_KEY"
    if provider == OPENAI_COMPAT_PROVIDER:
        return "OPENAI_API_KEY"
    raise AIError(f"Unknown AI provider: {provider}")


def call_ai(prompt: str, ai_config) -> str:
    provider, provider_model = split_model_string(ai_config.model)
    api_key_env = getattr(ai_config, "api_key_env", None) or default_api_key_env(
        provider
    )
    api_key = os.getenv(api_key_env)
    if api_key is None or not api_key.strip():
        raise AIError(f"{api_key_env} is required for AI requests")

    base_url = _resolved_base_url(provider, getattr(ai_config, "base_url", None))
    logger.info(
        "AI request provider=%s model=%s base_url=%s",
        provider,
        provider_model,
        base_url or "<default>",
    )

    if provider == ANTHROPIC_PROVIDER:
        return call_anthropic(
            prompt,
            model=provider_model,
            max_tokens=ai_config.max_tokens,
            temperature=ai_config.temperature,
            api_key_env=api_key_env,
        )

    return call_openai_compatible(
        prompt,
        model=provider_model,
        base_url=base_url,
        api_key=api_key.strip(),
        max_tokens=ai_config.max_tokens,
        temperature=ai_config.temperature,
    )


def call_anthropic(
    prompt: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout_s: int = 60,
    api_key_env: str = "ANTHROPIC_API_KEY",
) -> str:
    api_key = os.getenv(api_key_env)
    if api_key is None or not api_key.strip():
        raise AIError(f"{api_key_env} is required for AI requests")

    try:
        client = _build_client(api_key.strip(), timeout_s)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise AIError(f"Anthropic request failed: {exc}") from exc

    text = _first_text_block(response)
    if text is None:
        raise AIError("Anthropic response did not contain a text block")
    return text


from openai import OpenAI


def call_openai_compatible(
    prompt: str,
    *,
    model: str,
    base_url: str | None,
    api_key: str,
    max_tokens: int,
    temperature: float,
    timeout_s: int = 60,
) -> str:
    try:
        kwargs: dict[str, Any] = {"api_key": api_key, "timeout": timeout_s}
        if base_url is not None:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise AIError(f"OpenAI-compatible request failed: {exc}") from exc

    text = _first_chat_message_text(response)
    if text is None:
        raise AIError("OpenAI-compatible response did not contain text")
    return text


def _resolved_base_url(provider: str, base_url: str | None) -> str | None:
    if base_url is not None and base_url.strip():
        return base_url.strip()
    if provider == OPENROUTER_PROVIDER:
        return OPENROUTER_DEFAULT_BASE_URL
    if provider == OPENAI_COMPAT_PROVIDER:
        raise AIError("openai-compat provider requires ai.base_url")
    return None


def _build_client(api_key: str, timeout_s: int):
    try:
        return anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
    except TypeError:
        return anthropic.Anthropic(api_key=api_key)


def _first_text_block(response: Any) -> str | None:
    content = _get_value(response, "content", [])
    for block in content:
        if _get_value(block, "type") == "text":
            text = _get_value(block, "text")
            if isinstance(text, str):
                return text
    return None


def _first_chat_message_text(response: Any) -> str | None:
    choices = _get_value(response, "choices", [])
    for choice in choices:
        message = _get_value(choice, "message")
        content = _get_value(message, "content")
        if isinstance(content, str):
            return content
    return None


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
