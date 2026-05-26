"""Unit tests for AgentLink-Visa MCP tools.

All tests mock pyvisa — no real hardware required.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentlink.exceptions import ConfigError

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


# ---------------------------------------------------------------------------
# tools.connect — session leak regression
# ---------------------------------------------------------------------------

class TestConnectSessionLeak:
    def test_idn_failure_cleans_up_session(self):
        """If *IDN? raises after open_session succeeds, the alias must not
        remain stuck in _sessions."""
        import pyvisa
        from agentlink import tools
        from agentlink import session as _session

        config = _make_config()
        resource = MagicMock()
        resource.query.side_effect = pyvisa.Error("timeout")

        with patch("agentlink.tools.load_config", return_value=config), \
             patch("agentlink.tools._session.open_session", return_value=resource), \
             patch("agentlink.tools._session.is_connected", return_value=False), \
             patch("agentlink.tools._session.close_session") as mock_close:
            result = tools.connect("test_scope")

        assert result["success"] is False
        mock_close.assert_called_once_with(config.alias)

    def test_open_session_failure_does_not_call_close(self):
        """If open_session itself raises, there is nothing to close."""
        import pyvisa
        from agentlink import tools

        config = _make_config()

        with patch("agentlink.tools.load_config", return_value=config), \
             patch("agentlink.tools._session.is_connected", return_value=False), \
             patch("agentlink.tools._session.open_session", side_effect=pyvisa.Error("no device")), \
             patch("agentlink.tools._session.close_session") as mock_close:
            result = tools.connect("test_scope")

        assert result["success"] is False
        # close_session is still called (safe to call even if open failed,
        # it silently ignores the SessionError in the cleanup path)
        mock_close.assert_called_once_with(config.alias)


# ---------------------------------------------------------------------------
# diagnostics tests
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def _mock_rm(self, resources=("USB0::0x1::0x2::A::INSTR",)):
        rm = MagicMock()
        rm.list_resources.return_value = resources
        return rm

    def test_basic_report_structure(self):
        from agentlink import diagnostics

        rm = self._mock_rm()
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm):
            report = diagnostics.run_diagnostics()

        assert "system" in report
        assert "dependencies" in report
        assert "visa" in report
        assert "interfaces" in report
        assert "config_dir" in report
        assert "action_items" in report
        assert "ready" in report
        assert "alias_check" not in report

    def test_no_resources_adds_action_item(self):
        from agentlink import diagnostics

        rm = self._mock_rm(resources=())
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm):
            report = diagnostics.run_diagnostics()

        assert not report["ready"]
        assert any("No VISA resources" in item for item in report["action_items"])

    def test_resource_manager_failure_adds_action_item(self):
        from agentlink import diagnostics
        import pyvisa

        with patch("agentlink.diagnostics.pyvisa.ResourceManager", side_effect=pyvisa.Error("backend missing")):
            report = diagnostics.run_diagnostics()

        assert not report["visa"]["resource_manager_ok"]
        assert any("ResourceManager" in item for item in report["action_items"])

    def test_usb_resource_categorised(self):
        from agentlink import diagnostics

        rm = self._mock_rm(resources=("USB0::0x0699::0x1::C1::INSTR",))
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm):
            report = diagnostics.run_diagnostics()

        assert len(report["interfaces"]["usb"]["resources"]) == 1
        assert report["interfaces"]["gpib"]["resources"] == []
        assert report["interfaces"]["lan"]["resources"] == []

    def test_alias_check_config_missing(self, tmp_path):
        from agentlink import diagnostics
        import agentlink.config as cfg_module

        rm = self._mock_rm()
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm), \
             patch.object(cfg_module, "get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.load_config", side_effect=ConfigError("not found")):
            report = diagnostics.run_diagnostics(alias="missing")

        assert "alias_check" in report
        assert not report["alias_check"]["config_ok"]
        assert any("Config for" in item for item in report["action_items"])

    def test_alias_check_usb_in_list(self, tmp_path):
        from agentlink import diagnostics
        from agentlink.config import InstrumentConfig

        rs = "USB0::0x0699::0x0527::C012345::INSTR"
        config = InstrumentConfig(
            alias="test_scope", resource_string=rs,
            manufacturer="Tektronix", model_number="MSO44",
            timeout_ms=5000, read_termination="\n", write_termination="\n",
        )
        rm = self._mock_rm(resources=(rs,))
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm), \
             patch("agentlink.diagnostics.get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.load_config", return_value=config):
            report = diagnostics.run_diagnostics(alias="test_scope")

        assert report["alias_check"]["config_ok"]
        assert report["alias_check"]["interface_type"] == "USB"
        assert report["alias_check"]["in_visa_list"] is True

    def test_alias_check_usb_not_in_list_adds_action(self, tmp_path):
        from agentlink import diagnostics
        from agentlink.config import InstrumentConfig

        config = InstrumentConfig(
            alias="test_scope",
            resource_string="USB0::0x0699::0x0527::C012345::INSTR",
            manufacturer="Tektronix", model_number="MSO44",
            timeout_ms=5000, read_termination="\n", write_termination="\n",
        )
        rm = self._mock_rm(resources=())
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm), \
             patch("agentlink.diagnostics.get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.load_config", return_value=config):
            report = diagnostics.run_diagnostics(alias="test_scope")

        assert report["alias_check"]["in_visa_list"] is False
        assert any("USB resource" in item for item in report["action_items"])

    def test_alias_check_tcpip_ping_ok_port_closed(self, tmp_path):
        from agentlink import diagnostics
        from agentlink.config import InstrumentConfig

        config = InstrumentConfig(
            alias="lan_scope",
            resource_string="TCPIP0::192.168.1.100::INSTR",
            manufacturer="Keysight", model_number="DSOX1204G",
            timeout_ms=5000, read_termination="\n", write_termination="\n",
        )
        rm = self._mock_rm(resources=())
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm), \
             patch("agentlink.diagnostics.get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.load_config", return_value=config), \
             patch("agentlink.diagnostics._ping", return_value=True), \
             patch("agentlink.diagnostics._port_open", return_value=False):
            report = diagnostics.run_diagnostics(alias="lan_scope")

        assert report["alias_check"]["tcpip_host"] == "192.168.1.100"
        assert report["alias_check"]["ping_ok"] is True
        assert report["alias_check"]["scpi_port_5025_open"] is False
        assert any("port 5025" in item for item in report["action_items"])

    def test_alias_check_tcpip_no_ping(self, tmp_path):
        from agentlink import diagnostics
        from agentlink.config import InstrumentConfig

        config = InstrumentConfig(
            alias="lan_scope",
            resource_string="TCPIP::10.0.0.5::INSTR",
            manufacturer="Keysight", model_number="DSOX1204G",
            timeout_ms=5000, read_termination="\n", write_termination="\n",
        )
        rm = self._mock_rm(resources=())
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm), \
             patch("agentlink.diagnostics.get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.load_config", return_value=config), \
             patch("agentlink.diagnostics._ping", return_value=False), \
             patch("agentlink.diagnostics._port_open", return_value=False):
            report = diagnostics.run_diagnostics(alias="lan_scope")

        assert report["alias_check"]["ping_ok"] is False
        assert any("ping" in item.lower() for item in report["action_items"])

    def test_ready_true_when_no_issues(self, tmp_path):
        from agentlink import diagnostics
        from agentlink.config import InstrumentConfig

        rs = "USB0::0x0699::0x0527::C012345::INSTR"
        config = InstrumentConfig(
            alias="test_scope", resource_string=rs,
            manufacturer="Tektronix", model_number="MSO44",
            timeout_ms=5000, read_termination="\n", write_termination="\n",
        )
        toml_file = tmp_path / "test_scope.toml"
        toml_file.write_text("")  # just needs to exist for the count
        rm = self._mock_rm(resources=(rs,))
        with patch("agentlink.diagnostics.pyvisa.ResourceManager", return_value=rm), \
             patch("agentlink.diagnostics.get_config_dir", return_value=tmp_path), \
             patch("agentlink.diagnostics.load_config", return_value=config):
            report = diagnostics.run_diagnostics(alias="test_scope")

        assert report["ready"] is True
        assert report["action_items"] == []
