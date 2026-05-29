"""VISA driver — PyVISA/SCPI control of T&M instruments.

VisaDriver subclasses LabLinkDriver[VisaDriverConfig]. It owns the shared
pyvisa.ResourceManager (one per server process, lazily created on first
connect; see lablink_plan.md §4.2). All pyvisa imports are lazy — they happen
inside methods, never at module load — so the package imports cleanly without
the [visa] extra installed.
"""

import ctypes.util
import importlib.metadata
import importlib.util
import platform
import re
import socket
import subprocess
from dataclasses import asdict
from typing import Any, Optional

from lablink import session as session_registry
from lablink.base import (
    ConnectResult,
    DiagnosticResult,
    LabLinkDriver,
    ReadResult,
    Result,
    Session,
    SystemDepStatus,
)
from lablink.interfaces.visa.config import VisaDriverConfig
from lablink.event_logger import log_event

_DEFAULT_VISA_BACKEND = "@py"


# ---------------------------------------------------------------------------
# Reachability helpers (ported from the v0.1 diagnostics module)
# ---------------------------------------------------------------------------


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
        return subprocess.run(cmd, capture_output=True, timeout=5).returncode == 0
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

    Handles TCPIP::host::INSTR, TCPIP0::host::inst0::INSTR, etc.
    """
    match = re.match(r"TCPIP\d*::([^:]+)", resource_string, re.IGNORECASE)
    return match.group(1) if match else None


def _interface_type(resource_string: str) -> str:
    upper = resource_string.upper()
    for prefix in ("TCPIP", "USB", "GPIB", "ASRL"):
        if upper.startswith(prefix):
            return prefix
    return "UNKNOWN"


class VisaDriver(LabLinkDriver[VisaDriverConfig]):
    """VISA/SCPI driver."""

    type_name = "visa"

    def __init__(self) -> None:
        # Shared ResourceManager — lazily constructed on first connect() so the
        # pyvisa import stays lazy. Reused across all VISA sessions.
        self._rm: Any = None

    # --- internal ---

    def _get_rm(self) -> Any:
        """Return the shared ResourceManager, creating it on first call."""
        import os

        import pyvisa

        if self._rm is None:
            backend = os.environ.get("LABLINK_VISA_BACKEND", _DEFAULT_VISA_BACKEND)
            self._rm = pyvisa.ResourceManager(backend)
        return self._rm

    # --- lifecycle ---

    def connect(self, config: VisaDriverConfig) -> ConnectResult:
        try:
            import pyvisa
        except ImportError:
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="visa",
                error="Missing dependency: pyvisa",
                hint="Run: pip install lablink-mcp[visa]",
            )

        if session_registry.is_registered(config.alias):
            err = f"Session already open for alias '{config.alias}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="visa",
                error=err,
                hint="Call disconnect(alias) first, or use the existing session.",
            )

        if not config.resource_string:
            err = "Config field 'resource_string' is empty."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="visa",
                error=err,
                hint="Add a resource_string (e.g. 'USB0::0x...::INSTR' or 'TCPIP0::host::INSTR') to the config.",
            )

        try:
            rm = self._get_rm()
            resource = rm.open_resource(config.resource_string)
            resource.timeout = config.timeout_ms
            resource.read_termination = config.read_termination
            resource.write_termination = config.write_termination
        except pyvisa.Error as exc:
            err = f"VISA error: {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="visa",
                error=err,
                hint="Check that the instrument is powered on, the resource string is correct, and the VISA backend is installed.",
            )

        try:
            idn = resource.query("*IDN?").strip()
        except pyvisa.Error as exc:
            # Resource opened but *IDN? failed — close it so the alias is not
            # left half-open. Nothing was registered yet, so there is no
            # session to deregister.
            try:
                resource.close()
            except Exception:
                pass
            err = f"VISA error: {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="visa",
                error=err,
                hint="The instrument opened but did not answer *IDN?. Increase timeout_ms or verify it speaks SCPI.",
            )

        session = Session(
            alias=config.alias,
            interface_type="visa",
            raw=resource,
            config=config,
        )
        session_registry.register(session)
        log_event(op="connect", alias=config.alias, idn=idn, success=True)
        return ConnectResult(
            success=True,
            alias=config.alias,
            interface_type="visa",
            identity=idn,
            techmanual_document_ids=list(config.techmanual_document_ids),
            metadata={
                "manufacturer": config.manufacturer,
                "model_number": config.model_number,
            },
        )

    def disconnect(self, session: Session[VisaDriverConfig]) -> Result:
        try:
            session.raw.close()
        except Exception as exc:
            log_event(op="disconnect", alias=session.alias, success=False, error=str(exc))
            return Result(
                success=False,
                error=f"Error closing VISA session: {exc}",
                hint="Session may already be closed. The alias is deregistered regardless.",
            )
        log_event(op="disconnect", alias=session.alias, success=True)
        return Result(success=True)

    def diagnose(self, config: VisaDriverConfig) -> DiagnosticResult:
        """Stateless per-alias diagnosis: VISA backend health, resource
        visibility, and interface-specific reachability.
        """
        checks: dict[str, Any] = {}
        action_items: list[str] = []
        iface = _interface_type(config.resource_string) if config.resource_string else "UNKNOWN"

        try:
            import pyvisa
        except ImportError:
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type=iface,
                checks={"pyvisa": {"status": "missing", "detail": "pyvisa not installed"}},
                action_items=["pyvisa is not installed. Run: pip install lablink-mcp[visa]"],
            )

        resources: list[str] = []
        try:
            rm = self._get_rm()
            checks["resource_manager"] = {"status": "ok", "detail": None}
            try:
                resources = list(rm.list_resources())
                checks["resources_found"] = {"status": "ok", "detail": resources}
            except Exception as exc:
                checks["resources_found"] = {"status": "error", "detail": str(exc)}
                action_items.append(f"list_resources() failed: {exc}")
        except pyvisa.Error as exc:
            checks["resource_manager"] = {"status": "error", "detail": str(exc)}
            action_items.append(f"Could not create VISA ResourceManager: {exc}")
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type=iface,
                checks=checks,
                action_items=action_items,
            )

        if not config.resource_string:
            action_items.append("Config field 'resource_string' is empty.")
        else:
            in_list = config.resource_string in resources
            checks["in_visa_list"] = {"status": "ok" if in_list else "missing", "detail": in_list}

            if iface == "TCPIP":
                host = _extract_tcpip_host(config.resource_string)
                checks["tcpip_host"] = {"status": "ok" if host else "error", "detail": host}
                if host:
                    ping_ok = _ping(host)
                    checks["ping"] = {"status": "ok" if ping_ok else "fail", "detail": ping_ok}
                    if not ping_ok:
                        action_items.append(
                            f"Cannot ping {host}. Check the instrument is powered on, on the "
                            "same subnet, and not blocked by a firewall."
                        )
                    scpi_ok = _port_open(host, port=5025)
                    checks["scpi_port_5025"] = {"status": "ok" if scpi_ok else "closed", "detail": scpi_ok}
                    if ping_ok and not scpi_ok:
                        action_items.append(
                            f"{host} is reachable but TCP port 5025 (SCPI) is closed. Enable the "
                            "LAN SCPI socket server in the instrument's network settings."
                        )
                else:
                    action_items.append(
                        f"Could not parse host from resource string: {config.resource_string!r}. "
                        "Expected TCPIP[0]::host::INSTR."
                    )
            elif iface == "USB":
                if not in_list:
                    action_items.append(
                        f"USB resource '{config.resource_string}' is not in list_resources(). "
                        "Check the cable, power, and USB driver (libusb)."
                    )
            elif iface == "GPIB":
                gpib = [r for r in resources if r.upper().startswith("GPIB")]
                if not gpib:
                    action_items.append(
                        "No GPIB resources detected. Check the GPIB adapter and its driver "
                        "(pyvisa-py has limited GPIB support — NI-VISA may be required)."
                    )
                elif not in_list:
                    action_items.append(
                        f"GPIB resource '{config.resource_string}' not found. Verify the GPIB "
                        "address on the instrument's front panel."
                    )

        return DiagnosticResult(
            ready=len(action_items) == 0,
            alias=config.alias,
            interface_type=iface,
            checks=checks,
            action_items=action_items,
        )

    # --- operation logic (shared by MCP tools and CLI) ---

    def visa_query_impl(
        self, alias: str, command: str, timeout_ms: Optional[int] = None
    ) -> dict:
        lookup = session_registry.lookup(alias, expected_type="visa")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            log_event(op="visa_query", alias=alias, command=command, success=False, error=result.error)
            return asdict(result)

        import pyvisa

        session = lookup.session
        # Reset the timeout from scratch every call — never rely on the previous
        # call's state (lablink_plan.md §6.2 per-call timeout invariant).
        session.raw.timeout = timeout_ms or session.config.timeout_ms
        try:
            response = session.raw.query(command).strip()
            result = ReadResult(success=True, raw=response, format="text")
        except pyvisa.errors.VisaIOError as exc:
            result = ReadResult(
                success=False,
                error=f"VISA I/O error: {exc}",
                hint="Check command syntax and that the instrument is ready. A timeout may mean it does not respond to this query — try *OPC? first or increase timeout_ms.",
            )
        except pyvisa.Error as exc:
            result = ReadResult(
                success=False,
                error=f"VISA error: {exc}",
                hint="Unexpected VISA error. Try disconnect() and reconnect().",
            )

        log_event(
            op="visa_query", alias=alias, command=command,
            response=result.raw, success=result.success, error=result.error,
        )
        return asdict(result)

    def visa_write_impl(
        self, alias: str, command: str, timeout_ms: Optional[int] = None
    ) -> dict:
        lookup = session_registry.lookup(alias, expected_type="visa")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            log_event(op="visa_write", alias=alias, command=command, success=False, error=result.error)
            return asdict(result)

        import pyvisa

        session = lookup.session
        session.raw.timeout = timeout_ms or session.config.timeout_ms
        try:
            session.raw.write(command)
            result = ReadResult(success=True, raw=None)
        except pyvisa.errors.VisaIOError as exc:
            result = ReadResult(
                success=False,
                error=f"VISA I/O error: {exc}",
                hint="Check the command syntax and that the instrument is connected.",
            )
        except pyvisa.Error as exc:
            result = ReadResult(
                success=False,
                error=f"VISA error: {exc}",
                hint="Unexpected VISA error. Try disconnect() and reconnect().",
            )

        log_event(op="visa_write", alias=alias, command=command, success=result.success, error=result.error)
        return asdict(result)

    @staticmethod
    def _no_session_result(alias: str, lookup: "session_registry.SessionLookup") -> ReadResult:
        if lookup.wrong_type:
            return ReadResult(
                success=False,
                error=f"Alias '{alias}' has an open {lookup.actual_type} session, not a VISA session.",
                hint=f"Use a {lookup.actual_type}_* tool for this alias, or disconnect and re-add the config with type='visa'.",
            )
        return ReadResult(
            success=False,
            error=f"No open session for '{alias}'.",
            hint="Call connect(alias) first.",
        )

    # --- registration ---

    def register_tools(self, mcp) -> None:
        driver = self

        @mcp.tool()
        def visa_query(alias: str, command: str, timeout_ms: int | None = None) -> dict:
            """Send a SCPI query to a VISA instrument (write + read atomically).

            Use for commands that return data (queries ending in '?'). The
            instrument session must already be open via connect(alias).

            Args:
                alias: Configured device alias (must be a VISA-type alias).
                command: SCPI query string, e.g. "MEAS:FREQ? CH1".
                timeout_ms: Per-call timeout override in milliseconds; defaults
                    to the config's timeout_ms.

            Returns a ReadResult dict: success, raw (the response string),
            format, error, hint. A VISA I/O error usually means the command is
            unsupported, the syntax is wrong for this firmware, or the
            instrument is still settling — disambiguate via the hint.
            """
            return driver.visa_query_impl(alias, command, timeout_ms)

        @mcp.tool()
        def visa_write(alias: str, command: str, timeout_ms: int | None = None) -> dict:
            """Send a SCPI write command to a VISA instrument (no response).

            Write is fire-and-forget: success confirms bytes were delivered
            without a VISA-layer error, NOT that the instrument changed state.
            Follow any state-changing write with a confirming visa_query.

            Args:
                alias: Configured device alias (must be a VISA-type alias).
                command: SCPI command string, e.g. "CH1:SCALE 0.5".
                timeout_ms: Per-call timeout override in milliseconds; defaults
                    to the config's timeout_ms.
            """
            return driver.visa_write_impl(alias, command, timeout_ms)

    def register_cli_commands(self, cli_group) -> None:
        import sys

        import click

        from lablink.config import load_config
        from lablink.exceptions import ConfigError

        driver = self

        def _with_session(alias: str, op):
            """Open a VISA session, run op(), and always disconnect.

            The CLI is per-invocation (no session persists across commands), so
            each VISA command opens and closes its own session — matching the
            debug-UX contract documented in current_status.md.
            """
            try:
                config = load_config(alias)
            except ConfigError as exc:
                click.echo(f"Error: {exc}", err=True)
                sys.exit(1)
            conn = driver.connect(config)
            if not conn.success:
                click.echo(f"Error: {conn.error}", err=True)
                click.echo(f"Hint: {conn.hint}", err=True)
                sys.exit(1)
            try:
                return op()
            finally:
                session = session_registry.get_any(alias)
                if session is not None:
                    driver.disconnect(session)
                    session_registry.deregister(alias)

        def _emit(result: dict, on_success) -> None:
            if result["success"]:
                on_success(result)
            else:
                click.echo(f"Error: {result['error']}", err=True)
                click.echo(f"Hint: {result['hint']}", err=True)
                sys.exit(1)

        @cli_group.group(name="visa")
        def visa_group() -> None:
            """VISA/SCPI operations."""

        @visa_group.command(name="query")
        @click.argument("alias")
        @click.argument("command")
        def visa_query_cmd(alias: str, command: str) -> None:
            """Send a SCPI query COMMAND to ALIAS and print the response."""
            result = _with_session(alias, lambda: driver.visa_query_impl(alias, command))
            _emit(result, lambda r: click.echo(r["raw"]))

        @visa_group.command(name="write")
        @click.argument("alias")
        @click.argument("command")
        def visa_write_cmd(alias: str, command: str) -> None:
            """Send a SCPI write COMMAND to ALIAS (no response expected)."""
            result = _with_session(alias, lambda: driver.visa_write_impl(alias, command))
            _emit(result, lambda r: click.echo(f"Sent: {command}", err=True))

    # --- system audit hooks ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        return [
            ("pyvisa", importlib.util.find_spec("pyvisa") is not None),
            ("pyvisa-py", importlib.util.find_spec("pyvisa_py") is not None),
        ]

    @classmethod
    def system_dep_check(cls) -> list[SystemDepStatus]:
        present = ctypes.util.find_library("usb-1.0") is not None
        system = platform.system()
        if system == "Darwin":
            hint = "brew install libusb"
        elif system == "Windows":
            hint = "pip install libusb-package"
        else:
            hint = "apt install libusb-1.0-0  (or your distro's equivalent)"
        version = None
        if present:
            try:
                version = importlib.metadata.version("libusb-package")
            except importlib.metadata.PackageNotFoundError:
                version = None
        return [
            SystemDepStatus(
                name="libusb",
                present=present,
                version=version,
                install_hint=None if present else hint,
            )
        ]
