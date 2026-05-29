"""Config loader, device-memory, and auto-migration tests.

All filesystem access is redirected to tmp_path; no real config dirs touched.
"""

from unittest.mock import patch

import pytest

import lablink.config as cfg_module
from lablink.config import load_config, load_device_memory, maybe_migrate_legacy_configs
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


# ---------------------------------------------------------------------------
# Auto-migration (Phase 0a contract — unchanged in 0b)
# ---------------------------------------------------------------------------


class TestAutoMigration:
    def _setup(self, tmp_path, monkeypatch):
        src = tmp_path / "legacy"
        dest = tmp_path / "new"
        src.mkdir(parents=True)
        monkeypatch.setattr(cfg_module, "_LEGACY_CONFIG_DIR", src)
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(dest))
        monkeypatch.delenv("LABLINK_AUTO_MIGRATE", raising=False)
        return src, dest

    def test_happy_path_copies_and_injects_type(self, tmp_path, monkeypatch, capsys):
        src, dest = self._setup(tmp_path, monkeypatch)
        toml = b'alias = "scope_a"\nresource_string = "USB0::0x1::0x2::A::INSTR"\n'
        (src / "scope_a.toml").write_bytes(toml)
        (src / "scope_a.md").write_text("# memory", encoding="utf-8")

        n = maybe_migrate_legacy_configs()

        assert n == 2
        copied = (dest / "scope_a.toml").read_bytes()
        assert copied.startswith(b'type = "visa"\n')
        assert b'alias = "scope_a"' in copied
        assert (dest / "scope_a.md").read_text(encoding="utf-8") == "# memory"
        marker = src / "MIGRATED.txt"
        assert marker.exists()
        assert "scope_a.toml" in marker.read_text(encoding="utf-8")
        assert "Migrated 2 config file(s)" in capsys.readouterr().err

    def test_existing_type_field_not_overwritten(self, tmp_path, monkeypatch):
        src, dest = self._setup(tmp_path, monkeypatch)
        toml = b'type = "ssh"\nalias = "lab_pi"\nhost = "192.168.1.10"\n'
        (src / "lab_pi.toml").write_bytes(toml)
        maybe_migrate_legacy_configs()
        copied = (dest / "lab_pi.toml").read_bytes()
        assert copied == toml
        assert copied.count(b"type =") == 1

    def test_marker_gates_rerun(self, tmp_path, monkeypatch, capsys):
        src, dest = self._setup(tmp_path, monkeypatch)
        (src / "scope_a.toml").write_bytes(b'alias = "scope_a"\n')
        (src / "MIGRATED.txt").write_text("migrated_at: yesterday\n", encoding="utf-8")
        assert maybe_migrate_legacy_configs() == 0
        assert not (dest / "scope_a.toml").exists()
        assert "Migrated" not in capsys.readouterr().err

    def test_disabled_via_env(self, tmp_path, monkeypatch):
        src, dest = self._setup(tmp_path, monkeypatch)
        monkeypatch.setenv("LABLINK_AUTO_MIGRATE", "0")
        (src / "scope_a.toml").write_bytes(b'alias = "scope_a"\n')
        assert maybe_migrate_legacy_configs() == 0
        assert not (dest / "scope_a.toml").exists()

    def test_disabled_via_env_case_insensitive(self, tmp_path, monkeypatch):
        for i, val in enumerate(("False", "NO", "false", "no", "0")):
            src, dest = self._setup(tmp_path / f"case_{i}", monkeypatch)
            monkeypatch.setenv("LABLINK_AUTO_MIGRATE", val)
            (src / "scope_a.toml").write_bytes(b'alias = "x"\n')
            assert maybe_migrate_legacy_configs() == 0, f"{val!r} should disable migration"

    def test_destination_with_existing_toml_skips(self, tmp_path, monkeypatch):
        src, dest = self._setup(tmp_path, monkeypatch)
        dest.mkdir()
        (dest / "existing.toml").write_bytes(b'alias = "x"\n')
        (src / "scope_a.toml").write_bytes(b'alias = "scope_a"\n')
        assert maybe_migrate_legacy_configs() == 0
        assert not (dest / "scope_a.toml").exists()
        assert not (src / "MIGRATED.txt").exists()

    def test_destination_with_only_md_still_migrates(self, tmp_path, monkeypatch):
        src, dest = self._setup(tmp_path, monkeypatch)
        dest.mkdir()
        (dest / "pre_staged.md").write_text("# pre-staged", encoding="utf-8")
        (src / "scope_a.toml").write_bytes(b'alias = "scope_a"\n')
        assert maybe_migrate_legacy_configs() == 1
        assert (dest / "scope_a.toml").exists()
        assert (dest / "pre_staged.md").read_text(encoding="utf-8") == "# pre-staged"

    def test_per_file_no_overwrite_logs_warning(self, tmp_path, monkeypatch, capsys):
        src, dest = self._setup(tmp_path, monkeypatch)
        dest.mkdir()
        (dest / "scope_a.md").write_text("pre-staged memory", encoding="utf-8")
        (src / "scope_a.toml").write_bytes(b'alias = "scope_a"\n')
        (src / "scope_a.md").write_text("legacy memory", encoding="utf-8")
        assert maybe_migrate_legacy_configs() == 1
        assert (dest / "scope_a.md").read_text(encoding="utf-8") == "pre-staged memory"
        assert "Skipped: scope_a.md already exists" in capsys.readouterr().err

    def test_malformed_toml_copied_as_is_with_warning(self, tmp_path, monkeypatch, capsys):
        src, dest = self._setup(tmp_path, monkeypatch)
        bad = b"this = is not = valid toml\n"
        (src / "broken.toml").write_bytes(bad)
        assert maybe_migrate_legacy_configs() == 1
        assert (dest / "broken.toml").read_bytes() == bad
        assert "could not parse broken.toml" in capsys.readouterr().err

    def test_no_legacy_dir_is_noop(self, tmp_path, monkeypatch):
        nonexistent = tmp_path / "does_not_exist"
        dest = tmp_path / "new"
        monkeypatch.setattr(cfg_module, "_LEGACY_CONFIG_DIR", nonexistent)
        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(dest))
        monkeypatch.delenv("LABLINK_AUTO_MIGRATE", raising=False)
        assert maybe_migrate_legacy_configs() == 0
        assert not dest.exists()

    def test_ignores_non_toml_non_md_files(self, tmp_path, monkeypatch):
        src, dest = self._setup(tmp_path, monkeypatch)
        (src / "scope_a.toml").write_bytes(b'alias = "scope_a"\n')
        (src / "notes.txt").write_text("a side note", encoding="utf-8")
        (src / "binary.bin").write_bytes(b"\x00\x01")
        assert maybe_migrate_legacy_configs() == 1
        assert (dest / "scope_a.toml").exists()
        assert not (dest / "notes.txt").exists()
        assert not (dest / "binary.bin").exists()
