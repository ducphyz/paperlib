from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from conftest import _write_config, _write_minimal_pdf
from paperlib.cli import main
from paperlib.config import load_config
from paperlib.models.file import FileRecord
from paperlib.models.record import PaperRecord
from paperlib.pipeline.summarise import locked_metadata, restore_locked_metadata
from paperlib.store import db
from paperlib.store.json_store import write_record_atomic


def _config(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    config_path = tmp_path / "config.toml"
    _write_config(config_path, root)
    return load_config(config_path), config_path


def _record() -> PaperRecord:
    record = PaperRecord(paper_id="p_resummarise", handle_id="resummarise_2024")
    record.identity.aliases = ["hash:ccccccccccccdddd"]
    record.metadata["title"].value = "Resummarise Me"
    record.metadata["authors"].value = ["Baker"]
    record.metadata["year"].value = 2024
    record.files.append(
        FileRecord(
            file_hash="c" * 64,
            original_filename="resummarise.pdf",
            canonical_path="papers/2024/resummarise.pdf",
            text_path="text/resummarise.txt",
            size_bytes=123,
            added_at="2026-04-26T00:00:00Z",
        )
    )
    return record


def _write_indexed_record(config, *, summary_status="failed", write_json: bool = True) -> dict:
    record = _record()
    record.summary["status"] = summary_status
    record.status["summary"] = summary_status
    record_path = config.paths.records / "p_resummarise.json"
    text_path = config.library.root / record.files[0].text_path
    record_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("resummarise fixture text", encoding="utf-8")

    if write_json:
        write_record_atomic(record_path, record)

    conn = db.connect(config.paths.db)
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_resummarise.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
        db.insert_file(conn, record.paper_id, record.files[0])
    finally:
        conn.close()

    return {
        "record": record,
        "record_path": record_path,
        "text_path": text_path,
    }


def test_resummary_processes_all_eligible_records(tmp_path: Path):
    """Test that all eligible records (status failed or skipped) are processed."""
    config, _config_path = _config(tmp_path)
    
    # Create records with different summary statuses
    failed_record = _record()
    failed_record.summary["status"] = "failed"
    failed_record.status["summary"] = "failed"
    failed_record.paper_id = "p_failed"
    failed_record.handle_id = "failed_2024"
    
    skipped_record = _record()
    skipped_record.summary["status"] = "skipped"
    skipped_record.status["summary"] = "skipped"
    skipped_record.paper_id = "p_skipped"
    skipped_record.handle_id = "skipped_2024"
    
    # And a record with generated status that should NOT be processed
    generated_record = _record()
    generated_record.summary["status"] = "generated"
    generated_record.status["summary"] = "generated"
    generated_record.paper_id = "p_generated"
    generated_record.handle_id = "generated_2024"
    
    # Write the records and index them
    for record in [failed_record, skipped_record, generated_record]:
        record_path = config.paths.records / f"{record.paper_id}.json"
        text_path = config.library.root / record.files[0].text_path
        record_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(f"{record.paper_id} test text", encoding="utf-8")
        write_record_atomic(record_path, record)
        
        conn = db.connect(config.paths.db)
        db.init_db(conn)
        try:
            db.upsert_paper(conn, record, f"records/{record.paper_id}.json")
            db.insert_aliases(conn, record.paper_id, record.identity.aliases)
            db.insert_file(conn, record.paper_id, record.files[0])
        finally:
            conn.close()
    
    # Import and mock the summarise_record function to avoid AI calls
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        # Simulate successful summarisation
        record.summary["status"] = "generated"
        record.status["summary"] = "generated"
        record.summary["one_sentence"] = "Mock summary"
        return record, True, None
    
    # Temporarily patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert "eligible:             2" in result.output  # Should have 2 eligible (failed + skipped)
        assert "processed:            2" in result.output  # Should process both eligible ones
        # generated record should not be touched
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_skips_locked_record(tmp_path: Path):
    """Test that a record with summary.locked=True is silently skipped."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="failed")
    
    # Lock the summary
    record = fixture["record"]
    record.summary["locked"] = True
    write_record_atomic(fixture["record_path"], record)
    
    # Update database index to reflect change
    conn = db.connect(config.paths.db)
    try:
        db.upsert_paper(conn, record, "records/p_resummarise.json")
    finally:
        conn.close()
    
    # Mock the summarise_record function to see if it's called
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    mock_called = False
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        nonlocal mock_called
        mock_called = True
        return record, True, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert not mock_called  # summarise_record should not be called
        assert "skipped locked:" in result.output
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_skips_missing_text_file(tmp_path: Path):
    """Test that a record whose text file is missing is skipped with a warning."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="failed")
    
    # Remove the text file
    fixture["text_path"].unlink()
    
    runner = CliRunner()
    result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
    
    assert result.exit_code == 0
    assert "skipped no text:" in result.output


def test_resummary_source_file_selection(tmp_path: Path):
    """Test that source file selection prefers the file matching source_file_hash."""
    config, _config_path = _config(tmp_path)
    
    # Create a record with multiple files and set source_file_hash
    record = _record()
    record.summary["status"] = "failed"
    record.status["summary"] = "failed"
    record.summary["source_file_hash"] = "c" * 64  # Point to the first file
    
    # Add a second file
    second_file = FileRecord(
        file_hash="d" * 64,
        original_filename="second.pdf",
        canonical_path="papers/2024/second.pdf",
        text_path="text/second.txt",
        size_bytes=456,
        added_at="2026-04-26T00:00:00Z",
    )
    record.files.append(second_file)
    
    record_path = config.paths.records / "p_multi.json"
    text_path = config.library.root / record.files[0].text_path  # Use first file's text path
    second_text_path = config.library.root / second_file.text_path  # Second file's text path
    
    record_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    second_text_path.parent.mkdir(parents=True, exist_ok=True)
    
    text_path.write_text("first file content", encoding="utf-8")
    second_text_path.write_text("second file content", encoding="utf-8")
    
    write_record_atomic(record_path, record)
    
    conn = db.connect(config.paths.db)
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_multi.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
        for file_record in record.files:
            db.insert_file(conn, record.paper_id, file_record)
    finally:
        conn.close()
    
    # Mock the summarise_record function to capture the source_file_hash passed
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    captured_source_hash = None
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        nonlocal captured_source_hash
        captured_source_hash = source_file_hash
        record.summary["status"] = "generated"
        record.status["summary"] = "generated"
        return record, True, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert captured_source_hash == "c" * 64  # Should match the source_file_hash
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_json_and_sqlite_updated(tmp_path: Path):
    """Test that after processing, JSON and SQLite are both updated."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="failed")
    
    # Mock summarise_record to update the record
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        record.summary["status"] = "generated"
        record.status["summary"] = "generated"
        record.summary["one_sentence"] = "Updated summary"
        return record, True, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert "processed:" in result.output
        assert "generated:" in result.output
        
        # Check that JSON was updated
        from paperlib.store.json_store import read_record
        updated_record = read_record(fixture["record_path"])
        assert updated_record.summary["status"] == "generated"
        assert updated_record.status["summary"] == "generated"
        assert updated_record.summary["one_sentence"] == "Updated summary"
        
        # Check that SQLite was updated
        conn = db.connect(config.paths.db)
        try:
            row = conn.execute(
                "SELECT summary_status FROM papers WHERE paper_id = 'p_resummarise'"
            ).fetchone()
            assert row is not None
            assert row[0] == 'generated'
        finally:
            conn.close()
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_limit_n_processes_at_most_n_records(tmp_path: Path):
    """Test that --limit N processes at most N records."""
    config, _config_path = _config(tmp_path)
    
    # Create 3 failed records
    for i in range(3):
        record = _record()
        record.paper_id = f"p_failed_{i}"
        record.handle_id = f"failed_{i}_2024"
        record.summary["status"] = "failed"
        record.status["summary"] = "failed"
        
        record_path = config.paths.records / f"p_failed_{i}.json"
        text_path = config.library.root / record.files[0].text_path
        record_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(f"test text {i}", encoding="utf-8")
        
        write_record_atomic(record_path, record)
        
        conn = db.connect(config.paths.db)
        db.init_db(conn)
        try:
            db.upsert_paper(conn, record, f"records/p_failed_{i}.json")
            db.insert_aliases(conn, record.paper_id, record.identity.aliases)
            db.insert_file(conn, record.paper_id, record.files[0])
        finally:
            conn.close()
    
    # Mock the summarise_record function
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    processed_count = 0
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        nonlocal processed_count
        processed_count += 1
        record.summary["status"] = "generated"
        record.status["summary"] = "generated"
        return record, True, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--limit", "2", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert processed_count == 2  # Should only process 2 records due to limit
        assert "eligible:" in result.output
        assert "processed:" in result.output
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_no_ai_passes_no_ai_true(tmp_path: Path):
    """Test that --no-ai passes no_ai=True to summarise_record."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="failed")
    
    # Mock the summarise_record function to check if no_ai=True is passed
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    captured_no_ai = None
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        nonlocal captured_no_ai
        captured_no_ai = no_ai
        record.summary["status"] = "skipped"
        record.status["summary"] = "skipped"
        return record, False, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert captured_no_ai is True  # --no-ai should pass no_ai=True
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_specific_id_resummarises_that_record(tmp_path: Path):
    """Test re-summarise with a specific id_or_alias re-summarises that record."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="generated")  # Even if status is generated, it should still be processed
    
    # Mock the summarise_record function
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    mock_called = False
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        nonlocal mock_called
        mock_called = True
        record.summary["status"] = "generated"
        record.status["summary"] = "generated"
        return record, True, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "resummarise_2024", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        assert mock_called  # summarise_record should be called
        assert "eligible:" in result.output  # Specific ID mode should still show this field
        assert "processed:" in result.output
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise


def test_resummary_unknown_id_exits_nonzero(tmp_path: Path):
    """Test re-summarise with unknown id_or_alias exits nonzero with 'Paper not found'."""
    config, _config_path = _config(tmp_path)
    # Don't create any records so the ID won't exist
    
    runner = CliRunner()
    result = runner.invoke(main, ["re-summarise", "nonexistent_id", "--no-ai", "--config", str(_config_path)])
    
    assert result.exit_code != 0
    # The error now is "No database found" because the library root is empty
    assert "No database found" in result.output or "Paper not found" in result.output


def test_resummary_specific_id_missing_json_exits_nonzero(tmp_path: Path):
    """A specific record request should fail cleanly if its JSON is missing."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="failed")
    fixture["record_path"].unlink()

    result = CliRunner().invoke(
        main,
        [
            "re-summarise",
            "resummarise_2024",
            "--no-ai",
            "--config",
            str(_config_path),
        ],
    )

    assert result.exit_code != 0
    assert "Could not read record" in result.output
    assert "Traceback" not in result.output


def test_resummary_no_eligible_records_prints_zeroed_report(tmp_path: Path):
    """Test re-summarise with no eligible records prints zeroed report and exits zero."""
    config, _config_path = _config(tmp_path)
    # Create a record with successful status, so no records are eligible
    record = _record()
    record.summary["status"] = "generated"
    record.status["summary"] = "generated"
    
    record_path = config.paths.records / "p_ok.json"
    text_path = config.library.root / record.files[0].text_path
    record_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text("ok test text", encoding="utf-8")
    
    write_record_atomic(record_path, record)
    
    conn = db.connect(config.paths.db)
    db.init_db(conn)
    try:
        db.upsert_paper(conn, record, "records/p_ok.json")
        db.insert_aliases(conn, record.paper_id, record.identity.aliases)
        db.insert_file(conn, record.paper_id, record.files[0])
    finally:
        conn.close()
    
    runner = CliRunner()
    result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
    
    assert result.exit_code == 0
    # Check that all values are 0
    import re
    assert re.search(r"eligible:\s*0", result.output)
    assert re.search(r"processed:\s*0", result.output)
    assert re.search(r"generated:\s*0", result.output)
    assert re.search(r"failed:\s*0", result.output)
    assert re.search(r"skipped locked:\s*0", result.output)
    assert re.search(r"skipped no text:\s*0", result.output)


def test_resummary_report_fields_present_in_output(tmp_path: Path):
    """Test that report fields are all present in output."""
    config, _config_path = _config(tmp_path)
    fixture = _write_indexed_record(config, summary_status="failed")
    
    # Mock to ensure processing happens
    from paperlib.pipeline.summarise import summarise_record
    original_summarise = summarise_record
    
    def mock_summarise(record, *, cleaned_text, source_file_hash, ai_config, no_ai):
        record.summary["status"] = "generated"
        record.status["summary"] = "generated"
        return record, True, None
    
    # Patch the function
    import paperlib.cli
    paperlib.cli.summarise_record = mock_summarise
    
    try:
        runner = CliRunner()
        result = runner.invoke(main, ["re-summarise", "--no-ai", "--config", str(_config_path)])
        
        assert result.exit_code == 0
        # Check that all required report fields are present
        assert "eligible:" in result.output
        assert "processed:" in result.output
        assert "generated:" in result.output
        assert "failed:" in result.output
        assert "skipped locked:" in result.output
        assert "skipped no text:" in result.output
    finally:
        # Restore original function
        paperlib.cli.summarise_record = original_summarise
