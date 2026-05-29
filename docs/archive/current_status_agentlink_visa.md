# Archived History — agentlink-visa era (pre-LabLink-pivot)

These Recent History entries were pruned from `docs/agent_docs/current_status.md`
during Phase 0c (rolling-window maintenance, per `readme_agent.md` §3). They
document the original single-driver `agentlink-visa` v0.1 implementation, before
the 2026-05-28 LabLink pivot. Kept here for provenance; not load-bearing for
current work. The authoritative spec is `docs/lablink_plan.md`.

---

- **2026-05-27** — **[Demo / Validation]** Square-wave oscilloscope demo run on real hardware. Agent measured relative time offset between channels on a real scope using VISA/SCPI through agentlink-visa MCP. Video footage captured for product. Demo validated that (1) the architecture works end-to-end on real hardware, (2) DUT control is the value proposition (not techmanual demo), and (3) techmanual.ai layered on top measurably improves first-try success on unfamiliar instruments. This demo result drove the LabLink architectural pivot.

- **2026-05-27** — **[Feature]** Multi-doc IDs, alias naming convention, explicit techmanual instruction. (1) `techmanual_document_id: Optional[int]` renamed to `techmanual_document_ids: list[int]` in `InstrumentConfig`; legacy singular field auto-converted on load via `_load_document_ids()`. (2) `connect()` response key updated to `techmanual_document_ids`. (3) `_INSTRUCTIONS` in `mcp_server.py`: added `## Using techmanual.ai` section directing agents to consult docs before issuing SCPI; updated config format docs; added `<manufacturer>_<model>` alias naming convention. (4) Both local instrument configs updated to `techmanual_document_ids = [1291, 1323]`. (5) Agent docs updated. 3 new tests; 47/47 passing.

- **2026-05-27** — **[Feature]** Added per-instrument memory file. `load_instrument_memory(alias)` added to `config.py` — reads `~/.agentlink/instruments/<alias>.md`, returns content or None. `connect()` response now includes `instrument_memory` field. `run_diagnostics()` now includes `instrument_memory` in `alias_check`. Added `## Instrument Memory` section to `_INSTRUCTIONS` with format spec. 7 new tests. 44/44 passing.

- **2026-05-27** — **[Feature]** Added SCPI transaction logging and VISA/SCPI agent context. New `agentlink/scpi_logger.py`: `log_event(**fields)` appends JSONL entries to `~/.agentlink/logs/YYYY-MM-DD.jsonl`. Default-on; disable by setting `AGENTLINK_LOG_DIR=""`. Every connect/disconnect/query/write call logs op, alias, command, response/error. Never raises. Added `## VISA/SCPI Behavior` section to `_INSTRUCTIONS`. Added 8 `TestScpiLogger` tests. 37/37 tests passing.

- **2026-05-26** — **[Bugfix + Feature]** Pre-hardware polish pass. Fixed session leak in `connect()`. Fixed `agentlink list` stdout/stderr bug. Removed dead `QueryError` exception. Added `agentlink/diagnostics.py`: `run_diagnostics(alias=None)` checks pyvisa/pyvisa-py versions, VISA backend health, `list_resources()` output, interface-type breakdown, config directory status, and alias-specific reachability. Exposed as `diagnose_connection` MCP tool and `agentlink diagnose [alias]` CLI command. 29/29 tests passing.

- **2026-05-26** — **[UX/Docs]** Added MCP server-level `instructions` to `mcp_server.py`. Created `server.json` (MCP registry manifest). Rewrote `README.md`: pip-based install, `"command": "agentlink-mcp"` MCP config pattern.

- **2026-05-26** — **[Bugfix/Polish]** Pre-hardware audit pass. Fixed CLI double command registration. Fixed `ResourceManager` leak in `session.py`. Fixed silent double-connect overwrite. Added broken-config warnings to `agentlink list`. 17/17 tests passing.

- **2026-05-26** — **[MVP]** Full agentlink-visa v0.1 implementation complete. Built: `agentlink/exceptions.py`, `agentlink/config.py`, `agentlink/session.py`, `agentlink/tools.py`, `mcp_server.py`, `cli.py`, `tests/test_tools.py`, etc. 16/16 unit tests passing (all mocked, no hardware required).

- **2026-05-26** — **[Bootstrap]** Repository created. `agent-bootstrap.md` written by lead developer establishing founding agentlink-visa design decisions. `docs/agent_docs/` scaffolded. No application code yet.
