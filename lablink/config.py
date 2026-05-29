"""Device config loader.

Reads <config_dir>/<alias>.toml, resolves the driver-specific config subclass
via DRIVER_CONFIG_REGISTRY[type], and returns a validated DriverConfig. All
config access must go through this module.
"""

import dataclasses
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore[no-redef]

from lablink.base import DriverConfig
from lablink.exceptions import ConfigError

_DEFAULT_CONFIG_DIR = Path.home() / ".lablink" / "devices"

_LEGACY_CONFIG_DIR = Path.home() / ".agentlink" / "instruments"
_MIGRATION_MARKER = "MIGRATED.txt"

# Fields whose values are filesystem paths and must be tilde-expanded at load
# time (TOML does not auto-expand tildes). See lablink_plan.md §5.4.
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
    file — drivers never touch it (see lablink_plan.md §6.3.1).

    Args:
        alias: Device alias matching the config filename.
    """
    memory_path = get_config_dir() / f"{alias}.md"
    try:
        return memory_path.read_text(encoding="utf-8") if memory_path.exists() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Auto-migration from legacy ~/.agentlink/instruments/
# See docs/lablink_plan.md §9 Phase 0a Task 7 for the full contract.
# ---------------------------------------------------------------------------


def _auto_migrate_enabled() -> bool:
    """Return False only when LABLINK_AUTO_MIGRATE is explicitly disabled."""
    val = os.environ.get("LABLINK_AUTO_MIGRATE")
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "no")


def _maybe_inject_visa_type(name: str, content: bytes) -> bytes:
    """Prepend ``type = "visa"\\n`` to a legacy config that lacks a top-level
    ``type`` field. Parse failures fall through with a stderr warning so the
    user can fix unusual files (sectioned TOML, BOM, exotic encoding) by hand.
    """
    try:
        parsed = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        print(
            f"[lablink] Warning: could not parse {name} during migration ({exc}); "
            "copied as-is without injecting `type = \"visa\"`. "
            "Add the field manually if the config does not load.",
            file=sys.stderr,
        )
        return content
    if "type" in parsed:
        return content
    return b'type = "visa"\n' + content


def maybe_migrate_legacy_configs() -> int:
    """Copy configs from the legacy agentlink-visa dir into the LabLink dir.

    Behavior (see ``docs/lablink_plan.md`` §9 Phase 0a Task 7):

    - No-op when ``LABLINK_AUTO_MIGRATE`` is set to ``0`` / ``false`` / ``no``.
    - No-op when the legacy directory does not exist.
    - No-op when ``MIGRATED.txt`` already exists in the legacy directory.
    - No-op when the destination directory already contains any ``*.toml``
      file (a ``.md``-only destination still triggers migration).
    - Copies every ``*.toml`` and ``*.md`` from the legacy directory to the
      destination, preserving filenames. Existing destination files are
      never overwritten; a per-file stderr line is logged when one is
      skipped.
    - For each copied ``.toml``: if parsing succeeds and the parsed dict has
      no top-level ``type`` field, prepends ``type = "visa"\\n``. Parse
      failures copy the file as-is with a stderr warning.
    - On any non-zero copy, writes ``MIGRATED.txt`` to the legacy directory
      and prints one stderr summary line.

    Never raises. Returns the number of files copied (0 on every no-op
    branch and on filesystem-error branches).
    """
    if not _auto_migrate_enabled():
        return 0

    src_dir = _LEGACY_CONFIG_DIR
    if not src_dir.is_dir():
        return 0

    marker = src_dir / _MIGRATION_MARKER
    if marker.exists():
        return 0

    dest_dir = get_config_dir()
    if dest_dir.is_dir() and any(dest_dir.glob("*.toml")):
        return 0

    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(
            f"[lablink] Auto-migration aborted: could not create {dest_dir}: {exc}",
            file=sys.stderr,
        )
        return 0

    copied: list[str] = []
    for src in sorted(src_dir.iterdir()):
        if src.suffix not in (".toml", ".md"):
            continue
        if src.name == _MIGRATION_MARKER:
            continue
        dest = dest_dir / src.name
        if dest.exists():
            print(
                f"[lablink] Skipped: {src.name} already exists in destination.",
                file=sys.stderr,
            )
            continue
        try:
            content = src.read_bytes()
        except OSError as exc:
            print(
                f"[lablink] Skipped: could not read {src.name}: {exc}",
                file=sys.stderr,
            )
            continue

        if src.suffix == ".toml":
            content = _maybe_inject_visa_type(src.name, content)

        try:
            dest.write_bytes(content)
        except OSError as exc:
            print(
                f"[lablink] Skipped: could not write {dest}: {exc}",
                file=sys.stderr,
            )
            continue

        copied.append(src.name)

    if not copied:
        return 0

    marker_text = (
        f"migrated_at: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
        f"dest: {dest_dir}/\n"
        f"files: {', '.join(copied)}\n"
    )
    try:
        marker.write_text(marker_text, encoding="utf-8")
    except OSError as exc:
        print(
            f"[lablink] Warning: migration copied {len(copied)} file(s) but "
            f"could not write {marker}: {exc}.",
            file=sys.stderr,
        )

    print(
        f"[lablink] Migrated {len(copied)} config file(s) from {src_dir}/ "
        f"to {dest_dir}/. See {marker}.",
        file=sys.stderr,
    )
    return len(copied)
