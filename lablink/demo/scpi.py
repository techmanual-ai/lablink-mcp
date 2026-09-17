"""SCPI-over-TCP front-end for the simulated bench.

Serves a real SCPI command set on a socket, so LabLink reaches it through
the genuine VISA driver with no mocking and no special-case code path:

    resource_string = "TCPIP0::127.0.0.1::5025::SOCKET"

pyvisa-py opens that as a raw socket, which is exactly what this server
speaks. From the driver's point of view the simulator is indistinguishable
from a bench instrument with a LAN port.
"""

from __future__ import annotations

import re
import socketserver
import threading
from typing import Callable, Optional

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from lablink.demo.instruments import Bench, SimError

try:
    __version__ = _pkg_version("lablink-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0.dev0"

# SCPI error queue codes used here (IEEE 488.2 / SCPI-99).
_ERR_NONE = (0, "No error")
_ERR_UNDEFINED_HEADER = (-113, "Undefined header")
_ERR_DATA_OUT_OF_RANGE = (-222, "Data out of range")
_ERR_ILLEGAL_PARAM = (-224, "Illegal parameter value")

_CHANNEL_LIST = re.compile(r"\(@\s*(\d+)\s*\)")


def _parse_channel_list(arg: str) -> int:
    """Parse a single-channel SCPI channel list such as ``(@3)``."""
    match = _CHANNEL_LIST.search(arg)
    if not match:
        raise SimError(f"Malformed channel list: {arg!r}")
    return int(match.group(1))


def _on_off(token: str) -> bool:
    t = token.strip().upper()
    if t in ("ON", "1", "TRUE"):
        return True
    if t in ("OFF", "0", "FALSE"):
        return False
    raise SimError(f"Expected ON|OFF|1|0, got {token!r}")


class ScpiInstrument:
    """SCPI command dispatch for one simulated instrument.

    Both simulated instruments share a single :class:`Bench`, so a setting
    written to the generator is immediately visible in the DAQ's readings.

    Args:
        bench: The shared bench state.
        role: Either ``"gen"`` or ``"daq"`` — selects the command subset and
            the identity reported by ``*IDN?``.
        serial_number: Reported as the third ``*IDN?`` field.
    """

    def __init__(self, bench: Bench, role: str, serial_number: str = "SIM0001") -> None:
        if role not in ("gen", "daq"):
            raise ValueError(f"role must be 'gen' or 'daq', got {role!r}")
        self.bench = bench
        self.role = role
        self.serial_number = serial_number
        self._errors: list[tuple[int, str]] = []

    @property
    def model(self) -> str:
        return self.bench.gen.model if self.role == "gen" else self.bench.daq.model

    def _push_error(self, code_msg: tuple[int, str], detail: str = "") -> None:
        code, msg = code_msg
        self._errors.append((code, f"{msg}{'; ' + detail if detail else ''}"))

    def _pop_error(self) -> str:
        code, msg = self._errors.pop(0) if self._errors else _ERR_NONE
        return f'{code},"{msg}"'

    def handle(self, line: str) -> Optional[str]:
        """Execute one SCPI line.

        Returns:
            The response string for a query, or None for a command. Errors are
            pushed onto the error queue and surface via ``SYST:ERR?``, which is
            how a real instrument behaves.
        """
        cmd = line.strip()
        if not cmd:
            return None
        try:
            return self._dispatch(cmd)
        except SimError as exc:
            self._push_error(_ERR_DATA_OUT_OF_RANGE, str(exc))
            return None
        except (ValueError, IndexError) as exc:
            self._push_error(_ERR_ILLEGAL_PARAM, str(exc))
            return None

    def _dispatch(self, cmd: str) -> Optional[str]:
        head, _, arg = cmd.partition(" ")
        head_u = head.upper()
        arg = arg.strip()

        # --- IEEE 488.2 common commands (both roles) ---
        if head_u == "*IDN?":
            return f"LabLink,{self.model},{self.serial_number},{__version__}"
        if head_u == "*RST":
            self.bench.reset()
            self._errors.clear()
            return None
        if head_u == "*CLS":
            self._errors.clear()
            return None
        if head_u == "*OPC?":
            return "1"
        if head_u in ("SYST:ERR?", "SYSTEM:ERROR?"):
            return self._pop_error()

        handler: Optional[Callable[[str, str], Optional[str]]] = (
            self._gen_command if self.role == "gen" else self._daq_command
        )
        result = handler(head_u, arg)
        if result is NotImplemented:
            self._push_error(_ERR_UNDEFINED_HEADER, head)
            return None
        return result

    # --- generator subset ---

    def _gen_command(self, head: str, arg: str):
        gen = self.bench.gen
        source = re.match(r"^SOUR(?:CE)?(\d+):(FREQ|VOLT|FUNC)(?:UENCY|AGE|TION)?(\?)?$", head)
        if source:
            ch, param, is_query = int(source.group(1)), source.group(2), bool(source.group(3))
            status = gen.get_status(ch)
            if param == "FREQ":
                if is_query:
                    return f"{status['freq_hz']:.6E}"
                gen.set_frequency(ch, float(arg))
                return None
            if param == "VOLT":
                if is_query:
                    return f"{status['amplitude_v']:.6E}"
                gen.set_amplitude(ch, float(arg))
                return None
            if is_query:
                return status["waveform"].upper()[:4]
            gen.set_waveform(ch, arg)
            return None

        output = re.match(r"^OUTP(?:UT)?(\d+)(\?)?$", head)
        if output:
            ch, is_query = int(output.group(1)), bool(output.group(2))
            if is_query:
                return "1" if gen.get_status(ch)["output"] else "0"
            gen.enable_output(ch, _on_off(arg))
            return None

        return NotImplemented

    # --- DAQ subset ---

    def _daq_command(self, head: str, arg: str):
        daq = self.bench.daq

        if head in ("MEAS:VOLT:DC?", "MEAS:VOLT?", "MEASURE:VOLTAGE:DC?"):
            return f"{self.bench.read(_parse_channel_list(arg)):.6E}"

        if head == "READ?":
            return ",".join(f"{v:.6E}" for v in self.bench.read_all().values())

        if head in ("CONF:VOLT:RANG", "CONFIGURE:VOLTAGE:RANGE"):
            low, high, ch_list = arg.split(",", 2)
            daq.set_range(_parse_channel_list(ch_list), float(low), float(high))
            return None

        if head in ("CONF:VOLT:RANG?", "CONFIGURE:VOLTAGE:RANGE?"):
            low, high = daq.get_range(_parse_channel_list(arg))
            return f"{low:.6E},{high:.6E}"

        if head in ("CALC:AVER:AVER?", "CALC:AVER?"):
            mean = daq.stats(_parse_channel_list(arg))["mean"]
            return f"{mean:.6E}" if mean is not None else "9.91E+37"  # SCPI NaN

        if head in ("SYST:CHAN?", "SYSTEM:CHANNELS?"):
            return str(daq.n_channels)

        return NotImplemented


class _ScpiHandler(socketserver.StreamRequestHandler):
    """One client connection. Reads newline-terminated commands until EOF."""

    def handle(self) -> None:
        instrument: ScpiInstrument = self.server.instrument  # type: ignore[attr-defined]
        for raw in self.rfile:
            try:
                line = raw.decode("ascii", errors="replace").strip()
            except Exception:
                continue
            if not line:
                continue
            response = instrument.handle(line)
            if response is not None:
                self.wfile.write(f"{response}\n".encode("ascii"))
                self.wfile.flush()


class ScpiServer(socketserver.ThreadingTCPServer):
    """Threaded SCPI socket server wrapping one simulated instrument."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, instrument: ScpiInstrument) -> None:
        super().__init__((host, port), _ScpiHandler)
        self.instrument = instrument

    def serve_in_thread(self) -> threading.Thread:
        """Start serving on a daemon thread and return it."""
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        return thread
