# Project Status

## Current Phase
**LabLink Phase 1.5 Complete — SSH streaming shipped (VISA + SSH + REST + Serial + python_shell + external)**

All v1 drivers shipped. Phase 1.5 (SSH streaming) completed 2026-05-29,
ratifying the §6.5 streaming contract.

Phase 0c finished the peripheral cleanup on top of the 0b core; all of Phase 0
(migration, architectural core, cleanup) is now done. The codebase is a
multi-driver dispatch system with VISA, SSH, REST, Serial, python_shell, and
external as the current drivers:
- `lablink/base.py` — data models, config dataclasses (`DriverConfig` +
  `AuthConfig`/`DocumentedConfig` mixins, all `kw_only=True`),
  `Session[ConfigT]`, the `LabLinkDriver[ConfigT]` ABC.
- VISA in `lablink/interfaces/visa/` on the ABC; self-registers `visa_query` /
  `visa_write` (tools) and the `lablink visa ...` CLI subgroup.
- SSH in `lablink/interfaces/ssh/` on the ABC; self-registers `ssh_exec` /
  `ssh_shell_session` (tools) and the `lablink ssh exec ...` CLI subgroup.
- REST in `lablink/interfaces/rest/` on the ABC; self-registers `rest_get` /
  `rest_post` / `rest_put` / `rest_patch` / `rest_delete` (tools) and the
  `lablink rest get/post ...` CLI subgroup.
  `SshDriverConfig(DriverConfig, AuthConfig)`; supports `none`, `ssh_key`,
  `ssh_password`, `basic` auth types.
- Serial in `lablink/interfaces/serial/` on the ABC; self-registers
  `serial_query` / `serial_write` / `serial_read` / `serial_flush` (tools) and
  the `lablink serial query/write ...` CLI subgroup. `SerialDriverConfig(DriverConfig)`;
  no auth mixin (serial is inherently local/physical). Covers RS232/RS422/RS485
  via pyserial; parity (`none/even/odd/mark/space`) and termination are config fields.
- python_shell in `lablink/interfaces/python_shell/` on the ABC; self-registers
  `python_shell_exec` / `python_shell_eval` (tools) and the
  `lablink python-shell exec/eval ...` CLI subgroup. `PythonShellDriverConfig(DriverConfig)`;
  no auth mixin (no network layer). Spawns a persistent subprocess running the
  user's interpreter (`python_path`) with `bootstrap.py` as the JSONL wire-protocol
  REPL. State (namespace) persists across calls within a session. No Python extras
  required (stdlib only); `check_python_deps()` returns `[]`. Handles: busy-flag
  serialisation, timeout-with-recovery, subprocess-crash detection, 8 MB output
  truncation, shutdown sequence (shutdown request → 2s wait → SIGTERM → SIGKILL).
- Shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`)
  in `mcp_server.py` dispatch via `DRIVER_REGISTRY` / `DRIVER_CONFIG_REGISTRY`.
- `event_logger` (renamed from `scpi_logger`) with the §6.4 canonical-field
  contract; multi-driver `_INSTRUCTIONS` with a runtime loaded-driver count.

**277/277 tests pass.** Tool surface: 4 shared + 2 VISA + 5 SSH + 5 REST + 4 Serial + 2 python_shell.
All phase exit gates are MET; the VISA path was validated end-to-end on the real
Siglent SDS1104X-E. SSH and REST validated by unit tests. Serial validated by unit
tests. python_shell validated by 50 tests including 7 real-subprocess integration
tests that exercise the bootstrap wire protocol (exec, eval, exception, namespace
persistence, stdout capture, shutdown) against the current Python interpreter.

**Authoritative architectural spec:** `docs/lablink_plan.md`. The per-task
implementation log is `docs/agent_docs/implementation_log.md`.

**All v1 drivers are shipped. Phase 1.5 complete.** Post-v1 scope: async
dispatch, additional streaming drivers (MQTT/WebSocket per §6.5), PyPI publish.

---

## What Exists On Disk Right Now

- `lablink/base.py` — data models, config dataclasses, `Session[ConfigT]`, the
  `LabLinkDriver` ABC
- `lablink/session.py` — protocol-agnostic session registry (three-state lookup)
- `lablink/config.py` — generic loader via `DRIVER_CONFIG_REGISTRY`; auto-migration
- `lablink/interfaces/__init__.py` — `DRIVER_REGISTRY` + `DRIVER_CONFIG_REGISTRY`
- `lablink/interfaces/visa/` — `VisaDriver` + `VisaDriverConfig`
- `lablink/interfaces/ssh/` — `SshDriver` + `SshDriverConfig`
- `lablink/interfaces/rest/` — `RestDriver` + `RestDriverConfig(DriverConfig, AuthConfig)`
- `lablink/interfaces/serial/` — `SerialDriver` + `SerialDriverConfig(DriverConfig)`
- `lablink/interfaces/python_shell/` — `PythonShellDriver` + `PythonShellDriverConfig(DriverConfig)` + `bootstrap.py`
- `lablink/event_logger.py` — JSONL event log; §6.4 canonical-field contract
- `mcp_server.py` — shared lifecycle tools + per-driver registration; `lablink-mcp`
- `cli.py` — shared subcommands + per-driver subgroups (`lablink visa ...`); `lablink`
- `~/.lablink/devices/<alias>.toml` config location; legacy
  `~/.agentlink/instruments/` auto-migrated on first run
- `examples/configs/visa_scope.toml` (carries `type = "visa"`)
- `examples/configs/ssh_pi.toml` (carries `type = "ssh"`)
- `examples/configs/rest_daq.toml` (carries `type = "rest"`)
- `examples/configs/serial_device.toml` (carries `type = "serial"`)
- `tests/` — `test_config`, `test_logger`, `test_shared_tools`, `test_dispatch`,
  `test_fastmcp_late_registration`, `interfaces/test_visa`, `interfaces/test_ssh`,
  `interfaces/test_rest` (162 tests)
- `CHANGELOG.md`, `docs/lablink_plan.md` (authoritative), `docs/agent_docs/`
- `docs/archive/` — `agent-bootstrap.md`, `current_status_agentlink_visa.md`

**Deleted in 0b:** `lablink/tools.py`, `lablink/diagnostics.py`,
`tests/test_tools.py`. **Renamed in 0c:** `scpi_logger.py` → `event_logger.py`.

For the mapping of current code → target code, see `system_architecture.md` §5.

---

## Technical Debt & Known Issues

- **`server.json` `repository.url` may still reference `agentlink-visa`.** The
  GitHub repo itself has been renamed (the git remote is now
  `github.com:techmanual-ai/lablink-mcp.git`), but `server.json`'s
  `repository.url` field has not been audited since — verify/fix before any
  registry publish. (The README and package identifier already use `lablink-mcp`.)
- **VISA required-field validation relaxed in 0b.** Only `type`/`alias`/
  `timeout_ms` are strictly required; VISA-specific fields default and an empty
  `resource_string` is caught in `connect()`. Plan design, not an oversight.
- **No `wrap_tool_errors()` helper in base.py.** The plan mentions one but does
  not specify it; deferred until a second driver creates real duplication
  (scope discipline). The two VISA tools inline their error handling.
- **CLI commands open and close a session per invocation.** Intentional for
  debug UX; sessions persist only across MCP calls. Not a concern for the
  rearchitecture.
- **`duration_ms` not yet emitted.** §6.4 lists it as a recommended log field
  but no tool measures wall-clock time yet. Optional; add when useful.

---

## Recent History

- **2026-05-29** — **[Phase 1.5 Complete]** SSH streaming shipped. Added three
  tools to `SshDriver`: `ssh_start_stream(alias, command)` (runs a long-lived
  exec channel; background thread buffers stdout into a bounded `Queue(maxsize=1000)`
  with drop-oldest overflow; returns `Result(success=True)` immediately),
  `ssh_read_stream(alias, timeout_ms?)` (non-blocking drain; returns concatenated
  chunks in `raw`; `timed_out=True` when buffer empty and stream alive;
  `metadata={"stream_ended": bool}`), `ssh_stop_stream(alias)` (closes channel,
  `join(timeout=2.0)`, drains remainder into `raw`; `metadata={"warning": ...}`
  if thread did not exit cleanly). `disconnect()` updated to tear down any active
  stream (close channel → join 2s → clear `buffer_thread`/`buffer`) before
  closing the SSH connection. `_stream_worker` is a module-level daemon thread
  function; sets `session.metadata["stream_error"]` on exception; always puts
  `None` sentinel. §6.5 contract validated — rules held up without change.
  27 new tests (start/read/stop/disconnect/worker integration); 277/277 pass.
  Hardware smoke test: ✓ validated on `rpi_dev` (RPi 4, Debian 13). Tailed the
  live rtl_433 capture log (`tail -f ~/rtl433_captures/sniff_YYYY-MM-DD.json`);
  received 3 RF decode events on first read, `timed_out=True` on second read
  (empty buffer, stream alive), clean stop with no warning. All Phase 1.5 exit
  gate criteria met.

- **2026-05-29** — **[Phase 4 Complete]** python_shell driver shipped. Added
  `lablink/interfaces/python_shell/` with `PythonShellDriverConfig(DriverConfig)`,
  `PythonShellDriver` implementing the full `LabLinkDriver[PythonShellDriverConfig]`
  ABC, and `bootstrap.py` (the JSONL wire-protocol REPL that runs inside the
  user's interpreter subprocess). Tools: `python_shell_exec` (run a code block;
  captures stdout/stderr; returns traceback on exception with `success=True`),
  `python_shell_eval` (evaluate a single expression; returns `repr(value)`).
  Subprocess lifecycle: connect spawns `python -u bootstrap.py`, waits for READY
  handshake (10s timeout), stores `busy` + `req_counter` in session metadata;
  disconnect sends `{"op":"shutdown"}` → wait 2s → SIGTERM → SIGKILL. Timeout
  returns `timed_out=True`, `busy` stays `True` (subprocess still running);
  BrokenPipeError/EOF returns `success=False` and clears `busy`. Wire protocol:
  newline-delimited JSON, 8 MB combined stdout+stderr soft limit with truncation
  flag. No Python extras required (stdlib only); `check_python_deps()` = `[]`;
  tools always registered. CLI: `lablink python-shell exec/eval <alias> "<code>"`.
  pyproject: added `[python_shell] = []` extra; updated `[all]`. 50 new tests
  (43 unit + 7 real-subprocess bootstrap integration); 250/250 pass. Added
  `examples/configs/python_shell_env.toml`. All v1 drivers are now shipped.

- **2026-05-29** — **[Phase 3 Complete]** Serial driver shipped. Added
  `lablink/interfaces/serial/` with `SerialDriverConfig(DriverConfig)` and
  `SerialDriver` implementing the full `LabLinkDriver[SerialDriverConfig]` ABC.
  Tools: `serial_query` (write + `read_until` with configured termination;
  `timed_out` flag when terminator not received), `serial_write` (write-only;
  `metadata={"bytes_written"}`), `serial_read` (drain OS buffer with timeout;
  `timed_out=True` on empty read), `serial_flush` (clears input + output buffers;
  returns `Result`). CLI: `lablink serial query <alias> "<cmd>"` and
  `lablink serial write <alias> "<cmd>"`. No `AuthConfig` mixin — serial is
  inherently local/physical. Parity map: `none/even/odd/mark/space` → pyserial
  single-char constants; case-insensitive. Port existence check on POSIX only
  (skipped on Windows COM ports). pyproject: added `[serial]` (pyserial>=3.5)
  extra; updated `[all]` and `[dev]`. 38 new serial tests via
  `patch("serial.Serial")`; 200/200 pass. Added `examples/configs/serial_device.toml`.

- **2026-05-29** — **[REST smoke test — live]** End-to-end MCP tool test against
  `jsonplaceholder` (public REST API, `auth_type = "none"`). All five REST tools
  exercised via MCP: `rest_get` (single resource + query params), `rest_post`
  (201 + `Location` header), `rest_put` (200, full replace), `rest_patch` (200,
  partial update), `rest_delete` (200, empty body). Every call returned
  `success=True` with correct `status_code` and `decoded` JSON. Driver behavior
  confirmed: HTTP 4xx/5xx pass through as `success=True` (agent reads
  `status_code`); `success=False` reserved for network-level failures.

- **2026-05-29** — **[Phase 2 Complete]** REST driver shipped. Added
  `lablink/interfaces/rest/` with `RestDriverConfig(DriverConfig, AuthConfig)` and
  `RestDriver` implementing the full `LabLinkDriver[RestDriverConfig]` ABC. Tools:
  `rest_get` (params + per-request headers), `rest_post`, `rest_put`, `rest_patch`
  (JSON body), `rest_delete`. All tools return `ReadResult` with
  `metadata={"status_code", "headers"}`; HTTP 4xx/5xx are `success=True` — the
  agent checks `status_code`. `success=False` reserved for network-level failures.
  CLI: `lablink rest get <alias> <path>` and `lablink rest post <alias> <path>
  --body '<json>'`. Auth: `none`, `bearer` (`Authorization: Bearer …`),
  `api_key` (`X-API-Key: …`), `basic` (httpx.BasicAuth). All credentials from
  env vars via `AuthConfig`. `verify_ssl` field on config (default `True`).
  Registered in both `DRIVER_REGISTRY` and `DRIVER_CONFIG_REGISTRY`. pyproject:
  added `[rest]` (httpx>=0.27) extra; updated `[all]` and `[dev]`. 42 new REST
  tests via `patch("httpx.Client")`; 162/162 pass. Added
  `examples/configs/rest_daq.toml`. Phase 1.5 (SSH streaming) deprioritized as a
  tech-debug item per developer direction.

- **2026-05-29** — **[External driver]** Added `type = "external"` routing stub for
  devices controlled by manufacturer-supplied MCP servers. `ExternalDriverConfig`
  has two fields: `mcp_server` (freeform label) and `tool_instructions` (routing
  hint surfaced to the agent via `device_memory` on `connect()`). `ExternalDriver`
  registers no operation tools — the external server provides those directly.
  `mcp_server.do_connect` / `do_diagnose` updated with a generic fallback: when
  no `<alias>.md` file exists, the driver's `ConnectResult.device_memory` is used
  instead of `None` (benefits external and any future driver that wants to supply
  default memory). 15 new tests; 120/120 pass. Added
  `examples/configs/external_saleae.toml`.

- **2026-05-29** — **[Phase 1 Complete]** SSH driver shipped as the first new
  driver on the multi-driver core. Added `lablink/interfaces/ssh/` with
  `SshDriverConfig(DriverConfig, AuthConfig)` and `SshDriver` implementing the
  full `LabLinkDriver[SshDriverConfig]` ABC. Tools: `ssh_exec` (non-interactive
  exec channel; `metadata={exit_code, stderr}`) and `ssh_shell_session`
  (per-call PTY; returns full terminal transcript). CLI: `lablink ssh exec
  <alias> "<command>"`. All paramiko imports are lazy; `check_python_deps()`
  uses `find_spec`. Auth supports `none`, `ssh_key`, `ssh_password`, `basic`.
  Diagnose: TCP reachability + key file presence + auth config validation.
  Registered in both `DRIVER_REGISTRY` and `DRIVER_CONFIG_REGISTRY`. Updated
  `pyproject.toml`: pyvisa moved from core deps to `[visa]` extra; added
  `[ssh]` (paramiko>=3.0), `[all]`, `[dev]` extras — matches `lablink_plan.md`
  §2.8 dep architecture. 105/105 tests pass (29 new SSH tests via
  `patch("paramiko.SSHClient")`). Added `examples/configs/ssh_pi.toml`.
  Hardware smoke test: pending real SSH target.

- **2026-05-29** — **[Phase 0c Complete]** Peripheral cleanup on top of the 0b
  core. Renamed `scpi_logger.py` → `event_logger.py` and formalized the §6.4
  log contract (`log_event(*, op, alias, success, error=None, duration_ms=None,
  **extra)`; ts/op/alias/success guaranteed). Rewrote `cli.py`: shared
  lifecycle subcommands stay top-level, per-driver subgroups register via
  `register_cli_commands` (so `lablink visa query/write`; flat `query`/`write`
  dropped); `diagnose` now emits pure JSON to stdout. Rewrote `_INSTRUCTIONS`
  multi-driver with a runtime loaded-driver count. Moved
  `examples/devices/example_scope.toml` → `examples/configs/visa_scope.toml`.
  Archived `docs/agent-bootstrap.md` and the pre-pivot agentlink-visa history
  into `docs/archive/`. Added CLI-gating, diagnose-enumeration, and
  event-logger-contract tests (76 passing). Updated README + CHANGELOG for the
  breaking tool/CLI renames. All of Phase 0 is now complete. (Bench Siglent
  went offline mid-session — confirmed not a regression; OS-level ping fails.)

- **2026-05-29** — **[Phase 0b Complete]** Architectural core landed. Added
  `lablink/base.py` (data models, config mixins all `kw_only=True`,
  `Session[ConfigT]`, `LabLinkDriver` ABC). Rewrote `session.py` into a
  protocol-agnostic registry with three-state `lookup()` + `get(alias,
  expected_type)`; the shared `pyvisa.ResourceManager` moved onto the VISA
  driver. Refactored VISA into `lablink/interfaces/visa/` on the ABC,
  self-registering `visa_query`/`visa_write`. Rewrote `config.py` as a generic
  loader via `DRIVER_CONFIG_REGISTRY` (`load_instrument_memory` →
  `load_device_memory`). Added `lablink/interfaces/__init__.py` with both
  registries + import-time key-match guard. Rewrote `mcp_server.py`: shared
  lifecycle tools (`connect`/`disconnect`/`list_devices`/`diagnose`) dispatching
  via the registry, device_memory injected via `dataclasses.replace()`,
  per-driver tool registration gated on `check_python_deps()`. Deleted
  `tools.py` + `diagnostics.py`. Test suite re-homed and expanded to 71 (was
  58): `test_config`, `test_logger`, `test_shared_tools`, `test_dispatch`,
  `test_fastmcp_late_registration`, `interfaces/test_visa`. Deferred to 0c:
  `event_logger` rename, CLI subgroups, `_INSTRUCTIONS` multi-driver rewrite,
  `examples/` restructure. Validated end-to-end on the real Siglent
  SDS1104X-E the same day (connect/diagnose/query/write/device_memory/event-log
  all pass; live auto-migration of 4 configs) — 0b exit gate fully met. See
  `implementation_log.md` for per-task detail and the hardware smoke-test record.

- **2026-05-29** — **[Phase 0a Complete]** Mechanical rename + auto-migration shipped.
  `agentlink/` → `lablink/`; entry points `lablink` / `lablink-mcp` replace
  `agentlink` / `agentlink-mcp` (hard cutover, no shim — per
  `lablink_plan.md` §9). All env vars `AGENTLINK_*` → `LABLINK_*`; config
  dir `~/.agentlink/instruments/` → `~/.lablink/devices/`; log dir
  `~/.agentlink/logs/` → `~/.lablink/logs/`. New
  `maybe_migrate_legacy_configs()` in `lablink/config.py` copies legacy
  `.toml`/`.md` files into the new dir on first run, injecting
  `type = "visa"` into TOML that lacks it; gated by `MIGRATED.txt`
  marker in legacy dir and by destination already containing `.toml`
  files; opt-out via `LABLINK_AUTO_MIGRATE=0`. CLI command structure
  unchanged (the architectural CLI rewrite into per-driver subgroups is
  Phase 0c). Added `CHANGELOG.md` and a "Migration from agentlink-visa"
  section in `README.md` per the plan's discoverability requirement.
  58/58 tests pass (47 original + 11 new `TestAutoMigration` cases). See
  `implementation_log.md` for per-task detail.

- **2026-05-28** — **[Docs / Pivot]** Rewrote `docs/agent_docs/` for the LabLink pivot. `readme_agent.md` updated with new onboarding order including `lablink_plan.md` as a required ingestion document. `project_goal.md` rewritten for LabLink scope and DUT-control-as-product framing. `system_architecture.md` rewritten to document both the current (pre-pivot) layout and the target architecture with explicit migration mapping. `agent_development.md` updated for multi-driver patterns (per-driver `register_tools()`, `Session[ConfigT]`, lazy imports, dispatch tests). `current_status.md` (this file) rewritten to phase 0 planning.

- **2026-05-28** — **[Planning]** Wrote `docs/lablink_plan.md` v2 (~640 lines): single-repo multi-driver architecture; shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`) + per-driver operation tools (`visa_query`, `ssh_exec`, `rest_get`, ...) registered dynamically based on installed extras. Replaces an earlier draft that proposed a uniform `connect/query/write/read/custom_action` surface — that draft was rejected because the per-protocol semantic overload made it the worst of both worlds. Plan locks: `Generic[ConfigT]` on Session and Driver, `AuthConfig` and `DocumentedConfig` config mixins, streaming-deferred-to-post-v1, auto-migration in Phase 0a (not 0b), `python_shell` driver promoted into v1 as the vendor-SDK gateway.

- **Earlier agentlink-visa-era history (pre-pivot, 2026-05-26 → 2026-05-27)**
  — the original single-driver v0.1 implementation log (MVP, diagnostics,
  SCPI logging, per-instrument memory, multi-doc IDs, and the square-wave
  hardware demo that drove the pivot) is archived in
  `docs/archive/current_status_agentlink_visa.md`.

---

## Notes for Reviewing Agents

If you have been brought in to review `docs/lablink_plan.md` or related design choices:

1. The plan in its current form (v2, dated 2026-05-28) reflects one full review cycle with a prior agent. The §0 Revision Notes at the top of the plan summarize what changed and why.
2. The previous draft locked a uniform tool surface (`query`/`write`/`read`/`custom_action`/`list_actions`) that was dropped in v2. If you find yourself reasoning about those tools, you're reading stale framing somewhere — re-read §0.2 and §2.2.
3. The pivot from "agentlink-visa demos techmanual" to "LabLink is the product, techmanual is the docs layer" is locked. Don't argue for the older framing without strong evidence.
4. The unified-repo decision is locked. Don't propose re-splitting into sibling repos without engaging with the user-friction rationale in §0.3.
5. Streaming drivers (MQTT, WebSocket, continuous serial) are deferred to post-v1. The data model has hooks; no v1 code uses them. Don't design streaming features into v1 drivers.
6. Most other things are negotiable — push back on anything that looks weak, especially threading/lifecycle details, the contributor flow for new drivers, and the `_INSTRUCTIONS` scaling question (acknowledged but unresolved in §13).
