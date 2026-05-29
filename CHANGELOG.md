# Changelog

## Unreleased — LabLink Phase 0b + 0c (multi-driver architecture)

The architectural rewrite from single-driver VISA to a multi-driver dispatch
system (driver ABC + registries). See `docs/lablink_plan.md` §9.

**Breaking — tool and CLI surface renamed.**

| Old (agentlink-visa / Phase 0a) | New |
|-----|-----|
| MCP tool `connect_instrument` | `connect` |
| MCP tool `disconnect_instrument` | `disconnect` |
| MCP tool `query_instrument` | `visa_query` |
| MCP tool `write_instrument` | `visa_write` |
| MCP tool `diagnose_connection` | `diagnose` (+ new `list_devices`) |
| CLI `lablink query <alias> ...` | `lablink visa query <alias> ...` |
| CLI `lablink write <alias> ...` | `lablink visa write <alias> ...` |

Shared lifecycle tools (`connect`, `disconnect`, `list_devices`, `diagnose`)
work across all drivers and dispatch via the config `type` field. Per-driver
operation tools (`visa_query`, `visa_write`) register only when that driver's
dependencies are installed. CLI shared commands stay top-level; per-driver
commands move under a driver subgroup (`lablink visa ...`).

**Config now requires a `type` field** selecting the driver (e.g.
`type = "visa"`). Migrated agentlink-visa configs get `type = "visa"` injected
automatically (Phase 0a auto-migration); new configs must declare it. Example:
`examples/configs/visa_scope.toml`.

### Added
- Driver ABC (`LabLinkDriver`), data models, and config mixins in `lablink/base.py`.
- `DRIVER_REGISTRY` / `DRIVER_CONFIG_REGISTRY` dispatch (`lablink/interfaces/`).
- `list_devices` tool/`lablink list` reporting per-alias status.
- System-audit `diagnose()` (no alias): per-driver dependency report.

### Changed
- `scpi_logger` → `event_logger`; canonical log fields formalized
  (`ts`/`op`/`alias`/`success` guaranteed; §6.4). The `op` field is now any
  tool name, not just SCPI ops.
- Server `instructions` rewritten multi-driver, with a runtime loaded-driver
  count.
- `instrument_memory` → `device_memory` on `connect()` (the old field is kept
  as a deprecated mirror through Phase 1).

### Validated
- The refactored VISA path was exercised end-to-end on a real Siglent
  SDS1104X-E (connect / diagnose / query / write / device memory / event log),
  closing the Phase 0b exit gate.

### Archived
- `docs/agent-bootstrap.md` → `docs/archive/agent-bootstrap.md`.

## Unreleased — LabLink Phase 0a (mechanical rename + auto-migration)

**Breaking change.** The `agentlink-visa` PyPI package has been renamed to
`lablink-mcp` as part of the pivot to a multi-driver architecture (see
`docs/lablink_plan.md`). The v0.1 tool surface is unchanged in behavior; the
architectural rewrite (driver ABC, dispatch, per-driver tools) lands in
Phase 0b/0c.

### Renamed

| Old | New |
|-----|-----|
| PyPI package: `agentlink-visa` | `lablink-mcp` |
| Python package: `agentlink` | `lablink` |
| CLI command: `agentlink` | `lablink` |
| MCP entry point: `agentlink-mcp` | `lablink-mcp` |
| Config directory: `~/.agentlink/instruments/` | `~/.lablink/devices/` |
| Log directory: `~/.agentlink/logs/` | `~/.lablink/logs/` |
| Env var: `AGENTLINK_CONFIG_DIR` | `LABLINK_CONFIG_DIR` |
| Env var: `AGENTLINK_LOG_DIR` | `LABLINK_LOG_DIR` |
| Env var: `AGENTLINK_VISA_BACKEND` | `LABLINK_VISA_BACKEND` |

The old `agentlink-mcp` and `agentlink` entry points are removed entirely (no
stderr shim) — MCP clients do not surface failed-server stderr to the user, so
a shim would be invisible. Update your MCP client config and any scripts to
the new names.

### Added

- **Auto-migration of configs.** On first `lablink` or `lablink-mcp`
  invocation, every `*.toml` and `*.md` in `~/.agentlink/instruments/` is
  copied into `~/.lablink/devices/`. Each migrated TOML that lacks a top-level
  `type` field is rewritten with `type = "visa"` prepended (legacy
  agentlink-visa configs had no `type` field). A `MIGRATED.txt` marker is
  written to the old directory to gate re-runs.

  Set `LABLINK_AUTO_MIGRATE=0` (or `false` / `no`) to disable.

  Per-file rules:
  - Never overwrites an existing destination file.
  - TOML files that fail to parse are copied as-is with a stderr warning.
  - One-line stderr summary on success; one stderr line per skipped or
    warned file.

### Unchanged

- The tool surface (`connect_instrument`, `disconnect_instrument`,
  `query_instrument`, `write_instrument`, `diagnose_connection`) and CLI
  command structure (`lablink connect`, `lablink list`, etc.) are byte-for-byte
  identical to the agentlink-visa shape — only string-level renames in this
  phase. The architectural CLI rewrite into per-driver subgroups
  (`lablink visa query ...`) lands in Phase 0c.

### Known follow-ups

- Phase 0b's behavioral-equivalence gate originally wanted a pre-rename
  baseline of `agentlink connect <local_instrument>` output. That baseline was
  never capturable (the `agentlink` entry point was removed in 0a) — it has
  since been **superseded by direct real-hardware validation of the new
  `lablink` path** (Phase 0b, Siglent SDS1104X-E). Resolved.
