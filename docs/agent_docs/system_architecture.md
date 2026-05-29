# System Architecture

This document describes both the **current shipped architecture** (agentlink-visa, single-driver) and the **LabLink target architecture** (multi-driver) that Phase 0 migration is moving toward. They differ significantly. For most design questions, defer to `docs/lablink_plan.md` — it is the authoritative spec for the target.

---

## 0. Two Architectures, One Document

| | Current (on disk now) | Target (post Phase 0) |
|---|---|---|
| Package name | `agentlink` | `lablink` |
| Repo / PyPI | `agentlink-visa` | `lablink-mcp` |
| Config dir | `~/.agentlink/instruments/` | `~/.lablink/devices/` |
| Drivers | VISA only | VISA, SSH, REST, serial, python_shell |
| Tool surface | `connect`, `disconnect`, `query`, `write`, `diagnose_connection` (VISA-specific) | `connect`, `disconnect`, `list_devices`, `diagnose` (shared) + per-driver tools (`visa_query`, `ssh_exec`, ...) |
| Tool dispatch | Direct (single driver) | Shared lifecycle dispatches via `DRIVER_REGISTRY[type]`; per-driver tools self-register |
| Config schema | Single TOML shape, no `type` field | Per-driver schemas resolved via `DRIVER_CONFIG_REGISTRY[type]` |
| Session model | `_sessions: dict[str, pyvisa.Resource]` | `_sessions: dict[str, Session[ConfigT]]` |

Sections 1–4 describe **target** architecture (post-pivot). Section 5 documents the **current** layout for agents working in the pre-migration codebase.

---

## 1. High-Level Overview (Target)

LabLink MCP is a local-first Python application. It provides two interfaces to the same per-driver core: an MCP server (primary, for AI agents) and a CLI (secondary, for development and debugging).

**Core stack:**
- **Runtime:** Python 3.10+
- **MCP framework:** FastMCP (stdio transport)
- **Drivers:** PyVISA (VISA), Paramiko (SSH), httpx (REST), pyserial (serial), stdlib subprocess (python_shell)
- **Config format:** TOML (tomllib / tomli)
- **CLI:** Click
- **Package manager:** uv

---

## 2. Directory Structure (Target)

```text
lablink-mcp/
├── docs/
│   ├── lablink_plan.md              # authoritative architectural plan
│   └── agent_docs/
│       ├── readme_agent.md
│       ├── project_goal.md
│       ├── agent_development.md
│       ├── current_status.md
│       └── system_architecture.md   # this file
├── lablink/
│   ├── __init__.py
│   ├── base.py                      # ABC, all data models, Session[ConfigT], shared helpers
│   ├── config.py                    # Base config loader; uses DRIVER_CONFIG_REGISTRY
│   ├── session.py                   # _sessions dict, register/deregister/get helpers
│   ├── event_logger.py              # Generalized from scpi_logger.py; logs all tool events
│   ├── exceptions.py                # ConfigError, SessionError, DriverError
│   └── interfaces/
│       ├── __init__.py              # DRIVER_REGISTRY, DRIVER_CONFIG_REGISTRY
│       ├── visa/                    # driver.py + config.py per driver
│       ├── ssh/
│       ├── rest/
│       ├── serial/
│       └── python_shell/
├── mcp_server.py                    # FastMCP entrypoint; registers shared tools + dispatches driver.register_tools()
├── cli.py                           # Click root; dispatches to driver.register_cli_commands()
├── tests/
│   ├── conftest.py
│   ├── test_shared_tools.py
│   ├── test_dispatch.py
│   └── interfaces/                  # one test file per driver
├── examples/configs/                # one example .toml per driver
├── pyproject.toml
└── README.md
```

There is no `lablink/tools.py`. Shared lifecycle tools live in `mcp_server.py`. Per-driver operation tools live inside each `lablink/interfaces/<type>/driver.py` and self-register via `register_tools(mcp)`.

For the full target directory layout including stubs of every file, see `lablink_plan.md` §7.

---

## 3. Core Components (Target)

### A. Base Module (`lablink/base.py`)
Houses all type definitions and the driver ABC. Specifically:

- **Data models:** `Result`, `ReadResult`, `ConnectResult`, `DiagnosticResult`, `SystemDepStatus`
- **Config types:** `DriverConfig` (base), `AuthConfig` (mixin for SSH/REST), `DocumentedConfig` (mixin for VISA — carries `techmanual_document_ids`)
- **Session:** `Session[ConfigT]` (Generic over the driver's config subclass)
- **Driver ABC:** `LabLinkDriver[ConfigT]` — abstract methods `connect`, `disconnect`, `diagnose`, `register_tools`; classmethods `check_python_deps`, `system_dep_check`
- **Shared helpers:** `session_registry.get(alias, expected_type=...)`, tool-error wrappers, event-logging shortcuts

Field shapes and ABC method contracts are documented in `lablink_plan.md` §3 and §4.

### B. Config Loader (`lablink/config.py`)
- Reads `~/.lablink/devices/<alias>.toml` (or `$LABLINK_CONFIG_DIR/<alias>.toml`).
- Reads the `type` field; looks up `DRIVER_CONFIG_REGISTRY[type]`; instantiates the driver-specific config subclass with all TOML fields.
- Raises `ConfigError` on unknown `type` with a message listing valid types.
- Calls `Path(value).expanduser()` on every path field at load time (TOML does not auto-expand tildes).
- Implements Phase 0a auto-migration: on first load, if `~/.lablink/devices/` does not exist and `~/.agentlink/instruments/` does, copies `.toml` and `.md` files and injects `type = "visa"` into any TOML lacking it. Disabled by setting `LABLINK_AUTO_MIGRATE=0`.
- `load_device_memory(alias)` reads `<config_dir>/<alias>.md` and returns content or `None`. Never raises.

### C. Session Manager (`lablink/session.py`)
- Module-level `_sessions: dict[str, Session]`.
- `register(session)` adds, `deregister(alias)` removes, `get(alias, expected_type=None)` looks up with optional type check.
- The driver's `connect()` constructs the `Session` and calls `register()`. The shared `disconnect()` tool calls `deregister()` after the driver's `disconnect()` returns, regardless of return value.
- Per-driver tools call `get(alias, expected_type=cls.type_name)` — returns `None` on missing session or type mismatch, defending against agent-typing bugs.

### D. Driver Implementations (`lablink/interfaces/<type>/`)
Each driver lives in its own subpackage with:
- `driver.py` — subclass of `LabLinkDriver[<ConfigT>]`. Implements `connect`, `disconnect`, `diagnose`, `register_tools`, and (where applicable) `register_cli_commands`. Lazy-imports the third-party dep inside `connect()`.
- `config.py` — driver-specific `DriverConfig` subclass. Inherits `AuthConfig` if the driver needs auth, `DocumentedConfig` if it targets devices with manuals on techmanual.ai.

Per-driver tool surface and behavior is defined in `lablink_plan.md` §9 (one subsection per driver).

### E. MCP Server (`mcp_server.py`)
FastMCP stdio entrypoint. Startup flow:
1. Instantiate FastMCP server.
2. Register shared lifecycle tools: `connect`, `disconnect`, `list_devices`, `diagnose`. These tools dispatch via `DRIVER_REGISTRY[config.type]` or `DRIVER_REGISTRY[session.interface_type]`.
3. For each driver in `DRIVER_REGISTRY`, call `check_python_deps()`. If all deps present, instantiate and call `driver.register_tools(mcp)`. If any missing, skip registration and log a stderr notice with the install hint.
4. Start the stdio loop.

The `_INSTRUCTIONS` constant gives the agent a multi-driver architecture overview, points to per-driver tool docstrings for protocol-specific semantics, and tells the agent to call `diagnose()` to see which drivers are available.

### F. CLI (`cli.py`)
Click root group. Shared subcommands (`lablink connect`, `lablink disconnect`, `lablink list`, `lablink diagnose`) always present. Per-driver subgroups (`lablink visa ...`, `lablink ssh ...`, etc.) registered via `driver.register_cli_commands(group)` using the same dep-presence logic as the MCP server.

### G. Event Logger (`lablink/event_logger.py`)
JSONL transaction log, one file per UTC day at `~/.lablink/logs/YYYY-MM-DD.jsonl`. Called by every tool at every success and failure return point. Generalized from agentlink-visa's `scpi_logger.py` — the `op` field is now any tool name, not just `connect`/`query`/`write`/`disconnect`. Disable by setting `LABLINK_LOG_DIR=""`.

### H. Exceptions (`lablink/exceptions.py`)
- `ConfigError` — invalid or missing config fields, unknown `type`.
- `SessionError` — alias not registered (raised internally; tools convert to structured error dicts).
- `DriverError` — driver-internal failures that don't fit the standard structured-error pattern.

---

## 4. Data Flow (Target)

### Agent control loop (MCP)
```
Agent (Claude)
  → connect("bench_scope")
      → mcp_server.connect: load config (type="visa") → DRIVER_REGISTRY["visa"] → VisaDriver().connect(config)
      → VisaDriver.connect: lazy import pyvisa, open resource, send *IDN?, build Session, register
      ← ConnectResult(success=true, alias="bench_scope", interface_type="visa", identity="...",
                       device_memory="...", techmanual_document_ids=[1291, 1323])

  → visa_query("bench_scope", "MEAS:FREQ? CH1")
      → VisaDriver.visa_query (registered as @mcp.tool): session_registry.get("bench_scope", expected_type="visa")
      → session.raw.query("MEAS:FREQ? CH1")
      → event_logger.log_event(op="visa_query", ...)
      ← ReadResult(success=true, raw="1000.00", format="text")

  → ssh_exec("lab_pi", "uname -a")
      → SshDriver.ssh_exec: session_registry.get("lab_pi", expected_type="ssh")
      → session.raw.exec_command("uname -a")
      ← ReadResult(success=true, raw="Linux...", metadata={"exit_code": 0, "stderr": ""})

  → disconnect("bench_scope")
      → mcp_server.disconnect: session_registry.get → VisaDriver.disconnect → session_registry.deregister
      ← Result(success=true)
```

### Diagnose (no alias) — system audit
```
Agent → diagnose()
  → mcp_server.diagnose: iterate DRIVER_REGISTRY
       → for each driver:
            check_python_deps() via importlib.util.find_spec (no side effects)
            if all present, system_dep_check() for OS-level deps
       → build DiagnosticResult with action_items ordered most-blocking-first
  ← DiagnosticResult(ready=true|false, drivers={visa: {...}, ssh: {...}, ...}, action_items=[...])
```

### CLI debug loop
```
$ lablink connect bench_scope
  → same dispatch path as MCP connect; prints ConnectResult as JSON

$ lablink visa query bench_scope "MEAS:FREQ? CH1"
  → same dispatch path as MCP visa_query
```

---

## 5. Current (Pre-Pivot) Architecture

> **Status (post Phase 0b):** This section is now largely historical. The
> migration mapping below has been *realized* for the architectural core —
> `lablink/base.py`, `lablink/session.py` (registry), `lablink/interfaces/`
> (registries + VISA driver), and the shared-tool dispatch in `mcp_server.py`
> all exist as described in §§1–4. `agentlink/tools.py` and
> `agentlink/diagnostics.py` have been deleted (split into the VISA driver and
> the shared diagnose/system-audit paths). What remains pre-target as of 0b:
> `scpi_logger.py` (renamed to `event_logger` in 0c), the flat `cli.py`
> (subgroups in 0c), and the VISA-flavored `_INSTRUCTIONS` (multi-driver rewrite
> in 0c). The table below is kept for provenance.

The original on-disk codebase reflected the single-driver agentlink-visa design. This section documents what was there and where each piece moved in Phase 0.

### Current directory
```text
agentlink/
├── __init__.py
├── config.py                       # → lablink/config.py (extended with DRIVER_CONFIG_REGISTRY)
├── session.py                      # → lablink/session.py (extended with expected_type lookup)
├── tools.py                        # → split: shared tools to mcp_server.py; VISA tools to lablink/interfaces/visa/driver.py
├── diagnostics.py                  # → folded into VisaDriver.diagnose() + mcp_server.diagnose() system audit
├── scpi_logger.py                  # → renamed lablink/event_logger.py
└── exceptions.py                   # → lablink/exceptions.py (adds DriverError)
mcp_server.py                       # → rewritten for shared + per-driver registration
cli.py                              # → rewritten for shared + per-driver subgroups
```

### Current tool surface
- `connect(alias)` — VISA-specific; returns `instrument_memory`, `techmanual_document_ids`
- `disconnect(alias)`
- `query(alias, command)` — SCPI query
- `write(alias, command)` — SCPI write
- `diagnose_connection(alias=None)` — diagnostics

### Migration mapping
| Current | Target |
|---|---|
| `agentlink.tools.connect` | `mcp_server.connect` (shared dispatch) |
| `agentlink.tools.query` | `VisaDriver.visa_query` (per-driver) |
| `agentlink.tools.write` | `VisaDriver.visa_write` (per-driver) |
| `agentlink.tools.disconnect` | `mcp_server.disconnect` (shared dispatch) |
| `agentlink.tools.diagnose_connection` | `mcp_server.diagnose` (shared) + `VisaDriver.diagnose` (per-alias path) |
| `agentlink.diagnostics.run_diagnostics` | folded into the two methods above |
| `agentlink.config.InstrumentConfig` | `VisaDriverConfig(DriverConfig, DocumentedConfig)` in `lablink/interfaces/visa/config.py` |
| `agentlink.config.load_instrument_memory` | `lablink.config.load_device_memory` (renamed) |
| `agentlink.scpi_logger.log_event` | `lablink.event_logger.log_event` (generalized op field) |
| `instrument_memory` (ConnectResult field) | `device_memory` (renamed) |

---

## 6. Configuration

### Device config (`~/.lablink/devices/<alias>.toml`)
One file per device. Every config has a `type` field that selects the driver. Required and optional fields per driver are documented in `lablink_plan.md` §5. Alias convention: `<vendor>_<model>` (T&M) or `<role>_<host>` (compute), lowercase with underscores.

### Device memory (`~/.lablink/devices/<alias>.md`)
Optional. Agent-maintained Markdown file of device-specific quirks and workarounds. Created and appended by agents when they encounter non-obvious device issues. Returned as `device_memory` in `connect()` and `diagnose(alias)` responses. Format: `## category` headers with one-line bullet entries per quirk.

### VISA backend (`~/.lablink/visa.toml` or `LABLINK_VISA_BACKEND` env var)
Optional. Overrides the default `pyvisa-py` backend. Document both setup paths in the README — this is the #1 setup friction point for the VISA driver.

### Environment Variables
| Variable | Purpose |
|----------|---------|
| `LABLINK_CONFIG_DIR` | Override device config directory (default: `~/.lablink/devices/`) |
| `LABLINK_VISA_BACKEND` | Override pyvisa backend (default: `@py` for pyvisa-py) |
| `LABLINK_LOG_DIR` | Override event log directory (default: `~/.lablink/logs/`); empty string disables logging |
| `LABLINK_AUTO_MIGRATE` | Set to `0` to disable Phase 0a auto-migration from `~/.agentlink/instruments/` |
| `TMAI_API_KEY` | techmanual.ai API key for agent-directed manual lookups (optional) |

The legacy `AGENTLINK_*` env vars are accepted as fallbacks during the transition period and will be removed after Phase 1 ships.
