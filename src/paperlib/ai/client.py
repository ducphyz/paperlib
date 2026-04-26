from __future__ import annotations

import os
from typing import Any

import anthropic


class AIError(Exception):
    pass


def call_anthropic(
    prompt: str,
    *,
    model: str,
    max_tokens: int,
    temperature: float,
    timeout_s: int = 60,
) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key is None or not api_key.strip():
        raise AIError("ANTHROPIC_API_KEY is required for AI requests")

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


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)
