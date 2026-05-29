# Agent Development Guidelines

## 1. Coding Standards

### Python
- **Version:** Python 3.10+
- **Style:** PEP 8 strictly.
- **Type Hinting:** Strict type hints (`typing` module) for all function signatures. Use `Generic[ConfigT]` on `Session` and `LabLinkDriver` per `lablink_plan.md` §3 / §4 — avoid `cast()` boilerplate inside driver methods.
- **Docstrings:** Google Style for all modules, classes, and functions. Per-driver MCP tool docstrings are load-bearing — they are surfaced to the agent as tool descriptions. State explicitly what each parameter means for this protocol.
- **Linters:** Compatible with `flake8` and `black` formatting.

### TOML Config
- Use `tomllib` (stdlib, Python 3.11+) or `tomli` (backport for 3.10) for config loading. Do not use third-party TOML libraries.
- Config loading lives in `lablink/config.py`. Never scatter config reads across other modules.
- Every config has a `type` field that maps to a driver via `DRIVER_CONFIG_REGISTRY` in `lablink/interfaces/__init__.py`. `config.py` reads `type`, looks up the registry, and instantiates the driver-specific subclass.
- Validate required fields at load time and raise a typed `ConfigError` with a clear message. Unknown `type` raises `ConfigError` listing all valid types.
- Any field that accepts a filesystem path (e.g. `auth_ssh_key_path`, `python_path`, `working_dir`) must be processed with `Path(value).expanduser()` at load time. TOML does not auto-expand tildes.
- Alias naming convention: `<vendor>_<model>` for T&M instruments, `<role>_<host>` for compute targets. Lowercase with underscores.
- `techmanual_document_ids: list[int]` lives on `DocumentedConfig` (mixin inherited by VISA-style configs). The legacy singular `techmanual_document_id` is accepted at load time and auto-converted. Always write new configs using the plural list form.

### MCP (FastMCP)
- Follow the FastMCP stdio pattern.
- The MCP tool surface has two layers (per `lablink_plan.md` §2.2):
  - **Shared lifecycle tools** (`connect`, `disconnect`, `list_devices`, `diagnose`) registered in `mcp_server.py` and dispatched via `DRIVER_REGISTRY[type]`.
  - **Per-driver operation tools** (`visa_query`, `ssh_exec`, etc.) registered inside each driver's `register_tools(mcp)` method, only when the driver's deps are present.
- Tool return values for error cases must be structured dicts (`{"success": false, "error": "...", "hint": "..."}`) rather than raising exceptions. See `project_goal.md` §2.7.
- Per-driver tool docstrings must explicitly define what each parameter means in this protocol's terms. The agent uses these as its source of truth.

### CLI (Click)
- Click root group in `cli.py`. Shared subcommands always present. Per-driver subgroups (`lablink visa ...`, `lablink ssh ...`, etc.) registered via each driver's `register_cli_commands(group)` method, mirroring the MCP tool registration pattern.
- Status/diagnostic output goes to stderr. Command output goes to stdout.
- CLI commands should be thin wrappers over the same per-driver code paths used by MCP tools.

## 2. Environment & Package Management

- **Package manager:** `uv`. Use `uv venv` to create the environment, `uv pip install -e .[dev]` for development.
- **Optional extras:** every driver's dependencies are an optional extra (`lablink-mcp[visa]`, `[ssh]`, `[rest]`, `[serial]`, `[common]`, `[all]`). See `lablink_plan.md` §12.
- **Secrets:** never hardcode. Use environment variables and `.env` files. Config files reference env var names; never values. See `project_goal.md` §2.5.
- **No Docker.** LabLink runs locally on the user's machine. USB/serial passthrough into containers defeats the point.

## 3. Driver Implementation Guidelines

When implementing or extending a driver in `lablink/interfaces/<type>/`:

### Lazy imports
All third-party driver deps (`pyvisa`, `paramiko`, `httpx`, `pyserial`, ...) must be imported **inside** `connect()` (or inside individual `@mcp.tool()` functions that need them), not at module level. A missing dep returns a structured error with the install command:

```python
def connect(self, config: SshDriverConfig) -> ConnectResult:
    try:
        import paramiko
    except ImportError:
        return ConnectResult(
            success=False, alias=config.alias, interface_type="ssh",
            error="Missing dependency: paramiko",
            hint="Run: pip install lablink-mcp[ssh]",
        )
    # ... proceed
```

`check_python_deps()` separately uses `importlib.util.find_spec(pkg_name)` so the system audit can report availability without side effects.

### Session ownership
- The driver's `connect()` constructs the `Session`, calls `session_registry.register(session)`, and returns `ConnectResult`. `mcp_server.connect` does not build the session.
- The driver's `disconnect()` closes the native connection and tears down any buffer thread. The shared `disconnect()` tool calls `session_registry.deregister(alias)` after the driver's `disconnect()` returns, regardless of return value.
- Per-driver tools look up their session via `session_registry.get(alias, expected_type=cls.type_name)`. `None` return means missing session or wrong type — return a structured error.

### Per-call timeout
Drivers must honor a per-call `timeout_ms` kwarg on any tool where it makes sense. The pattern:

```python
effective_timeout = timeout_ms or session.config.timeout_ms
```

Never hardcode a timeout. Config `timeout_ms` is the default; the per-call kwarg overrides.

### Diagnostics
`diagnose(config: ConfigT)` is **stateless** — it receives a config, not a session, and works whether or not a session is open. It may perform fresh test connections (TCP reachability, auth check, etc.). The no-alias system audit lives in `mcp_server.diagnose` and iterates `DRIVER_REGISTRY` calling `check_python_deps()` and `system_dep_check()` on each driver class.

### Event logging
Every tool must call `event_logger.log_event(op=..., alias=..., ...)` at every success and failure return point. Logging must never raise — `event_logger` no-ops on filesystem errors.

### Streaming drivers
v1 does not ship any streaming drivers. If you are implementing one post-v1, follow the five-rule contract in `lablink_plan.md` §6.5 (bounded queue with documented overflow, thread setup in `connect()` or per-driver `start_*` tool, thread teardown in `disconnect()` with `join(timeout=2.0)`, exception isolation via `session.metadata["stream_error"]`, documented batching semantics in the read tool's docstring).

## 4. Testing

- **Framework:** `pytest`.
- **Requirement:** every new function in `lablink/` must have unit tests.
- **Mocking:** use `unittest.mock` to mock `pyvisa.ResourceManager`, `paramiko.SSHClient`, `httpx.Client`, `serial.Serial`, and subprocess equivalents. Tests must never open a real connection.
- **Test location:** `tests/test_shared_tools.py` for shared lifecycle tools, `tests/test_dispatch.py` for type→driver dispatch and dep-presence behavior, `tests/interfaces/test_<type>.py` for per-driver implementations.
- **No hardware-dependent tests in CI.** If a test requires real hardware, mark it `@pytest.mark.skip(reason="requires hardware")` and document the manual test procedure.
- **Required dispatch tests (Phase 0b):**
  - Unknown `type` in config raises `ConfigError` listing valid types.
  - A driver with missing Python deps does not register its tools; its tools are absent from the MCP surface.
  - `connect()` for an alias of a deps-missing driver returns a structured error with the install hint.
  - `session_registry.get(alias, expected_type="ssh")` returns `None` when the alias is actually a VISA session.

## 5. Per-Driver Agent Context Pattern

Each driver registers operation tools whose **docstrings** carry the per-protocol semantics that the agent needs. The `_INSTRUCTIONS` constant in `mcp_server.py` no longer carries every protocol detail; it provides a multi-driver architecture overview and points the agent to:

- `diagnose()` to see which drivers are available
- Per-driver tool docstrings for protocol semantics
- `connect()` response (`interface_type`, `device_memory`, `techmanual_document_ids`) for runtime device context

Per-driver tool docstrings should cover:
- What each parameter means for this protocol
- Error causes the agent can disambiguate (e.g. timeout vs. command-rejected vs. no-such-channel)
- Efficiency patterns (e.g. parallel queries) where they apply
- Where data flows (return shape, metadata fields)

The VISA driver's tools are the canonical template once Phase 0b lands.

## 6. Documentation Maintenance

When your changes are non-trivial:

- **Update `current_status.md`** — Add a concise entry to "Recent History" describing what changed and why. Update "Current Phase" if your work completes a milestone. Record hacks in "Technical Debt & Known Issues." Prune older tactical entries (rolling window ~10).
- **Update `lablink_plan.md`** — If implementation reveals a flaw in the plan, fix the plan rather than silently diverge. The plan is a living document.
- **Update this file (`agent_development.md`)** — When the developer corrects you on a pattern that should hold generally, capture it here. This document is the codified collective memory.
- **Update `system_architecture.md`** — When the code's component map changes (new module, renamed file, changed data flow), reflect it here.
- **Update `project_goal.md`** — Only when a strategic shift occurs (vision, locked design decisions, scope). Routine implementation does not touch this file.

## 7. Agent Behavior & Interaction

- **Ambiguity:** always ask clarifying questions before implementation. Do not guess.
- **Scope discipline:** do not add features, refactor, or introduce abstractions beyond what the current task requires. v1 scope is defined in `project_goal.md` (v1 drivers) and `lablink_plan.md` (architecture).
- **Locked decisions:** do not revisit §2 of `project_goal.md` or §2 of `lablink_plan.md` without explicit instruction from the lead developer.
- **Context documents:** be concise. Favor detail over fluff but minimize context window usage.
- **Self-correction:** if corrected by the developer on a preference or rule, update this document to capture it for future agents.

## 8. Git & Version Control

- **Commit messages:** imperative mood ("Add feature", not "Added feature").
- **Granularity:** atomic commits — one feature or fix per commit.
- **Never** skip pre-commit hooks (`--no-verify`) unless explicitly requested. If a hook fails, fix the issue and create a new commit.
