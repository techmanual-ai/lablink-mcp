"""AgentLink-Visa CLI.

Thin wrappers over the same core functions used by the MCP tools.
Intended for development, debugging, and instrument config validation.

Usage:
    agentlink connect <alias>
    agentlink query <alias> "<command>"
    agentlink write <alias> "<command>"
    agentlink list
"""

import json
import sys

import click

from agentlink.config import list_configs
from agentlink.tools import connect, disconnect, query, write


@click.group()
def cli() -> None:
    """AgentLink-Visa: AI agent control of T&M equipment via VISA."""


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
    from agentlink.config import get_config_dir, load_config
    from agentlink.exceptions import ConfigError

    config_dir = get_config_dir()
    configs = list_configs()

    if not configs and (not config_dir.exists() or not list(config_dir.glob("*.toml"))):
        click.echo("No instrument configs found.", err=True)
        click.echo(
            "Add a <alias>.toml file to ~/.agentlink/instruments/ to get started.",
            err=True,
        )
        sys.exit(1)

    for cfg in configs:
        click.echo(f"{cfg.alias}")
        click.echo(f"  {cfg.manufacturer} {cfg.model_number}", err=True)
        click.echo(f"  {cfg.resource_string}", err=True)
        if cfg.description:
            click.echo(f"  {cfg.description}", err=True)

    # Warn about any TOML files that failed to load
    if config_dir.exists():
        loaded_aliases = {cfg.alias for cfg in configs}
        for toml_file in sorted(config_dir.glob("*.toml")):
            if toml_file.stem not in loaded_aliases:
                try:
                    load_config(toml_file.stem)
                except ConfigError as exc:
                    click.echo(f"Warning: {toml_file.name}: {exc}", err=True)


if __name__ == "__main__":
    cli()
