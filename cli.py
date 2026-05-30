"""LabLink CLI.

Thin wrappers over the same shared dispatch path used by the MCP tools.
Intended for development, debugging, and config validation.

Structure mirrors the MCP tool surface (docs/ARCHITECTURE.md §4):
  - Shared lifecycle commands (always present): connect, disconnect, list,
    diagnose.
  - Per-driver subgroups (present only when the driver's deps are installed),
    registered via each driver's register_cli_commands(): e.g.
    `lablink visa query <alias> "<cmd>"`, `lablink visa write <alias> "<cmd>"`.

Usage:
    lablink list
    lablink diagnose [alias]
    lablink connect <alias>
    lablink disconnect <alias>
    lablink visa query <alias> "<command>"
    lablink visa write <alias> "<command>"
"""

import json
import sys

import click

from lablink.interfaces import DRIVER_REGISTRY

# Shared lifecycle logic + the driver-instance accessor live in mcp_server so
# the CLI reuses the exact same dispatch path as the MCP tools (importing it
# does not start a FastMCP server).
from mcp_server import (
    do_connect,
    do_diagnose,
    do_disconnect,
    do_list_devices,
    get_driver,
)


@click.group()
def cli() -> None:
    """LabLink: AI agent control of lab devices."""


# --- Shared lifecycle commands ---------------------------------------------


@cli.command(name="connect")
@click.argument("alias")
def connect_cmd(alias: str) -> None:
    """Open a session to ALIAS and verify communication."""
    result = do_connect(alias)
    if result["success"]:
        click.echo(f"Connected: {result.get('identity')}")
        if result.get("techmanual_document_ids"):
            click.echo(f"techmanual document IDs: {result['techmanual_document_ids']}", err=True)
    else:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result.get('hint')}", err=True)
        sys.exit(1)


@cli.command(name="disconnect")
@click.argument("alias")
def disconnect_cmd(alias: str) -> None:
    """Close the session for ALIAS."""
    result = do_disconnect(alias)
    if result["success"]:
        click.echo(f"Disconnected: {alias}")
    else:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result.get('hint')}", err=True)
        sys.exit(1)


@cli.command(name="list")
def list_cmd() -> None:
    """List all configured device aliases."""
    devices = do_list_devices()
    if not devices:
        click.echo("No device configs found.", err=True)
        click.echo("Add a <alias>.toml to ~/.lablink/devices/ to get started.", err=True)
        sys.exit(1)

    for d in devices:
        if d["status"] == "invalid":
            click.echo(f"Warning: {d['alias']}.toml: {d.get('error')}", err=True)
            continue
        click.echo(f"{d['alias']}  [{d['type']}]  {d['status']}")
        if d.get("description"):
            click.echo(f"  {d['description']}")


@cli.command(name="diagnose")
@click.argument("alias", required=False, default=None)
def diagnose_cmd(alias: str | None) -> None:
    """Run diagnostics for ALIAS, or a system audit when ALIAS is omitted.

    The structured report is printed as JSON to stdout; a human-readable
    summary of issues (if any) goes to stderr.
    """
    report = do_diagnose(alias)

    if report.get("ready"):
        click.echo("All checks passed. Ready to connect.", err=True)
    else:
        items = report.get("action_items", [])
        click.echo(f"{len(items)} issue(s) found:", err=True)
        for i, item in enumerate(items, 1):
            click.echo(f"  {i}. {item}", err=True)

    click.echo(json.dumps(report, indent=2))


# --- Per-driver subgroups (registered when the driver's deps are present) ---


def _register_driver_clis(group: click.Group = cli) -> None:
    """Attach each deps-present driver's CLI subgroup to `group`.

    Mirrors the MCP-side register_driver_tools() gating: a driver whose Python
    deps are missing does not contribute a CLI subgroup.
    """
    for type_name, cls in DRIVER_REGISTRY.items():
        if any(not present for _, present in cls.check_python_deps()):
            continue
        get_driver(type_name).register_cli_commands(group)


_register_driver_clis()


if __name__ == "__main__":
    cli()
