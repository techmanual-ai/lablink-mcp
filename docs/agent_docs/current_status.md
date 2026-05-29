# Project Status

## Current Phase
**LabLink Phase 0 — Planning (Architectural Pivot Under Review)**

The agentlink-visa v0.1 MVP is complete and was validated on real hardware (square-wave oscilloscope demo, 2026-05-27). That demo drove the strategic conclusion that **DUT control is the actual product**, not a demo for techmanual.ai. The project is now being rearchitected and renamed to LabLink MCP — a multi-driver MCP server with VISA, SSH, REST, serial, and python_shell support in v1.

**Authoritative architectural spec:** `docs/lablink_plan.md`. Read this before any non-trivial design discussion. It supersedes `project_goal.md` and `system_architecture.md` wherever they conflict.

The plan is currently going through review cycles with multiple agents. No Phase 0 implementation work has started. Wait for the lead developer's explicit instruction before beginning Phase 0a (mechanical rename + auto-migration).

---

## What Exists On Disk Right Now

- `agentlink/` package (single-driver VISA implementation) — works, all tests pass
- `mcp_server.py`, `cli.py` — agentlink-visa entrypoints
- `~/.agentlink/instruments/<alias>.toml` configs (local development only)
- `agent-bootstrap.md` at repo root — original agentlink-visa founding document; will be archived in Phase 0b
- `docs/lablink_plan.md` — new architectural plan (this is what's being reviewed)
- `docs/agent_docs/` — onboarding docs, freshly rewritten 2026-05-28 to reflect the LabLink pivot

For the mapping of current code → target code, see `system_architecture.md` §5.

---

## Technical Debt & Known Issues

- **Naming and paths are still `agentlink-visa`.** Package name, repo, PyPI name, env var prefix, config directory all use the old names. The Phase 0a migration plan in `lablink_plan.md` §9 covers the rename including auto-migration of user configs from `~/.agentlink/instruments/` to `~/.lablink/devices/`.
- **CLI `connect`/`query`/`write` subcommands open and close a session per invocation.** Intentional for debug UX but means the session is not persistent across CLI calls (only across MCP calls). Not a concern for v0.1 or the LabLink rearchitecture.
- **`agent-bootstrap.md` at repo root** references the old architecture. Phase 0b archives it to `docs/archive/agent-bootstrap.md`. Treat it as historical context only.

---

## Recent History

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
