"""REST driver — httpx-based HTTP API client.

RestDriver subclasses LabLinkDriver[RestDriverConfig]. All httpx imports are
lazy — they happen inside methods, never at module load — so the package
imports cleanly without the [rest] extra installed.

It ships five tools: rest_get, rest_post, rest_put, rest_patch,
rest_delete. All return ReadResult with status_code and response headers in
metadata. HTTP 4xx/5xx responses are success=True — the transport worked; the
agent decides what to do with the status code. success=False is reserved for
network-level failures (connection refused, DNS error, timeout).
"""

import importlib.util
import os
import socket
from dataclasses import asdict
from typing import Any
from urllib.parse import urlparse

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
from lablink.interfaces.rest.config import RestDriverConfig
from lablink.redaction import secret_values


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + "/" + path.lstrip("/")


def _log_rest(
    op: str,
    alias: str,
    path: str,
    secrets: set[str],
    *,
    success: bool,
    status_code: int | None = None,
    error: str | None = None,
) -> None:
    """Log a REST op with configured credentials scrubbed from the durable log.

    A token can ride in a query string (``path``) or be echoed back inside an
    error message (which embeds the full URL). Both are free-form fields; passing
    ``secrets`` to ``log_event`` scrubs them at the write boundary. The
    ReadResult returned to the agent is untouched.
    """
    extra = {"status_code": status_code} if status_code is not None else {}
    log_event(op=op, alias=alias, path=path, success=success, error=error, secrets=secrets, **extra)


def _resolve_auth(config: RestDriverConfig) -> tuple[Any, dict[str, str]]:
    """Return (httpx_auth_object_or_None, extra_headers_dict) for the config."""
    import httpx

    if config.auth_type == "bearer":
        token = os.environ.get(config.auth_token_env or "", "")
        return None, {"Authorization": f"Bearer {token}"} if token else {}
    if config.auth_type == "api_key":
        token = os.environ.get(config.auth_token_env or "", "")
        return None, {"X-API-Key": token} if token else {}
    if config.auth_type == "basic":
        username = os.environ.get(config.auth_username_env or "", "")
        password = os.environ.get(config.auth_password_env or "", "")
        if username and password:
            return httpx.BasicAuth(username, password), {}
        return None, {}
    return None, {}


def _response_to_result(response: Any) -> ReadResult:
    """Convert an httpx.Response to a ReadResult.

    HTTP error responses (4xx, 5xx) are success=True — the transport worked.
    The agent checks metadata["status_code"] to determine application-level
    success or failure.
    """
    content_type = response.headers.get("content-type", "")
    fmt = "json" if "json" in content_type else "text"
    decoded: Any = None
    if fmt == "json":
        try:
            decoded = response.json()
        except Exception:
            fmt = "text"

    return ReadResult(
        success=True,
        raw=response.text,
        decoded=decoded,
        format=fmt,
        metadata={
            "status_code": response.status_code,
            "headers": dict(response.headers),
        },
    )


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


class RestDriver(LabLinkDriver[RestDriverConfig]):
    """REST API driver using httpx."""

    type_name = "rest"

    # --- lifecycle ---

    def connect(self, config: RestDriverConfig) -> ConnectResult:
        try:
            import httpx
        except ImportError:
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="rest",
                error="Missing dependency: httpx",
                hint="Run: pip install lablink-mcp[rest]",
            )

        if session_registry.is_registered(config.alias):
            err = f"Session already open for alias '{config.alias}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="rest",
                error=err,
                hint="Call disconnect(alias) first, or use the existing session.",
            )

        if not config.base_url:
            err = "Config field 'base_url' is empty."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="rest",
                error=err,
                hint="Add a 'base_url' field (e.g. 'https://api.example.com/v1') to the config.",
            )

        parsed = urlparse(config.base_url)
        if parsed.scheme not in ("http", "https"):
            err = f"Invalid base_url scheme '{parsed.scheme}': must be http or https."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="rest",
                error=err,
                hint="Ensure base_url starts with 'http://' or 'https://'.",
            )

        auth, extra_headers = _resolve_auth(config)

        client = httpx.Client(
            base_url=config.base_url,
            auth=auth,
            headers=extra_headers,
            verify=config.verify_ssl,
            timeout=config.timeout_ms / 1000,
        )

        session = Session(
            alias=config.alias,
            interface_type="rest",
            raw=client,
            config=config,
        )
        session_registry.register(session)
        identity = config.base_url
        log_event(op="connect", alias=config.alias, identity=identity, success=True)
        return ConnectResult(
            success=True,
            alias=config.alias,
            interface_type="rest",
            identity=identity,
        )

    def disconnect(self, session: Session[RestDriverConfig]) -> Result:
        try:
            session.raw.close()
        except Exception as exc:
            log_event(op="disconnect", alias=session.alias, success=False, error=str(exc))
            return Result(
                success=False,
                error=f"Error closing REST client: {exc}",
                hint="Client may already be closed. The alias is deregistered regardless.",
            )
        log_event(op="disconnect", alias=session.alias, success=True)
        return Result(success=True)

    def diagnose(self, config: RestDriverConfig) -> DiagnosticResult:
        """Stateless per-alias diagnosis: URL format, auth env vars, TCP reachability."""
        checks: dict[str, Any] = {}
        action_items: list[str] = []

        # URL format
        if not config.base_url:
            checks["base_url"] = {"status": "missing", "detail": ""}
            action_items.append("Config field 'base_url' is empty. Set it to the API root URL.")
        else:
            parsed = urlparse(config.base_url)
            if parsed.scheme not in ("http", "https"):
                checks["base_url"] = {"status": "invalid", "detail": config.base_url}
                action_items.append(
                    f"base_url has unsupported scheme '{parsed.scheme}'. Use http or https."
                )
            else:
                checks["base_url"] = {"status": "ok", "detail": config.base_url}

                # TCP reachability
                host = parsed.hostname or ""
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                tcp_ok = _port_open(host, port) if host else False
                checks["tcp_port"] = {
                    "status": "ok" if tcp_ok else "closed",
                    "detail": f"{host}:{port}",
                }
                if not tcp_ok:
                    action_items.append(
                        f"Cannot reach {host}:{port}. Check that the API server is running "
                        "and the host is reachable from this machine."
                    )

        # Auth env vars
        valid_auth = {"none", "bearer", "api_key", "basic"}
        if config.auth_type not in valid_auth:
            checks["auth_type"] = {"status": "invalid", "detail": config.auth_type}
            action_items.append(
                f"Unknown auth_type '{config.auth_type}'. Valid values: {sorted(valid_auth)}."
            )
        else:
            checks["auth_type"] = {"status": "ok", "detail": config.auth_type}

            if config.auth_type in ("bearer", "api_key") and config.auth_token_env:
                present = config.auth_token_env in os.environ
                checks["auth_token_env"] = {
                    "status": "ok" if present else "missing",
                    "detail": config.auth_token_env,
                }
                if not present:
                    action_items.append(
                        f"Environment variable '{config.auth_token_env}' is not set. "
                        f"Export it before connecting."
                    )

            if config.auth_type == "basic":
                for env_field, label in [
                    (config.auth_username_env, "auth_username_env"),
                    (config.auth_password_env, "auth_password_env"),
                ]:
                    if env_field:
                        present = env_field in os.environ
                        checks[label] = {
                            "status": "ok" if present else "missing",
                            "detail": env_field,
                        }
                        if not present:
                            action_items.append(
                                f"Environment variable '{env_field}' is not set. "
                                "Export it before connecting."
                            )

        return DiagnosticResult(
            ready=len(action_items) == 0,
            alias=config.alias,
            interface_type="rest",
            checks=checks,
            action_items=action_items,
        )

    # --- operation logic (shared by MCP tools and CLI) ---

    def _get_session(self, alias: str, op: str) -> tuple[Session | None, dict | None]:
        """Look up the session; return (session, None) or (None, error_dict)."""
        lookup = session_registry.lookup(alias, expected_type="rest")
        if not lookup.found:
            if lookup.wrong_type:
                result = ReadResult(
                    success=False,
                    error=f"Alias '{alias}' has an open {lookup.actual_type} session, not a REST session.",
                    hint=f"Use a {lookup.actual_type}_* tool for this alias, or disconnect and reconfigure with type='rest'.",
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

    def rest_get_impl(
        self,
        alias: str,
        path: str,
        params: dict | None = None,
        headers: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "rest_get")
        if err:
            return err

        secrets = secret_values(session.config)
        url = _build_url(session.config.base_url, path)
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        try:
            response = session.raw.get(url, params=params, headers=headers, timeout=effective_timeout)
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"GET {url} failed: {exc}",
                hint="Check network connectivity and that the API server is running.",
            )
            _log_rest("rest_get", alias, path, secrets, success=False, error=result.error)
            return asdict(result)

        result = _response_to_result(response)
        _log_rest("rest_get", alias, path, secrets, success=True, status_code=response.status_code)
        return asdict(result)

    def rest_post_impl(
        self,
        alias: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "rest_post")
        if err:
            return err

        secrets = secret_values(session.config)
        url = _build_url(session.config.base_url, path)
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        try:
            response = session.raw.post(
                url, json=body, headers=headers, timeout=effective_timeout
            )
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"POST {url} failed: {exc}",
                hint="Check network connectivity and request body format.",
            )
            _log_rest("rest_post", alias, path, secrets, success=False, error=result.error)
            return asdict(result)

        result = _response_to_result(response)
        _log_rest("rest_post", alias, path, secrets, success=True, status_code=response.status_code)
        return asdict(result)

    def rest_put_impl(
        self,
        alias: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "rest_put")
        if err:
            return err

        secrets = secret_values(session.config)
        url = _build_url(session.config.base_url, path)
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        try:
            response = session.raw.put(
                url, json=body, headers=headers, timeout=effective_timeout
            )
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"PUT {url} failed: {exc}",
                hint="Check network connectivity and request body format.",
            )
            _log_rest("rest_put", alias, path, secrets, success=False, error=result.error)
            return asdict(result)

        result = _response_to_result(response)
        _log_rest("rest_put", alias, path, secrets, success=True, status_code=response.status_code)
        return asdict(result)

    def rest_patch_impl(
        self,
        alias: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "rest_patch")
        if err:
            return err

        secrets = secret_values(session.config)
        url = _build_url(session.config.base_url, path)
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        try:
            response = session.raw.patch(
                url, json=body, headers=headers, timeout=effective_timeout
            )
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"PATCH {url} failed: {exc}",
                hint="Check network connectivity and request body format.",
            )
            _log_rest("rest_patch", alias, path, secrets, success=False, error=result.error)
            return asdict(result)

        result = _response_to_result(response)
        _log_rest("rest_patch", alias, path, secrets, success=True, status_code=response.status_code)
        return asdict(result)

    def rest_delete_impl(
        self,
        alias: str,
        path: str,
        headers: dict | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        session, err = self._get_session(alias, "rest_delete")
        if err:
            return err

        secrets = secret_values(session.config)
        url = _build_url(session.config.base_url, path)
        effective_timeout = (timeout_ms or session.config.timeout_ms) / 1000
        try:
            response = session.raw.delete(
                url, headers=headers, timeout=effective_timeout
            )
        except Exception as exc:
            result = ReadResult(
                success=False,
                error=f"DELETE {url} failed: {exc}",
                hint="Check network connectivity and that the resource exists.",
            )
            _log_rest("rest_delete", alias, path, secrets, success=False, error=result.error)
            return asdict(result)

        result = _response_to_result(response)
        _log_rest("rest_delete", alias, path, secrets, success=True, status_code=response.status_code)
        return asdict(result)

    # --- registration ---

    def register_tools(self, mcp) -> None:
        driver = self

        @mcp.tool()
        def rest_get(
            alias: str,
            path: str,
            params: dict | None = None,
            headers: dict | None = None,
            timeout_ms: int | None = None,
        ) -> dict:
            """Send an HTTP GET request to a REST API.

            Appends path to the configured base_url (e.g. base_url="https://api.example.com/v1",
            path="/users/42" → GET https://api.example.com/v1/users/42). The session must
            already be open via connect(alias).

            Args:
                alias: Configured device alias (must be a rest-type alias).
                path: URL path to append to base_url. Leading/trailing slashes normalized.
                params: Query parameters as a dict, e.g. {"page": 1, "limit": 100}.
                headers: Extra per-request headers merged with session auth headers.
                timeout_ms: Per-call timeout in milliseconds; defaults to the config's
                    timeout_ms. Increase for slow or large-response endpoints.

            Returns a ReadResult dict:
                raw: Response body as a UTF-8 string.
                decoded: Parsed JSON body if Content-Type is application/json, else None.
                format: "json" or "text".
                metadata: {"status_code": int, "headers": dict}. HTTP 4xx/5xx responses
                    are success=True — check status_code to detect application errors.
                success: False only on network-level failures (connection refused, DNS
                    error, timeout). A 404 or 500 response is success=True.
            """
            return driver.rest_get_impl(alias, path, params, headers, timeout_ms)

        @mcp.tool()
        def rest_post(
            alias: str,
            path: str,
            body: dict | None = None,
            headers: dict | None = None,
            timeout_ms: int | None = None,
        ) -> dict:
            """Send an HTTP POST request to a REST API.

            Serializes body as JSON (Content-Type: application/json). For APIs that
            expect no body, omit the body argument or pass None.

            Args:
                alias: Configured device alias (must be a rest-type alias).
                path: URL path to append to base_url.
                body: Request body as a dict; JSON-serialized before sending. Pass None
                    for requests with no body.
                headers: Extra per-request headers.
                timeout_ms: Per-call timeout in milliseconds.

            Returns a ReadResult dict with the same shape as rest_get.
                metadata["status_code"] is the primary signal — 201 Created, 200 OK,
                400/422 for validation errors, etc.
            """
            return driver.rest_post_impl(alias, path, body, headers, timeout_ms)

        @mcp.tool()
        def rest_put(
            alias: str,
            path: str,
            body: dict | None = None,
            headers: dict | None = None,
            timeout_ms: int | None = None,
        ) -> dict:
            """Send an HTTP PUT request to a REST API.

            Replaces the resource at path with body. Serializes body as JSON.

            Args:
                alias: Configured device alias (must be a rest-type alias).
                path: URL path to append to base_url.
                body: Replacement resource body as a dict; JSON-serialized.
                headers: Extra per-request headers.
                timeout_ms: Per-call timeout in milliseconds.

            Returns a ReadResult dict. metadata["status_code"] is typically 200 OK
            or 204 No Content on success.
            """
            return driver.rest_put_impl(alias, path, body, headers, timeout_ms)

        @mcp.tool()
        def rest_patch(
            alias: str,
            path: str,
            body: dict | None = None,
            headers: dict | None = None,
            timeout_ms: int | None = None,
        ) -> dict:
            """Send an HTTP PATCH request to a REST API.

            Applies a partial update to the resource at path. Serializes body as JSON.

            Args:
                alias: Configured device alias (must be a rest-type alias).
                path: URL path to append to base_url.
                body: Partial update fields as a dict; JSON-serialized.
                headers: Extra per-request headers.
                timeout_ms: Per-call timeout in milliseconds.

            Returns a ReadResult dict. metadata["status_code"] is typically 200 OK
            or 204 No Content on success.
            """
            return driver.rest_patch_impl(alias, path, body, headers, timeout_ms)

        @mcp.tool()
        def rest_delete(
            alias: str,
            path: str,
            headers: dict | None = None,
            timeout_ms: int | None = None,
        ) -> dict:
            """Send an HTTP DELETE request to a REST API.

            Args:
                alias: Configured device alias (must be a rest-type alias).
                path: URL path of the resource to delete.
                headers: Extra per-request headers.
                timeout_ms: Per-call timeout in milliseconds.

            Returns a ReadResult dict. metadata["status_code"] is typically 204 No Content
            or 200 OK on success. Response body is often empty for DELETE — check
            status_code rather than raw.
            """
            return driver.rest_delete_impl(alias, path, headers, timeout_ms)

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

        @cli_group.group(name="rest")
        def rest_group() -> None:
            """REST API operations."""

        @rest_group.command(name="get")
        @click.argument("alias")
        @click.argument("path")
        def rest_get_cmd(alias: str, path: str) -> None:
            """Send GET PATH to the REST API at ALIAS and print the response body."""
            result = _with_session(alias, lambda: driver.rest_get_impl(alias, path))
            _emit(result, lambda r: click.echo(r["raw"]))

        @rest_group.command(name="post")
        @click.argument("alias")
        @click.argument("path")
        @click.option("--body", default=None, help="JSON body string, e.g. '{\"key\": \"value\"}'")
        def rest_post_cmd(alias: str, path: str, body: str | None) -> None:
            """Send POST PATH to the REST API at ALIAS and print the response body."""
            import json

            parsed_body = json.loads(body) if body else None
            result = _with_session(alias, lambda: driver.rest_post_impl(alias, path, parsed_body))
            _emit(result, lambda r: click.echo(r["raw"]))

    # --- system audit hooks ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        return [
            ("httpx", importlib.util.find_spec("httpx") is not None),
        ]
