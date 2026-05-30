"""External MCP driver.

ExternalMcpDriver is a routing stub. It has no operation tools of its own —
those are provided by a separate manufacturer-supplied MCP server. LabLink's
role is to:
  1. Include the alias in list_devices() alongside native devices.
  2. Return tool_instructions via connect() so the agent knows which external
     tools to call for this device.
  3. Provide a clean connect/disconnect lifecycle so the agent can treat the
     alias uniformly.

No third-party deps; no operation tools registered; no real network connection.
"""

from lablink import session as session_registry
from lablink.base import (
    ConnectResult,
    DiagnosticResult,
    LabLinkDriver,
    Result,
    Session,
)
from lablink.event_logger import log_event
from lablink.interfaces.external_mcp.config import ExternalMcpDriverConfig


class ExternalMcpDriver(LabLinkDriver[ExternalMcpDriverConfig]):
    """Routing stub for devices controlled by an external MCP server."""

    type_name = "external_mcp"

    def connect(self, config: ExternalMcpDriverConfig) -> ConnectResult:
        if session_registry.is_registered(config.alias):
            err = f"Session already open for alias '{config.alias}'."
            log_event(op="connect", alias=config.alias, success=False, error=err)
            return ConnectResult(
                success=False,
                alias=config.alias,
                interface_type="external_mcp",
                error=err,
                hint="Call disconnect(alias) first.",
            )

        session = Session(
            alias=config.alias,
            interface_type="external_mcp",
            raw=None,
            config=config,
        )
        session_registry.register(session)

        identity = f"external_mcp/{config.mcp_server}" if config.mcp_server else "external_mcp"
        log_event(op="connect", alias=config.alias, mcp_server=config.mcp_server, success=True)
        return ConnectResult(
            success=True,
            alias=config.alias,
            interface_type="external_mcp",
            identity=identity,
            # tool_instructions surfaces as device_memory so the agent reads it
            # via the same field it checks for all devices. The shared connect
            # tool uses this as a fallback when no <alias>.md file exists.
            device_memory=config.tool_instructions or None,
        )

    def disconnect(self, session: Session[ExternalMcpDriverConfig]) -> Result:
        log_event(op="disconnect", alias=session.alias, success=True)
        return Result(success=True)

    def diagnose(self, config: ExternalMcpDriverConfig) -> DiagnosticResult:
        checks: dict = {}
        action_items: list[str] = []

        if config.mcp_server:
            checks["mcp_server"] = {"status": "ok", "detail": config.mcp_server}
        else:
            checks["mcp_server"] = {"status": "missing", "detail": "field is empty"}
            action_items.append(
                "Add 'mcp_server' to the config — the name of the external MCP server "
                "as listed in your MCP client configuration."
            )

        if config.tool_instructions:
            checks["tool_instructions"] = {
                "status": "ok",
                "detail": f"{len(config.tool_instructions)} chars",
            }
        else:
            checks["tool_instructions"] = {"status": "missing", "detail": "field is empty"}
            action_items.append(
                "Add 'tool_instructions' to the config — routing hints for the agent "
                "(which server, which tools, any gotchas)."
            )

        return DiagnosticResult(
            ready=len(action_items) == 0,
            alias=config.alias,
            interface_type="external_mcp",
            checks=checks,
            action_items=action_items,
        )

    def register_tools(self, mcp) -> None:
        # External MCP devices have no LabLink operation tools — the external
        # MCP server provides those directly.
        pass

    def register_cli_commands(self, cli_group) -> None:
        # No CLI subgroup; connect/disconnect/list/diagnose cover what's needed.
        pass
