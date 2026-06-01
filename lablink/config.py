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

from lablink.base import (
    Constraint,
    DeviceConnections,
    DriverConfig,
    Link,
    Net,
    NetEndpoint,
    SystemNode,
    SystemTopology,
)
from lablink.exceptions import ConfigError

_DEFAULT_CONFIG_DIR = Path.home() / ".lablink" / "devices"
_DEFAULT_TOPOLOGY_FILE = Path.home() / ".lablink" / "topology.toml"

# Fields whose values are filesystem paths and must be tilde-expanded at load
# time (TOML does not auto-expand tildes). See docs/ARCHITECTURE.md §7.5.
_PATH_FIELDS = frozenset({"auth_ssh_key_path", "python_path", "working_dir"})


def get_config_dir() -> Path:
    """Return the device config directory, applying env override if set."""
    env_override = os.environ.get("LABLINK_CONFIG_DIR")
    return Path(env_override) if env_override else _DEFAULT_CONFIG_DIR


def get_topology_file() -> Path:
    """Return the topology file path, applying env override if set.

    Resolved independently of LABLINK_CONFIG_DIR — defaults to
    ~/.lablink/topology.toml regardless of where the devices dir is set.
    """
    env_override = os.environ.get("LABLINK_TOPOLOGY_FILE")
    return Path(env_override) if env_override else _DEFAULT_TOPOLOGY_FILE


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


def list_configured_aliases() -> list[str]:
    """Return the stems of all .toml files in the config directory.

    This is the single home for the config-dir glob. Never raises — returns
    [] when the directory does not exist (Path.glob yields nothing for a
    missing dir, but we guard explicitly to avoid any I/O that could throw
    on the _system_audit "cannot fail" path).

    Returns:
        Sorted list of alias strings (filename stems, no extension).
    """
    config_dir = get_config_dir()
    if not config_dir.exists():
        return []
    return sorted(p.stem for p in config_dir.glob("*.toml"))


def _parse_constraints(raw_constraints: list, context: str) -> list[Constraint]:
    """Parse a list of raw constraint tables into Constraint objects.

    ``severity`` is required: a constraint block is a safety signal, and a
    missing severity must not silently become the least-severe value (§4.1).
    ``limit`` and ``note`` default to "" when absent.

    Raises:
        ConfigError: if any constraint omits ``severity``.
    """
    constraints: list[Constraint] = []
    for c in raw_constraints:
        if "severity" not in c:
            raise ConfigError(
                f"topology.toml constraint on {context} is missing required 'severity'."
            )
        constraints.append(
            Constraint(severity=c["severity"], limit=c.get("limit", ""), note=c.get("note", ""))
        )
    return constraints


def load_system() -> Optional[SystemTopology]:
    """Load and parse ~/.lablink/topology.toml (or LABLINK_TOPOLOGY_FILE).

    Returns None when the file is absent — absence is not an error.
    Raises ConfigError on malformed TOML or structural problems (e.g. a
    node with neither ``alias`` nor ``id``, or a constraint missing its
    required ``severity``). Unknown severity *values* are preserved verbatim
    (not a load error) — only an entirely absent severity raises.

    Hot-path callers (connect, diagnose, _system_audit) must catch ConfigError
    and degrade gracefully per docs/system_connections_plan.md §4.2.

    Returns:
        Parsed SystemTopology, or None if the file does not exist.

    Raises:
        ConfigError: on malformed TOML or a structurally invalid document.
    """
    topology_path = get_topology_file()
    if not topology_path.exists():
        return None

    try:
        with open(topology_path, "rb") as f:
            raw = tomllib.load(f)
    except Exception as exc:
        raise ConfigError(f"Failed to parse topology file {topology_path}: {exc}") from exc

    # Parse nodes
    nodes: list[SystemNode] = []
    for i, raw_node in enumerate(raw.get("node", [])):
        alias = raw_node.get("alias")
        id_ = raw_node.get("id")
        if alias is None and id_ is None:
            raise ConfigError(
                f"topology.toml node[{i}] must have at least one of 'alias' or 'id'."
            )
        nodes.append(SystemNode(alias=alias, id=id_, role=raw_node.get("role")))

    # Parse links
    links: list[Link] = []
    for raw_link in raw.get("link", []):
        constraints = _parse_constraints(
            raw_link.get("constraint", []), f"link {raw_link.get('from')} -> {raw_link.get('to')}"
        )
        links.append(
            Link(
                from_port=raw_link["from"],
                to_port=raw_link["to"],
                signal=raw_link.get("signal"),
                params=raw_link.get("params", {}),
                constraints=constraints,
            )
        )

    # Parse nets
    nets: list[Net] = []
    for raw_net in raw.get("net", []):
        endpoints = [
            NetEndpoint(port=ep["port"], role=ep.get("role"))
            for ep in raw_net.get("endpoints", [])
        ]
        constraints = _parse_constraints(
            raw_net.get("constraint", []), f"net '{raw_net.get('name')}'"
        )
        nets.append(
            Net(
                name=raw_net["name"],
                signal=raw_net.get("signal"),
                params=raw_net.get("params", {}),
                endpoints=endpoints,
                constraints=constraints,
            )
        )

    return SystemTopology(name=raw.get("name"), nodes=nodes, links=links, nets=nets)


