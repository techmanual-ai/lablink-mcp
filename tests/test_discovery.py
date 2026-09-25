"""Discovery sweep tests. pyvisa and pyserial are mocked — no hardware.

Both libraries are installed in the dev env, so the lazy `import pyvisa` /
`import serial` inside the sweeps succeeds and we patch attributes on the real
modules (ResourceManager, serial.Serial, list_ports.comports). To exercise the
missing-driver path we put None in sys.modules for the package, which makes the
lazy import raise ImportError exactly as an uninstalled extra would.
"""

import sys
from unittest.mock import MagicMock

import pyvisa
import pytest
import serial
from click.testing import CliRunner
from serial.tools import list_ports

from lablink import cli as cli_module
from lablink import discovery

_SCOPE_RESOURCE = "USB0::0x0699::0x0527::C012345::INSTR"
_SCOPE_IDN = "TEKTRONIX,MSO44,C012345,CF:91.1CT FV:1.2.3\n"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def no_visa(monkeypatch):
    """Make the lazy `import pyvisa` fail, as an uninstalled extra would."""
    monkeypatch.setitem(sys.modules, "pyvisa", None)


@pytest.fixture
def no_serial(monkeypatch):
    """Make the lazy `import serial` fail, as an uninstalled extra would."""
    monkeypatch.setitem(sys.modules, "serial", None)


@pytest.fixture
def visa_resources(monkeypatch):
    """Install a fake ResourceManager; returns it for per-test setup."""
    rm = MagicMock()
    rm.list_resources.return_value = ()
    monkeypatch.setattr(pyvisa, "ResourceManager", lambda backend: rm)
    return rm


@pytest.fixture
def serial_ports(monkeypatch):
    """Patch comports() and Serial; returns the port list tests append to.

    Serial defaults to raising so no test can open a real port; tests that
    need a reply re-patch it.
    """
    ports: list = []
    monkeypatch.setattr(list_ports, "comports", lambda: ports)

    def _unopenable(**kwargs):
        raise serial.SerialException("no such port")

    monkeypatch.setattr(serial, "Serial", _unopenable)
    return ports


def _fake_port(device="/dev/ttyUSB0", description="USB-Serial CH340",
               vid=0x1A86, pid=0x7523, manufacturer="wch.cn"):
    port = MagicMock()
    port.device = device
    port.description = description
    port.vid = vid
    port.pid = pid
    port.manufacturer = manufacturer
    return port


# ---------------------------------------------------------------------------
# *IDN? parsing and alias suggestion
# ---------------------------------------------------------------------------


class TestParseIdn:
    def test_four_fields(self):
        assert discovery.parse_idn("TEKTRONIX,MSO44,C012345,1.2.3") == (
            "TEKTRONIX", "MSO44", "C012345", "1.2.3",
        )

    def test_missing_trailing_fields_are_none(self):
        assert discovery.parse_idn("LabLink,FG-100") == ("LabLink", "FG-100", None, None)

    def test_commas_in_firmware_are_preserved(self):
        _, _, _, firmware = discovery.parse_idn("KEYSIGHT,33500B,MY123,A.02.17,boot 1.4")
        assert firmware == "A.02.17,boot 1.4"


class TestSuggestAlias:
    def test_vendor_model_lowercase_with_underscores(self):
        assert discovery.suggest_alias("TEKTRONIX", "MSO44") == "tektronix_mso44"

    def test_punctuation_folds_to_single_underscore(self):
        assert discovery.suggest_alias("Rohde & Schwarz", "FSW-26") == "rohde_schwarz_fsw_26"

    def test_no_usable_fields_returns_none(self):
        assert discovery.suggest_alias(None, "") is None


# ---------------------------------------------------------------------------
# VISA sweep
# ---------------------------------------------------------------------------


class TestScanVisa:
    def test_resource_found_and_identified(self, visa_resources, serial_ports):
        resource = MagicMock()
        resource.query.return_value = _SCOPE_IDN
        visa_resources.list_resources.return_value = (_SCOPE_RESOURCE,)
        visa_resources.open_resource.return_value = resource

        result = discovery.scan()

        assert result.action_items == []
        assert len(result.devices) == 1
        device = result.devices[0]
        assert device.resource == _SCOPE_RESOURCE
        assert device.driver_type == "visa"
        assert device.interface_type == "USB"
        assert device.identified is True
        assert device.manufacturer == "TEKTRONIX"
        assert device.model == "MSO44"
        assert device.serial_number == "C012345"
        assert device.firmware == "CF:91.1CT FV:1.2.3"
        assert device.suggested_alias == "tektronix_mso44"
        resource.close.assert_called_once()

    def test_probe_timeout_is_applied_per_resource(self, visa_resources, serial_ports):
        resource = MagicMock()
        resource.query.return_value = _SCOPE_IDN
        visa_resources.list_resources.return_value = (_SCOPE_RESOURCE,)
        visa_resources.open_resource.return_value = resource

        discovery.scan(timeout_s=2.0)

        assert resource.timeout == 2000

    def test_resource_that_never_answers_is_still_reported(self, visa_resources, serial_ports):
        resource = MagicMock()
        resource.query.side_effect = pyvisa.Error("timeout")
        visa_resources.list_resources.return_value = (_SCOPE_RESOURCE,)
        visa_resources.open_resource.return_value = resource

        result = discovery.scan()

        device = result.devices[0]
        assert device.resource == _SCOPE_RESOURCE
        assert device.identified is False
        assert device.manufacturer is None
        assert device.suggested_alias is None
        assert "*IDN?" in device.detail
        # Half-open resources are closed even when the probe fails.
        resource.close.assert_called_once()

    def test_resource_that_raises_on_open_is_still_reported(self, visa_resources, serial_ports):
        visa_resources.list_resources.return_value = (_SCOPE_RESOURCE, "GPIB0::7::INSTR")
        visa_resources.open_resource.side_effect = pyvisa.Error("resource busy")

        result = discovery.scan()

        assert [d.resource for d in result.devices] == [_SCOPE_RESOURCE, "GPIB0::7::INSTR"]
        assert all(d.identified is False for d in result.devices)
        assert "could not open" in result.devices[0].detail

    def test_list_resources_failure_becomes_an_action_item(self, visa_resources, serial_ports):
        visa_resources.list_resources.side_effect = pyvisa.Error("no backend")

        result = discovery.scan()

        assert result.devices == []
        assert "VISA sweep failed" in result.action_items[0]

    def test_pyvisa_missing_skips_sweep_and_names_the_extra(self, no_visa, serial_ports):
        serial_ports.append(_fake_port())

        result = discovery.scan()

        assert "pip install lablink-mcp[visa]" in result.action_items[0]
        # The other sweep still runs.
        assert [d.resource for d in result.devices] == ["/dev/ttyUSB0"]


# ---------------------------------------------------------------------------
# Serial sweep
# ---------------------------------------------------------------------------


class TestScanSerial:
    def test_port_identified_and_bus_metadata_reported(self, visa_resources, serial_ports, monkeypatch):
        conn = MagicMock()
        conn.read_until.return_value = b"LabLink,FG-100,SIM-0001,1.0.0\n"
        monkeypatch.setattr(serial, "Serial", lambda **kwargs: conn)
        serial_ports.append(_fake_port())

        result = discovery.scan()

        device = result.devices[0]
        assert device.driver_type == "serial"
        assert device.interface_type == "serial"
        assert device.identified is True
        assert device.model == "FG-100"
        assert device.suggested_alias == "lablink_fg_100"
        assert "VID:PID=1a86:7523" in device.detail
        conn.close.assert_called_once()

    def test_silent_port_is_reported_as_found_not_identified(self, visa_resources, serial_ports, monkeypatch):
        conn = MagicMock()
        conn.read_until.return_value = b""
        monkeypatch.setattr(serial, "Serial", lambda **kwargs: conn)
        serial_ports.append(_fake_port())

        result = discovery.scan()

        device = result.devices[0]
        assert device.identified is False
        assert device.resource == "/dev/ttyUSB0"
        assert "USB-Serial CH340" in device.detail
        assert "no *IDN? reply" in device.detail

    def test_port_that_raises_on_open_is_still_reported(self, visa_resources, serial_ports, monkeypatch):
        def boom(**kwargs):
            raise serial.SerialException("permission denied")

        monkeypatch.setattr(serial, "Serial", boom)
        serial_ports.append(_fake_port())

        result = discovery.scan()

        assert result.devices[0].identified is False
        assert "could not open" in result.devices[0].detail

    def test_pyserial_missing_skips_sweep_and_names_the_extra(self, visa_resources, no_serial):
        resource = MagicMock()
        resource.query.return_value = _SCOPE_IDN
        visa_resources.list_resources.return_value = (_SCOPE_RESOURCE,)
        visa_resources.open_resource.return_value = resource

        result = discovery.scan()

        assert "pip install lablink-mcp[serial]" in result.action_items[0]
        # The other sweep still runs.
        assert [d.resource for d in result.devices] == [_SCOPE_RESOURCE]


# ---------------------------------------------------------------------------
# Nothing attached
# ---------------------------------------------------------------------------


def test_nothing_found_reports_no_devices_and_no_action_items(visa_resources, serial_ports):
    result = discovery.scan()

    assert result.devices == []
    assert result.action_items == []


def test_both_drivers_missing_reports_both_extras(no_visa, no_serial):
    result = discovery.scan()

    assert result.devices == []
    assert len(result.action_items) == 2


# ---------------------------------------------------------------------------
# CLI rendering
# ---------------------------------------------------------------------------


class TestScanCommand:
    def _run(self, monkeypatch, result):
        monkeypatch.setattr(discovery, "scan", lambda *a, **kw: result)
        return CliRunner().invoke(cli_module.cli, ["scan"])

    def test_table_lists_idn_fields_and_suggested_alias(self, monkeypatch):
        from lablink.base import DiscoveredDevice, ScanResult

        result = ScanResult(
            devices=[
                DiscoveredDevice(
                    resource=_SCOPE_RESOURCE,
                    driver_type="visa",
                    interface_type="USB",
                    identified=True,
                    idn=_SCOPE_IDN.strip(),
                    manufacturer="TEKTRONIX",
                    model="MSO44",
                    serial_number="C012345",
                    firmware="1.2.3",
                    suggested_alias="tektronix_mso44",
                ),
                DiscoveredDevice(
                    resource="/dev/ttyUSB0",
                    driver_type="serial",
                    interface_type="serial",
                    detail="USB-Serial CH340 (no *IDN? reply)",
                ),
            ]
        )

        invoked = self._run(monkeypatch, result)

        assert invoked.exit_code == 0
        assert "SUGGESTED ALIAS" in invoked.output
        assert "tektronix_mso44" in invoked.output
        assert "/dev/ttyUSB0" in invoked.output
        assert "2 device(s) found, 1 identified." in invoked.output
        assert "no *IDN? reply" in invoked.output

    def test_empty_scan_explains_what_that_means(self, monkeypatch):
        from lablink.base import ScanResult

        invoked = self._run(monkeypatch, ScanResult())

        assert invoked.exit_code == 0
        assert "No devices found." in invoked.output
        assert "powered off" in invoked.output
        assert "SUGGESTED ALIAS" not in invoked.output

    def test_missing_driver_action_items_are_shown(self, monkeypatch):
        from lablink.base import ScanResult

        invoked = self._run(
            monkeypatch,
            ScanResult(action_items=["pyvisa is not installed. Run: pip install lablink-mcp[visa]"]),
        )

        assert "pip install lablink-mcp[visa]" in invoked.output


# ---------------------------------------------------------------------------
# Config writing (`lablink scan --write-configs`)
# ---------------------------------------------------------------------------


def _found(alias="tektronix_mso44", resource=_SCOPE_RESOURCE, serial_number="C012345",
           driver_type="visa", manufacturer="TEKTRONIX", model="MSO44"):
    """Build an identified DiscoveredDevice as a sweep would have returned it."""
    from lablink.base import DiscoveredDevice

    return DiscoveredDevice(
        resource=resource,
        driver_type=driver_type,
        interface_type="USB" if driver_type == "visa" else "serial",
        identified=True,
        idn=f"{manufacturer},{model},{serial_number},1.2.3",
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        suggested_alias=alias,
    )


class TestWriteConfigs:
    def test_writes_one_config_per_identified_device(self, tmp_path):
        outcomes = discovery.write_configs([_found()], tmp_path)

        assert [o.alias for o in outcomes] == ["tektronix_mso44"]
        path = tmp_path / "tektronix_mso44.toml"
        assert outcomes[0].path == path
        body = path.read_text()
        assert 'type            = "visa"' in body
        assert f'resource_string = "{_SCOPE_RESOURCE}"' in body
        assert 'manufacturer    = "TEKTRONIX"' in body
        assert 'model_number    = "MSO44"' in body
        # The header teaches the schema rather than dumping every default.
        assert body.startswith("# tektronix_mso44 — written by `lablink scan --write-configs`.")
        assert "*IDN? ->" in body
        keys = [
            line.split("=")[0].strip()
            for line in body.splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert keys == [
            "type", "alias", "resource_string", "manufacturer", "model_number", "timeout_ms",
        ]

    def test_written_config_round_trips_through_load_config(self, tmp_path, monkeypatch):
        from lablink.config import load_config
        from lablink.interfaces.visa.config import VisaDriverConfig

        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))
        discovery.write_configs([_found()], tmp_path)

        config = load_config("tektronix_mso44")

        assert isinstance(config, VisaDriverConfig)
        assert config.alias == "tektronix_mso44"
        assert config.type == "visa"
        assert config.resource_string == _SCOPE_RESOURCE
        assert config.manufacturer == "TEKTRONIX"
        assert config.model_number == "MSO44"
        assert config.timeout_ms > 0
        assert config.read_termination == "\n"

    def test_written_serial_config_round_trips(self, tmp_path, monkeypatch):
        from lablink.config import load_config
        from lablink.interfaces.serial.config import SerialDriverConfig

        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))
        device = _found(alias="lablink_fg_100", resource="/dev/ttyUSB0",
                        driver_type="serial", manufacturer="LabLink", model="FG-100")
        discovery.write_configs([device], tmp_path)

        config = load_config("lablink_fg_100")

        assert isinstance(config, SerialDriverConfig)
        assert config.serial_port == "/dev/ttyUSB0"
        assert config.baud_rate == 115200

    def test_quotes_in_idn_fields_do_not_break_the_toml(self, tmp_path, monkeypatch):
        from lablink.config import load_config

        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))
        discovery.write_configs([_found(manufacturer='ACME "Labs"')], tmp_path)

        assert load_config("tektronix_mso44").manufacturer == 'ACME "Labs"'

    def test_existing_file_is_skipped_not_overwritten(self, tmp_path):
        path = tmp_path / "tektronix_mso44.toml"
        path.write_text("# hand-edited\n")

        outcomes = discovery.write_configs([_found()], tmp_path)

        assert outcomes[0].path is None
        assert "already exists" in outcomes[0].reason
        assert "--force" in outcomes[0].reason
        assert path.read_text() == "# hand-edited\n"

    def test_force_overwrites(self, tmp_path):
        path = tmp_path / "tektronix_mso44.toml"
        path.write_text("# hand-edited\n")

        outcomes = discovery.write_configs([_found()], tmp_path, force=True)

        assert outcomes[0].path == path
        assert "hand-edited" not in path.read_text()

    def test_duplicate_alias_is_disambiguated_by_serial_number(self, tmp_path):
        devices = [
            _found(resource="USB0::0x0699::0x0527::C012345::INSTR", serial_number="C012345"),
            _found(resource="USB0::0x0699::0x0527::C099999::INSTR", serial_number="C099999"),
        ]

        outcomes = discovery.write_configs(devices, tmp_path)

        assert [o.alias for o in outcomes] == ["tektronix_mso44", "tektronix_mso44_c099999"]
        assert (tmp_path / "tektronix_mso44_c099999.toml").exists()

    def test_duplicate_alias_without_serial_falls_back_to_a_number(self, tmp_path):
        devices = [
            _found(resource="GPIB0::7::INSTR", serial_number=""),
            _found(resource="GPIB0::8::INSTR", serial_number=""),
            _found(resource="GPIB0::9::INSTR", serial_number=""),
        ]

        outcomes = discovery.write_configs(devices, tmp_path)

        assert [o.alias for o in outcomes] == [
            "tektronix_mso44", "tektronix_mso44_2", "tektronix_mso44_3",
        ]

    def test_unidentified_device_is_skipped_with_a_reason(self, tmp_path):
        from lablink.base import DiscoveredDevice

        silent = DiscoveredDevice(
            resource="/dev/ttyUSB0",
            driver_type="serial",
            interface_type="serial",
            detail="USB-Serial CH340 (no *IDN? reply)",
        )

        outcomes = discovery.write_configs([silent, _found()], tmp_path)

        assert outcomes[0].path is None
        assert outcomes[0].alias is None
        assert "not identified" in outcomes[0].reason
        assert list(tmp_path.glob("*.toml")) == [tmp_path / "tektronix_mso44.toml"]


class TestScanWriteConfigsCommand:
    def _run(self, monkeypatch, devices, args):
        from lablink.base import ScanResult

        monkeypatch.setattr(discovery, "scan", lambda *a, **kw: ScanResult(devices=devices))
        return CliRunner().invoke(cli_module.cli, ["scan", *args])

    def test_default_target_is_the_configured_config_dir(self, monkeypatch, tmp_path):
        from pathlib import Path

        from lablink.config import get_config_dir

        devices_dir = tmp_path / "devices"
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(devices_dir))
        assert get_config_dir() == devices_dir

        invoked = self._run(monkeypatch, [_found()], ["--write-configs"])

        assert invoked.exit_code == 0
        assert (devices_dir / "tektronix_mso44.toml").exists()
        # A test that wrote to the developer's real config dir would be a bug.
        assert not (Path.home() / ".lablink" / "devices" / "tektronix_mso44.toml").exists()

    def test_explicit_directory_overrides_the_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path / "unused"))

        invoked = self._run(monkeypatch, [_found()], ["--write-configs", str(tmp_path / "here")])

        assert (tmp_path / "here" / "tektronix_mso44.toml").exists()
        assert not (tmp_path / "unused").exists()
        assert f"Wrote {tmp_path / 'here' / 'tektronix_mso44.toml'}" in invoked.output

    def test_prints_the_next_command_to_run(self, monkeypatch, tmp_path):
        invoked = self._run(monkeypatch, [_found()], ["--write-configs", str(tmp_path)])

        assert "lablink connect tektronix_mso44" in invoked.output

    def test_skips_are_reported_with_their_reason(self, monkeypatch, tmp_path):
        (tmp_path / "tektronix_mso44.toml").write_text("# hand-edited\n")

        invoked = self._run(monkeypatch, [_found()], ["--write-configs", str(tmp_path)])

        assert "already exists" in invoked.output
        assert "No configs written." in invoked.output
        assert (tmp_path / "tektronix_mso44.toml").read_text() == "# hand-edited\n"

    def test_force_flag_overwrites(self, monkeypatch, tmp_path):
        (tmp_path / "tektronix_mso44.toml").write_text("# hand-edited\n")

        invoked = self._run(
            monkeypatch, [_found()], ["--write-configs", str(tmp_path), "--force"]
        )

        assert "Wrote" in invoked.output
        assert "hand-edited" not in (tmp_path / "tektronix_mso44.toml").read_text()

    def test_without_the_flag_nothing_is_written_and_the_flag_is_suggested(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))

        invoked = self._run(monkeypatch, [_found()], [])

        assert list(tmp_path.glob("*.toml")) == []
        assert "lablink scan --write-configs" in invoked.output
