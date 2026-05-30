"""Event logger.

Appends one JSONL entry per tool call to a per-UTC-day file in the log
directory. The ``op`` field is any tool name (``connect``, ``visa_query``,
``ssh_exec``, ``disconnect``, ...).

Default log directory: ~/.lablink/logs/
Override:  set LABLINK_LOG_DIR to a different path.
Disable:   set LABLINK_LOG_DIR to an empty string.

One file per day: <log_dir>/YYYY-MM-DD.jsonl

Canonical field contract (docs/ARCHITECTURE.md §8.4):
  - ts       — auto-populated UTC ISO-8601 timestamp.
  - op       — caller-required; the tool name as the agent sees it.
  - alias    — caller-required; the device alias (None for the no-alias
               diagnose system audit).
  - success  — caller-required; True or False.
  - error    — recommended on failure (omitted from the entry when None).
  - duration_ms — recommended where measured (omitted when None).
Everything else is free-form per-tool extras (e.g. command/response for VISA,
exit_code/stderr for SSH). The four canonical fields are the only ones every
log consumer can rely on.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DEFAULT_LOG_DIR = Path.home() / ".lablink" / "logs"


def get_log_dir() -> Optional[Path]:
    """Return the active log directory, or None if logging is disabled.

    Resolved from os.environ on every call (not cached at import) so test
    fixtures can toggle LABLINK_LOG_DIR. Logging is disabled only when
    LABLINK_LOG_DIR is explicitly an empty string; unset uses the default.
    """
    env_val = os.environ.get("LABLINK_LOG_DIR")
    if env_val is not None and env_val.strip() == "":
        return None
    return Path(env_val) if env_val else _DEFAULT_LOG_DIR


def log_event(
    *,
    op: str,
    alias: Optional[str],
    success: bool,
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
    **extra: Any,
) -> None:
    """Append one JSONL entry for a tool call.

    The three canonical fields (op, alias, success) are required keyword
    arguments so every call site honors the contract; ``ts`` is added
    automatically. ``error``/``duration_ms`` are recorded only when provided.
    Any additional per-tool fields are passed as keyword extras.

    Silently no-ops if logging is disabled and never raises on filesystem or
    serialization errors — logging must never affect tool behavior.
    """
    log_dir = get_log_dir()
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.jsonl"
        entry: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "op": op,
            "alias": alias,
            "success": success,
        }
        if error is not None:
            entry["error"] = error
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        entry.update(extra)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
