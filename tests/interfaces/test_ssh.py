"""SshDriver unit tests. paramiko is installed but patched at the library level.

Because the driver lazy-imports paramiko inside methods (not at module load),
we cannot patch 'lablink.interfaces.ssh.driver.paramiko'. Instead we patch
'paramiko.SSHClient' directly — the lazy import gets the real module, but
SSHClient is replaced with our mock. Exception classes remain real so
except-clause matching works correctly.
"""

import time
from queue import Queue
from threading import Thread
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from lablink import session as session_registry
from lablink.base import Session
from lablink.interfaces.ssh import SshDriver, SshDriverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> SshDriverConfig:
    defaults = dict(
        alias="test_pi",
        type="ssh",
        timeout_ms=5000,
        host="192.168.1.42",
        port=22,
        username="pi",
        auth_type="none",
    )
    defaults.update(overrides)
    return SshDriverConfig(**defaults)


def _mock_client(banner: str = "SSH-2.0-OpenSSH_9.0") -> MagicMock:
    """Return a mock paramiko.SSHClient with a connected transport."""
    client = MagicMock()
    transport = MagicMock()
    transport.remote_version = banner
    client.get_transport.return_value = transport
    return client


def _register_session(client: MagicMock, config: SshDriverConfig) -> Session:
    session = Session(
        alias=config.alias, interface_type="ssh", raw=client, config=config
    )
    session_registry.register(session)
    return session


@pytest.fixture(autouse=True)
def clear_sessions():
    """Ensure each test starts with a clean session registry."""
    session_registry.deregister("test_pi")
    yield
    session_registry.deregister("test_pi")


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_success_registers_session(self):
        client = _mock_client()
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=client):
            result = driver.connect(_config())

        assert result.success is True
        assert result.interface_type == "ssh"
        assert "SSH-2.0" in result.identity
        assert session_registry.is_registered("test_pi")
        assert result.device_memory is None  # injected by shared layer, not driver

    def test_identity_is_server_banner(self):
        client = _mock_client(banner="SSH-2.0-OpenSSH_8.4p1")
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=client):
            result = driver.connect(_config())

        assert result.identity == "SSH-2.0-OpenSSH_8.4p1"

    def test_already_open_returns_error(self):
        existing = _mock_client()
        _register_session(existing, _config())
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=_mock_client()):
            result = driver.connect(_config())

        assert result.success is False
        assert "already open" in result.error

    def test_empty_host_returns_error(self):
        driver = SshDriver()
        with patch("paramiko.SSHClient"):
            result = driver.connect(_config(host=""))
        assert result.success is False
        assert "host" in result.error

    def test_empty_username_returns_error(self):
        driver = SshDriver()
        with patch("paramiko.SSHClient"):
            result = driver.connect(_config(username=""))
        assert result.success is False
        assert "username" in result.error

    def test_authentication_failure(self):
        client = _mock_client()
        client.connect.side_effect = paramiko.AuthenticationException("bad key")
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=client):
            result = driver.connect(_config())

        assert result.success is False
        assert "authentication" in result.error.lower()
        assert not session_registry.is_registered("test_pi")

    def test_ssh_exception(self):
        client = _mock_client()
        client.connect.side_effect = paramiko.SSHException("no route")
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=client):
            result = driver.connect(_config())

        assert result.success is False
        assert "SSH error" in result.error
        assert not session_registry.is_registered("test_pi")

    def test_os_error(self):
        client = _mock_client()
        client.connect.side_effect = OSError("connection refused")
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=client):
            result = driver.connect(_config())

        assert result.success is False
        assert "Connection failed" in result.error

    def test_missing_paramiko_returns_structured_error(self):
        driver = SshDriver()
        with patch.dict("sys.modules", {"paramiko": None}):
            result = driver.connect(_config())
        assert result.success is False
        assert "paramiko" in result.error
        assert "lablink-mcp[ssh]" in result.hint

    def test_ssh_key_auth_passes_key_filename(self):
        client = _mock_client()
        driver = SshDriver()
        config = _config(
            auth_type="ssh_key",
            auth_ssh_key_path="/home/pi/.ssh/id_rsa",
        )

        with patch("paramiko.SSHClient", return_value=client):
            driver.connect(config)

        client.connect.assert_called_once()
        call_kwargs = client.connect.call_args.kwargs
        assert call_kwargs["key_filename"] == "/home/pi/.ssh/id_rsa"


def _exec_client(stdout_bytes: bytes = b"ok\n", exit_code: int = 0) -> MagicMock:
    client = _mock_client()
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = exit_code
    mock_stdout.read.return_value = stdout_bytes
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    return client


class TestCredentialRedaction:
    def test_secret_in_command_redacted_from_log(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PI_PASS", "hunter2")
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        cfg = _config(auth_type="ssh_password", auth_password_env="PI_PASS")
        client = _exec_client()
        driver = SshDriver()
        _register_session(client, cfg)

        result = driver.ssh_exec_impl("test_pi", "echo hunter2 | sudo -S id")

        # Returned to the agent: warning attached, real output intact.
        assert result["success"] is True
        assert "security_warning" in result["metadata"]

        # Durable log: secret scrubbed, command preserved structurally.
        log_text = next(tmp_path.glob("*.jsonl")).read_text()
        assert "hunter2" not in log_text
        assert "***" in log_text
        assert "sudo -S id" in log_text

    def test_clean_command_no_warning(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PI_PASS", "hunter2")
        monkeypatch.setenv("LABLINK_LOG_DIR", str(tmp_path))
        cfg = _config(auth_type="ssh_password", auth_password_env="PI_PASS")
        client = _exec_client()
        driver = SshDriver()
        _register_session(client, cfg)

        result = driver.ssh_exec_impl("test_pi", "uname -a")

        assert result["success"] is True
        assert "security_warning" not in result["metadata"]


class TestPeerAddress:
    def test_connect_surfaces_resolved_peer(self):
        client = _mock_client()
        client.get_transport.return_value.getpeername.return_value = ("192.168.1.42", 22)
        driver = SshDriver()

        with patch("paramiko.SSHClient", return_value=client):
            result = driver.connect(_config())

        assert result.success is True
        assert result.metadata.get("peer_address") == "192.168.1.42:22"


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_success_closes_client(self):
        client = _mock_client()
        session = _register_session(client, _config())
        driver = SshDriver()

        result = driver.disconnect(session)

        assert result.success is True
        client.close.assert_called_once()

    def test_close_error_returns_failure(self):
        client = _mock_client()
        client.close.side_effect = Exception("already closed")
        session = _register_session(client, _config())
        driver = SshDriver()

        result = driver.disconnect(session)

        assert result.success is False
        assert "Error closing SSH session" in result.error


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_tcp_reachable_ready(self):
        driver = SshDriver()
        with patch("lablink.interfaces.ssh.driver._port_open", return_value=True):
            result = driver.diagnose(_config())
        assert result.ready is True
        assert result.checks["tcp_port"]["status"] == "ok"
        assert result.action_items == []

    def test_tcp_unreachable_adds_action_item(self):
        driver = SshDriver()
        with patch("lablink.interfaces.ssh.driver._port_open", return_value=False):
            result = driver.diagnose(_config())
        assert result.ready is False
        assert result.checks["tcp_port"]["status"] == "closed"
        assert any("192.168.1.42" in item for item in result.action_items)

    def test_empty_host_adds_action_item(self):
        driver = SshDriver()
        with patch("lablink.interfaces.ssh.driver._port_open", return_value=False):
            result = driver.diagnose(_config(host=""))
        assert result.ready is False
        assert any("host" in item for item in result.action_items)

    def test_key_file_missing_adds_action_item(self):
        driver = SshDriver()
        with patch("lablink.interfaces.ssh.driver._port_open", return_value=True):
            result = driver.diagnose(
                _config(
                    auth_type="ssh_key",
                    auth_ssh_key_path="/nonexistent/id_rsa",
                )
            )
        assert result.ready is False
        assert any("key file" in item.lower() for item in result.action_items)
        assert result.checks["ssh_key_file"]["status"] == "missing"

    def test_invalid_auth_type_adds_action_item(self):
        driver = SshDriver()
        with patch("lablink.interfaces.ssh.driver._port_open", return_value=True):
            result = driver.diagnose(_config(auth_type="magic_token"))
        assert result.ready is False
        assert any("auth_type" in item for item in result.action_items)


# ---------------------------------------------------------------------------
# ssh_exec_impl
# ---------------------------------------------------------------------------


class TestSshExec:
    def _exec_session(
        self, stdout_data: str = "", stderr_data: str = "", exit_code: int = 0
    ) -> MagicMock:
        client = _mock_client()
        stdout = MagicMock()
        stderr_mock = MagicMock()
        stdout.read.return_value = stdout_data.encode()
        stderr_mock.read.return_value = stderr_data.encode()
        stdout.channel.recv_exit_status.return_value = exit_code
        client.exec_command.return_value = (MagicMock(), stdout, stderr_mock)
        _register_session(client, _config())
        return client

    def test_success_returns_stdout(self):
        self._exec_session(stdout_data="Linux pi 5.15\n")
        driver = SshDriver()

        result = driver.ssh_exec_impl("test_pi", "uname -r")

        assert result["success"] is True
        assert "Linux" in result["raw"]
        assert result["metadata"]["exit_code"] == 0
        assert result["metadata"]["stderr"] == ""

    def test_nonzero_exit_code_in_metadata(self):
        self._exec_session(
            stdout_data="", stderr_data="command not found\n", exit_code=127
        )
        driver = SshDriver()

        result = driver.ssh_exec_impl("test_pi", "badcmd")

        assert result["success"] is True  # transport success
        assert result["metadata"]["exit_code"] == 127
        assert "not found" in result["metadata"]["stderr"]

    def test_no_session_returns_error(self):
        driver = SshDriver()
        result = driver.ssh_exec_impl("test_pi", "ls")
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_wrong_type_session_returns_error(self):
        visa_session = Session(
            alias="test_pi",
            interface_type="visa",
            raw=MagicMock(),
            config=MagicMock(),
        )
        session_registry.register(visa_session)
        driver = SshDriver()

        result = driver.ssh_exec_impl("test_pi", "ls")

        assert result["success"] is False
        assert "visa" in result["error"]

    def test_exec_exception_returns_structured_error(self):
        client = _mock_client()
        client.exec_command.side_effect = Exception("transport closed")
        _register_session(client, _config())
        driver = SshDriver()

        result = driver.ssh_exec_impl("test_pi", "ls")

        assert result["success"] is False
        assert "SSH exec error" in result["error"]


# ---------------------------------------------------------------------------
# ssh_shell_session_impl
# ---------------------------------------------------------------------------


class TestSshShellSession:
    def _shell_session(self, transcript: str = "$ ls\nfile.txt\n$ ") -> MagicMock:
        client = _mock_client()
        chan = MagicMock()
        # Simulate: first recv_ready=True returns transcript, then quiet
        data = transcript.encode()
        chan.recv_ready.side_effect = [True, False] + [False] * 30
        chan.recv.side_effect = [data] + [b""] * 30
        client.invoke_shell.return_value = chan
        _register_session(client, _config())
        return client

    def test_success_returns_transcript(self):
        self._shell_session(transcript="$ echo hello\nhello\n$ ")
        driver = SshDriver()

        result = driver.ssh_shell_session_impl("test_pi", ["echo hello"])

        assert result["success"] is True
        assert isinstance(result["raw"], str)

    def test_no_session_returns_error(self):
        driver = SshDriver()
        result = driver.ssh_shell_session_impl("test_pi", ["ls"])
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_invoke_shell_exception_returns_structured_error(self):
        client = _mock_client()
        client.invoke_shell.side_effect = Exception("channel failed")
        _register_session(client, _config())
        driver = SshDriver()

        result = driver.ssh_shell_session_impl("test_pi", ["ls"])

        assert result["success"] is False
        assert "SSH shell error" in result["error"]


# ---------------------------------------------------------------------------
# check_python_deps
# ---------------------------------------------------------------------------


class TestCheckPythonDeps:
    def test_paramiko_present(self):
        deps = SshDriver.check_python_deps()
        names = [name for name, _ in deps]
        assert "paramiko" in names

    def test_paramiko_available(self):
        deps = dict(SshDriver.check_python_deps())
        assert deps["paramiko"] is True

    def test_paramiko_missing(self):
        with patch(
            "lablink.interfaces.ssh.driver.importlib.util.find_spec",
            return_value=None,
        ):
            deps = dict(SshDriver.check_python_deps())
        assert deps["paramiko"] is False


# ---------------------------------------------------------------------------
# system_dep_check
# ---------------------------------------------------------------------------


class TestSystemDepCheck:
    def test_no_system_deps(self):
        # SSH has no OS-level deps (unlike VISA which needs libusb)
        assert SshDriver.system_dep_check() == []


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------


def _stream_session(*, recv_ready_seq=None, recv_data=b"", exit_ready=True) -> MagicMock:
    """Register a session with a mock channel configured for streaming tests."""
    client = _mock_client()
    channel = MagicMock()
    stdout = MagicMock()
    stdout.channel = channel
    client.exec_command.return_value = (MagicMock(), stdout, MagicMock())

    if recv_ready_seq is not None:
        channel.recv_ready.side_effect = recv_ready_seq
    else:
        channel.recv_ready.return_value = False
    channel.exit_status_ready.return_value = exit_ready
    channel.recv.return_value = recv_data

    _register_session(client, _config())
    return client


def _session_obj() -> Session:
    return session_registry.get_any("test_pi")


# ---------------------------------------------------------------------------
# ssh_start_stream_impl
# ---------------------------------------------------------------------------


class TestSshStartStream:
    def test_success_sets_buffer_and_thread(self):
        _stream_session()
        driver = SshDriver()

        result = driver.ssh_start_stream_impl("test_pi", "tail -f /var/log/syslog")

        assert result["success"] is True
        sess = _session_obj()
        assert sess.buffer is not None
        assert sess.buffer_thread is not None
        assert sess.metadata.get("stream_channel") is not None

    def test_no_session_returns_error(self):
        driver = SshDriver()
        result = driver.ssh_start_stream_impl("test_pi", "tail -f /log")
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_wrong_type_session_returns_error(self):
        visa_session = Session(
            alias="test_pi", interface_type="visa", raw=MagicMock(), config=MagicMock()
        )
        session_registry.register(visa_session)
        driver = SshDriver()
        result = driver.ssh_start_stream_impl("test_pi", "tail -f /log")
        assert result["success"] is False
        assert "visa" in result["error"]

    def test_already_streaming_returns_error(self):
        _stream_session()
        sess = _session_obj()
        alive_thread = MagicMock(spec=Thread)
        alive_thread.is_alive.return_value = True
        sess.buffer_thread = alive_thread
        driver = SshDriver()

        result = driver.ssh_start_stream_impl("test_pi", "tail -f /log")

        assert result["success"] is False
        assert "already active" in result["error"]

    def test_exec_command_failure_returns_error(self):
        client = _mock_client()
        client.exec_command.side_effect = Exception("transport closed")
        _register_session(client, _config())
        driver = SshDriver()

        result = driver.ssh_start_stream_impl("test_pi", "tail -f /log")

        assert result["success"] is False
        assert "Failed to start stream" in result["error"]

    def test_dead_prior_thread_allows_new_stream(self):
        """A finished (not alive) prior thread does not block a new stream."""
        _stream_session()
        sess = _session_obj()
        dead_thread = MagicMock(spec=Thread)
        dead_thread.is_alive.return_value = False
        sess.buffer_thread = dead_thread
        driver = SshDriver()

        result = driver.ssh_start_stream_impl("test_pi", "tail -f /log")
        assert result["success"] is True


# ---------------------------------------------------------------------------
# ssh_read_stream_impl
# ---------------------------------------------------------------------------


class TestSshReadStream:
    def _preloaded_session(self, items: list) -> Session:
        """Register a session with a pre-populated buffer queue."""
        _stream_session()
        sess = _session_obj()
        buf: Queue = Queue(maxsize=1000)
        for item in items:
            buf.put_nowait(item)
        thread = MagicMock(spec=Thread)
        thread.is_alive.return_value = True
        sess.buffer = buf
        sess.buffer_thread = thread
        return sess

    def test_no_session_returns_error(self):
        driver = SshDriver()
        result = driver.ssh_read_stream_impl("test_pi")
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_no_stream_returns_error(self):
        _stream_session()
        # buffer_thread is None by default on a freshly registered session
        driver = SshDriver()
        result = driver.ssh_read_stream_impl("test_pi")
        assert result["success"] is False
        assert "No active stream" in result["error"]

    def test_dead_thread_with_error_returns_failure(self):
        _stream_session()
        sess = _session_obj()
        dead_thread = MagicMock(spec=Thread)
        dead_thread.is_alive.return_value = False
        sess.buffer_thread = dead_thread
        sess.buffer = Queue()
        sess.metadata["stream_error"] = "recv failed"
        driver = SshDriver()

        result = driver.ssh_read_stream_impl("test_pi")

        assert result["success"] is False
        assert "Stream thread died" in result["error"]
        assert "recv failed" in result["error"]

    def test_returns_buffered_chunks(self):
        self._preloaded_session(["line1\n", "line2\n", "line3\n"])
        driver = SshDriver()

        result = driver.ssh_read_stream_impl("test_pi")

        assert result["success"] is True
        assert result["raw"] == "line1\nline2\nline3\n"
        assert result["timed_out"] is False

    def test_empty_buffer_returns_timed_out(self):
        self._preloaded_session([])
        driver = SshDriver()

        result = driver.ssh_read_stream_impl("test_pi")

        assert result["success"] is True
        assert result["raw"] is None
        assert result["timed_out"] is True

    def test_none_sentinel_sets_stream_ended(self):
        """None sentinel in buffer indicates remote command exited."""
        self._preloaded_session(["final line\n", None])
        driver = SshDriver()

        result = driver.ssh_read_stream_impl("test_pi")

        assert result["success"] is True
        assert result["raw"] == "final line\n"
        assert result["metadata"]["stream_ended"] is True

    def test_only_sentinel_sets_stream_ended_empty_raw(self):
        self._preloaded_session([None])
        driver = SshDriver()

        result = driver.ssh_read_stream_impl("test_pi")

        assert result["success"] is True
        assert result["raw"] == ""
        assert result["metadata"]["stream_ended"] is True


# ---------------------------------------------------------------------------
# ssh_stop_stream_impl
# ---------------------------------------------------------------------------


class TestSshStopStream:
    def _streaming_session(self, buffered: list | None = None) -> Session:
        """Register a session that looks like it has an active stream."""
        _stream_session()
        sess = _session_obj()
        buf: Queue = Queue(maxsize=1000)
        for item in buffered or []:
            buf.put_nowait(item)
        thread = MagicMock(spec=Thread)
        thread.is_alive.return_value = False  # exits cleanly after join
        sess.buffer = buf
        sess.buffer_thread = thread
        sess.metadata["stream_channel"] = MagicMock()
        return sess

    def test_no_session_returns_error(self):
        driver = SshDriver()
        result = driver.ssh_stop_stream_impl("test_pi")
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_no_stream_returns_error(self):
        _stream_session()
        driver = SshDriver()
        result = driver.ssh_stop_stream_impl("test_pi")
        assert result["success"] is False
        assert "No active stream" in result["error"]

    def test_success_returns_remaining_transcript(self):
        sess = self._streaming_session(buffered=["part1\n", "part2\n"])
        driver = SshDriver()

        result = driver.ssh_stop_stream_impl("test_pi")

        assert result["success"] is True
        assert result["raw"] == "part1\npart2\n"
        assert "warning" not in result["metadata"]

    def test_empty_buffer_returns_empty_raw(self):
        self._streaming_session(buffered=[])
        driver = SshDriver()

        result = driver.ssh_stop_stream_impl("test_pi")

        assert result["success"] is True
        assert result["raw"] == ""

    def test_clears_streaming_state(self):
        sess = self._streaming_session()
        driver = SshDriver()

        driver.ssh_stop_stream_impl("test_pi")

        assert sess.buffer_thread is None
        assert sess.buffer is None
        assert "stream_channel" not in sess.metadata

    def test_closes_channel(self):
        sess = self._streaming_session()
        channel = sess.metadata["stream_channel"]
        driver = SshDriver()

        driver.ssh_stop_stream_impl("test_pi")

        channel.close.assert_called_once()

    def test_thread_join_timeout_adds_warning(self):
        """Thread still alive after join → warning in metadata."""
        self._streaming_session()
        sess = _session_obj()
        sess.buffer_thread.is_alive.return_value = True  # still alive after join
        driver = SshDriver()

        result = driver.ssh_stop_stream_impl("test_pi")

        assert result["success"] is True
        assert "warning" in result["metadata"]

    def test_none_sentinel_in_buffer_handled(self):
        self._streaming_session(buffered=["chunk\n", None])
        driver = SshDriver()

        result = driver.ssh_stop_stream_impl("test_pi")

        assert result["raw"] == "chunk\n"


# ---------------------------------------------------------------------------
# disconnect with active stream
# ---------------------------------------------------------------------------


class TestDisconnectWithStream:
    def test_disconnect_joins_thread_and_closes_channel(self):
        """disconnect() tears down an active stream before closing the SSH connection."""
        client = _mock_client()
        sess = _register_session(client, _config())
        channel = MagicMock()
        thread = MagicMock(spec=Thread)
        thread.is_alive.return_value = False
        sess.buffer_thread = thread
        sess.buffer = Queue()
        sess.metadata["stream_channel"] = channel
        driver = SshDriver()

        result = driver.disconnect(sess)

        assert result.success is True
        channel.close.assert_called_once()
        thread.join.assert_called_once_with(timeout=2.0)
        client.close.assert_called_once()

    def test_disconnect_clears_stream_state(self):
        client = _mock_client()
        sess = _register_session(client, _config())
        thread = MagicMock(spec=Thread)
        thread.is_alive.return_value = False
        sess.buffer_thread = thread
        sess.buffer = Queue()
        driver = SshDriver()

        driver.disconnect(sess)

        assert sess.buffer_thread is None
        assert sess.buffer is None

    def test_disconnect_no_stream_unaffected(self):
        """disconnect() with no active stream still closes the connection cleanly."""
        client = _mock_client()
        sess = _register_session(client, _config())
        driver = SshDriver()

        result = driver.disconnect(sess)

        assert result.success is True
        client.close.assert_called_once()


# ---------------------------------------------------------------------------
# _stream_worker integration (real thread, mock channel)
# ---------------------------------------------------------------------------


class TestStreamWorker:
    def test_worker_puts_chunks_then_sentinel(self):
        from lablink.interfaces.ssh.driver import _stream_worker

        channel = MagicMock()
        channel.exit_status_ready.return_value = False
        # Produce one chunk, then EOF
        channel.recv_ready.side_effect = [True, True, False]
        channel.recv.side_effect = [b"hello\n", b""]

        buf: Queue = Queue(maxsize=1000)
        metadata: dict = {}

        thread = Thread(target=_stream_worker, args=(channel, buf, metadata))
        thread.start()
        thread.join(timeout=3.0)

        items = []
        while not buf.empty():
            items.append(buf.get_nowait())

        assert items[0] == "hello\n"
        assert items[-1] is None  # EOF sentinel

    def test_worker_sets_stream_error_on_exception(self):
        from lablink.interfaces.ssh.driver import _stream_worker

        channel = MagicMock()
        channel.exit_status_ready.side_effect = RuntimeError("boom")

        buf: Queue = Queue(maxsize=1000)
        metadata: dict = {}

        thread = Thread(target=_stream_worker, args=(channel, buf, metadata))
        thread.start()
        thread.join(timeout=3.0)

        assert "stream_error" in metadata
        assert "boom" in metadata["stream_error"]
        # sentinel still put despite exception
        assert buf.get_nowait() is None

    def test_worker_drop_oldest_on_overflow(self):
        from lablink.interfaces.ssh.driver import _stream_worker

        channel = MagicMock()
        channel.exit_status_ready.return_value = False

        num_chunks = 1002  # 2 more than maxsize=1000
        ready = [True] * num_chunks + [True]
        channel.recv_ready.side_effect = ready
        chunks = [f"line{i}\n".encode() for i in range(num_chunks)] + [b""]
        channel.recv.side_effect = chunks

        buf: Queue = Queue(maxsize=1000)
        metadata: dict = {}

        thread = Thread(target=_stream_worker, args=(channel, buf, metadata))
        thread.start()
        thread.join(timeout=5.0)

        # Buffer should not exceed maxsize + 1 (sentinel)
        assert buf.qsize() <= 1001
