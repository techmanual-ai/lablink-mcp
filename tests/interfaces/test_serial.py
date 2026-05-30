"""SerialDriver unit tests. pyserial is patched at the library level.

Because the driver lazy-imports serial inside methods, we patch 'serial.Serial'
directly — the lazy import gets the real module, but Serial is replaced with
our mock.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

from lablink import session as session_registry
from lablink.base import Session
from lablink.interfaces.serial import SerialDriver, SerialDriverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> SerialDriverConfig:
    defaults = dict(
        alias="test_serial",
        type="serial",
        timeout_ms=2000,
        serial_port="/dev/ttyUSB0",
        baud_rate=115200,
        data_bits=8,
        parity="none",
        stop_bits=1,
        read_termination="\n",
        write_termination="\n",
    )
    defaults.update(overrides)
    return SerialDriverConfig(**defaults)


def _mock_ser() -> MagicMock:
    ser = MagicMock()
    ser.in_waiting = 0
    return ser


def _register_session(ser: MagicMock, config: SerialDriverConfig) -> Session:
    session = Session(
        alias=config.alias, interface_type="serial", raw=ser, config=config
    )
    session_registry.register(session)
    return session


@pytest.fixture(autouse=True)
def clear_sessions():
    session_registry.deregister("test_serial")
    yield
    session_registry.deregister("test_serial")


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_success_registers_session(self):
        ser = _mock_ser()
        driver = SerialDriver()
        with patch("serial.Serial", return_value=ser):
            result = driver.connect(_config())
        assert result.success
        assert result.interface_type == "serial"
        assert result.identity == "/dev/ttyUSB0@115200baud"
        assert session_registry.is_registered("test_serial")

    def test_success_passes_config_fields_to_serial(self):
        ser = _mock_ser()
        driver = SerialDriver()
        with patch("serial.Serial", return_value=ser) as mock_cls:
            driver.connect(_config(baud_rate=9600, data_bits=7, parity="even", stop_bits=2))
        mock_cls.assert_called_once_with(
            port="/dev/ttyUSB0",
            baudrate=9600,
            bytesize=7,
            parity="E",
            stopbits=2,
            timeout=2.0,
        )

    def test_missing_dep_returns_error(self):
        driver = SerialDriver()
        with patch.dict(sys.modules, {"serial": None}):
            # Force ImportError by patching the import
            with patch("builtins.__import__", side_effect=_import_error_for("serial")):
                result = driver.connect(_config())
        assert not result.success
        assert "pyserial" in result.error
        assert "lablink-mcp[serial]" in result.hint

    def test_already_connected_returns_error(self):
        ser = _mock_ser()
        _register_session(ser, _config())
        driver = SerialDriver()
        with patch("serial.Serial", return_value=_mock_ser()):
            result = driver.connect(_config())
        assert not result.success
        assert "already open" in result.error

    def test_empty_port_returns_error(self):
        driver = SerialDriver()
        with patch("serial.Serial"):
            result = driver.connect(_config(serial_port=""))
        assert not result.success
        assert "serial_port" in result.error

    def test_invalid_parity_returns_error(self):
        driver = SerialDriver()
        with patch("serial.Serial"):
            result = driver.connect(_config(parity="bogus"))
        assert not result.success
        assert "parity" in result.error

    def test_serial_exception_returns_error(self):
        driver = SerialDriver()
        import serial
        with patch("serial.Serial", side_effect=serial.SerialException("No such port")):
            result = driver.connect(_config())
        assert not result.success
        assert "No such port" in result.error

    def test_parity_case_insensitive(self):
        ser = _mock_ser()
        driver = SerialDriver()
        with patch("serial.Serial", return_value=ser) as mock_cls:
            result = driver.connect(_config(parity="EVEN"))
        assert result.success
        _, kwargs = mock_cls.call_args
        assert kwargs["parity"] == "E"


def _import_error_for(module_name: str):
    """Return a side_effect callable that raises ImportError only for module_name."""
    real_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

    def _import(name, *args, **kwargs):
        if name == module_name:
            raise ImportError(f"No module named '{module_name}'")
        return real_import(name, *args, **kwargs)

    return _import


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_success_closes_port(self):
        ser = _mock_ser()
        config = _config()
        session = _register_session(ser, config)
        driver = SerialDriver()
        result = driver.disconnect(session)
        assert result.success
        ser.close.assert_called_once()

    def test_close_exception_returns_error(self):
        ser = _mock_ser()
        ser.close.side_effect = OSError("already closed")
        config = _config()
        session = _register_session(ser, config)
        driver = SerialDriver()
        result = driver.disconnect(session)
        assert not result.success
        assert "already closed" in result.error


# ---------------------------------------------------------------------------
# serial_query
# ---------------------------------------------------------------------------


class TestSerialQuery:
    def test_success(self):
        ser = _mock_ser()
        ser.read_until.return_value = b"OK\n"
        ser.write.return_value = 5
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_query_impl("test_serial", "PING")
        assert result["success"]
        assert result["raw"] == "OK\n"
        assert result["metadata"]["bytes_written"] == 5
        ser.write.assert_called_once_with(b"PING\n")
        ser.read_until.assert_called_once_with(b"\n")

    def test_timeout_sets_timed_out(self):
        ser = _mock_ser()
        # Response missing terminator → timed_out
        ser.read_until.return_value = b"partial"
        ser.write.return_value = 5
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_query_impl("test_serial", "CMD")
        assert result["success"]
        assert result["timed_out"]

    def test_per_call_timeout_overrides_config(self):
        ser = _mock_ser()
        ser.read_until.return_value = b"OK\n"
        ser.write.return_value = 5
        config = _config(timeout_ms=2000)
        _register_session(ser, config)
        driver = SerialDriver()
        driver.serial_query_impl("test_serial", "CMD", timeout_ms=500)
        assert ser.timeout == 0.5

    def test_write_termination_appended(self):
        ser = _mock_ser()
        ser.read_until.return_value = b"OK\r\n"
        ser.write.return_value = 6
        config = _config(write_termination="\r\n", read_termination="\r\n")
        _register_session(ser, config)
        driver = SerialDriver()
        driver.serial_query_impl("test_serial", "CMD")
        ser.write.assert_called_once_with(b"CMD\r\n")
        ser.read_until.assert_called_once_with(b"\r\n")

    def test_os_error_returns_failure(self):
        ser = _mock_ser()
        ser.write.side_effect = OSError("write failed")
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_query_impl("test_serial", "CMD")
        assert not result["success"]
        assert "write failed" in result["error"]

    def test_no_session_returns_error(self):
        driver = SerialDriver()
        result = driver.serial_query_impl("test_serial", "CMD")
        assert not result["success"]
        assert "No open session" in result["error"]

    def test_wrong_type_session_returns_error(self):
        ser = _mock_ser()
        wrong_session = Session(
            alias="test_serial", interface_type="rest", raw=ser,
            config=_config()
        )
        session_registry.register(wrong_session)
        driver = SerialDriver()
        result = driver.serial_query_impl("test_serial", "CMD")
        assert not result["success"]
        assert "rest" in result["error"]


# ---------------------------------------------------------------------------
# serial_write
# ---------------------------------------------------------------------------


class TestSerialWrite:
    def test_success(self):
        ser = _mock_ser()
        ser.write.return_value = 6
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_write_impl("test_serial", "RESET")
        assert result["success"]
        assert result["metadata"]["bytes_written"] == 6
        assert result["raw"] is None
        ser.write.assert_called_once_with(b"RESET\n")

    def test_os_error_returns_failure(self):
        ser = _mock_ser()
        ser.write.side_effect = OSError("port closed")
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_write_impl("test_serial", "CMD")
        assert not result["success"]
        assert "port closed" in result["error"]

    def test_no_session_returns_error(self):
        driver = SerialDriver()
        result = driver.serial_write_impl("test_serial", "CMD")
        assert not result["success"]


# ---------------------------------------------------------------------------
# serial_read
# ---------------------------------------------------------------------------


class TestSerialRead:
    def test_success_with_data(self):
        ser = _mock_ser()
        # First read returns 1 byte, in_waiting has 4 more
        ser.read.side_effect = [b"H", b"ello"]
        ser.in_waiting = 4
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_read_impl("test_serial")
        assert result["success"]
        assert result["raw"] == "Hello"
        assert not result["timed_out"]

    def test_timeout_no_data(self):
        ser = _mock_ser()
        ser.read.return_value = b""  # timeout, no data
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_read_impl("test_serial")
        assert result["success"]
        assert result["raw"] == ""
        assert result["timed_out"]

    def test_per_call_timeout_set(self):
        ser = _mock_ser()
        ser.read.return_value = b""
        config = _config(timeout_ms=3000)
        _register_session(ser, config)
        driver = SerialDriver()
        driver.serial_read_impl("test_serial", timeout_ms=100)
        assert ser.timeout == 0.1

    def test_no_in_waiting_skips_second_read(self):
        ser = _mock_ser()
        ser.read.return_value = b"X"
        ser.in_waiting = 0
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_read_impl("test_serial")
        assert result["success"]
        assert result["raw"] == "X"
        ser.read.assert_called_once_with(1)

    def test_no_session_returns_error(self):
        driver = SerialDriver()
        result = driver.serial_read_impl("test_serial")
        assert not result["success"]


# ---------------------------------------------------------------------------
# serial_flush
# ---------------------------------------------------------------------------


class TestSerialFlush:
    def test_success(self):
        ser = _mock_ser()
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_flush_impl("test_serial")
        assert result["success"]
        ser.reset_input_buffer.assert_called_once()
        ser.reset_output_buffer.assert_called_once()

    def test_os_error_returns_failure(self):
        ser = _mock_ser()
        ser.reset_input_buffer.side_effect = OSError("io error")
        config = _config()
        _register_session(ser, config)
        driver = SerialDriver()
        result = driver.serial_flush_impl("test_serial")
        assert not result["success"]
        assert "io error" in result["error"]

    def test_no_session_returns_error(self):
        driver = SerialDriver()
        result = driver.serial_flush_impl("test_serial")
        assert not result["success"]
        assert "No open session" in result["error"]

    def test_wrong_type_session_returns_error(self):
        ser = _mock_ser()
        wrong_session = Session(
            alias="test_serial", interface_type="visa", raw=ser, config=_config()
        )
        session_registry.register(wrong_session)
        driver = SerialDriver()
        result = driver.serial_flush_impl("test_serial")
        assert not result["success"]
        assert "visa" in result["error"]


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_valid_config_ready(self):
        driver = SerialDriver()
        with patch("pathlib.Path.exists", return_value=True):
            result = driver.diagnose(_config())
        assert result.ready
        assert result.alias == "test_serial"
        assert result.interface_type == "serial"

    def test_empty_port_not_ready(self):
        driver = SerialDriver()
        result = driver.diagnose(_config(serial_port=""))
        assert not result.ready
        assert any("serial_port" in item for item in result.action_items)

    def test_port_not_found_on_posix(self):
        driver = SerialDriver()
        with patch("sys.platform", "linux"), patch("pathlib.Path.exists", return_value=False):
            result = driver.diagnose(_config(serial_port="/dev/ttyUSB99"))
        assert not result.ready
        assert any("does not exist" in item for item in result.action_items)

    def test_port_check_skipped_on_windows(self):
        driver = SerialDriver()
        with patch("sys.platform", "win32"):
            result = driver.diagnose(_config(serial_port="COM3"))
        # On Windows path check is skipped, so no port_exists check
        assert "port_exists" not in result.checks

    def test_invalid_baud_rate(self):
        driver = SerialDriver()
        result = driver.diagnose(_config(baud_rate=0))
        assert not result.ready
        assert any("baud_rate" in item for item in result.action_items)

    def test_invalid_parity(self):
        driver = SerialDriver()
        result = driver.diagnose(_config(parity="bogus"))
        assert not result.ready
        assert any("parity" in item for item in result.action_items)

    def test_invalid_data_bits(self):
        driver = SerialDriver()
        result = driver.diagnose(_config(data_bits=9))
        assert not result.ready
        assert any("data_bits" in item for item in result.action_items)


# ---------------------------------------------------------------------------
# check_python_deps
# ---------------------------------------------------------------------------


class TestCheckPythonDeps:
    def test_serial_present(self):
        with patch("importlib.util.find_spec", return_value=MagicMock()):
            deps = SerialDriver.check_python_deps()
        assert len(deps) == 1
        assert deps[0][0] == "serial"
        assert deps[0][1] is True

    def test_serial_missing(self):
        with patch("importlib.util.find_spec", return_value=None):
            deps = SerialDriver.check_python_deps()
        assert deps[0][1] is False
