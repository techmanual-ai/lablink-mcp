"""PythonShellDriver unit tests. subprocess.Popen is patched throughout.

The JSONL wire protocol is simulated by controlling what mock_proc.stdout.readline
returns for each call sequence. _read_line_with_timeout is patched directly for
timeout tests so the test suite doesn't actually block.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lablink import session as session_registry
from lablink.base import Session
from lablink.interfaces.python_shell import PythonShellDriver, PythonShellDriverConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_READY_LINE = (
    json.dumps({"op": "ready", "python_version": "3.11.0", "interpreter": "/usr/bin/python3"}) + "\n"
).encode("utf-8")


def _config(**overrides) -> PythonShellDriverConfig:
    defaults = dict(
        alias="test_env",
        type="python_shell",
        timeout_ms=5000,
        python_path="/usr/bin/python3",
    )
    defaults.update(overrides)
    return PythonShellDriverConfig(**defaults)


def _exec_response(req_id: str = "req-1", stdout: str = "", stderr: str = "", exception=None) -> bytes:
    return (
        json.dumps({
            "id": req_id, "op": "exec",
            "stdout": stdout, "stderr": stderr,
            "result": None, "exception": exception, "duration_ms": 5,
        }) + "\n"
    ).encode("utf-8")


def _eval_response(req_id: str = "req-1", result: str = "42", stdout: str = "", exception=None) -> bytes:
    return (
        json.dumps({
            "id": req_id, "op": "eval",
            "stdout": stdout, "stderr": "",
            "result": result, "exception": exception, "duration_ms": 3,
        }) + "\n"
    ).encode("utf-8")


def _mock_proc(readline_side_effect=None) -> MagicMock:
    proc = MagicMock()
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.poll.return_value = None
    proc.returncode = None
    if readline_side_effect is not None:
        proc.stdout.readline.side_effect = readline_side_effect
    return proc


def _register_session(proc: MagicMock, config: PythonShellDriverConfig) -> Session:
    session = Session(
        alias=config.alias,
        interface_type="python_shell",
        raw=proc,
        config=config,
        metadata={"busy": False, "req_counter": 0},
    )
    session_registry.register(session)
    return session


@pytest.fixture(autouse=True)
def clear_sessions():
    session_registry.deregister("test_env")
    yield
    session_registry.deregister("test_env")


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_success_registers_session(self):
        proc = _mock_proc([_READY_LINE])
        driver = PythonShellDriver()
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = driver.connect(_config())

        assert result.success is True
        assert result.interface_type == "python_shell"
        assert "Python 3.11.0" in result.identity
        assert session_registry.is_registered("test_env")

    def test_success_identity_contains_interpreter_path(self):
        proc = _mock_proc([_READY_LINE])
        driver = PythonShellDriver()
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("pathlib.Path.exists", return_value=True),
        ):
            result = driver.connect(_config())

        assert "/usr/bin/python3" in result.identity

    def test_duplicate_connect_returns_error(self):
        proc = _mock_proc([_READY_LINE])
        driver = PythonShellDriver()
        config = _config()
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("pathlib.Path.exists", return_value=True),
        ):
            driver.connect(config)
            result = driver.connect(config)

        assert result.success is False
        assert "already open" in result.error

    def test_empty_python_path_returns_error(self):
        driver = PythonShellDriver()
        result = driver.connect(_config(python_path=""))

        assert result.success is False
        assert "python_path" in result.error

    def test_interpreter_not_found_returns_error(self):
        driver = PythonShellDriver()
        with patch("pathlib.Path.exists", return_value=False):
            result = driver.connect(_config())

        assert result.success is False
        assert "not found" in result.error
        assert not session_registry.is_registered("test_env")

    def test_popen_raises_returns_error(self):
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", side_effect=OSError("permission denied")),
        ):
            result = driver.connect(_config())

        assert result.success is False
        assert "spawn" in result.error.lower() or "permission denied" in result.error

    def test_ready_timeout_kills_process_and_returns_error(self):
        proc = _mock_proc()
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=proc),
            patch(
                "lablink.interfaces.python_shell.driver._read_line_with_timeout",
                return_value=None,
            ),
        ):
            result = driver.connect(_config())

        assert result.success is False
        assert "READY" in result.error
        proc.kill.assert_called_once()

    def test_eof_before_ready_returns_error(self):
        proc = _mock_proc([b""])  # EOF
        proc.stderr.read.return_value = b"SyntaxError: bad code\n"
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=proc),
        ):
            result = driver.connect(_config())

        assert result.success is False
        assert "exited" in result.error.lower()

    def test_bad_json_ready_returns_error(self):
        proc = _mock_proc([b"not json\n"])
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=proc),
        ):
            result = driver.connect(_config())

        assert result.success is False
        assert "handshake" in result.error.lower() or "JSON" in result.error

    def test_wrong_op_in_ready_returns_error(self):
        wrong_line = (json.dumps({"op": "error", "msg": "boom"}) + "\n").encode("utf-8")
        proc = _mock_proc([wrong_line])
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.Popen", return_value=proc),
        ):
            result = driver.connect(_config())

        assert result.success is False
        assert "ready" in result.error.lower()

    def test_session_metadata_initialized(self):
        proc = _mock_proc([_READY_LINE])
        driver = PythonShellDriver()
        with (
            patch("subprocess.Popen", return_value=proc),
            patch("pathlib.Path.exists", return_value=True),
        ):
            driver.connect(_config())

        session = session_registry.get("test_env", "python_shell")
        assert session.metadata["busy"] is False
        assert session.metadata["req_counter"] == 0


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_sends_shutdown_and_waits(self):
        proc = _mock_proc()
        driver = PythonShellDriver()
        session = _register_session(proc, _config())

        result = driver.disconnect(session)

        assert result.success is True
        proc.stdin.write.assert_called()
        written = proc.stdin.write.call_args[0][0].decode("utf-8")
        assert '"op": "shutdown"' in written
        proc.wait.assert_called()

    def test_clears_busy_flag(self):
        proc = _mock_proc()
        driver = PythonShellDriver()
        session = _register_session(proc, _config())
        session.metadata["busy"] = True

        driver.disconnect(session)

        assert session.metadata["busy"] is False

    def test_handles_broken_pipe_gracefully(self):
        proc = _mock_proc()
        proc.stdin.write.side_effect = BrokenPipeError
        driver = PythonShellDriver()
        session = _register_session(proc, _config())

        result = driver.disconnect(session)

        assert result.success is True

    def test_kills_on_timeout(self):
        import subprocess as sp

        proc = _mock_proc()
        proc.wait.side_effect = [sp.TimeoutExpired(cmd="py", timeout=2), None, None]
        driver = PythonShellDriver()
        session = _register_session(proc, _config())

        result = driver.disconnect(session)

        assert result.success is True
        proc.terminate.assert_called_once()

    def test_skips_shutdown_if_already_exited(self):
        proc = _mock_proc()
        proc.poll.return_value = 0  # process already dead
        driver = PythonShellDriver()
        session = _register_session(proc, _config())

        result = driver.disconnect(session)

        assert result.success is True
        proc.stdin.write.assert_not_called()


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_ready_when_handshake_succeeds(self):
        proc = _mock_proc([_READY_LINE])
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("os.access", return_value=True),
            patch("subprocess.Popen", return_value=proc),
        ):
            result = driver.diagnose(_config())

        assert result.ready is True
        assert result.alias == "test_env"
        assert "handshake" in result.checks
        assert result.checks["handshake"]["status"] == "ok"

    def test_not_ready_when_python_path_empty(self):
        driver = PythonShellDriver()
        result = driver.diagnose(_config(python_path=""))

        assert result.ready is False
        assert any("python_path" in item for item in result.action_items)

    def test_not_ready_when_interpreter_not_found(self):
        driver = PythonShellDriver()
        with patch("pathlib.Path.exists", return_value=False):
            result = driver.diagnose(_config())

        assert result.ready is False
        assert result.checks["interpreter_exists"]["status"] == "not_found"

    def test_not_ready_when_handshake_times_out(self):
        proc = _mock_proc()
        driver = PythonShellDriver()
        with (
            patch("pathlib.Path.exists", return_value=True),
            patch("os.access", return_value=True),
            patch("subprocess.Popen", return_value=proc),
            patch(
                "lablink.interfaces.python_shell.driver._read_line_with_timeout",
                return_value=None,
            ),
        ):
            result = driver.diagnose(_config())

        assert result.ready is False
        assert result.checks["handshake"]["status"] == "timeout"
        proc.kill.assert_called()


# ---------------------------------------------------------------------------
# python_shell_exec — clean execution
# ---------------------------------------------------------------------------


class TestExec:
    def test_clean_exec_returns_stdout(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _exec_response(stdout="hello\n")
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", 'print("hello")')

        assert result["success"] is True
        assert result["timed_out"] is False
        assert "hello" in result["raw"]
        assert result["metadata"]["stdout"] == "hello\n"
        assert result["metadata"].get("exception") is None

    def test_exec_result_is_null(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _exec_response()
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "x = 1")

        assert result["metadata"]["result"] is None

    def test_exec_sends_correct_wire_op(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _exec_response()
        driver = PythonShellDriver()
        _register_session(proc, _config())

        driver.python_shell_exec_impl("test_env", "x = 42")

        written = proc.stdin.write.call_args[0][0].decode("utf-8")
        req = json.loads(written.strip())
        assert req["op"] == "exec"
        assert req["code"] == "x = 42"

    def test_exec_increments_req_counter(self):
        proc = _mock_proc()
        proc.stdout.readline.side_effect = [_exec_response("req-1"), _exec_response("req-2")]
        driver = PythonShellDriver()
        _register_session(proc, _config())

        driver.python_shell_exec_impl("test_env", "a = 1")
        driver.python_shell_exec_impl("test_env", "b = 2")

        session = session_registry.get("test_env", "python_shell")
        assert session.metadata["req_counter"] == 2

    def test_exec_clears_busy_after_response(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _exec_response()
        driver = PythonShellDriver()
        _register_session(proc, _config())

        driver.python_shell_exec_impl("test_env", "pass")

        session = session_registry.get("test_env", "python_shell")
        assert session.metadata["busy"] is False

    def test_exec_no_session_returns_error(self):
        driver = PythonShellDriver()
        result = driver.python_shell_exec_impl("test_env", "pass")

        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_exec_wrong_type_session_returns_error(self):
        # Register a session of a different type
        mock_raw = MagicMock()
        other_config = MagicMock()
        other_config.alias = "test_env"
        session = Session(
            alias="test_env", interface_type="ssh", raw=mock_raw, config=other_config
        )
        session_registry.register(session)
        driver = PythonShellDriver()

        result = driver.python_shell_exec_impl("test_env", "pass")

        assert result["success"] is False
        assert "ssh" in result["error"]


# ---------------------------------------------------------------------------
# python_shell_exec — exception in user code
# ---------------------------------------------------------------------------


class TestExecException:
    def test_exception_returns_success_true_with_traceback(self):
        exc_info = {
            "type": "ZeroDivisionError",
            "message": "division by zero",
            "traceback": "Traceback (most recent call last):\n  ...\nZeroDivisionError: division by zero\n",
        }
        proc = _mock_proc()
        proc.stdout.readline.return_value = _exec_response(exception=exc_info)
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "1/0")

        assert result["success"] is True
        assert result["timed_out"] is False
        assert "ZeroDivisionError" in result["raw"]
        assert result["metadata"]["exception"]["type"] == "ZeroDivisionError"

    def test_exception_with_stdout_prepends_stdout_to_raw(self):
        exc_info = {
            "type": "ValueError",
            "message": "bad",
            "traceback": "Traceback...\nValueError: bad\n",
        }
        proc = _mock_proc()
        proc.stdout.readline.return_value = _exec_response(stdout="before\n", exception=exc_info)
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "print('before'); raise ValueError('bad')")

        assert "before" in result["raw"]
        assert "ValueError" in result["raw"]


# ---------------------------------------------------------------------------
# python_shell_eval — clean evaluation
# ---------------------------------------------------------------------------


class TestEval:
    def test_clean_eval_returns_result_repr(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _eval_response(result="42")
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_eval_impl("test_env", "6 * 7")

        assert result["success"] is True
        assert result["raw"] == "42"
        assert result["metadata"]["result"] == "42"

    def test_eval_sends_correct_wire_op(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _eval_response()
        driver = PythonShellDriver()
        _register_session(proc, _config())

        driver.python_shell_eval_impl("test_env", "x + 1")

        written = proc.stdin.write.call_args[0][0].decode("utf-8")
        req = json.loads(written.strip())
        assert req["op"] == "eval"
        assert req["expression"] == "x + 1"

    def test_eval_with_stdout_prepends_to_raw(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = _eval_response(result="10", stdout="debug\n")
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_eval_impl("test_env", "some_expr")

        assert "debug" in result["raw"]
        assert "10" in result["raw"]

    def test_eval_exception_returns_traceback(self):
        exc_info = {
            "type": "NameError",
            "message": "name 'x' is not defined",
            "traceback": "Traceback...\nNameError: name 'x' is not defined\n",
        }
        proc = _mock_proc()
        proc.stdout.readline.return_value = _eval_response(result=None, exception=exc_info)
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_eval_impl("test_env", "x")

        assert result["success"] is True
        assert "NameError" in result["raw"]
        assert result["metadata"]["exception"]["type"] == "NameError"


# ---------------------------------------------------------------------------
# Timeout and busy handling
# ---------------------------------------------------------------------------


class TestTimeoutAndBusy:
    def test_timeout_returns_timed_out_true_and_busy_stays_true(self):
        proc = _mock_proc()
        driver = PythonShellDriver()
        _register_session(proc, _config())

        with patch(
            "lablink.interfaces.python_shell.driver._read_line_with_timeout",
            return_value=None,
        ):
            result = driver.python_shell_exec_impl("test_env", "import time; time.sleep(60)")

        assert result["success"] is True
        assert result["timed_out"] is True
        session = session_registry.get("test_env", "python_shell")
        assert session.metadata["busy"] is True

    def test_busy_session_rejects_new_call(self):
        proc = _mock_proc()
        driver = PythonShellDriver()
        session = _register_session(proc, _config())
        session.metadata["busy"] = True

        result = driver.python_shell_exec_impl("test_env", "pass")

        assert result["success"] is False
        assert "busy" in result["error"].lower()
        # stdin should NOT have been written to
        proc.stdin.write.assert_not_called()

    def test_disconnect_after_timeout_recovers(self):
        """Timeout-still-running with recovery via disconnect."""
        proc = _mock_proc()
        driver = PythonShellDriver()
        session = _register_session(proc, _config())

        # Simulate a timed-out state: busy=True, session still registered.
        session.metadata["busy"] = True

        # Disconnect should succeed and clear busy.
        result = driver.disconnect(session)

        assert result.success is True
        assert session.metadata["busy"] is False


# ---------------------------------------------------------------------------
# Subprocess crash handling
# ---------------------------------------------------------------------------


class TestSubprocessCrash:
    def test_broken_pipe_on_write_returns_error(self):
        proc = _mock_proc()
        proc.stdin.write.side_effect = BrokenPipeError("pipe broken")
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "x = 1")

        assert result["success"] is False
        assert "exited unexpectedly" in result["error"]
        session = session_registry.get("test_env", "python_shell")
        assert session.metadata["busy"] is False

    def test_eof_on_read_returns_error(self):
        proc = _mock_proc()
        proc.stdout.readline.return_value = b""  # EOF
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "x = 1")

        assert result["success"] is False
        assert "exited unexpectedly" in result["error"]

    def test_exited_process_before_send_returns_error(self):
        proc = _mock_proc()
        proc.poll.return_value = 1  # non-zero exit
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "pass")

        assert result["success"] is False
        assert "exited" in result["error"].lower()
        proc.stdin.write.assert_not_called()


# ---------------------------------------------------------------------------
# Vendor-SDK persistence scenario (state persists across calls)
# ---------------------------------------------------------------------------


class TestVendorSdkPersistence:
    def test_namespace_state_persists_across_calls(self):
        """Import in one call, use the variable in a subsequent call.

        Since we're mocking the subprocess, we verify the correct wire
        frames are sent in sequence — the real namespace persistence is
        tested by bootstrap.py when run as an actual subprocess.
        """
        import_response = _exec_response("req-1")
        query_response = _eval_response("req-2", result="'sensor_value'")

        proc = _mock_proc([import_response, query_response])
        driver = PythonShellDriver()
        _register_session(proc, _config())

        # Call 1: "import" a vendor lib and create a handle.
        r1 = driver.python_shell_exec_impl("test_env", "import math; handle = math")
        assert r1["success"] is True

        # Call 2: use the handle — variable from previous call.
        r2 = driver.python_shell_eval_impl("test_env", "handle.pi")
        assert r2["success"] is True
        assert r2["metadata"]["result"] == "'sensor_value'"

        # Verify both frames were sent in order.
        calls = proc.stdin.write.call_args_list
        req1 = json.loads(calls[0][0][0].decode("utf-8").strip())
        req2 = json.loads(calls[1][0][0].decode("utf-8").strip())
        assert req1["op"] == "exec"
        assert req1["id"] == "req-1"
        assert req2["op"] == "eval"
        assert req2["id"] == "req-2"


# ---------------------------------------------------------------------------
# Truncated output
# ---------------------------------------------------------------------------


class TestTruncatedOutput:
    def test_truncated_flag_surfaced_in_metadata(self):
        truncated_response = (
            json.dumps({
                "id": "req-1", "op": "exec",
                "stdout": "x" * 100, "stderr": "",
                "result": None, "exception": None, "duration_ms": 1,
                "truncated": True, "truncated_bytes": 999,
            }) + "\n"
        ).encode("utf-8")

        proc = _mock_proc()
        proc.stdout.readline.return_value = truncated_response
        driver = PythonShellDriver()
        _register_session(proc, _config())

        result = driver.python_shell_exec_impl("test_env", "print('x' * 9_000_000)")

        assert result["metadata"]["truncated"] is True
        assert result["metadata"]["truncated_bytes"] == 999


# ---------------------------------------------------------------------------
# check_python_deps — always empty (stdlib only)
# ---------------------------------------------------------------------------


class TestCheckPythonDeps:
    def test_no_python_deps(self):
        assert PythonShellDriver.check_python_deps() == []

    def test_system_dep_check_empty(self):
        assert PythonShellDriver.system_dep_check() == []


# ---------------------------------------------------------------------------
# Bootstrap integration — run the real bootstrap as a subprocess
# ---------------------------------------------------------------------------


class TestBootstrapIntegration:
    """Run the real bootstrap.py under the current interpreter and exercise
    the wire protocol end-to-end. These tests do NOT require any hardware
    and do NOT require any extra Python packages.
    """

    def _spawn_bootstrap(self) -> "subprocess.Popen":
        import subprocess

        bootstrap = Path(__file__).parent.parent.parent / "lablink" / "interfaces" / "python_shell" / "bootstrap.py"
        return subprocess.Popen(
            [sys.executable, "-u", str(bootstrap)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )

    def _send(self, proc, frame: dict) -> dict:
        import subprocess as _sp
        line = json.dumps(frame) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        proc.stdin.flush()
        raw = proc.stdout.readline()
        return json.loads(raw.decode("utf-8"))

    def test_ready_handshake(self):
        proc = self._spawn_bootstrap()
        try:
            raw = proc.stdout.readline()
            handshake = json.loads(raw.decode("utf-8"))
            assert handshake["op"] == "ready"
            assert "python_version" in handshake
            assert "interpreter" in handshake
        finally:
            proc.kill()
            proc.wait()

    def test_exec_clean(self):
        proc = self._spawn_bootstrap()
        try:
            proc.stdout.readline()  # consume READY
            resp = self._send(proc, {"id": "r1", "op": "exec", "code": "x = 42"})
            assert resp["op"] == "exec"
            assert resp["exception"] is None
            assert resp["result"] is None
        finally:
            proc.kill()
            proc.wait()

    def test_eval_clean(self):
        proc = self._spawn_bootstrap()
        try:
            proc.stdout.readline()  # consume READY
            self._send(proc, {"id": "r1", "op": "exec", "code": "x = 42"})
            resp = self._send(proc, {"id": "r2", "op": "eval", "expression": "x * 2"})
            assert resp["result"] == "84"
            assert resp["exception"] is None
        finally:
            proc.kill()
            proc.wait()

    def test_exec_exception_with_traceback(self):
        proc = self._spawn_bootstrap()
        try:
            proc.stdout.readline()  # consume READY
            resp = self._send(proc, {"id": "r1", "op": "exec", "code": "raise ValueError('oops')"})
            assert resp["exception"] is not None
            assert resp["exception"]["type"] == "ValueError"
            assert "oops" in resp["exception"]["message"]
            assert "Traceback" in resp["exception"]["traceback"]
        finally:
            proc.kill()
            proc.wait()

    def test_namespace_persists_across_calls(self):
        proc = self._spawn_bootstrap()
        try:
            proc.stdout.readline()  # consume READY
            self._send(proc, {"id": "r1", "op": "exec", "code": "counter = 0"})
            self._send(proc, {"id": "r2", "op": "exec", "code": "counter += 1"})
            resp = self._send(proc, {"id": "r3", "op": "eval", "expression": "counter"})
            assert resp["result"] == "1"
        finally:
            proc.kill()
            proc.wait()

    def test_stdout_capture(self):
        proc = self._spawn_bootstrap()
        try:
            proc.stdout.readline()  # consume READY
            resp = self._send(proc, {"id": "r1", "op": "exec", "code": "print('captured')"})
            assert resp["stdout"] == "captured\n"
        finally:
            proc.kill()
            proc.wait()

    def test_shutdown(self):
        proc = self._spawn_bootstrap()
        try:
            proc.stdout.readline()  # consume READY
            resp = self._send(proc, {"id": "s1", "op": "shutdown"})
            assert resp["op"] == "shutdown"
            proc.wait(timeout=2)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


import sys
