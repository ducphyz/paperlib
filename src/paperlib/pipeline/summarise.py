from __future__ import annotations

import json

from paperlib.ai.client import AIError, call_ai
from paperlib.ai.prompts import SUMMARY_PROMPT_VERSION, build_summary_prompt
from paperlib.models import status as status_values
from paperlib.models.record import PaperRecord
from paperlib.utils import metadata_status, utc_now


REQUIRED_MODEL_KEYS = {
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
}

REQUIRED_SUMMARY_KEYS = {
    "status",
    "locked",
    "source_file_hash",
    "one_sentence",
    "short",
    "technical",
    "key_contributions",
    "methods",
    "limitations",
    "physics",
    "tags",
}

REQUIRED_PHYSICS_KEYS = {
    "field",
    "materials",
    "devices",
    "measurements",
    "main_theory",
}


class SummaryError(Exception):
    pass


def summarise_record(
    record: PaperRecord,
    *,
    cleaned_text: str,
    source_file_hash: str,
    ai_config,
    no_ai: bool,
    now_iso: str | None = None,
) -> tuple[PaperRecord, bool, str | None]:
    if now_iso is None:
        now_iso = utc_now()

    if no_ai or not getattr(ai_config, "enabled", True):
        _mark_summary_skipped(record)
        return record, False, None

    if record.summary.get("locked", False):
        return record, False, None

    prompt = ""
    try:
        prompt = build_summary_prompt(
            cleaned_text=cleaned_text,
            doi=record.identity.doi,
            arxiv_id=record.identity.arxiv_id,
            max_chars=40000,
        )
        raw_text = call_ai(prompt, ai_config)
        parsed = parse_model_json(raw_text)
        normalized = normalize_model_output(parsed)
        record = apply_ai_output_to_record(
            record,
            normalized=normalized,
            source_file_hash=source_file_hash,
            model=ai_config.model,
            prompt_version=SUMMARY_PROMPT_VERSION,
            now_iso=now_iso,
        )
    except (AIError, SummaryError, json.JSONDecodeError, Exception) as exc:
        _mark_summary_failed(record)
        return record, False, _safe_error_message(exc, prompt)

    return record, True, None


def strip_json_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```json"):
        text = text.removeprefix("```json").strip()
    elif text.startswith("```"):
        text = text.removeprefix("```").strip()
    if text.endswith("```"):
        text = text.removesuffix("```").strip()
    return text


def parse_model_json(raw_text: str) -> dict:
    stripped = strip_json_fences(raw_text)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise SummaryError(f"Invalid model JSON: {exc.msg}") from exc

    if not isinstance(parsed, dict):
        raise SummaryError("Model JSON must be an object")

    missing_model_keys = REQUIRED_MODEL_KEYS - set(parsed)
    if missing_model_keys:
        missing = ", ".join(sorted(missing_model_keys))
        raise SummaryError(f"Model JSON missing required keys: {missing}")

    physics = parsed["physics"]
    if not isinstance(physics, dict):
        raise SummaryError("Model JSON physics field must be an object")

    missing_physics_keys = REQUIRED_PHYSICS_KEYS - set(physics)
    if missing_physics_keys:
        missing = ", ".join(sorted(missing_physics_keys))
        raise SummaryError(f"Model JSON physics missing required keys: {missing}")

    return parsed


def normalize_model_output(data: dict) -> dict:
    physics = data.get("physics")
    if not isinstance(physics, dict):
        physics = {}

    return {
        "title": _string_or_none(data.get("title")),
        "authors": _authors_or_none(data.get("authors")),
        "journal": _string_or_none(data.get("journal")),
        "one_sentence": _string_or_none(data.get("one_sentence")),
        "short": _string_or_none(data.get("short")),
        "technical": _string_or_none(data.get("technical")),
        "key_contributions": _string_list(data.get("key_contributions")),
        "methods": _string_list(data.get("methods")),
        "limitations": _string_list(data.get("limitations")),
        "physics": {
            "field": _string_or_none(physics.get("field")),
            "materials": _string_list(physics.get("materials")),
            "devices": _string_list(physics.get("devices")),
            "measurements": _string_list(physics.get("measurements")),
            "main_theory": _string_list(physics.get("main_theory")),
        },
        "tags": _string_list(data.get("tags")),
    }


def _string_or_none(value) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _authors_or_none(value) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    authors = _string_list(value)
    return authors or None


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def apply_ai_output_to_record(
    record: PaperRecord,
    *,
    normalized: dict,
    source_file_hash: str,
    model: str,
    prompt_version: str,
    now_iso: str,
) -> PaperRecord:
    for field_name in ("title", "authors", "journal"):
        value = normalized.get(field_name)
        field = record.metadata[field_name]
        if value is not None and not field.locked:
            field.value = value
            field.source = status_values.SOURCE_AI
            field.confidence = 0.70
            field.updated_at = now_iso

    if not record.summary.get("locked", False):
        record.summary.update(
            {
                "status": status_values.SUMMARY_GENERATED,
                "source_file_hash": source_file_hash,
                "model": model,
                "prompt_version": prompt_version,
                "generated_at": now_iso,
                "locked": False,
                "one_sentence": normalized["one_sentence"],
                "short": normalized["short"],
                "technical": normalized["technical"],
                "key_contributions": normalized["key_contributions"],
                "methods": normalized["methods"],
                "limitations": normalized["limitations"],
                "physics": normalized["physics"],
                "tags": normalized["tags"],
            }
        )
        record.status["summary"] = status_values.SUMMARY_GENERATED

    record.status["metadata"] = metadata_status(record)
    record.timestamps["updated_at"] = now_iso
    _validate_record_summary(record.summary)
    return record


def _validate_record_summary(summary: dict) -> None:
    missing_summary_keys = REQUIRED_SUMMARY_KEYS - set(summary)
    if missing_summary_keys:
        missing = ", ".join(sorted(missing_summary_keys))
        raise SummaryError(f"Record summary missing required keys: {missing}")

    physics = summary.get("physics")
    if not isinstance(physics, dict):
        raise SummaryError("Record summary physics field must be an object")

    missing_physics_keys = REQUIRED_PHYSICS_KEYS - set(physics)
    if missing_physics_keys:
        missing = ", ".join(sorted(missing_physics_keys))
        raise SummaryError(
            f"Record summary physics missing required keys: {missing}"
        )


def _mark_summary_skipped(record: PaperRecord) -> None:
    if record.summary.get("locked", False):
        return
    record.summary["status"] = status_values.SUMMARY_SKIPPED
    record.status["summary"] = status_values.SUMMARY_SKIPPED


def _mark_summary_failed(record: PaperRecord) -> None:
    if record.summary.get("locked", False):
        return
    record.summary["status"] = status_values.SUMMARY_FAILED
    record.status["summary"] = status_values.SUMMARY_FAILED


def _safe_error_message(exc: Exception, prompt: str) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    if prompt:
        message = message.replace(prompt, "[prompt omitted]")
    return f"{exc.__class__.__name__}: {message}"




def locked_metadata(record: PaperRecord) -> dict:
    """Return a deep copy of all locked metadata fields from the record."""
    from copy import deepcopy
    return {
        name: deepcopy(field_value)
        for name, field_value in record.metadata.items()
        if field_value.locked
    }


def restore_locked_metadata(record: PaperRecord, locked_metadata: dict) -> int:
    """Restore locked metadata fields to the record.
    
    Returns the number of fields restored.
    """
    for name, field_value in locked_metadata.items():
        record.metadata[name] = field_value
    return len(locked_metadata)
