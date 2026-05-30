"""ExternalMcpDriver unit tests.

No third-party deps to mock — the external_mcp driver has none. Tests cover the
connect/disconnect/diagnose lifecycle and the device_memory fallback in the
shared connect tool (do_connect).
"""

import pytest

from lablink import session as session_registry
from lablink.interfaces.external_mcp import ExternalMcpDriver, ExternalMcpDriverConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config(**overrides) -> ExternalMcpDriverConfig:
    defaults = dict(
        alias="test_saleae",
        type="external_mcp",
        timeout_ms=5000,
        mcp_server="saleae-logic2-mcp",
        tool_instructions="Use saleae_start_capture and saleae_stop_capture.",
    )
    defaults.update(overrides)
    return ExternalMcpDriverConfig(**defaults)


@pytest.fixture(autouse=True)
def clear_sessions():
    session_registry.deregister("test_saleae")
    yield
    session_registry.deregister("test_saleae")


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------


class TestConnect:
    def test_success_registers_session(self):
        driver = ExternalMcpDriver()
        result = driver.connect(_config())

        assert result.success is True
        assert result.interface_type == "external_mcp"
        assert session_registry.is_registered("test_saleae")

    def test_identity_includes_mcp_server_name(self):
        driver = ExternalMcpDriver()
        result = driver.connect(_config())

        assert "saleae-logic2-mcp" in result.identity

    def test_identity_fallback_when_no_mcp_server(self):
        driver = ExternalMcpDriver()
        result = driver.connect(_config(mcp_server=""))

        assert result.identity == "external_mcp"

    def test_tool_instructions_in_device_memory(self):
        driver = ExternalMcpDriver()
        result = driver.connect(_config())

        assert result.device_memory == "Use saleae_start_capture and saleae_stop_capture."

    def test_device_memory_none_when_no_instructions(self):
        driver = ExternalMcpDriver()
        result = driver.connect(_config(tool_instructions=""))

        assert result.device_memory is None

    def test_duplicate_connect_returns_error(self):
        driver = ExternalMcpDriver()
        driver.connect(_config())

        result = driver.connect(_config())
        assert result.success is False
        assert "already open" in result.error

    def test_no_python_deps(self):
        assert ExternalMcpDriver.check_python_deps() == []


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    def test_success(self):
        driver = ExternalMcpDriver()
        driver.connect(_config())
        session = session_registry.get_any("test_saleae")

        result = driver.disconnect(session)

        assert result.success is True

    def test_disconnect_does_not_deregister(self):
        # Deregistration is the shared tool's job, not the driver's.
        driver = ExternalMcpDriver()
        driver.connect(_config())
        session = session_registry.get_any("test_saleae")

        driver.disconnect(session)

        assert session_registry.is_registered("test_saleae")


# ---------------------------------------------------------------------------
# diagnose
# ---------------------------------------------------------------------------


class TestDiagnose:
    def test_ready_when_all_fields_present(self):
        driver = ExternalMcpDriver()
        result = driver.diagnose(_config())

        assert result.ready is True
        assert result.checks["mcp_server"]["status"] == "ok"
        assert result.checks["tool_instructions"]["status"] == "ok"
        assert result.action_items == []

    def test_not_ready_when_mcp_server_missing(self):
        driver = ExternalMcpDriver()
        result = driver.diagnose(_config(mcp_server=""))

        assert result.ready is False
        assert result.checks["mcp_server"]["status"] == "missing"
        assert any("mcp_server" in item for item in result.action_items)

    def test_not_ready_when_tool_instructions_missing(self):
        driver = ExternalMcpDriver()
        result = driver.diagnose(_config(tool_instructions=""))

        assert result.ready is False
        assert result.checks["tool_instructions"]["status"] == "missing"
        assert any("tool_instructions" in item for item in result.action_items)

    def test_interface_type_is_external_mcp(self):
        driver = ExternalMcpDriver()
        result = driver.diagnose(_config())

        assert result.interface_type == "external_mcp"
        assert result.alias == "test_saleae"


# ---------------------------------------------------------------------------
# mcp_server device_memory fallback (§6.3.1 extension)
# ---------------------------------------------------------------------------


class TestDeviceMemoryFallback:
    """Verify that do_connect surfaces tool_instructions when no .md file exists."""

    def test_tool_instructions_used_when_no_md_file(self, tmp_path, monkeypatch):
        import mcp_server as srv

        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))

        toml_path = tmp_path / "test_saleae.toml"
        toml_path.write_text(
            "type = 'external_mcp'\n"
            "alias = 'test_saleae'\n"
            "timeout_ms = 5000\n"
            "mcp_server = 'saleae-logic2-mcp'\n"
            "tool_instructions = 'Use saleae_start_capture.'\n"
        )

        result = srv.do_connect("test_saleae")

        assert result["success"] is True
        assert result["device_memory"] == "Use saleae_start_capture."

    def test_md_file_takes_precedence_over_tool_instructions(self, tmp_path, monkeypatch):
        import mcp_server as srv

        monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))

        toml_path = tmp_path / "test_saleae.toml"
        toml_path.write_text(
            "type = 'external_mcp'\n"
            "alias = 'test_saleae'\n"
            "timeout_ms = 5000\n"
            "mcp_server = 'saleae-logic2-mcp'\n"
            "tool_instructions = 'TOML instructions'\n"
        )
        md_path = tmp_path / "test_saleae.md"
        md_path.write_text("## notes\n- from the .md file")

        result = srv.do_connect("test_saleae")

        assert result["success"] is True
        assert result["device_memory"] == "## notes\n- from the .md file"
