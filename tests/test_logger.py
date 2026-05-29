"""Event logging tests.

Currently exercises lablink.scpi_logger; the module is renamed to
lablink.event_logger in Phase 0c (the contract is unchanged).
"""

import json
from pathlib import Path

from lablink import scpi_logger


class TestLogger:
    def test_writes_jsonl_entry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        scpi_logger.log_event(
            op="visa_query", alias="scope", command="*IDN?", response="ACME", success=True
        )
        log_files = list(tmp_path.glob("*.jsonl"))
        assert len(log_files) == 1
        entry = json.loads(log_files[0].read_text().strip())
        assert entry["op"] == "visa_query"
        assert entry["alias"] == "scope"
        assert entry["command"] == "*IDN?"
        assert entry["response"] == "ACME"
        assert entry["success"] is True
        assert "ts" in entry

    def test_multiple_events_append(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        scpi_logger.log_event(op="connect", alias="scope", success=True)
        scpi_logger.log_event(op="visa_query", alias="scope", command="FREQ?", success=True)
        scpi_logger.log_event(op="visa_write", alias="scope", command="TDIV 1E-3", success=True)
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        assert [json.loads(l)["op"] for l in lines] == ["connect", "visa_query", "visa_write"]

    def test_disabled_when_env_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", "")
        scpi_logger.log_event(op="visa_query", alias="scope", success=True)
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_error_entry_has_error_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        scpi_logger.log_event(op="visa_query", alias="scope", success=False, error="VISA I/O error: timeout")
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert entry["success"] is False
        assert "timeout" in entry["error"]

    def test_never_raises_on_bad_dir(self, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", "/nonexistent/readonly/path/xyz")
        scpi_logger.log_event(op="visa_write", alias="scope", success=True)

    def test_get_log_dir_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("LABLINK_LOG_DIR", raising=False)
        assert scpi_logger.get_log_dir() == Path.home() / ".lablink" / "logs"

    def test_get_log_dir_none_when_empty(self, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", "")
        assert scpi_logger.get_log_dir() is None

    def test_get_log_dir_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        assert scpi_logger.get_log_dir() == tmp_path
