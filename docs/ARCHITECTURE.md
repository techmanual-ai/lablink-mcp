# LabLink Architecture

A reference for contributors and anyone extending LabLink with a new driver.
For installation and usage, see the [README](../README.md).

---

## 1. Overview

LabLink is a local-first Python application. It exposes two interfaces over one
shared core:

- **An MCP server** (primary) — for AI agents, over FastMCP's stdio transport.
- **A CLI** (secondary) — for development, debugging, and scripted use.

Both interfaces dispatch through the same driver registry, so a device behaves
identically whether you reach it from an agent or the command line.

**Core stack**

| Concern | Choice |
|---------|--------|
| Runtime | Python 3.10+ |
| MCP framework | FastMCP (stdio) |
| Config format | TOML (`tomllib` / `tomli`) |
| CLI | Click |
| Packaging | `uv` + `hatchling`, optional extras per driver |

Driver libraries (PyVISA, Paramiko, httpx, pyserial) are **optional extras** and
are **imported lazily** — the server starts and runs with zero drivers
installed, and only the drivers whose dependencies are present expose tools.

---

## 2. Design Principles

- **Shared lifecycle + per-driver operations.** Every driver shares four
  lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`). Each
  driver then registers its own operation tools (`visa_query`, `ssh_exec`,
  `rest_get`, …). There is deliberately no universal `query`/`write`/`read`
  tool — a uniform surface leaks across protocols (the same `data` argument
  meaning a URL path, a SCPI string, or raw bytes depending on the driver).
  Honest, per-protocol names beat one overloaded surface.

- **Tools appear only when they can work.** A driver whose Python dependency is
  not installed does not register its tools. The agent never sees a tool that
  would fail with "missing dependency."

- **Diagnose, don't fail silently.** `diagnose()` is the agent's oracle. When a
  dependency is missing, an instrument is unreachable, or a config field is
  wrong, `diagnose()` reports what is broken and what to do about it.

- **Config selects the driver.** Every device config carries a `type` field that
  maps to a driver in `DRIVER_REGISTRY`. No protocol-conditional logic exists
  anywhere except inside driver implementations.

- **Credentials by reference only.** Config files never contain secrets. Auth
  fields name an environment variable; the variable holds the value.

- **Sessions persist between calls.** `connect()` opens a session held in a
  module-level registry keyed by alias; `disconnect()` closes it. Per-driver
  tools look up their session by alias and verify the type matches.

- **Structured errors, never exceptions across the boundary.** Tools return
  `{"success": false, "error": ..., "hint": ...}` rather than raising, so the
  agent can reason about and recover from failures.

---

## 3. Directory Structure

```text
lablink-mcp/
├── docs/
│   ├── ARCHITECTURE.md             # this file
│   └── agent_docs/                 # contributor/agent working guides
├── lablink/
│   ├── mcp_server.py               # FastMCP entrypoint; shared tools + driver.register_tools()
│   ├── cli.py                      # Click root; driver.register_cli_commands()
│   ├── base.py                     # data models, config dataclasses, Session, the driver ABC
│   ├── config.py                   # TOML loader via DRIVER_CONFIG_REGISTRY; device-memory reader; load_system()
│   ├── system.py                   # topology graph logic: device_slice(), validate_system()
│   ├── session.py                  # _sessions registry; three-state lookup
│   ├── event_logger.py             # JSONL event log
│   ├── exceptions.py               # ConfigError, SessionError, DriverError
│   ├── py.typed                    # PEP 561 marker
│   └── interfaces/
│       ├── __init__.py             # DRIVER_REGISTRY, DRIVER_CONFIG_REGISTRY
│       ├── visa/                   # driver.py + config.py per driver
│       ├── ssh/
│       ├── rest/
│       ├── serial/
│       ├── python_shell/           # + bootstrap.py (subprocess REPL)
│       └── external/               # routing stub for vendor-supplied MCP servers
├── tests/
│   └── test_system.py              # topology subsystem tests
├── examples/
│   ├── configs/                    # one example .toml per driver
│   └── topology.toml               # RF-bench topology example
└── pyproject.toml
```

There is no `lablink/tools.py`. Shared lifecycle tools live in `lablink/mcp_server.py`;
per-driver operation tools live inside each `lablink/interfaces/<type>/driver.py`
and self-register via `register_tools(mcp)`. The dispatch layer is the two
registries.

---

## 4. Core Components

**`lablink/base.py`** — all type definitions and the driver ABC:
- Data models: `Result`, `ReadResult`, `ConnectResult`, `DiagnosticResult`,
  `SystemDepStatus` (§5).
- Config types: `DriverConfig` (base) plus the `AuthConfig` and
  `DocumentedConfig` mixins (§7).
- `Session[ConfigT]` — a live connection, generic over the driver's config type.
- `LabLinkDriver[ConfigT]` — the driver ABC (§6).

**`lablink/config.py`** — reads `~/.lablink/devices/<alias>.toml` (or
`$LABLINK_CONFIG_DIR/<alias>.toml`), looks up `DRIVER_CONFIG_REGISTRY[type]`, and
instantiates the matching config subclass. Raises `ConfigError` on an unknown
`type` with a message listing valid types. Expands `~` on every path field at
load time (TOML does not). `load_device_memory(alias)` is the single reader of
`<alias>.md` (§8.3).

**`lablink/session.py`** — module-level `_sessions: dict[str, Session]` with
`register` / `deregister` / `get` / three-state `lookup` (§8).

**`lablink/interfaces/<type>/`** — one subpackage per driver: `driver.py`
(subclass of `LabLinkDriver`) and `config.py` (subclass of `DriverConfig`).

**`lablink/mcp_server.py`** — FastMCP entrypoint. Registers the four shared lifecycle
tools, then for each driver whose deps are present, instantiates it and calls
`register_tools(mcp)`. Holds driver instances as server-lifetime singletons.

**`lablink/cli.py`** — Click root group. Shared subcommands always present; per-driver
subgroups (`lablink visa …`) registered via `register_cli_commands(group)` with
the same dep gating as the MCP server.

**`lablink/event_logger.py`** — appends one JSONL entry per tool call to
`~/.lablink/logs/YYYY-MM-DD.jsonl`. Never raises (§8.4).

**`lablink/redaction.py`** — shared credential scrubber. `secret_values(config)`
resolves a config's `auth_*_env` fields to their live `os.environ` values (values
below a short-secret floor are excluded — see §8.4); `redact(text, secrets)`
replaces any occurrence with `***`; `contains_secret(text, secrets)` is the cheap
detector for the agent-facing warning. The scrub itself runs at the logging
boundary (`event_logger.log_event`, §8.4), not at each call site. Stdlib-only;
depends on nothing else in `lablink`.

---

## 5. Data Models

All tool return values are one of three result types, serialized to a dict for
MCP transport. They carry orthogonal information, so a single optional-field
type was rejected in favor of three with clear contracts.

- **`ConnectResult`** — identity, device memory, and documentation pointers.
  Populated once per session by `connect()`.
- **`ReadResult`** — `raw` / `decoded` / `format` / `timed_out` plus
  `metadata`. The right shape for anything that returns data: queries, reads,
  exec stdout, REST bodies. Write-style tools also use it when they carry useful
  metadata (exit code, status code, bytes written).
- **`Result`** — bare success/failure for tools with no payload (`disconnect`,
  `serial_flush`).

`DiagnosticResult` and `SystemDepStatus` carry diagnose output.

### 5.1 `kw_only=True` is mandatory on every config dataclass

**All `DriverConfig` subclasses, the base, and all mixins MUST be declared with
`@dataclass(kw_only=True)`.** Without it, dataclass field ordering across
multiple-inheritance bases is MRO-sensitive: as soon as a subclass adds a
required field after a mixin contributes a defaulted one, construction fails with
`TypeError: non-default argument follows default argument`. With `kw_only=True`,
all fields are keyword-only and ordering is irrelevant — required and defaulted
fields interleave freely across the MRO. This applies to the result types and
`Session` as well; do not omit it even on a class that "works today."

### 5.2 Read-style result semantics (locked)

The three-way distinction lets the agent tell "try again" from "something is
wrong":

```text
Data arrived:        ReadResult(success=True,  raw=<data>, timed_out=False)
Timeout, no data:    ReadResult(success=True,  raw=None,   timed_out=True)
Broken / dead:       ReadResult(success=False, error=..., hint=...)
```

---

## 6. The Driver Contract

`LabLinkDriver[ConfigT]` (an ABC in `base.py`) is a **code-sharing contract, not
a tool-uniformity contract**. Drivers register their own MCP tools.

Required methods:

| Method | Responsibility |
|--------|----------------|
| `connect(config) -> ConnectResult` | Open the connection, build a `Session`, register it. Lazy-import the third-party dep here. On failure, return `success=False` and register nothing. |
| `disconnect(session) -> Result` | Close the native connection and tear down any buffer thread. The shared tool deregisters the alias afterward regardless. |
| `diagnose(config) -> DiagnosticResult` | Stateless per-alias check. Receives a config, not a session; may open fresh test connections. |
| `register_tools(mcp)` | Register this driver's `@mcp.tool()` operation tools. |
| `register_cli_commands(group)` | Register this driver's CLI subgroup. |

Classmethod hooks for the system audit:

| Method | Default | Purpose |
|--------|---------|---------|
| `check_python_deps()` | `[]` | `[(package, is_available), …]` via `importlib.util.find_spec` (no import side effects). |
| `system_dep_check()` | `[]` | One `SystemDepStatus` per OS-level dependency. |

### 6.1 Registration

`lablink/interfaces/__init__.py` holds two parallel registries:

```python
DRIVER_REGISTRY:        dict[str, type[LabLinkDriver]]  # type -> driver class
DRIVER_CONFIG_REGISTRY: dict[str, type[DriverConfig]]   # type -> config class
```

An import-time check raises `RuntimeError` if their key sets ever diverge (it
uses `if/raise`, not `assert`, so `python -O` cannot strip it). Adding a driver
is one line in each registry — no changes to `config.py`, `lablink/mcp_server.py`, or
`lablink/cli.py`.

### 6.2 Instance lifecycle and state placement

Driver instances are **server-lifetime singletons** held in `lablink/mcp_server.py`,
keyed by `type_name`, instantiated once at startup if their deps are present.
The same instance handles every `connect` / `disconnect` / `diagnose` and every
tool it registered (tool closures capture the instance at registration time).

- **Per-session state** (native handles, per-device buffers, in-flight counters)
  lives on the `Session`, created in `connect()` and destroyed in `disconnect()`.
- **Cross-session driver state** (a shared `pyvisa.ResourceManager`, an `httpx`
  pool) lives on the driver instance — set lazily inside the first `connect()`,
  not at import (lazy-import constraint).
- **Process-wide state** (the session registry, the event logger) lives in
  module-level dicts in `session.py` and `event_logger.py`.

Avoid holding session state on the driver instance (it leaks across sessions)
and module-level mutable state inside a driver module (defeats the singleton and
complicates testing).

---

## 7. Config Schema

### 7.1 Base fields (all drivers)

```toml
type        = "visa"          # must match a key in DRIVER_REGISTRY
alias       = "bench_scope"   # unique; <vendor>_<model> or <role>_<host>
timeout_ms  = 5000
description = "..."           # optional
```

### 7.2 Auth mixin (drivers that need credentials — SSH, REST)

```toml
auth_type               = "bearer"   # none | bearer | api_key | basic | ssh_key | ssh_password
auth_token_env          = "MY_TOKEN" # env var name — value never in config
auth_username_env       = "MY_USER"
auth_password_env       = "MY_PASS"
auth_ssh_key_path       = "~/.ssh/id_rsa"
auth_ssh_passphrase_env = "SSH_PASSPHRASE"
```

VISA, serial, and python_shell configs do **not** inherit `AuthConfig` — those
fields would be noise on them.

### 7.3 Documented mixin (devices with manuals — VISA)

`DocumentedConfig` adds `techmanual_document_ids: list[int]`, used for targeted
[techmanual.ai](https://techmanual.ai) lookups. Drivers that target generic
compute (SSH, REST, python_shell) do not inherit it by default; it can be added
to any config later without migration impact, since an empty list means "no
manuals."

### 7.4 Per-driver fields

| Driver | Adds |
|--------|------|
| `visa` | `resource_string`, `manufacturer`, `model_number`, `read_termination`, `write_termination`, `techmanual_document_ids` |
| `ssh` | `host`, `port`, `username` + auth |
| `rest` | `base_url`, `verify_ssl` + auth |
| `serial` | `serial_port`, `baud_rate`, `data_bits`, `parity`, `stop_bits`, `read_termination`, `write_termination` |
| `python_shell` | `python_path`, `working_dir` |

`serial_port` is deliberately named (not `port`) so it does not read ambiguously
next to SSH's integer `port` in side-by-side config examples.

### 7.5 Loader rules

- Unknown `type` → `ConfigError` listing valid types.
- Any path field (`auth_ssh_key_path`, `python_path`, `working_dir`) is run
  through `Path(value).expanduser()` at load time.
- The plural `techmanual_document_ids` is canonical; a legacy singular
  `techmanual_document_id` is accepted and converted to a one-element list.

See [examples/configs/](../examples/configs/) for a complete template per driver.

---

## 8. Session Management

`session.py` keeps `_sessions: dict[str, Session]` and exposes a three-state
lookup that distinguishes "no session" from "wrong type," so error messages and
recovery hints can be specific (a wrong-type result means the alias is in use by
a different driver — calling `connect()` would clobber it):

```python
def lookup(alias, expected_type) -> SessionLookup   # found / wrong_type / session
def get(alias, expected_type) -> Session | None      # Session iff found and type matches
```

### 8.1 Shared lifecycle flow

- **`connect(alias)`** — load config → `DRIVER_REGISTRY[type]` → if the driver's
  deps are missing, return an install hint → `driver.connect(config)` → inject
  device memory → return `ConnectResult`.
- **`disconnect(alias)`** — look up the session → `driver.disconnect(session)` →
  **always** deregister the alias afterward.
- **`diagnose(alias?)`** — with an alias, dispatch to `driver.diagnose(config)`;
  without one, run the system audit (§9).
- **`list_devices()`** — scan the config dir and return a list of dicts with
  `status` in `{"connected", "configured", "invalid"}`. `"configured"` means the
  TOML parsed — **not** that the device is reachable. Use `diagnose(alias)` or
  `connect(alias)` to check reachability.

### 8.2 Per-driver tool flow

Each registered tool follows the same shape:

1. `session_registry.lookup(alias, expected_type=cls.type_name)`; return a
   structured error if missing or wrong type.
2. Run the operation, wrapping native exceptions into `ReadResult` / `Result`.
3. Call `event_logger.log_event(...)` at every return point.

**Per-call timeout invariant.** For drivers like VISA where the library exposes
timeout as a resource attribute (not a per-call kwarg), every tool must reset
`session.raw.timeout = timeout_ms or session.config.timeout_ms` at the top of the
call. Never assume the previous call left it in any state — otherwise a long
debug query bleeds its timeout into later fast queries. Drivers whose library
takes a per-call timeout (httpx, paramiko `exec_command`) use that instead and
do not mutate session state.

### 8.3 Device memory is loaded by the shared layer

`load_device_memory(alias)` in `config.py` is the **single** reader of
`<config_dir>/<alias>.md`. Drivers return `device_memory=None`; the shared
`connect` / `diagnose` tools inject it by **re-constructing** the result with
`dataclasses.replace()` (post-hoc attribute assignment would not re-run
`__post_init__`). This keeps path resolution, encoding, and error-swallowing
uniform in one place.

### 8.4 Event logger contract

`log_event(**fields)` appends one JSONL entry per call. It **never raises** —
filesystem errors are swallowed so logging cannot affect tool behavior.
`LABLINK_LOG_DIR` is read on every call (so tests can redirect or disable it);
set it to `""` to disable logging.

- **Always present:** `ts` (auto), `op`, `alias`, `success`.
- **Where applicable:** `error`, `duration_ms`.
- **Driver-specific extras** pass through freely (`command`/`response` for VISA,
  `verb`/`path`/`status_code` for REST, etc.). Drivers must not emit fields that
  make no sense for their protocol.

**Credential redaction.** Free-form log fields can carry a secret the agent
inlined by mistake (e.g. a password in an `ssh_exec` command, a token in a REST
query path or echoed error). Redaction happens **at the logging boundary**: a
driver passes the secrets in scope for the call via `log_event(..., secrets=...)`
(from `redaction.secret_values(config)`), and `log_event` scrubs every free-form
string field — `error` and each string-valued extra — to `***` before
serialization. Centralizing it at the single write point means a driver cannot
leak a known credential by forgetting to wrap an individual field; the canonical
`op`/`alias` fields are structural and never scrubbed. The result returned to the
agent is left intact (the agent already holds the secret); only the durable log
is scrubbed. The SSH tools additionally set `metadata.security_warning` (via
`redaction.contains_secret`) when a known credential is detected inline.

Two limitations are accepted by design, not silently relied upon:

- **Known secrets only.** Only values held in the config's `auth_*_env` variables
  are in the secret set; a secret the agent invents from another source passes
  through. The agent-facing tool docstrings instruct agents never to inline
  secrets — redaction is the safety net, not the front line.
- **Literal-value matching, with a short-secret floor.** A secret is matched as
  its raw `os.environ` value, so a transformed copy (percent-encoded in a query
  string, base64 in a Basic-auth header) will not match. Values shorter than
  `redaction._MIN_SECRET_LEN` are excluded entirely: blanket substring
  replacement of a 1–3 character value corrupts unrelated log text far more than
  it protects.

---

## 9. Dependency Architecture & `diagnose()`

Dependencies fall into four layers:

1. **Python runtime** — `uv` is the single user-facing prerequisite; it installs
   and manages Python itself.
2. **Python packages** — optional extras per driver (`lablink-mcp[visa]`,
   `[ssh]`, `[rest]`, `[serial]`, `[all]`). All driver imports are lazy. A driver
   whose package is missing does not register its tools, and `connect()` for that
   type returns a structured error with the install command.
3. **System packages** — some drivers need OS-level libraries pip cannot install
   (e.g. `libusb` for VISA USB access). There is no programmatic install; the
   fix is surfacing them.
4. **The user's own Python environment** — vendor SDKs (`nidaqmx`, `picosdk`, …)
   with no VISA or network interface. The `python_shell` driver bridges to these
   by spawning a subprocess in a user-supplied interpreter.

**`diagnose()` with no alias is the system audit.** It iterates `DRIVER_REGISTRY`,
calls `check_python_deps()` (via `find_spec`, no side effects) and, where Python
deps are present, `system_dep_check()`. It returns a `DiagnosticResult` whose
`drivers` map gives each driver an exhaustive status
(`ready` / `missing_python` / `missing_system`) and whose `action_items` list is
ordered most-blocking first. There is no `"unknown"` state — an undeterminable
status is a driver bug, not a silent third option.

Installing a new extra does **not** retroactively add its tools to a running
server — the tool surface is fixed at startup. Restart the server to pick up a
newly installed driver.

---

## 10. Concurrency Model (v1)

FastMCP's stdio transport **serializes tool calls**: while one tool runs, every
other waits, including tools targeting different aliases. The practical
implications:

**Works today**
- Many sessions open at once, each addressed by alias.
- Fast interleaved request/response across devices (read A → compute → write B).

**Does not work in v1**
- Genuinely parallel tool execution.
- Watching a long acquisition on A while configuring B — a 30-second sweep blocks
  the whole server for 30 seconds.

Mitigations: keep `timeout_ms` short per device, prefer status-poll over
blocking-await for slow operations, and sequence long operations rather than
expecting overlap. Lifting this constraint would require an async dispatch
refactor or a different transport, deferred until real demand surfaces.

---

## 11. Streaming Driver Contract

The data model carries streaming hooks (`Session.buffer`,
`Session.buffer_thread`) and the SSH streaming tools exercise them. Any future
streaming driver must follow these rules:

1. **Bounded queue.** Create `session.buffer` with `maxsize=1000` by default;
   document any override. Overflow is drop-oldest. An unbounded queue is grounds
   for review pushback.
2. **Thread setup.** Always-on streaming drivers start the buffer thread in
   `connect()`. Hybrid drivers (like SSH) start it in a per-driver lifecycle tool
   (`ssh_start_stream`) that mutates `session.buffer` / `session.buffer_thread`
   in place.
3. **Thread teardown in `disconnect()`** (and the per-driver stop tool for hybrid
   drivers), with `thread.join(timeout=2.0)`. On join timeout, return
   `Result(success=True, metadata={"warning": ...})` rather than blocking.
4. **Exceptions stay in the thread.** Wrap the thread body in `try/except`; on
   error set `session.metadata["stream_error"]` and exit cleanly. The read tool
   checks `buffer_thread is None` **before** `is_alive()` (calling `is_alive()`
   on `None` is an `AttributeError`).
5. **Document batching.** Each read tool's docstring states whether it returns
   one item, a drained batch, or up to N items. `ReadResult.raw` is typed
   `str | bytes | list | None` to accommodate batches.

---

## 12. python_shell Wire Protocol

`python_shell` runs a persistent subprocess in a user-supplied interpreter,
speaking newline-delimited JSON over stdin/stdout. The bootstrap REPL ships at
`lablink/interfaces/python_shell/bootstrap.py`.

**Requests** (LabLink → subprocess): `{"id", "op": "exec"|"eval"|"shutdown", …}`.
**Responses** (subprocess → LabLink): `{"id", "op", "stdout", "stderr",
"result", "exception", "duration_ms"}`. `result` is `repr(value)` for `eval`
(JSON-safe; vendor objects rarely serialize). `exception` is `{"type",
"message", "traceback"}` when non-null. On start the bootstrap emits a `ready`
handshake with the Python version and interpreter path.

State persists across calls within a session — importing an SDK in one call and
using it in the next is the whole point.

**Failure modes**, tracked via a per-session `busy` flag:

- **Agent timeout, subprocess alive** → `ReadResult(success=True,
  timed_out=True)`; do not kill; `busy` stays set.
- **Subprocess crash** (`BrokenPipeError` or empty read) → `success=False` with a
  reconnect hint; `busy` cleared. No auto-restart.
- **Request while busy** → `success=False` ("session is busy") without touching
  the subprocess.
- **`disconnect()`** always terminates: `shutdown` → wait → SIGTERM → SIGKILL;
  always clears `busy`.

Captured stdout/stderr is soft-capped at 8 MB per call; over that, the response
sets `truncated`. Continuous/streaming subprocess output is out of scope for
`python_shell` (that is a streaming concern, §11).

> **Security note.** This driver executes arbitrary Python with the privileges of
> the LabLink process. Anyone able to send it tool calls can run code. This is by
> design — the operator has consented to giving their agent execution. Deployments
> that do not want this should not install the `[python_shell]` extra; without it,
> the driver never registers.

---

## 13. Adding a Driver

1. Create `lablink/interfaces/<type>/` with `driver.py`, `config.py`,
   `__init__.py`.
2. Subclass `LabLinkDriver[YourConfig]` and implement `connect`, `disconnect`,
   `diagnose`, `register_tools`, `register_cli_commands`. Override
   `check_python_deps()` (and `system_dep_check()` if it has OS-level deps).
3. Subclass `DriverConfig` (`@dataclass(kw_only=True)`). Inherit `AuthConfig` if
   it needs credentials; inherit `DocumentedConfig` only if it targets devices
   with manuals.
4. Register it — one line in each of `DRIVER_REGISTRY` and
   `DRIVER_CONFIG_REGISTRY`.
5. Write clear tool docstrings — they are the agent's only source of truth for
   each tool's parameters and per-protocol semantics.
6. Lazy-import the third-party dep inside `connect()` (and any tool that needs
   it), returning a structured install hint on `ImportError`. Never import it at
   module level.
7. Add `tests/interfaces/test_<type>.py` with full mock coverage (mock the
   underlying library; never open a real connection — mark hardware tests
   `@pytest.mark.skip`). Add `examples/configs/<type>_device.toml`.

No changes to `lablink/mcp_server.py` or `lablink/cli.py` are required.

---

## 14. Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LABLINK_CONFIG_DIR` | `~/.lablink/devices/` | Device config directory |
| `LABLINK_TOPOLOGY_FILE` | `~/.lablink/topology.toml` | System topology file; resolved independently of `LABLINK_CONFIG_DIR` |
| `LABLINK_VISA_BACKEND` | `@py` | PyVISA backend (`@py` or `@ni`) |
| `LABLINK_LOG_DIR` | `~/.lablink/logs/` | Event log directory; `""` disables logging |
| `TMAI_API_KEY` | — | techmanual.ai API key for agent-directed manual lookups (optional) |

---

## 15. System Topology

`system_topology` is a **shared system-level tool** (alongside the lifecycle four) that surfaces a machine-readable map of how the lab bench is physically wired — which ports connect to which, what signals flow, and what safety constraints apply.

### 15.1 Module placement

The subsystem is split across three existing module homes to match LabLink conventions:

- **Data models → `lablink/base.py`** — `Constraint`, `SystemNode`, `Link`, `NetEndpoint`, `Net`, `SystemTopology`, `DeviceConnections`. `ConnectResult` and `DiagnosticResult` each carry a `topology_context: DeviceConnections | None` field. `DiagnosticResult` also carries `topology_warnings: list[str]`.
- **TOML loading → `lablink/config.py`** — `load_system() -> SystemTopology | None` (the single reader of `topology.toml`; `None` when absent; raises `ConfigError` on malformed input) and `list_configured_aliases() -> list[str]` (the single home for the config-dir glob).
- **Graph logic → `lablink/system.py`** (new) — `device_slice(topology, alias)` and `validate_system(topology, known_aliases)`.

### 15.2 Data models

- `Constraint(severity, limit, note)` — `severity` is a plain `str` (not `Enum`) so it survives `asdict()` and preserves unrecognized values verbatim.
- `SystemNode(alias?, id?, role?)` — at least one of `alias` / `id` required.
- `Link(from_port, to_port, signal?, params, constraints)` — directed 2-endpoint connection.
- `Net(name, signal?, params, endpoints, constraints)` — n-ary shared bus.
- `DeviceConnections(alias, links, nets, neighbors, constraints)` — one device's topology slice; what the agent receives on `connect()` and `diagnose(alias)`.

### 15.3 Injection contract (extending §8.3)

`connect()` and `diagnose(alias)` inject `topology_context` the same way device memory is injected — via a single `dataclasses.replace()` call. Both guard the load with `try/except ConfigError`: a malformed `topology.toml` must not break a healthy device connection or diagnosis. A device with no matching wiring receives `topology_context=None` (not an empty slice).

### 15.4 Error isolation

`load_system()` raises `ConfigError` on malformed input. Hot-path callers each catch it:
- `connect()` / `diagnose(alias)` — catch, inject nothing, return device result unchanged.
- `_system_audit()` — catch, record the error in `topology_warnings` (never in `action_items`); `ready` is unaffected. Topology warnings are always separate from driver-dependency `action_items` so a wiring advisory is never mistaken for a required install step.

The `system_topology` tool is the one caller that surfaces parse errors directly to the agent (its job is to expose topology problems, not hide them).

### 15.5 `validate_system()` checks

1. **Unresolved port prefix** — a `link`/`net` endpoint whose prefix matches no node.
2. **Declared-but-unconfigured device** — a node `alias` with no `<alias>.toml`.
3. **Unknown `severity`** — a constraint whose value is outside `{info, warning, critical}`.
4. **alias/id collision** — a passive `id` that equals a managed node's `alias` (would be silently shadowed by alias-first port resolution).

All are soft warnings; `validate_system` never raises.
