"""LabLink CLI.

Thin wrappers over the same shared dispatch path used by the MCP tools.
Intended for development, debugging, and config validation.

NOTE (Phase 0b): the command structure is still the flat agentlink-visa shape
(`lablink query`, `lablink write`). The architectural rewrite into per-driver
subgroups (`lablink visa query ...`, via each driver's register_cli_commands)
is Phase 0c. The flat query/write commands here are VISA-only.

Usage:
    lablink list
    lablink diagnose [alias]
    lablink connect <alias>
    lablink query <alias> "<command>"
    lablink write <alias> "<command>"
"""

import json
import sys

import click

from lablink.config import maybe_migrate_legacy_configs

# Shared lifecycle logic + the driver-instance accessor live in mcp_server so
# the CLI reuses the exact same dispatch path as the MCP tools (no FastMCP
# server is started by importing it; driver tools register only in main()).
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
    maybe_migrate_legacy_configs()


@cli.command(name="connect")
@click.argument("alias")
def connect_cmd(alias: str) -> None:
    """Open a session to ALIAS and verify communication."""
    result = do_connect(alias)
    if result["success"]:
        click.echo(f"Connected: {result.get('identity')}")
        if result.get("techmanual_document_ids"):
            click.echo(
                f"techmanual document IDs: {result['techmanual_document_ids']}", err=True
            )
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


@cli.command(name="query")
@click.argument("alias")
@click.argument("command")
def query_cmd(alias: str, command: str) -> None:
    """Send a SCPI query COMMAND to ALIAS and print the response (VISA)."""
    result = do_connect(alias)
    if not result["success"]:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result.get('hint')}", err=True)
        sys.exit(1)

    qresult = get_driver("visa").visa_query_impl(alias, command)
    if qresult["success"]:
        click.echo(qresult["raw"])
    else:
        click.echo(f"Error: {qresult['error']}", err=True)
        click.echo(f"Hint: {qresult.get('hint')}", err=True)
        do_disconnect(alias)
        sys.exit(1)

    do_disconnect(alias)


@cli.command(name="write")
@click.argument("alias")
@click.argument("command")
def write_cmd(alias: str, command: str) -> None:
    """Send a SCPI write COMMAND to ALIAS, no response expected (VISA)."""
    result = do_connect(alias)
    if not result["success"]:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result.get('hint')}", err=True)
        sys.exit(1)

    wresult = get_driver("visa").visa_write_impl(alias, command)
    if wresult["success"]:
        click.echo(f"Sent: {command}", err=True)
    else:
        click.echo(f"Error: {wresult['error']}", err=True)
        click.echo(f"Hint: {wresult.get('hint')}", err=True)
        do_disconnect(alias)
        sys.exit(1)

    do_disconnect(alias)


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
        line = f"{d['alias']}  [{d['type']}]  {d['status']}"
        click.echo(line)
        if d.get("description"):
            click.echo(f"  {d['description']}")


@cli.command(name="diagnose")
@click.argument("alias", required=False, default=None)
def diagnose_cmd(alias: str | None) -> None:
    """Run diagnostics for ALIAS, or a system audit when ALIAS is omitted."""
    report = do_diagnose(alias)

    if report.get("ready"):
        click.echo("All checks passed. Ready to connect.")
    else:
        items = report.get("action_items", [])
        click.echo(f"{len(items)} issue(s) found:", err=True)
        for i, item in enumerate(items, 1):
            click.echo(f"  {i}. {item}", err=True)

    click.echo(json.dumps(report, indent=2))


if __name__ == "__main__":
    cli()
