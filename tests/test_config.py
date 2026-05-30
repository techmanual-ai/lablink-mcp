"""Config loader and device-memory tests.

All filesystem access is redirected to tmp_path; no real config dirs touched.
"""

from unittest.mock import patch

import pytest

import lablink.config as cfg_module
from lablink.config import load_config, load_device_memory
from lablink.exceptions import ConfigError
from lablink.interfaces.visa import VisaDriverConfig


def _write(tmp_path, name: str, content: bytes):
    (tmp_path / name).write_bytes(content)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_file_raises(self, tmp_path):
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            with pytest.raises(ConfigError, match="No config file found"):
                load_config("nonexistent")

    def test_missing_type_raises(self, tmp_path):
        _write(tmp_path, "x.toml", b'alias = "x"\nresource_string = "USB0::INSTR"\ntimeout_ms = 5000\n')
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            with pytest.raises(ConfigError, match="missing required field: type"):
                load_config("x")

    def test_unknown_type_raises_with_valid_types(self, tmp_path):
        _write(tmp_path, "x.toml", b'type = "modbus"\nalias = "x"\ntimeout_ms = 5000\n')
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            with pytest.raises(ConfigError, match="Unknown driver type 'modbus'"):
                load_config("x")

    def test_missing_required_field_raises(self, tmp_path):
        # timeout_ms is required (no default) on DriverConfig.
        _write(tmp_path, "x.toml", b'type = "visa"\nalias = "x"\nresource_string = "USB0::INSTR"\n')
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            with pytest.raises(ConfigError, match="timeout_ms"):
                load_config("x")

    def test_valid_visa_config_loads(self, tmp_path):
        _write(
            tmp_path,
            "scope.toml",
            b'type = "visa"\nalias = "scope"\nresource_string = "USB0::0x1::INSTR"\n'
            b'manufacturer = "Tektronix"\nmodel_number = "MSO44"\ntimeout_ms = 5000\n'
            b'description = "bench scope"\n',
        )
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            config = load_config("scope")
        assert isinstance(config, VisaDriverConfig)
        assert config.type == "visa"
        assert config.alias == "scope"
        assert config.model_number == "MSO44"
        assert config.read_termination == "\n"  # default applied
        assert config.description == "bench scope"

    def test_alias_defaults_to_filename(self, tmp_path):
        _write(tmp_path, "from_name.toml", b'type = "visa"\nresource_string = "USB0::INSTR"\ntimeout_ms = 5000\n')
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            config = load_config("from_name")
        assert config.alias == "from_name"

    def test_plural_document_ids(self, tmp_path):
        _write(
            tmp_path,
            "scope.toml",
            b'type = "visa"\nalias = "scope"\nresource_string = "USB0::INSTR"\ntimeout_ms = 5000\n'
            b"techmanual_document_ids = [1291, 1323]\n",
        )
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            config = load_config("scope")
        assert config.techmanual_document_ids == [1291, 1323]

    def test_singular_document_id_backward_compat(self, tmp_path):
        _write(
            tmp_path,
            "scope.toml",
            b'type = "visa"\nalias = "scope"\nresource_string = "USB0::INSTR"\ntimeout_ms = 5000\n'
            b"techmanual_document_id = 142\n",
        )
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            config = load_config("scope")
        assert config.techmanual_document_ids == [142]

    def test_unknown_keys_are_ignored(self, tmp_path):
        # Stray keys in the TOML must not break construction (filtered to fields).
        _write(
            tmp_path,
            "scope.toml",
            b'type = "visa"\nalias = "scope"\nresource_string = "USB0::INSTR"\ntimeout_ms = 5000\n'
            b'some_future_field = "ignored"\n',
        )
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            config = load_config("scope")
        assert config.alias == "scope"


# ---------------------------------------------------------------------------
# device memory
# ---------------------------------------------------------------------------


class TestDeviceMemory:
    def test_returns_none_when_absent(self, tmp_path):
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            assert load_device_memory("scope") is None

    def test_returns_content_when_present(self, tmp_path):
        content = "# scope — Device Memory\n\n## cursor\n- quirk\n"
        (tmp_path / "scope.md").write_text(content, encoding="utf-8")
        with patch.object(cfg_module, "get_config_dir", return_value=tmp_path):
            assert load_device_memory("scope") == content


