<!-- mcp-name: io.github.techmanual-ai/lablink-mcp -->

<h1 align="center">LabLink</h1>

<p align="center">
  Unified &amp; extensible MCP server enabling connectivity to all types of lab equipment
</p>

<p align="center">
  <a href="https://pypi.org/project/lablink-mcp/"><img src="https://img.shields.io/pypi/v/lablink-mcp?style=for-the-badge&logo=pypi&logoColor=white&color=3775A9" alt="PyPI version"></a>
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/techmanual-ai/lablink-mcp/actions"><img src="https://img.shields.io/github/actions/workflow/status/techmanual-ai/lablink-mcp/ci.yml?style=for-the-badge&logo=githubactions&logoColor=white&label=tests" alt="CI status"></a>
  <img src="https://img.shields.io/badge/MCP-server-6E56CF?style=for-the-badge" alt="MCP server">
</p>

<p align="center">
  <a href="https://github.com/techmanual-ai/lablink-mcp/stargazers"><img src="https://img.shields.io/github/stars/techmanual-ai/lablink-mcp?style=flat-square&logo=github" alt="Stars"></a>
  <a href="https://github.com/techmanual-ai/lablink-mcp/releases"><img src="https://img.shields.io/github/v/release/techmanual-ai/lablink-mcp?style=flat-square" alt="Release"></a>
  <img src="https://img.shields.io/badge/drivers-5-6E56CF?style=flat-square" alt="5 drivers">
  <a href="CONTRIBUTING.md"><img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs welcome"></a>
</p>

<p align="center">
  <a href="#-install">Install</a> ·
  <a href="#-quick-start">Quick Start</a> ·
  <a href="#mcp-tools">Tools</a> ·
  <a href="#device-configuration">Configuration</a> ·
  <a href="#cli-reference">CLI</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

<img src="docs/assets/banner.svg" alt="LabLink — one agent, every interface" width="100%">

---

## What is LabLink?

LabLink gives an AI agent direct, structured access to lab hardware and services — without a human in the loop. Connect by alias, send commands, read results, and iterate across any combination of devices in a single session.

- **5 protocol drivers** out of the box: VISA/SCPI, SSH, REST, serial, and a Python subprocess shell
- **Per-protocol tool names** (`visa_query`, `ssh_exec`, `rest_get`) rather than one leaky, one-size-fits-all interface
- Drivers are optional extras, so you install only the ones you need; the server runs with zero installed
- **`diagnose()`** reports what is missing or unreachable before the agent tries to use it
- Adding a driver takes no changes to the core server or CLI

---

## Supported Protocols

Each device is addressed by an **alias** whose config `type` field selects the driver.
Per-driver tools register only when that driver's dependencies are installed.

| Protocol | `type` | Transport | Operation tools | Extra | Hardware-validated |
|----------|--------|-----------|-----------------|-------|:------------------:|
| **VISA / SCPI** | `visa` | PyVISA (USB-TMC, TCPIP, GPIB, Serial) | `visa_query`, `visa_write` | `[visa]` | ✅ |
| **SSH** | `ssh` | Paramiko | `ssh_exec`, `ssh_shell_session`, `ssh_start_stream`, `ssh_read_stream`, `ssh_stop_stream` | `[ssh]` | ✅ |
| **REST** | `rest` | httpx | `rest_get`, `rest_post`, `rest_put`, `rest_patch`, `rest_delete` | `[rest]` | ✅ |
| **Serial** | `serial` | pyserial (RS232/RS422/RS485) | `serial_query`, `serial_write`, `serial_read`, `serial_flush` | `[serial]` | ⚪ |
| **Python shell** | `python_shell` | subprocess REPL | `python_shell_exec`, `python_shell_eval` | `[python_shell]` | ✅ |

An `external` routing stub also lets a device be handled by a vendor-supplied MCP server.
`connect()` returns the routing hints for it.

> **Legend:** ✅ exercised end-to-end on real hardware · ⚪ covered by unit tests with the
> driver library mocked (real use needs real hardware).

---

## 📦 Install

```bash
pip install lablink-mcp          # core only (no drivers)
pip install lablink-mcp[visa]    # + PyVISA
pip install lablink-mcp[ssh]     # + Paramiko
pip install lablink-mcp[all]     # all drivers
```

---

## 🧪 Try it with no hardware

LabLink ships a simulated bench, so you can watch an agent drive instruments
before you connect anything real.

```bash
pip install lablink-mcp[visa,rest,serial,demo]
lablink-sim --write-configs ~/.lablink/devices
```

That starts two simulated instruments behind real protocol front-ends:

```
  FG-100  SCPI    TCPIP0::127.0.0.1::5025::SOCKET
  DAQ-8   SCPI    TCPIP0::127.0.0.1::5026::SOCKET
  DAQ-8   REST    http://127.0.0.1:8080/api/v1
  DAQ-8   serial  /dev/ttys006

  wiring  FG-100:CH1 --coax--> DAQ-8:CH0
```

Nothing is mocked. The simulator speaks SCPI on a socket, so LabLink reaches
it through the same `visa` driver it uses for a bench instrument — and the
same `rest` and `serial` drivers besides. The generator's CH1 is patched into
the DAQ's CH0, so this actually works:

```bash
lablink visa query sim_fgen "SOUR1:FREQ 1000"
lablink visa query sim_fgen "SOUR1:VOLT 2.0"
lablink visa query sim_fgen "OUTP1 ON"
lablink visa query sim_daq  "MEAS:VOLT? (@0)"   # reads the waveform back
```

Add the topology to see *why* those two are connected:

```bash
cp examples/topology_sim.toml ~/.lablink/topology.toml
lablink topology show sim_daq
```

Then ask an agent: *"set sim_fgen CH1 to a 1 kHz 2 V sine, enable the output,
then read sim_daq CH0 and tell me what you expect to see."*

All four aliases address one shared bench, so a change written over SCPI is
visible over REST and serial in the same instant.

---

## 🚀 Quick Start

### 1. See what is attached

```bash
lablink scan
```

`scan` enumerates every VISA resource and serial port on the machine, asks each
one `*IDN?`, and prints what answered:

```
RESOURCE                              TYPE    MANUFACTURER  MODEL   SERIAL   FIRMWARE  SUGGESTED ALIAS
USB0::0x0699::0x0527::C012345::INSTR  USB     TEKTRONIX     MSO44   C012345  1.2.3     tektronix_mso44
/dev/cu.usbserial-1420                serial  -             -       -        -         -

2 device(s) found, 1 identified.
  /dev/cu.usbserial-1420: USB-Serial CH340, VID:PID=1a86:7523 (no *IDN? reply)
```

A device that is found but never answers is still listed. Not everything on a
serial bus speaks SCPI, and knowing the port is there beats hiding it. If a
sweep's driver is not installed, `scan` reports it with the install command
instead of skipping silently.

### 2. Write the configs

```bash
lablink scan --write-configs
lablink connect tektronix_mso44
```

`--write-configs` turns the scan into one `<alias>.toml` per identified device
in `~/.lablink/devices` (pass a directory to write somewhere else), then prints
the path of each file and the `connect` command for it:

```
Wrote /home/you/.lablink/devices/tektronix_mso44.toml
Skipped /dev/cu.usbserial-1420: found but not identified — nothing to write without a manufacturer and model

Connect to one:
  lablink connect tektronix_mso44
```

A file that already exists is skipped, never overwritten — pass `--force` when
you mean to replace it. Two instruments of the same model get their serial
number appended to the second alias. A device that did not answer `*IDN?` is
reported as skipped, because a config full of blanks is worse than no config.

### 3. Or write a device config by hand

One TOML file per device at `~/.lablink/devices/<alias>.toml`. The `type` field
selects the driver. Anything `scan` cannot enumerate, like an SSH host or a raw
TCP socket instrument, is configured this way.

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

`resource_string` and the alias both come straight from `lablink scan` — this
is the file `--write-configs` writes for you.

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

### 4. Verify with the CLI

```bash
lablink list                              # show all configured devices
lablink connect tek_mso44                 # open session, print identity
lablink visa query tek_mso44 "*IDN?"     # send SCPI query
```

### 5. Add to your MCP client

**Claude Code**: add to `~/.claude.json` (global) or `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "lablink-mcp": {
      "command": "lablink-mcp"
    }
  }
}
```

**Claude Desktop**: add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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
| `connect(alias)` | Open session, return identity, device memory, and topology slice |
| `disconnect(alias)` | Close session |
| `list_devices()` | List all configured aliases with status |
| `diagnose(alias?)` | Reachability and dependency check; system audit (with topology warnings) when no alias given |
| `system_topology(alias?)` | Return the bench wiring map; or a single device's slice |

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

**VISA** adds `resource_string`, `manufacturer`, `model_number`, `read_termination`, `write_termination`, `document_ids`

**SSH** adds `host`, `port`, `username`, auth fields (`auth_type`, `auth_ssh_key_path`, `auth_token_env`, etc.)

**REST** adds `base_url`, auth fields

**Serial** adds `serial_port`, `baud_rate`, `data_bits`, `parity`, `stop_bits`, `read_termination`, `write_termination`

**python_shell** adds `python_path` (path to interpreter), `working_dir`

Credentials are always referenced by environment variable name, never stored in config files directly.

---

## CLI Reference

The CLI mirrors the MCP tool surface for development and debugging.

```bash
lablink scan                                    # discover attached devices, identify with *IDN?
lablink scan --write-configs                    # ...and write a config per identified device
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

**`lablink scan` finds nothing (`list_resources()` returns an empty tuple)**

- Confirm the instrument is powered on and the cable is connected.
- For USB instruments on macOS, check System Settings → Privacy & Security → USB.
- For USB instruments on Windows, pyvisa-py requires `libusb`. Install via `pip install libusb-package`.
- For GPIB instruments, pyvisa-py has limited GPIB support; consider NI-VISA.

**VISA timeout on connect or query**

- Increase `timeout_ms` in your instrument config.
- Confirm no other software (e.g. NI MAX, Keysight Connection Expert) has the port locked.

---

## VISA Backend

LabLink uses **pyvisa-py** by default: a pure-Python implementation, so there is nothing else to install.

To use NI-VISA instead (e.g. for GPIB or if you already have it installed):

```bash
export LABLINK_VISA_BACKEND=@ni
```

---

## Pointing the agent at documentation (optional)

Agents guess SCPI. If you have a documentation source the agent can reach — a manual index, an internal wiki, a vendor MCP server — record the relevant document IDs on the instrument's config:

```toml
document_ids = [1291, 1323]   # e.g. user manual, programming guide
```

`connect()` returns them, so the agent can pull the right pages instead of searching or relying on training data. LabLink does not resolve these IDs; they are opaque pointers into whatever index you use.

---

## Control your home (Home Assistant)

[Home Assistant](https://www.home-assistant.io/) is a local-first, open-source smart-home hub. It unifies thousands of devices across ecosystems — Apple Home, Alexa, Google, Zigbee, Z-Wave, Matter — behind one local REST API. LabLink's existing `rest` driver reaches all of it through a single alias, with no extra driver to install.

Create a **Long-Lived Access Token** in Home Assistant (Profile → Security → Long-Lived Access Tokens), export it, and point a REST config at your hub:

```toml
# ~/.lablink/devices/home_assistant.toml
type        = "rest"
alias       = "home_assistant"
timeout_ms  = 10000
base_url    = "http://homeassistant.local:8123/api"
auth_type      = "bearer"
auth_token_env = "HASS_TOKEN"          # export HASS_TOKEN=<your-token>
```

Then your agent can read and command anything HA manages:

```bash
lablink rest get  home_assistant /states                 # every entity's state
lablink rest post home_assistant /services/light/turn_on --body '{"entity_id": "light.kitchen"}'
```

See [examples/configs/rest_home_assistant.toml](examples/configs/rest_home_assistant.toml) for the full template.

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
- **No simulation of your instrument.** No driver has a simulation code path;
  `lablink-sim` is a separate optional bench that the real drivers connect to
  over real protocols, to evaluate LabLink without hardware.
- **No GUI.** The CLI is the only interface beyond MCP.
- **Not a protocol library.** LabLink sends commands and returns responses; it
  does not parse or interpret SCPI or any other protocol syntax. That knowledge
  lives in the agent or in whatever documentation source it can reach.
- **Docker is not a primary install target.** USB/serial passthrough into
  containers defeats the point of local lab use.

Streaming-first protocols (MQTT, WebSocket) and others (Modbus, OPC-UA, CAN, …)
are considered case-by-case as demand surfaces.

---

## Architecture & Contributing

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the data models, driver
contract, dispatch model, and a step-by-step guide to adding a new driver.
Adding a driver requires no changes to `lablink/mcp_server.py` or `lablink/cli.py`, just a new
`lablink/interfaces/<type>/` package and one line in each registry.

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/
```

All tests mock hardware drivers. No real instruments required.

---

## Mapping your system (optional)

Create `~/.lablink/topology.toml` to give the agent a machine-readable map of how your bench is physically wired: which ports connect to which, what signals flow, and what safety limits apply.

```toml
# ~/.lablink/topology.toml
name = "rf_bench"

[[node]]
alias = "siglent_sdg6022"
role  = "signal generator"
[[node]]
alias = "tek_mso44"
role  = "oscilloscope"
[[node]]
id    = "pad_10db"          # passive gear: no alias needed
role  = "10 dB attenuator"

[[link]]
from   = "siglent_sdg6022:OUTPUT1"
to     = "pad_10db:IN"
signal = "stimulus"
params = { impedance_ohm = 50 }

[[link]]
from   = "pad_10db:OUT"
to     = "tek_mso44:CH1"
signal = "stimulus (attenuated)"

[[link]]
from   = "keysight_e36313:CH1"
to     = "dut_serial:12V_IN"
signal = "DC power"
  [[link.constraint]]
  severity = "critical"
  limit    = "voltage <= 13.5"
  note     = "DUT is damaged above 13.5 V on 12V_IN."
```

Call `system_topology()` to retrieve the full map, or `system_topology(alias)` to get one device's wiring slice. `connect()` and `diagnose(alias)` also inject the slice automatically as `topology_context`.

**Constraints are advisory only.** LabLink surfaces `severity` / `limit` / `note` to the agent; it does not and cannot enforce them (it does not parse protocol syntax). The agent is responsible for honoring limits.

Validate the file with:

```bash
lablink topology validate
lablink topology show               # full map
lablink topology show tek_mso44     # one device's slice
```

See [examples/topology.toml](examples/topology.toml) for a full annotated example.

Override the default path:

```bash
export LABLINK_TOPOLOGY_FILE=/path/to/topology.toml
```

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LABLINK_CONFIG_DIR` | `~/.lablink/devices/` | Device config directory |
| `LABLINK_TOPOLOGY_FILE` | `~/.lablink/topology.toml` | System topology file (independent of `LABLINK_CONFIG_DIR`) |
| `LABLINK_VISA_BACKEND` | `@py` | pyvisa backend (`@py` or `@ni`) |
| `LABLINK_LOG_DIR` | `~/.lablink/logs/` | Event log directory; set to `""` to disable |

---

## License

LabLink is released under the [MIT License](LICENSE).

<!-- MCP server VISA instruments · MCP server SCPI · MCP server GPIB · MCP server SSH ·
     MCP server serial device · MCP server REST API · MCP server oscilloscope ·
     MCP server Raspberry Pi · MCP server RS-232 · MCP server RS-485 ·
     MQTT MCP server · Modbus MCP server · WebSocket MCP server · OPC-UA MCP server ·
     CAN bus MCP server · AI agent lab instrument control · AI agent SCPI ·
     AI agent VISA · AI agent oscilloscope · AI agent power supply · AI agent DMM ·
     AI agent NI-DAQmx · AI agent nidaqmx · AI agent PicoScope · AI agent serial port ·
     Claude lab automation · LLM instrument control · lab automation MCP ·
     automated test equipment AI · SCPI automation AI · instrument control AI agent ·
     multi-protocol MCP server · hardware control MCP server · FastMCP lab instruments ·
     bench instrument AI · give AI agent hardware access · AI test automation Python ·
     vendor SDK AI agent · AI agent function generator · AI agent spectrum analyzer ·
     AI agent signal analyzer · AI agent bench instrument ·
     Keysight MCP server · Keysight oscilloscope MCP · Keysight SCPI AI ·
     Keysight InfiniiVision MCP · Keysight signal generator MCP · Keysight power supply MCP ·
     Tektronix MCP server · Tektronix SCPI automation · Tektronix oscilloscope AI ·
     Rigol MCP server · Rigol oscilloscope MCP · Rigol DS1054Z MCP ·
     Siglent MCP server · Siglent oscilloscope AI agent ·
     Rohde Schwarz MCP server · R&S MCP server · R&S oscilloscope AI ·
     Fluke MCP server · Fluke multimeter AI agent ·
     National Instruments MCP · NI DAQ MCP server · NI-DAQmx MCP ·
     Yokogawa MCP server · Anritsu MCP server · LeCroy MCP server ·
     Thorlabs MCP server · Newport MCP server · BK Precision MCP server ·
     Arduino serial MCP · Arduino MCP server · microcontroller MCP server ·
     Home Assistant MCP server · MCP server Home Assistant · Home Assistant AI agent ·
     AI agent smart home · smart home MCP server · control smart home AI ·
     Home Assistant REST API MCP · AI agent home automation · LLM smart home control ·
     AI agent smart lights · AI agent thermostat · AI agent smart lock ·
     Zigbee MCP server · Z-Wave MCP server · Matter MCP server · home automation AI agent -->

