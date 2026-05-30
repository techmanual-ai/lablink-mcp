# Contributing to LabLink

Thank you for considering a contribution. This document covers the essentials
for getting oriented, making changes, and submitting them.

---

## Getting started

```bash
git clone https://github.com/techmanual-ai/lablink-mcp
cd lablink-mcp
uv venv
uv pip install -e ".[dev]"
```

Run the tests to confirm your environment is clean:

```bash
pytest tests/
```

All tests mock hardware drivers — no real instruments required.

---

## Project structure

```text
lablink/
├── mcp_server.py       # FastMCP entrypoint; shared lifecycle tools
├── cli.py              # Click root; shared lifecycle commands
├── base.py             # Data models, config dataclasses, driver ABC
├── config.py           # TOML loader
├── session.py          # Session registry
├── event_logger.py     # JSONL event log
├── exceptions.py       # ConfigError, SessionError, DriverError
└── interfaces/
    ├── visa/
    ├── ssh/
    ├── rest/
    ├── serial/
    ├── python_shell/
    └── external_mcp/
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design.

---

## Adding a driver

1. Create `lablink/interfaces/<type>/` with `driver.py`, `config.py`, `__init__.py`.
2. Subclass `LabLinkDriver[YourConfig]`; implement `connect`, `disconnect`,
   `diagnose`, `register_tools`, `register_cli_commands`.
3. Subclass `DriverConfig` (`@dataclass(kw_only=True)`).
4. Register it — one line in each of `DRIVER_REGISTRY` and
   `DRIVER_CONFIG_REGISTRY` in `lablink/interfaces/__init__.py`.
5. Write `tests/interfaces/test_<type>.py` with full mock coverage.
6. Add `examples/configs/<type>_device.toml`.

No changes to `lablink/mcp_server.py` or `lablink/cli.py` are required.

---

## Coding standards

- Python 3.10+, PEP 8, strict type hints on all signatures.
- Google-style docstrings. MCP tool docstrings are load-bearing: they are
  surfaced to agents as the tool description.
- Lazy-import all third-party driver deps inside `connect()`. Missing deps must
  return a structured `{"success": false, "error": ..., "hint": ...}` dict —
  never raise across the MCP boundary.
- Every tool call logs via `event_logger.log_event()`. Logging must never raise.
- Tests use `unittest.mock` to mock driver libraries. No real connections in CI.
- `@dataclass(kw_only=True)` is mandatory on every config dataclass and result
  type (see [docs/ARCHITECTURE.md §5.1](docs/ARCHITECTURE.md)).

---

## Commit style

- Imperative mood: `Add REST driver`, `Fix VISA timeout reset`.
- One feature or fix per commit.
- Do not skip pre-commit hooks (`--no-verify`).

---

## Submitting a pull request

1. Fork the repo and create a branch from `main`.
2. Make your changes and add tests.
3. Run `pytest tests/` and confirm it passes.
4. Open a pull request with a clear description of what changed and why.

For significant changes or new drivers, open an issue first to discuss the
approach before investing time in implementation.

---

## License

By contributing, you agree that your contributions will be licensed under the
[MIT License](LICENSE).
