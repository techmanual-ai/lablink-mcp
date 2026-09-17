"""REST front-end for the simulated bench.

Serves a small JSON API over the same :class:`Bench` the SCPI servers use,
so a change written over SCPI is visible here immediately. Exercises the
LabLink ``rest`` driver (rest_get / rest_post) with no hardware.

Routes:
    GET  /api/v1/status                 bench summary
    GET  /api/v1/channels               all DAQ readings
    GET  /api/v1/channels/<n>           one DAQ reading
    GET  /api/v1/channels/<n>/stats     history statistics
    GET  /api/v1/generator/<n>          generator channel state
    POST /api/v1/generator/<n>          update generator channel

Built on http.server so the demo extra needs no dependencies.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lablink.demo.instruments import Bench, SimError

_CHANNEL_RE = re.compile(r"^/api/v1/channels/(\d+)(/stats)?$")
_GENERATOR_RE = re.compile(r"^/api/v1/generator/(\d+)$")


class _RestHandler(BaseHTTPRequestHandler):
    server_version = "LabLinkSim/0.2.0"

    @property
    def bench(self) -> Bench:
        return self.server.bench  # type: ignore[attr-defined]

    def log_message(self, *args) -> None:  # noqa: D102 - silence stderr access log
        pass

    def _send(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        try:
            if self.path == "/api/v1/status":
                return self._send(
                    {
                        "generator": {
                            "model": self.bench.gen.model,
                            "channels": self.bench.gen.get_status(),
                        },
                        "daq": {
                            "model": self.bench.daq.model,
                            "n_channels": self.bench.daq.n_channels,
                        },
                        "patches": [
                            {
                                "from": f"FG-100:CH{p.src_channel}",
                                "to": f"DAQ-8:CH{p.dst_channel}",
                                "attenuation_db": p.attenuation_db,
                            }
                            for p in self.bench.patches
                        ],
                    }
                )

            if self.path == "/api/v1/channels":
                readings = self.bench.read_all()
                return self._send(
                    {"unit": "V", "channels": {str(k): round(v, 6) for k, v in readings.items()}}
                )

            match = _CHANNEL_RE.match(self.path)
            if match:
                channel = int(match.group(1))
                if match.group(2):
                    return self._send({"channel": channel, **self.bench.daq.stats(channel)})
                return self._send(
                    {"channel": channel, "unit": "V", "value": round(self.bench.read(channel), 6)}
                )

            match = _GENERATOR_RE.match(self.path)
            if match:
                channel = int(match.group(1))
                return self._send({"channel": channel, **self.bench.gen.get_status(channel)})

            return self._send({"error": "not found", "path": self.path}, status=404)
        except SimError as exc:
            return self._send({"error": str(exc)}, status=400)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        match = _GENERATOR_RE.match(self.path)
        if not match:
            return self._send({"error": "not found", "path": self.path}, status=404)

        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as exc:
            return self._send({"error": f"invalid JSON: {exc}"}, status=400)

        channel = int(match.group(1))
        gen = self.bench.gen
        try:
            if "freq_hz" in payload:
                gen.set_frequency(channel, float(payload["freq_hz"]))
            if "amplitude_v" in payload:
                gen.set_amplitude(channel, float(payload["amplitude_v"]))
            if "waveform" in payload:
                gen.set_waveform(channel, str(payload["waveform"]))
            if "output" in payload:
                gen.enable_output(channel, bool(payload["output"]))
        except SimError as exc:
            return self._send({"error": str(exc)}, status=400)

        return self._send({"channel": channel, **gen.get_status(channel)})


class RestServer(ThreadingHTTPServer):
    """Threaded HTTP server wrapping the shared bench."""

    daemon_threads = True

    def __init__(self, host: str, port: int, bench: Bench) -> None:
        super().__init__((host, port), _RestHandler)
        self.bench = bench

    def serve_in_thread(self) -> threading.Thread:
        """Start serving on a daemon thread and return it."""
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread
