"""Serial driver — pyserial-based RS232/RS422/RS485 byte-stream device control.

SerialDriver subclasses LabLinkDriver[SerialDriverConfig]. All pyserial imports
are lazy — they happen inside methods, never at module load — so the package
imports cleanly without the [serial] extra installed.

Phase 3 ships four tools: serial_query (write + read_until), serial_write
(write only), serial_read (drain buffer), serial_flush (clear buffers).

HTTP semantics note (contrast with REST): serial is a stateful byte stream.
There is no request/response framing — the driver imposes one via
read_termination. If a device sends multi-frame responses, the agent must
call serial_read in a loop; serial_query is only appropriate when the device
always ends its response with the configured terminator.
"""

import importlib.util
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
from lablink.event_logger import log_event
from lablink.interfaces.serial.config import SerialDriverConfig


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_VALID_PARITY = {"none", "even", "odd", "mark", "space"}

# Maps our string parity names to pyserial single-char constants.
_PARITY_MAP = {
    "none": "N",
    "even": "E",
    "odd": "O",
    "mark": "M",
    "space": "S",
}

_VALID_DATA_BITS = {5, 6, 7, 8}


# ---------------------------------------------------------------------------
# SerialDriver
# ---------------------------------------------------------------------------


class SerialDriver(LabLinkDriver[SerialDriverConfig]):
    """Serial driver using pyserial. Handles RS232, RS422, and RS485."""

    type_name = "serial"

    # --- lifecycle ---

    def connect(self, config: SerialDriverConfig) -> ConnectResult:
        try:
            import serial
        except ImportError:
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="serial",
                error="Missing dependency: pyserial",
                hint="Run: pip install lablink-mcp[serial]",
            )

        if session_registry.is_registered(config.alias):
            err = f"Session already open for alias '{config.alias}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="serial",
                error=err,
                hint="Call disconnect(alias) first, or use the existing session.",
            )

        if not config.serial_port:
            err = "Config field 'serial_port' is empty."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="serial",
                error=err,
                hint="Set 'serial_port' to the OS port path (e.g. '/dev/ttyUSB0' or 'COM3').",
            )

        parity_lower = config.parity.lower()
        if parity_lower not in _VALID_PARITY:
            err = f"Invalid parity '{config.parity}'. Valid values: {sorted(_VALID_PARITY)}."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="serial",
                error=err,
                hint="Set parity to one of: none, even, odd, mark, space.",
            )

        try:
            ser = serial.Serial(
                port=config.serial_port,
                baudrate=config.baud_rate,
                bytesize=config.data_bits,
                parity=_PARITY_MAP[parity_lower],
                stopbits=config.stop_bits,
                timeout=config.timeout_ms / 1000,
            )
        except serial.SerialException as exc:
            err = f"Failed to open {config.serial_port}: {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="serial",
                error=err,
                hint="Check that the port exists, is not in use, and you have read/write permission.",
            )

        session = Session(
            alias=config.alias,
            interface_type="serial",
            raw=ser,
            config=config,
        )
        session_registry.register(session)
        identity = f"{config.serial_port}@{config.baud_rate}baud"
        log_event(op="connect", alias=config.alias, identity=identity, success=True)
        return ConnectResult(
            success=True,
            alias=config.alias,
            interface_type="serial",
            identity=identity,
        )

    def disconnect(self, session: Session[SerialDriverConfig]) -> Result:
        try:
            session.raw.close()
        except Exception as exc:
            log_event(op="disconnect", alias=session.alias, success=False, error=str(exc))
            return Result(
                success=False,
                error=f"Error closing serial port: {exc}",
                hint="Port may already be closed. The alias is deregistered regardless.",
            )
        log_event(op="disconnect", alias=session.alias, success=True)
        return Result(success=True)

    def diagnose(self, config: SerialDriverConfig) -> DiagnosticResult:
        """Stateless per-alias diagnosis: config field validation and port existence."""
        checks: dict[str, Any] = {}
        action_items: list[str] = []

        # serial_port
        if not config.serial_port:
            checks["serial_port"] = {"status": "missing", "detail": ""}
            action_items.append(
                "Config field 'serial_port' is empty. Set it to the OS port path "
                "(e.g. '/dev/ttyUSB0' or 'COM3')."
            )
        else:
            checks["serial_port"] = {"status": "ok", "detail": config.serial_port}
            # Port existence — only checkable on POSIX (not COM ports on Windows).
            if sys.platform != "win32":
                exists = Path(config.serial_port).exists()
                checks["port_exists"] = {
                    "status": "ok" if exists else "not_found",
                    "detail": config.serial_port,
                }
                if not exists:
                    action_items.append(
                        f"Port path '{config.serial_port}' does not exist. "
                        "Check that the device is connected and the path is correct. "
                        "On macOS: ls /dev/tty.* to list available ports."
                    )

        # baud_rate
        if config.baud_rate <= 0:
            checks["baud_rate"] = {"status": "invalid", "detail": str(config.baud_rate)}
            action_items.append(f"baud_rate must be a positive integer, got {config.baud_rate}.")
        else:
            checks["baud_rate"] = {"status": "ok", "detail": str(config.baud_rate)}

        # parity
        if config.parity.lower() not in _VALID_PARITY:
            checks["parity"] = {"status": "invalid", "detail": config.parity}
            action_items.append(
                f"Invalid parity '{config.parity}'. Valid values: {sorted(_VALID_PARITY)}."
            )
        else:
            checks["parity"] = {"status": "ok", "detail": config.parity}

        # data_bits
        if config.data_bits not in _VALID_DATA_BITS:
            checks["data_bits"] = {"status": "invalid", "detail": str(config.data_bits)}
            action_items.append(
                f"Invalid data_bits '{config.data_bits}'. Valid values: {sorted(_VALID_DATA_BITS)}."
            )
        else:
            checks["data_bits"] = {"status": "ok", "detail": str(config.data_bits)}

        return DiagnosticResult(
            ready=len(action_items) == 0,
            alias=config.alias,
            interface_type="serial",
            checks=checks,
            action_items=action_items,
        )

    # --- operation logic (shared by MCP tools and CLI) ---

    def _get_session(self, alias: str, op: str) -> tuple[Session | None, dict | None]:
        """Look up the session; return (session, None) or (None, error_dict)."""
        lookup = session_registry.lookup(alias, expected_type="serial")
        if not lookup.found:
            if lookup.wrong_type:
                result = ReadResult(
                    success=False,
                    error=f"Alias '{alias}' has an open {lookup.actual_type} session, not a serial session.",
                    hint=f"Use a {lookup.actual_type}_* tool for this alias, or disconnect and reconfigure with type='serial'.",
                )
            else:
                result = ReadResult(
                    success=False,
                    error=f"No open session for '{alias}'.",
                    hint="Call connect(alias) first.",
                )
            log_event(op=op, alias=alias, success=False, error=result.error)
            return None, asdict(result)
        return lookup.session, None

    def serial_query_impl(
        self,
        alias: str,
        command: str,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "serial_query")
        if err:
            return err

        ser = session.raw
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        ser.timeout = effective_timeout

        write_data = (command + session.config.write_termination).encode("utf-8")
        read_term = session.config.read_termination.encode("utf-8")

        try:
            bytes_written = ser.write(write_data)
            response_bytes = ser.read_until(read_term)
            response = response_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"serial_query failed on '{alias}': {exc}",
                hint="Check device connection and that the command syntax is correct.",
            )
            log_event(op="serial_query", alias=alias, command=command, success=False, error=result.error)
            return asdict(result)

        timed_out = not response_bytes.endswith(read_term)
        result = ReadResult(
            success=True,
            raw=response,
            format="text",
            timed_out=timed_out,
            metadata={"bytes_written": bytes_written},
        )
        log_event(op="serial_query", alias=alias, command=command, bytes_read=len(response_bytes), success=True)
        return asdict(result)

    def serial_write_impl(
        self,
        alias: str,
        command: str,
    ) -> dict:
        session, err = self._get_session(alias, "serial_write")
        if err:
            return err

        ser = session.raw
        write_data = (command + session.config.write_termination).encode("utf-8")

        try:
            bytes_written = ser.write(write_data)
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"serial_write failed on '{alias}': {exc}",
                hint="Check device connection.",
            )
            log_event(op="serial_write", alias=alias, command=command, success=False, error=result.error)
            return asdict(result)

        result = ReadResult(
            success=True,
            raw=None,
            metadata={"bytes_written": bytes_written},
        )
        log_event(op="serial_write", alias=alias, command=command, bytes_written=bytes_written, success=True)
        return asdict(result)

    def serial_read_impl(
        self,
        alias: str,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "serial_read")
        if err:
            return err

        ser = session.raw
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        ser.timeout = effective_timeout

        try:
            # Wait for first byte (blocks up to timeout), then drain any remainder.
            first = ser.read(1)
            if not first:
                result = ReadResult(
                    success=True,
                    raw="",
                    format="text",
                    timed_out=True,
                )
                log_event(op="serial_read", alias=alias, bytes_read=0, success=True)
                return asdict(result)

            remainder = ser.read(ser.in_waiting) if ser.in_waiting else b""
            data = (first + remainder).decode("utf-8", errors="replace")
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"serial_read failed on '{alias}': {exc}",
                hint="Check device connection.",
            )
            log_event(op="serial_read", alias=alias, success=False, error=result.error)
            return asdict(result)

        result = ReadResult(
            success=True,
            raw=data,
            format="text",
            timed_out=False,
        )
        log_event(op="serial_read", alias=alias, bytes_read=len(data), success=True)
        return asdict(result)

    def serial_flush_impl(self, alias: str) -> dict:
        lookup = session_registry.lookup(alias, expected_type="serial")
        if not lookup.found:
            if lookup.wrong_type:
                result = Result(
                    success=False,
                    error=f"Alias '{alias}' has an open {lookup.actual_type} session, not a serial session.",
                )
            else:
                result = Result(
                    success=False,
                    error=f"No open session for '{alias}'.",
                    hint="Call connect(alias) first.",
                )
            log_event(op="serial_flush", alias=alias, success=False, error=result.error)
            return asdict(result)

        ser = lookup.session.raw
        try:
            ser.reset_input_buffer()
            ser.reset_output_buffer()
        except Exception as exc:
            result = Result(
                success=False,
                error=f"serial_flush failed on '{alias}': {exc}",
                hint="Check device connection.",
            )
            log_event(op="serial_flush", alias=alias, success=False, error=result.error)
            return asdict(result)

        log_event(op="serial_flush", alias=alias, success=True)
        return asdict(Result(success=True))

    # --- registration ---

    def register_tools(self, mcp) -> None:
        driver = self

        @mcp.tool()
        def serial_query(
            alias: str,
            command: str,
            timeout_ms: int | None = None,
        ) -> dict:
            """Send a command to a serial device and read the response.

            Appends the configured write_termination to command before sending, then
            reads bytes until read_termination is received or timeout expires.
            The session must already be open via connect(alias).

            This is the standard request/response pattern for instruments that send a
            single terminated line per command (e.g. most SCPI-over-serial devices,
            GPS receivers, PLCs). For devices that send multi-line or streaming
            responses, use serial_write + serial_read in a loop instead.

            Args:
                alias: Configured device alias (must be a serial-type alias).
                command: Command string to send. write_termination is appended automatically.
                timeout_ms: Per-call read timeout in milliseconds; defaults to the
                    config's timeout_ms. If the device does not send the read_termination
                    within this window, the call returns with timed_out=True and whatever
                    partial data arrived.

            Returns a ReadResult dict:
                raw: Response string (decoded UTF-8; non-UTF-8 bytes replaced with
                    the Unicode replacement character).
                format: "text".
                timed_out: True if read_termination was not received before timeout.
                metadata: {"bytes_written": int} — bytes sent to the device.
                success: False only on OS-level I/O errors, not on timeouts or
                    device-level NACK responses. Check raw content for device errors.
            """
            return driver.serial_query_impl(alias, command, timeout_ms)

        @mcp.tool()
        def serial_write(
            alias: str,
            command: str,
        ) -> dict:
            """Send a command to a serial device without reading a response.

            Appends the configured write_termination before sending. Use this for
            fire-and-forget commands (e.g. actuator commands, configuration writes)
            where no response is expected, or when you want to read the response
            separately via serial_read.

            Args:
                alias: Configured device alias (must be a serial-type alias).
                command: Command string to send. write_termination is appended automatically.

            Returns a ReadResult dict:
                raw: None (no read performed).
                metadata: {"bytes_written": int} — bytes actually sent to the device.
                success: False only on OS-level I/O errors.
            """
            return driver.serial_write_impl(alias, command)

        @mcp.tool()
        def serial_read(
            alias: str,
            timeout_ms: int | None = None,
        ) -> dict:
            """Read accumulated bytes from a serial device's OS buffer.

            Waits up to timeout_ms for data to arrive, then drains whatever
            is in the OS-level input buffer. This is request/response-style
            draining — there is no background thread; data is read on demand.

            Use serial_query for the common write + read pattern. Use this
            directly when you have already written a command via serial_write and
            want to read the response, or when the device sends unsolicited data.

            Args:
                alias: Configured device alias (must be a serial-type alias).
                timeout_ms: How long to wait for the first byte; defaults to
                    config's timeout_ms. timed_out=True is returned if no data
                    arrives within this window.

            Returns a ReadResult dict:
                raw: Decoded UTF-8 string of all bytes read, or "" on timeout.
                format: "text".
                timed_out: True if no data arrived before timeout expired.
                success: False only on OS-level I/O errors.
            """
            return driver.serial_read_impl(alias, timeout_ms)

        @mcp.tool()
        def serial_flush(alias: str) -> dict:
            """Clear the serial device's input and output buffers.

            Discards all bytes waiting in both the input buffer (data the device
            sent that has not been read yet) and the output buffer (data queued
            to send that has not been transmitted yet). Use this to recover from
            a desynchronized state — e.g., after a timeout mid-response — before
            sending the next command.

            Args:
                alias: Configured device alias (must be a serial-type alias).

            Returns a Result dict:
                success: True on success, False on OS-level error.
            """
            return driver.serial_flush_impl(alias)

    def register_cli_commands(self, cli_group) -> None:
        import sys

        import click

        from lablink.config import load_config
        from lablink.exceptions import ConfigError

        driver = self

        def _with_session(alias: str, op):
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
                if result.get("hint"):
                    click.echo(f"Hint: {result['hint']}", err=True)
                sys.exit(1)

        @cli_group.group(name="serial")
        def serial_group() -> None:
            """Serial device operations."""

        @serial_group.command(name="query")
        @click.argument("alias")
        @click.argument("command")
        def serial_query_cmd(alias: str, command: str) -> None:
            """Send COMMAND to the serial device at ALIAS and print the response."""
            result = _with_session(alias, lambda: driver.serial_query_impl(alias, command))
            _emit(result, lambda r: click.echo(r["raw"]))

        @serial_group.command(name="write")
        @click.argument("alias")
        @click.argument("command")
        def serial_write_cmd(alias: str, command: str) -> None:
            """Send COMMAND to the serial device at ALIAS (no response read)."""
            result = _with_session(alias, lambda: driver.serial_write_impl(alias, command))
            _emit(result, lambda r: click.echo(f"Wrote {r['metadata']['bytes_written']} bytes."))

    # --- system audit hooks ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        return [
            ("serial", importlib.util.find_spec("serial") is not None),
        ]
