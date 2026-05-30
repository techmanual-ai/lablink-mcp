"""Device config loader.

Reads <config_dir>/<alias>.toml, resolves the driver-specific config subclass
via DRIVER_CONFIG_REGISTRY[type], and returns a validated DriverConfig. All
config access must go through this module.
"""

import dataclasses
import os
import sys
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from lablink.base import DriverConfig
from lablink.exceptions import ConfigError

_DEFAULT_CONFIG_DIR = Path.home() / ".lablink" / "devices"

# Fields whose values are filesystem paths and must be tilde-expanded at load
# time (TOML does not auto-expand tildes). See docs/ARCHITECTURE.md §7.5.
_PATH_FIELDS = frozenset({"auth_ssh_key_path", "python_path", "working_dir"})


def get_config_dir() -> Path:
    """Return the device config directory, applying env override if set."""
    env_override = os.environ.get("LABLINK_CONFIG_DIR")
    return Path(env_override) if env_override else _DEFAULT_CONFIG_DIR


def _valid_types() -> list[str]:
    from lablink.interfaces import DRIVER_CONFIG_REGISTRY

    return sorted(DRIVER_CONFIG_REGISTRY.keys())


def load_config(alias: str) -> DriverConfig:
    """Load and validate the device config for the given alias.

    Reads the ``type`` field, resolves the driver-specific config subclass via
    DRIVER_CONFIG_REGISTRY, filters the TOML keys to that subclass's fields, and
    instantiates it. The alias is taken from the filename when absent from the
    TOML body. Path-valued fields are tilde-expanded.

    Args:
        alias: Device alias matching the filename (<alias>.toml).

    Returns:
        A validated DriverConfig subclass instance.

    Raises:
        ConfigError: file not found, missing/unknown ``type``, or missing
            required fields for the resolved driver.
    """
    from lablink.interfaces import DRIVER_CONFIG_REGISTRY

    config_path = get_config_dir() / f"{alias}.toml"
    if not config_path.exists():
        raise ConfigError(
            f"No config file found for alias '{alias}'. Expected: {config_path}"
        )

    with open(config_path, "rb") as f:
        raw = tomllib.load(f)

    type_ = raw.get("type")
    if type_ is None:
        raise ConfigError(
            f"Config for '{alias}' is missing required field: type. "
            f"Valid types: {_valid_types()}."
        )

    config_cls = DRIVER_CONFIG_REGISTRY.get(type_)
    if config_cls is None:
        raise ConfigError(
            f"Unknown driver type '{type_}'. Valid types: {_valid_types()}."
        )

    field_names = {f.name for f in dataclasses.fields(config_cls)}
    kwargs = {k: v for k, v in raw.items() if k in field_names}
    kwargs["type"] = type_
    kwargs.setdefault("alias", alias)

    # Legacy singular techmanual_document_id -> one-element plural list.
    if (
        "techmanual_document_ids" in field_names
        and "techmanual_document_ids" not in kwargs
        and raw.get("techmanual_document_id") is not None
    ):
        kwargs["techmanual_document_ids"] = [int(raw["techmanual_document_id"])]

    for path_field in _PATH_FIELDS & field_names:
        if kwargs.get(path_field) is not None:
            kwargs[path_field] = str(Path(kwargs[path_field]).expanduser())

    try:
        return config_cls(**kwargs)
    except TypeError as exc:
        # Missing a required field (no default) for this driver type.
        raise ConfigError(f"Config for '{alias}' is invalid: {exc}") from exc


def load_device_memory(alias: str) -> Optional[str]:
    """Return the device memory file content for the given alias, or None.

    The memory file (<config_dir>/<alias>.md) is an optional agent-maintained
    document of device-specific quirks and workarounds. Returns None if the
    file does not exist. Never raises. This is the single reader of the memory
    file — drivers never touch it (see docs/ARCHITECTURE.md §8.3).

    Args:
        alias: Device alias matching the config filename.
    """
    memory_path = get_config_dir() / f"{alias}.md"
    try:
        return memory_path.read_text(encoding="utf-8") if memory_path.exists() else None
    except Exception:
        return None


