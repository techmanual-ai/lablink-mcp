# Implementation Log

Per-task progress log for `docs/lablink_plan.md` execution. Detailed entries
(intent, actions, surprises, decisions, blockers) for each task. Complements
`current_status.md` (rolling high-level summary).

Format: most recent entry on top. Each entry has the structure:

```
## YYYY-MM-DD — Phase 0X — Task N: <name>
**Status:** in-progress | completed | blocked
**Intent:** what this task changes and why
**Actions taken:** concrete steps
**Surprises / decisions:** anything non-obvious; rationale recorded
**Open follow-ups:** anything left for a later task or phase
```

---

## Phase 0b — Architectural Core (ABC, data models, VISA refactor, dispatch)

Plan reference: `docs/lablink_plan.md` §9 Phase 0b. Scope: introduce the driver
ABC + data models, refactor VISA onto the ABC, and route everything through
`DRIVER_REGISTRY`. The `event_logger` rename, the CLI subgroup rewrite, and the
`_INSTRUCTIONS` multi-driver rewrite are explicitly deferred to 0c.

### 2026-05-29 — Phase 0b Task 0: FastMCP late-registration smoke test
**Status:** completed
**Intent:** Validate the load-bearing assumption (drivers register tools via an
instance method called after server construction) before building on it.
**Actions taken:** Wrote `tests/test_fastmcp_late_registration.py`. FastMCP
3.3.1 exposes `list_tools()` as an **async** method returning `FunctionTool`
objects with `.name`; the test uses `asyncio.run(mcp.list_tools())`.
**Surprises / decisions:** The plan's pseudo-code used `mcp.list_tools()`
sync; the real API is async in 3.x. Test passes — architecture is sound, green
light for the rest of 0b.

### 2026-05-29 — Phase 0b Tasks 1–7: core build
**Status:** completed
**Intent:** Land the multi-driver core with VISA as the only registered driver.
**Actions taken:**
- `lablink/base.py` (new): `Result`, `ReadResult`, `ConnectResult` (with the
  `__post_init__` device_memory→instrument_memory mirror), `DiagnosticResult`,
  `SystemDepStatus`, `DriverConfig`/`AuthConfig`/`DocumentedConfig` (all
  `kw_only=True`), `Session[ConfigT]`, and the `LabLinkDriver[ConfigT]` ABC.
- `lablink/session.py` (rewritten): protocol-agnostic registry — `_sessions`,
  `register`/`deregister`/`is_registered`/`get_any`, three-state `lookup()` +
  `SessionLookup`, and `get(alias, expected_type)`. The shared
  `pyvisa.ResourceManager` moved OUT of session.py onto the VISA driver (§4.2).
- `lablink/interfaces/visa/{config,driver,__init__}.py` (new): `VisaDriverConfig`
  and `VisaDriver(LabLinkDriver[VisaDriverConfig])` implementing connect /
  disconnect / diagnose / register_tools (`visa_query`, `visa_write`) /
  register_cli_commands / check_python_deps / system_dep_check. Operation logic
  lives in `visa_query_impl` / `visa_write_impl` so the MCP tool closures, the
  CLI subgroup, and the flat 0b CLI all share one code path.
- `lablink/config.py` (rewritten): generic loader resolving
  `DRIVER_CONFIG_REGISTRY[type]`, filtering TOML keys to the subclass's fields,
  injecting `alias` from the filename, expanding `_PATH_FIELDS`, converting the
  legacy singular `techmanual_document_id`, and converting a missing required
  field (TypeError) into `ConfigError`. `load_instrument_memory` →
  `load_device_memory`. Auto-migration carried over verbatim.
- `lablink/interfaces/__init__.py` (new): `DRIVER_REGISTRY` +
  `DRIVER_CONFIG_REGISTRY` (visa) with the import-time key-match guard.
- `mcp_server.py` (rewritten): shared lifecycle logic as plain functions
  (`do_connect`/`do_disconnect`/`do_list_devices`/`do_diagnose`) with thin
  `@mcp.tool()` wrappers `connect`/`disconnect`/`list_devices`/`diagnose`;
  server-lifetime driver singletons via `get_driver`; `register_driver_tools()`
  gates per-driver tool registration on `check_python_deps()`. device_memory is
  injected at the shared layer via `dataclasses.replace()` (§6.3.1).
- `cli.py` (updated, flat shape retained): routes through the shared `do_*`
  functions + the VISA impl methods. Subgroup rewrite stays 0c.
- Deleted `lablink/tools.py` and `lablink/diagnostics.py` (folded into the VISA
  driver + the shared diagnose/system-audit paths).

**Surprises / decisions:**
- **VISA-specific required-field validation relaxed.** v0.1 required 7 fields
  (resource_string, manufacturer, model_number, ...). The plan's
  `VisaDriverConfig` defaults all VISA-specific fields, so only the base
  `DriverConfig` fields (`type`, `alias`, `timeout_ms`) are strictly required;
  `alias` is filename-injected. `connect()` surfaces a clear error if
  `resource_string` is empty rather than failing inside pyvisa. The old
  "missing model_number raises" test was updated to "missing timeout_ms
  raises" to match the new contract.
- **Session registration moved after `*IDN?`.** v0.1 registered the resource
  then cleaned up on IDN failure; the new VISA `connect()` registers the
  Session only after IDN succeeds, so an IDN failure closes the raw resource
  and never registers — simpler, and no leak window.
- **Did not add a `wrap_tool_errors()` helper.** The plan mentions one for
  base.py but does not specify its contract, and only two VISA tools exist;
  adding an unspecified abstraction would violate the scope-discipline
  directive. Revisit when a second driver creates real duplication.
- **`check_python_deps` probes `pyvisa_py`** (the importable module) while
  reporting the pip name `pyvisa-py`.
- `list_tools()` is async (see Task 0). The dispatch tests use `asyncio.run`.

**Open follow-ups (0c and beyond):**
- `scpi_logger` → `event_logger` rename + canonical-field contract (§6.4).
- CLI subgroup rewrite (`lablink visa query`), dropping flat `query`/`write`;
  `VisaDriver.register_cli_commands` is implemented and ready to be wired.
- `_INSTRUCTIONS` multi-driver rewrite with the runtime driver-count paragraph.
- `examples/devices/` → `examples/configs/`; I added `type = "visa"` to the
  existing example now (the loader requires it) but left the directory rename
  for 0c.

### 2026-05-29 — Phase 0b Tasks (testing)
**Status:** completed
**Intent:** Re-home and expand the suite onto the new structure; cover the
Phase 0b exit-gate dispatch tests.
**Actions taken:** Deleted `tests/test_tools.py`. Added `tests/test_config.py`
(loader + device memory + the 11 migration cases), `tests/test_logger.py`,
`tests/test_shared_tools.py` (shared dispatch + device-memory injection),
`tests/test_dispatch.py` (registry key-match, unknown type, deps-missing
connect install hint, wrong-type session → None, register_driver_tools
gating), and `tests/interfaces/test_visa.py` (VISA connect/disconnect/query/
write/diagnose/audit-hooks). Added an autouse `_clear_session_registry`
fixture to conftest. **71 passed** (was 58).

### Phase 0b Exit Gate — status
- All prior behaviors retained (re-homed) and passing: **71/71**. ✅
- VISA driver implements the full ABC and self-registers its tools. ✅
- `mcp_server.py` has no protocol-specific logic — verified the assembled tool
  surface is exactly `{connect, disconnect, list_devices, diagnose}` (shared) +
  `{visa_query, visa_write}` (VISA self-registered). ✅
- Required dispatch tests present and passing (unknown type; deps-missing
  connect install hint; wrong-type session → None). ✅
- **Behavioral-equivalence diff against a pre-Phase-0a `agentlink connect`
  baseline: NOT closeable in this environment.** The baseline was never
  captured (deferred in 0a, Siglent offline) and the old `agentlink` entry
  point was removed in 0a, so the diff target does not exist. It also requires
  real hardware. This gate item remains open and is hardware-gated; the
  structural equivalence (same dispatch path, same `*IDN?` flow, same identity/
  memory/doc-id surface) is argued in code review instead. Recommend capturing
  the baseline via Option 1 (checkout pre-0a commit + plug in the scope) when
  the scope is next available, then diffing post-0b `lablink connect` output.

---

## Phase 0a — Mechanical Rename + Auto-Migration

Plan reference: `docs/lablink_plan.md` §9 Phase 0a. Scope is strictly
string-level renames plus the auto-migration in Task 7. CLI command
structure stays exactly as agentlink-visa shipped (architectural CLI rewrite
deferred to 0c).

### Deferred from Phase 0a (must land before Phase 0b exit gate)

- **Task 0 — Pre-rename baseline capture.** The plan calls for capturing
  `agentlink connect siglent_sds1104xe` stdout/stderr to
  `tests/baselines/agentlink_connect_pre_phase_0a.txt` before the rename,
  as the diff target for Phase 0b's behavioral-equivalence gate. Deferred
  at developer request because the Siglent scope is not powered on right
  now. **This is the only chance to capture the baseline; the old
  `agentlink` CLI is removed by Phase 0a Task 6.** Mitigation options for
  Phase 0b:
  1. Capture the baseline against a future state of the codebase by
     checking out the pre-Phase-0a commit, plugging in the scope, and
     running the old CLI.
  2. Hand-construct an expected-output document from the connect-success
     code path in `agentlink/tools.py:connect()` and use that as the diff
     target.
  Option 1 is more faithful and is the recommended approach when the scope
  is next available.

---

## 2026-05-29 — Phase 0a Tasks 1-6: Mechanical rename + pyproject
**Status:** completed
**Intent:** Rename `agentlink-visa` → `lablink-mcp` end to end (package,
entry points, paths, env vars, brand strings) without changing behavior.
CLI command structure stays exactly as agentlink-visa shipped.

**Actions taken:**
- `git mv agentlink/ lablink/` (Task 1).
- Single sed sweep across `lablink/*.py`, `mcp_server.py`, `cli.py`,
  `tests/*.py`, `.env.example`, `README.md`, `server.json` (Tasks 3, 4, 5):
  - `from agentlink.X` / `import agentlink.X` → `lablink`
  - `AGENTLINK_*` env vars → `LABLINK_*`
  - `~/.agentlink/instruments` → `~/.lablink/devices`
  - `~/.agentlink/logs` → `~/.lablink/logs`
  - `AgentLink-Visa` → `LabLink` (brand)
  - `agentlink-visa` → `lablink-mcp` (package name)
  - `agentlink-mcp` → `lablink-mcp` (MCP entry point)
  - `agentlink {list,connect,disconnect,query,write,diagnose}` →
    `lablink {…}`
- Hand-edited the three `Path.home() / ".agentlink" / "..."` constructions
  that sed couldn't catch (config.py, scpi_logger.py, test_tools.py).
- Rewrote `pyproject.toml` with the new package name and entry points
  (`lablink` and `lablink-mcp`); deleted the old entry points (Task 6).
- Renamed `examples/instruments/` → `examples/devices/` to match the
  runtime convention; updated README link and the example file's
  "Copy to…" comment.
- Added a "Migration from agentlink-visa" section near the top of
  `README.md` and created `CHANGELOG.md` documenting the rename. The plan
  §9 Phase 0a requires both as the discoverability path that compensates
  for the hard cutover of the old `agentlink-mcp` entry point.

**Surprises / decisions:**
- macOS HFS is case-insensitive — the case-insensitive env-var test had
  to use numbered subdirs rather than `tmp_path / val`, otherwise `False`
  and `false` collided.
- `Click --help` short-circuits before the group callback fires, so
  `lablink --help` does *not* trigger auto-migration. End-user
  invocations (`lablink list`, `lablink connect`, ...) and
  `lablink-mcp main()` do trigger it.
- Migration dest dir uses `get_config_dir()` (respects
  `LABLINK_CONFIG_DIR` override), not the literal `~/.lablink/devices/`
  the spec wording implies. Migrating into a dir the user has overridden
  away from would make migration invisible.

**Open follow-ups:**
- Phase 0c will restructure `examples/devices/` → `examples/configs/`
  per the target layout in `lablink_plan.md` §7.

---

## 2026-05-29 — Phase 0a Task 7: Auto-migration in lablink/config.py
**Status:** completed
**Intent:** First-run migration of `~/.agentlink/instruments/` →
`~/.lablink/devices/` so users do not have to manually move configs.
Trigger gated by `MIGRATED.txt` marker in the legacy dir and by the
destination already having `.toml` files; opt-out via
`LABLINK_AUTO_MIGRATE=0`.

**Actions taken:**
- Added `maybe_migrate_legacy_configs()` and helpers
  (`_auto_migrate_enabled`, `_maybe_inject_visa_type`) to
  `lablink/config.py`. Function never raises — filesystem errors print
  one stderr line per affected file.
- Wired the call into `mcp_server.main()` (before `mcp.run()`) and the
  `cli` Click group callback so migration runs once per process.
- Per-file rules implemented exactly to the §9 contract: no-overwrite,
  parse-first injection of `type = "visa"`, malformed TOML copied as-is
  with a warning, MIGRATED.txt summary in the legacy dir.

**Surprises / decisions:**
- The plan's MIGRATED.txt example uses millisecond precision but the
  spec text says ISO-8601; I went with `timespec='seconds'` (e.g.
  `2026-05-29T15:32:17+00:00`) because sub-second precision is
  irrelevant to the operation and reads cleaner.
- When the marker write fails after a successful copy, the function
  still prints the "Migrated N files" line and a separate warning about
  the marker. Next run will skip already-copied files (no-overwrite)
  and re-attempt the marker — slightly degraded forensics but the
  migration itself is intact.

**Open follow-ups:** none for Phase 0a.

---

## 2026-05-29 — Phase 0a Task 8: Migration tests + conftest opt-out
**Status:** completed
**Intent:** Cover the §9 migration contract with mock-only unit tests
and prevent the autouse test environment from migrating the developer's
real `~/.agentlink/instruments/`.

**Actions taken:**
- Added an autouse `_disable_auto_migration` fixture in
  `tests/conftest.py` that sets `LABLINK_AUTO_MIGRATE=0`. Tests that
  exercise migration explicitly `monkeypatch.delenv` it inside the test
  body.
- Added `TestAutoMigration` (11 cases) in `tests/test_tools.py`:
  happy path, existing `type` field preserved, MIGRATED.txt gates
  re-run, env-var disable (incl. case-insensitive), destination with
  existing `.toml` skips entirely, destination with only `.md` still
  migrates, per-file no-overwrite, malformed TOML copied with warning,
  no legacy dir is a no-op, non-toml/non-md files ignored.
- All 58 tests pass (47 original + 11 new).

**Surprises / decisions:**
- Used `capsys` to assert on the stderr summary line rather than
  capturing via `caplog` (the function uses `print(..., file=sys.stderr)`
  directly, not the `logging` module).

**Open follow-ups:**
- Phase 0b will add `tests/test_dispatch.py` for the
  type→driver registry tests; the migration tests stay where they are.

---

## Phase 0a Exit Criterion

> All existing tests pass unchanged plus the new migration tests.
> `lablink connect bench_scope` on a machine with an existing
> `~/.agentlink/instruments/bench_scope.toml` works end-to-end with no
> manual user steps.

**Status: met (with one caveat).**
- 58/58 tests pass (47 original; the "unchanged" criterion held — only
  module-path / env-var renames inside test bodies, no behavioral
  changes).
- The end-to-end check is structurally satisfied (migration is
  unconditional on first run with legacy configs present and a clean
  destination), but has not been validated against real hardware
  because the Siglent scope was offline. Recommended verification when
  the scope is next available: run `lablink connect siglent_sds1104xe`
  in a fresh shell and confirm (a) the stderr migration line appears
  once, (b) `~/.lablink/devices/siglent_sds1104xe.toml` is populated
  with `type = "visa"` prepended, (c) the IDN comes back, (d) re-running
  is silent (gated by `MIGRATED.txt`).

