"""Instrument config loader.

Reads <config_dir>/<alias>.toml, validates required fields, and returns a
typed InstrumentConfig dataclass. All config access must go through this module.
"""

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
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

_LEGACY_CONFIG_DIR = Path.home() / ".agentlink" / "instruments"
_MIGRATION_MARKER = "MIGRATED.txt"


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
        # Files copied successfully but the marker write failed. A retry will
        # skip already-copied files (no-overwrite rule) and re-attempt the
        # marker. The user-facing summary still fires so the migration is not
        # silently hidden.
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
