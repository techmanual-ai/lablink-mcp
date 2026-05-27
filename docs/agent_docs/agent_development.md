# Agent Development Guidelines

## 1. Coding Standards

### Python
- **Version:** Python 3.10+
- **Style:** PEP 8 strictly.
- **Type Hinting:** Strict type hints (`typing` module) for all function signatures.
- **Docstrings:** Google Style for all modules, classes, and functions.
- **Linters:** Compatible with `flake8` and `black` formatting.

### TOML Config
- Use `tomllib` (stdlib, Python 3.11+) or `tomli` (backport for 3.10) for config loading. Do not use third-party TOML libraries.
- Config loading lives in `agentlink/config.py`. Never scatter config reads across other modules.
- Always validate required fields (`alias`, `resource_string`, `manufacturer`, `model_number`, `timeout_ms`, `read_termination`, `write_termination`) at load time and raise a typed `ConfigError` with a clear message if any are missing.

### MCP (FastMCP)
- Follow the same FastMCP stdio pattern used in `techmanual-ai/claude-plugin`.
- Tool functions must have clear docstrings — FastMCP surfaces these to the agent as tool descriptions.
- Tool return values for error cases must be structured dicts (`{"success": false, "error": "...", "hint": "..."}`) rather than raising exceptions. See `project_goal.md` §2.7.

### CLI (Click)
- Follow the same Click patterns used in `techmanual-client`.
- Status/diagnostic output goes to stderr. Command output goes to stdout.
- CLI commands should be thin wrappers that call the same core functions used by the MCP tools.

## 2. Environment & Package Management

- **Package manager:** `uv`. Use `uv venv` to create the environment, `uv pip install -r requirements.txt` to sync.
- **Secrets:** Never hardcode. Use environment variables and `.env` files. `AGENTLINK_CONFIG_DIR` and `TMAI_API_KEY` are the two primary env vars.
- **No Docker.** AgentLink-Visa runs locally on the user's machine. Do not introduce Docker or container dependencies.

## 3. VISA & Hardware Guidelines

- **Default backend:** `pyvisa-py` (pure-Python, no NI-VISA required). This must be the default; document the NI-VISA override path clearly in the README.
- **Never require real hardware in CI.** All tests must mock `pyvisa`. Use `unittest.mock` to patch `pyvisa.ResourceManager` and resource objects.
- **Resource string validation:** Do not validate or parse resource strings beyond confirming the field is a non-empty string. Resource string syntax is hardware-specific; let PyVISA surface errors.
- **Timeout:** Always use the `timeout_ms` value from the instrument config. Never hardcode a timeout.
- **Session state:** Sessions are keyed by alias in a module-level dict in `session.py`. The `connect()` tool opens and registers the session; `disconnect()` closes and removes it. `query()` and `write()` look up the session by alias and return a structured error if no session is open.

## 4. Testing

- **Framework:** `pytest`.
- **Requirement:** Every new function in `agentlink/` must have unit tests.
- **Mocking:** Use `unittest.mock` to mock `pyvisa.ResourceManager` and instrument resource objects. Tests must never open a real VISA connection.
- **Test location:** `tests/test_tools.py` for MCP tool tests, add additional test files as modules grow.
- **No hardware-dependent tests in CI.** If a test requires real hardware, mark it `@pytest.mark.skip(reason="requires hardware")` and document the manual test procedure.

## 5. Interface-Specific Agent Context Pattern

Each AgentLink interface type (VISA, SSH, gRPC, REST, …) must include a
dedicated behavior section in its MCP server's `_INSTRUCTIONS`. This section
is surfaced to every agent session and should cover:

- The protocol's request-response model and any fire-and-forget semantics
- How to distinguish error causes that produce the same surface symptom
- Instrument/service-specific response conventions that an agent could misread
- Efficiency patterns (e.g., parallel queries, session reuse)
- Where to find the session log and how to disable it

The VISA/SCPI section in `mcp_server.py` is the canonical template. When
adding a new sibling (`agentlink-ssh`, etc.), mirror this section with the
analogous background for that interface's protocol.

## 7. Agent Behavior & Interaction

- **Ambiguity:** Always ask clarifying questions before implementation. Do not guess.
- **Scope discipline:** Do not add features, refactor, or introduce abstractions beyond what the current task requires. v0.1 scope is bounded by the founding demo (see `project_goal.md`).
- **Locked decisions:** Do not revisit §2 decisions in `project_goal.md` without explicit instruction from the lead developer.
- **Context documents:** Be concise. Avoid fluff to minimize context window usage.
- **Self-correction:** If corrected by the developer on a preference or rule, update this document to capture it for future agents.

## 8. Git & Version Control

- **Commit messages:** Imperative mood ("Add feature", not "Added feature").
- **Granularity:** Atomic commits — one feature or fix per commit.
