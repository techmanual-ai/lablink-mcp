"""SSH driver — Paramiko-based remote command execution and streaming.

SshDriver subclasses LabLinkDriver[SshDriverConfig]. All paramiko imports are
lazy — they happen inside methods, never at module load — so the package
imports cleanly without the [ssh] extra installed.

Tools: ssh_exec and ssh_shell_session for command execution; ssh_start_stream,
ssh_read_stream, ssh_stop_stream for buffering long-running command output.
"""

import importlib.util
import os
import socket
import time
from dataclasses import asdict
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
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
from lablink.redaction import contains_secret, secret_values


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SECURITY_WARNING_KEY = "security_warning"
_SECURITY_WARNING = (
    "A configured credential value was detected in the command and has been "
    "redacted from logs. Never inline secrets in commands — use key-based auth, "
    "an askpass helper, or passwordless sudo."
)


def _alias_secrets(alias: str) -> set[str]:
    """Best-effort set of secret values an alias could expose.

    Prefers the live session's config; falls back to loading the on-disk config
    so the no-open-session error path still redacts. Never raises.
    """
    session = session_registry.get_any(alias)
    if session is not None:
        return secret_values(session.config)
    try:
        from lablink.config import load_config

        return secret_values(load_config(alias))
    except Exception:
        return set()


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


def _stream_worker(channel: Any, buffer: Queue, metadata: dict) -> None:
    """Background thread: read chunks from an SSH exec channel into a bounded queue.

    Puts decoded string chunks into buffer. On EOF or error, puts a None
    sentinel so readers know the stream has ended. Overflow policy: drop the
    oldest chunk before inserting the new one (bounded at maxsize=1000).
    """
    try:
        while True:
            if channel.exit_status_ready() and not channel.recv_ready():
                break
            if channel.recv_ready():
                chunk = channel.recv(4096)
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                if buffer.full():
                    try:
                        buffer.get_nowait()
                    except Empty:
                        pass
                buffer.put_nowait(text)
            else:
                time.sleep(0.05)
    except Exception as exc:
        metadata["stream_error"] = str(exc)
    finally:
        try:
            buffer.put_nowait(None)  # EOF sentinel
        except Exception:
            pass


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

        # Surface the resolved peer address (ground truth from the socket) so
        # downstream config — e.g. a REST base_url for a service on this same
        # host — can use the real IP instead of a guessed one. Never let a
        # metadata extra break connect.
        metadata: dict = {}
        try:
            peer = transport.getpeername() if transport else None
            if peer:
                metadata["peer_address"] = f"{peer[0]}:{peer[1]}"
        except Exception:
            pass

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
            metadata=metadata,
        )

    def disconnect(self, session: Session[SshDriverConfig]) -> Result:
        # Tear down any active stream before closing the SSH connection.
        if session.buffer_thread is not None:
            channel = session.metadata.get("stream_channel")
            if channel is not None:
                try:
                    channel.close()
                except Exception:
                    pass
            session.buffer_thread.join(timeout=2.0)
            session.buffer_thread = None
            session.buffer = None

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
        # The command is logged verbatim except for configured credentials, which
        # log_event scrubs given `secrets`; warn the agent if one was inlined.
        secrets = _alias_secrets(alias)
        secret_found = contains_secret(command, secrets)

        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            if secret_found:
                result.metadata[_SECURITY_WARNING_KEY] = _SECURITY_WARNING
            log_event(op="ssh_exec", alias=alias, command=command, success=False, error=result.error, secrets=secrets)
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
            if secret_found:
                result.metadata[_SECURITY_WARNING_KEY] = _SECURITY_WARNING
            log_event(op="ssh_exec", alias=alias, command=command, success=False, error=result.error, secrets=secrets)
            return asdict(result)

        metadata = {"exit_code": exit_code, "stderr": err_text}
        if secret_found:
            metadata[_SECURITY_WARNING_KEY] = _SECURITY_WARNING
        result = ReadResult(
            success=True,
            raw=out,
            format="text",
            metadata=metadata,
        )
        log_event(op="ssh_exec", alias=alias, command=command, exit_code=exit_code, success=True, secrets=secrets)
        return asdict(result)

    def ssh_shell_session_impl(
        self, alias: str, commands: list[str], timeout_ms: Optional[int] = None
    ) -> dict:
        # Resolve secrets before the lookup so the no-session path can still warn
        # the agent that it inlined a credential. This tool logs cmd_count, not
        # command text, but errors and the warning still depend on detection.
        secrets = _alias_secrets(alias)
        secret_found = contains_secret("\n".join(commands), secrets)

        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            if secret_found:
                result.metadata[_SECURITY_WARNING_KEY] = _SECURITY_WARNING
            log_event(op="ssh_shell_session", alias=alias, success=False, error=result.error, secrets=secrets)
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
            if secret_found:
                result.metadata[_SECURITY_WARNING_KEY] = _SECURITY_WARNING
            log_event(op="ssh_shell_session", alias=alias, success=False, error=result.error, secrets=secrets)
            return asdict(result)

        meta = {_SECURITY_WARNING_KEY: _SECURITY_WARNING} if secret_found else {}
        result = ReadResult(success=True, raw=transcript, format="text", metadata=meta)
        log_event(op="ssh_shell_session", alias=alias, cmd_count=len(commands), success=True, secrets=secrets)
        return asdict(result)

    def ssh_start_stream_impl(self, alias: str, command: str) -> dict:
        secrets = _alias_secrets(alias)
        secret_found = contains_secret(command, secrets)

        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            if secret_found:
                result.metadata[_SECURITY_WARNING_KEY] = _SECURITY_WARNING
            log_event(op="ssh_start_stream", alias=alias, command=command, success=False, error=result.error, secrets=secrets)
            return asdict(result)

        session = lookup.session

        if session.buffer_thread is not None and session.buffer_thread.is_alive():
            result = ReadResult(
                success=False,
                error=f"A stream is already active for '{alias}'.",
                hint="Call ssh_stop_stream(alias) to terminate it before starting a new one.",
            )
            log_event(op="ssh_start_stream", alias=alias, command=command, success=False, error=result.error, secrets=secrets)
            return asdict(result)

        try:
            _stdin, stdout, _stderr = session.raw.exec_command(command)
            channel = stdout.channel
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"Failed to start stream: {exc}",
                hint="Check that the session is still open and the command is valid.",
            )
            log_event(op="ssh_start_stream", alias=alias, command=command, success=False, error=result.error, secrets=secrets)
            return asdict(result)

        buf: Queue = Queue(maxsize=1000)
        session.buffer = buf
        session.metadata["stream_channel"] = channel
        session.metadata.pop("stream_error", None)

        thread = Thread(
            target=_stream_worker,
            args=(channel, buf, session.metadata),
            daemon=True,
            name=f"lablink-ssh-stream-{alias}",
        )
        session.buffer_thread = thread
        thread.start()

        meta = {_SECURITY_WARNING_KEY: _SECURITY_WARNING} if secret_found else {}
        log_event(op="ssh_start_stream", alias=alias, command=command, success=True, secrets=secrets)
        return asdict(Result(success=True, metadata=meta))

    def ssh_read_stream_impl(self, alias: str, timeout_ms: Optional[int] = None) -> dict:
        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            log_event(op="ssh_read_stream", alias=alias, success=False, error=result.error)
            return asdict(result)

        session = lookup.session

        # §6.5 rule 4: check None before is_alive to avoid AttributeError
        if session.buffer_thread is None:
            result = ReadResult(
                success=False,
                error=f"No active stream for '{alias}'.",
                hint="Call ssh_start_stream(alias, command) first.",
            )
            log_event(op="ssh_read_stream", alias=alias, success=False, error=result.error)
            return asdict(result)

        if not session.buffer_thread.is_alive() and "stream_error" in session.metadata:
            err = session.metadata["stream_error"]
            result = ReadResult(
                success=False,
                error=f"Stream thread died: {err}",
                hint="Call disconnect() and reconnect to restart the session.",
            )
            log_event(op="ssh_read_stream", alias=alias, success=False, error=result.error)
            return asdict(result)

        # Drain all available chunks without blocking
        chunks: list[str] = []
        stream_ended = False
        while True:
            try:
                item = session.buffer.get_nowait()
                if item is None:
                    stream_ended = True
                    break
                chunks.append(item)
            except Empty:
                break

        combined = "".join(chunks)
        if not combined and not stream_ended:
            result = ReadResult(success=True, raw=None, timed_out=True, format="text")
            log_event(op="ssh_read_stream", alias=alias, bytes_read=0, success=True)
            return asdict(result)

        result = ReadResult(
            success=True,
            raw=combined,
            format="text",
            metadata={"stream_ended": stream_ended},
        )
        log_event(op="ssh_read_stream", alias=alias, bytes_read=len(combined), success=True)
        return asdict(result)

    def ssh_stop_stream_impl(self, alias: str) -> dict:
        lookup = session_registry.lookup(alias, expected_type="ssh")
        if not lookup.found:
            result = self._no_session_result(alias, lookup)
            log_event(op="ssh_stop_stream", alias=alias, success=False, error=result.error)
            return asdict(result)

        session = lookup.session

        if session.buffer_thread is None:
            result = ReadResult(
                success=False,
                error=f"No active stream for '{alias}'.",
                hint="Call ssh_start_stream(alias, command) first.",
            )
            log_event(op="ssh_stop_stream", alias=alias, success=False, error=result.error)
            return asdict(result)

        # Close channel to signal the worker thread to exit
        channel = session.metadata.get("stream_channel")
        if channel is not None:
            try:
                channel.close()
            except Exception:
                pass

        # Join with 2s timeout per §6.5 rule 3
        session.buffer_thread.join(timeout=2.0)
        thread_clean = not session.buffer_thread.is_alive()

        # Drain any remaining buffered output
        chunks: list[str] = []
        while True:
            try:
                item = session.buffer.get_nowait()
                if item is None:
                    break
                chunks.append(item)
            except Empty:
                break

        final_transcript = "".join(chunks)

        # Reset streaming state so a new stream can start
        session.buffer_thread = None
        session.buffer = None
        session.metadata.pop("stream_channel", None)
        session.metadata.pop("stream_error", None)

        meta: dict = {}
        if not thread_clean:
            meta["warning"] = "Stream thread did not exit cleanly within 2s."

        result = ReadResult(success=True, raw=final_transcript, format="text", metadata=meta)
        log_event(op="ssh_stop_stream", alias=alias, bytes_read=len(final_transcript), success=True)
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

            Security: the command string is written to the event log and lands
            in the remote's shell history and process list. NEVER inline a
            credential (no `echo $PASS | sudo -S ...`); for privileged work use
            key-based auth, an askpass helper, or passwordless sudo. Configured
            credential values are scrubbed from the log and trigger a
            metadata.security_warning, but that scrubbing is best-effort — do
            not rely on it for secrets LabLink does not know about.

            Do not guess a remote's network identity. When you need its IP or
            hostname for downstream config, read connect()'s
            metadata.peer_address (the resolved socket peer) or query the host
            (e.g. `hostname -I`) — never invent an address.

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
                    May also carry "security_warning" if a credential was
                    detected in the command.
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
            between calls). Sends each command in order and collects
            the full terminal transcript, including prompts and command echo.
            Use when commands depend on interactive shell state (env vars set
            by prior commands, directory context). Prefer ssh_exec for
            independent one-shot commands — it is faster and gives a clean
            exit code.

            Security: same rule as ssh_exec — never inline credentials in any
            command. A detected credential sets metadata.security_warning.

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

        @mcp.tool()
        def ssh_start_stream(alias: str, command: str) -> dict:
            """Start a long-lived SSH command and buffer its stdout in the background.

            Spawns a background thread that runs command via an exec channel and
            continuously buffers stdout into a bounded queue (capacity 1000 chunks,
            drop-oldest on overflow). Returns immediately with an acknowledgement —
            output is collected asynchronously. Read buffered output with
            ssh_read_stream; terminate with ssh_stop_stream. Only one stream per
            alias is allowed at a time.

            Use for commands that produce continuous output: "tail -f /var/log/syslog",
            "journalctl -f", "ping host", long-running scripts. For commands with
            defined exit behavior, prefer ssh_exec — it blocks until exit and returns
            a clean exit code.

            Args:
                alias: Configured device alias (must be an SSH-type alias).
                command: Long-lived shell command that produces continuous output.

            Returns a Result dict:
                success: True when the stream was started. False if no session is
                    open, a stream is already active, or exec_command failed.
            """
            return driver.ssh_start_stream_impl(alias, command)

        @mcp.tool()
        def ssh_read_stream(alias: str, timeout_ms: int | None = None) -> dict:
            """Drain buffered output from an active SSH stream.

            Returns all chunks currently in the buffer as a single string. Non-blocking
            — if the buffer is empty, raw=None and timed_out=True (nothing yet, try
            again). Poll at 500ms–2s intervals for typical log tails.

            Buffer overflow policy: if the producer outpaces the reader, the oldest
            chunks are silently dropped. Read frequently enough to keep up.

            Args:
                alias: Configured device alias (must be an SSH-type alias).
                timeout_ms: Reserved for future use; reads are non-blocking regardless.

            Returns a ReadResult dict:
                raw: Concatenated buffered output since the last read, or None when
                    the buffer was empty.
                timed_out: True when raw is None and the stream is still alive (no
                    data yet, try again).
                metadata: {"stream_ended": bool} — True when the remote command has
                    exited and no more data will arrive. Call ssh_stop_stream to
                    release the channel even after stream_ended is True.
                success: False if no session exists, no stream is active, or the
                    stream thread died unexpectedly (check error for details).
            """
            return driver.ssh_read_stream_impl(alias, timeout_ms)

        @mcp.tool()
        def ssh_stop_stream(alias: str) -> dict:
            """Terminate an active SSH stream and return any remaining buffered output.

            Closes the exec channel (signalling the remote process), waits up to 2
            seconds for the background thread to exit cleanly, drains the buffer,
            and resets streaming state. Always call this when done — even if the
            stream already ended — so a new stream can start on the same session.

            Args:
                alias: Configured device alias (must be an SSH-type alias).

            Returns a ReadResult dict:
                raw: Any output buffered since the last ssh_read_stream call.
                metadata: {"warning": "..."} if the thread did not exit within 2s.
                success: False if no session is open or no stream was active.
            """
            return driver.ssh_stop_stream_impl(alias)

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
