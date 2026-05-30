"""RestDriver unit tests. httpx is patched at the library level.

Because the driver lazy-imports httpx inside methods, we patch 'httpx.Client'
directly — the lazy import gets the real module, but Client is replaced with
our mock. httpx.BasicAuth is also patched where auth tests need it.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from lablink import session as session_registry
from lablink.base import Session
from lablink.interfaces.rest import RestDriver, RestDriverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> RestDriverConfig:
    defaults = dict(
        alias="test_api",
        type="rest",
        timeout_ms=5000,
        base_url="https://api.example.com/v1",
        auth_type="none",
    )
    defaults.update(overrides)
    return RestDriverConfig(**defaults)


def _mock_response(status_code: int = 200, text: str = '{"ok": true}', content_type: str = "application/json") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.headers = {"content-type": content_type, "x-request-id": "abc123"}
    resp.json.return_value = {"ok": True}
    return resp


def _mock_client(response: MagicMock | None = None) -> MagicMock:
    client = MagicMock()
    if response:
        client.get.return_value = response
        client.post.return_value = response
        client.put.return_value = response
        client.patch.return_value = response
        client.delete.return_value = response
    return client


def _register_session(client: MagicMock, config: RestDriverConfig) -> Session:
    session = Session(
        alias=config.alias, interface_type="rest", raw=client, config=config
    )
    session_registry.register(session)
    return session


@pytest.fixture(autouse=True)
def clear_sessions():
    session_registry.deregister("test_api")
    yield
    session_registry.deregister("test_api")


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_success_registers_session(self):
        client = _mock_client()
        driver = RestDriver()

        with patch("httpx.Client", return_value=client):
            result = driver.connect(_config())

        assert result.success is True
        assert result.interface_type == "rest"
        assert result.identity == "https://api.example.com/v1"
        assert session_registry.is_registered("test_api")

    def test_empty_base_url_fails(self):
        driver = RestDriver()
        with patch("httpx.Client"):
            result = driver.connect(_config(base_url=""))
        assert result.success is False
        assert "base_url" in result.error
        assert not session_registry.is_registered("test_api")

    def test_invalid_scheme_fails(self):
        driver = RestDriver()
        with patch("httpx.Client"):
            result = driver.connect(_config(base_url="ftp://example.com"))
        assert result.success is False
        assert "scheme" in result.error

    def test_already_connected_fails(self):
        client = _mock_client()
        _register_session(client, _config())
        driver = RestDriver()
        with patch("httpx.Client", return_value=client):
            result = driver.connect(_config())
        assert result.success is False
        assert "already open" in result.error

    def test_missing_httpx_fails(self):
        driver = RestDriver()
        with patch("builtins.__import__", side_effect=ImportError("No module named 'httpx'")):
            # Patch find_spec to simulate httpx missing
            pass
        # Simulate missing dep by patching the import inside connect
        import builtins
        real_import = builtins.__import__

        def _bad_import(name, *args, **kwargs):
            if name == "httpx":
                raise ImportError("No module named 'httpx'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_bad_import):
            result = driver.connect(_config())
        assert result.success is False
        assert "httpx" in result.error
        assert "pip install" in result.hint

    def test_bearer_auth_builds_client_with_header(self):
        client = _mock_client()
        driver = RestDriver()
        env = {"MY_TOKEN": "secret123"}
        with patch.dict(os.environ, env), patch("httpx.Client", return_value=client) as mock_cls:
            result = driver.connect(_config(auth_type="bearer", auth_token_env="MY_TOKEN"))
        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["headers"] == {"Authorization": "Bearer secret123"}

    def test_api_key_auth_builds_client_with_header(self):
        client = _mock_client()
        driver = RestDriver()
        env = {"MY_KEY": "apikey456"}
        with patch.dict(os.environ, env), patch("httpx.Client", return_value=client) as mock_cls:
            result = driver.connect(_config(auth_type="api_key", auth_token_env="MY_KEY"))
        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["headers"] == {"X-API-Key": "apikey456"}

    def test_basic_auth_builds_client_with_auth(self):
        client = _mock_client()
        driver = RestDriver()
        env = {"MY_USER": "alice", "MY_PASS": "pw"}
        with patch.dict(os.environ, env), patch("httpx.Client", return_value=client) as mock_cls, \
                patch("httpx.BasicAuth", return_value="BASIC_AUTH") as mock_basic:
            result = driver.connect(_config(
                auth_type="basic",
                auth_username_env="MY_USER",
                auth_password_env="MY_PASS",
            ))
        assert result.success is True
        mock_basic.assert_called_once_with("alice", "pw")
        assert mock_cls.call_args.kwargs["auth"] == "BASIC_AUTH"

    def test_verify_ssl_false_passed_to_client(self):
        client = _mock_client()
        driver = RestDriver()
        with patch("httpx.Client", return_value=client) as mock_cls:
            driver.connect(_config(verify_ssl=False))
        assert mock_cls.call_args.kwargs["verify"] is False


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_success_closes_client(self):
        client = _mock_client()
        session = _register_session(client, _config())
        driver = RestDriver()
        result = driver.disconnect(session)
        assert result.success is True
        client.close.assert_called_once()

    def test_close_error_returns_structured_error(self):
        client = _mock_client()
        client.close.side_effect = RuntimeError("already closed")
        session = _register_session(client, _config())
        driver = RestDriver()
        result = driver.disconnect(session)
        assert result.success is False
        assert "already closed" in result.error


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_valid_config_no_auth(self):
        driver = RestDriver()
        with patch("lablink.interfaces.rest.driver._port_open", return_value=True):
            result = driver.diagnose(_config())
        assert result.ready is True
        assert result.checks["base_url"]["status"] == "ok"

    def test_empty_base_url(self):
        driver = RestDriver()
        result = driver.diagnose(_config(base_url=""))
        assert result.ready is False
        assert any("base_url" in item for item in result.action_items)

    def test_invalid_scheme(self):
        driver = RestDriver()
        result = driver.diagnose(_config(base_url="ftp://example.com"))
        assert result.ready is False
        assert any("scheme" in item for item in result.action_items)

    def test_tcp_unreachable(self):
        driver = RestDriver()
        with patch("lablink.interfaces.rest.driver._port_open", return_value=False):
            result = driver.diagnose(_config())
        assert result.ready is False
        assert any("reach" in item for item in result.action_items)

    def test_bearer_token_env_missing(self):
        driver = RestDriver()
        with patch("lablink.interfaces.rest.driver._port_open", return_value=True), \
                patch.dict(os.environ, {}, clear=True):
            result = driver.diagnose(_config(auth_type="bearer", auth_token_env="MISSING_VAR"))
        assert result.ready is False
        assert any("MISSING_VAR" in item for item in result.action_items)

    def test_bearer_token_env_present(self):
        driver = RestDriver()
        with patch("lablink.interfaces.rest.driver._port_open", return_value=True), \
                patch.dict(os.environ, {"MY_TOK": "val"}):
            result = driver.diagnose(_config(auth_type="bearer", auth_token_env="MY_TOK"))
        assert result.ready is True

    def test_invalid_auth_type(self):
        driver = RestDriver()
        result = driver.diagnose(_config(auth_type="oauth2"))
        assert result.ready is False
        assert any("auth_type" in item for item in result.action_items)


# ---------------------------------------------------------------------------
# rest_get_impl
# ---------------------------------------------------------------------------


class TestRestGet:
    def _setup(self):
        resp = _mock_response()
        client = _mock_client(resp)
        _register_session(client, _config())
        return RestDriver(), client, resp

    def test_success_returns_read_result(self):
        driver, client, resp = self._setup()
        result = driver.rest_get_impl("test_api", "/users")
        assert result["success"] is True
        assert result["raw"] == resp.text
        assert result["metadata"]["status_code"] == 200
        client.get.assert_called_once()

    def test_url_built_correctly(self):
        driver, client, _ = self._setup()
        driver.rest_get_impl("test_api", "/users/42")
        call_args = client.get.call_args
        assert call_args[0][0] == "https://api.example.com/v1/users/42"

    def test_params_forwarded(self):
        driver, client, _ = self._setup()
        driver.rest_get_impl("test_api", "/items", params={"page": 2})
        assert client.get.call_args.kwargs["params"] == {"page": 2}

    def test_extra_headers_forwarded(self):
        driver, client, _ = self._setup()
        driver.rest_get_impl("test_api", "/items", headers={"X-Custom": "val"})
        assert client.get.call_args.kwargs["headers"] == {"X-Custom": "val"}

    def test_timeout_ms_overrides_config(self):
        driver, client, _ = self._setup()
        driver.rest_get_impl("test_api", "/items", timeout_ms=2000)
        assert client.get.call_args.kwargs["timeout"] == 2.0

    def test_no_session_returns_error(self):
        driver = RestDriver()
        result = driver.rest_get_impl("no_such_alias", "/items")
        assert result["success"] is False
        assert "No open session" in result["error"]

    def test_network_error_returns_structured_error(self):
        client = _mock_client()
        client.get.side_effect = ConnectionError("refused")
        _register_session(client, _config())
        driver = RestDriver()
        result = driver.rest_get_impl("test_api", "/items")
        assert result["success"] is False
        assert "refused" in result["error"]

    def test_json_response_decoded(self):
        resp = _mock_response(text='{"count": 5}', content_type="application/json")
        resp.json.return_value = {"count": 5}
        client = _mock_client(resp)
        _register_session(client, _config())
        driver = RestDriver()
        result = driver.rest_get_impl("test_api", "/items")
        assert result["format"] == "json"
        assert result["decoded"] == {"count": 5}

    def test_text_response_not_decoded(self):
        resp = _mock_response(text="hello world", content_type="text/plain")
        client = _mock_client(resp)
        _register_session(client, _config())
        driver = RestDriver()
        result = driver.rest_get_impl("test_api", "/items")
        assert result["format"] == "text"
        assert result["decoded"] is None

    def test_http_404_is_success_true(self):
        resp = _mock_response(status_code=404, text="Not Found", content_type="text/plain")
        client = _mock_client(resp)
        _register_session(client, _config())
        driver = RestDriver()
        result = driver.rest_get_impl("test_api", "/missing")
        assert result["success"] is True
        assert result["metadata"]["status_code"] == 404

    def test_wrong_session_type_returns_error(self):
        # Register a visa session under this alias
        from lablink.interfaces.visa.config import VisaDriverConfig
        visa_config = VisaDriverConfig(
            alias="test_api", type="visa", timeout_ms=5000, resource_string="USB0::INSTR"
        )
        session = Session(alias="test_api", interface_type="visa", raw=MagicMock(), config=visa_config)
        session_registry.register(session)
        driver = RestDriver()
        result = driver.rest_get_impl("test_api", "/items")
        assert result["success"] is False
        assert "visa" in result["error"]


# ---------------------------------------------------------------------------
# rest_post_impl
# ---------------------------------------------------------------------------


class TestRestPost:
    def _setup(self):
        resp = _mock_response(status_code=201, text='{"id": 1}')
        client = _mock_client(resp)
        _register_session(client, _config())
        return RestDriver(), client, resp

    def test_success(self):
        driver, client, _ = self._setup()
        result = driver.rest_post_impl("test_api", "/users", body={"name": "Alice"})
        assert result["success"] is True
        assert result["metadata"]["status_code"] == 201
        client.post.assert_called_once()

    def test_body_sent_as_json(self):
        driver, client, _ = self._setup()
        driver.rest_post_impl("test_api", "/users", body={"name": "Alice"})
        assert client.post.call_args.kwargs["json"] == {"name": "Alice"}

    def test_none_body_sent_as_none(self):
        driver, client, _ = self._setup()
        driver.rest_post_impl("test_api", "/trigger")
        assert client.post.call_args.kwargs["json"] is None

    def test_network_error(self):
        client = _mock_client()
        client.post.side_effect = TimeoutError("timed out")
        _register_session(client, _config())
        driver = RestDriver()
        result = driver.rest_post_impl("test_api", "/users", body={})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# rest_put_impl / rest_patch_impl / rest_delete_impl
# ---------------------------------------------------------------------------


class TestRestMutating:
    def _setup(self, status=200):
        resp = _mock_response(status_code=status, text="")
        client = _mock_client(resp)
        _register_session(client, _config())
        return RestDriver(), client

    def test_put_success(self):
        driver, client = self._setup()
        result = driver.rest_put_impl("test_api", "/users/1", body={"name": "Bob"})
        assert result["success"] is True
        client.put.assert_called_once()
        assert client.put.call_args.kwargs["json"] == {"name": "Bob"}

    def test_patch_success(self):
        driver, client = self._setup()
        result = driver.rest_patch_impl("test_api", "/users/1", body={"name": "Carol"})
        assert result["success"] is True
        client.patch.assert_called_once()

    def test_delete_success(self):
        driver, client = self._setup(status=204)
        result = driver.rest_delete_impl("test_api", "/users/1")
        assert result["success"] is True
        assert result["metadata"]["status_code"] == 204
        client.delete.assert_called_once()

    def test_delete_network_error(self):
        client = _mock_client()
        client.delete.side_effect = ConnectionError("refused")
        _register_session(client, _config())
        driver = RestDriver()
        result = driver.rest_delete_impl("test_api", "/users/1")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# check_python_deps
# ---------------------------------------------------------------------------


class TestCheckPythonDeps:
    def test_httpx_present(self):
        deps = RestDriver.check_python_deps()
        names = [name for name, _ in deps]
        assert "httpx" in names

    def test_returns_list_of_tuples(self):
        deps = RestDriver.check_python_deps()
        assert all(isinstance(d, tuple) and len(d) == 2 for d in deps)


# ---------------------------------------------------------------------------
# URL building helper
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def test_trailing_slash_on_base(self):
        from lablink.interfaces.rest.driver import _build_url
        assert _build_url("https://api.example.com/v1/", "/users") == "https://api.example.com/v1/users"

    def test_no_trailing_slash_on_base(self):
        from lablink.interfaces.rest.driver import _build_url
        assert _build_url("https://api.example.com/v1", "users") == "https://api.example.com/v1/users"

    def test_both_slashes_normalized(self):
        from lablink.interfaces.rest.driver import _build_url
        assert _build_url("https://api.example.com/v1/", "/users/42") == "https://api.example.com/v1/users/42"
