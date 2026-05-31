"""Event logger tests (lablink.event_logger, §6.4 contract)."""

import json
from pathlib import Path

from lablink import event_logger


class TestLogger:
    def test_writes_jsonl_entry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(
            op="visa_query", alias="scope", success=True, command="*IDN?", response="ACME"
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

    def test_canonical_fields_always_present(self, tmp_path, monkeypatch):
        # §6.4: ts/op/alias/success are the four guaranteed fields, even with no
        # extras and alias=None (system-audit diagnose).
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(op="diagnose", alias=None, success=True)
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert set(["ts", "op", "alias", "success"]) <= set(entry)
        assert entry["alias"] is None

    def test_optional_fields_omitted_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(op="connect", alias="scope", success=True)
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert "error" not in entry
        assert "duration_ms" not in entry

    def test_multiple_events_append(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(op="connect", alias="scope", success=True)
        event_logger.log_event(op="visa_query", alias="scope", success=True, command="FREQ?")
        event_logger.log_event(op="visa_write", alias="scope", success=True, command="TDIV 1E-3")
        lines = list(tmp_path.glob("*.jsonl"))[0].read_text().strip().splitlines()
        assert [json.loads(l)["op"] for l in lines] == ["connect", "visa_query", "visa_write"]

    def test_disabled_when_env_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", "")
        event_logger.log_event(op="visa_query", alias="scope", success=True)
        assert list(tmp_path.glob("*.jsonl")) == []

    def test_error_entry_has_error_field(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(op="visa_query", alias="scope", success=False, error="VISA I/O error: timeout")
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert entry["success"] is False
        assert "timeout" in entry["error"]

    def test_never_raises_on_bad_dir(self, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", "/nonexistent/readonly/path/xyz")
        event_logger.log_event(op="visa_write", alias="scope", success=True)

    def test_get_log_dir_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("LABLINK_LOG_DIR", raising=False)
        assert event_logger.get_log_dir() == Path.home() / ".lablink" / "logs"

    def test_get_log_dir_none_when_empty(self, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", "")
        assert event_logger.get_log_dir() is None

    def test_get_log_dir_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        assert event_logger.get_log_dir() == tmp_path


class TestRedactionAtBoundary:
    """`secrets=` scrubs every free-form string field before writing (§8.4)."""

    def test_secret_scrubbed_from_extra_and_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(
            op="ssh_exec",
            alias="pi",
            success=False,
            error="failed running echo s3cret-token | sudo -S id",
            command="echo s3cret-token | sudo -S id",
            secrets={"s3cret-token"},
        )
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert "s3cret-token" not in json.dumps(entry)
        assert entry["command"] == "echo *** | sudo -S id"
        assert "***" in entry["error"]

    def test_non_string_extras_untouched(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(
            op="ssh_exec", alias="pi", success=True, exit_code=0, command="ok", secrets={"abc123"}
        )
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert entry["exit_code"] == 0  # int passed through, not coerced

    def test_no_secrets_writes_verbatim(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        event_logger.log_event(op="ssh_exec", alias="pi", success=True, command="echo hunter2")
        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert entry["command"] == "echo hunter2"
