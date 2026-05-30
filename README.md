# lablink-mcp

<!-- mcp-name: io.github.techmanual-ai/lablink-mcp -->

MCP server that gives AI agents direct, structured control over lab devices and services — test instruments (VISA/SCPI), remote systems (SSH), web APIs (REST), serial devices, and user-supplied Python environments. One server, many protocol drivers, one install.

**Works standalone.** Pair it with [techmanual.ai](https://techmanual.ai) to give your agent both hardware access and instrument documentation, but neither product requires the other.

---

## Why LabLink

An agent given a hardware-control task can already write the Python to do it — but
someone has to run that code, read back what the instrument returned, and feed it
to the next step. The agent never closes the loop itself.

LabLink removes the human from that loop. The agent connects to a device by alias,
sends commands, reads results, and iterates — measuring, computing, configuring,
and self-correcting across one or many devices in a single session. It picks up a
screwdriver of its own.

The tool surface is honest about each protocol: the agent sees `visa_query`,
`ssh_exec`, `rest_get` rather than one overloaded interface that hides per-protocol
behavior. Only the drivers whose dependencies you install are exposed, and
`diagnose()` tells the agent exactly what's missing or unreachable when something
doesn't work.

> **Pairs with [techmanual.ai](https://techmanual.ai).** An agent with LabLink
> alone handles common hardware well using its own knowledge. Add techmanual.ai —
> a searchable index of manufacturer manuals and SCPI references — and the agent
> can look up the right command for an unfamiliar instrument, send it via LabLink,
> observe the response, and iterate. The two are designed to be used together;
> neither requires the other.

---

## Install

```bash
pip install lablink-mcp          # core only (no drivers)
pip install lablink-mcp[visa]    # + PyVISA
pip install lablink-mcp[ssh]     # + Paramiko
pip install lablink-mcp[all]     # all drivers
```

> **Not yet on PyPI?** Clone the repo and install locally:
> ```bash
> git clone https://github.com/techmanual-ai/lablink-mcp
> cd lablink-mcp
> pip install -e ".[all]"
> ```

---

## Quick Start

### 1. Create a device config

One TOML file per device at `~/.lablink/devices/<alias>.toml`. The `type` field selects the driver.

**VISA instrument:**

```toml
# ~/.lablink/devices/tek_mso44.toml
type        = "visa"
alias       = "tek_mso44"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms  = 5000
description = "4-channel mixed signal oscilloscope"
```

Find your resource string:
```bash
python -c "import pyvisa; print(pyvisa.ResourceManager('@py').list_resources())"
```

**SSH host:**

```toml
# ~/.lablink/devices/rpi_dev.toml
type        = "ssh"
alias       = "rpi_dev"
host        = "192.168.1.42"
port        = 22
username    = "pi"
auth_type   = "ssh_key"
auth_ssh_key_path = "~/.ssh/id_rsa"
timeout_ms  = 10000
```

See [examples/configs/](examples/configs/) for templates for all drivers.

### 2. Verify with the CLI

```bash
lablink list                              # show all configured devices
lablink connect tek_mso44                 # open session, print identity
lablink visa query tek_mso44 "*IDN?"     # send SCPI query
```

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

---

## MCP Tools

### Shared lifecycle (all drivers)

| Tool | Description |
|------|-------------|
| `connect(alias)` | Open session, return identity and device memory |
| `disconnect(alias)` | Close session |
| `list_devices()` | List all configured aliases with status |
| `diagnose(alias?)` | Reachability and dependency check; system audit when no alias given |

### Per-driver operation tools

| Driver | Tools |
|--------|-------|
| `visa` | `visa_query`, `visa_write` |
| `ssh` | `ssh_exec`, `ssh_shell_session`, `ssh_start_stream`, `ssh_read_stream`, `ssh_stop_stream` |
| `rest` | `rest_get`, `rest_post`, `rest_put`, `rest_patch`, `rest_delete` |
| `serial` | `serial_query`, `serial_write`, `serial_read`, `serial_flush` |
| `python_shell` | `python_shell_exec`, `python_shell_eval` |

Per-driver tools are only registered when that driver's dependencies are installed. All tools return structured dicts. On failure:

```json
{"success": false, "error": "VISA timeout", "hint": "Check that the instrument is powered on."}
```

---

## Device Configuration

One TOML file per device at `~/.lablink/devices/<alias>.toml`. Override the directory:

```bash
export LABLINK_CONFIG_DIR=/path/to/devices/
```

### Base fields (all drivers)

| Field | Required | Description |
|-------|----------|-------------|
| `type` | yes | Driver: `visa`, `ssh`, `rest`, `serial`, `python_shell` |
| `alias` | yes | Must match the filename (e.g. `tek_mso44.toml` → `alias = "tek_mso44"`) |
| `timeout_ms` | yes | Default communication timeout in milliseconds |
| `description` | no | Shown in `lablink list` output |

### Per-driver extras

See [examples/configs/](examples/configs/) for complete templates.

**VISA** — adds `resource_string`, `manufacturer`, `model_number`, `read_termination`, `write_termination`, `techmanual_document_ids`

**SSH** — adds `host`, `port`, `username`, auth fields (`auth_type`, `auth_ssh_key_path`, `auth_token_env`, etc.)

**REST** — adds `base_url`, auth fields

**Serial** — adds `serial_port`, `baud_rate`, `data_bits`, `parity`, `stop_bits`, `read_termination`, `write_termination`

**python_shell** — adds `python_path` (path to interpreter), `working_dir`

Credentials are always referenced by environment variable name — never stored in config files directly.

---

## CLI Reference

The CLI mirrors the MCP tool surface for development and debugging.

```bash
lablink list                                    # list all configured devices
lablink diagnose                                # system dep check (all drivers)
lablink diagnose tek_mso44                      # device-specific reachability check
lablink connect tek_mso44                       # open session, print identity
lablink disconnect tek_mso44                    # close session

lablink visa query tek_mso44 "*IDN?"            # SCPI query
lablink visa write tek_mso44 "CH1:SCALE 0.5"   # SCPI write

lablink ssh exec rpi_dev "uname -a"             # run SSH command

lablink rest get my_api /status                 # HTTP GET
lablink rest post my_api /jobs --body '{"n":1}' # HTTP POST

lablink serial query my_device "MEAS?"          # serial write + read
```

Per-protocol commands appear only when that driver's deps are installed.

---

## VISA Troubleshooting

**`list_resources()` returns an empty tuple `()`**

- Confirm the instrument is powered on and the cable is connected.
- For USB instruments on macOS, check System Settings → Privacy & Security → USB.
- For USB instruments on Windows, pyvisa-py requires `libusb`. Install via `pip install libusb-package`.
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

---

## Using with techmanual.ai (optional)

[techmanual.ai](https://techmanual.ai) is a searchable index of technical manuals for T&M equipment. When both MCP servers are loaded, your agent can look up SCPI commands and execute them without human intervention.

Add `techmanual_document_ids` to your VISA config to enable targeted lookups:

```toml
techmanual_document_ids = [1291, 1323]   # user manual, programming guide
```

When this field is set, `connect()` returns the IDs so the agent can fetch relevant pages without a search query.

---

## Scope

LabLink ships five protocol drivers — `visa`, `ssh`, `rest`, `serial`, and
`python_shell` — on a shared multi-driver dispatch core. (An `external` routing
stub also lets a device be handled by a vendor-supplied MCP server.) GPIB is
covered by `visa` through PyVISA; RS232/RS422/RS485 are electrical variants of the
one `serial` driver.

**Deliberately out of scope:**

- **No server component.** LabLink runs on your local machine. There is no cloud
  deployment or hosted endpoint.
- **No instrument simulation.** Tests mock the driver libraries; real use needs
  real hardware.
- **No GUI.** The CLI is the only interface beyond MCP.
- **Not a protocol library.** LabLink sends commands and returns responses; it
  does not parse or interpret SCPI or any other protocol syntax. That knowledge
  lives in the agent or in [techmanual.ai](https://techmanual.ai).
- **Docker is not a primary install target.** USB/serial passthrough into
  containers defeats the point of local lab use.

Streaming-first protocols (MQTT, WebSocket) and others (Modbus, OPC-UA, CAN, …)
are considered case-by-case as demand surfaces.

---

## Architecture & Contributing

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data models, driver
contract, dispatch model, and a step-by-step guide to adding a new driver.
Adding a driver requires no changes to `lablink/mcp_server.py` or `lablink/cli.py` — just a new
`lablink/interfaces/<type>/` package and one line in each registry.

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/
```

All tests mock hardware drivers — no real instruments required.

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LABLINK_CONFIG_DIR` | `~/.lablink/devices/` | Device config directory |
| `LABLINK_VISA_BACKEND` | `@py` | pyvisa backend (`@py` or `@ni`) |
| `LABLINK_LOG_DIR` | `~/.lablink/logs/` | Event log directory; set to `""` to disable |
| `TMAI_API_KEY` | — | techmanual.ai API key for agent-directed manual lookups |
