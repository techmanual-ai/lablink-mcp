# LabLink MCP — Architecture & Implementation Plan

**Created:** 2026-05-28
**Last revised:** 2026-05-28 (architectural pivot — see §0 Revision Notes)
**Status:** Planning — pre-implementation. No code has been written against this spec.
**Replaces:** The single-protocol `agentlink-visa` design.

**Relationship to other docs:** `project_goal.md` is the strategic source of truth (vision, scope, non-goals). This document is the architectural source of truth (data models, tool surface, driver contract, phase order). They are complementary, not super/subordinate — both were rewritten on 2026-05-28 and are kept in sync. `system_architecture.md` documents the current on-disk layout and the migration mapping; the *target* architecture it describes is sourced from this document.

---

## 0. Revision Notes — For Future Agents

This document went through one major revision before any code was written. Future agents reading the codebase will benefit from knowing the intent shifts that landed here:

### 0.1 The pivot from agentlink-visa
The original v0.1 (agentlink-visa) was built primarily to *demo* techmanual.ai — show what an agent can do when it has both a manual database and a way to run SCPI commands. The square-wave demo was filmed on real hardware on 2026-05-27 and worked well. What that demo showed is that **DUT control is the product**. techmanual.ai is the documentation layer that takes a competent agent from ~85% (no manuals, pattern-matching from training data) to ~99% (manufacturer-verified specifics). The product hierarchy is now:

- **LabLink MCP (primary product):** the execution backbone — gives agents real control over real devices.
- **techmanual.ai (complementary service):** the knowledge backbone — surfaces manufacturer specs and SCPI references for whatever device the agent is talking to.

Future agents: do not revert to framing LabLink as "the techmanual demo." It's the other way around now.

### 0.2 The dropped uniform tool surface
An earlier draft of this plan promised a uniform MCP tool surface (`connect`, `query`, `write`, `read`, `custom_action`, `list_actions`, `diagnose`) shared across all drivers. The intent was "the agent never needs to know the transport." In practice this produced a leaky abstraction:
- `data` meant URL path for REST, command string for VISA/SSH, raw bytes for TCP
- `query` was GET-only for REST, blocking-exec for SSH
- REST had no `read()` and required a capability flag
- `body` was meaningful only for REST and silently discarded everywhere else
- Anything that didn't fit (PUT/PATCH/DELETE, streaming start/stop, shell sessions) fell into `custom_action` — making it the dumping ground rather than the escape hatch

We dropped the uniform surface and replaced it with **shared lifecycle tools + per-driver operation tools registered dynamically based on installed extras**. The driver ABC is now a code-sharing contract, not a tool-uniformity contract. See §2.2 and §4 for the new design.

### 0.3 The unified-repo decision (held)
The original agentlink-visa plan called for a family of sibling repos (agentlink-visa, agentlink-ssh, agentlink-rest, etc.). That decision was reversed for **user-friction reasons**: requiring users to install and configure multiple MCP servers to control a heterogeneous lab is a worse onboarding experience than one server with opt-in extras. The original concerns (different deps, different deployment, different distribution) are addressed by:
- **Different deps:** optional extras groups (`lablink-mcp[visa]`, `lablink-mcp[ssh]`, ...) + lazy imports inside `connect()`
- **Different deployment:** all drivers share the local-first install model; there is no server component anywhere
- **Different distribution:** single PyPI package; users install only the extras they need

Future agents: this decision is locked. Do not propose re-splitting the repo.

### 0.4 Streaming drivers deferred
v1 ships zero streaming drivers (no MQTT, no WebSocket, no continuous-data interfaces). The data model has the hooks for streaming (`Session.buffer`, `Session.buffer_thread`) and §6.5 defines the contract future streaming drivers must follow, but no v1 driver exercises them. Streaming is Phase 2+ work, gated on real hardware to validate against.

### 0.5 Kill criteria for v1 scope
The pivot from VISA-only to five drivers is justified by *architecture cost* (multi-driver dispatch is much cheaper to design now than to bolt on later), not by user demand for five drivers. We re-evaluate scope between phases. **Shipping LabLink with VISA + SSH + one well-validated additional driver is a perfectly good v1** if the others don't have a real user behind them. See §13 for the full re-evaluation gates between phases.

---

## 1. What LabLink MCP Is

LabLink MCP is a local-first MCP server that gives AI agents direct, structured control over the devices and services they need to talk to — test instruments, servers, microcontrollers, REST APIs, and so on. The agent uses one MCP server with one config directory, and gets a per-protocol tool surface that honestly reflects what each protocol can do.

**Design philosophy:**
- One MCP server, many protocol drivers, one install
- Tool names are honest about the protocol (`visa_query`, `ssh_exec`, `rest_get`) — no semantic overloading
- Only tools whose drivers have their deps installed are exposed to the agent
- Built-in drivers only for v1; designed for community contribution via PRs once public
- Public and forkable — generality and contributor clarity are first-class requirements

### 1.1 Relationship to techmanual.ai

LabLink and techmanual.ai are designed to be used together but neither depends on the other functionally. An agent with both loaded gets the full loop: look up SCPI command in the manual → send it via LabLink → observe the response → iterate.

- **LabLink without techmanual.ai:** the agent can still connect to and operate devices using whatever knowledge it has from training. Works well for common instruments; gets brittle for unusual models or vendor quirks.
- **techmanual.ai without LabLink:** the agent can produce code or commands for a human to run, but cannot close the loop itself.
- **Both together:** the agent can plan, execute, observe, and self-correct without a human in the middle. This is the founding use case.

LabLink reads the `TMAI_API_KEY` env var if present and surfaces relevant manual IDs (via the `techmanual_document_ids` field on T&M-domain driver configs) when the agent connects. It does not require techmanual.ai to function.

### 1.2 The Multi-Device Use Case

A key motivator is concurrent multi-device orchestration. An agent session may need to:
- Connect to instrument A (VISA) and embedded board B (serial) at the same time
- Read a measurement from A, compute something, write a configuration to B
- Pull calibration data from a REST API, apply it to the result

All of this from one MCP server, with each device identified by its alias. LabLink holds sessions open in a module-level registry; the agent addresses each one by name. The exact semantics of "concurrent" in v1 are tightly constrained — see §1.5.

### 1.3 Connectivity Landscape

The following protocols are in scope for the architecture. The list is intentionally small. Other protocols (i2c, spi, ble, can, snmp, opc_ua, zmq, modbus, ...) may eventually fit, but they have lifecycle and data-model differences that warp the abstraction if planned for prematurely. They will be considered case-by-case after v1 ships.

| Driver Key | Transport | Typical Devices |
|-----------|-----------|----------------|
| `visa` | USB-TMC, TCPIP, GPIB, Serial-VISA (via PyVISA) | Oscilloscopes, DMMs, signal generators, spectrum analyzers, power supplies, LCR meters, network analyzers |
| `ssh` | SSH (Paramiko) | Linux servers, Raspberry Pi, embedded Linux, network switches/routers, anything with an SSH daemon |
| `rest` | HTTP/HTTPS (httpx) | Cloud data services, DAQ REST APIs, device management portals, any HTTP API |
| `serial` | RS232 / RS422 / RS485 (pyserial) | Legacy instruments, industrial sensors, Arduino/microcontrollers, GPS receivers, PLCs without Ethernet |
| `python_shell` | Subprocess (configurable interpreter) | Interactive Python REPL bound to a user-supplied venv/conda env; lets agents leverage vendor SDKs (nidaqmx, picosdk, vendor instrument libs) that have no VISA or network interface |

Notes:
- RS232, RS422, and RS485 are all handled by the `serial` driver — they are electrical variants of the same byte-stream model. The distinction lives in wiring, not in the driver.
- GPIB is covered by `visa` via PyVISA's GPIB backend — no separate driver needed.
- `python_shell` is in scope despite being "just Python" — it is the designed escape hatch for vendor SDKs. The `python_path` config field points to a user-managed interpreter (conda env, project venv, system Python), and the driver spawns a subprocess in that environment. This makes vendor-proprietary toolchains accessible from agent sessions without bloating LabLink's own deps.

### 1.4 Streaming Protocols — Deferred

Buffer/poll protocols (MQTT, WebSocket, continuous serial streams) are *architecturally* supported via `Session.buffer` and `Session.buffer_thread` (§6) and the contract in §6.5. **No streaming driver ships in v1.** The data model exists so the first streaming driver doesn't require a rewrite, but no v1 code paths exercise the streaming machinery. Streaming-first protocols will be added after v1 lands and real streaming hardware is available to test against.

### 1.5 What Concurrency Looks Like in v1 (And What It Doesn't)

This is the single biggest ergonomics surprise a new LabLink user will hit, so it gets its own subsection rather than a buried paragraph.

**What works in v1:**
- Many sessions open simultaneously. `connect("scope_a")`, `connect("scope_b")`, `connect("lab_pi")`, `connect("daq_api")` — all four can coexist. Each is held open in `_sessions` keyed by alias.
- Fast interleaved request/response. The agent calls `visa_query(scope_a, ...)` → `rest_get(daq_api, ...)` → `serial_write(arduino, ...)` in sequence. Each completes before the next starts. With sub-100ms operations, this *feels* concurrent.
- Cross-device data flow within one logical task. Read from A, compute, write to B — the standard multi-device orchestration pattern that motivates the architecture.

**What does NOT work in v1:**
- Genuinely parallel tool execution. FastMCP's stdio transport serializes tool calls. While one tool is running, all others wait — including tools targeting different aliases.
- "Monitor a stream from A while polling B." Continuous-data drivers are deferred to post-v1 (§1.4), and even if you implemented one, the buffer-reading tool would still serialize against other tool calls.
- Long-running operations without blocking everything. A 30-second VISA sweep means no other device can be addressed for 30 seconds. If the agent calls a slow operation, it has to wait for it to return before doing anything else — including disconnecting it.

**Practical user implications:**
- Configure short `timeout_ms` defaults per device. A device that occasionally takes 10 seconds will block the whole server for 10 seconds.
- Slow operations should expose an early-return path where possible (status-poll rather than blocking-await).
- The "watch a long acquisition while configuring another device" workflow does not work in v1. Sequence them.

**Post-v1 mitigation paths (not committed):**
- FastMCP's async tool support could allow true parallelism per session if the dispatch layer is reworked.
- Streaming drivers (post-v1) decouple data acquisition from tool calls — but the read tool still serializes against everything else.

This constraint is a deliberate v1 simplification, not an oversight. Solving it requires either an async dispatch refactor or a different transport (SSE, WebSocket MCP), and the cost is not justified until real demand surfaces. See §13 for the post-v1 revisit gate.

---

## 2. Locked Design Decisions

These decisions were made in the founding planning sessions and must not be revisited without explicit instruction from the lead developer.

### 2.1 Single Unified Repo
All protocol drivers live in `lablink-mcp`. No sibling repos. Rationale recorded in §0.3. One install, one MCP server, all protocols.

### 2.2 Tool Surface: Shared Lifecycle + Per-Driver Operations
The MCP tool surface has two layers:

**Shared lifecycle tools** (always registered, work across all drivers, identified by alias):
| Tool | Description |
|------|-------------|
| `connect(alias)` | Load config, resolve driver via `DRIVER_REGISTRY`, dispatch to `driver.connect()`. Returns `ConnectResult`. |
| `disconnect(alias)` | Look up session, dispatch to the owning driver's `disconnect()`. Always removes the alias from the session registry. |
| `list_devices()` | List all configured aliases with their `type`, `description`, and current connection status. |
| `diagnose(alias?)` | With alias: dispatch to `driver.diagnose(config)`. Without alias: system audit (loops `DRIVER_REGISTRY` and calls each driver's `system_dep_check()` and `check_python_deps()`). |

**Per-driver operation tools** (registered only when the driver's Python deps are present):
- VISA: `visa_query`, `visa_write`
- SSH (Phase 1): `ssh_exec`, `ssh_shell_session`
- SSH (Phase 1.5, gated on real Phase 1 feedback): `ssh_start_stream`, `ssh_stop_stream`, `ssh_read_stream`
- REST: `rest_get`, `rest_post`, `rest_put`, `rest_patch`, `rest_delete`
- Serial: `serial_query`, `serial_write`, `serial_read`, `serial_flush`
- python_shell: `python_shell_exec`, `python_shell_eval`

Each driver's `register_tools(mcp)` method registers its operation tools with the FastMCP server at startup. Drivers whose Python deps are missing skip registration; their tools never appear in the agent's surface.

**Why this shape instead of a uniform surface:** see §0.2. Honest names beat overloaded names; the agent learns one signature per tool instead of seven driver-specific reinterpretations of `data`.

**Tool count budget:** with this model, a typical user has 4 shared + 2–5 per driver × 2–3 installed drivers ≈ 10–20 visible tools. A full v1 `[all]` install is 19 tools (4 shared + 2 VISA + 2 SSH Phase 1 + 5 REST + 4 serial + 2 python_shell); +3 after Phase 1.5 lands SSH streaming = 22. Comfortably inside the range where modern frontier models select tools reliably (~50–100 ceiling before degradation).

**Agent discovery flow:** an agent walking into an unfamiliar LabLink install should follow this sequence:
1. Read `_INSTRUCTIONS` for the multi-driver architecture overview and the count of currently-loaded drivers (injected at startup — see Phase 0c task 3).
2. Call `list_devices()` for configured aliases — returns which devices the user has set up and what type each is.
3. Call `diagnose()` (no alias) only if something looks broken — surfaces missing deps, system-level issues, and the install commands to fix them.

Tool docstrings are discovered through FastMCP's standard mechanism (auto-listed by the client); no special call is needed. Tool names follow the `<type>_<operation>` convention; per-protocol semantics are in each tool's docstring.

`_INSTRUCTIONS` is responsible for documenting this flow explicitly (one short paragraph). Phase 0c task 3 covers writing it.

### 2.3 Protocol Dispatch via Config `type` Field
The config `type` field maps to a driver in the internal registry. `connect()` and `diagnose(alias)` look up `DRIVER_REGISTRY[config.type]` and dispatch. No protocol-conditional logic anywhere except inside driver implementations.

### 2.4 Credentials — Env Var Reference Only
Config files never contain secrets. Credentials are referenced by environment variable name. The env var holds the actual value. This applies to all drivers that need auth.

```toml
auth_type = "bearer"
auth_token_env = "MY_SERVICE_TOKEN"   # reads os.environ["MY_SERVICE_TOKEN"]
```

Supported `auth_type` values (driver-dependent): `none`, `bearer`, `api_key`, `basic`, `ssh_key`, `ssh_password`.

### 2.5 Built-in Drivers Only (v1)
All drivers live in the repo. Community contributions are PRs. The driver registry is a plain dict in `lablink/interfaces/__init__.py`. This decision is revisable once the project is public and driver volume warrants a plugin/entry-point system.

### 2.6 Error Handling
All tools return structured dicts on failure rather than raising. The agent can reason about and retry failures.

```json
{"success": false, "error": "SSH auth failed", "hint": "Check that SSH_KEY_PATH points to a valid private key and the host has your public key in authorized_keys."}
```

### 2.7 Device Memory (Carried Forward)
The `~/.agentlink/instruments/<alias>.md` instrument memory pattern is retained and renamed. The new path is `~/.lablink/devices/<alias>.md`. The field name on `ConnectResult` is `device_memory` (was `instrument_memory`) to reflect that LabLink now talks to non-instrument devices too. Agents continue to write compact, one-line quirk notes per device per category header.

**Field rename is a soft breaking change.** Any agent prompt or user CLAUDE.md that references `instrument_memory` will silently miss the new field. Mitigation: through Phase 1, `ConnectResult.__post_init__` (see §3) auto-populates `instrument_memory` from `device_memory` whenever the latter is set. Drivers only ever set `device_memory`; the alias is maintained by the dataclass, not by per-driver code. `instrument_memory` is marked deprecated and removed in Phase 2. The dual-field window gives agents one release to update prompts.

### 2.8 Dependency Architecture
- `uv` is the single user-facing prerequisite. It handles Python version management and package installation. No other tool needs to be pre-installed.
- Python package dependencies are managed via **optional extras** groups in `pyproject.toml`. The core install pulls only FastMCP, Click, and `tomli`/`tomllib`. Driver deps are opt-in.
- All driver imports are **lazy** — a missing dep returns a structured error from `connect()` with the install command, not a server-startup crash.
- A driver whose Python deps are not installed **does not register its tools**. The MCP surface only contains tools that can actually work.
- `diagnose()` with no alias performs a **full system audit**: checks installed driver extras, their system-level dependencies, and reports what is missing with platform-appropriate install instructions.
- Docker is explicitly **not** a primary install target. USB and serial passthrough into containers adds friction that defeats the point of local lab use. A community-contributed `docs/deployment/docker.md` is welcome but not maintained as a first-class path.

---

## 3. Data Models

Defined in `lablink/base.py`. All tool return values use these types (serialized to dict for MCP transport).

### 3.0 Why three result types and not one

`Result`, `ReadResult`, and `ConnectResult` exist as separate types because they carry orthogonal information that no single shape captures cleanly:

- **`ConnectResult`** carries identity, device memory, and documentation pointers (`identity`, `device_memory`, `techmanual_document_ids`, `interface_type`). These are populated once per session by `connect()` and irrelevant to every other tool.
- **`ReadResult`** carries `raw` / `decoded` / `format` / `timed_out` — the four-way distinction between "data arrived," "nothing yet, try again," "broken stream," and "decoded into a structured form." This shape is the right one for any tool that returns data: queries, reads, exec stdout, REST GET bodies.
- **`Result`** is the bare success/failure carrier for tools that don't return data — `disconnect()`, fire-and-forget writes where the driver has nothing useful to return.

A single `ToolResult` with optional fields was considered and rejected: the optional-field model invites drivers to forget which fields apply to which tool and forces the agent to check field presence on every call. Three types with clear contracts are easier to read and easier to type-check.

**Per-tool return type map.** Every v1 tool returns one of the three types. Write-style tools default to `ReadResult` (not `Result`) whenever the operation produces useful metadata (exit code, status code, bytes written). `Result` is reserved for operations with truly no payload — exclusively `disconnect()` in v1.

| Tool | Return type | Reasoning |
|------|-------------|-----------|
| `connect` | `ConnectResult` | identity, memory, doc IDs |
| `disconnect` | `Result` | true no-payload |
| `list_devices` | (list of dicts; not one of the three types) | §6.1 shape |
| `diagnose` | `DiagnosticResult` | structured per §3 |
| `visa_query` | `ReadResult` | response string |
| `visa_write` | `ReadResult` | success/error only; `raw=None`; carries no useful metadata but uses ReadResult for surface consistency |
| `ssh_exec` | `ReadResult` | stdout in `raw`; `metadata={"exit_code", "stderr"}` is load-bearing — agents reason about exit codes |
| `ssh_shell_session` | `ReadResult` | full transcript in `raw` |
| `ssh_start_stream` (1.5) | `Result` | acknowledgement only; data arrives via `ssh_read_stream` |
| `ssh_stop_stream` (1.5) | `ReadResult` | final transcript in `raw` |
| `ssh_read_stream` (1.5) | `ReadResult` | buffered output |
| `rest_get` | `ReadResult` | body in `raw`/`decoded`; `metadata={"status_code", "headers"}` |
| `rest_post`/`put`/`patch` | `ReadResult` | response body may be empty but `status_code` is always meaningful |
| `rest_delete` | `ReadResult` | `status_code` even when body empty |
| `serial_query` | `ReadResult` | response bytes/string |
| `serial_write` | `ReadResult` | `metadata={"bytes_written"}` |
| `serial_read` | `ReadResult` | drained bytes |
| `serial_flush` | `Result` | true no-payload |
| `python_shell_exec` | `ReadResult` | stdout/stderr/result/exception (see §9 Phase 4 wire protocol) |
| `python_shell_eval` | `ReadResult` | same |

Rule of thumb for future drivers: if the operation can reasonably surface useful metadata (status code, exit code, bytes count) even on success, return `ReadResult`. Only use `Result` when literally success/failure is the entire signal.

### 3.1 Mandatory dataclass rule: `kw_only=True` for all configs

**All `DriverConfig` subclasses (and the base, and all mixins) MUST be declared with `@dataclass(kw_only=True)`.** Reason: without `kw_only=True`, Python's dataclass field ordering across multiple inheritance bases is order-of-MRO-sensitive. As soon as any subclass introduces a required field (no default) after a mixin contributes defaulted fields, class construction fails with `TypeError: non-default argument follows default argument`. With `kw_only=True`, all fields are keyword-only and ordering becomes irrelevant — required and defaulted fields can interleave freely across the MRO.

Concretely, this means every config dataclass in the codebase, including the base:

```python
@dataclass(kw_only=True)
class DriverConfig:
    alias: str
    type: str
    timeout_ms: int
    description: str | None = None

@dataclass(kw_only=True)
class AuthConfig:
    auth_type: str = "none"
    # ... etc

@dataclass(kw_only=True)
class DocumentedConfig:
    techmanual_document_ids: list[int] = field(default_factory=list)

@dataclass(kw_only=True)
class SshDriverConfig(DriverConfig, AuthConfig):
    host: str                    # required, no default — works under kw_only
    username: str                # required, no default — works under kw_only
    port: int = 22
```

This rule applies to `Result`, `ReadResult`, `ConnectResult`, `DiagnosticResult`, `SystemDepStatus`, `Session`, and every driver-specific config subclass. Do not omit `kw_only=True` even on classes that "work today" — a future required field added to any subclass will silently break the project.

```python
from dataclasses import dataclass, field
from typing import Any, ClassVar, Generic, TypeVar
from queue import Queue
from threading import Thread

ConfigT = TypeVar("ConfigT", bound="DriverConfig")


@dataclass(kw_only=True)
class Result:
    """Generic tool result — used by disconnect() and write-style tools with no payload."""
    success: bool
    error: str | None = None
    hint: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class ReadResult:
    """Tool result for any tool that returns data — queries, reads, exec stdout, etc."""
    success: bool
    raw: str | bytes | list | None = None  # See §6.5 for streaming-driver batching rules.
    decoded: Any | None = None             # Driver-parsed form when applicable (e.g., REST JSON body).
    format: str = "text"                   # "text" | "json" | "bytes" — what raw contains.
    encoding: str = "utf-8"                # Character encoding when format is "text" or "json".
    timed_out: bool = False                # True when a read tool returned because timeout elapsed, not because data arrived.
    metadata: dict = field(default_factory=dict)  # e.g. {status_code: 200, exit_code: 0, stderr: "..."}
    error: str | None = None
    hint: str | None = None


# read-style tool semantics (locked):
#   Data arrived:          ReadResult(success=True, raw=<data>, timed_out=False)
#   Timeout, no data:      ReadResult(success=True, raw=None, timed_out=True)
#   Broken/dead stream:    ReadResult(success=False, error="...", hint="...")
# The agent distinguishes "nothing yet, try again" from "something is wrong."


@dataclass(kw_only=True)
class ConnectResult:
    success: bool
    alias: str
    interface_type: str                   # "visa" | "ssh" | "rest" | ...
    identity: str | None = None           # *IDN?, SSH banner, HTTP server header, etc.
    device_memory: str | None = None      # content of <alias>.md if present
    instrument_memory: str | None = None  # DEPRECATED alias of device_memory; auto-populated by
                                          # __post_init__ for backwards compat through Phase 1;
                                          # field removed in Phase 2. Do not read in new code.
    techmanual_document_ids: list[int] = field(default_factory=list)  # only populated by T&M-domain drivers
    metadata: dict = field(default_factory=dict)
    error: str | None = None
    hint: str | None = None

    def __post_init__(self) -> None:
        # Mirror device_memory → instrument_memory at construction time.
        # IMPORTANT: __post_init__ runs once, at construction. Post-hoc attribute writes
        # (`result.device_memory = "..."`) do NOT re-trigger this and leave instrument_memory
        # stale at None. Code paths that populate device_memory *after* the dataclass exists
        # must use dataclasses.replace() to get a fresh instance with __post_init__ re-run.
        # See §6.3.1 for the shared connect tool's use of replace() to inject device_memory.
        # When instrument_memory is removed in Phase 2, this method is deleted entirely.
        if self.device_memory is not None and self.instrument_memory is None:
            self.instrument_memory = self.device_memory


@dataclass(kw_only=True)
class DiagnosticResult:
    ready: bool
    alias: str | None = None              # None when called without alias (system audit)
    interface_type: str | None = None
    checks: dict = field(default_factory=dict)   # per-alias diagnose: {check_name: {status, detail}}
    drivers: dict = field(default_factory=dict)  # system audit: {type: {python_deps, system_deps, status}}
    action_items: list[str] = field(default_factory=list)  # ordered most-blocking first
    device_memory: str | None = None
    error: str | None = None


@dataclass(kw_only=True)
class SystemDepStatus:
    name: str                             # e.g. "libusb", "NI-VISA"
    present: bool
    version: str | None = None
    install_hint: str | None = None       # platform-appropriate install command


# --- Config types ---

@dataclass(kw_only=True)
class DriverConfig:
    """Base config — fields shared by every driver. Driver-specific subclasses add their own."""
    alias: str
    type: str
    timeout_ms: int
    description: str | None = None


@dataclass(kw_only=True)
class AuthConfig:
    """Mixin for drivers that need authentication. Inherited by SshDriverConfig, RestDriverConfig.
    Drivers without auth (VISA, serial, python_shell) do NOT inherit this — those fields would
    be noise on their configs.
    """
    auth_type: str = "none"                       # none | bearer | api_key | basic | ssh_key | ssh_password
    auth_token_env: str | None = None
    auth_username_env: str | None = None
    auth_password_env: str | None = None
    auth_ssh_key_path: str | None = None          # tilde expanded by config.py via Path(value).expanduser()
    auth_ssh_passphrase_env: str | None = None


@dataclass(kw_only=True)
class DocumentedConfig:
    """Mixin for drivers that connect to documented devices (T&M instruments). Inherited by
    VisaDriverConfig. Drivers that talk to generic compute (ssh, rest, python_shell) do not
    inherit this by default — manuals are not typically the right shape for those targets.

    The mixin can be added to any driver's config later without a migration impact, since
    techmanual_document_ids defaults to [] and consumers (the `connect()` response and
    `_INSTRUCTIONS` pointers) treat an empty list as "no manuals." A programmable network
    analyzer controlled by SSH, for example, would inherit both AuthConfig and DocumentedConfig
    without breaking existing SSH configs.
    """
    techmanual_document_ids: list[int] = field(default_factory=list)


# Examples of driver-specific subclasses (defined in lablink/interfaces/<type>/config.py):
#
#   @dataclass(kw_only=True)
#   class VisaDriverConfig(DriverConfig, DocumentedConfig):
#       resource_string: str = ""
#       manufacturer: str = ""
#       model_number: str = ""
#       read_termination: str = "\n"
#       write_termination: str = "\n"
#
#   @dataclass(kw_only=True)
#   class SshDriverConfig(DriverConfig, AuthConfig):
#       host: str = ""
#       port: int = 22
#       username: str = ""
#
#   @dataclass(kw_only=True)
#   class RestDriverConfig(DriverConfig, AuthConfig):
#       base_url: str = ""
#
#   @dataclass(kw_only=True)
#   class SerialDriverConfig(DriverConfig):
#       serial_port: str = ""              # named serial_port (not port) for reader clarity —
#                                          # config examples appear side-by-side in docs, and a
#                                          # bare `port` field invites the reader to ask "which
#                                          # driver's port?" The classes can't collide; the name
#                                          # disambiguates the human reading the docs.
#       baud_rate: int = 115200
#       data_bits: int = 8
#       parity: str = "none"
#       stop_bits: int = 1
#       read_termination: str = "\n"
#       write_termination: str = "\n"
#
#   @dataclass(kw_only=True)
#   class PythonShellDriverConfig(DriverConfig):
#       python_path: str = ""              # path to interpreter; tilde-expanded
#       working_dir: str | None = None


# --- Session ---

@dataclass(kw_only=True)
class Session(Generic[ConfigT]):
    """A live connection registered in _sessions.

    Session is Generic[ConfigT] so each driver can declare its config type once
    (class VisaDriver(LabLinkDriver[VisaDriverConfig])) and avoid per-method
    cast(VisaDriverConfig, session.config) boilerplate. The added complexity is
    one type parameter; the win is type-safe access to driver-specific config
    fields throughout the driver implementation.
    """
    alias: str
    interface_type: str
    raw: Any                              # native connection object (pyvisa.Resource, paramiko.SSHClient, etc.)
    config: ConfigT
    buffer: Queue | None = None           # populated by streaming-aware drivers; None for request/response
    buffer_thread: Thread | None = None
    metadata: dict = field(default_factory=dict)  # e.g. {"stream_error": "..."} set by buffer thread on failure
```

---

## 4. The Driver Contract

Defined as an ABC in `lablink/base.py`. The ABC is a **code-sharing contract**, not a tool-uniformity contract. Drivers register their own MCP tools via `register_tools()`.

```python
from abc import ABC, abstractmethod
from typing import ClassVar, Generic

class LabLinkDriver(ABC, Generic[ConfigT]):
    """Base class for all LabLink drivers. Generic over the driver's config subclass."""

    type_name: ClassVar[str]              # "visa", "ssh", etc. — must match the DRIVER_REGISTRY key.

    # --- Lifecycle ---

    @abstractmethod
    def connect(self, config: ConfigT) -> ConnectResult:
        """Open the connection and register a Session in lablink.session._sessions.

        On success: construct a Session(alias, interface_type, raw, config, ...),
                    call session_registry.register(session), and return
                    ConnectResult(success=True, ...).
        On failure: return ConnectResult(success=False, error=..., hint=...).
                    Do NOT register a session. Clean up any partial state.

        Lazy import all third-party deps inside this method, not at module load.
        A missing dep returns ConnectResult(success=False, error="Missing dependency: <pkg>",
        hint="Run: pip install lablink-mcp[<extra>]").
        """
        ...

    @abstractmethod
    def disconnect(self, session: Session[ConfigT]) -> Result:
        """Close the native connection and tear down any buffer thread.

        After this returns (success or failure), the shared disconnect() tool
        removes the alias from _sessions regardless. Stateless drivers (REST)
        may return Result(success=True) immediately.
        """
        ...

    @abstractmethod
    def diagnose(self, config: ConfigT) -> DiagnosticResult:
        """Per-alias diagnosis. Receives a config (NOT a session) — diagnostics
        are stateless and work whether or not a session is open. May perform
        fresh test connections (TCP reachability, auth check, etc.); does not
        inspect any existing open session.
        """
        ...

    # --- Tool registration ---

    @abstractmethod
    def register_tools(self, mcp) -> None:
        """Register this driver's operation tools with the FastMCP server.

        Called once at server startup if and only if check_python_deps() reports
        all deps present. Use FastMCP's @mcp.tool() decorator inside this method.
        Each registered tool is responsible for:
            1. Looking up the session by alias via session_registry.get(alias, expected_type=cls.type_name)
            2. Returning a structured error dict if no session or wrong type
            3. Wrapping native exceptions and returning ReadResult/Result dicts
            4. Calling event_logger.log_event() at every return point

        Tool docstrings are surfaced to the agent as MCP tool descriptions. Make
        them explicit about what each parameter means for this protocol.
        """
        ...

    @abstractmethod
    def register_cli_commands(self, cli_group) -> None:
        """Register this driver's CLI subgroup with the root Click group.

        Called from cli.py at startup, with the same dep-presence gating as
        register_tools. A driver that has no useful CLI surface (rare; if you
        have tools, you should expose them in the CLI for debugging) may
        implement this as `pass` and document why.
        """
        ...

    # --- System audit hooks (called by tools.py during no-alias diagnose) ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        """Return [(package_name, is_available), ...] for each Python dep.
        Use importlib.util.find_spec(pkg_name) — not try/import — to avoid side effects.
        Default: empty list (driver has no Python-level deps).
        """
        return []

    @classmethod
    def system_dep_check(cls) -> list[SystemDepStatus]:
        """Return one SystemDepStatus per OS-level dep this driver requires.
        Drivers with no system deps return []. Default: empty list.
        """
        return []
```

**Driver registration** — `lablink/interfaces/__init__.py`:

```python
DRIVER_REGISTRY: dict[str, type[LabLinkDriver]] = {
    "visa":         VisaDriver,
    "ssh":          SshDriver,
    "rest":         RestDriver,
    "serial":       SerialDriver,
    "python_shell": PythonShellDriver,
}

DRIVER_CONFIG_REGISTRY: dict[str, type[DriverConfig]] = {
    "visa":         VisaDriverConfig,
    "ssh":          SshDriverConfig,
    "rest":         RestDriverConfig,
    "serial":       SerialDriverConfig,
    "python_shell": PythonShellDriverConfig,
}

# Runtime check at module import. Uses if/raise rather than assert because
# Python -O strips assertions — and a registry-sync check that disappears
# under optimization is worse than no check at all. Drift would otherwise
# surface as a confusing KeyError deep in config.py instead of a clear
# "you forgot to update one of the two registries" message at startup.
if DRIVER_REGISTRY.keys() != DRIVER_CONFIG_REGISTRY.keys():
    raise RuntimeError(
        f"DRIVER_REGISTRY and DRIVER_CONFIG_REGISTRY key sets must match. "
        f"Diff: registry-only={DRIVER_REGISTRY.keys() - DRIVER_CONFIG_REGISTRY.keys()}, "
        f"config-only={DRIVER_CONFIG_REGISTRY.keys() - DRIVER_REGISTRY.keys()}."
    )
```

Adding a new driver requires one entry in each registry — no changes to `config.py`, `tools.py`, or `mcp_server.py`. The import-time check catches forgetting one of the two; Phase 0b includes an explicit test for this case (`test_dispatch.py::test_registry_keys_match`).

Future cleanup option (deferred to post-v1): unify into a single registry of tuples (`{"visa": (VisaDriver, VisaDriverConfig), ...}`). Would eliminate the drift class entirely. Held off for now because the parallel-dict form reads more clearly in PR diffs when only one side is touched.

### 4.1 Tool registration flow

At server startup, `mcp_server.py`:

1. Instantiates the FastMCP server.
2. Registers shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`) directly. These tools use `DRIVER_REGISTRY` to dispatch.
3. For each driver in `DRIVER_REGISTRY`:
   - Calls `driver.check_python_deps()`.
   - If all deps present: instantiate the driver and call `driver.register_tools(mcp)`.
   - If any dep missing: skip registration. Log a stderr notice listing which extras to install.
4. Starts the FastMCP stdio loop.

The agent sees only tools whose drivers have working deps. There is no "tool exists but capability flag says no" pattern.

### 4.2 Driver instance lifecycle

**Driver instances are server-lifetime singletons** held by `mcp_server.py` in a module-level dict keyed by `type_name`. Instantiated once at startup if their deps are present; never re-instantiated. The same instance handles every call to `connect`, `disconnect`, `diagnose`, and every per-driver tool registered by `register_tools()`.

Per-driver tools registered via `@mcp.tool()` close over the driver instance. The FastMCP decorator captures the instance reference at registration time. This is the standard Python closure pattern; it requires no special handling.

**State placement:**
- **Per-session state** (native connection handles, per-device buffers, in-flight request counters) belongs on the `Session` object. Created in `driver.connect()`, destroyed in `driver.disconnect()`.
- **Cross-session driver state** (shared `httpx` connection pools, shared `pyvisa.ResourceManager`, shared subprocess pools, vendor-SDK module-level handles) belongs on the driver instance — i.e., set as instance attributes in `__init__` or lazily inside the first `connect()` call.
- **Process-wide state** (the session registry itself, the event logger) belongs in module-level dicts in `lablink/session.py` and `lablink/event_logger.py` respectively.

The shared `pyvisa.ResourceManager` is the canonical example: `VisaDriver.__init__` does not instantiate it (lazy import constraint); the first `connect()` call lazy-imports pyvisa and constructs the RM, storing it on `self._rm`; subsequent connects reuse it. This is the agentlink-visa pattern carried forward.

**What to avoid:**
- Holding session state on driver instance attributes (it leaks across sessions).
- Module-level mutable state inside `lablink/interfaces/<type>/driver.py` (defeats the singleton pattern and complicates testing — driver tests should be able to instantiate fresh drivers).

---

## 5. Config Schema

### 5.1 Base fields (required for all drivers)

```toml
type        = "visa"           # must match a key in DRIVER_REGISTRY
alias       = "bench_scope"    # unique; convention: <vendor>_<model> or <role>_<host>
timeout_ms  = 5000
description = "..."            # optional, human-readable
```

### 5.2 Auth block (optional, inherited only by drivers that need auth)

```toml
auth_type               = "bearer"        # none | bearer | api_key | basic | ssh_key | ssh_password
auth_token_env          = "MY_TOKEN"      # env var name — value never in config
auth_username_env       = "MY_USER"
auth_password_env       = "MY_PASS"
auth_ssh_key_path       = "~/.ssh/id_rsa" # tilde expanded by config.py
auth_ssh_passphrase_env = "SSH_PASSPHRASE"
```

VISA, serial, and python_shell configs **do not** carry these fields — their configs do not inherit `AuthConfig`.

### 5.3 Per-driver extras

**VISA** (`VisaDriverConfig(DriverConfig, DocumentedConfig)`):
```toml
resource_string         = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer            = "Tektronix"
model_number            = "MSO44"
read_termination        = "\n"
write_termination       = "\n"
techmanual_document_ids = [1291, 1323]   # [user_manual_id, programming_guide_id]; from DocumentedConfig mixin
```

**SSH** (`SshDriverConfig(DriverConfig, AuthConfig)`):
```toml
host     = "192.168.1.42"
port     = 22
username = "pi"
# auth_type + key/password fields from §5.2
```

**REST** (`RestDriverConfig(DriverConfig, AuthConfig)`):
```toml
base_url = "https://daq.local/api/v1"
# auth block as needed
```

**Serial** (`SerialDriverConfig(DriverConfig)`):
```toml
serial_port       = "/dev/ttyUSB0"  # or "COM3" on Windows; named serial_port to avoid colliding with ssh port (int)
baud_rate         = 115200
data_bits         = 8
parity            = "none"
stop_bits         = 1
read_termination  = "\n"
write_termination = "\n"
```

**python_shell** (`PythonShellDriverConfig(DriverConfig)`):
```toml
python_path = "~/miniconda3/envs/labwork/bin/python"  # tilde expanded
working_dir = "/Users/me/projects/lab"                # optional
```

Configs live at `~/.lablink/devices/<alias>.toml`. Override via `LABLINK_CONFIG_DIR`. The directory is renamed from `~/.agentlink/instruments/` during Phase 0 migration.

### 5.4 Validation rules (enforced in `lablink/config.py`)

- If the TOML `type` field does not match any key in `DRIVER_CONFIG_REGISTRY`: raise `ConfigError(f"Unknown driver type '{type}'. Valid types: {sorted(DRIVER_CONFIG_REGISTRY.keys())}.")`
- Any field that accepts a filesystem path (`auth_ssh_key_path`, `python_path`, `working_dir`): processed with `Path(value).expanduser()` at load time. TOML does not auto-expand tildes.
- The plural `techmanual_document_ids: list[int]` is the canonical form. The legacy singular `techmanual_document_id: int` is accepted at load time and auto-converted to a one-element list for migrated configs.

---

## 6. Session Management

`lablink/session.py` maintains a module-level dict keyed by alias:

```python
_sessions: dict[str, Session] = {}

def register(session: Session) -> None: ...
def deregister(alias: str) -> None: ...

# Three-state lookup. Distinguishes "no session" from "wrong type" so error
# messages and recovery hints can be specific. A wrong-type result tells the
# agent the alias is in use by a different driver — calling connect() would
# clobber the existing session, which is the bug we're defending against.
def lookup(alias: str, expected_type: str) -> SessionLookup: ...

@dataclass
class SessionLookup:
    found: bool
    wrong_type: bool                        # True iff a session exists but its interface_type differs from expected_type
    session: Session | None = None          # populated only when found=True and wrong_type=False
    actual_type: str | None = None          # populated only when wrong_type=True

# Convenience wrapper that drivers use most often:
def get(alias: str, expected_type: str) -> Session | None: ...
# Returns the Session iff found AND type matches. Returns None in both the
# "no session" and "wrong type" cases — drivers that need to disambiguate
# the error message use lookup() instead.
```

`Session` is the persistent record (defined in §3). Buffer/poll fields default to `None`; streaming drivers initialize them per §6.5.

### 6.1 Lifecycle tool flow

`connect(alias)` (shared tool, in `mcp_server.py`):
1. Load config from disk via `config.py`. On `ConfigError`, return structured error.
2. Look up driver via `DRIVER_REGISTRY[config.type]`.
3. If the driver is not registered (deps missing), return error explaining which extra to install.
4. Call `driver.connect(config)` — driver owns session creation and registration.
5. Return `ConnectResult` to agent.

`disconnect(alias)` (shared tool):
1. Look up session via `session_registry.get(alias)`. Return error if not found.
2. Look up driver via `DRIVER_REGISTRY[session.interface_type]`.
3. Call `driver.disconnect(session)`.
4. Always call `session_registry.deregister(alias)` regardless of return value.
5. Return result to agent.

`diagnose(alias=None)` (shared tool):
- With alias: load config, look up driver, call `driver.diagnose(config)`. Driver returns `DiagnosticResult`.
- Without alias: iterate `DRIVER_REGISTRY`, call `check_python_deps()` and `system_dep_check()` on each, build a system-audit `DiagnosticResult`.

`list_devices()` (shared tool):
- Scan `~/.lablink/devices/*.toml`, return a list of dicts (not a dict-of-dicts):
  ```python
  [
      {"alias": "bench_scope", "type": "visa", "description": "...", "status": "connected"},
      {"alias": "lab_pi",      "type": "ssh",  "description": "...", "status": "configured"},
      {"alias": "broken_cfg",  "type": None,   "description": None,  "status": "invalid",
       "error": "Unknown driver type 'modbus'."},
  ]
  ```
  `status` values:
  - `"connected"` — session is currently open in `_sessions`.
  - `"configured"` — config TOML parsed successfully; **no reachability check has been performed**. "Configured" does not mean "ready to use" — the device may be powered off, the network unreachable, the credentials wrong. Agents that need to know whether the device responds should call `diagnose(alias)` or `connect(alias)`.
  - `"invalid"` — config failed to load. `error` populated; other fields may be partial.

  A separate `"reachable"` status was considered and rejected for v1 — it would require per-alias network/USB probing on every `list_devices` call, which is too expensive to do unconditionally. If reachability surfacing becomes important, `list_devices(probe=True)` is the natural extension.

### 6.2 Per-driver tool flow

Each tool registered by `driver.register_tools(mcp)` follows the same shape:

```python
@mcp.tool()
def visa_query(alias: str, command: str, timeout_ms: int | None = None) -> dict:
    """Send a SCPI query to a VISA instrument (write + read atomically).

    Args:
        alias: configured device alias (must be a VISA-type alias)
        command: SCPI command string, e.g. "MEAS:FREQ? CH1"
        timeout_ms: per-call override; defaults to the config's timeout_ms
    """
    lookup = session_registry.lookup(alias, expected_type="visa")
    if not lookup.found:
        result = ReadResult(success=False, error=f"No open session for '{alias}'.",
                            hint="Call connect(alias) first.")
        event_logger.log_event(op="visa_query", alias=alias, command=command,
                               success=False, error=result.error)
        return asdict(result)
    if lookup.wrong_type:
        result = ReadResult(
            success=False,
            error=f"Alias '{alias}' has an open {lookup.actual_type} session, not a VISA session.",
            hint=f"Use a {lookup.actual_type}_* tool for this alias, or disconnect and re-add the alias config with type='visa'.",
        )
        event_logger.log_event(op="visa_query", alias=alias, command=command,
                               success=False, error=result.error)
        return asdict(result)
    session = lookup.session

    # Every call sets the resource timeout from scratch before use — no restore needed
    # because the next call will set it again. Do NOT rely on the previous tool call
    # leaving the timeout in any particular state.
    session.raw.timeout = timeout_ms or session.config.timeout_ms
    try:
        response = session.raw.query(command)
        result = ReadResult(success=True, raw=response, format="text")
    except pyvisa.VisaIOError as e:
        result = ReadResult(success=False, error=str(e), hint="...")

    event_logger.log_event(op="visa_query", alias=alias, command=command,
                           success=result.success, response=result.raw, error=result.error)
    return asdict(result)
```

**Per-call timeout invariant:** for drivers like VISA where the underlying library exposes timeout as a Resource attribute (rather than a per-call kwarg), every tool that uses the timeout must reset it from `timeout_ms or session.config.timeout_ms` at the top of the call. Never assume the previous call left it in any particular state. This avoids a bug class where a long-timeout debug query bleeds into subsequent fast queries. Drivers whose underlying library supports per-call timeout kwargs (httpx, paramiko `exec_command`) should use those instead and not mutate session state.

Common helpers (`session lookup`, `event logging`, `error wrapping`) live in `lablink/base.py` so drivers don't reimplement them.

### 6.3 `diagnose(alias)` is stateless
`driver.diagnose(config)` receives a `DriverConfig`, not a `Session`. It performs reachability checks, credential file validation, fresh test connections where needed. It does not inspect any active session. If a session is currently open for the alias, `diagnose()` still opens fresh test connections independently. This is intentional: diagnostics must work whether or not a session exists.

### 6.3.1 Device memory loading is shared, not per-driver

`load_device_memory(alias)` lives in `lablink/config.py` and is the **single** reader of `<config_dir>/<alias>.md`. Both `mcp_server.connect` and `mcp_server.diagnose` (the alias-form) call it after the driver returns its result. Drivers never touch the file.

```python
# lablink/config.py
def load_device_memory(alias: str) -> str | None:
    """Read <config_dir>/<alias>.md. Returns content or None if absent. Never raises."""
    ...
```

**Injection pattern: `dataclasses.replace()`, not post-hoc attribute assignment.** Drivers return `ConnectResult(device_memory=None, ...)`. The shared connect tool must inject `device_memory` by *re-constructing* the result:

```python
# In mcp_server.connect:
driver_result = driver.connect(config)
if not driver_result.success:
    return asdict(driver_result)

# Re-construct with device_memory populated. dataclasses.replace() re-runs
# __post_init__, which is what mirrors device_memory → instrument_memory for the
# deprecated alias. Plain `driver_result.device_memory = ...` leaves the alias
# stale at None and silently breaks the Phase-1 back-compat window.
import dataclasses
final = dataclasses.replace(driver_result, device_memory=load_device_memory(alias))
return asdict(final)
```

The same pattern applies wherever `device_memory` is populated post-construction (`mcp_server.diagnose` alias-form is the other v1 call site).

This avoids per-driver duplication and ensures memory-loading semantics (path resolution, encoding, error swallowing) are uniform across the codebase. If a future feature changes how memory is loaded (e.g., team-shared memory via a second source), only `load_device_memory` needs to change. The driver-side `ConnectResult` and `DiagnosticResult` types carry the `device_memory` field; population is the shared-tool layer's job, not the driver's.

### 6.4 Event Logger Contract

`lablink/event_logger.py` exposes `log_event(**fields)` — appends one JSONL entry per call to `<log_dir>/YYYY-MM-DD.jsonl`. Generalized from agentlink-visa's `scpi_logger.py`. Disabled by setting `LABLINK_LOG_DIR=""`. Never raises — filesystem errors are silently swallowed; logging must never affect tool behavior.

**`LABLINK_LOG_DIR` resolution:** read from `os.environ` on **every call** to `log_event`, not cached at import time. This means a test fixture can `monkeypatch.setenv("LABLINK_LOG_DIR", tmp_path)` and the next `log_event` call honors it; an autouse fixture can set `LABLINK_LOG_DIR=""` to disable logging for the test run. Per-call resolution has negligible cost (one dict lookup) and avoids the test-isolation pain of import-time caching.

**Required fields (auto-populated or caller-required):**
- `ts` (auto-populated, UTC ISO-8601 timestamp)
- `op` (caller-required, the tool name as the agent sees it: `"connect"`, `"visa_query"`, `"ssh_exec"`, `"rest_post"`, `"disconnect"`, etc.)
- `alias` (caller-required, the device alias; pass `None` for `op="diagnose"` system-audit calls)
- `success` (caller-required, `True` or `False`)

**Recommended fields (where applicable):**
- `error` (string) — populated on `success=False`
- `duration_ms` (int) — wall-clock time of the operation

**Driver-specific extras (free-form `**kwargs`):**
- VISA: `command`, `response`
- SSH: `command`, `exit_code`, `stderr_bytes`
- REST: `verb`, `path`, `status_code`
- Serial: `command`, `bytes_read`
- python_shell: `code_lines`, `stdout_bytes`, `stderr_bytes`, `returncode`

The contract is intentionally loose. The four required fields are the only ones every consumer (the agent, debug tooling, the user inspecting logs) can rely on. Everything else is per-tool best-effort. Drivers must not pass `response`/`command`/etc. fields that would not make sense for their protocol (e.g., REST has no `command` — it has `verb` + `path` + `body`).

### 6.5 Streaming Lifecycle Contract (Provisional)

> **Status: provisional.** v1 ships zero streaming drivers, so none of these rules has been validated against real hardware. They are the best-guess starting point for the first streaming driver author. **Phase 1.5 (SSH streaming) ratifies the final contract.** Expect revisions to this section when Phase 1.5 lands and surfaces gaps. Until then, treat the rules below as strong guidance rather than locked contract — a Phase 1.5 author who finds a rule wrong should propose an edit to this section rather than work around it.

`Session.buffer` and `Session.buffer_thread` exist for forward compatibility. Future streaming drivers should follow these five rules:

1. **Bounded queue.** `session.buffer` should be created with `maxsize=1000` by default. Drivers may override but should document the chosen size. Overflow policy is drop-oldest (`buffer.get_nowait()` to drain before `put_nowait()`). An unbounded queue is grounds for review pushback.

2. **Thread setup belongs in `connect()` for always-on streaming drivers** (MQTT subscribes on connect, WebSocket opens on connect). For hybrid drivers (e.g., a future SSH streaming mode), thread setup belongs in a per-driver lifecycle tool (`ssh_start_stream`). The lifecycle tool mutates `session.buffer` and `session.buffer_thread` in place on the existing Session.

3. **Thread teardown belongs in `disconnect()`.** The driver should call `thread.join(timeout=2.0)`. If the join times out, return `Result(success=True, metadata={"warning": "buffer thread did not exit cleanly within 2s"})` rather than blocking the agent indefinitely. Hybrid drivers should also tear down on the per-driver stop tool (`ssh_stop_stream`).

4. **Thread exceptions should not propagate.** The driver wraps the thread body in `try/except`, and on exception sets `session.metadata["stream_error"] = str(exc)` then exits the thread cleanly. The driver's read tool checks for a broken stream BEFORE calling `is_alive()`:

   ```python
   # Correct order — None-safe.
   if session.buffer_thread is None:
       return ReadResult(success=False, error="No stream registered.",
                         hint="Call start_stream first.")
   if not session.buffer_thread.is_alive():
       err = session.metadata.get("stream_error", "unknown")
       return ReadResult(success=False, error=f"Stream thread died: {err}",
                         hint="Call disconnect() and reconnect.")
   ```

   `buffer_thread.is_alive()` is an `AttributeError` if `buffer_thread is None` — non-streaming sessions and hybrid sessions that haven't started streaming both have `None` here. The None-check is non-optional.

5. **Read tool batching is per-driver but should be documented.** Each driver's `*_read_stream` (or equivalent) tool documents whether it returns one item per call, a drained batch, or up to N items. `ReadResult.raw` is typed `str | bytes | list | None` to accommodate batch returns. The driver's docstring is the source of truth — there is no single uniform answer.

**Re-ratification at Phase 1.5 exit gate:** when SSH streaming lands, this section is re-reviewed against what actually worked. Rules that survived become locked (MUST language reinstated); rules that didn't get rewritten or removed. Until then, this section is a forward-compat sketch, not a binding contract.

---

## 6.6 CLI Scope

The CLI mirrors the MCP tool surface and is intended for development, debugging, and scripted use — not production agent operation.

**Shared lifecycle commands** (always present):
| CLI command | MCP equivalent |
|-------------|----------------|
| `lablink connect <alias>` | `connect` |
| `lablink disconnect <alias>` | `disconnect` |
| `lablink list` | `list_devices` |
| `lablink diagnose [alias]` | `diagnose` |

**Per-driver commands** (only present when the driver's deps are installed):
| CLI command | Tool |
|-------------|------|
| `lablink visa query <alias> "<command>"` | `visa_query` |
| `lablink visa write <alias> "<command>"` | `visa_write` |
| `lablink ssh exec <alias> "<command>"` | `ssh_exec` |
| `lablink ssh start-stream <alias> "<command>"` | `ssh_start_stream` |
| `lablink ssh stop-stream <alias>` | `ssh_stop_stream` |
| `lablink ssh read-stream <alias>` | `ssh_read_stream` |
| `lablink rest get <alias> <path>` | `rest_get` |
| `lablink rest post <alias> <path> --body '{...}'` | `rest_post` |
| ... | ... |

The CLI uses Click's command-group nesting (`lablink visa query ...`). Each driver's `register_tools()` has a sibling method `register_cli_commands(cli_group)` that adds its subgroup. Same dynamic-registration logic — drivers with missing deps don't add their subgroup.

---

## 7. Directory Structure (Target)

```
lablink-mcp/
├── docs/
│   ├── lablink_plan.md              # this document
│   └── agent_docs/
│       ├── readme_agent.md
│       ├── project_goal.md          # update to reflect LabLink scope
│       ├── agent_development.md     # update coding standards
│       ├── current_status.md        # update phase
│       └── system_architecture.md   # full rewrite to match this plan
├── lablink/
│   ├── __init__.py
│   ├── base.py                      # ABC, all data models, Session, helpers (session lookup, error wrapping)
│   ├── config.py                    # Base config loader; uses DRIVER_CONFIG_REGISTRY for driver-specific subclasses
│   ├── session.py                   # _sessions dict, register/deregister/get helpers
│   ├── event_logger.py              # Generalized from scpi_logger.py; logs all tool events
│   ├── exceptions.py                # ConfigError, SessionError, DriverError
│   └── interfaces/
│       ├── __init__.py              # DRIVER_REGISTRY, DRIVER_CONFIG_REGISTRY
│       ├── visa/
│       │   ├── __init__.py
│       │   ├── driver.py            # VisaDriver(LabLinkDriver[VisaDriverConfig])
│       │   └── config.py            # VisaDriverConfig
│       ├── ssh/
│       │   ├── __init__.py
│       │   ├── driver.py
│       │   └── config.py
│       ├── rest/
│       │   ├── __init__.py
│       │   ├── driver.py
│       │   └── config.py
│       ├── serial/
│       │   ├── __init__.py
│       │   ├── driver.py
│       │   └── config.py
│       ├── python_shell/
│       │   ├── __init__.py
│       │   ├── driver.py
│       │   └── config.py
│       └── [future drivers]/
├── mcp_server.py                    # FastMCP entrypoint; registers shared tools and dispatches driver.register_tools()
├── cli.py                           # Click root group; dispatches to driver.register_cli_commands()
├── tests/
│   ├── conftest.py
│   ├── test_shared_tools.py         # connect/disconnect/diagnose/list_devices
│   ├── test_dispatch.py             # type→driver dispatch, unknown type, missing deps
│   └── interfaces/
│       ├── test_visa.py
│       ├── test_ssh.py
│       ├── test_rest.py
│       ├── test_serial.py
│       └── test_python_shell.py
├── examples/
│   └── configs/
│       ├── visa_scope.toml
│       ├── ssh_server.toml
│       ├── rest_api.toml
│       ├── serial_device.toml
│       └── python_shell_env.toml
├── pyproject.toml
├── requirements.txt
├── .env.example
└── README.md
```

Note: there is no `lablink/tools.py` in this design. Shared tools live in `mcp_server.py`; per-driver tools live in each `lablink/interfaces/<type>/driver.py`. The dispatch layer is the registries.

---

## 8. Planned v1 Drivers

Priority order for implementation after Phase 0 migration:

| Priority | Driver Key | Transport | Notes |
|----------|-----------|-----------|-------|
| 0 (existing) | `visa` | PyVISA | Migrate from current codebase; refactor to ABC + register_tools |
| 1 | `ssh` | Paramiko | Exec mode primary; interactive shell via `ssh_shell_session` tool |
| 2 | `rest` | httpx | Full HTTP verb support via per-verb tools; auth block standard |
| 3 | `serial` | pyserial | RS232/RS422/RS485 are config variants of same driver |
| 4 | `python_shell` | subprocess | Configurable interpreter; gateway to vendor SDKs |

Beyond v1 (not yet scoped): TCP, WebSocket, MQTT (first streaming driver — will validate §6.5), Modbus, OPC-UA, gRPC, others as user demand surfaces.

---

## 9. Implementation Phases

### Phase 0 — Migration & Scaffold
**Goal:** Rename, restructure, and establish the base. VISA functionality must survive unchanged. No regressions.

**This phase is split into 0a and 0b. This split is mandatory, not advisory.**

#### Phase 0a — Mechanical rename + auto-migration

The original draft of this plan separated the rename from the auto-migration into 0a and 0b respectively. That left a window where the package looked for configs at `~/.lablink/devices/` but nothing had populated that directory yet. **Auto-migration must land in the same phase as the path rename.** It is therefore part of 0a.

**Phase 0a scope is strictly string-level renames.** No command-structure changes, no Click group restructuring, no new dataclasses, no behavioral changes beyond the auto-migration in task 7. If a change requires more than `sed`-style mechanical work, it belongs in 0b or 0c. The CLI command structure in this phase is exactly what agentlink-visa shipped, just with `agentlink` → `lablink` in the names.

**Entry point: clean hard cutover. No stderr shim.** Phase 0a removes `agentlink-mcp` and `agentlink` entry points entirely. The new entry points are `lablink-mcp` (MCP server) and `lablink` (CLI). The MCP client config requires one line to change (`"command": "lablink-mcp"`).

A previous draft proposed keeping `agentlink-mcp` as a tiny stderr-printing shim through Phase 1 to soften the "command not found" UX for users who pull the new release without reading the README. That was a mistake: MCP clients (Claude Desktop, Claude Code) do not surface failed-server stderr to the user. A shim printing to stderr would be invisible — the user would see only "server failed to start" in the MCP client. The shim is real code maintenance for zero practical benefit.

The discoverability gap is real but the fix lives elsewhere:
- The README must prominently document the entry-point rename, both in a "Migration from agentlink-visa" section near the top AND in a `CHANGELOG.md` entry.
- The PyPI release notes for the first `lablink-mcp` version must call out the breaking entry-point change.
- The auto-migration stderr line (printed by `lablink-mcp` on first run after migration) should include a reminder: `[lablink] If you're seeing this, your old MCP client config pointed at agentlink-mcp. Update it to lablink-mcp; see README.md Migration section.`

Auto-migration handles configs; documentation handles the entry-point edit; nothing handles users who skip both. That's an acceptable miss for a v1 release.

Tasks:

0. **Capture pre-rename baseline output.** Before any other change: run `agentlink connect <local_instrument>` for at least one configured VISA instrument and save stdout/stderr to `tests/baselines/agentlink_connect_pre_phase_0a.txt`. This is the only chance to capture the baseline against which Phase 0b's behavioral-equivalence gate will diff. If the old `agentlink` CLI is broken at any point during 0a, this baseline cannot be recreated. Commit the baseline file as the very first commit of Phase 0a work.

1. Rename package directory `agentlink/` → `lablink/`.
2. **Do not split `tools.py` in 0a.** The current `agentlink/tools.py` mixes shared lifecycle (`connect`/`disconnect`) with VISA-specific operations (`query`/`write`/`diagnose_connection`). Moving the file into `lablink/interfaces/visa/` would put shared logic in the wrong place; leaving it at `lablink/tools.py` doesn't match the target layout. Phase 0a tolerates the temporary mismatch: `tools.py` stays at `lablink/tools.py` with its current shape, just renamed. The split (shared dispatch → `mcp_server.py`; VISA-specific operations → `lablink/interfaces/visa/driver.py`; `lablink/tools.py` deleted) happens in Phase 0b as part of the ABC refactor. Do not create `lablink/interfaces/visa/` in 0a — that directory is introduced in 0b alongside the per-driver structure.
3. Update all internal imports — `agentlink.*` → `lablink.*`
4. Update `mcp_server.py` server identifier and the existing Click group name in `cli.py` — string-level only. The Click command structure (top-level `connect`/`query`/`write`/`disconnect`/`diagnose`) does not change in this phase; the architectural rewrite to subgroups lands in Phase 0c.
5. Update all env var names (`AGENTLINK_*` → `LABLINK_*`) and config path references
6. Update `pyproject.toml` — package name `lablink-mcp`, entry points `lablink-mcp` and `lablink`. **Remove** the old `agentlink-mcp` and `agentlink` entry points; do not publish both.
7. **Implement auto-migration** in `lablink/config.py`. Detailed contract:

   **Trigger condition:** old dir `~/.agentlink/instruments/` exists AND new dir `~/.lablink/devices/` contains zero `.toml` files. (An empty-but-existing new dir still triggers migration — the user may have created the directory in advance. A new dir with any `.toml` skips migration entirely, assuming the user has already set things up.) **Note:** a new dir with `.md` files but no `.toml` files DOES still trigger migration — see the per-file safety rule below.

   **Per-file behavior:**
   - **Never overwrite an existing destination file.** For each source file, check whether the destination path already exists. If yes, skip the copy and log one stderr line: `[lablink] Skipped: <filename> already exists in destination.` This protects against clobbering `.md` files a user may have pre-staged in the new dir (the trigger condition only checks `.toml` presence, not `.md`).
   - Copy every `*.toml` and `*.md` file from old dir to new dir, preserving filenames, subject to the no-overwrite rule above.
   - For each copied `.toml`, attempt to parse with `tomllib`. If parsing succeeds and the parsed dict has no top-level `type` key, prepend `type = "visa"\n` to the file content before writing. If parsing fails (malformed source, unusual encoding, BOM), copy the file as-is without injection and log a stderr warning naming the file. The user fixes it manually after.
   - **TOML injection assumes flat keyed configs.** Existing agentlink-visa configs are all flat key-value at root with no sections, no BOM, and no leading whitespace. If a migrated file starts with `[section]` headers or has a UTF-8 BOM, naive `type = "visa"\n` prepending produces invalid TOML. The "parse-first" rule above means such files surface as a stderr warning rather than silently breaking. They are rare enough in v0.x configs that handling them more carefully than "warn and skip injection" is over-engineering; if the user has unusual configs, they can add `type = "visa"` themselves.

   **`MIGRATED.txt` marker:** written to the *old* dir on successful migration. Contents (multi-line):
   ```
   migrated_at: 2026-05-29T14:32:17Z
   dest: /Users/me/.lablink/devices/
   files: bench_scope.toml, bench_scope.md, lab_pi.toml
   ```
   If `MIGRATED.txt` exists in the old dir, the migration is treated as already-done and is **not** re-run, even if new `.toml` files have been added to the old dir since. The user who wants a second migration must delete `MIGRATED.txt` explicitly.

   **`LABLINK_AUTO_MIGRATE` override:** values `"0"`, `"false"`, `"no"` (case-insensitive) disable migration. Any other value, including empty string and unset, enables it. Document this in the README.

   **Output:** on successful migration, print one stderr line: `[lablink] Migrated N config files from ~/.agentlink/instruments/ to ~/.lablink/devices/. See ~/.agentlink/instruments/MIGRATED.txt.` On any per-file warning, print an additional stderr line per file.

8. Update all tests — package imports, config paths, env vars, plus migration tests covering: happy path, malformed source TOML, MIGRATED.txt gating re-runs, `LABLINK_AUTO_MIGRATE=0` opt-out, type-field injection on migrated VISA configs.

**Phase 0a exit criterion:** all existing tests pass unchanged plus the new migration tests. `lablink connect bench_scope` on a machine with an existing `~/.agentlink/instruments/bench_scope.toml` works end-to-end with no manual user steps.

#### Phase 0b — Architectural core (ABC, data models, VISA refactor, dispatch)

The original draft of Phase 0b touched every file in the codebase in one step. That made it a brutal review and a risky merge — a regression in any one task would block the rest. Phase 0b is now split into 0b (architectural core) and 0c (peripheral cleanup). Both have separate exit gates.

Tasks (0b — architectural core only):

0. **FastMCP late-registration smoke test.** Before any other 0b work, write a ~10-line test that verifies the load-bearing assumption: FastMCP accepts `@mcp.tool()`-decorated functions applied *inside an instance method called after server construction* (the `register_tools(mcp)` pattern). Standard FastMCP usage is module-level `mcp = FastMCP()` plus top-level decorated functions; our pattern instantiates a driver, calls `register_tools(mcp)`, and decorates inside that method. This should work — `@mcp.tool()` is just a decorator — but verifying it before committing the entire architecture is cheap insurance. If the smoke test fails, the architecture needs revisiting; treat this as a Phase 0b stop-the-line.

   ```python
   # tests/test_fastmcp_late_registration.py — write this FIRST
   from fastmcp import FastMCP

   class FakeDriver:
       def register_tools(self, mcp: FastMCP) -> None:
           @mcp.tool()
           def fake_query(alias: str, command: str) -> dict:
               return {"alias": alias, "command": command}

   def test_late_registered_tool_is_discoverable():
       mcp = FastMCP("smoketest")
       FakeDriver().register_tools(mcp)
       tools = mcp.list_tools()  # or whatever the FastMCP API surface is
       assert any(t.name == "fake_query" for t in tools)
   ```

1. Create `lablink/base.py` — ABC + all data models (`Result`, `ReadResult`, `ConnectResult`, `DriverConfig`, `AuthConfig`, `DocumentedConfig`, `DiagnosticResult`, `SystemDepStatus`, `Session[ConfigT]`). Include shared helpers: `session_registry.get()`, `wrap_tool_errors()`, etc. `ConnectResult` includes the deprecated `instrument_memory` alias field per §2.7.
2. Create `lablink/interfaces/visa/config.py` — `VisaDriverConfig(DriverConfig, DocumentedConfig)`
3. Update `lablink/config.py` — base config loader using `DRIVER_CONFIG_REGISTRY`; raises `ConfigError` on unknown type; calls `Path(value).expanduser()` on all path fields. (Auto-migration was already landed in Phase 0a; nothing new here.)
4. Refactor VISA driver to subclass `LabLinkDriver[VisaDriverConfig]`:
   - `connect()` returns `ConnectResult` (populates both `device_memory` and `instrument_memory`) and registers `Session` itself
   - `disconnect()` closes resource and deregisters
   - `diagnose(config)` performs the existing diagnostic checks
   - `register_tools(mcp)` registers `visa_query` and `visa_write`
   - `register_cli_commands(group)` registers the `lablink visa ...` subgroup
   - `check_python_deps()` returns `[("pyvisa", ...), ("pyvisa-py", ...)]`
   - `system_dep_check()` returns libusb status with platform-appropriate hint
5. Update `lablink/session.py` — module-level `_sessions` dict; `register/deregister/get` helpers; `get(alias, expected_type=...)` returns None on type mismatch
6. Create `lablink/interfaces/__init__.py` — `DRIVER_REGISTRY` and `DRIVER_CONFIG_REGISTRY` with VISA entry
7. Rewrite `mcp_server.py` to dispatch via the registries:
   - Register shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`)
   - Iterate `DRIVER_REGISTRY`; for each driver whose `check_python_deps()` passes, instantiate and call `register_tools(mcp)`
   - For each skipped driver, log a stderr notice with the install command
   - `_INSTRUCTIONS` rewrite is deferred to 0c — keep the existing VISA-flavored text in place; it will not mislead since VISA is the only registered driver at the end of 0b.

**Phase 0b exit gate (MUST pass before 0c starts):**
- All Phase 0a tests still pass.
- VISA driver implements the full ABC and self-registers its tools.
- `mcp_server.py` contains no protocol-specific logic — all VISA-specific behavior is in `lablink/interfaces/visa/`.
- New required dispatch tests:
  - Unknown `type` in config raises `ConfigError` listing valid types.
  - `connect()` for an alias of a hypothetical driver type whose deps are missing returns a structured error with the install hint. (Test by mocking `check_python_deps` to report missing.)
  - `session_registry.get(alias, expected_type="ssh")` returns `None` when the alias is actually a VISA session.
- `lablink connect bench_scope` produces output equivalent to what `agentlink connect bench_scope` produced before Phase 0a. Equivalence is verified by saving the pre-Phase-0a `agentlink connect` output for at least one local instrument config before starting Phase 0a, then diffing against post-Phase-0b output. (The old `agentlink` entry point itself was removed in Phase 0a; this gate is about *behavioral* equivalence, not dual entry points.)

#### Phase 0c — Peripheral cleanup (logging, CLI, docs, archives)

**Phase 0c scope is architectural rewrites and content rewrites that touch every layer but don't change the data flow established in 0b.** This is where `cli.py` becomes a Click root + subgroups (vs. the flat command list Phase 0a preserved), `_INSTRUCTIONS` becomes multi-driver, `scpi_logger` becomes `event_logger`, and stale docs/archives move out of the way.

Tasks:
1. **Rewrite `cli.py`** — Click root group + shared subcommands + driver subgroups via each driver's `register_cli_commands(group)`. CLI for VISA: `lablink visa query`, `lablink visa write`. Drop the old top-level `lablink query` / `lablink write` commands that Phase 0a left in place. (This is the architectural CLI work that Phase 0a explicitly deferred.)

   **Acknowledged breaking change:** any user scripts calling `agentlink query <alias> <cmd>` (or the temporarily-renamed `lablink query <alias> <cmd>` from Phase 0a) break in Phase 0c. The new equivalent is `lablink visa query <alias> <cmd>`. Since the CLI is documented as debug-only (not for production agent operation, per `agent_development.md` §1), and v1 is the first public release of LabLink, this breakage is acceptable. It must still be called out explicitly in CHANGELOG.md and the README migration section so the change is discoverable. Do not add a Click-level deprecation shim for the old flat commands — same reasoning as the entry-point hard cutover (§9 Phase 0a).
2. Rename `agentlink/scpi_logger.py` → `lablink/event_logger.py`; formalize the canonical-field contract (§6.4); update every tool call site to use the new signature.
3. Rewrite `_INSTRUCTIONS` in `mcp_server.py`: multi-driver architecture overview, how to discover what tools are available (call `diagnose()`), per-driver pointer to that driver's tool docstrings, deprecated-alias note for `instrument_memory`. **Must include a runtime-aware paragraph**: at startup, compute the number of registered drivers and inject it into `_INSTRUCTIONS` (template substitution), e.g., `"This server is multi-driver capable; {N} driver(s) are currently loaded — call diagnose() for the active set."` Without this, an agent reading the post-0c `_INSTRUCTIONS` against a VISA-only install sees a multi-driver overview that doesn't match its actual tool surface and burns context window cycles trying to reconcile.
4. Archive `agent-bootstrap.md` → `docs/archive/agent-bootstrap.md` (references old agentlink-visa architecture; would mislead future agents).
5. Archive old agentlink-visa history entries from `docs/agent_docs/current_status.md` into `docs/archive/current_status_agentlink_visa.md`. (The agent_docs themselves were rewritten 2026-05-28 in the planning phase; only the history pruning is new here.)
6. Expanded dispatch tests (beyond the 0b gate):
   - `diagnose()` without alias enumerates all drivers and reports dep status.
   - `register_tools()` is called only for drivers whose deps are present.
   - `register_cli_commands()` is called only for drivers whose deps are present.
   - Event logger receives the required four fields on every tool call.
7. Update `examples/configs/visa_scope.toml` to include the `type = "visa"` field as the documented canonical form (migrated configs auto-inject; new configs declare it explicitly).

**Phase 0c exit gate:**
- All 0b tests still pass.
- New CLI subgroup tests pass.
- Event logger contract verified by tests.
- `_INSTRUCTIONS` reviewed against the multi-driver tool surface.

---

### Phase 1 — SSH Driver (exec-only)
**Goal:** First new driver. Validates the dispatch architecture end-to-end for a protocol with genuinely different semantics from VISA.

Phase 1 is intentionally exec-only — it does NOT include streaming. The streaming contract in §6.5 has never been exercised against real hardware, and SSH would be both the first new driver AND the first streaming driver if everything shipped together. Decoupling them lets us learn from real SSH usage before locking the streaming machinery against it. Streaming lands as Phase 1.5.

Tools registered by `SshDriver.register_tools(mcp)` in Phase 1:
- `ssh_exec(alias, command, timeout_ms?)` — run a command, wait for exit, return stdout. Metadata includes `{exit_code, stderr}`. Use Paramiko's per-call `exec_command(timeout=...)` so session state is not mutated.
- `ssh_shell_session(alias, commands, timeout_ms?)` — run a scripted sequence on an interactive PTY; return full transcript. Per-call PTY; no persistent shell state across tool calls in Phase 1.

Lifecycle:
- `connect` — establish SSH connection (Paramiko); return server banner as `identity`.
- `disconnect` — close SSH connection.
- `diagnose` — TCP reachability on port 22, auth method check, key file presence.

Dependencies: `paramiko`

### Phase 1.5 — SSH Streaming
**Goal:** First streaming driver. Validates the §6.5 contract.

Gated on real-world Phase 1 feedback. Do not start until SSH-exec has seen real usage (or until the developer explicitly requests it). The streaming contract may need revision based on what Phase 1 reveals.

Tools added in Phase 1.5:
- `ssh_start_stream(alias, command)` — run a long-lived command; background thread buffers stdout into `session.buffer` per §6.5.
- `ssh_stop_stream(alias)` — terminate the background process, flush buffer, return final transcript.
- `ssh_read_stream(alias, timeout_ms?)` — drain the buffer. Batching semantics documented in the tool docstring.

Lifecycle changes:
- `disconnect` updated to terminate any active stream before closing the SSH connection.
- `SshDriver.capabilities` (or equivalent runtime check) reflects streaming availability.

Phase 1.5 exit gate:
- Real-hardware test: start a streaming command (`tail -f`, `journalctl -f`), confirm buffer accumulates, confirm `stop_stream` cleans up.
- §6.5 contract is re-reviewed against what Phase 1.5 actually shipped; gaps in the contract get fixed before any second streaming driver lands.

### Phase 2 — REST Driver
**Goal:** Establishes the auth block pattern and the persistent-client pattern (`httpx.Client` kept open across calls for connection pooling and auth header reuse).

Tools registered by `RestDriver.register_tools(mcp)`:
- `rest_get(alias, path, query_params?)` — GET. `ReadResult.metadata` includes `{status_code, headers}`.
- `rest_post(alias, path, body?)` — POST with optional JSON body or form data.
- `rest_put(alias, path, body?)`
- `rest_patch(alias, path, body?)`
- `rest_delete(alias, path)`

Lifecycle:
- `connect` — validate base URL reachability; optionally hit a health endpoint for `identity`. The "session" is the persistent `httpx.Client` (kept open for connection pooling and auth header reuse).
- `disconnect` — close the `httpx.Client`; deregister.
- `diagnose` — URL reachability, HTTP status on base URL, auth header presence.

Dependencies: `httpx`

### Phase 3 — Serial Driver
**Goal:** RS232/RS422/RS485 coverage with a single driver.

Tools:
- `serial_query(alias, command, timeout_ms?)` — write + read with configured termination.
- `serial_write(alias, command)` — write bytes, no read.
- `serial_read(alias, timeout_ms?)` — drain accumulated bytes (this is request/response-style draining, not §6.5 streaming — buffer is the OS-level serial buffer, not a background thread).
- `serial_flush(alias)` — clear input/output buffers.

Lifecycle: standard.

Dependencies: `pyserial`

### Phase 4 — python_shell Driver
**Goal:** Gateway to vendor SDKs that have no VISA or network interface.

This is the most leakily-abstracted v1 driver — "Python REPL as a session" requires a real wire protocol between LabLink and the user's interpreter subprocess. The protocol is specified below rather than left as an implementation detail because the hard questions (output vs. return value, traceback capture, timeout vs. "still running") need to be answered before this driver lands.

#### Tools
- `python_shell_exec(alias, code, timeout_ms?)` — run a code block (statements); return captured stdout/stderr and any exception. `code` is a string that may span multiple lines.
- `python_shell_eval(alias, expression, timeout_ms?)` — evaluate a single expression; return its repr and stdout/stderr.

#### Lifecycle
- `connect` — spawn a persistent subprocess running the configured interpreter with the bootstrap script (see below) as `python -u <bootstrap>`. Wait for `READY` handshake. Return Python version + interpreter path as `identity`.
- `disconnect` — send `{"op": "shutdown"}`; wait up to 2 seconds for clean exit; SIGTERM then SIGKILL on timeout.
- `diagnose` — interpreter path exists and is executable; spawn-and-handshake test; report Python version.

State persists across calls within a session — opening an instrument once in one `python_shell_exec` call and querying it from a subsequent call is the whole point.

Dependencies: none beyond stdlib for LabLink (the *user's* interpreter has whatever it has).

#### Wire Protocol (JSONL over stdin/stdout)

The bootstrap script is shipped with LabLink: `lablink/interfaces/python_shell/bootstrap.py`. It runs in the user's interpreter subprocess.

**Request frame (LabLink → subprocess, one JSON object per line on stdin):**
```json
{"id": "req-7", "op": "exec",     "code": "import nidaqmx\nimport_ok = True"}
{"id": "req-8", "op": "eval",     "expression": "import_ok"}
{"id": "req-9", "op": "shutdown"}
```

**Response frame (subprocess → LabLink, one JSON object per line on stdout):**
```json
{"id": "req-7", "op": "exec",
 "stdout": "", "stderr": "", "result": null, "exception": null, "duration_ms": 12}
{"id": "req-8", "op": "eval",
 "stdout": "", "stderr": "", "result": "True", "exception": null, "duration_ms": 1}
{"id": "req-9", "op": "shutdown",
 "stdout": "", "stderr": "", "result": null, "exception": null, "duration_ms": 0}
```

- `stdout`/`stderr` are strings captured for the duration of the call via `contextlib.redirect_stdout` / `redirect_stderr`.
- `result` is `repr(value)` for `eval`, or `null` for `exec`. (Repr, not the value itself, to keep the wire format strictly JSON-safe — vendor SDK objects are rarely JSON-serializable.)
- `exception`, when non-null, is an object: `{"type": "RuntimeError", "message": "...", "traceback": "Traceback (most recent call last):\n..."}`. Traceback is the full `traceback.format_exc()` string.

The single-line-per-frame constraint means stdout produced during the call cannot leak into the response stream. The bootstrap accumulates captured output in memory and writes it only as part of the response frame. Programs that print huge volumes will buffer in memory until the call returns — this is intentional; the alternative (streaming output) is a §6.5 streaming concern and is out of scope for v1.

**Handshake:** on subprocess start, the bootstrap writes one line: `{"op": "ready", "python_version": "3.11.5", "interpreter": "/Users/.../bin/python"}`. The driver waits for this line before considering `connect` successful.

#### Timeout, crashes, and "still running"

The driver distinguishes three failure modes — agent gave up, subprocess crashed, request raced an in-flight call. Each has different recovery semantics.

1. Track a `busy: bool` flag per session. Set `True` on request send, `False` on response receive (regardless of whether the response was success/exception/EOF).
2. **Agent timeout (subprocess still running):** no response within `effective_timeout` AND the subprocess is alive. Return `ReadResult(success=True, timed_out=True, raw=None, hint="The previous call is still running. Subsequent calls will fail with 'busy' until it completes or the session is disconnected.")`. **Do not** kill the subprocess. `busy` stays `True`.
3. **Subprocess crash mid-call:** `BrokenPipeError` on write, or empty-string read (`""` from stdout) — both indicate the subprocess exited. Clear `busy=False`, terminate any residual process state, and return `ReadResult(success=False, error="python_shell subprocess exited unexpectedly.", hint="Call disconnect() and connect() to restart the interpreter. Any session state has been lost.")`. The driver does not auto-restart; the agent decides.
4. **Race: request arrives while `busy=True`:** return `ReadResult(success=False, error="Session is busy executing a previous call.", hint="Wait or call disconnect() to force termination.")` without sending to the subprocess.
5. **`disconnect()`** always succeeds in terminating the subprocess (`{"op": "shutdown"}` → wait 2s → SIGTERM → wait 1s → SIGKILL). Always clears `busy`.

This means a runaway `while True: pass` is recoverable via `disconnect`/`reconnect`. A crashed subprocess is recoverable the same way. The subprocess is fully owned by LabLink.

#### Response framing and size limits

The wire protocol is newline-delimited JSON. There is a soft per-frame size limit: **8 MB** of stdout+stderr captured per call. Frames larger than this are truncated; the response includes `"truncated": true` and `"truncated_bytes": <N>` in the response object, and the captured stdout/stderr stops at the limit. Larger output is not a hard error — the agent sees the truncation flag and can ask for narrower output on the next call.

Rationale for 8 MB: typical instrument scripts produce KB-scale output; the limit is generous enough to never bite normal use, small enough to avoid pathological memory blowups (a user printing a multi-GB array). Streaming-style continuous output is a §6.5 streaming concern and is explicitly out of scope for `python_shell` — if a user needs streaming subprocess output, that's a future streaming-capable variant, not v1 `python_shell`.

#### Forward note: `busy` under async dispatch

`busy` is a plain bool, not a lock. Single-threaded FastMCP stdio dispatch makes this safe in v1 — tool calls are serialized, so there is no race between "check busy" and "set busy=True." If post-v1 async dispatch lands (per §1.5), `busy` becomes a race condition: two parallel `python_shell_exec` calls against the same session could both see `busy=False`, both proceed to write to stdin, and corrupt the wire protocol.

Mitigation if/when async dispatch lands: replace `busy: bool` with `busy_lock: asyncio.Lock` (or threading.Lock under threaded dispatch). The flag-vs-lock distinction is small and isolated to this driver; documenting it now prevents the contract from silently breaking under a future dispatch change.

#### Security note

This driver executes arbitrary Python with the privileges of the LabLink server process, inside the user's interpreter. Anyone with access to send tool calls to the MCP server can run arbitrary code. This is documented as a known characteristic, not a flaw: the user invoking LabLink has consented to giving their agent code execution. The README must surface this. Server deployments that don't want code execution should not install the `[python_shell]` extra — its absence prevents the driver from registering at all.

#### Phase 4 exit gate
- The four protocol cases pass: clean exec, clean eval, exception with traceback, timeout-still-running with recovery via disconnect.
- A vendor-SDK-style scenario works end-to-end with mocks: "import vendor lib in one call, instantiate handle, query in subsequent calls, results persist across the session."

---

## 10. Naming & Branding

| Item | Old | New |
|------|-----|-----|
| Repo name | `agentlink-visa` | `lablink-mcp` |
| Python package | `agentlink` | `lablink` |
| PyPI name | `agentlink-visa` | `lablink-mcp` |
| MCP entry point | `agentlink-mcp` | `lablink-mcp` |
| CLI command | `agentlink` | `lablink` |
| Config directory | `~/.agentlink/instruments/` | `~/.lablink/devices/` |
| Log directory | `~/.agentlink/logs/` | `~/.lablink/logs/` |
| Env var prefix | `AGENTLINK_` | `LABLINK_` |
| Device memory files | `<alias>.md` in config dir | `<alias>.md` in config dir (unchanged) |
| Memory field in ConnectResult | `instrument_memory` | `device_memory` |

---

## 11. Contributor Guidelines (Public Repo)

Adding a new driver requires exactly these things:

1. **Create `lablink/interfaces/<type>/`** with `driver.py`, `config.py`, and `__init__.py`.
2. **Subclass `LabLinkDriver[YourConfig]`** and implement the four required methods: `connect`, `disconnect`, `diagnose`, `register_tools`. Also override `check_python_deps()` and (if applicable) `system_dep_check()`.
3. **Subclass `DriverConfig`** for the driver's config type. Inherit `AuthConfig` if the driver needs auth; inherit `DocumentedConfig` only if the driver targets devices with manuals on techmanual.ai (mostly T&M instruments).
4. **Register it** — add one line each to `DRIVER_REGISTRY` and `DRIVER_CONFIG_REGISTRY` in `lablink/interfaces/__init__.py`.
5. **Write clear tool docstrings** — each `@mcp.tool()`-decorated function inside `register_tools()` must have a docstring that explicitly defines its parameters in this protocol's terms. The MCP description is the agent's only source of truth for what each tool does.
6. **Add tests** — `tests/interfaces/test_<type>.py` with full mock coverage. Real hardware tests are `@pytest.mark.skip(reason="requires hardware")`.
7. **Add an example config** — `examples/configs/<type>_device.toml`.

No changes to `mcp_server.py` or `cli.py` are required for a new driver.

**Lazy import pattern** — all driver-specific Python deps must be imported inside `connect()` (and inside individual `@mcp.tool()` functions that need them), not at module level:

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
    # ... proceed with connection
```

This ensures the server starts with zero optional extras installed and only errors on actual use. It also lets `check_python_deps()` use `importlib.util.find_spec("paramiko")` without ever executing the import.

---

## 12. Dependency Architecture

### The Four Layers

**Layer 1 — Python runtime**
`uv` is the single user-facing prerequisite. It is a standalone binary that installs without Python and manages Python versions itself via `uv python install`. Users who don't have Python don't need to install it separately. The onboarding story is: install `uv`, then install LabLink.

**Layer 2 — Python package dependencies**
Managed via optional extras groups in `pyproject.toml`. The core package installs only what is needed to run the MCP server and load configs — FastMCP, Click, `tomli`/`tomllib`, and nothing else. All driver deps are opt-in:

| Extra | Installs | Covers |
|-------|----------|--------|
| `lablink-mcp[visa]` | pyvisa, pyvisa-py | VISA driver |
| `lablink-mcp[ssh]` | paramiko | SSH driver |
| `lablink-mcp[rest]` | httpx | REST driver |
| `lablink-mcp[serial]` | pyserial | Serial / RS232 / RS422 / RS485 driver |
| `lablink-mcp[python_shell]` | (no extras — uses stdlib) | python_shell driver |
| `lablink-mcp[common]` | all of the above | "typical lab" bundle |
| `lablink-mcp[all]` | everything | Full install |

All driver imports are **lazy** — the import happens only when the driver is first invoked, not at server startup. If the package is not installed:
- The driver's `check_python_deps()` reports missing — `register_tools()` is skipped, so the driver's tools never appear in the agent's surface.
- `connect()` for an alias of that type returns a structured error with the install command.

The server runs normally with zero optional extras installed. Only the drivers whose deps are present are functional.

**Layer 3 — System-level dependencies**
Some drivers require OS-level packages or closed-source drivers that pip cannot install:

| Driver | System Dep | Notes |
|--------|-----------|-------|
| `visa` | `libusb` | `brew install libusb` / `apt install libusb-1.0-0`; needed for USB instrument access via pyvisa-py |
| `visa` | NI-VISA (optional) | Closed-source NI driver; only needed if user wants the NI backend instead of pyvisa-py |

There is no programmatic way to install these. The solution is surfacing: **`diagnose()` with no alias performs a full system audit.** It checks which driver extras are installed, which system-level deps are present for each installed driver, and returns a prioritized list of missing items with platform-appropriate install commands. This makes `diagnose` the agent's oracle for "why doesn't this work."

**Layer 4 — User's existing Python environment**
Users often have conda environments, project venvs, or system Python installs containing vendor SDKs (`nidaqmx`, `picosdk`, proprietary instrument libraries) that have no VISA or network interface and cannot be pip-installed into LabLink's environment.

The `python_shell` driver is the designed solution. It accepts a `python_path` config field pointing to any interpreter, and spawns a long-lived subprocess REPL in that environment. This is in v1 scope (Phase 4) precisely because it unlocks the long tail of vendor SDKs.

### `diagnose()` Full System Audit Behavior

`diagnose()` called with no alias:
1. Checks `uv` and Python version
2. Iterates every entry in `DRIVER_REGISTRY`
3. For each driver, calls `check_python_deps()` (uses `importlib.util.find_spec` — no side effects) and records pass/fail
4. For each driver whose Python deps are present, calls `system_dep_check()` and records the result
5. Returns a structured result: `{ready: bool, drivers: {<type>: {python_deps: ..., system_deps: ..., status: "ready" | "missing_python" | "missing_system"}}, action_items: [...]}`

Status values are exhaustive — every driver resolves to exactly one. There is no `"unknown"` — if a driver's `check_python_deps` or `system_dep_check` cannot determine status, that is a bug in the driver and should surface as a `DriverError` with an action item naming the driver, not a silent third state.

The `action_items` list is ordered by impact — the most blocking missing dep first. The agent reads this list and surfaces it directly to the user.

### Hot-installing extras

The tool surface is fixed at MCP server startup. Installing a new extra (`pip install lablink-mcp[ssh]`) does not retroactively make its tools available in a running server. Restart the MCP server to pick up newly-installed drivers. Document this in the README.

### Docker

Docker is **not** a primary install target. USB and serial port passthrough into containers adds friction that defeats the purpose of easy local lab setup. Docker is not documented in the main README; it belongs in a `docs/deployment/docker.md` contributed by the community if needed.

---

## 13. Open Questions & Acknowledged Risks

This section captures things that are known to be unresolved or that may need revisiting. The plan does not lock them.

### Kill criteria — when to stop building drivers

The pivot from "VISA only" to "5 drivers in v1" was driven by a single demo's lesson (DUT control is the product). That insight justified the architecture work — multi-driver, dispatch, dependency machinery — because the *cost of being wrong about the architecture* later is much higher than the cost of doing it now. It does not automatically justify building all five drivers.

After each phase, re-evaluate:

- **After Phase 0c lands:** does the architecture still feel right? If the dispatch model is awkward in practice, fix it before adding a second driver. If it works, proceed.
- **After Phase 1 (SSH-exec) ships and gets real use:** has any user or agent actually exercised SSH-from-LabLink? If yes, what did it reveal? If no, why are we building REST next instead of going back to VISA polish?
- **Before Phase 2 (REST):** is there a concrete use case beyond "REST is popular"? If not, defer.
- **Before Phase 3 (Serial):** same question.
- **Before Phase 4 (python_shell):** this is the highest-value driver in the v1 list (unlocks the long tail of vendor SDKs) and the highest-risk (wire-protocol design, subprocess lifecycle). It's also the one that should land last — Phases 1–3 inform the contract.

Shipping LabLink with VISA + SSH + one well-validated additional driver is a perfectly good v1 if the other two don't have a real user behind them. Speculative driver count is a worse outcome than focused driver depth.

### Other open questions

- **`_INSTRUCTIONS` scaling.** With 5 drivers in v1 and per-driver tool docstrings carrying most of the per-protocol semantics, `_INSTRUCTIONS` should stay manageable. If it grows past ~3K tokens, consider per-driver instruction blocks that load only when their driver's deps are present (mirroring the tool-registration pattern).
- **`format = "base64"`.** Not used in v1 since no driver returns binary data. Will be added back when the first driver needs it (likely waveform capture in a future VISA tool, or a binary REST payload).
- **`python_shell` security model.** The driver executes arbitrary Python in a user-configured interpreter. This is by design — the user is the agent's operator and the agent is acting on their behalf — but the README must surface that running a LabLink server exposing `python_shell_exec` against an untrusted agent is equivalent to giving that agent a shell. The mitigation for users who want to disable this is documented: don't install `lablink-mcp[python_shell]`. Without the extra, the driver doesn't register.
- **Auto-migration UX.** Phase 0a auto-migrates the config directory on first run. `LABLINK_AUTO_MIGRATE=0` opts out. If users hit unexpected behavior, consider promoting migration to an explicit `lablink migrate` command and refusing to start otherwise.
- **SSH streaming contract validation.** Phase 1.5 is the first real test of §6.5. The contract may need revision. Hold a §6.5 re-review at the Phase 1.5 exit gate.
- **Hybrid drivers and `session.buffer` initialization.** For SSH (Phase 1.5), the per-tool `ssh_start_stream` mutates `session.buffer` in place. Tests should cover the start → read → stop → read (returns error) → start again sequence explicitly.

### Known generalization debt

Items where the v1 data model has a specific shape that won't generalize cleanly to plausible future features. None of these block v1; all are documented so a future agent doesn't have to re-discover the constraint.

- **`techmanual_document_ids` is brand-coupled.** The field name embeds techmanual.ai as a vendor. A future second documentation source (or a generalized doc-ref scheme) would awkwardly fit under this name. The clean name would be `documentation_refs` or `manual_document_ids`. Not renamed now because (a) techmanual.ai is the only doc source in scope and (b) the rename cost (every config, every test, every migration) exceeds the current benefit. Flagged for a possible rename if a second source ever lands.
- **`device_memory` is filesystem-bound.** Memory lives at `~/.lablink/devices/<alias>.md` — one file per device, owned by the local user. A future "team-shared device memory" feature or a per-agent memory variant cannot use this shape without redesign. `load_device_memory` is the choke point (§6.3.1), so the redesign would be localized — but the on-disk format and ownership model are v1-specific.
- **`AuthConfig` is one credential per device.** Multi-tenant REST APIs, GPIB instruments with multiple personalities, or any device requiring per-endpoint credentials cannot be expressed in v1. Each alias is one connection with one set of credentials. Users with multi-credential targets work around this by defining multiple aliases pointing at the same host/base_url with different credentials — workable but ugly. Worth revisiting if it becomes a frequent complaint.
- **`_sessions` is a module-level dict.** Fine for local-first (one process, one user). Breaks for any "lablink-as-daemon, multiple agents" use case — but that case is an explicit non-goal in `project_goal.md`. Consistent with the project's scope, not a hidden constraint.
- **Single-process model.** LabLink runs as one MCP server process per user. No multi-process coordination, no inter-server session sharing. Consistent with non-goals.
