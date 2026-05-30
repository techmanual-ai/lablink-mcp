"""FastMCP late-registration smoke test.

The LabLink architecture relies on a load-bearing assumption: FastMCP accepts
``@mcp.tool()``-decorated functions applied *inside an instance method called
after server construction* (the ``register_tools(mcp)`` pattern). Standard
FastMCP usage is module-level ``mcp = FastMCP()`` plus top-level decorated
functions; our pattern instantiates a driver, then calls ``register_tools(mcp)``
which decorates inside that method.

If this test fails, the entire per-driver registration architecture is invalid.
See docs/ARCHITECTURE.md §6.
"""

import asyncio

from fastmcp import FastMCP


class _FakeDriver:
    """Stand-in for a real driver — registers a tool inside an instance method."""

    def register_tools(self, mcp: FastMCP) -> None:
        @mcp.tool()
        def fake_query(alias: str, command: str) -> dict:
            return {"alias": alias, "command": command}


def test_late_registered_tool_is_discoverable():
    mcp = FastMCP("smoketest")
    _FakeDriver().register_tools(mcp)

    # FastMCP 3.x exposes list_tools() as an async method returning Tool objects.
    tools = asyncio.run(mcp.list_tools())

    assert any(t.name == "fake_query" for t in tools), (
        "Late-registered tool not discoverable — the register_tools(mcp) "
        "architecture assumption does not hold for this FastMCP version."
    )
