# Changelog

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

- Phase 0b's behavioral-equivalence gate requires a pre-rename baseline of
  `agentlink connect <local_instrument>` output. This was deferred at
  developer request because the local Siglent scope was offline at the time
  of Phase 0a. See `docs/agent_docs/implementation_log.md` for the
  recommended capture strategy.
