"""LabLink MCP server entrypoint.

Installed as the 'lablink-mcp' console script via pip install.
Configure in your MCP client as: {"command": "lablink-mcp"}
"""

from fastmcp import FastMCP
from lablink.diagnostics import run_diagnostics
from lablink.tools import connect, disconnect, query, write

_INSTRUCTIONS = """
You are operating LabLink, an MCP server for direct AI agent control of
test and measurement instruments via VISA/SCPI.

## Your role in instrument setup

You own the instrument configuration. Users should not need to create or edit
config files manually. When a user mentions an instrument or asks to connect:

1. Run `lablink list` (via Bash) to check for existing configs.
2. If no config exists, discover connected instruments:
   python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
   The output is a tuple of VISA resource strings, e.g.:
   ('USB0::0x0699::0x0527::C012345::INSTR',)
   If the tuple is empty, the instrument may be off, disconnected, or require a
   driver — diagnose before asking the user.
3. Write the config file to ~/.lablink/devices/<alias>.toml.
   Use the manufacturer and model from the IDN response or the user's description.
   Default termination values work for most instruments:
     read_termination = "\\n"
     write_termination = "\\n"
   Name the alias using the convention <manufacturer>_<model>, lowercase with
   underscores (e.g. siglent_sds1104xe, tektronix_mso44, keysight_dsox1204g).
4. If techmanual.ai is available, search for the model number and extract document
   IDs from the results. Instruments typically have two relevant documents: a user
   manual and a programming guide. Add both to the config:
     techmanual_document_ids = [<user_manual_id>, <programming_guide_id>]
   This lets future sessions skip the search and go directly to the manuals.
5. Call connect_instrument(alias) to open the session and confirm.

## Config file format

~/.lablink/devices/<alias>.toml — one file per instrument.

Required: alias, resource_string, manufacturer, model_number, timeout_ms,
          read_termination, write_termination
Optional: description, techmanual_document_ids (list of ints, e.g. [1291, 1323])

The legacy single-ID format (techmanual_document_id = 142) is still accepted
and auto-converted to a one-element list on load.

## Using techmanual.ai

If the techmanual.ai MCP tool is available in your session, use it as the
primary SCPI and instrument reference — do not rely on training data alone
for command syntax. Firmware variants and series differences cause silent
failures that manual lookup prevents.

**On every connect:** check the techmanual_document_ids list in the response.
- If non-empty: query those documents directly before issuing any SCPI.
  Instruments typically have two: a user manual (measurement concepts,
  parameter definitions) and a programming guide (SCPI syntax, ranges).
  Look up both before your first command.
- If empty and techmanual is available: search by manufacturer and
  model_number, identify the relevant documents, then update the config
  with the discovered IDs so future sessions skip this step.

Never start issuing SCPI commands against an unfamiliar instrument without
first consulting available documentation. The time cost of one search query
is far less than a cycle of failed command attempts.

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

## Instrument Memory

Each instrument may have a memory file at
~/.lablink/devices/<alias>.md containing device-specific quirks,
failure modes, and workarounds documented by previous agents.

`connect_instrument` always returns an `instrument_memory` field — read it
before issuing any commands. `diagnose_connection` returns the same field
inside `alias_check` when a valid config is found — read it before
suggesting troubleshooting steps, as the fix may already be documented.

**When to add an entry:** Only when you spent meaningful time on a
non-obvious device-specific issue that would cost a future agent the same
time. Do not add entries for normal SCPI operation.

**Format — strictly enforced:**
- File header: `# <alias> — Instrument Memory`
- One `## <category>` section per topic (e.g., `cursor`, `trigger`,
  `firmware`, `recovery`, `programming_guide`)
- One bullet per quirk, single line:
  `` `affected_command` — symptom — root cause — workaround ``
- No prose paragraphs. No multi-line entries.

Example entry:
```
## cursor
- `CURSOR_X1?` timeout: SDS1104X-E older cursor arch not in prog guide doc 1323; writes work, readback does not → use `PAVA FREQ` for period
```

Write the file directly: ~/.lablink/devices/<alias>.md

## VISA/SCPI Behavior

VISA is a synchronous, session-based protocol. Internalize these behaviors
before issuing commands:

**Write is fire-and-forget.** `write_instrument` returning success confirms
bytes were delivered without a VISA-layer error. It does not confirm the
instrument executed the command or changed state. Any write where the result
matters must be followed by a confirming query.

**A query timeout has three distinct causes — each requires a different
response:**
1. Command not supported by this instrument: try an alternate SCPI path or
   consult the programming guide for this specific model.
2. Wrong syntax for this firmware generation: verify the programming guide
   lists your exact model number, not just the same series. Advanced features
   (cursors, math, decode) often vary across firmware generations; core
   commands (IDN, TDIV, VDIV, measurement parameters) are typically stable.
3. Instrument busy or settling after a write: issue `*OPC?` before the query
   (waits for pending operations to complete) or increase timeout_ms in the
   config.

**`****` in a query response** is a valid instrument reply meaning "no valid
measurement" — signal absent, wrong channel, or parameter not applicable to
the current waveform. It is not a VISA error and does not indicate a command
problem.

**Parallel queries are efficient and correct.** All calls in a single agent
turn execute on the same held-open session. Fire multiple independent queries
in one turn to snapshot instrument state cheaply.

**Session log.** All SCPI I/O is logged to ~/.lablink/logs/YYYY-MM-DD.jsonl
by default. Override the directory with LABLINK_LOG_DIR; disable logging by
setting LABLINK_LOG_DIR to an empty string. Review the log to verify command
history or diagnose failures post-hoc.
"""

mcp = FastMCP("lablink-mcp", instructions=_INSTRUCTIONS)


@mcp.tool()
def connect_instrument(alias: str) -> dict:
    """Open a VISA session to a configured instrument and verify with *IDN?.

    Before calling this, ensure ~/.lablink/devices/<alias>.toml exists.
    If it does not, create it — do not ask the user to do so. See server
    instructions for the full setup sequence.

    On success, the response includes techmanual_document_ids (a list of ints —
    update config if empty) and instrument_memory (device-specific quirks from
    previous sessions, or null). Consult techmanual.ai using those IDs and read
    instrument_memory before issuing any SCPI commands.

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
    Returns a structured report with a ready flag, an action_items list of
    concrete steps to resolve issues, and (when alias is provided and config
    is valid) instrument_memory with device-specific quirks from prior sessions.
    Read instrument_memory before suggesting troubleshooting steps.

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
