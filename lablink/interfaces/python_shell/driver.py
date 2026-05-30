"""python_shell driver — persistent Python subprocess with JSONL wire protocol.

PythonShellDriver subclasses LabLinkDriver[PythonShellDriverConfig]. It has no
third-party Python deps (uses only stdlib), so check_python_deps() returns []
and its tools are always registered.

It ships two tools: python_shell_exec (run a code block) and
python_shell_eval (evaluate a single expression and return its repr).

Wire protocol: docs/ARCHITECTURE.md §12. The bootstrap script
(lablink/interfaces/python_shell/bootstrap.py) runs in the user's interpreter
subprocess. Requests and responses are newline-delimited JSON over stdin/stdout.
State (the namespace dict inside the bootstrap) persists for the lifetime of
the session.

Session.metadata keys:
    busy: bool          — True while a request is in-flight. Safe as a plain
                          bool under v1's single-threaded FastMCP dispatch.
                          See docs/ARCHITECTURE.md §10 for the async-dispatch
                          caveat if concurrent dispatch ever lands.
    req_counter: int    — monotonic counter used to generate request IDs.
"""

import json
import os
import subprocess
import sys
import threading
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
from lablink.interfaces.python_shell.config import PythonShellDriverConfig

_BOOTSTRAP_PATH = Path(__file__).parent / "bootstrap.py"
_CONNECT_TIMEOUT_SEC = 10.0


# ---------------------------------------------------------------------------
# Module-level helper — extracted for testability
# ---------------------------------------------------------------------------


def _read_line_with_timeout(proc: subprocess.Popen, timeout_sec: float) -> bytes | None:
    """Read one line from proc.stdout within timeout_sec.

    Returns the raw bytes line (including newline) on success, b"" if the
    process closed stdout (EOF), or None if the timeout elapsed before a line
    arrived (subprocess is still alive).
    """
    result: list[bytes | None] = [None]

    def _reader() -> None:
        try:
            result[0] = proc.stdout.readline()
        except Exception:
            result[0] = b""

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout_sec)
    if t.is_alive():
        return None  # timeout — subprocess still running
    return result[0]


# ---------------------------------------------------------------------------
# PythonShellDriver
# ---------------------------------------------------------------------------


class PythonShellDriver(LabLinkDriver[PythonShellDriverConfig]):
    """Persistent Python subprocess driver. No third-party deps required."""

    type_name = "python_shell"

    # --- lifecycle ---

    def connect(self, config: PythonShellDriverConfig) -> ConnectResult:
        if session_registry.is_registered(config.alias):
            err = f"Session already open for alias '{config.alias}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
                hint="Call disconnect(alias) first, or use the existing session.",
            )

        if not config.python_path:
            err = "Config field 'python_path' is empty."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
                hint="Set 'python_path' to the interpreter path (e.g. '~/venv/bin/python').",
            )

        interp = Path(config.python_path)
        if not interp.exists():
            err = f"Interpreter not found: {config.python_path}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
                hint="Check that 'python_path' points to a valid Python interpreter.",
            )

        cwd = str(Path(config.working_dir)) if config.working_dir else None
        try:
            proc = subprocess.Popen(
                [config.python_path, "-u", str(_BOOTSTRAP_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                bufsize=0,
            )
        except Exception as exc:
            err = f"Failed to spawn interpreter '{config.python_path}': {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
                hint="Check that 'python_path' is executable.",
            )

        # Wait for READY handshake.
        raw_line = _read_line_with_timeout(proc, _CONNECT_TIMEOUT_SEC)

        if raw_line is None:
            proc.kill()
            proc.wait()
            err = f"Interpreter did not send READY within {_CONNECT_TIMEOUT_SEC}s."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
                hint="The interpreter may be slow to start or the bootstrap script may have an error.",
            )

        if not raw_line:
            stderr_out = proc.stderr.read().decode("utf-8", errors="replace")
            proc.wait()
            err = (
                "Interpreter exited before sending READY."
                + (f" stderr: {stderr_out.strip()}" if stderr_out.strip() else "")
            )
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
                hint="Check that 'python_path' points to a working Python interpreter.",
            )

        try:
            handshake = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            proc.kill()
            proc.wait()
            err = f"Invalid READY handshake from interpreter: {exc}. Line: {raw_line!r}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
            )

        if handshake.get("op") != "ready":
            proc.kill()
            proc.wait()
            err = f"Expected 'ready' handshake op, got '{handshake.get('op')}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="python_shell",
                error=err,
            )

        python_version = handshake.get("python_version", "unknown")
        interpreter = handshake.get("interpreter", config.python_path)
        identity = f"Python {python_version} @ {interpreter}"

        session = Session(
            alias=config.alias,
            interface_type="python_shell",
            raw=proc,
            config=config,
            metadata={"busy": False, "req_counter": 0},
        )
        session_registry.register(session)
        log_event(op="connect", alias=config.alias, identity=identity, success=True)
        return ConnectResult(
            success=True,
            alias=config.alias,
            interface_type="python_shell",
            identity=identity,
        )

    def disconnect(self, session: Session[PythonShellDriverConfig]) -> Result:
        proc: subprocess.Popen = session.raw
        session.metadata["busy"] = False  # always clear before teardown

        if proc.poll() is None:
            try:
                shutdown_frame = json.dumps({"id": "shutdown", "op": "shutdown"}) + "\n"
                proc.stdin.write(shutdown_frame.encode("utf-8"))
                proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass  # process may have already exited

            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        log_event(op="disconnect", alias=session.alias, success=True)
        return Result(success=True)

    def diagnose(self, config: PythonShellDriverConfig) -> DiagnosticResult:
        """Stateless per-alias diagnosis: path checks and spawn-and-handshake test."""
        checks: dict[str, Any] = {}
        action_items: list[str] = []

        if not config.python_path:
            checks["python_path"] = {"status": "missing", "detail": ""}
            action_items.append(
                "Config field 'python_path' is empty. Set it to the interpreter path "
                "(e.g. '~/miniconda3/envs/labwork/bin/python')."
            )
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type="python_shell",
                checks=checks,
                action_items=action_items,
            )

        checks["python_path"] = {"status": "ok", "detail": config.python_path}

        interp = Path(config.python_path)
        if not interp.exists():
            checks["interpreter_exists"] = {"status": "not_found", "detail": config.python_path}
            action_items.append(
                f"Interpreter not found at '{config.python_path}'. "
                "Check that the path is correct and the environment exists."
            )
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type="python_shell",
                checks=checks,
                action_items=action_items,
            )

        checks["interpreter_exists"] = {"status": "ok", "detail": config.python_path}

        if sys.platform != "win32" and not os.access(str(interp), os.X_OK):
            checks["interpreter_executable"] = {
                "status": "not_executable",
                "detail": config.python_path,
            }
            action_items.append(
                f"Interpreter at '{config.python_path}' is not executable. "
                f"Run: chmod +x {config.python_path}"
            )
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type="python_shell",
                checks=checks,
                action_items=action_items,
            )

        checks["interpreter_executable"] = {"status": "ok", "detail": config.python_path}

        # Spawn-and-handshake test.
        try:
            proc = subprocess.Popen(
                [config.python_path, "-u", str(_BOOTSTRAP_PATH)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as exc:
            checks["spawn"] = {"status": "error", "detail": str(exc)}
            action_items.append(f"Failed to spawn interpreter: {exc}")
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type="python_shell",
                checks=checks,
                action_items=action_items,
            )

        raw_line = _read_line_with_timeout(proc, _CONNECT_TIMEOUT_SEC)
        proc.kill()
        proc.wait()

        if raw_line is None:
            checks["handshake"] = {
                "status": "timeout",
                "detail": f"no READY within {_CONNECT_TIMEOUT_SEC}s",
            }
            action_items.append(
                "Interpreter did not send READY — may be slow to start or "
                "the bootstrap script has an error."
            )
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type="python_shell",
                checks=checks,
                action_items=action_items,
            )

        try:
            handshake = json.loads(raw_line.decode("utf-8", errors="replace"))
            python_version = handshake.get("python_version", "unknown")
            checks["handshake"] = {"status": "ok", "detail": f"Python {python_version}"}
        except Exception as exc:
            checks["handshake"] = {"status": "error", "detail": str(exc)}
            action_items.append(f"Invalid READY handshake: {exc}")
            return DiagnosticResult(
                ready=False,
                alias=config.alias,
                interface_type="python_shell",
                checks=checks,
                action_items=action_items,
            )

        return DiagnosticResult(
            ready=True,
            alias=config.alias,
            interface_type="python_shell",
            checks=checks,
            action_items=[],
        )

    # --- operation logic (shared by MCP tools and CLI) ---

    def _get_session(self, alias: str, op: str) -> tuple[Session | None, dict | None]:
        """Look up the session; return (session, None) or (None, error_dict)."""
        lkp = session_registry.lookup(alias, expected_type="python_shell")
        if not lkp.found:
            if lkp.wrong_type:
                result = ReadResult(
                    success=False,
                    error=(
                        f"Alias '{alias}' has an open {lkp.actual_type} session, "
                        "not a python_shell session."
                    ),
                    hint=(
                        f"Use {lkp.actual_type}_* tools for this alias, or disconnect "
                        "and reconfigure with type='python_shell'."
                    ),
                )
            else:
                result = ReadResult(
                    success=False,
                    error=f"No open session for '{alias}'.",
                    hint="Call connect(alias) first.",
                )
            log_event(op=op, alias=alias, success=False, error=result.error)
            return None, asdict(result)
        return lkp.session, None

    def _send_and_receive(
        self,
        session: Session[PythonShellDriverConfig],
        tool_op: str,
        wire_op: str,
        wire_kwargs: dict,
        timeout_ms: int | None,
    ) -> dict:
        """Send a wire-protocol request and wait for the response.

        Handles the four failure modes from docs/ARCHITECTURE.md §12:
        busy check, subprocess crash on write, timeout (timed_out=True, busy
        stays True), and clean response.
        """
        effective_timeout_sec = (timeout_ms or session.config.timeout_ms) / 1000

        if session.metadata["busy"]:
            result = ReadResult(
                success=False,
                error="Session is busy executing a previous call.",
                hint="Wait or call disconnect() to force termination.",
            )
            log_event(op=tool_op, alias=session.alias, success=False, error=result.error)
            return asdict(result)

        proc: subprocess.Popen = session.raw
        if proc.poll() is not None:
            result = ReadResult(
                success=False,
                error="python_shell subprocess has exited.",
                hint="Call disconnect() and connect() to restart the interpreter.",
            )
            log_event(op=tool_op, alias=session.alias, success=False, error=result.error)
            return asdict(result)

        counter = session.metadata["req_counter"] + 1
        session.metadata["req_counter"] = counter
        req_id = f"req-{counter}"

        request: dict = {"id": req_id, "op": wire_op}
        request.update(wire_kwargs)

        session.metadata["busy"] = True
        try:
            frame = json.dumps(request) + "\n"
            proc.stdin.write(frame.encode("utf-8"))
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            session.metadata["busy"] = False
            result = ReadResult(
                success=False,
                error=f"python_shell subprocess exited unexpectedly: {exc}",
                hint=(
                    "Call disconnect() and connect() to restart the interpreter. "
                    "Any session state has been lost."
                ),
            )
            log_event(op=tool_op, alias=session.alias, success=False, error=result.error)
            return asdict(result)

        raw_line = _read_line_with_timeout(proc, effective_timeout_sec)

        if raw_line is None:
            # Timeout — subprocess is still running; busy stays True.
            result = ReadResult(
                success=True,
                timed_out=True,
                raw=None,
                hint=(
                    "The previous call is still running. Subsequent calls will fail "
                    "with 'busy' until it completes or the session is disconnected."
                ),
            )
            log_event(op=tool_op, alias=session.alias, success=True)
            return asdict(result)

        session.metadata["busy"] = False

        if not raw_line:
            # EOF — subprocess exited unexpectedly.
            result = ReadResult(
                success=False,
                error="python_shell subprocess exited unexpectedly.",
                hint=(
                    "Call disconnect() and connect() to restart the interpreter. "
                    "Any session state has been lost."
                ),
            )
            log_event(op=tool_op, alias=session.alias, success=False, error=result.error)
            return asdict(result)

        try:
            response = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            result = ReadResult(
                success=False,
                error=f"Invalid response from python_shell subprocess: {exc}",
            )
            log_event(op=tool_op, alias=session.alias, success=False, error=result.error)
            return asdict(result)

        stdout = response.get("stdout", "")
        stderr = response.get("stderr", "")
        result_val = response.get("result")
        exc_info = response.get("exception")
        duration_ms_val = response.get("duration_ms", 0)

        metadata: dict = {
            "stdout": stdout,
            "stderr": stderr,
            "result": result_val,
            "duration_ms": duration_ms_val,
        }
        if response.get("truncated"):
            metadata["truncated"] = True
            metadata["truncated_bytes"] = response.get("truncated_bytes", 0)

        if exc_info is not None:
            # User code raised an exception. Tool succeeded; code failed.
            # raw = full traceback (with any captured stdout prepended).
            metadata["exception"] = exc_info
            tb = exc_info.get("traceback", exc_info.get("message", ""))
            raw = (stdout.rstrip("\n") + "\n" + tb) if stdout else tb
        elif result_val is not None:
            # eval: result repr is the primary output.
            raw = (stdout.rstrip("\n") + "\n" + result_val) if stdout else result_val
        else:
            # exec: stdout is the primary output (may be empty).
            raw = stdout

        log_event(
            op=tool_op,
            alias=session.alias,
            stdout_bytes=len(stdout),
            stderr_bytes=len(stderr),
            success=True,
        )
        return asdict(ReadResult(success=True, raw=raw, format="text", metadata=metadata))

    def python_shell_exec_impl(
        self,
        alias: str,
        code: str,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "python_shell_exec")
        if err:
            return err
        return self._send_and_receive(session, "python_shell_exec", "exec", {"code": code}, timeout_ms)

    def python_shell_eval_impl(
        self,
        alias: str,
        expression: str,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "python_shell_eval")
        if err:
            return err
        return self._send_and_receive(
            session, "python_shell_eval", "eval", {"expression": expression}, timeout_ms
        )

    # --- registration ---

    def register_tools(self, mcp) -> None:
        driver = self

        @mcp.tool()
        def python_shell_exec(
            alias: str,
            code: str,
            timeout_ms: int | None = None,
        ) -> dict:
            """Execute a Python code block in a persistent interpreter subprocess.

            The session must already be open via connect(alias). State (variables,
            imports, open handles) persists across calls within the same session —
            this is what makes python_shell useful for vendor SDKs: import and
            initialise a library in one call, then use it in subsequent calls.

            User code that raises an exception returns success=True with the
            traceback in raw and metadata["exception"]. success=False means the
            wire protocol itself failed (subprocess crashed, timeout was hit on a
            previous call that left busy=True, etc.).

            Args:
                alias: Configured device alias (must be a python_shell-type alias).
                code: Python source to execute. Multi-line strings are supported.
                    write_termination is NOT appended — send raw Python.
                timeout_ms: Per-call timeout in milliseconds; defaults to
                    config's timeout_ms. On timeout, returns timed_out=True and
                    busy stays True — subsequent calls will fail until disconnect.

            Returns a ReadResult dict:
                raw: Captured stdout, or the traceback if an exception occurred.
                format: "text".
                timed_out: True if timeout elapsed before the subprocess responded.
                metadata: {
                    "stdout": str,        captured stdout
                    "stderr": str,        captured stderr
                    "result": null,       always null for exec (no return value)
                    "duration_ms": int,   wall time inside the subprocess
                    "exception": {        present only when user code raised
                        "type": str,
                        "message": str,
                        "traceback": str
                    }
                }
            """
            return driver.python_shell_exec_impl(alias, code, timeout_ms)

        @mcp.tool()
        def python_shell_eval(
            alias: str,
            expression: str,
            timeout_ms: int | None = None,
        ) -> dict:
            """Evaluate a Python expression in a persistent interpreter subprocess.

            Like python_shell_exec but for single expressions. Returns repr() of
            the expression's value in raw and metadata["result"]. Useful for
            inspecting state or reading instrument values in one line.

            Args:
                alias: Configured device alias (must be a python_shell-type alias).
                expression: A single Python expression to evaluate. Must be
                    evaluable — statements (assignment, import, for loops) will
                    raise SyntaxError. Use python_shell_exec for those.
                timeout_ms: Per-call timeout in milliseconds; defaults to
                    config's timeout_ms.

            Returns a ReadResult dict:
                raw: repr(value) of the evaluated expression, or the traceback
                    if an exception occurred. Stdout captured during evaluation
                    is prepended when non-empty.
                format: "text".
                metadata: {
                    "stdout": str,        captured stdout (rare for eval)
                    "stderr": str,        captured stderr
                    "result": str | null, repr(value) or null on exception
                    "duration_ms": int,
                    "exception": {...}    present only on exception
                }
            """
            return driver.python_shell_eval_impl(alias, expression, timeout_ms)

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
                if conn.hint:
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

        @cli_group.group(name="python-shell")
        def python_shell_group() -> None:
            """Python interpreter subprocess operations."""

        @python_shell_group.command(name="exec")
        @click.argument("alias")
        @click.argument("code")
        def python_shell_exec_cmd(alias: str, code: str) -> None:
            """Execute CODE in the Python interpreter at ALIAS and print stdout."""
            result = _with_session(alias, lambda: driver.python_shell_exec_impl(alias, code))
            _emit(result, lambda r: click.echo(r["raw"]))

        @python_shell_group.command(name="eval")
        @click.argument("alias")
        @click.argument("expression")
        def python_shell_eval_cmd(alias: str, expression: str) -> None:
            """Evaluate EXPRESSION in the Python interpreter at ALIAS and print the result."""
            result = _with_session(alias, lambda: driver.python_shell_eval_impl(alias, expression))
            _emit(result, lambda r: click.echo(r["raw"]))

    # --- system audit hooks ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        # python_shell uses only stdlib — no optional Python deps to check.
        return []
