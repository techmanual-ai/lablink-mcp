"""SSH driver — Paramiko-based remote command execution.

SshDriver subclasses LabLinkDriver[SshDriverConfig]. All paramiko imports are
lazy — they happen inside methods, never at module load — so the package
imports cleanly without the [ssh] extra installed.

Phase 1 ships two tools: ssh_exec (non-interactive exec channel) and
ssh_shell_session (per-call interactive PTY). Streaming (ssh_start_stream /
ssh_stop_stream / ssh_read_stream) is deferred to Phase 1.5.
"""

import importlib.util
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path
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
from lablink.event_logger import log_event
from lablink.interfaces.ssh.config import SshDriverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    """Return True if TCP port is accepting connections on host."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _drain_channel(chan: Any, quiet_timeout_s: float) -> str:
    """Read from a paramiko channel until no data arrives for quiet_timeout_s.

    Resets the deadline each time a chunk arrives so fast-producing commands
    are drained fully before returning.
    """
    buf = ""
    deadline = time.monotonic() + quiet_timeout_s
    while time.monotonic() < deadline:
        try:
            if chan.recv_ready():
                chunk = chan.recv(4096)
                if not chunk:
                    break
                buf += chunk.decode("utf-8", errors="replace")
                deadline = time.monotonic() + quiet_timeout_s
            else:
                time.sleep(0.02)
        except Exception:
            break
    return buf


class SshDriver(LabLinkDriver[SshDriverConfig]):
    """SSH driver using Paramiko."""

    type_name = "ssh"

    # --- lifecycle ---

    def connect(self, config: SshDriverConfig) -> ConnectResult:
        try:
            import paramiko
        except ImportError:
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error="Missing dependency: paramiko",
                hint="Run: pip install lablink-mcp[ssh]",
            )

        if session_registry.is_registered(config.alias):
            err = f"Session already open for alias '{config.alias}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error=err,
                hint="Call disconnect(alias) first, or use the existing session.",
            )

        if not config.host:
            err = "Config field 'host' is empty."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error=err,
                hint="Add a 'host' field (hostname or IP) to the config.",
            )

        if not config.username:
            err = "Config field 'username' is empty."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error=err,
                hint="Add a 'username' field to the config.",
            )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs: dict = {
            "hostname": config.host,
            "port": config.port,
            "username": config.username,
            "timeout": config.timeout_ms / 1000,
        }

        if config.auth_type == "ssh_key":
            if config.auth_ssh_key_path:
                connect_kwargs["key_filename"] = config.auth_ssh_key_path
            if config.auth_ssh_passphrase_env:
                passphrase = os.environ.get(config.auth_ssh_passphrase_env)
                if passphrase:
                    connect_kwargs["passphrase"] = passphrase
        elif config.auth_type in ("ssh_password", "basic"):
            if config.auth_password_env:
                password = os.environ.get(config.auth_password_env)
                if password:
                    connect_kwargs["password"] = password
        # auth_type "none": rely on SSH agent / default key files (paramiko default)

        try:
            client.connect(**connect_kwargs)
        except paramiko.AuthenticationException as exc:
            err = f"SSH authentication failed: {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error=err,
                hint=(
                    "Check auth_type, key path, or password env var. "
                    "Ensure the host has your public key in authorized_keys."
                ),
            )
        except paramiko.SSHException as exc:
            err = f"SSH error: {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error=err,
                hint="Check that the SSH service is running on the host and that the port is correct.",
            )
        except OSError as exc:
            err = f"Connection failed: {exc}"
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="ssh",
                error=err,
                hint=(
                    f"Cannot reach {config.host}:{config.port}. "
                    "Check host, port, and network connectivity."
                ),
            )

        transport = client.get_transport()
        identity = transport.remote_version if transport else f"SSH {config.host}"

        session = Session(
            alias=config.alias,
            interface_type="ssh",
            raw=client,
            config=config,
        )
        session_registry.register(session)
        log_event(op="connect", alias=config.alias, identity=identity, success=True)
        return ConnectResult(
            success=True,
            alias=config.alias,
            interface_type="ssh",
            identity=identity,
        )

    def disconnect(self, session: Session[SshDriverConfig]) -> Result:
        try:
            session.raw.close()
        except Exception as exc:
            log_event(op="disconnect", alias=session.alias, success=False, error=str(exc))
            return Result(
                success=False,
                error=f"Error closing SSH session: {exc}",
                hint="Session may already be closed. The alias is deregistered regardless.",
            )
        log_event(op="disconnect", alias=session.alias, success=True)
        return Result(success=True)

    def diagnose(self, config: SshDriverConfig) -> DiagnosticResult:
        """Stateless per-alias diagnosis: TCP reachability, key file presence, auth config."""
        checks: dict[str, Any] = {}
        action_items: list[str] = []

        tcp_ok = _port_open(config.host, config.port) if config.host else False
        checks["tcp_port"] = {
            "status": "ok" if tcp_ok else "closed",
            "detail": f"{config.host}:{config.port}",
        }
        if not config.host:
            action_items.append("Config field 'host' is empty.")
        elif not tcp_ok:
            action_items.append(
                f"Cannot reach {config.host}:{config.port}. "
                "Check that the SSH daemon is running, the host is reachable, and the port is not firewalled."
            )

        valid_auth = {"none", "ssh_key", "ssh_password", "basic"}
        if config.auth_type not in valid_auth:
            checks["auth_type"] = {"status": "invalid", "detail": config.auth_type}
            action_items.append(
                f"Unknown auth_type '{config.auth_type}'. Valid values: {sorted(valid_auth)}."
            )
        else:
            checks["auth_type"] = {"status": "ok", "detail": config.auth_type}

        if config.auth_type == "ssh_key" and config.auth_ssh_key_path:
            key_path = Path(config.auth_ssh_key_path)
            key_ok = key_path.exists()
            checks["ssh_key_file"] = {
                "status": "ok" if key_ok else "missing",
                "detail": str(key_path),
            }
            if not key_ok:
                action_items.append(
                    f"SSH key file not found: {key_path}. "
                    "Update auth_ssh_key_path in the config."
                )

        return DiagnosticResult(
            ready=len(action_items) == 0,
            alias=config.alias,
            interface_type="ssh",
            checks=checks,
            action_items=action_items,
        )

    # --- operation logic (shared by MCP tools and CLI) ---

    def ssh_exec_impl(
        self, alias: str, command: str, timeout_ms: Optional[int] = None
    ) -> dict:
        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            log_event(op="ssh_exec", alias=alias, command=command, success=False, error=result.error)
            return asdict(result)

        session = lookup.session
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000

        try:
            _stdin, stdout, stderr = session.raw.exec_command(command, timeout=effective_timeout)
            exit_code = stdout.channel.recv_exit_status()
            out = stdout.read().decode("utf-8", errors="replace")
            err_text = stderr.read().decode("utf-8", errors="replace")
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"SSH exec error: {exc}",
                hint="Check that the command is valid and the session is still open. Increase timeout_ms for long-running commands.",
            )
            log_event(op="ssh_exec", alias=alias, command=command, success=False, error=result.error)
            return asdict(result)

        result = ReadResult(
            success=True,
            raw=out,
            format="text",
            metadata={"exit_code": exit_code, "stderr": err_text},
        )
        log_event(op="ssh_exec", alias=alias, command=command, exit_code=exit_code, success=True)
        return asdict(result)

    def ssh_shell_session_impl(
        self, alias: str, commands: list[str], timeout_ms: Optional[int] = None
    ) -> dict:
        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            log_event(op="ssh_shell_session", alias=alias, success=False, error=result.error)
            return asdict(result)

        session = lookup.session
        effective_timeout_s = (timeout_ms or session.config.timeout_ms) / 1000
        # Inter-command drain window: at most 2 s, at least 0.5 s
        inter_cmd_s = max(0.5, min(2.0, effective_timeout_s / max(len(commands), 1)))

        try:
            chan = session.raw.invoke_shell()
            chan.settimeout(0.5)

            transcript = _drain_channel(chan, inter_cmd_s)  # consume initial prompt

            for cmd in commands:
                chan.send(cmd + "\n")
                transcript += _drain_channel(chan, inter_cmd_s)

            chan.send("exit\n")
            transcript += _drain_channel(chan, 1.0)
            chan.close()
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"SSH shell error: {exc}",
                hint="Check that the session is still open and the host allows interactive shells.",
            )
            log_event(op="ssh_shell_session", alias=alias, success=False, error=result.error)
            return asdict(result)

        result = ReadResult(success=True, raw=transcript, format="text")
        log_event(op="ssh_shell_session", alias=alias, cmd_count=len(commands), success=True)
        return asdict(result)

    @staticmethod
    def _no_session_result(
        alias: str, lookup: "session_registry.SessionLookup"
    ) -> ReadResult:
        if lookup.wrong_type:
            return ReadResult(
                success=False,
                error=f"Alias '{alias}' has an open {lookup.actual_type} session, not an SSH session.",
                hint=f"Use a {lookup.actual_type}_* tool for this alias, or disconnect and reconfigure with type='ssh'.",
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
        def ssh_exec(alias: str, command: str, timeout_ms: int | None = None) -> dict:
            """Run a command on an SSH host and return its output.

            Executes command in a non-interactive exec channel (not a PTY),
            waits for the process to exit, and returns stdout. Use for single
            commands with defined exit behavior. The session must already be
            open via connect(alias).

            Args:
                alias: Configured device alias (must be an SSH-type alias).
                command: Shell command to execute, e.g. "uname -a" or "ls /data".
                timeout_ms: Per-call timeout in milliseconds; defaults to the
                    config's timeout_ms. Applies to reading stdout/stderr —
                    increase it for long-running commands.

            Returns a ReadResult dict:
                raw: stdout as a UTF-8 string.
                metadata: {"exit_code": int, "stderr": str}. exit_code is
                    load-bearing — a non-zero value means the command failed
                    even when success is True. Check it explicitly for scripts.
                success: False only on transport errors. A command returning
                    exit_code=1 still yields success=True with the non-zero
                    exit code in metadata.
            """
            return driver.ssh_exec_impl(alias, command, timeout_ms)

        @mcp.tool()
        def ssh_shell_session(
            alias: str, commands: list[str], timeout_ms: int | None = None
        ) -> dict:
            """Run a scripted sequence of commands on an interactive PTY.

            Opens a fresh PTY shell for each call (no persistent shell state
            between calls in Phase 1). Sends each command in order and collects
            the full terminal transcript, including prompts and command echo.
            Use when commands depend on interactive shell state (env vars set
            by prior commands, directory context). Prefer ssh_exec for
            independent one-shot commands — it is faster and gives a clean
            exit code.

            Args:
                alias: Configured device alias (must be an SSH-type alias).
                commands: Ordered list of shell commands, e.g.
                    ["cd /data", "ls -la", "cat results.txt"].
                timeout_ms: Per-call timeout in milliseconds; defaults to the
                    config's timeout_ms. Applied as the inter-command drain
                    window — increase if commands take longer than ~2 seconds
                    to produce output.

            Returns a ReadResult dict:
                raw: Full terminal transcript as a UTF-8 string, including
                    prompts and command echo. PTY output is noisy — parse
                    cautiously.
                success: False on transport or PTY errors only. A command that
                    fails inside the shell does not set success=False; inspect
                    the transcript for error indicators.
            """
            return driver.ssh_shell_session_impl(alias, commands, timeout_ms)

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

        @cli_group.group(name="ssh")
        def ssh_group() -> None:
            """SSH operations."""

        @ssh_group.command(name="exec")
        @click.argument("alias")
        @click.argument("command")
        def ssh_exec_cmd(alias: str, command: str) -> None:
            """Execute COMMAND on the SSH host at ALIAS and print stdout."""
            result = _with_session(alias, lambda: driver.ssh_exec_impl(alias, command))
            _emit(result, lambda r: click.echo(r["raw"]))

    # --- system audit hooks ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        return [
            ("paramiko", importlib.util.find_spec("paramiko") is not None),
        ]
