"""External MCP driver config.

ExternalMcpDriverConfig represents a device whose operation tools are provided
by a separate, manufacturer-supplied MCP server rather than by LabLink. LabLink
manages the alias in list_devices() and surfaces routing instructions to the
agent via connect(); all operation calls go directly to the external server's
tools.
"""

from dataclasses import dataclass

from lablink.base import DriverConfig


@dataclass(kw_only=True)
class ExternalMcpDriverConfig(DriverConfig):
    """Config for a device controlled by an external MCP server.

    mcp_server is a freeform label — the name of the external MCP server as
    configured in the agent's MCP client (e.g. 'saleae-logic2-mcp'). It is
    not validated programmatically; it is surfaced to the agent via connect()
    so it knows where to route commands.

    tool_instructions is a freeform string — the agent reads it after connect()
    and uses it to know which tools to call for this device. Keep it concise:
    name the server, list the relevant tool names, note any gotchas.
    """

    mcp_server: str = ""
    tool_instructions: str = ""
