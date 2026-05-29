# Project Status

## Current Phase
**LabLink Phase 0b Complete — Ready for Phase 0c (Peripheral Cleanup)**

Phase 0b landed the architectural core. The single-driver VISA implementation
is now a multi-driver dispatch system:
- `lablink/base.py` — all data models (`Result`, `ReadResult`, `ConnectResult`,
  `DiagnosticResult`, `SystemDepStatus`), config dataclasses (`DriverConfig` +
  `AuthConfig`/`DocumentedConfig` mixins, all `kw_only=True`),
  `Session[ConfigT]`, and the `LabLinkDriver[ConfigT]` ABC.
- VISA refactored into `lablink/interfaces/visa/` (driver + config) on the ABC;
  it self-registers `visa_query` / `visa_write` via `register_tools(mcp)`.
- Shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`)
  live in `mcp_server.py` and dispatch via `DRIVER_REGISTRY` /
  `DRIVER_CONFIG_REGISTRY`. No protocol-specific logic remains in the server.
- `lablink/tools.py` and `lablink/diagnostics.py` deleted (folded in).

**71/71 tests pass** (was 58). Tool surface verified: 4 shared + 2 VISA.

**Authoritative architectural spec:** `docs/lablink_plan.md`. The per-task
implementation log is `docs/agent_docs/implementation_log.md`.

**Next phase: 0c — Peripheral Cleanup.** `scpi_logger` → `event_logger`
(+ §6.4 field contract); CLI subgroup rewrite (`lablink visa query`, dropping
flat `query`/`write` — `VisaDriver.register_cli_commands` is already
implemented and ready to wire); `_INSTRUCTIONS` multi-driver rewrite with the
runtime driver-count paragraph; `examples/devices/` → `examples/configs/`;
archive `agent-bootstrap.md` and old current_status history. See
`lablink_plan.md` §9 Phase 0c.

**0b exit gate: MET — including real-hardware validation.** On 2026-05-29 the
refactored VISA path was exercised end-to-end against the physical Siglent
SDS1104X-E (connect/diagnose/query/write/device_memory/event-log), and Phase
0a's auto-migration fired live (4 configs). The literal pre-0a `agentlink`
baseline diff was never capturable (that entry point is gone), but the outcome
it protected — the new `lablink` path driving the instrument correctly — is
directly confirmed. See `implementation_log.md` for the full smoke-test record.

---

## What Exists On Disk Right Now

- `lablink/base.py` — data models, config dataclasses, `Session[ConfigT]`, the
  `LabLinkDriver` ABC
- `lablink/session.py` — protocol-agnostic session registry (three-state lookup)
- `lablink/config.py` — generic loader via `DRIVER_CONFIG_REGISTRY`; auto-migration
- `lablink/interfaces/__init__.py` — `DRIVER_REGISTRY` + `DRIVER_CONFIG_REGISTRY`
- `lablink/interfaces/visa/` — `VisaDriver` + `VisaDriverConfig` (only v1 driver so far)
- `lablink/scpi_logger.py` — event log (renamed to `event_logger` in 0c)
- `mcp_server.py` — shared lifecycle tools + per-driver registration; `lablink-mcp`
- `cli.py` — flat CLI (subgroup rewrite is 0c); `lablink`
- `~/.lablink/devices/<alias>.toml` config location; legacy
  `~/.agentlink/instruments/` auto-migrated on first run
- `examples/devices/example_scope.toml` (now carries `type = "visa"`)
- `tests/` — `test_config`, `test_logger`, `test_shared_tools`, `test_dispatch`,
  `test_fastmcp_late_registration`, `interfaces/test_visa` (71 tests)
- `CHANGELOG.md`, `docs/lablink_plan.md` (authoritative), `docs/agent_docs/`
- `agent-bootstrap.md` at repo root — archived in Phase 0c

**Deleted in 0b:** `lablink/tools.py`, `lablink/diagnostics.py`,
`tests/test_tools.py`.

For the mapping of current code → target code, see `system_architecture.md` §5.

---

## Technical Debt & Known Issues

- **GitHub repo still named `agentlink-visa`.** Local repo dir, GitHub
  remote URL (`github.com/techmanual-ai/agentlink-visa`), and the
  `server.json` `repository.url` field are unchanged. The GitHub rename
  is a manual step outside Phase 0a; the README and `server.json` package
  identifier already use `lablink-mcp`.
- **Duplicate Siglent aliases.** `sds1104xe` and `siglent_sds1104xe` in
  `~/.lablink/devices/` both point at the same scope (both migrated from the
  legacy dir). Harmless but confusing — delete one. Not code; a config-hygiene
  note for the developer.
- **Pre-0a `agentlink` baseline diff — closed by hardware validation, not by
  the diff.** The literal diff was never capturable (entry point removed in 0a);
  the 2026-05-29 real-hardware smoke test supersedes it. No longer outstanding.
- **VISA required-field validation relaxed in 0b.** Only `type`/`alias`/
  `timeout_ms` are strictly required now; VISA-specific fields default and an
  empty `resource_string` is caught in `connect()`. This is the plan's design,
  not an oversight.
- **No `wrap_tool_errors()` helper in base.py.** The plan mentions one but does
  not specify it; deferred until a second driver creates real duplication
  (scope discipline). The two VISA tools inline their error handling.
- **CLI is still the flat 0a shape** (`lablink query`/`write`, VISA-only) and
  imports the shared `do_*` functions from `mcp_server`. The subgroup rewrite
  (`lablink visa query`) and the clean separation are Phase 0c.
- **CLI `connect`/`query`/`write` open and close a session per invocation.**
  Intentional for debug UX; sessions persist only across MCP calls. Not a
  concern for the rearchitecture.
- **`agent-bootstrap.md` at repo root** references the old architecture. Phase 0c archives it to `docs/archive/agent-bootstrap.md`. Treat it as historical context only.

---

## Recent History

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

- **2026-05-27** — **[Demo / Validation]** Square-wave oscilloscope demo run on real hardware. Agent measured relative time offset between channels on a real scope using VISA/SCPI through agentlink-visa MCP. Video footage captured for product. Demo validated that (1) the architecture works end-to-end on real hardware, (2) DUT control is the value proposition (not techmanual demo), and (3) techmanual.ai layered on top measurably improves first-try success on unfamiliar instruments. This demo result drove the LabLink architectural pivot.

- **2026-05-27** — **[Feature]** Multi-doc IDs, alias naming convention, explicit techmanual instruction. (1) `techmanual_document_id: Optional[int]` renamed to `techmanual_document_ids: list[int]` in `InstrumentConfig`; legacy singular field auto-converted on load via `_load_document_ids()`. (2) `connect()` response key updated to `techmanual_document_ids`. (3) `_INSTRUCTIONS` in `mcp_server.py`: added `## Using techmanual.ai` section directing agents to consult docs before issuing SCPI; updated config format docs; added `<manufacturer>_<model>` alias naming convention. (4) Both local instrument configs updated to `techmanual_document_ids = [1291, 1323]`. (5) Agent docs updated. 3 new tests; 47/47 passing.

- **2026-05-27** — **[Feature]** Added per-instrument memory file. `load_instrument_memory(alias)` added to `config.py` — reads `~/.agentlink/instruments/<alias>.md`, returns content or None. `connect()` response now includes `instrument_memory` field. `run_diagnostics()` now includes `instrument_memory` in `alias_check`. Added `## Instrument Memory` section to `_INSTRUCTIONS` with format spec. 7 new tests. 44/44 passing.

- **2026-05-27** — **[Feature]** Added SCPI transaction logging and VISA/SCPI agent context. New `agentlink/scpi_logger.py`: `log_event(**fields)` appends JSONL entries to `~/.agentlink/logs/YYYY-MM-DD.jsonl`. Default-on; disable by setting `AGENTLINK_LOG_DIR=""`. Every connect/disconnect/query/write call logs op, alias, command, response/error. Never raises. Added `## VISA/SCPI Behavior` section to `_INSTRUCTIONS`. Added 8 `TestScpiLogger` tests. 37/37 tests passing.

- **2026-05-26** — **[Bugfix + Feature]** Pre-hardware polish pass. Fixed session leak in `connect()`. Fixed `agentlink list` stdout/stderr bug. Removed dead `QueryError` exception. Added `agentlink/diagnostics.py`: `run_diagnostics(alias=None)` checks pyvisa/pyvisa-py versions, VISA backend health, `list_resources()` output, interface-type breakdown, config directory status, and alias-specific reachability. Exposed as `diagnose_connection` MCP tool and `agentlink diagnose [alias]` CLI command. 29/29 tests passing.

- **2026-05-26** — **[UX/Docs]** Added MCP server-level `instructions` to `mcp_server.py`. Created `server.json` (MCP registry manifest). Rewrote `README.md`: pip-based install, `"command": "agentlink-mcp"` MCP config pattern.

- **2026-05-26** — **[Bugfix/Polish]** Pre-hardware audit pass. Fixed CLI double command registration. Fixed `ResourceManager` leak in `session.py`. Fixed silent double-connect overwrite. Added broken-config warnings to `agentlink list`. 17/17 tests passing.

- **2026-05-26** — **[MVP]** Full agentlink-visa v0.1 implementation complete. Built: `agentlink/exceptions.py`, `agentlink/config.py`, `agentlink/session.py`, `agentlink/tools.py`, `mcp_server.py`, `cli.py`, `tests/test_tools.py`, etc. 16/16 unit tests passing (all mocked, no hardware required).

- **2026-05-26** — **[Bootstrap]** Repository created. `agent-bootstrap.md` written by lead developer establishing founding agentlink-visa design decisions. `docs/agent_docs/` scaffolded. No application code yet.

---

## Notes for Reviewing Agents

If you have been brought in to review `docs/lablink_plan.md` or related design choices:

1. The plan in its current form (v2, dated 2026-05-28) reflects one full review cycle with a prior agent. The §0 Revision Notes at the top of the plan summarize what changed and why.
2. The previous draft locked a uniform tool surface (`query`/`write`/`read`/`custom_action`/`list_actions`) that was dropped in v2. If you find yourself reasoning about those tools, you're reading stale framing somewhere — re-read §0.2 and §2.2.
3. The pivot from "agentlink-visa demos techmanual" to "LabLink is the product, techmanual is the docs layer" is locked. Don't argue for the older framing without strong evidence.
4. The unified-repo decision is locked. Don't propose re-splitting into sibling repos without engaging with the user-friction rationale in §0.3.
5. Streaming drivers (MQTT, WebSocket, continuous serial) are deferred to post-v1. The data model has hooks; no v1 code uses them. Don't design streaming features into v1 drivers.
6. Most other things are negotiable — push back on anything that looks weak, especially threading/lifecycle details, the contributor flow for new drivers, and the `_INSTRUCTIONS` scaling question (acknowledged but unresolved in §13).
