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

