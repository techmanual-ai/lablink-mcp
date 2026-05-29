"""LabLink CLI.

Thin wrappers over the same core functions used by the MCP tools.
Intended for development, debugging, and instrument config validation.

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

from lablink.config import (
    get_config_dir,
    list_configs,
    load_config,
    maybe_migrate_legacy_configs,
)
from lablink.diagnostics import run_diagnostics
from lablink.exceptions import ConfigError
from lablink.tools import connect, disconnect, query, write


@click.group()
def cli() -> None:
    """LabLink: AI agent control of T&M equipment via VISA."""
    maybe_migrate_legacy_configs()


@cli.command(name="connect")
@click.argument("alias")
def connect_cmd(alias: str) -> None:
    """Open a VISA session to ALIAS and verify with *IDN?."""
    result = connect(alias)
    if result["success"]:
        click.echo(f"Connected: {result['idn']}")
        if result.get("techmanual_document_id"):
            click.echo(f"techmanual document ID: {result['techmanual_document_id']}", err=True)
    else:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result['hint']}", err=True)
        sys.exit(1)


@cli.command(name="disconnect")
@click.argument("alias")
def disconnect_cmd(alias: str) -> None:
    """Close the VISA session for ALIAS."""
    result = disconnect(alias)
    if result["success"]:
        click.echo(f"Disconnected: {alias}")
    else:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result['hint']}", err=True)
        sys.exit(1)


@cli.command(name="query")
@click.argument("alias")
@click.argument("command")
def query_cmd(alias: str, command: str) -> None:
    """Send COMMAND to ALIAS and print the response."""
    result = connect(alias)
    if not result["success"]:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result['hint']}", err=True)
        sys.exit(1)

    result = query(alias, command)
    if result["success"]:
        click.echo(result["response"])
    else:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result['hint']}", err=True)
        sys.exit(1)

    disconnect(alias)


@cli.command(name="write")
@click.argument("alias")
@click.argument("command")
def write_cmd(alias: str, command: str) -> None:
    """Send COMMAND to ALIAS (no response expected)."""
    result = connect(alias)
    if not result["success"]:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result['hint']}", err=True)
        sys.exit(1)

    result = write(alias, command)
    if result["success"]:
        click.echo(f"Sent: {command}", err=True)
    else:
        click.echo(f"Error: {result['error']}", err=True)
        click.echo(f"Hint: {result['hint']}", err=True)
        sys.exit(1)

    disconnect(alias)


@cli.command(name="list")
def list_cmd() -> None:
    """List all configured instrument aliases."""
    config_dir = get_config_dir()

    if not config_dir.exists():
        click.echo(f"Config directory not found: {config_dir}", err=True)
        click.echo("Create it with: mkdir -p ~/.lablink/devices", err=True)
        sys.exit(1)

    toml_files = sorted(config_dir.glob("*.toml"))
    if not toml_files:
        click.echo("No instrument configs found.", err=True)
        click.echo("Add a <alias>.toml to ~/.lablink/devices/ to get started.", err=True)
        sys.exit(1)

    found_any = False
    for toml_file in toml_files:
        try:
            cfg = load_config(toml_file.stem)
            click.echo(f"{cfg.alias}  {cfg.manufacturer} {cfg.model_number}  {cfg.resource_string}")
            if cfg.description:
                click.echo(f"  {cfg.description}")
            found_any = True
        except ConfigError as exc:
            click.echo(f"Warning: {toml_file.name}: {exc}", err=True)

    if not found_any:
        sys.exit(1)


@cli.command(name="diagnose")
@click.argument("alias", required=False, default=None)
def diagnose_cmd(alias: str | None) -> None:
    """Run connection diagnostics, optionally for a specific ALIAS.

    Checks pyvisa installation, VISA backend health, detected resources,
    and (when ALIAS is given) config validity and interface reachability.
    """
    import json

    report = run_diagnostics(alias)

    if report["ready"]:
        click.echo("All checks passed. Ready to connect.")
    else:
        click.echo(f"{len(report['action_items'])} issue(s) found:", err=True)
        for i, item in enumerate(report["action_items"], 1):
            click.echo(f"  {i}. {item}", err=True)

    click.echo(json.dumps(report, indent=2))


if __name__ == "__main__":
    cli()
