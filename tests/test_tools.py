"""Unit tests for AgentLink-Visa MCP tools.

All tests mock pyvisa — no real hardware required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**overrides):
    """Return a minimal valid InstrumentConfig, with optional field overrides."""
    from agentlink.config import InstrumentConfig

    defaults = dict(
        alias="test_scope",
        resource_string="USB0::0x0699::0x0527::C012345::INSTR",
        manufacturer="Tektronix",
        model_number="MSO44",
        timeout_ms=5000,
        read_termination="\n",
        write_termination="\n",
        techmanual_document_id=None,
        description=None,
    )
    defaults.update(overrides)
    return InstrumentConfig(**defaults)


def _make_mock_resource(idn_response: str = "TEKTRONIX,MSO44,C012345,CF:91.1CT FV:v1.26") -> MagicMock:
    """Return a mock pyvisa resource."""
    resource = MagicMock()
    resource.query.return_value = idn_response
    return resource


# ---------------------------------------------------------------------------
# config.py tests
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_missing_file_raises_config_error(self, tmp_path):
        from agentlink.exceptions import ConfigError
        import agentlink.config as cfg_module

        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            with pytest.raises(ConfigError, match="No config file found"):
                cfg_module.load_config("nonexistent")

    def test_missing_required_field_raises_config_error(self, tmp_path):
        from agentlink.exceptions import ConfigError
        import agentlink.config as cfg_module

        toml_content = b"""
alias = "test_scope"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
# model_number intentionally missing
timeout_ms = 5000
read_termination = "\\n"
write_termination = "\\n"
"""
        (tmp_path / "test_scope.toml").write_bytes(toml_content)

        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            with pytest.raises(ConfigError, match="model_number"):
                cfg_module.load_config("test_scope")

    def test_valid_config_loads(self, tmp_path):
        import agentlink.config as cfg_module

        toml_content = b"""
alias = "test_scope"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms = 5000
read_termination = "\\n"
write_termination = "\\n"
techmanual_document_id = 142
description = "bench scope"
"""
        (tmp_path / "test_scope.toml").write_bytes(toml_content)

        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            config = cfg_module.load_config("test_scope")

        assert config.alias == "test_scope"
        assert config.model_number == "MSO44"
        assert config.techmanual_document_id == 142
        assert config.description == "bench scope"

    def test_list_configs_empty_dir(self, tmp_path):
        import agentlink.config as cfg_module

        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            result = cfg_module.list_configs()

        assert result == []

    def test_list_configs_returns_valid_entries(self, tmp_path):
        import agentlink.config as cfg_module

        toml_content = b"""
alias = "scope_a"
resource_string = "USB0::0x1::0x2::A::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms = 5000
read_termination = "\\n"
write_termination = "\\n"
"""
        (tmp_path / "scope_a.toml").write_bytes(toml_content)

        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            result = cfg_module.list_configs()

        assert len(result) == 1
        assert result[0].alias == "scope_a"


# ---------------------------------------------------------------------------
# tools.connect tests
# ---------------------------------------------------------------------------

class TestConnect:
    def test_success(self):
        from agentlink import tools
        config = _make_config(techmanual_document_id=42)
        resource = _make_mock_resource("TEKTRONIX,MSO44,C012345,v1.0\n")

        with patch("agentlink.tools.load_config", return_value=config), \
             patch("agentlink.tools._session.open_session", return_value=resource):
            result = tools.connect("test_scope")

        assert result["success"] is True
        assert result["alias"] == "test_scope"
        assert "TEKTRONIX" in result["idn"]
        assert result["techmanual_document_id"] == 42

    def test_config_not_found(self):
        from agentlink import tools
        from agentlink.exceptions import ConfigError

        with patch("agentlink.tools.load_config", side_effect=ConfigError("not found")):
            result = tools.connect("missing_alias")

        assert result["success"] is False
        assert "not found" in result["error"]
        assert "hint" in result

    def test_visa_error_on_open(self):
        import pyvisa
        from agentlink import tools
        config = _make_config()

        with patch("agentlink.tools.load_config", return_value=config), \
             patch("agentlink.tools._session.open_session", side_effect=pyvisa.Error("timeout")):
            result = tools.connect("test_scope")

        assert result["success"] is False
        assert "VISA error" in result["error"]
        assert "hint" in result

    def test_already_connected_returns_error(self):
        from agentlink import tools

        with patch("agentlink.tools._session.is_connected", return_value=True):
            result = tools.connect("test_scope")

        assert result["success"] is False
        assert "already open" in result["error"]
        assert "hint" in result


# ---------------------------------------------------------------------------
# tools.disconnect tests
# ---------------------------------------------------------------------------

class TestDisconnect:
    def test_success(self):
        from agentlink import tools

        with patch("agentlink.tools._session.close_session"):
            result = tools.disconnect("test_scope")

        assert result["success"] is True
        assert result["alias"] == "test_scope"

    def test_no_open_session(self):
        from agentlink import tools
        from agentlink.exceptions import SessionError

        with patch("agentlink.tools._session.close_session", side_effect=SessionError("no session")):
            result = tools.disconnect("test_scope")

        assert result["success"] is False
        assert "hint" in result


# ---------------------------------------------------------------------------
# tools.query tests
# ---------------------------------------------------------------------------

class TestQuery:
    def test_success(self):
        from agentlink import tools
        resource = MagicMock()
        resource.query.return_value = "1000.00\n"

        with patch("agentlink.tools._session.get_session", return_value=resource):
            result = tools.query("test_scope", "MEAS:FREQ? CH1")

        assert result["success"] is True
        assert result["response"] == "1000.00"
        assert result["command"] == "MEAS:FREQ? CH1"

    def test_no_open_session(self):
        from agentlink import tools
        from agentlink.exceptions import SessionError

        with patch("agentlink.tools._session.get_session", side_effect=SessionError("no session")):
            result = tools.query("test_scope", "MEAS:FREQ? CH1")

        assert result["success"] is False
        assert "hint" in result

    def test_visa_io_error(self):
        import pyvisa
        from agentlink import tools
        resource = MagicMock()
        resource.query.side_effect = pyvisa.errors.VisaIOError(0)

        with patch("agentlink.tools._session.get_session", return_value=resource):
            result = tools.query("test_scope", "BAD:COMMAND?")

        assert result["success"] is False
        assert "VISA I/O error" in result["error"]


# ---------------------------------------------------------------------------
# tools.write tests
# ---------------------------------------------------------------------------

class TestWrite:
    def test_success(self):
        from agentlink import tools
        resource = MagicMock()

        with patch("agentlink.tools._session.get_session", return_value=resource):
            result = tools.write("test_scope", "CH1:SCALE 0.5")

        assert result["success"] is True
        assert result["command"] == "CH1:SCALE 0.5"
        resource.write.assert_called_once_with("CH1:SCALE 0.5")

    def test_no_open_session(self):
        from agentlink import tools
        from agentlink.exceptions import SessionError

        with patch("agentlink.tools._session.get_session", side_effect=SessionError("no session")):
            result = tools.write("test_scope", "CH1:SCALE 0.5")

        assert result["success"] is False
        assert "hint" in result

    def test_visa_io_error(self):
        import pyvisa
        from agentlink import tools
        resource = MagicMock()
        resource.write.side_effect = pyvisa.errors.VisaIOError(0)

        with patch("agentlink.tools._session.get_session", return_value=resource):
            result = tools.write("test_scope", "CH1:SCALE 0.5")

        assert result["success"] is False
        assert "VISA I/O error" in result["error"]
