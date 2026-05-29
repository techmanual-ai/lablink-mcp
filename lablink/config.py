"""Instrument config loader.

Reads <config_dir>/<alias>.toml, validates required fields, and returns a
typed InstrumentConfig dataclass. All config access must go through this module.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from lablink.exceptions import ConfigError

_REQUIRED_FIELDS = (
    "alias",
    "resource_string",
    "manufacturer",
    "model_number",
    "timeout_ms",
    "read_termination",
    "write_termination",
)

_DEFAULT_CONFIG_DIR = Path.home() / ".lablink" / "devices"


@dataclass
class InstrumentConfig:
    """Validated instrument configuration."""

    alias: str
    resource_string: str
    manufacturer: str
    model_number: str
    timeout_ms: int
    read_termination: str
    write_termination: str
    techmanual_document_ids: list[int] = field(default_factory=list)
    description: Optional[str] = None


def _load_document_ids(raw: dict) -> list[int]:
    """Extract techmanual document IDs from a raw config dict.

    Accepts both the current plural format (techmanual_document_ids = [1291, 1323])
    and the legacy singular format (techmanual_document_id = 1291).
    """
    plural = raw.get("techmanual_document_ids")
    if isinstance(plural, list):
        return [int(i) for i in plural]
    singular = raw.get("techmanual_document_id")
    if singular is not None:
        return [int(singular)]
    return []


def get_config_dir() -> Path:
    """Return the instrument config directory, applying env override if set."""
    env_override = os.environ.get("LABLINK_CONFIG_DIR")
    return Path(env_override) if env_override else _DEFAULT_CONFIG_DIR


def load_config(alias: str) -> InstrumentConfig:
    """Load and validate the instrument config for the given alias.

    Args:
        alias: Instrument alias matching the filename (<alias>.toml).

    Returns:
        Validated InstrumentConfig dataclass.

    Raises:
        ConfigError: If the config file is not found or required fields are missing.
    """
    config_dir = get_config_dir()
    config_path = config_dir / f"{alias}.toml"

    if not config_path.exists():
        raise ConfigError(
            f"No config file found for alias '{alias}'. "
            f"Expected: {config_path}"
        )

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    missing = [field for field in _REQUIRED_FIELDS if field not in raw]
    if missing:
        raise ConfigError(
            f"Config for '{alias}' is missing required fields: {', '.join(missing)}"
        )

    return InstrumentConfig(
        alias=raw["alias"],
        resource_string=raw["resource_string"],
        manufacturer=raw["manufacturer"],
        model_number=raw["model_number"],
        timeout_ms=int(raw["timeout_ms"]),
        read_termination=raw["read_termination"],
        write_termination=raw["write_termination"],
        techmanual_document_ids=_load_document_ids(raw),
        description=raw.get("description"),
    )


def load_instrument_memory(alias: str) -> Optional[str]:
    """Return the instrument memory file content for the given alias, or None.

    The memory file (~/.lablink/devices/<alias>.md) is an optional
    agent-maintained document of device-specific quirks and workarounds.
    Returns None if the file does not exist. Never raises.

    Args:
        alias: Instrument alias matching the config filename.
    """
    memory_path = get_config_dir() / f"{alias}.md"
    try:
        return memory_path.read_text(encoding="utf-8") if memory_path.exists() else None
    except Exception:
        return None


def list_configs() -> list[InstrumentConfig]:
    """Return all valid instrument configs in the config directory.

    Returns:
        List of InstrumentConfig objects. Files that fail validation are skipped silently.
    """
    config_dir = get_config_dir()
    if not config_dir.exists():
        return []

    configs = []
    for toml_file in sorted(config_dir.glob("*.toml")):
        alias = toml_file.stem
        try:
            configs.append(load_config(alias))
        except ConfigError:
            pass
    return configs
