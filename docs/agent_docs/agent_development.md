# Agent Development Guidelines

## 1. Coding Standards

### Python
- **Version:** Python 3.10+
- **Style:** PEP 8 strictly.
- **Type Hinting:** Strict type hints (`typing` module) for all function signatures. Use `Generic[ConfigT]` on `Session` and `LabLinkDriver` (see `docs/ARCHITECTURE.md`) — avoid `cast()` boilerplate inside driver methods.
- **Docstrings:** Google Style for all modules, classes, and functions. Per-driver MCP tool docstrings are load-bearing — they are surfaced to the agent as tool descriptions. State explicitly what each parameter means for this protocol.
- **Linters:** Compatible with `flake8` and `black` formatting.

### TOML Config
- Use `tomllib` (stdlib, Python 3.11+) or `tomli` (backport for 3.10) for config loading. Do not use third-party TOML libraries.
- Config loading lives in `lablink/config.py`. Never scatter config reads across other modules.
- Every config has a `type` field that maps to a driver via `DRIVER_CONFIG_REGISTRY` in `lablink/interfaces/__init__.py`. `config.py` reads `type`, looks up the registry, and instantiates the driver-specific subclass.
- Validate required fields at load time and raise a typed `ConfigError` with a clear message. Unknown `type` raises `ConfigError` listing all valid types.
- Any field that accepts a filesystem path (e.g. `auth_ssh_key_path`, `python_path`, `working_dir`) must be processed with `Path(value).expanduser()` at load time. TOML does not auto-expand tildes.
- Alias naming convention: `<vendor>_<model>` for T&M instruments, `<role>_<host>` for compute targets. Lowercase with underscores.
- `document_ids: list[int]` lives on `DocumentedConfig` (mixin inherited by VISA-style configs). Opaque pointers into an external documentation index; LabLink does not resolve them. The legacy keys `techmanual_document_ids`, `techmanual_document_id` and `document_id` are accepted at load time and normalized. Always write new configs using `document_ids`.

### MCP (FastMCP)
- Follow the FastMCP stdio pattern.
- The MCP tool surface has two layers (see `docs/ARCHITECTURE.md` §2):
  - **Shared lifecycle tools** (`connect`, `disconnect`, `list_devices`, `diagnose`) registered in `lablink/mcp_server.py` and dispatched via `DRIVER_REGISTRY[type]`.
  - **Per-driver operation tools** (`visa_query`, `ssh_exec`, etc.) registered inside each driver's `register_tools(mcp)` method, only when the driver's deps are present.
- Tool return values for error cases must be structured dicts (`{"success": false, "error": "...", "hint": "..."}`) rather than raising exceptions.
- Per-driver tool docstrings must explicitly define what each parameter means in this protocol's terms. The agent uses these as its source of truth.

### CLI (Click)
- Click root group in `lablink/cli.py`. Shared subcommands always present. Per-driver subgroups (`lablink visa ...`, `lablink ssh ...`, etc.) registered via each driver's `register_cli_commands(group)` method, mirroring the MCP tool registration pattern.
- Status/diagnostic output goes to stderr. Command output goes to stdout.
- CLI commands should be thin wrappers over the same per-driver code paths used by MCP tools.
- A CLI-only command (e.g. `scan`) still keeps its logic in a module the command calls — never in the command body. The command formats output; the module is what tests exercise without click. Behavior that crosses two drivers belongs in a shared module (`discovery.py`, `system.py`), not in one of the drivers.

## 2. Environment & Package Management

- **Package manager:** `uv`. Use `uv venv` to create the environment, `uv pip install -e .[dev]` for development.
- **Optional extras:** every driver's dependencies are an optional extra (`lablink-mcp[visa]`, `[ssh]`, `[rest]`, `[serial]`, `[python_shell]`, `[all]`). See `docs/ARCHITECTURE.md` §9.
- **Secrets:** never hardcode. Use environment variables and `.env` files. Config files reference env var names; never values.
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

If a tool logs any field that could contain a credential the agent inlined (a `command`, a URL `path`, an `error` that echoes either), pass `secrets=redaction.secret_values(config)` to `log_event`. The scrub happens at that boundary, so you never hand-redact individual fields — and you cannot leak a known secret by forgetting one. Use `redaction.contains_secret(text, secrets)` if you also want to set a `metadata.security_warning` on the agent-facing result. See `docs/ARCHITECTURE.md` §8.4.

### Streaming drivers
The SSH driver's `ssh_start_stream` / `ssh_read_stream` / `ssh_stop_stream` tools are the reference streaming implementation. Any new streaming driver must follow the five-rule contract in `docs/ARCHITECTURE.md` §11 (bounded queue with documented overflow, thread setup in `connect()` or per-driver `start_*` tool, thread teardown in `disconnect()` with `join(timeout=2.0)`, exception isolation via `session.metadata["stream_error"]`, documented batching semantics in the read tool's docstring).

## 4. Testing

- **Framework:** `pytest`.
- **Requirement:** every new function in `lablink/` must have unit tests.
- **Mocking:** use `unittest.mock` to mock `pyvisa.ResourceManager`, `paramiko.SSHClient`, `httpx.Client`, `serial.Serial`, and subprocess equivalents. Tests must never open a real connection. This includes enumeration APIs (`list_resources()`, `serial.tools.list_ports.comports()`) — a sweep that touches the real machine gives a different answer on every developer's laptop.
- **Simulating a missing extra:** `monkeypatch.setitem(sys.modules, "pyvisa", None)` makes a lazy `import pyvisa` raise `ImportError` exactly as an uninstalled extra would. Prefer it to patching `check_python_deps()` when what you are testing is the lazy-import fallback itself.
- **Never touch the real config directory.** Any test that writes a config file must point `LABLINK_CONFIG_DIR` at a `tmp_path` (`monkeypatch.setenv`) and assert the file landed there. A test run that overwrites a developer's `~/.lablink/devices` is a bug, not a flake. The same goes for production code: resolve the directory through `config.get_config_dir()`, never a hardcoded `~/.lablink/devices`.
- **Test location:** `tests/test_shared_tools.py` for shared lifecycle tools, `tests/test_dispatch.py` for type→driver dispatch and dep-presence behavior, `tests/interfaces/test_<type>.py` for per-driver implementations.
- **No hardware-dependent tests in CI.** If a test requires real hardware, mark it `@pytest.mark.skip(reason="requires hardware")` and document the manual test procedure.
- **Dispatch behavior to keep covered:**
  - Unknown `type` in config raises `ConfigError` listing valid types.
  - A driver with missing Python deps does not register its tools; its tools are absent from the MCP surface.
  - `connect()` for an alias of a deps-missing driver returns a structured error with the install hint.
  - `session_registry.get(alias, expected_type="ssh")` returns `None` when the alias is actually a VISA session.

## 5. Per-Driver Agent Context Pattern

Each driver registers operation tools whose **docstrings** carry the per-protocol semantics that the agent needs. The `_INSTRUCTIONS` constant in `lablink/mcp_server.py` no longer carries every protocol detail; it provides a multi-driver architecture overview and points the agent to:

- `diagnose()` to see which drivers are available
- Per-driver tool docstrings for protocol semantics
- `connect()` response (`interface_type`, `device_memory`, `document_ids`) for runtime device context

Per-driver tool docstrings should cover:
- What each parameter means for this protocol
- Error causes the agent can disambiguate (e.g. timeout vs. command-rejected vs. no-such-channel)
- Efficiency patterns (e.g. parallel queries) where they apply
- Where data flows (return shape, metadata fields)

The VISA driver's tools are the canonical template — match their docstring depth and error-disambiguation style.

## 6. Documentation Maintenance

When your changes are non-trivial:

- **Update `CHANGELOG.md`** — Add a concise entry under `[Unreleased]` for any user-facing change (new driver, new tool, behavior change), in release-note tone.
- **Update `docs/ARCHITECTURE.md`** — When the code's component map, data flow, or a documented contract changes (new module, renamed file, new driver, changed dispatch). If implementation reveals a flaw in the design, fix the doc rather than silently diverge.
- **Update `README.md`** — When scope, the tool surface, or the config schema changes.
- **Update this file (`agent_development.md`)** — When the developer corrects you on a pattern that should hold generally, capture it here. This document is the codified collective memory.

## 7. Documentation Voice

These rules govern prose in `README.md`, `docs/`, and `CHANGELOG.md`. They are a
checklist: read a draft against them before committing it. Code blocks, tables,
tool docstrings and captured command output are exempt. This is about sentences.

Reference: <https://www.pangram.com/blog/comprehensive-guide-to-spotting-ai-writing-patterns>

### 7.1 Inflated vocabulary

Every one of these has a plain replacement, so use the replacement:

| Don't | Do |
|-------|-----|
| delve into | dig into, read |
| leverage, utilize | use |
| robust | reliable, well-tested (say which) |
| seamless | smooth, or drop the word |
| comprehensive | full, complete |
| crucial, pivotal | important, or say what breaks without it |
| facilitate | let, help, make possible |
| underscore | show, confirm |
| myriad, plethora | many, or the actual count |
| realm, landscape | area, field |
| testament to | evidence of, or drop the sentence |
| meticulous | careful, thorough |

> **Before:** LabLink leverages a robust driver registry to facilitate seamless
> protocol extension.
>
> **After:** A new driver registers in two tables. The core server does not change.

### 7.2 The antithesis cliché

"It's not just X — it's Y." "This isn't about X. It's about Y." The first half
denies something nobody claimed and the second half is the only content. Drop the
frame and state the thing.

> **Before:** LabLink isn't just a PyVISA wrapper — it's a control layer for the
> whole bench.
>
> **After:** LabLink drives VISA, SSH, REST and serial devices from one session.

### 7.3 Rule-of-three everywhere

Three parallel items in sentence after sentence is a rhythm, not a fact pattern.
Count what is actually there and write that number, whether it is two, four or
seven.

> **Before:** Fast, reliable, and extensible.
>
> **After:** Four of the five drivers are exercised end-to-end on real hardware.

### 7.4 Participial tails

Do not end a sentence with ", ensuring X", ", allowing Y", ", making it Z". Cut
the tail or promote it to its own sentence.

> **Before:** Drivers import their dependencies lazily, ensuring the server starts
> with no extras installed.
>
> **After:** Drivers import their dependencies lazily. The server starts with no
> extras installed.

### 7.5 Restating the heading

A section must not open by repeating its own title. Delete the sentence and start
with the content.

> **Before:** `## Install` followed by "Installing LabLink is straightforward."
>
> **After:** `## Install` followed by the `pip install` command.

### 7.6 Hedging and throat-clearing

Delete on sight: "It's worth noting that", "Essentially", "In essence", "Simply
put", "At its core". They postpone the sentence without qualifying it.

> **Before:** It's worth noting that credentials are referenced by environment
> variable name.
>
> **After:** Credentials are referenced by environment variable name, never stored
> in config files.

### 7.7 Uniform sentence length

Generated prose runs 15–25 words a sentence with no variation, which is what makes
a paragraph feel flat even when every fact in it is right. Vary the length
deliberately. A short one lands.

> **Before:** The simulator implements SCPI over a TCP socket, which means that
> LabLink connects to it through the same visa driver that it would use for a real
> bench instrument, without any simulation code path being involved.
>
> **After:** The simulator speaks SCPI on a socket, so LabLink reaches it through
> the same `visa` driver it uses for a bench instrument. Nothing is mocked.

### 7.8 Bolded lead-in on every bullet

Bold the lead-in when it is a label the reader scans for: a driver name, a config
key, a non-goal. Drop it when the bullet is just a sentence. A list where every
item is bolded has stopped signaling anything.

> **Before:** `- **Local-first** — LabLink runs on your machine.`
>
> **After:** `- LabLink runs on your machine. There is no hosted endpoint.`

### 7.9 Em-dash density

Keep the em-dashes that set off a real parenthetical or mark an abrupt turn.
Everything else becomes a comma, a colon or a full stop. A label followed by its
definition does not need one.

> **Before:** **REST** — adds `base_url`, auth fields
>
> **After:** **REST** adds `base_url` and auth fields.

### 7.10 Marketing adjectives on technical claims

No "powerful", "elegant", "effortless", "blazing", "world-class", "enterprise-grade".
A technical doc earns trust with a number, a command, or a named limitation.

> **Before:** LabLink offers powerful diagnostics.
>
> **After:** `diagnose()` reports which drivers are installed, which system
> dependencies are missing, and the command that installs each.

## 8. Agent Behavior & Interaction

- **Ambiguity:** always ask clarifying questions before implementation. Do not guess.
- **Never invent remote facts.** IPs, hostnames, ports, file paths, and device
  state must be discovered, not assumed — from `connect()` metadata (e.g.
  `peer_address`), a `diagnose()`, or a read command (`hostname -I`, `ls`). A
  guessed value that happens to be wrong is a silent failure; treat inventing one
  as a bug.
- **Operating remote devices:** never inline a credential in a command or path
  (e.g. `echo $PASS | sudo -S ...`). Reference secrets by environment variable,
  exactly as config does. The SSH/REST drivers redact known credentials from the
  event log and warn, but that is a backstop, not permission. For privileged SSH
  work prefer key-based auth, an askpass helper, or passwordless sudo.
- **Don't batch interdependent remote steps in one turn.** Sequential
  dependencies (install → pull → run → configure) cannot run in parallel — issue
  one step, confirm its result, then the next. Firing them together (plus
  duplicate "is it done yet?" probes) produces thrash and masks the first
  failure.
- **Scope discipline:** do not add features, refactor, or introduce abstractions beyond what the current task requires. Scope (drivers and non-goals) is defined in `README.md`; architecture in `docs/ARCHITECTURE.md`.
- **Design decisions:** the design principles in `docs/ARCHITECTURE.md` §2 are settled — do not revisit them without explicit instruction from the lead developer.
- **Context documents:** be concise. Favor detail over fluff but minimize context window usage.
- **Self-correction:** if corrected by the developer on a preference or rule, update this document to capture it for future agents.

## 9. Git & Version Control

- **Commit messages:** imperative mood ("Add feature", not "Added feature").
- **Granularity:** atomic commits — one feature or fix per commit.
- **Never** skip pre-commit hooks (`--no-verify`) unless explicitly requested. If a hook fails, fix the issue and create a new commit.
