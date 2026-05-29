# Project Goal

Originally written 2026-05-26 for `agentlink-visa`. Rewritten 2026-05-28 to reflect the LabLink pivot.

## What Is LabLink MCP?

LabLink MCP is a local-first MCP server that gives AI agents direct, structured control over the devices and services they need to talk to in a lab, on a bench, or in the field. The agent connects to a device by alias, sends commands, reads results, and iterates — closing the loop that previously required a human to run generated code.

**The problem it solves:** Today, an agent given a hardware-control task can write the Python — but a human has to run it. The agent never sees what came back, can't iterate, and can't troubleshoot. LabLink removes the human from that loop. The agent picks up a screwdriver of its own.

**Why this is a separate product from techmanual.ai:**
- **LabLink MCP = the execution backbone.** Real control over real devices. The actual product.
- **techmanual.ai = the documentation backbone.** Manufacturer-authored manuals and SCPI references. Optional complement.
- An agent with LabLink alone reaches ~85% on common hardware (it uses training-data knowledge). With techmanual.ai layered on, it reaches ~99% on unfamiliar or unusual hardware. The two are designed to be used together but neither is required by the other.

This framing is recent. Earlier project documentation (and the package name `agentlink-visa`) reflects the opposite: LabLink as the demo, techmanual.ai as the headline. The square-wave oscilloscope demo on real hardware (2026-05-27) flipped this. Future agents: do not revert to the older framing.

---

## Project Ethos

- **Minimal user friction.** One install, one server, opt-in extras per protocol. The user picks `lablink-mcp[visa,ssh]` (or `[all]`) and is done.
- **Honest tool names.** The agent sees `visa_query`, `ssh_exec`, `rest_get` — not a uniform-looking surface that hides per-protocol semantics. Honest names beat overloaded names.
- **Diagnose, don't fail silently.** `diagnose()` is the agent's oracle. When a driver's deps are missing, when an instrument is unreachable, when a config field is wrong — `diagnose` says what's broken and what to do about it.
- **The agent is the operator.** LabLink trusts the agent and the user who is operating it. There is no sandboxing layer between the agent and the device. This is intentional and is the source of LabLink's value.

---

## Design Decisions (Locked)

These decisions were made in the founding design sessions and the 2026-05-28 architectural pivot. They must not be revisited without explicit instruction from the lead developer.

For the **full, current set of locked decisions and their rationale**, see `docs/lablink_plan.md` §2 ("Locked Design Decisions") and §0 ("Revision Notes"). This section is a summary; the plan is the source of truth.

### 2.1 Single Unified Repo
All protocol drivers live in `lablink-mcp`. No sibling repos. Rationale: user-friction reasons — requiring multiple MCP server installs to control a heterogeneous lab is a worse UX than one server with opt-in extras. Different deps are handled by `pyproject.toml` extras and lazy imports.

### 2.2 Tool Surface — Shared Lifecycle + Per-Driver Operations
Two layers:
- **Shared lifecycle tools** (always present): `connect`, `disconnect`, `list_devices`, `diagnose`. Identified by alias; protocol is determined by the alias's config.
- **Per-driver operation tools** (registered only when the driver's Python deps are installed): `visa_query`, `ssh_exec`, `rest_get`, `serial_write`, `python_shell_exec`, etc.

There is no universal `query/write/read` tool. An earlier draft had one; the leaky semantics across protocols made it the worst of both worlds.

### 2.3 Instrument/Device Config Registry
A local directory of per-device TOML config files, one per alias. Default: `~/.lablink/devices/`. Override: `LABLINK_CONFIG_DIR` env var. Each file is named `<alias>.toml`.

Every config has a `type` field that maps to a driver in `DRIVER_REGISTRY`. Driver-specific config schemas (resource_string for VISA, host/port for SSH, base_url for REST, etc.) are documented in `lablink_plan.md` §5.

Alias naming convention: `<vendor>_<model>` (T&M devices) or `<role>_<host>` (compute targets). Lowercase with underscores.

### 2.4 techmanual.ai Integration Pattern
**On-demand (Option B), not auto-inject (Option A).** Auto-injection risks context bloat and irrelevant content. The agent decides when to look things up. The `techmanual_document_ids` field (on documented-device configs like VISA) makes targeted lookups trivial.

### 2.5 Credentials — Env Var Reference Only
Config files never contain secrets. Credentials are referenced by environment variable name. The env var holds the actual value. Applies to all drivers that need auth (SSH, REST).

### 2.6 Session Persistence
Sessions are held open between MCP tool calls (not opened/closed per call). The explicit `connect()`/`disconnect()` pair encodes this model. Sessions live in a module-level `_sessions` dict keyed by alias. Per-driver tools look up their session by alias and verify the type matches.

### 2.7 Error Handling
On any failure, tools return a structured error dict rather than raising. This gives the agent the ability to reason about and retry.

```json
{"success": false, "error": "VISA timeout", "hint": "Check that the instrument is powered on and the resource string is correct."}
```

### 2.8 TMAI_API_KEY Configuration
The techmanual.ai API key is read from the `TMAI_API_KEY` environment variable — same as the claude-plugin for techmanual.ai, zero extra setup for users who already have it configured. LabLink does not require techmanual.ai to function.

### 2.9 Streaming Drivers Deferred to Post-v1
v1 ships zero streaming drivers (no MQTT, no WebSocket, no continuous-data interfaces). The data model has the hooks (`Session.buffer`, `Session.buffer_thread`) and `lablink_plan.md` §6.5 defines the contract future streaming drivers must follow, but no v1 driver exercises this machinery. Streaming work is gated on real hardware to validate against.

---

## Founding Demo Context (Complete)

The agentlink-visa v0.1 founding demo — measure the relative time offset of a square wave on two oscilloscope channels, with and without LabLink — was completed on 2026-05-27. Results were filmed for product video. The demo validated that:

- VISA/SCPI control via PyVISA + FastMCP works reliably on real hardware
- An agent given direct DUT control completes measurement tasks the same model cannot complete via code generation alone
- techmanual.ai meaningfully improves the agent's first-try success rate on unfamiliar instruments

What the demo showed about *value* — that DUT control is the product, not the demo — is what drove the LabLink rearchitecture documented in `docs/lablink_plan.md`.

---

## v1 Scope

v1 ships these drivers, in priority order (see `lablink_plan.md` §8):

| Priority | Driver | Transport | Notes |
|----------|--------|-----------|-------|
| 0 (existing) | `visa` | PyVISA | Refactor of agentlink-visa code |
| 1 | `ssh` | Paramiko | Exec + interactive shell |
| 2 | `rest` | httpx | Full HTTP verb support |
| 3 | `serial` | pyserial | RS232/RS422/RS485 |
| 4 | `python_shell` | subprocess | Long-lived REPL bound to user-supplied interpreter; unlocks vendor SDKs (nidaqmx, picosdk, etc.) |

v1 is bounded by these five drivers and the architecture documented in `lablink_plan.md`. Do not add drivers, abstractions, or features beyond this without explicit instruction.

---

## Non-Goals (Explicit)

- **No server component.** Runs on the user's local machine only. No cloud deployment, no hosted endpoint.
- **No instrument simulation.** Tests mock pyvisa/paramiko/httpx/pyserial. Real drivers require real hardware.
- **No GUI.** CLI only beyond the MCP interface.
- **No streaming drivers in v1.** MQTT, WebSocket, continuous data — all deferred. The contract exists; no driver exercises it.
- **Not a general SCPI/protocol library.** LabLink sends commands and returns responses. It does not parse, validate, or interpret protocol-specific syntax. Protocol knowledge lives in techmanual.ai (for T&M) or the agent's training data.
- **Docker is not a primary install target.** USB/serial passthrough into containers defeats the point of local lab use. Community-contributed `docs/deployment/docker.md` is welcome but not maintained.
- **No primary support for protocols outside the v1 list.** TCP, WebSocket, MQTT, Modbus, OPC-UA, CAN, I2C, SPI, BLE, ZeroMQ, gRPC, SNMP, telnet — all listed for awareness in earlier drafts of `lablink_plan.md` but explicitly out of v1. They will be considered case-by-case after v1 ships.
