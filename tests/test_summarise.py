from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from paperlib.ai.client import AIError
from paperlib.pipeline.summarise import (
    REQUIRED_MODEL_KEYS,
    REQUIRED_PHYSICS_KEYS,
    SummaryError,
    apply_ai_output_to_record,
    normalize_model_output,
    parse_model_json,
    summarise_record,
    strip_json_fences,
)
from paperlib.models import status as status_values
from paperlib.models.identity import PaperIdentity
from paperlib.models.record import PaperRecord


def _valid_model_output() -> dict:
    return {
        "title": "A Paper",
        "authors": ["Ada Lovelace"],
        "journal": None,
        "one_sentence": "A concise result.",
        "short": "A short summary.",
        "technical": "A technical summary.",
        "key_contributions": ["contribution"],
        "methods": ["method"],
        "limitations": [],
        "physics": {
            "field": "condensed matter",
            "materials": [],
            "devices": [],
            "measurements": [],
            "main_theory": [],
        },
        "tags": ["physics"],
    }


def test_strip_json_fences_accepts_plain_json():
    assert strip_json_fences('{"ok": true}') == '{"ok": true}'


def test_strip_json_fences_removes_json_fences():
    raw = '```json\n{"ok": true}\n```'

    assert strip_json_fences(raw) == '{"ok": true}'


def test_strip_json_fences_removes_generic_fences():
    raw = '```\n{"ok": true}\n```'

    assert strip_json_fences(raw) == '{"ok": true}'


def test_parse_model_json_accepts_valid_output():
    parsed = parse_model_json(json.dumps(_valid_model_output()))

    assert parsed["title"] == "A Paper"
    assert parsed["physics"]["field"] == "condensed matter"


def test_parse_model_json_rejects_invalid_json():
    with pytest.raises(SummaryError, match="Invalid model JSON"):
        parse_model_json("{not json")


def test_parse_model_json_rejects_list_json():
    with pytest.raises(SummaryError, match="object"):
        parse_model_json("[]")


def test_parse_model_json_rejects_missing_top_level_key():
    output = _valid_model_output()
    del output["technical"]

    with pytest.raises(SummaryError, match="technical"):
        parse_model_json(json.dumps(output))


def test_parse_model_json_rejects_missing_physics_key():
    output = _valid_model_output()
    del output["physics"]["devices"]

    with pytest.raises(SummaryError, match="devices"):
        parse_model_json(json.dumps(output))


def test_normalize_blank_title_becomes_none():
    output = _valid_model_output()
    output["title"] = "   "

    normalized = normalize_model_output(output)

    assert normalized["title"] is None


def test_normalize_numeric_title_becomes_none():
    output = _valid_model_output()
    output["title"] = 123

    normalized = normalize_model_output(output)

    assert normalized["title"] is None


def test_normalize_authors_list_is_stripped_and_blanks_removed():
    output = _valid_model_output()
    output["authors"] = [" Ada ", "", "  ", "Grace Hopper", 123]

    normalized = normalize_model_output(output)

    assert normalized["authors"] == ["Ada", "Grace Hopper"]


def test_normalize_invalid_authors_type_becomes_none():
    output = _valid_model_output()
    output["authors"] = "Ada Lovelace"

    normalized = normalize_model_output(output)

    assert normalized["authors"] is None


def test_normalize_invalid_list_fields_become_empty_lists():
    output = _valid_model_output()
    output["key_contributions"] = "not a list"
    output["methods"] = None
    output["limitations"] = 123
    output["tags"] = {"tag": "physics"}

    normalized = normalize_model_output(output)

    assert normalized["key_contributions"] == []
    assert normalized["methods"] == []
    assert normalized["limitations"] == []
    assert normalized["tags"] == []


def test_normalize_invalid_physics_list_fields_become_empty_lists():
    output = _valid_model_output()
    output["physics"]["materials"] = "silicon"
    output["physics"]["devices"] = None
    output["physics"]["measurements"] = 123
    output["physics"]["main_theory"] = {"name": "BCS"}

    normalized = normalize_model_output(output)

    assert normalized["physics"]["materials"] == []
    assert normalized["physics"]["devices"] == []
    assert normalized["physics"]["measurements"] == []
    assert normalized["physics"]["main_theory"] == []


def test_normalize_missing_or_invalid_physics_becomes_default_physics():
    output = _valid_model_output()
    output["physics"] = ["not", "a", "dict"]

    normalized = normalize_model_output(output)

    assert normalized["physics"] == {
        "field": None,
        "materials": [],
        "devices": [],
        "measurements": [],
        "main_theory": [],
    }


def test_normalize_all_required_keys_remain_present_and_input_is_unchanged():
    output = _valid_model_output()
    output["title"] = "  A Paper  "
    original_title = output["title"]

    normalized = normalize_model_output(output)

    assert REQUIRED_MODEL_KEYS <= set(normalized)
    assert REQUIRED_PHYSICS_KEYS <= set(normalized["physics"])
    assert output["title"] == original_title
    assert normalized["title"] == "A Paper"


def _apply_record(
    record: PaperRecord | None = None, normalized: dict | None = None
) -> PaperRecord:
    return apply_ai_output_to_record(
        record or PaperRecord(paper_id="p_test"),
        normalized=normalized or normalize_model_output(_valid_model_output()),
        source_file_hash="a" * 64,
        model="claude-test",
        prompt_version="v1",
        now_iso="2026-04-26T12:00:00Z",
    )


def test_apply_ai_title_authors_journal_update_unlocked_fields():
    record = _apply_record()

    assert record.metadata["title"].value == "A Paper"
    assert record.metadata["authors"].value == ["Ada Lovelace"]
    assert record.metadata["journal"].value is None
    assert record.metadata["title"].source == status_values.SOURCE_AI
    assert record.metadata["authors"].confidence == 0.70
    assert record.metadata["title"].updated_at == "2026-04-26T12:00:00Z"


def test_apply_ai_overrides_unlocked_embedded_metadata_fields():
    record = PaperRecord(paper_id="p_test")
    record.metadata["title"].value = "Embedded Title"
    record.metadata["title"].source = status_values.SOURCE_PDF_EMBEDDED_META
    record.metadata["title"].confidence = 0.60
    record.metadata["authors"].value = ["Embedded Author"]
    record.metadata["authors"].source = status_values.SOURCE_PDF_EMBEDDED_META
    record.metadata["authors"].confidence = 0.60

    updated = _apply_record(record)

    assert updated.metadata["title"].value == "A Paper"
    assert updated.metadata["title"].source == status_values.SOURCE_AI
    assert updated.metadata["authors"].value == ["Ada Lovelace"]
    assert updated.metadata["authors"].source == status_values.SOURCE_AI


def test_apply_ai_locked_title_is_not_overwritten():
    record = PaperRecord(paper_id="p_test")
    record.metadata["title"].value = "Human Title"
    record.metadata["title"].locked = True

    updated = _apply_record(record)

    assert updated.metadata["title"].value == "Human Title"
    assert updated.metadata["title"].source is None


def test_apply_ai_locked_authors_is_not_overwritten():
    record = PaperRecord(paper_id="p_test")
    record.metadata["authors"].value = ["Human Author"]
    record.metadata["authors"].locked = True

    updated = _apply_record(record)

    assert updated.metadata["authors"].value == ["Human Author"]
    assert updated.metadata["authors"].source is None


def test_apply_ai_locked_journal_is_not_overwritten():
    record = PaperRecord(paper_id="p_test")
    record.metadata["journal"].value = "Human Journal"
    record.metadata["journal"].locked = True
    normalized = normalize_model_output(_valid_model_output())
    normalized["journal"] = "AI Journal"

    updated = _apply_record(record, normalized)

    assert updated.metadata["journal"].value == "Human Journal"
    assert updated.metadata["journal"].source is None


def test_apply_ai_year_remains_unchanged_and_extra_year_is_ignored():
    record = PaperRecord(paper_id="p_test")
    record.metadata["year"].value = 2024
    record.metadata["year"].source = "filename"
    normalized = normalize_model_output(_valid_model_output())
    normalized["year"] = 2026

    updated = _apply_record(record, normalized)

    assert updated.metadata["year"].value == 2024
    assert updated.metadata["year"].source == "filename"


def test_apply_ai_summary_updates_when_unlocked():
    normalized = normalize_model_output(_valid_model_output())
    record = _apply_record(normalized=normalized)

    assert record.summary["status"] == status_values.SUMMARY_GENERATED
    assert record.summary["source_file_hash"] == "a" * 64
    assert record.summary["model"] == "claude-test"
    assert record.summary["prompt_version"] == "v1"
    assert record.summary["generated_at"] == "2026-04-26T12:00:00Z"
    assert record.summary["locked"] is False
    assert record.summary["one_sentence"] == normalized["one_sentence"]
    assert record.summary["physics"] == normalized["physics"]
    assert record.status["summary"] == status_values.SUMMARY_GENERATED


def test_apply_ai_summary_does_not_update_when_locked():
    record = PaperRecord(paper_id="p_test")
    record.summary["locked"] = True
    record.summary["status"] = status_values.SUMMARY_SKIPPED
    record.summary["one_sentence"] = "Human summary."
    record.status["summary"] = status_values.SUMMARY_SKIPPED

    updated = _apply_record(record)

    assert updated.summary["locked"] is True
    assert updated.summary["status"] == status_values.SUMMARY_SKIPPED
    assert updated.summary["one_sentence"] == "Human summary."
    assert updated.status["summary"] == status_values.SUMMARY_SKIPPED
    assert updated.metadata["title"].value == "A Paper"


def test_apply_ai_metadata_status_becomes_ok_when_title_and_authors_exist():
    record = _apply_record()

    assert record.status["metadata"] == status_values.METADATA_OK


def test_apply_ai_metadata_status_partial_if_only_doi_arxiv_or_year_exists():
    normalized = normalize_model_output(_valid_model_output())
    normalized["title"] = None
    normalized["authors"] = None
    normalized["journal"] = None

    doi_record = PaperRecord(
        paper_id="p_doi", identity=PaperIdentity(doi="10.1234/example")
    )
    arxiv_record = PaperRecord(
        paper_id="p_arxiv", identity=PaperIdentity(arxiv_id="2401.12345")
    )
    year_record = PaperRecord(paper_id="p_year")
    year_record.metadata["year"].value = 2024

    assert (
        _apply_record(doi_record, normalized).status["metadata"]
        == status_values.METADATA_PARTIAL
    )
    assert (
        _apply_record(arxiv_record, normalized).status["metadata"]
        == status_values.METADATA_PARTIAL
    )
    assert (
        _apply_record(year_record, normalized).status["metadata"]
        == status_values.METADATA_PARTIAL
    )


def test_locked_title_remains_manual_title_after_ai_apply():
    record = PaperRecord(paper_id="p_test")
    record.metadata["title"].value = "Manual Title"
    record.metadata["title"].locked = True
    normalized = normalize_model_output(_valid_model_output())
    normalized["title"] = "AI Title"

    updated = _apply_record(record, normalized)

    assert updated.metadata["title"].value == "Manual Title"


def test_locked_authors_remain_manual_authors_after_ai_apply():
    record = PaperRecord(paper_id="p_test")
    record.metadata["authors"].value = ["Manual Author"]
    record.metadata["authors"].locked = True
    normalized = normalize_model_output(_valid_model_output())
    normalized["authors"] = ["AI Author"]

    updated = _apply_record(record, normalized)

    assert updated.metadata["authors"].value == ["Manual Author"]


def test_locked_journal_remains_manual_journal_after_ai_apply():
    record = PaperRecord(paper_id="p_test")
    record.metadata["journal"].value = "Manual Journal"
    record.metadata["journal"].locked = True
    normalized = normalize_model_output(_valid_model_output())
    normalized["journal"] = "AI Journal"

    updated = _apply_record(record, normalized)

    assert updated.metadata["journal"].value == "Manual Journal"


def test_apply_ai_never_overwrites_year_from_normalized_output():
    record = PaperRecord(paper_id="p_test")
    record.metadata["year"].value = 2024
    normalized = normalize_model_output(_valid_model_output())
    normalized["year"] = 2025

    updated = _apply_record(record, normalized)

    assert updated.metadata["year"].value == 2024


def _ai_config(enabled: bool = True):
    return SimpleNamespace(
        enabled=enabled,
        model="claude-test",
        max_tokens=100,
        temperature=0.2,
    )


def test_summarise_record_no_ai_does_not_call_ai_and_sets_skipped(
    monkeypatch,
):
    called = False

    def fake_call_ai(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not call AI")

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)
    record = PaperRecord(paper_id="p_test")

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(),
        no_ai=True,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert called is False
    assert success is False
    assert error is None
    assert updated.summary["status"] == status_values.SUMMARY_SKIPPED
    assert updated.status["summary"] == status_values.SUMMARY_SKIPPED


def test_summarise_record_disabled_ai_does_not_call_ai_and_sets_skipped(
    monkeypatch,
):
    called = False

    def fake_call_ai(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not call AI")

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)
    record = PaperRecord(paper_id="p_test")

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(enabled=False),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert called is False
    assert success is False
    assert error is None
    assert updated.summary["status"] == status_values.SUMMARY_SKIPPED
    assert updated.status["summary"] == status_values.SUMMARY_SKIPPED


def test_summarise_record_locked_summary_does_not_call_or_modify_summary(
    monkeypatch,
):
    called = False

    def fake_call_ai(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not call AI")

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)
    record = PaperRecord(paper_id="p_test")
    record.summary["locked"] = True
    record.summary["status"] = status_values.SUMMARY_SKIPPED
    record.summary["one_sentence"] = "Human summary."
    record.status["summary"] = status_values.SUMMARY_SKIPPED

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert called is False
    assert success is False
    assert error is None
    assert updated.summary["locked"] is True
    assert updated.summary["status"] == status_values.SUMMARY_SKIPPED
    assert updated.summary["one_sentence"] == "Human summary."
    assert updated.status["summary"] == status_values.SUMMARY_SKIPPED


def test_summarise_record_locked_summary_preserves_manual_short(monkeypatch):
    called = False

    def fake_call_ai(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("must not call AI")

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)
    record = PaperRecord(paper_id="p_test")
    record.summary["locked"] = True
    record.summary["short"] = "Manual summary"

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(enabled=True),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert called is False
    assert success is False
    assert error is None
    assert updated.summary["short"] == "Manual summary"


def test_summarise_record_successful_mocked_ai_updates_summary(monkeypatch):
    model_output = json.dumps(_valid_model_output())
    captured = {}

    def fake_call_ai(prompt, ai_config):
        captured["prompt"] = prompt
        captured["ai_config"] = ai_config
        return model_output

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)
    record = PaperRecord(
        paper_id="p_test",
        identity=PaperIdentity(doi="10.1234/example", arxiv_id="2401.12345"),
    )

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert success is True
    assert error is None
    assert "10.1234/example" in captured["prompt"]
    assert "2401.12345" in captured["prompt"]
    assert captured["ai_config"].model == "claude-test"
    assert captured["ai_config"].max_tokens == 100
    assert captured["ai_config"].temperature == 0.2
    assert updated.summary["status"] == status_values.SUMMARY_GENERATED
    assert updated.summary["model"] == "claude-test"
    assert updated.summary["prompt_version"] == "v1"
    assert updated.summary["source_file_hash"] == "a" * 64
    assert updated.metadata["title"].value == "A Paper"


def test_summarise_record_ai_error_sets_failed_and_returns_error(monkeypatch):
    def fake_call_ai(*args, **kwargs):
        raise AIError("network unavailable")

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)
    record = PaperRecord(paper_id="p_test")

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert success is False
    assert error is not None
    assert "AIError" in error
    assert updated.summary["status"] == status_values.SUMMARY_FAILED
    assert updated.status["summary"] == status_values.SUMMARY_FAILED
    assert "warnings" not in updated.summary


def test_summarise_record_invalid_model_json_sets_failed_and_returns_error(
    monkeypatch,
):
    monkeypatch.setattr(
        "paperlib.pipeline.summarise.call_ai",
        lambda *args, **kwargs: "{not json",
    )
    record = PaperRecord(paper_id="p_test")

    updated, success, error = summarise_record(
        record,
        cleaned_text="paper text",
        source_file_hash="a" * 64,
        ai_config=_ai_config(),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert success is False
    assert error is not None
    assert "Invalid model JSON" in error
    assert updated.summary["status"] == status_values.SUMMARY_FAILED
    assert updated.status["summary"] == status_values.SUMMARY_FAILED


def test_summarise_record_error_message_does_not_contain_prompt_text(
    monkeypatch,
):
    prompt_text = "SECRET PAPER TEXT"

    def fake_call_ai(prompt, _ai_config):
        raise RuntimeError(f"failed while handling {prompt}")

    monkeypatch.setattr("paperlib.pipeline.summarise.call_ai", fake_call_ai)

    _, success, error = summarise_record(
        PaperRecord(paper_id="p_test"),
        cleaned_text=prompt_text,
        source_file_hash="a" * 64,
        ai_config=_ai_config(),
        no_ai=False,
        now_iso="2026-04-26T12:00:00Z",
    )

    assert success is False
    assert error is not None
    assert prompt_text not in error
    assert "prompt omitted" in error
