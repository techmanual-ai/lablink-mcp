"""Shared lifecycle tool tests: connect / disconnect / list_devices / diagnose.

These exercise the dispatch layer in mcp_server. The owning driver is mocked so
the tests are pure dispatch + device-memory-injection checks, independent of any
particular driver's behavior.
"""

from unittest.mock import MagicMock, patch

import mcp_server
from lablink import session as session_registry
from lablink.base import ConnectResult, DiagnosticResult, Result, Session
from lablink.interfaces.visa import VisaDriverConfig


def _visa_config(**overrides) -> VisaDriverConfig:
    defaults = dict(alias="scope", type="visa", timeout_ms=5000, resource_string="USB0::INSTR")
    defaults.update(overrides)
    return VisaDriverConfig(**defaults)


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_dispatches_and_injects_device_memory(self):
        config = _visa_config()
        driver = MagicMock()
        driver.connect.return_value = ConnectResult(
            success=True, alias="scope", interface_type="visa", identity="TEK"
        )
        with patch("mcp_server.load_config", return_value=config), \
             patch("mcp_server.get_driver", return_value=driver), \
             patch("mcp_server.load_device_memory", return_value="MEM"):
            result = mcp_server.do_connect("scope")

        driver.connect.assert_called_once_with(config)
        assert result["success"] is True
        assert result["device_memory"] == "MEM"
        # deprecated alias mirrored via replace() re-running __post_init__
        assert result["instrument_memory"] == "MEM"

    def test_config_error_returns_structured_error(self):
        from lablink.exceptions import ConfigError

        with patch("mcp_server.load_config", side_effect=ConfigError("bad")):
            result = mcp_server.do_connect("scope")
        assert result["success"] is False
        assert "bad" in result["error"]
        assert "hint" in result

    def test_failed_driver_connect_not_memory_injected(self):
        config = _visa_config()
        driver = MagicMock()
        driver.connect.return_value = ConnectResult(
            success=False, alias="scope", interface_type="visa", error="boom"
        )
        with patch("mcp_server.load_config", return_value=config), \
             patch("mcp_server.get_driver", return_value=driver), \
             patch("mcp_server.load_device_memory") as mem:
            result = mcp_server.do_connect("scope")
        assert result["success"] is False
        mem.assert_not_called()

    def test_missing_deps_returns_install_hint(self):
        config = _visa_config()
        with patch("mcp_server.load_config", return_value=config), \
             patch("mcp_server._missing_python_deps", return_value=["pyvisa"]):
            result = mcp_server.do_connect("scope")
        assert result["success"] is False
        assert "pip install lablink-mcp[visa]" in result["hint"]


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_dispatches_and_always_deregisters(self):
        config = _visa_config()
        session = Session(alias="scope", interface_type="visa", raw=MagicMock(), config=config)
        session_registry.register(session)
        driver = MagicMock()
        driver.disconnect.return_value = Result(success=True)

        with patch("mcp_server.get_driver", return_value=driver):
            result = mcp_server.do_disconnect("scope")

        assert result["success"] is True
        driver.disconnect.assert_called_once_with(session)
        assert not session_registry.is_registered("scope")

    def test_deregisters_even_on_driver_failure(self):
        config = _visa_config()
        session = Session(alias="scope", interface_type="visa", raw=MagicMock(), config=config)
        session_registry.register(session)
        driver = MagicMock()
        driver.disconnect.return_value = Result(success=False, error="x")

        with patch("mcp_server.get_driver", return_value=driver):
            mcp_server.do_disconnect("scope")

        assert not session_registry.is_registered("scope")

    def test_no_session_returns_error(self):
        result = mcp_server.do_disconnect("scope")
        assert result["success"] is False
        assert "No open session" in result["error"]


# ---------------------------------------------------------------------------
# list_devices
# ---------------------------------------------------------------------------


class TestListDevices:
    def test_reports_status(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))
        (tmp_path / "scope.toml").write_bytes(
            b'type = "visa"\nalias = "scope"\nresource_string = "USB0::INSTR"\n'
            b'timeout_ms = 5000\ndescription = "bench"\n'
        )
        (tmp_path / "broken.toml").write_bytes(b'type = "modbus"\nalias = "broken"\ntimeout_ms = 1\n')

        # mark scope connected
        session_registry.register(
            Session(alias="scope", interface_type="visa", raw=MagicMock(), config=_visa_config())
        )

        devices = {d["alias"]: d for d in mcp_server.do_list_devices()}

        assert devices["scope"]["type"] == "visa"
        assert devices["scope"]["status"] == "connected"
        assert devices["scope"]["description"] == "bench"
        assert devices["broken"]["status"] == "invalid"
        assert "modbus" in devices["broken"]["error"]

    def test_configured_when_not_connected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))
        (tmp_path / "scope.toml").write_bytes(
            b'type = "visa"\nalias = "scope"\nresource_string = "USB0::INSTR"\ntimeout_ms = 5000\n'
        )
        devices = mcp_server.do_list_devices()
        assert devices[0]["status"] == "configured"

    def test_empty_when_no_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path / "missing"))
        assert mcp_server.do_list_devices() == []


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_system_audit_lists_drivers(self):
        # No alias -> system audit. pyvisa + libusb are present in dev env.
        report = mcp_server.do_diagnose()
        assert "visa" in report["drivers"]
        assert report["drivers"]["visa"]["status"] == "ready"
        assert report["alias"] is None

    def test_system_audit_reports_missing_python(self, monkeypatch):
        from lablink.interfaces.visa import VisaDriver

        monkeypatch.setattr(
            VisaDriver, "check_python_deps", classmethod(lambda cls: [("pyvisa", False)])
        )
        report = mcp_server.do_diagnose()
        assert report["ready"] is False
        assert report["drivers"]["visa"]["status"] == "missing_python"
        assert any("pip install lablink-mcp[visa]" in a for a in report["action_items"])

    def test_alias_dispatches_to_driver_and_injects_memory(self):
        config = _visa_config()
        driver = MagicMock()
        driver.diagnose.return_value = DiagnosticResult(
            ready=True, alias="scope", interface_type="visa"
        )
        with patch("mcp_server.load_config", return_value=config), \
             patch("mcp_server.get_driver", return_value=driver), \
             patch("mcp_server.load_device_memory", return_value="MEM"):
            report = mcp_server.do_diagnose("scope")
        assert report["ready"] is True
        assert report["device_memory"] == "MEM"

    def test_alias_config_error(self):
        from lablink.exceptions import ConfigError

        with patch("mcp_server.load_config", side_effect=ConfigError("nope")):
            report = mcp_server.do_diagnose("scope")
        assert report["ready"] is False
        assert "nope" in report["error"]
