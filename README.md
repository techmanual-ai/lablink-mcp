# agentlink-visa

MCP server that gives AI agents direct, structured control over test and measurement equipment via PyVISA. The execution backbone that complements [techmanual.ai](https://techmanual.ai)'s knowledge backbone.

**The loop:** An agent with both MCP plugins loaded can look up SCPI commands via techmanual.ai _and_ execute them via AgentLink-Visa — no human required to run code.

---

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv)
- A VISA-capable instrument connected via USB, GPIB, LAN, or serial

---

## Installation

```bash
git clone https://github.com/techmanual-ai/agentlink-visa
cd agentlink-visa
uv venv
uv pip install -r requirements.txt
# Install the package so the CLI and MCP entry points are on your PATH
uv pip install -e .
```

---

## VISA Backend

AgentLink-Visa uses **pyvisa-py** by default — a pure-Python VISA implementation that requires no additional software.

**Default (pyvisa-py):** Works out of the box for USB and most LAN instruments. No extra installation needed.

**NI-VISA override:** If you have NI-VISA installed and prefer it (e.g. for GPIB), set:

```bash
export AGENTLINK_VISA_BACKEND=@ni
```

Or add it to your `.env` file. NI-VISA can be downloaded from [ni.com/visa](https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html).

**Find your resource string:**

```bash
uv run python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
```

---

## Instrument Configuration

Create one TOML file per instrument at `~/.agentlink/instruments/<alias>.toml`.

```toml
alias = "tek_mso44"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms = 5000
read_termination = "\n"
write_termination = "\n"

# Optional — links directly to the instrument manual in techmanual.ai
# techmanual_document_id = 142

# Optional — shown in 'agentlink list' output
# description = "4-channel mixed signal oscilloscope, bench 3"
```

See [examples/instruments/example_scope.toml](examples/instruments/example_scope.toml) for a full template.

**Override the config directory:**

```bash
export AGENTLINK_CONFIG_DIR=/path/to/your/instruments/
```

---

## MCP Server Setup

After installing with `uv pip install -e .`, the `agentlink-mcp` entry point is available. Configure your MCP client to launch it using `uv --directory` so the correct virtual environment is always resolved:

**Claude Code** — add to `.mcp.json` in your project root (or `~/.claude.json` for global access):

```json
{
  "mcpServers": {
    "agentlink-visa": {
      "command": "uv",
      "args": ["--directory", "/path/to/agentlink-visa", "run", "agentlink-mcp"]
    }
  }
}
```

**Claude Desktop** — add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentlink-visa": {
      "command": "uv",
      "args": ["--directory", "/path/to/agentlink-visa", "run", "agentlink-mcp"]
    }
  }
}
```

Replace `/path/to/agentlink-visa` with the absolute path to this repo on your machine.

### MCP Tools

| Tool | Description |
|------|-------------|
| `connect_instrument(alias)` | Open VISA session, verify with `*IDN?`, return instrument info |
| `disconnect_instrument(alias)` | Close VISA session |
| `query_instrument(alias, command)` | Send SCPI query, return response string |
| `write_instrument(alias, command)` | Send SCPI command, no response |

All tools return structured dicts. On failure:
```json
{"success": false, "error": "VISA timeout", "hint": "Check that the instrument is powered on."}
```

---

## CLI (Development & Debugging)

```bash
# List configured instruments
agentlink list

# Connect and verify IDN
agentlink connect tek_mso44

# Send a query
agentlink query tek_mso44 "MEAS:FREQ? CH1"

# Send a write command
agentlink write tek_mso44 "CH1:SCALE 0.5"
```

Diagnostic output goes to stderr; command output goes to stdout.

---

## Running Tests

```bash
uv pip install -e ".[dev]"
uv run pytest tests/
```

All tests mock pyvisa — no real hardware required.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `AGENTLINK_CONFIG_DIR` | `~/.agentlink/instruments/` | Instrument config directory |
| `AGENTLINK_VISA_BACKEND` | `@py` | pyvisa backend (`@py` or `@ni`) |
| `TMAI_API_KEY` | _(none)_ | techmanual.ai API key |
