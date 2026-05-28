# Project Goal

Created by the lead developer on 2026-05-26.

## What Is AgentLink-Visa?

AgentLink-Visa is a standalone MCP server that gives AI agents direct, structured control over test and measurement equipment via PyVISA. It is the execution layer that complements techmanual.ai's knowledge layer.

**The problem it solves:** An agent using techmanual.ai can look up instrument specs and SCPI commands and generate Python code — but a human must run that code. AgentLink-Visa removes the human from that loop. The agent looks up commands *and executes them*, reads results, iterates, troubleshoots, and records findings — acting as the lab technician itself.

**Relationship to techmanual.ai:**
- techmanual.ai = knowledge backbone (manuals, specs, SCPI command reference)
- AgentLink-Visa = execution backbone (connect, configure, query, measure)
- An agent session with both MCP plugins loaded gets the full loop: look up → command → execute → observe → iterate.
- AgentLink-Visa depends on techmanual.ai (via API key config). techmanual.ai has no dependency on AgentLink-Visa.

**AgentLink is a family, not a single tool.** AgentLink-Visa is the first member. Future siblings may include `agentlink-ssh`, `agentlink-grpc`, `agentlink-rest`. Do not design for these now. A shared `agentlink-core` abstraction can be extracted only after two or more siblings exist and the common surface is obvious.

---

## Project Ethos

- Minimal friction for the user: instrument config should be the only setup required beyond installation.
- The agent should be able to troubleshoot hardware issues without human guidance when hardware documentation is available via techmanual.ai.
- v0.1 scope is driven by the founding demo. Do not add features the demo does not require.

---

## Design Decisions (Locked)

These decisions were made in the founding design session and must not be revisited without explicit instruction from the lead developer.

### 2.1 Separate Repo
AgentLink-Visa lives at `techmanual-ai/agentlink-visa`, not inside the main techmanual.ai repo. Rationale: different deployment model (user's local machine, not a server), different dependency footprint (PyVISA, OS-level hardware access), different distribution (end-user install). This also enforces the architectural boundary.

### 2.2 MCP Server + CLI
Primary interface is an MCP server (FastMCP, stdio transport). A minimal CLI is also included for development and debugging. CLI target scope:
- `agentlink connect <alias>` — open a VISA session and verify IDN
- `agentlink query <alias> "<command>"` — send a query and print the response
- `agentlink write <alias> "<command>"` — send a write command
- `agentlink list` — list known instrument aliases from the config directory

### 2.3 Instrument Config Registry
A local directory of per-instrument TOML config files, one per alias. Default: `~/.agentlink/instruments/`. Override: `AGENTLINK_CONFIG_DIR` env var. Each file is named `<alias>.toml`.

Minimum required fields:
```toml
alias = "tek_mso44"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms = 5000
read_termination = "\n"
write_termination = "\n"
```

Optional fields:
```toml
techmanual_document_ids = [1291, 1323]   # [user_manual_id, programming_guide_id]
description = "4-channel mixed signal oscilloscope, bench 3"
```

The `techmanual_document_ids` list is the bridge: when present, the MCP server surfaces relevant manuals to the agent on `connect()` without requiring a search query. Instruments typically need two documents — a user manual for measurement concepts and a programming guide for SCPI syntax. The legacy single-int field `techmanual_document_id` is still accepted and auto-converted to a one-element list.

Alias naming convention: `<manufacturer>_<model>`, lowercase with underscores (e.g. `siglent_sds1104xe`, `tektronix_mso44`).

### 2.4 techmanual.ai Integration Pattern
**On-demand (Option B), not auto-inject (Option A).** Auto-injection risks context bloat and irrelevant content. The agent decides when to look things up. The `techmanual_document_id` field makes targeted lookups trivial.

### 2.5 MCP Tool Surface (v0.1 Scope)
Four tools:

| Tool | Description |
|------|-------------|
| `connect(alias)` | Open VISA session, verify with `*IDN?`, return instrument info |
| `disconnect(alias)` | Close VISA session |
| `query(alias, command)` | Write command + read response, return string |
| `write(alias, command)` | Write command, no response expected |

Explicitly out of scope for v0.1: binary data transfers, multi-instrument session management, async/parallel control, VISA event handling, instrument state save/restore.

### 2.6 Session Persistence
Sessions are held open between MCP tool calls (not opened/closed per call). The explicit `connect()`/`disconnect()` pair encodes this model. Held-open is faster, stateful, and consistent with the tool surface design.

### 2.7 Error Handling
On VISA timeout or bad response, MCP tools return a structured error dict rather than raising. This gives the agent the ability to reason about and retry failures:
```json
{"success": false, "error": "VISA timeout", "hint": "Check that the instrument is powered on and the resource string is correct."}
```

### 2.8 TMAI_API_KEY Configuration
The techmanual.ai API key is read from the `TMAI_API_KEY` environment variable — same as the claude-plugin, zero extra setup for users who already have it configured.

---

## Founding Demo Context

The immediate motivation for v0.1 is a demo comparing agent performance on an oscilloscope measurement task with and without hardware control.

**Demo scenario:**
- Input: a square wave connected to oscilloscope channels 1 and 2
- Task: measure relevant signal parameters and report the relative time offset between channels
- Comparison: agent-generated Python code (human runs it) vs. agent using AgentLink-Visa directly

The oscilloscope model is TBD (hardware in transit). The SCPI command set is standard enough that techmanual.ai docs + the AgentLink tool surface should be sufficient for the agent to complete the task without hardcoded instrument knowledge.

**v0.1 scope is bounded by what this demo requires.** Do not add features beyond it.

---

## Non-Goals (Explicit)

- **No server component.** Runs on the user's local machine only. No cloud deployment, no Docker, no hosted endpoint.
- **No instrument simulation.** Tests mock pyvisa. The demo requires real hardware.
- **No GUI.** CLI only beyond the MCP interface.
- **No non-VISA interfaces in this repo.** SSH, gRPC, REST belong in separate sibling repos.
- **No waveform/binary data in v0.1.** String queries only.
- **Not a general SCPI library.** AgentLink sends raw strings and does not parse, validate, or interpret SCPI syntax. That knowledge lives in techmanual.ai.
- **No `agentlink-core` abstraction yet.** Extract only after two or more siblings exist.
