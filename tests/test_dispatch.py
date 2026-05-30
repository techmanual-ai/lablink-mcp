"""Dispatch and registry tests (see docs/ARCHITECTURE.md §6).

Covers:
  - DRIVER_REGISTRY / DRIVER_CONFIG_REGISTRY key sets match.
  - Unknown config `type` raises ConfigError listing valid types.
  - connect() for a deps-missing driver returns a structured install-hint error.
  - session_registry.get(alias, expected_type="ssh") returns None for a VISA session.
  - register_driver_tools() skips a driver whose deps are missing.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

import lablink.mcp_server as mcp_server
from lablink import session as session_registry
from lablink.base import Session
from lablink.exceptions import ConfigError
from lablink.interfaces import DRIVER_CONFIG_REGISTRY, DRIVER_REGISTRY
from lablink.interfaces.visa import VisaDriver, VisaDriverConfig


def test_registry_keys_match():
    assert DRIVER_REGISTRY.keys() == DRIVER_CONFIG_REGISTRY.keys()


def test_unknown_type_raises_listing_valid_types(tmp_path, monkeypatch):
    monkeypatch.setenv("LABLINK_CONFIG_DIR", str(tmp_path))
    (tmp_path / "x.toml").write_bytes(b'type = "modbus"\nalias = "x"\ntimeout_ms = 5000\n')
    with pytest.raises(ConfigError) as exc:
        from lablink.config import load_config

        load_config("x")
    assert "Unknown driver type 'modbus'" in str(exc.value)
    assert "visa" in str(exc.value)  # valid types listed


def test_connect_with_missing_deps_returns_install_hint(monkeypatch):
    config = VisaDriverConfig(alias="scope", type="visa", timeout_ms=5000, resource_string="USB0::INSTR")
    monkeypatch.setattr("lablink.mcp_server.load_config", lambda alias: config)
    monkeypatch.setattr(
        VisaDriver, "check_python_deps", classmethod(lambda cls: [("pyvisa", False)])
    )
    result = mcp_server.do_connect("scope")
    assert result["success"] is False
    assert "pip install lablink-mcp[visa]" in result["hint"]


def test_session_get_wrong_type_returns_none():
    config = VisaDriverConfig(alias="scope", type="visa", timeout_ms=5000, resource_string="USB0::INSTR")
    session_registry.register(
        Session(alias="scope", interface_type="visa", raw=MagicMock(), config=config)
    )
    assert session_registry.get("scope", expected_type="ssh") is None
    assert session_registry.get("scope", expected_type="visa") is not None


def test_register_driver_tools_skips_missing_deps(monkeypatch, capsys):
    monkeypatch.setattr(
        VisaDriver, "check_python_deps", classmethod(lambda cls: [("pyvisa", False)])
    )
    # Fresh server so we can inspect which tools registered.
    from fastmcp import FastMCP

    test_mcp = FastMCP("dispatch-test")
    monkeypatch.setattr(mcp_server, "mcp", test_mcp)
    monkeypatch.setattr(mcp_server, "_driver_instances", {})

    mcp_server.register_driver_tools()

    tools = {t.name for t in asyncio.run(test_mcp.list_tools())}
    assert "visa_query" not in tools
    assert "not loaded" in capsys.readouterr().err


def test_register_driver_tools_registers_when_present(monkeypatch):
    from fastmcp import FastMCP

    test_mcp = FastMCP("dispatch-test")
    monkeypatch.setattr(mcp_server, "mcp", test_mcp)
    monkeypatch.setattr(mcp_server, "_driver_instances", {})

    mcp_server.register_driver_tools()

    tools = {t.name for t in asyncio.run(test_mcp.list_tools())}
    assert {"visa_query", "visa_write"} <= tools


# --- Expanded dispatch contracts -------------------------------------------


def test_diagnose_no_alias_enumerates_all_drivers():
    report = mcp_server.do_diagnose()
    assert report["alias"] is None
    # Every registered driver resolves to exactly one status (no "unknown").
    assert set(report["drivers"].keys()) == set(DRIVER_REGISTRY.keys())
    for entry in report["drivers"].values():
        assert entry["status"] in {"ready", "missing_python", "missing_system"}


def test_register_cli_commands_gated_on_deps(monkeypatch):
    import click

    import lablink.cli as cli_module

    # deps present -> visa subgroup attached
    present_group = click.Group()
    monkeypatch.setattr(cli_module, "get_driver", mcp_server.get_driver)
    cli_module._register_driver_clis(present_group)
    assert "visa" in present_group.commands

    # deps missing -> no subgroup
    monkeypatch.setattr(
        VisaDriver, "check_python_deps", classmethod(lambda cls: [("pyvisa", False)])
    )
    missing_group = click.Group()
    cli_module._register_driver_clis(missing_group)
    assert "visa" not in missing_group.commands
