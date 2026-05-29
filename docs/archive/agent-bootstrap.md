# AgentLink-Visa: Bootstrap & Design Brief

This document is the founding context for the `techmanual-ai/agentlink-visa` repository.
A new agent should read this in full, then use it to scaffold the `docs/agent_docs/`
directory following the same protocol used in `techmanual-ai/techmanual.ai`.

---

## 1. What Is AgentLink-Visa?

AgentLink-Visa is a standalone MCP server that gives AI agents direct, structured
control over test and measurement equipment via PyVISA. It is the execution layer
that complements techmanual.ai's knowledge layer.

**The problem it solves:**
Today, an agent that uses techmanual.ai can look up instrument specs and SCPI commands
and generate Python code for a human to run. AgentLink-Visa removes the human from that
loop. The agent looks up the commands *and executes them*, reads the results, iterates,
troubleshoots, and records findings — acting as the lab technician itself.

**Relationship to techmanual.ai:**
- techmanual.ai = knowledge backbone (manuals, specs, SCPI command reference)
- AgentLink-Visa = execution backbone (connect, configure, query, measure)
- These are sibling tools. An agent session with both MCP plugins loaded gets the full
  loop: look up → command → execute → observe → iterate.
- AgentLink-Visa depends on techmanual.ai (via API key config); techmanual.ai has no
  dependency on AgentLink-Visa.

**AgentLink is a family, not a single tool:**
AgentLink-Visa is the first member. Future siblings might include:
- `agentlink-ssh` — SSH-based control (Linux instruments, embedded targets)
- `agentlink-grpc` — gRPC instrument interfaces
- `agentlink-rest` — REST-controlled lab equipment

Do not design for these siblings now. Keep scope tight. The shared abstraction (if any)
can be extracted into `agentlink-core` only after two or more siblings exist and the
common surface is obvious.

---

## 2. Design Decisions (Locked)

These decisions were made in the founding design session and should not be revisited
without explicit instruction from the lead developer.

### 2.1 Separate Repo
AgentLink-Visa lives at `techmanual-ai/agentlink-visa`, not inside the main
`techmanual.ai` repo. Reasons:
- Different deployment model: runs locally on the user's machine, not on a server.
- Different dependency footprint: PyVISA, VISA drivers, OS-level hardware access.
- Different distribution: installed by end users, not deployed by the maintainer.
- Enforces the architectural boundary: AgentLink uses techmanual.ai; it is not part of it.

### 2.2 MCP Server + CLI
The primary interface is an MCP server (FastMCP, stdio transport — same pattern as
`techmanual-ai/claude-plugin`). A minimal CLI is also included for development and
debugging use. Rationale: instrument config debugging requires a human-in-the-loop
testing path, and the CLI provides that without building a separate tool.

CLI commands (target scope):
- `agentlink connect <alias>` — open a VISA session and verify IDN
- `agentlink query <alias> "<command>"` — send a query and print the response
- `agentlink write <alias> "<command>"` — send a write command
- `agentlink list` — list known instrument aliases from the config directory

### 2.3 Instrument Config Registry
The hardest part of VISA control is knowing the resource string and per-instrument
quirks (termination characters, timeout, command syntax flavor). AgentLink-Visa uses
a local directory of per-instrument TOML config files, one per instrument alias.

Default config directory: `~/.agentlink/instruments/`
Override via env var: `AGENTLINK_CONFIG_DIR`

Each config file is named `<alias>.toml`. Example: `tek_mso44.toml`

Minimum required fields per config:
```toml
alias = "tek_mso44"
resource_string = "USB0::0x0699::0x0527::C012345::INSTR"
manufacturer = "Tektronix"
model_number = "MSO44"
timeout_ms = 5000
read_termination = "\n"
write_termination = "\n"
Optional fields:


techmanual_document_id = 142   # direct link to the manual in techmanual.ai
description = "4-channel mixed signal oscilloscope, bench 3"
The techmanual_document_id field is the bridge: when present, the MCP server can
surface the relevant manual directly to the agent without requiring a search query.

2.4 techmanual.ai Integration Pattern
Two integration patterns were considered:

Option A (auto-inject): On connect(), fetch relevant docs and inject into context.
Option B (on-demand): Agent has both MCP plugins and decides when to look things up.
Option B is the chosen pattern. Auto-injection risks context bloat and irrelevant
content. The agent should be able to ask "what's the SCPI command for trigger level on
this scope?" on demand. The techmanual_document_id field in the instrument config
makes targeted lookups trivial (get_page(doc_id, page) or search(q, document_id=X)).

2.5 MCP Tool Surface (Initial Scope)
Four tools for v0.1:

Tool	Description
connect(alias)	Open VISA session, verify with *IDN?, return instrument info
disconnect(alias)	Close VISA session
query(alias, command)	Write command + read response, return string
write(alias, command)	Write command, no response expected
Explicitly out of scope for v0.1:

Binary data transfers (waveform capture)
Multi-instrument session management
Async/parallel instrument control
VISA event handling
Instrument state save/restore
These can be added in later versions once the basic control loop is validated.

3. Tech Stack
Concern	Choice	Notes
Language	Python 3.10+	Matches techmanual.ai convention
MCP framework	FastMCP	Same as claude-plugin
VISA interface	pyvisa	With pyvisa-py as the pure-Python backend
Config format	TOML	tomllib (stdlib in 3.11+), tomli for 3.10
CLI	Click	Same as techmanual-client
Package manager	uv	Same as techmanual.ai
Testing	pytest + unittest.mock	No real hardware in CI
VISA backend note: pyvisa-py is the pure-Python backend that works without
National Instruments VISA installed. It should be the default. Users with NI-VISA
installed can override via their ~/.agentlink/visa.toml or env var. Document both
paths clearly in the README — this is the #1 setup friction point.

4. Repository Structure (Target)

agentlink-visa/
├── docs/
│   └── agent_docs/
│       ├── readme_agent.md         # Agent onboarding protocol (model on techmanual.ai)
│       ├── project_goal.md         # Vision, non-goals, roadmap
│       ├── agent_development.md    # Coding standards, dev guidelines
│       ├── current_status.md       # Current phase + recent history
│       └── system_architecture.md  # Component map, data flow
├── agentlink/
│   ├── __init__.py
│   ├── config.py                   # Config loader (TOML, env vars)
│   ├── session.py                  # VISA session lifecycle management
│   ├── tools.py                    # MCP tool implementations
│   └── exceptions.py               # Typed exceptions (ConnectionError, QueryError, etc.)
├── mcp_server.py                   # FastMCP entrypoint (stdio)
├── cli.py                          # Click CLI entrypoint
├── tests/
│   └── test_tools.py               # Unit tests (mocked pyvisa)
├── examples/
│   └── instruments/                # Example .toml config files for common instruments
├── pyproject.toml
├── requirements.txt
├── .env.example
├── agent_bootstrap.md              # This file
└── README.md
5. Founding Demo Context
The immediate motivation for building AgentLink-Visa is a demo comparing agent
performance on an oscilloscope measurement task with and without hardware control.

Demo scenario:

Input: a square wave connected to oscilloscope channels 1 and 2
Task: measure relevant signal parameters and report the relative time offset between channels
Comparison: agent-generated Python code (human runs it) vs. agent using AgentLink-Visa directly
The oscilloscope in use is TBD (hardware in transit). The SCPI command set is standard
enough that techmanual.ai documentation + the AgentLink tool surface should be sufficient
for the agent to complete the task without hardcoded instrument knowledge.

This demo should drive the v0.1 scope. Do not add features that the demo does not require.

6. Agent Onboarding Protocol
When a new agent is pointed at this repo, it should:

Read agent_bootstrap.md (this file) first.
Read all files in docs/agent_docs/ in the order specified in readme_agent.md.
Confirm the current phase from current_status.md and await instruction.
The docs/agent_docs/ directory should be modeled closely on the equivalent directory
in techmanual-ai/techmanual.ai. The key files to create first:

readme_agent.md — Onboarding protocol, document reading order, contribution rules, context exhaustion protocol. Model on techmanual.ai's version but scoped to this repo.
project_goal.md — Populate from Section 1 and Section 2 of this document. Include non-goals explicitly.
agent_development.md — Coding standards (Python 3.10+, PEP 8, type hints, Google docstrings, uv, pytest). VISA-specific dev notes (mock pyvisa in tests, never require hardware in CI). Same two-way update rule as techmanual.ai.
current_status.md — Start at "Phase 0: Scaffolding". Record this bootstrap as the first history entry.
system_architecture.md — Populate from Section 4 of this document once the scaffold is in place.
7. Non-Goals (Explicit)
No server component. AgentLink-Visa runs on the user's local machine. No cloud deployment, no Docker, no hosted endpoint.
No instrument simulation. Tests mock pyvisa; there is no instrument simulator. The demo requires real hardware.
No GUI. CLI only beyond the MCP interface.
No support for non-VISA interfaces in this repo. SSH, gRPC, REST belong in separate sibling repos when the time comes.
No waveform/binary data handling in v0.1. String queries only.
Not a general SCPI library. AgentLink sends raw strings. It does not parse, validate, or know anything about SCPI syntax. That knowledge lives in techmanual.ai.
8. Key Open Questions (For Lead Developer to Resolve)
These were not settled in the founding session and should be answered before or during
implementation:

Session persistence: Should VISA sessions be held open between MCP tool calls,
or opened/closed per call? Held-open is faster and stateful; per-call is simpler and
avoids leaked sessions. Recommendation: held-open with an explicit disconnect() tool,
but per-call as a fallback if session state causes issues.

Multi-instrument: Can the agent have multiple instruments connected simultaneously
(e.g., scope + signal generator)? The config registry supports this naturally; the
session manager needs to handle a dict of open sessions keyed by alias.

Error handling philosophy: On a VISA timeout or bad response, should the MCP tool
return an error string (agent handles it) or raise (MCP reports tool failure)? The
agent-handles approach gives the agent more ability to retry and troubleshoot. Recommendation:
return structured error dicts with success: false, error: "...", hint: "..." rather
than raising, so the agent can reason about the failure.

techmanual.ai API key config: Where does the user's TMAI_API_KEY live?
Options: same ~/.agentlink/ config dir, environment variable, or piggyback on
~/.claude/settings.json (already used by the claude-plugin). Recommendation:
environment variable (TMAI_API_KEY) — same as the claude-plugin, zero extra setup
for users who already have it configured.



---

That covers the full founding context. The new agent's first job should be to read this, scaffold the `docs/agent_docs/` directory, then come back and confirm `current_status.md` is written before touching any code.
