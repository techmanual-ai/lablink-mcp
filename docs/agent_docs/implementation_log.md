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

