"""VisaDriver unit tests. pyvisa is mocked — no real hardware required.

The driver lazy-imports pyvisa inside its methods; pyvisa is installed in the
dev env, so `import pyvisa` succeeds and we mock at the ResourceManager /
Resource level by assigning a fake RM onto driver._rm.
"""

from unittest.mock import MagicMock

import pyvisa
import pytest

from lablink import session as session_registry
from lablink.base import Session
from lablink.interfaces.visa import VisaDriver, VisaDriverConfig


def _config(**overrides) -> VisaDriverConfig:
    defaults = dict(
        alias="test_scope",
        type="visa",
        timeout_ms=5000,
        resource_string="USB0::0x0699::0x0527::C012345::INSTR",
        manufacturer="Tektronix",
        model_number="MSO44",
    )
    defaults.update(overrides)
    return VisaDriverConfig(**defaults)


def _driver_with_resource(resource: MagicMock) -> VisaDriver:
    driver = VisaDriver()
    rm = MagicMock()
    rm.open_resource.return_value = resource
    rm.list_resources.return_value = ()
    driver._rm = rm
    return driver


def _register_session(driver_resource: MagicMock, config: VisaDriverConfig) -> Session:
    session = Session(
        alias=config.alias, interface_type="visa", raw=driver_resource, config=config
    )
    session_registry.register(session)
    return session


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_success_registers_session(self):
        resource = MagicMock()
        resource.query.return_value = "TEKTRONIX,MSO44,C012345,v1.0\n"
        driver = _driver_with_resource(resource)

        result = driver.connect(_config(techmanual_document_ids=[42, 99]))

        assert result.success is True
        assert result.interface_type == "visa"
        assert "TEKTRONIX" in result.identity
        assert result.techmanual_document_ids == [42, 99]
        assert result.metadata["model_number"] == "MSO44"
        assert session_registry.is_registered("test_scope")
        # device_memory is injected by the shared layer, not the driver.
        assert result.device_memory is None

    def test_visa_error_on_open(self):
        driver = VisaDriver()
        rm = MagicMock()
        rm.open_resource.side_effect = pyvisa.Error("no device")
        driver._rm = rm

        result = driver.connect(_config())

        assert result.success is False
        assert "VISA error" in result.error
        assert not session_registry.is_registered("test_scope")

    def test_idn_failure_closes_resource_and_does_not_register(self):
        resource = MagicMock()
        resource.query.side_effect = pyvisa.Error("timeout")
        driver = _driver_with_resource(resource)

        result = driver.connect(_config())

        assert result.success is False
        resource.close.assert_called_once()
        assert not session_registry.is_registered("test_scope")

    def test_already_open_returns_error(self):
        resource = MagicMock()
        resource.query.return_value = "TEK\n"
        config = _config()
        _register_session(MagicMock(), config)
        driver = _driver_with_resource(resource)

        result = driver.connect(config)

        assert result.success is False
        assert "already open" in result.error

    def test_empty_resource_string_returns_error(self):
        driver = VisaDriver()
        result = driver.connect(_config(resource_string=""))
        assert result.success is False
        assert "resource_string" in result.error


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_success_closes_resource(self):
        resource = MagicMock()
        config = _config()
        session = _register_session(resource, config)
        driver = VisaDriver()

        result = driver.disconnect(session)

        assert result.success is True
        resource.close.assert_called_once()

    def test_close_error_returns_failure(self):
        resource = MagicMock()
        resource.close.side_effect = pyvisa.Error("already closed")
        config = _config()
        session = _register_session(resource, config)
        driver = VisaDriver()

        result = driver.disconnect(session)

        assert result.success is False
        assert "Error closing" in result.error


# ---------------------------------------------------------------------------
# visa_query_impl / visa_write_impl
# ---------------------------------------------------------------------------


class TestQuery:
    def test_success(self):
        resource = MagicMock()
        resource.query.return_value = "1000.00\n"
        config = _config()
        _register_session(resource, config)
        driver = VisaDriver()

        result = driver.visa_query_impl("test_scope", "MEAS:FREQ? CH1")

        assert result["success"] is True
        assert result["raw"] == "1000.00"
        # per-call timeout reset from config when not overridden
        assert resource.timeout == 5000

    def test_timeout_override_applied(self):
        resource = MagicMock()
        resource.query.return_value = "x\n"
        _register_session(resource, _config())
        VisaDriver().visa_query_impl("test_scope", "Q?", timeout_ms=12000)
        assert resource.timeout == 12000

    def test_no_session(self):
        result = VisaDriver().visa_query_impl("test_scope", "MEAS?")
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_wrong_type_session(self):
        session = Session(
            alias="lab_pi", interface_type="ssh", raw=MagicMock(), config=_config(alias="lab_pi")
        )
        session_registry.register(session)
        result = VisaDriver().visa_query_impl("lab_pi", "MEAS?")
        assert result["success"] is False
        assert "ssh session, not a VISA session" in result["error"]

    def test_visa_io_error(self):
        resource = MagicMock()
        resource.query.side_effect = pyvisa.errors.VisaIOError(0)
        _register_session(resource, _config())
        result = VisaDriver().visa_query_impl("test_scope", "BAD?")
        assert result["success"] is False
        assert "VISA I/O error" in result["error"]


class TestWrite:
    def test_success(self):
        resource = MagicMock()
        _register_session(resource, _config())
        result = VisaDriver().visa_write_impl("test_scope", "CH1:SCALE 0.5")
        assert result["success"] is True
        assert result["raw"] is None
        resource.write.assert_called_once_with("CH1:SCALE 0.5")

    def test_no_session(self):
        result = VisaDriver().visa_write_impl("test_scope", "CH1:SCALE 0.5")
        assert result["success"] is False

    def test_visa_io_error(self):
        resource = MagicMock()
        resource.write.side_effect = pyvisa.errors.VisaIOError(0)
        _register_session(resource, _config())
        result = VisaDriver().visa_write_impl("test_scope", "CH1:SCALE 0.5")
        assert result["success"] is False
        assert "VISA I/O error" in result["error"]


# ---------------------------------------------------------------------------
# diagnose (per-alias, stateless)
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_usb_in_list_ready(self):
        rs = "USB0::0x0699::0x0527::C012345::INSTR"
        driver = VisaDriver()
        rm = MagicMock()
        rm.list_resources.return_value = (rs,)
        driver._rm = rm

        result = driver.diagnose(_config(resource_string=rs))

        assert result.interface_type == "USB"
        assert result.checks["in_visa_list"]["detail"] is True
        assert result.ready is True

    def test_usb_not_in_list_adds_action(self):
        driver = VisaDriver()
        rm = MagicMock()
        rm.list_resources.return_value = ()
        driver._rm = rm

        result = driver.diagnose(_config())

        assert result.ready is False
        assert any("USB resource" in a for a in result.action_items)

    def test_tcpip_ping_ok_port_closed(self, monkeypatch):
        import lablink.interfaces.visa.driver as drv

        monkeypatch.setattr(drv, "_ping", lambda host: True)
        monkeypatch.setattr(drv, "_port_open", lambda host, port=5025: False)
        driver = VisaDriver()
        rm = MagicMock()
        rm.list_resources.return_value = ()
        driver._rm = rm

        result = driver.diagnose(_config(resource_string="TCPIP0::192.168.1.100::INSTR"))

        assert result.interface_type == "TCPIP"
        assert result.checks["tcpip_host"]["detail"] == "192.168.1.100"
        assert result.checks["ping"]["detail"] is True
        assert result.checks["scpi_port_5025"]["detail"] is False
        assert any("port 5025" in a for a in result.action_items)


# ---------------------------------------------------------------------------
# class-level audit hooks
# ---------------------------------------------------------------------------


class TestAuditHooks:
    def test_check_python_deps_reports_pyvisa(self):
        names = [n for n, _ in VisaDriver.check_python_deps()]
        assert names == ["pyvisa", "pyvisa-py"]

    def test_system_dep_check_reports_libusb(self):
        deps = VisaDriver.system_dep_check()
        assert len(deps) == 1
        assert deps[0].name == "libusb"


class TestEventLogContract:
    def test_tool_call_logs_canonical_fields(self, tmp_path, monkeypatch):
        """§6.4: every tool call produces a log entry carrying ts/op/alias/success."""
        import json

        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        resource = MagicMock()
        resource.query.return_value = "1000\n"
        _register_session(resource, _config())

        VisaDriver().visa_query_impl("test_scope", "MEAS?")

        entry = json.loads(list(tmp_path.glob("*.jsonl"))[0].read_text().strip())
        assert {"ts", "op", "alias", "success"} <= set(entry)
        assert entry["op"] == "visa_query"
        assert entry["alias"] == "test_scope"
        assert entry["success"] is True
