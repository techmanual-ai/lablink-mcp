"""Serial front-end for the simulated bench.

Opens a pseudo-terminal (pty) pair and speaks the same SCPI command set as
scpi.py over it. The slave device path (``/dev/ttysNNN``) can be dropped
straight into a LabLink serial config, which exercises the real ``serial``
driver through pyserial with no hardware attached.

POSIX only — pty is not available on Windows. The bench launcher skips this
front-end rather than failing when the platform does not support it.
"""

from __future__ import annotations

import os
import select
import threading
from typing import Optional

from lablink.demo.instruments import Bench
from lablink.demo.scpi import ScpiInstrument

try:
    import pty as _pty

    PTY_AVAILABLE = True
except ImportError:  # pragma: no cover - Windows
    _pty = None  # type: ignore[assignment]
    PTY_AVAILABLE = False


class SerialServer:
    """Serves the SCPI command set over a pseudo-terminal.

    Args:
        bench: The shared bench state.
        role: ``"gen"`` or ``"daq"`` — which command subset to expose.

    Attributes:
        device_path: Slave pty path to put in a LabLink serial config. Only
            valid after :meth:`start`.
    """

    def __init__(self, bench: Bench, role: str = "daq") -> None:
        if not PTY_AVAILABLE:
            raise RuntimeError("pty is unavailable on this platform (Windows)")
        self.instrument = ScpiInstrument(bench, role=role, serial_number="SIMSER01")
        self._master_fd: Optional[int] = None
        self._slave_fd: Optional[int] = None
        self.device_path: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> str:
        """Open the pty pair and begin serving. Returns the slave device path."""
        self._master_fd, slave_fd = _pty.openpty()
        self.device_path = os.ttyname(slave_fd)
        # Keep the slave open so the pty survives between client connections.
        self._slave_fd = slave_fd
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self.device_path

    def _serve(self) -> None:
        buffer = b""
        while not self._stop.is_set():
            # Poll with a timeout rather than blocking in os.read(). Closing the
            # master fd out from under a blocked read wedges the process in an
            # uninterruptible wait on macOS — not even SIGKILL reaps it — so the
            # reader must be able to notice _stop and return on its own.
            try:
                ready, _, _ = select.select([self._master_fd], [], [], 0.2)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                chunk = os.read(self._master_fd, 1024)
            except OSError:
                return
            if not chunk:
                return
            buffer += chunk
            while b"\n" in buffer:
                raw, _, buffer = buffer.partition(b"\n")
                line = raw.decode("ascii", errors="replace").strip()
                if not line:
                    continue
                response = self.instrument.handle(line)
                if response is not None:
                    try:
                        os.write(self._master_fd, f"{response}\n".encode("ascii"))
                    except OSError:
                        return

    def stop(self) -> None:
        """Stop serving and close the pty pair.

        Joins the reader before closing anything: the fds must outlive the
        thread that reads them (see _serve).
        """
        self._stop.set()
        thread = getattr(self, "_thread", None)
        if thread is not None:
            thread.join(timeout=2.0)
        for fd in (self._master_fd, getattr(self, "_slave_fd", None)):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
