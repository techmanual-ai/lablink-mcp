# Project Status

## Current Phase
**LabLink Phase 0a Complete — Ready for Phase 0b (Architectural Core)**

Phase 0a landed: the package, CLI command, MCP entry point, env vars, and
config paths are all renamed from `agentlink-visa` → `lablink-mcp`, and
auto-migration of legacy `~/.agentlink/instruments/` configs into
`~/.lablink/devices/` runs on first invocation. The v0.1 tool surface
(`connect_instrument`, `query_instrument`, etc.) is unchanged in behavior —
only string-level renames in this phase. 58/58 tests pass (47 original +
11 new migration tests).

**Authoritative architectural spec:** `docs/lablink_plan.md`. The
per-task implementation log is `docs/agent_docs/implementation_log.md`.

**Next phase: 0b — Architectural Core.** Driver ABC, data models, VISA
driver refactor onto the ABC, dispatch via `DRIVER_REGISTRY`. See
`lablink_plan.md` §9 Phase 0b. Phase 0b has a stop-the-line FastMCP
smoke test as Task 0 — write that before any other 0b work.

**Outstanding pre-0b work:** The Phase 0b exit gate requires a pre-rename
baseline of `agentlink connect <local_instrument>` output for the
behavioral-equivalence diff. This was deferred in 0a (Siglent scope was
offline). See `implementation_log.md` for the recommended capture path
before 0b ships.

---

## What Exists On Disk Right Now

- `lablink/` package (single-driver VISA implementation, renamed from `agentlink/`) — works, 58/58 tests pass
- `mcp_server.py`, `cli.py` — `lablink-mcp` / `lablink` entrypoints
- `~/.lablink/devices/<alias>.toml` is the new config location; legacy
  `~/.agentlink/instruments/` is auto-migrated on first run
- `examples/devices/example_scope.toml` (renamed from `examples/instruments/`)
- `CHANGELOG.md` documenting the rename and auto-migration
- `agent-bootstrap.md` at repo root — original agentlink-visa founding document; will be archived in Phase 0c
- `docs/lablink_plan.md` — authoritative architectural plan
- `docs/agent_docs/` — onboarding docs + `implementation_log.md` (per-task
  progress log for the plan)

For the mapping of current code → target code, see `system_architecture.md` §5.

---

## Technical Debt & Known Issues

- **GitHub repo still named `agentlink-visa`.** Local repo dir, GitHub
  remote URL (`github.com/techmanual-ai/agentlink-visa`), and the
  `server.json` `repository.url` field are unchanged. The GitHub rename
  is a manual step outside Phase 0a; the README and `server.json` package
  identifier already use `lablink-mcp`.
- **Phase 0b behavioral-equivalence baseline not captured.** See
  `docs/agent_docs/implementation_log.md` Phase 0a deferred-tasks
  section. Required before Phase 0b can declare its exit gate met.
- **CLI `connect`/`query`/`write` subcommands open and close a session per invocation.** Intentional for debug UX but means the session is not persistent across CLI calls (only across MCP calls). Not a concern for v0.1 or the LabLink rearchitecture.
- **`agent-bootstrap.md` at repo root** references the old architecture. Phase 0c archives it to `docs/archive/agent-bootstrap.md`. Treat it as historical context only.

---

## Recent History

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
