"""LabLink python_shell bootstrap.

Runs INSIDE the user's interpreter as a subprocess. Do NOT import this from
within LabLink's own process. PythonShellDriver spawns it as:

    python -u bootstrap.py

and communicates over stdin/stdout using newline-delimited JSON.

Wire protocol (docs/ARCHITECTURE.md §12):
  Request  (LabLink → subprocess, stdin):
    {"id": "req-N", "op": "exec",  "code": "..."}
    {"id": "req-N", "op": "eval",  "expression": "..."}
    {"id": "req-N", "op": "shutdown"}

  Response (subprocess → LabLink, stdout):
    {"id": "req-N", "op": "exec", "stdout": "", "stderr": "",
     "result": null, "exception": null, "duration_ms": 12}

On startup, before reading any request, writes one ready frame:
    {"op": "ready", "python_version": "3.11.5", "interpreter": "/path/to/python"}

State (the namespace dict) persists across calls — that is the whole point.
"""

import contextlib
import io
import json
import sys
import time
import traceback

_MAX_OUTPUT_BYTES = 8 * 1024 * 1024  # 8 MB soft limit per call


def _main() -> None:
    # Save references to the real binary pipe streams before any redirects.
    # redirect_stdout() changes sys.stdout but not sys.stdout.buffer's underlying fd.
    _raw_out = sys.stdout.buffer
    _raw_in = sys.stdin.buffer

    def _write(frame: dict) -> None:
        line = json.dumps(frame, ensure_ascii=False) + "\n"
        _raw_out.write(line.encode("utf-8"))
        _raw_out.flush()

    namespace: dict = {}

    # Handshake — driver waits for this before marking connect successful.
    _write({
        "op": "ready",
        "python_version": sys.version.split()[0],
        "interpreter": sys.executable,
    })

    while True:
        raw = _raw_in.readline()
        if not raw:
            break  # stdin closed / EOF

        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            _write({"op": "error", "error": f"JSON decode error: {exc}"})
            continue

        req_id = req.get("id", "")
        op = req.get("op", "")

        if op == "shutdown":
            _write({
                "id": req_id, "op": "shutdown",
                "stdout": "", "stderr": "", "result": None, "exception": None, "duration_ms": 0,
            })
            break

        out_buf = io.StringIO()
        err_buf = io.StringIO()
        result_val = None
        exc_info = None
        t0 = time.monotonic()

        try:
            with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
                if op == "exec":
                    code = req.get("code", "")
                    exec(compile(code, "<python_shell>", "exec"), namespace)  # noqa: S102
                elif op == "eval":
                    expr = req.get("expression", "")
                    val = eval(compile(expr, "<python_shell>", "eval"), namespace)  # noqa: S307
                    result_val = repr(val)
        except Exception:
            _, exc_val, _ = sys.exc_info()
            exc_info = {
                "type": type(exc_val).__name__,
                "message": str(exc_val),
                "traceback": traceback.format_exc(),
            }

        duration_ms = int((time.monotonic() - t0) * 1000)
        captured_stdout = out_buf.getvalue()
        captured_stderr = err_buf.getvalue()

        # Enforce 8 MB soft limit on combined stdout + stderr.
        stdout_b = captured_stdout.encode("utf-8", errors="replace")
        stderr_b = captured_stderr.encode("utf-8", errors="replace")
        total_bytes = len(stdout_b) + len(stderr_b)
        truncated = total_bytes > _MAX_OUTPUT_BYTES
        truncated_bytes = 0

        if truncated:
            truncated_bytes = total_bytes - _MAX_OUTPUT_BYTES
            limit = _MAX_OUTPUT_BYTES
            if len(stdout_b) >= limit:
                stdout_b = stdout_b[:limit]
                stderr_b = b""
            else:
                remaining = limit - len(stdout_b)
                stderr_b = stderr_b[:remaining]
            captured_stdout = stdout_b.decode("utf-8", errors="replace")
            captured_stderr = stderr_b.decode("utf-8", errors="replace")

        frame: dict = {
            "id": req_id,
            "op": op,
            "stdout": captured_stdout,
            "stderr": captured_stderr,
            "result": result_val,
            "exception": exc_info,
            "duration_ms": duration_ms,
        }
        if truncated:
            frame["truncated"] = True
            frame["truncated_bytes"] = truncated_bytes

        _write(frame)


if __name__ == "__main__":
    _main()
