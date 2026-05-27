# System Architecture

## 1. High-Level Overview

AgentLink-Visa is a local-first Python application. It runs on the user's machine and provides two interfaces to the same core instrument control logic: an MCP server (primary, for AI agents) and a CLI (secondary, for development and debugging).

**Core Stack:**
- **Runtime:** Python 3.10+
- **MCP Framework:** FastMCP (stdio transport)
- **VISA Interface:** pyvisa + pyvisa-py (pure-Python backend)
- **Config Format:** TOML (tomllib / tomli)
- **CLI:** Click
- **Package Manager:** uv

---

## 2. Directory Structure (Target)

```text
agentlink-visa/
├── docs/
│   └── agent_docs/
│       ├── readme_agent.md         # Agent onboarding protocol
│       ├── project_goal.md         # Vision, design decisions, non-goals
│       ├── agent_development.md    # Coding standards, dev guidelines
│       ├── current_status.md       # Current phase + recent history
│       └── system_architecture.md  # This file
├── agentlink/
│   ├── __init__.py
│   ├── config.py                   # Config loader: reads <alias>.toml, validates required fields
│   ├── session.py                  # VISA session lifecycle: open, close, module-level session dict
│   ├── tools.py                    # MCP tool implementations (connect, disconnect, query, write)
│   ├── diagnostics.py              # Connection diagnostics: deps, VISA backend, resource discovery, reachability
│   ├── scpi_logger.py              # SCPI transaction logger: JSONL log per day to ~/.agentlink/logs/
│   └── exceptions.py               # Typed exceptions (ConfigError, SessionError)
├── mcp_server.py                   # FastMCP entrypoint (stdio)
├── cli.py                          # Click CLI entrypoint
├── tests/
│   └── test_tools.py               # Unit tests (mocked pyvisa)
├── examples/
│   └── instruments/                # Example .toml configs for common instruments
├── pyproject.toml
├── requirements.txt
├── .env.example
├── agent-bootstrap.md              # Founding context document
└── README.md
```

---

## 3. Core Components

### A. Config Loader (`agentlink/config.py`)
- Reads `~/.agentlink/instruments/<alias>.toml` (or `$AGENTLINK_CONFIG_DIR/<alias>.toml`).
- Validates required fields at load time; raises `ConfigError` with a clear message on missing fields.
- Returns a typed config dataclass used by session and tool modules.
- The `techmanual_document_id` optional field is passed through to MCP tool responses to enable agent-directed manual lookups.

### B. Session Manager (`agentlink/session.py`)
- Maintains a module-level dict of open VISA sessions keyed by alias: `_sessions: dict[str, pyvisa.Resource]`.
- `open_session(config)` — calls `pyvisa.ResourceManager().open_resource()` with the configured resource string and timeout. Registers the resource in `_sessions`.
- `close_session(alias)` — closes the resource and removes it from `_sessions`.
- `get_session(alias)` — returns the open resource or raises `SessionError` if none exists.
- pyvisa backend defaults to `pyvisa-py`. Users with NI-VISA installed can override via `AGENTLINK_VISA_BACKEND` env var or `~/.agentlink/visa.toml`.

### C. MCP Tools (`agentlink/tools.py`)
Implements the four v0.1 tools. All call into `config.py` and `session.py`; none interact with pyvisa directly.

| Tool | Behavior |
|------|----------|
| `connect(alias)` | Load config → open session → send `*IDN?` → return instrument info dict. On IDN failure, cleans up the orphaned session before returning error. |
| `disconnect(alias)` | Close session → return success dict |
| `query(alias, command)` | Get session → `resource.query(command)` → return response string |
| `write(alias, command)` | Get session → `resource.write(command)` → return success dict |

All tools catch `pyvisa` exceptions and return structured error dicts (`{"success": false, "error": "...", "hint": "..."}`) rather than raising.

### D. Diagnostics (`agentlink/diagnostics.py`)
`run_diagnostics(alias=None)` — checks installed dependencies, VISA backend health, detected resources by interface type (USB/GPIB/TCPIP/serial), config directory status, and (when alias is provided) alias-specific reachability: USB presence in resource list, TCPIP ping + port 5025 check, GPIB adapter detection. Returns a structured dict with `ready: bool` and `action_items: list[str]` of concrete user-facing steps. Exposed as the `diagnose_connection` MCP tool and `agentlink diagnose [alias]` CLI command.

### E. SCPI Logger (`agentlink/scpi_logger.py`)
- `get_log_dir()` — returns the active log directory (`Path`) or `None` if logging is disabled.
- `log_event(**fields)` — appends one JSONL entry to `<log_dir>/YYYY-MM-DD.jsonl`. Prepends a `ts` (UTC ISO timestamp). Silently no-ops on any filesystem error — logging must never affect instrument control.
- Called by `tools.py` at every success and failure return point for all four tools.
- Default log dir: `~/.agentlink/logs/`. Override: `AGENTLINK_LOG_DIR` env var. Disable: set `AGENTLINK_LOG_DIR` to empty string.

### F. MCP Server (`mcp_server.py`)
- FastMCP entrypoint over stdio.
- Registers the five tools (connect, disconnect, query, write, diagnose_connection).
- `_INSTRUCTIONS` includes interface-setup guidance, troubleshooting steps, and a VISA/SCPI behavior section surfaced to every agent session.
- Entry point: `uv run mcp_server.py` or configured in `.mcp.json`.

### G. CLI (`cli.py`)
- Click group with four subcommands: `connect`, `query`, `write`, `list`.
- Thin wrappers over the same core functions used by MCP tools.
- Diagnostic output to stderr; command output to stdout.
- Intended for development, debugging, and instrument config validation — not for production agent use.

### H. Exceptions (`agentlink/exceptions.py`)
- `ConfigError` — raised on invalid or missing config fields.
- `SessionError` — raised when a tool is called for an alias with no open session.

---

## 4. Data Flow

### Agent Control Loop (MCP)
```
Agent (Claude)
  → connect("tek_mso44")
      → config.py: load ~/.agentlink/instruments/tek_mso44.toml
      → session.py: ResourceManager().open_resource(resource_string)
      → tools.py: resource.query("*IDN?")
      ← {"success": true, "alias": "tek_mso44", "idn": "...", "techmanual_document_id": 142}

  → query("tek_mso44", "MEAS:FREQ? CH1")
      → session.py: get_session("tek_mso44")
      → resource.query("MEAS:FREQ? CH1")
      ← {"success": true, "response": "1000.00"}

  → disconnect("tek_mso44")
      → session.py: resource.close(), remove from _sessions
      ← {"success": true}
```

### CLI Debug Loop
```
$ agentlink connect tek_mso44
  → same config + session path as MCP connect()
  → prints IDN response to stdout

$ agentlink query tek_mso44 "MEAS:FREQ? CH1"
  → prints query response to stdout
```

### Instrument Config Discovery
```
$ agentlink list
  → scans ~/.agentlink/instruments/*.toml
  → prints alias, manufacturer, model_number, resource_string for each
```

---

## 5. Configuration

### Instrument Config (`~/.agentlink/instruments/<alias>.toml`)
One file per instrument. Required and optional fields documented in `project_goal.md` §2.3.

### VISA Backend (`~/.agentlink/visa.toml` or `AGENTLINK_VISA_BACKEND` env var)
Optional. Overrides the default pyvisa-py backend. Document both setup paths in the README — this is the #1 setup friction point.

### Environment Variables
| Variable | Purpose |
|----------|---------|
| `AGENTLINK_CONFIG_DIR` | Override instrument config directory (default: `~/.agentlink/instruments/`) |
| `AGENTLINK_VISA_BACKEND` | Override pyvisa backend (default: `@py` for pyvisa-py) |
| `AGENTLINK_LOG_DIR` | Override SCPI log directory (default: `~/.agentlink/logs/`); set to empty string to disable logging |
| `TMAI_API_KEY` | techmanual.ai API key for agent-directed manual lookups |
