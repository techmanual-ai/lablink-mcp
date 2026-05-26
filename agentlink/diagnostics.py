"""Connection diagnostics for AgentLink-Visa.

Checks installed dependencies, VISA backend health, available hardware
interfaces, and (optionally) alias-specific reachability. Returns a
structured report designed for the agent to communicate clearly to the user.
"""

import importlib.metadata
import os
import platform
import re
import socket
import subprocess
import sys
from typing import Any, Optional

import pyvisa

from agentlink.config import get_config_dir, load_config
from agentlink.exceptions import ConfigError


def _dep_info(package: str) -> dict[str, Any]:
    try:
        return {"installed": True, "version": importlib.metadata.version(package)}
    except importlib.metadata.PackageNotFoundError:
        return {"installed": False, "version": None}


def _ping(host: str) -> bool:
    """Return True if host responds to a single ICMP ping."""
    system = platform.system()
    if system == "Windows":
        cmd = ["ping", "-n", "1", "-w", "2000", host]
    elif system == "Darwin":
        cmd = ["ping", "-c", "1", "-t", "2", host]
    else:
        cmd = ["ping", "-c", "1", "-W", "2", host]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _port_open(host: str, port: int = 5025, timeout: float = 2.0) -> bool:
    """Return True if TCP port is accepting connections on host."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _extract_tcpip_host(resource_string: str) -> Optional[str]:
    """Extract host/IP from a TCPIP resource string.

    Handles: TCPIP::host::INSTR, TCPIP0::host::inst0::INSTR, etc.
    """
    match = re.match(r"TCPIP\d*::([^:]+)", resource_string, re.IGNORECASE)
    return match.group(1) if match else None


def _interface_type(resource_string: str) -> str:
    upper = resource_string.upper()
    for prefix in ("TCPIP", "USB", "GPIB", "ASRL"):
        if upper.startswith(prefix):
            return prefix
    return "UNKNOWN"


def run_diagnostics(alias: Optional[str] = None) -> dict[str, Any]:
    """Run connection diagnostics and return a structured report.

    Checks: Python/OS info, pyvisa dependency versions, VISA backend health,
    discovered resources by interface type, config directory status, and
    (when alias is provided) alias-specific reachability and config validity.

    Args:
        alias: Optional instrument alias to include targeted checks for that
               instrument's config and connection path.

    Returns:
        Dict with keys: system, dependencies, visa, interfaces, config_dir,
        alias_check (if alias given), action_items, ready.
        ``ready`` is True only when action_items is empty.
    """
    action_items: list[str] = []
    report: dict[str, Any] = {}

    # --- System ---
    report["system"] = {
        "os": platform.system(),
        "os_version": platform.release(),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
    }

    # --- Dependencies ---
    deps = {
        "pyvisa": _dep_info("pyvisa"),
        "pyvisa-py": _dep_info("pyvisa-py"),
        "fastmcp": _dep_info("fastmcp"),
    }
    report["dependencies"] = deps

    if not deps["pyvisa"]["installed"]:
        action_items.append("pyvisa is not installed. Run: pip install pyvisa")
    if not deps["pyvisa-py"]["installed"]:
        action_items.append(
            "pyvisa-py is not installed. Run: pip install pyvisa-py  "
            "(required for the default pure-Python VISA backend)"
        )

    # --- VISA backend ---
    visa_backend = os.environ.get("AGENTLINK_VISA_BACKEND", "@py")
    visa: dict[str, Any] = {"backend": visa_backend}
    resources: list[str] = []

    try:
        rm = pyvisa.ResourceManager(visa_backend)
        visa["resource_manager_ok"] = True
        try:
            resources = list(rm.list_resources())
            visa["resources_found"] = resources
        except Exception as exc:
            visa["resources_found"] = []
            visa["list_resources_error"] = str(exc)
            action_items.append(f"list_resources() failed: {exc}")
    except Exception as exc:
        visa["resource_manager_ok"] = False
        visa["resource_manager_error"] = str(exc)
        action_items.append(
            f"Could not create VISA ResourceManager (backend='{visa_backend}'): {exc}"
        )
        if visa_backend == "@py" and platform.system() == "Windows":
            action_items.append(
                "On Windows, USB instruments with pyvisa-py require libusb. "
                "Install it with: pip install libusb-package"
            )

    report["visa"] = visa

    # --- Interfaces ---
    usb = [r for r in resources if r.upper().startswith("USB")]
    gpib = [r for r in resources if r.upper().startswith("GPIB")]
    lan = [r for r in resources if r.upper().startswith("TCPIP")]
    serial = [r for r in resources if r.upper().startswith("ASRL")]

    report["interfaces"] = {
        "usb": {"resources": usb},
        "gpib": {"resources": gpib},
        "lan": {"resources": lan},
        "serial": {"resources": serial},
    }

    if visa.get("resource_manager_ok") and not resources:
        action_items.append(
            "No VISA resources detected. Check that your instrument is powered on and "
            "the cable is connected, then re-run diagnostics."
        )
        if platform.system() == "Windows":
            action_items.append(
                "USB instruments on Windows with pyvisa-py require libusb: "
                "pip install libusb-package"
            )
        if platform.system() == "Linux":
            action_items.append(
                "On Linux, USB instruments may need a udev rule. "
                "See: https://pyvisa.readthedocs.io/en/latest/faq/getting_nivisa.html"
            )

    # --- Config directory ---
    config_dir = get_config_dir()
    toml_files = list(config_dir.glob("*.toml")) if config_dir.exists() else []
    report["config_dir"] = {
        "path": str(config_dir),
        "exists": config_dir.exists(),
        "instrument_count": len(toml_files),
    }

    if not config_dir.exists():
        action_items.append(
            f"Instrument config directory does not exist: {config_dir}. "
            "Create it with: mkdir -p ~/.agentlink/instruments"
        )
    elif not toml_files:
        action_items.append(
            f"No instrument configs found in {config_dir}. "
            "Create a <alias>.toml file for each instrument you want to control."
        )

    # --- Alias-specific checks ---
    if alias is not None:
        alias_check: dict[str, Any] = {"alias": alias}

        try:
            config = load_config(alias)
            alias_check["config_ok"] = True
            alias_check["resource_string"] = config.resource_string
            alias_check["manufacturer"] = config.manufacturer
            alias_check["model_number"] = config.model_number
            alias_check["timeout_ms"] = config.timeout_ms
            iface = _interface_type(config.resource_string)
            alias_check["interface_type"] = iface

            if iface == "TCPIP":
                host = _extract_tcpip_host(config.resource_string)
                alias_check["tcpip_host"] = host
                if host:
                    ping_ok = _ping(host)
                    alias_check["ping_ok"] = ping_ok
                    if not ping_ok:
                        action_items.append(
                            f"Cannot ping {host}. Check: "
                            "(1) instrument is powered on, "
                            "(2) both devices are on the same network/subnet, "
                            "(3) no firewall is blocking ICMP."
                        )
                    scpi_ok = _port_open(host, port=5025)
                    alias_check["scpi_port_5025_open"] = scpi_ok
                    if ping_ok and not scpi_ok:
                        action_items.append(
                            f"{host} is reachable but TCP port 5025 (SCPI) is closed. "
                            "Enable the LAN SCPI socket server in the instrument's "
                            "network/connectivity settings menu."
                        )
                    alias_check["in_visa_list"] = config.resource_string in resources
                else:
                    action_items.append(
                        f"Could not parse host from resource string: {config.resource_string!r}. "
                        "Expected format: TCPIP[0]::host::INSTR"
                    )

            elif iface == "USB":
                alias_check["in_visa_list"] = config.resource_string in resources
                if not alias_check["in_visa_list"]:
                    action_items.append(
                        f"USB resource '{config.resource_string}' is not in list_resources(). "
                        "Check: (1) USB cable is connected, (2) instrument is powered on, "
                        "(3) USB driver is installed "
                        "(libusb on Windows/macOS via 'pip install libusb-package', "
                        "udev rules on Linux)."
                    )

            elif iface == "GPIB":
                alias_check["in_visa_list"] = config.resource_string in resources
                if not gpib:
                    action_items.append(
                        "No GPIB resources detected. Check that your GPIB adapter is "
                        "connected and its driver is installed. "
                        "pyvisa-py has limited GPIB support — NI-VISA may be required."
                    )
                elif not alias_check["in_visa_list"]:
                    action_items.append(
                        f"GPIB resource '{config.resource_string}' not found. "
                        "Verify the GPIB address on the instrument's front panel."
                    )

        except ConfigError as exc:
            alias_check["config_ok"] = False
            alias_check["config_error"] = str(exc)
            action_items.append(
                f"Config for '{alias}' failed to load: {exc}. "
                f"Ensure ~/.agentlink/instruments/{alias}.toml exists and has all required fields."
            )

        report["alias_check"] = alias_check

    report["action_items"] = action_items
    report["ready"] = len(action_items) == 0
    return report
