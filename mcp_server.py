"""AgentLink-Visa MCP server entrypoint.

Installed as the 'agentlink-mcp' console script via pip install.
Configure in your MCP client as: {"command": "agentlink-mcp"}
"""

from fastmcp import FastMCP
from agentlink.diagnostics import run_diagnostics
from agentlink.tools import connect, disconnect, query, write

_INSTRUCTIONS = """
You are operating AgentLink-Visa, an MCP server for direct AI agent control of
test and measurement instruments via VISA/SCPI.

## Your role in instrument setup

You own the instrument configuration. Users should not need to create or edit
config files manually. When a user mentions an instrument or asks to connect:

1. Run `agentlink list` (via Bash) to check for existing configs.
2. If no config exists, discover connected instruments:
   python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
   The output is a tuple of VISA resource strings, e.g.:
   ('USB0::0x0699::0x0527::C012345::INSTR',)
   If the tuple is empty, the instrument may be off, disconnected, or require a
   driver — diagnose before asking the user.
3. Write the config file to ~/.agentlink/instruments/<alias>.toml.
   Use the manufacturer and model from the IDN response or the user's description.
   Default termination values work for most instruments:
     read_termination = "\\n"
     write_termination = "\\n"
4. If techmanual.ai is available, search for the model number and extract the
   document_id from the results. Add it to the config as techmanual_document_id.
   This lets future sessions skip the search and go directly to the manual.
5. Call connect_instrument(alias) to open the session and confirm.

## Config file format

~/.agentlink/instruments/<alias>.toml — one file per instrument.

Required: alias, resource_string, manufacturer, model_number, timeout_ms,
          read_termination, write_termination
Optional: description, techmanual_document_id

## Troubleshooting — resolve before escalating to the user

Run diagnose_connection(alias) first. It checks dependencies, the VISA
backend, available resources, and interface-specific reachability (ping,
SCPI port, USB presence, GPIB detection). Use its action_items list to
guide the user step by step.

Common issues:
- Config missing: create it (steps 2-3 above).
- VISA timeout: increase timeout_ms (e.g. 10000) in the config and reconnect.
- Resource string wrong: re-run list_resources() and update the config.
- list_resources() empty: check power, cable, and OS USB permissions.
  On Windows, USB instruments require libusb (pip install libusb-package).
- Session already open: call disconnect_instrument() first.

Only surface an issue to the user if it requires physical action they must
take themselves (e.g. powering on the instrument, plugging in a cable).
"""

mcp = FastMCP("agentlink-visa", instructions=_INSTRUCTIONS)


@mcp.tool()
def connect_instrument(alias: str) -> dict:
    """Open a VISA session to a configured instrument and verify with *IDN?.

    Before calling this, ensure ~/.agentlink/instruments/<alias>.toml exists.
    If it does not, create it — do not ask the user to do so. See server
    instructions for the full setup sequence.

    On success, the response includes techmanual_document_id if set in the config.
    If it is null and techmanual.ai is available, search for the model number,
    extract the document_id, update the config file, and note it for future sessions.

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


@mcp.tool()
def diagnose_connection(alias: str | None = None) -> dict:
    """Check dependencies, VISA backend, and hardware reachability.

    Run this first when a user has trouble connecting to an instrument.
    Returns a structured report with a ready flag and an action_items list
    of concrete steps the user should take to resolve any issues found.

    Checks performed:
    - pyvisa and pyvisa-py installation and versions
    - VISA ResourceManager creation (backend health)
    - list_resources() output (what instruments are visible)
    - Detected interface types: USB, GPIB, LAN, Serial
    - Instrument config directory existence and contents
    - When alias is provided: config validity, interface type, USB presence
      in resource list, TCPIP ping and SCPI port 5025 reachability, or
      GPIB adapter detection.

    Args:
        alias: Optional instrument alias to include targeted checks for that
               specific instrument's config and connection path.
    """
    return run_diagnostics(alias)


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
