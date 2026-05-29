"""LabLink MCP server entrypoint.

Installed as the 'lablink-mcp' console script via pip install.
Configure in your MCP client as: {"command": "lablink-mcp"}

Architecture (lablink_plan.md §2.2, §4.1):
  - Shared lifecycle tools (connect, disconnect, list_devices, diagnose) are
    defined here and dispatch to the owning driver via DRIVER_REGISTRY.
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
    load_config,
    load_device_memory,
    maybe_migrate_legacy_configs,
)
from lablink.exceptions import ConfigError
from lablink.interfaces import DRIVER_REGISTRY
from lablink.scpi_logger import log_event

# NOTE: _INSTRUCTIONS is intentionally still VISA-flavored. The multi-driver
# rewrite (with a runtime-aware loaded-driver count) is Phase 0c task 3. VISA is
# the only registered driver at the end of 0b, so this text does not mislead.
_INSTRUCTIONS = """
You are operating LabLink, an MCP server for direct AI agent control of
test and measurement instruments via VISA/SCPI.

## Tool surface

Shared lifecycle tools (any device, identified by alias):
  connect, disconnect, list_devices, diagnose
Per-driver operation tools (registered only when the driver's deps are present):
  VISA: visa_query, visa_write

## Your role in instrument setup

You own the instrument configuration. Users should not need to create or edit
config files manually. When a user mentions an instrument or asks to connect:

1. Call list_devices() to check for existing configs.
2. If no config exists, discover connected instruments:
   python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
   The output is a tuple of VISA resource strings, e.g.:
   ('USB0::0x0699::0x0527::C012345::INSTR',)
   If the tuple is empty, the instrument may be off, disconnected, or require a
   driver — diagnose before asking the user.
3. Write the config file to ~/.lablink/devices/<alias>.toml. Include a
   type = "visa" field. Use the manufacturer and model from the IDN response or
   the user's description. Default termination values work for most instruments:
     read_termination = "\\n"
     write_termination = "\\n"
   Name the alias using the convention <manufacturer>_<model>, lowercase with
   underscores (e.g. siglent_sds1104xe, tektronix_mso44, keysight_dsox1204g).
4. If techmanual.ai is available, search for the model number and extract document
   IDs from the results. Instruments typically have two relevant documents: a user
   manual and a programming guide. Add both to the config:
     techmanual_document_ids = [<user_manual_id>, <programming_guide_id>]
5. Call connect(alias) to open the session and confirm.

## Config file format

~/.lablink/devices/<alias>.toml — one file per instrument.

Required: type, alias, resource_string, timeout_ms
Optional: manufacturer, model_number, read_termination, write_termination,
          description, techmanual_document_ids (list of ints, e.g. [1291, 1323])

The legacy single-ID format (techmanual_document_id = 142) is still accepted
and auto-converted to a one-element list on load.

## Using techmanual.ai

If the techmanual.ai MCP tool is available, use it as the primary SCPI and
instrument reference — do not rely on training data alone for command syntax.

**On every connect:** check the techmanual_document_ids list in the response.
- If non-empty: query those documents directly before issuing any SCPI.
- If empty and techmanual is available: search by manufacturer and
  model_number, then update the config with the discovered IDs.

## Troubleshooting

Call diagnose(alias) first. It checks dependencies, the VISA backend, available
resources, and interface-specific reachability. Use its action_items list to
guide the user step by step. Call diagnose() with no alias for a system audit
of which drivers are installed and what is missing.

## Device Memory

Each device may have a memory file at ~/.lablink/devices/<alias>.md containing
device-specific quirks documented by previous agents. connect() returns a
device_memory field — read it before issuing any commands.

## VISA/SCPI Behavior

**Write is fire-and-forget.** visa_write returning success confirms bytes were
delivered without a VISA-layer error. It does not confirm the instrument
executed the command. Follow any state-changing write with a confirming
visa_query.

**A query timeout has three distinct causes:** (1) command unsupported by this
instrument, (2) wrong syntax for this firmware generation, (3) instrument busy
or settling — issue *OPC? first or increase timeout_ms.

**Session log.** All tool I/O is logged to ~/.lablink/logs/YYYY-MM-DD.jsonl by
default. Disable by setting LABLINK_LOG_DIR to an empty string.
"""

mcp = FastMCP("lablink-mcp", instructions=_INSTRUCTIONS)

# Server-lifetime driver singletons, keyed by type_name (lablink_plan.md §4.2).
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

    # Inject device_memory at the shared layer via replace() so __post_init__
    # re-runs and mirrors device_memory -> instrument_memory (§6.3.1).
    final = dataclasses.replace(result, device_memory=load_device_memory(alias))
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
    config_dir = get_config_dir()
    devices: list[dict] = []
    if not config_dir.exists():
        return devices
    for toml_file in sorted(config_dir.glob("*.toml")):
        alias = toml_file.stem
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

    log_event(op="diagnose", alias=None, success=ready)
    return asdict(DiagnosticResult(ready=ready, drivers=drivers, action_items=action_items))


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
    final = dataclasses.replace(result, device_memory=load_device_memory(alias))
    log_event(op="diagnose", alias=alias, success=final.ready)
    return asdict(final)


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
    device_memory when a valid config is found.

    Without an alias: a system audit — which driver extras are installed, their
    system-level deps, and a prioritized action_items list of what to install.

    Args:
        alias: Optional device alias for targeted checks.
    """
    return do_diagnose(alias)


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
    maybe_migrate_legacy_configs()
    register_driver_tools()
    mcp.run()


if __name__ == "__main__":
    main()
