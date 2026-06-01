"""LabLink MCP server entrypoint.

Installed as the 'lablink-mcp' console script via pip install.
Configure in your MCP client as: {"command": "lablink-mcp"}

Architecture (docs/ARCHITECTURE.md §2, §6.1):
  - Shared lifecycle tools (connect, disconnect, list_devices, diagnose,
    system_topology) are defined here; the per-device ones dispatch to the
    owning driver via DRIVER_REGISTRY.
  - Per-driver operation tools (visa_query, ...) self-register via each
    driver's register_tools(mcp), only when that driver's deps are installed.

The shared lifecycle *logic* lives in plain functions (do_connect, ...) so the
CLI can reuse the exact same dispatch path; the @mcp.tool() wrappers are thin.
"""

import dataclasses
import sys
from dataclasses import asdict
from typing import Optional

from fastmcp import FastMCP

from lablink import session as session_registry
from lablink.base import DiagnosticResult, Result
from lablink.config import (
    get_config_dir,
    get_topology_file,
    list_configured_aliases,
    load_config,
    load_device_memory,
    load_system,
)
from lablink.exceptions import ConfigError
from lablink.interfaces import DRIVER_REGISTRY
from lablink.event_logger import log_event
from lablink.system import device_slice, validate_system

_INSTRUCTIONS_TEMPLATE = """
You are operating LabLink, a local-first MCP server that gives you direct,
structured control over the devices and services in a lab — test instruments
(VISA/SCPI) and, as their extras are installed, SSH hosts, REST APIs, serial
devices, and user-supplied Python environments.

## Architecture

LabLink is multi-driver capable; {driver_count} driver(s) are currently loaded:
{driver_list}. Call diagnose() (no alias) for the authoritative active set.
Each device is addressed by an *alias* whose config `type` field selects the
driver. Two tool layers:

- Shared lifecycle tools (always present): connect, disconnect, list_devices,
  diagnose, system_topology. The driver is resolved from the alias's config.
  system_topology returns the lab's physical wiring map (or one device's slice);
  its constraints are advisory context, not enforced.
- Per-driver operation tools (present only when that driver's deps are
  installed) — e.g. visa_query, visa_write. **Each operation tool's docstring
  is the source of truth for that protocol's semantics; read it rather than
  assuming behavior carries over from another protocol.**

## Discovery flow

1. Read the loaded-driver count above.
2. list_devices() — configured aliases with their type and status
   ("connected" / "configured" / "invalid"). "configured" means the config
   parsed, NOT that the device is reachable.
3. diagnose() with no alias — system audit (installed drivers, missing system
   deps, install commands). diagnose(alias) — targeted reachability for one
   device.

## Device setup

You own device configuration — do not ask the user to hand-edit files. Write
~/.lablink/devices/<alias>.toml with a `type` field plus that driver's required
fields. Alias convention: <vendor>_<model> for instruments, <role>_<host> for
compute targets; lowercase with underscores. For VISA, discover the resource
string with:
  python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
An empty result means the instrument is off, unplugged, or needs a backend —
diagnose before asking the user.

## Device memory

connect() returns a `device_memory` field: the content of
~/.lablink/devices/<alias>.md, where prior agents recorded device-specific
quirks. Read it before issuing commands. (A deprecated `instrument_memory`
field mirrors `device_memory` for back-compat; prefer
`device_memory`.)

## techmanual.ai (documented devices)

For instruments, connect() returns techmanual_document_ids. If the techmanual.ai
MCP tool is available, consult those documents before issuing commands instead
of relying on training data. If the list is empty, search by manufacturer and
model_number and write the discovered IDs back into the config so later
sessions skip the search.

## External devices (type = "external_mcp")

Some devices are controlled by a separate manufacturer-supplied MCP server
rather than a LabLink driver. These appear in list_devices() like any other
alias. Calling connect(alias) on an external device returns routing
instructions in device_memory — read them to learn which external MCP server
and tools to use. Do not call LabLink operation tools (visa_*, ssh_*, etc.)
for external aliases.

## Logging

Every tool call is appended to ~/.lablink/logs/YYYY-MM-DD.jsonl (op, alias,
success, plus per-tool extras). Disable by setting LABLINK_LOG_DIR to "".
"""


def _loaded_driver_types() -> list[str]:
    """Type names whose Python deps are all present (so their tools register)."""
    return [
        type_name
        for type_name, cls in DRIVER_REGISTRY.items()
        if all(present for _, present in cls.check_python_deps())
    ]


def _build_instructions() -> str:
    loaded = _loaded_driver_types()
    return _INSTRUCTIONS_TEMPLATE.format(
        driver_count=len(loaded),
        driver_list=", ".join(loaded)
        if loaded
        else "(none — install an extra, e.g. `pip install lablink-mcp[visa]`)",
    )


_INSTRUCTIONS = _build_instructions()

mcp = FastMCP("lablink-mcp", instructions=_INSTRUCTIONS)

# Server-lifetime driver singletons, keyed by type_name (docs/ARCHITECTURE.md §6.2).
_driver_instances: dict = {}


def get_driver(type_name: str):
    """Return the server-lifetime driver instance for a type, creating it once.

    Drivers are instantiated regardless of dep presence (their __init__ does not
    import third-party deps); a missing dep surfaces from the driver's own
    connect()/diagnose() or is caught earlier by the dep gate in do_connect.
    """
    inst = _driver_instances.get(type_name)
    if inst is None:
        cls = DRIVER_REGISTRY.get(type_name)
        if cls is None:
            return None
        inst = cls()
        _driver_instances[type_name] = inst
    return inst


def _missing_python_deps(type_name: str) -> list[str]:
    cls = DRIVER_REGISTRY[type_name]
    return [pkg for pkg, present in cls.check_python_deps() if not present]


# ---------------------------------------------------------------------------
# Shared lifecycle logic (reused verbatim by the CLI)
# ---------------------------------------------------------------------------


def do_connect(alias: str) -> dict:
    try:
        config = load_config(alias)
    except ConfigError as exc:
        log_event(op="connect", alias=alias, success=False, error=str(exc))
        return {
            "success": False,
            "error": str(exc),
            "hint": f"Check that {get_config_dir() / (alias + '.toml')} exists and has all "
            "required fields, including a 'type' field.",
        }

    missing = _missing_python_deps(config.type)
    if missing:
        err = f"Driver '{config.type}' is missing Python dependencies: {', '.join(missing)}."
        log_event(op="connect", alias=alias, success=False, error=err)
        return {
            "success": False,
            "error": err,
            "hint": f"Run: pip install lablink-mcp[{config.type}]",
        }

    driver = get_driver(config.type)
    result = driver.connect(config)
    if not result.success:
        return asdict(result)

    # Inject device_memory and topology_context in a single replace() so
    # __post_init__ re-runs exactly once (§6.3.1 / §8.3).
    file_memory = load_device_memory(alias)

    # Only inject topology_context when the device actually appears in the
    # wiring — an empty slice (no links, no nets) means "not wired in" → None.
    topo_context = None
    try:
        topo = load_system()
        if topo is not None:
            slice_ = device_slice(topo, alias)
            if slice_.links or slice_.nets:
                topo_context = slice_
    except ConfigError:
        pass  # malformed topology must not break a healthy connect (§4.2)

    final = dataclasses.replace(
        result,
        device_memory=file_memory if file_memory is not None else result.device_memory,
        topology_context=topo_context,
    )
    return asdict(final)


def do_disconnect(alias: str) -> dict:
    session = session_registry.get_any(alias)
    if session is None:
        err = f"No open session for alias '{alias}'."
        log_event(op="disconnect", alias=alias, success=False, error=err)
        return asdict(
            Result(success=False, error=err, hint=f"Call connect('{alias}') first.")
        )

    driver = get_driver(session.interface_type)
    result = driver.disconnect(session)
    # Always deregister, regardless of the driver's return value (§6.1).
    session_registry.deregister(alias)
    return asdict(result)


def do_list_devices() -> list:
    devices: list[dict] = []
    for alias in list_configured_aliases():
        try:
            cfg = load_config(alias)
        except ConfigError as exc:
            devices.append(
                {
                    "alias": alias,
                    "type": None,
                    "description": None,
                    "status": "invalid",
                    "error": str(exc),
                }
            )
            continue
        status = "connected" if session_registry.is_registered(alias) else "configured"
        devices.append(
            {
                "alias": alias,
                "type": cfg.type,
                "description": cfg.description,
                "status": status,
            }
        )
    return devices


def _system_audit() -> dict:
    drivers: dict = {}
    action_items: list[str] = []
    ready = True
    for type_name, cls in DRIVER_REGISTRY.items():
        deps = cls.check_python_deps()
        py_missing = [pkg for pkg, present in deps if not present]
        entry: dict = {"python_deps": {pkg: present for pkg, present in deps}}
        if py_missing:
            entry["status"] = "missing_python"
            entry["system_deps"] = {}
            action_items.append(
                f"Driver '{type_name}': missing Python deps ({', '.join(py_missing)}). "
                f"Run: pip install lablink-mcp[{type_name}]"
            )
            ready = False
        else:
            sysdeps = cls.system_dep_check()
            entry["system_deps"] = {
                s.name: {
                    "present": s.present,
                    "version": s.version,
                    "install_hint": s.install_hint,
                }
                for s in sysdeps
            }
            missing_sys = [s for s in sysdeps if not s.present]
            if missing_sys:
                entry["status"] = "missing_system"
                for s in missing_sys:
                    action_items.append(
                        f"Driver '{type_name}': system dependency '{s.name}' missing. "
                        f"{s.install_hint}"
                    )
                ready = False
            else:
                entry["status"] = "ready"
        drivers[type_name] = entry

    # Topology validation — soft warnings only; never affects ready (§4.2).
    topology_warnings: list[str] = []
    known_aliases = list_configured_aliases()
    try:
        topo = load_system()
        if topo is not None:
            topology_warnings.extend(validate_system(topo, known_aliases))
    except ConfigError as exc:
        topology_warnings.append(f"topology.toml parse error: {exc}")

    log_event(op="diagnose", alias=None, success=ready)
    return asdict(
        DiagnosticResult(
            ready=ready,
            drivers=drivers,
            action_items=action_items,
            topology_warnings=topology_warnings,
        )
    )


def do_diagnose(alias: Optional[str] = None) -> dict:
    if alias is None:
        return _system_audit()

    try:
        config = load_config(alias)
    except ConfigError as exc:
        log_event(op="diagnose", alias=alias, success=False, error=str(exc))
        return asdict(
            DiagnosticResult(
                ready=False, alias=alias, action_items=[str(exc)], error=str(exc)
            )
        )

    missing = _missing_python_deps(config.type)
    if missing:
        item = (
            f"Driver '{config.type}' is missing Python dependencies "
            f"({', '.join(missing)}). Run: pip install lablink-mcp[{config.type}]"
        )
        log_event(op="diagnose", alias=alias, success=False, error=item)
        return asdict(
            DiagnosticResult(
                ready=False,
                alias=alias,
                interface_type=config.type,
                action_items=[item],
            )
        )

    driver = get_driver(config.type)
    result = driver.diagnose(config)
    file_memory = load_device_memory(alias)

    topo_context = None
    try:
        topo = load_system()
        if topo is not None:
            slice_ = device_slice(topo, alias)
            if slice_.links or slice_.nets:
                topo_context = slice_
    except ConfigError:
        pass  # malformed topology must not break diagnose (§4.2)

    final = dataclasses.replace(
        result,
        device_memory=file_memory if file_memory is not None else result.device_memory,
        topology_context=topo_context,
    )
    log_event(op="diagnose", alias=alias, success=final.ready)
    return asdict(final)


def do_system_topology(alias: Optional[str] = None) -> dict:
    try:
        topo = load_system()
    except ConfigError as exc:
        log_event(op="system_topology", alias=alias, success=False, error=str(exc))
        return {
            "success": False,
            "error": str(exc),
            "hint": "Run `lablink topology validate` to locate the problem.",
        }

    if topo is None:
        topo_path = get_topology_file()
        log_event(op="system_topology", alias=alias, success=True)
        result: dict = {
            "success": True,
            "topology": None,
            "metadata": {"note": f"No topology.toml configured. Expected: {topo_path}"},
        }
        if alias is not None:
            result["topology_context"] = None
        return result

    if alias is None:
        log_event(op="system_topology", alias=None, success=True)
        return {"success": True, "topology": asdict(topo)}

    slice_ = device_slice(topo, alias)
    log_event(op="system_topology", alias=alias, success=True)
    return {"success": True, "topology_context": asdict(slice_)}


# ---------------------------------------------------------------------------
# Shared lifecycle tools (thin @mcp.tool wrappers over the logic above)
# ---------------------------------------------------------------------------


@mcp.tool()
def connect(alias: str) -> dict:
    """Open a session to a configured device and verify communication.

    Resolves the driver from the config's `type` field. For VISA instruments
    this sends *IDN? and returns identity, device_memory (quirks from prior
    sessions — read it before issuing commands), and techmanual_document_ids.

    Before calling, ensure ~/.lablink/devices/<alias>.toml exists. If it does
    not, create it — do not ask the user to. See server instructions.

    Args:
        alias: Device alias matching the config filename (e.g. 'tek_mso44').
    """
    return do_connect(alias)


@mcp.tool()
def disconnect(alias: str) -> dict:
    """Close the session for a connected device and release the alias.

    Args:
        alias: Device alias of an open session.
    """
    return do_disconnect(alias)


@mcp.tool()
def list_devices() -> list:
    """List all configured devices with their type, description, and status.

    Status is one of: 'connected' (session open), 'configured' (config parsed;
    NO reachability check performed), or 'invalid' (config failed to load — see
    the 'error' field). 'configured' does not mean reachable — call
    diagnose(alias) or connect(alias) to confirm the device responds.
    """
    return do_list_devices()


@mcp.tool()
def diagnose(alias: str | None = None) -> dict:
    """Diagnose a device, or audit the whole system when alias is omitted.

    With an alias: dispatches to the device's driver for backend health,
    resource visibility, and interface-specific reachability; includes
    device_memory and topology_context (this device's wiring slice) when
    a valid config and topology are found.

    Without an alias: a system audit — which driver extras are installed, their
    system-level deps, a prioritized action_items list of what to install, and
    topology_warnings (soft wiring advisories that never affect the ready flag).

    Args:
        alias: Optional device alias for targeted checks.
    """
    return do_diagnose(alias)


@mcp.tool()
def system_topology(alias: str | None = None) -> dict:
    """Return the system topology — the physical wiring of the lab bench.

    IMPORTANT: Constraints in the topology are ADVISORY ONLY. LabLink surfaces
    severity/limit/note to help you make decisions; it does not and cannot
    enforce them (it does not parse protocol syntax). You are responsible for
    honoring any constraint marked 'critical' before issuing commands.

    Without an alias: returns the full topology (all nodes, links, nets).
    With an alias: returns only that device's wiring slice (its links, nets,
    neighbors, and constraints).

    Returns success=True with topology=None when no topology.toml is configured
    (absence is not an error — not every bench needs one). Returns success=False
    when topology.toml exists but cannot be parsed; run `lablink topology
    validate` to locate the problem.

    Args:
        alias: Optional device alias to filter for a single device's slice.
    """
    return do_system_topology(alias)


# ---------------------------------------------------------------------------
# Per-driver tool registration
# ---------------------------------------------------------------------------


def register_driver_tools() -> None:
    """Instantiate each driver whose Python deps are present and let it register
    its operation tools. Drivers with missing deps are skipped with a stderr
    notice naming the extra to install.
    """
    for type_name, cls in DRIVER_REGISTRY.items():
        missing = [pkg for pkg, present in cls.check_python_deps() if not present]
        if missing:
            print(
                f"[lablink] Driver '{type_name}' not loaded — missing Python deps: "
                f"{', '.join(missing)}. Install with: pip install lablink-mcp[{type_name}]",
                file=sys.stderr,
            )
            continue
        get_driver(type_name).register_tools(mcp)


def main() -> None:
    """Run the MCP server over stdio."""
    register_driver_tools()
    mcp.run()


if __name__ == "__main__":
    main()
