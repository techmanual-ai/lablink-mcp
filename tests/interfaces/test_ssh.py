"""SshDriver unit tests. paramiko is installed but patched at the library level.

Because the driver lazy-imports paramiko inside methods (not at module load),
we cannot patch 'lablink.interfaces.ssh.driver.paramiko'. Instead we patch
'paramiko.SSHClient' directly — the lazy import gets the real module, but
SSHClient is replaced with our mock. Exception classes remain real so
except-clause matching works correctly.
"""

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
