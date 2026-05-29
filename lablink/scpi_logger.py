"""SCPI transaction logger.

Logs all instrument I/O to JSONL files in the log directory.

Default log directory: ~/.lablink/logs/
Override:  set LABLINK_LOG_DIR to a different path.
Disable:   set LABLINK_LOG_DIR to an empty string.

One file per day: <log_dir>/YYYY-MM-DD.jsonl
Each line is a JSON object with a 'ts' timestamp and event fields.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_DEFAULT_LOG_DIR = Path.home() / ".lablink" / "logs"


def get_log_dir() -> Optional[Path]:
    """Return the active log directory, or None if logging is disabled.

    Logging is disabled only when LABLINK_LOG_DIR is explicitly set to an
    empty string. If the variable is unset, the default directory is used.
    """
    env_val = os.environ.get("LABLINK_LOG_DIR")
    if env_val is not None and env_val.strip() == "":
        return None
    return Path(env_val) if env_val else _DEFAULT_LOG_DIR


def log_event(**fields: Any) -> None:
    """Append a JSONL entry to today's log file.

    Silently no-ops if logging is disabled or if the log file cannot be
    written. Never raises — logging failures must not affect instrument control.

    A 'ts' field with the current UTC timestamp is prepended automatically.

    Args:
        **fields: Arbitrary key-value pairs to include in the log entry.
                  Typical fields: op, alias, command, response, success, error.
    """
    log_dir = get_log_dir()
    if log_dir is None:
        return
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = log_dir / f"{today}.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            **fields,
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
