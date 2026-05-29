# lablink-mcp

<!-- mcp-name: io.github.techmanual-ai/lablink-mcp -->

MCP server that gives AI agents direct, structured control of test and measurement equipment via PyVISA. Connect your agent to real hardware — oscilloscopes, spectrum analyzers, power supplies, DMMs, and any other VISA-compatible instrument.

**Works standalone.** LabLink is a complete, self-contained tool. Pair it with [techmanual.ai](https://techmanual.ai) to give your agent both hardware access and instrument documentation, but neither product requires the other.

---

## Migration from agentlink-visa

The `agentlink-visa` PyPI package has been renamed to `lablink-mcp`. **This is a
breaking change.** If you previously installed `agentlink-visa`:

1. Uninstall the old package: `pip uninstall agentlink-visa`
2. Install the new one: `pip install lablink-mcp`
3. Update your MCP client config: change `"command": "agentlink-mcp"` to
   `"command": "lablink-mcp"`. The CLI command renames the same way:
   `agentlink ...` → `lablink ...`.
4. Update env vars in any scripts: `AGENTLINK_CONFIG_DIR`, `AGENTLINK_LOG_DIR`,
   and `AGENTLINK_VISA_BACKEND` are now `LABLINK_*`.

**Your existing instrument configs are migrated automatically.** On first run,
`lablink` and `lablink-mcp` will copy every `*.toml` and `*.md` from
`~/.agentlink/instruments/` into `~/.lablink/devices/`, injecting
`type = "visa"` into any migrated TOML that lacks it. A one-line summary is
printed to stderr and a `MIGRATED.txt` marker is written to the old directory.
To skip migration (e.g. on shared machines), set `LABLINK_AUTO_MIGRATE=0`.

See [CHANGELOG.md](CHANGELOG.md) for the full list of renamed names and paths.

---

## Install

```bash
pip install lablink-mcp
```

> **Not yet on PyPI?** Clone the repo and install locally:
> ```bash
> git clone https://github.com/techmanual-ai/lablink-mcp
> cd lablink-mcp
> pip install -e .
> ```

---

## Quick Start

### 1. Create an instrument config

Create the config directory and add one TOML file per instrument:

```bash
mkdir -p ~/.lablink/devices
```

```toml
# ~/.lablink/devices/tek_mso44.toml

alias = "tek_mso44"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms = 5000
read_termination = "\n"
write_termination = "\n"
```

**Find your resource string** by running:

```bash
python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
```

This prints a tuple of connected instruments, e.g. `('USB0::0x0699::0x0527::C012345::INSTR',)`. Copy the string (without quotes) into your config. If the output is empty `()`, check that the instrument is powered on and connected — see [VISA Troubleshooting](#visa-troubleshooting) below.

`read_termination` and `write_termination` are `"\n"` for most instruments. Copy the example above as a safe starting point; change only if your instrument requires it.

See [examples/devices/example_scope.toml](examples/devices/example_scope.toml) for a full template.

### 2. Verify with the CLI

```bash
lablink list            # confirm the config is found
lablink connect tek_mso44   # open a session and check IDN response
```

A successful connect prints the instrument's identity string. If it errors, the hint field will tell you why.

### 3. Add to your MCP client

**Claude Code** — add to `~/.claude.json` (global) or `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "lablink-mcp": {
      "command": "lablink-mcp"
    }
  }
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "lablink-mcp": {
      "command": "lablink-mcp"
    }
  }
}
```

After restarting your MCP client, ask your agent to run `connect_instrument("tek_mso44")` to confirm the server is live.

---

## MCP Tools

| Tool | Description |
|------|-------------|
| `diagnose_connection(alias?)` | Check deps, VISA backend, hardware reachability. Run this first when troubleshooting. |
| `connect_instrument(alias)` | Open VISA session, verify with `*IDN?`, return instrument info |
| `disconnect_instrument(alias)` | Close VISA session |
| `query_instrument(alias, command)` | Send SCPI query, return response string |
| `write_instrument(alias, command)` | Send SCPI command, no response |

All tools return structured dicts. On failure:

```json
{"success": false, "error": "VISA timeout", "hint": "Check that the instrument is powered on."}
```

---

## Instrument Configuration

One TOML file per instrument at `~/.lablink/devices/<alias>.toml`.

**Required fields:**

| Field | Description |
|-------|-------------|
| `alias` | Must match the filename (e.g. `tek_mso44.toml` → `alias = "tek_mso44"`) |
| `resource_string` | VISA address from `list_resources()` |
| `manufacturer` | Instrument manufacturer |
| `model_number` | Model number |
| `timeout_ms` | Communication timeout in milliseconds |
| `read_termination` | Line terminator sent by the instrument (`"\n"` for most) |
| `write_termination` | Line terminator appended to commands (`"\n"` for most) |

**Optional fields:**

| Field | Description |
|-------|-------------|
| `description` | Shown in `lablink list` output |
| `techmanual_document_id` | Links to the instrument's manual in techmanual.ai (see [Using with techmanual.ai](#using-with-techmanualai-optional)) |

**Override the config directory:**

```bash
export LABLINK_CONFIG_DIR=/path/to/your/instruments/
```

---

## CLI Reference

The CLI is a development and debugging tool — not intended for production agent use.

```bash
lablink list                              # list all configured instruments
lablink diagnose                          # check VISA backend and available resources
lablink diagnose tek_mso44               # add alias-specific reachability checks
lablink connect tek_mso44                # open session, print IDN
lablink query tek_mso44 "MEAS:FREQ? CH1" # send query, print response
lablink write tek_mso44 "CH1:SCALE 0.5"  # send command
```

`diagnose` prints a human-readable issue list to stderr and the full JSON report to stdout. Run it first if `connect` fails.

---

## VISA Troubleshooting

**`list_resources()` returns an empty tuple `()`**

- Confirm the instrument is powered on and the cable is connected.
- For USB instruments on macOS, check System Settings → Privacy & Security → USB.
- For USB instruments on Windows, pyvisa-py requires `libusb`. Install via `pip install libusb-package` or download from [libusb.info](https://libusb.info).
- For GPIB instruments, pyvisa-py has limited GPIB support — consider NI-VISA.

**VISA timeout on connect or query**

- Increase `timeout_ms` in your instrument config.
- Confirm no other software (e.g. NI MAX, Keysight Connection Expert) has the port locked.

---

## VISA Backend

LabLink uses **pyvisa-py** by default — a pure-Python implementation with no additional software required.

To use NI-VISA instead (e.g. for GPIB or if you already have it installed):

```bash
export LABLINK_VISA_BACKEND=@ni
```

NI-VISA can be downloaded from [ni.com/visa](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html).

---

## Using with techmanual.ai (optional)

[techmanual.ai](https://techmanual.ai) is a searchable index of technical manuals for T&M equipment. When both MCP servers are loaded, your agent can look up SCPI commands via techmanual and execute them via AgentLink — closing the loop without human intervention.

To enable targeted manual lookups, add `techmanual_document_id` to your instrument config. This is the numeric document ID shown in the techmanual.ai UI for your instrument's manual.

```toml
# Optional — direct link to this instrument's manual in techmanual.ai
techmanual_document_id = 142
```

When this field is set, `connect_instrument()` returns the ID in its response so the agent can fetch the relevant pages without a search query.

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/
```

All tests mock pyvisa — no real hardware required.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LABLINK_CONFIG_DIR` | `~/.lablink/devices/` | Instrument config directory |
| `LABLINK_VISA_BACKEND` | `@py` | pyvisa backend (`@py` or `@ni`) |
