from __future__ import annotations

from paperlib.ai.prompts import SUMMARY_PROMPT_VERSION, build_summary_prompt


def test_summary_prompt_version_is_v1():
    assert SUMMARY_PROMPT_VERSION == "v1"


def test_prompt_contains_required_instructions():
    prompt = build_summary_prompt(cleaned_text="paper text")

    assert "single JSON object" in prompt
    assert "Do not fabricate" in prompt
    assert "No markdown fences" in prompt


def test_prompt_contains_required_schema_keys():
    prompt = build_summary_prompt(cleaned_text="paper text")

    for key in [
        "title",
        "authors",
        "journal",
        "one_sentence",
        "short",
        "technical",
        "key_contributions",
        "methods",
        "limitations",
        "physics",
        "tags",
    ]:
        assert key in prompt


def test_prompt_contains_required_physics_keys():
    prompt = build_summary_prompt(cleaned_text="paper text")

    for key in [
        "field",
        "materials",
        "devices",
        "measurements",
        "main_theory",
    ]:
        assert key in prompt


def test_prompt_includes_identifier_hints_when_provided():
    prompt = build_summary_prompt(
        cleaned_text="paper text",
        doi="10.1234/example",
        arxiv_id="2401.12345",
    )

    assert "DOI hint: 10.1234/example" in prompt
    assert "arXiv ID hint: 2401.12345" in prompt


def test_prompt_truncates_long_input_to_max_chars():
    prompt = build_summary_prompt(cleaned_text="abcXYZ", max_chars=3)

    assert "abc" in prompt
    assert "XYZ" not in prompt


def test_blank_text_still_returns_valid_prompt():
    prompt = build_summary_prompt(cleaned_text="   ")

    assert isinstance(prompt, str)
    assert "extracted text is empty" in prompt
    assert "single JSON object" in prompt
