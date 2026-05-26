"""AgentLink-Visa MCP server entrypoint.

Run with: uv run mcp_server.py
Or configure in .mcp.json as: {"command": "uv", "args": ["run", "mcp_server.py"]}
"""

from fastmcp import FastMCP
from agentlink.tools import connect, disconnect, query, write

mcp = FastMCP("agentlink-visa")


@mcp.tool()
def connect_instrument(alias: str) -> dict:
    """Open a VISA session to a configured instrument and verify with *IDN?.

    The instrument must have a config file at ~/.agentlink/instruments/<alias>.toml.
    Returns instrument identity info and the techmanual_document_id if configured,
    which can be used to look up the instrument manual via techmanual.ai.

    Args:
        alias: Instrument alias matching the config filename (e.g. 'tek_mso44').
    """
    return connect(alias)


@mcp.tool()
def disconnect_instrument(alias: str) -> dict:
    """Close the VISA session for a connected instrument.

    Args:
        alias: Instrument alias of an open session.
    """
    return disconnect(alias)


@mcp.tool()
def query_instrument(alias: str, command: str) -> dict:
    """Send a SCPI query to a connected instrument and return the response.

    Use this for commands that return data (queries ending in '?').
    The instrument session must already be open via connect_instrument().

    Args:
        alias: Instrument alias of an open session.
        command: SCPI query string (e.g. 'MEAS:FREQ? CH1').
    """
    return query(alias, command)


@mcp.tool()
def write_instrument(alias: str, command: str) -> dict:
    """Send a SCPI command to a connected instrument with no response expected.

    Use this for configuration commands that do not return data.
    The instrument session must already be open via connect_instrument().

    Args:
        alias: Instrument alias of an open session.
        command: SCPI command string (e.g. 'CH1:SCALE 0.5').
    """
    return write(alias, command)


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
