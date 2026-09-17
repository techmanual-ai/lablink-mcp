"""Simulated bench instruments.

Pure simulation — no I/O, no protocol, no LabLink deps. The protocol
front-ends (scpi.py, rest.py, serial_port.py) wrap these objects; the
python_shell driver imports them directly.

Two instruments and the patch cable between them:

    WaveformGen "FG-100"  CH1 ──coax──> DAQ "DAQ-8"  CH0

The patch is what makes the topology demo real: when the agent sets FG-100
CH1 to a 1 kHz 2 V sine, DAQ-8 CH0 actually reads that waveform back,
because Bench.read() routes the source signal through the patch list.
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

WAVEFORMS = ("sine", "square", "triangle", "sawtooth")

# Instrument noise floor, volts RMS. Small but non-zero so that repeated
# reads of an idle channel look like a real instrument rather than 0.000000.
_NOISE_V = 0.0012


class SimError(Exception):
    """Raised on an invalid parameter. Front-ends map this to a protocol error."""


# ---------------------------------------------------------------------------
# Waveform generator
# ---------------------------------------------------------------------------


@dataclass
class _GenChannel:
    freq_hz: float = 1000.0
    amplitude_v: float = 1.0
    waveform: str = "sine"
    output: bool = False


class WaveformGen:
    """Simulated two-channel function generator.

    Args:
        model: Model name. Must be a key of ``MODELS``.
    """

    MODELS = {
        "FG-100": {"channels": 2, "max_freq_hz": 20_000_000.0, "max_amp_v": 10.0},
        "FG-20": {"channels": 1, "max_freq_hz": 2_000_000.0, "max_amp_v": 5.0},
    }

    def __init__(self, model: str = "FG-100") -> None:
        if model not in self.MODELS:
            raise SimError(f"Unknown model {model}. Available: {sorted(self.MODELS)}")
        self.model = model
        spec = self.MODELS[model]
        self.max_freq_hz: float = spec["max_freq_hz"]
        self.max_amp_v: float = spec["max_amp_v"]
        self.channels: dict[int, _GenChannel] = {
            ch: _GenChannel() for ch in range(1, int(spec["channels"]) + 1)
        }
        self._t0 = time.monotonic()

    def _check(self, channel: int) -> _GenChannel:
        if channel not in self.channels:
            raise SimError(
                f"Channel {channel} not available on {self.model}. "
                f"Valid channels: {sorted(self.channels)}"
            )
        return self.channels[channel]

    def set_frequency(self, channel: int, freq_hz: float) -> None:
        """Set the output frequency on a channel, in Hz."""
        ch = self._check(channel)
        if not 0 < freq_hz <= self.max_freq_hz:
            raise SimError(f"freq_hz={freq_hz} out of range (0, {self.max_freq_hz}]")
        ch.freq_hz = float(freq_hz)

    def set_amplitude(self, channel: int, amplitude_v: float) -> None:
        """Set the peak amplitude on a channel, in volts."""
        ch = self._check(channel)
        if not 0 < amplitude_v <= self.max_amp_v:
            raise SimError(f"amplitude_v={amplitude_v} out of range (0, {self.max_amp_v}]")
        ch.amplitude_v = float(amplitude_v)

    def set_waveform(self, channel: int, waveform: str) -> None:
        """Set the waveform shape. One of sine, square, triangle, sawtooth."""
        ch = self._check(channel)
        shape = waveform.strip().lower()
        if shape not in WAVEFORMS:
            raise SimError(f"Unknown waveform {waveform}. Valid: {list(WAVEFORMS)}")
        ch.waveform = shape

    def enable_output(self, channel: int, enabled: bool) -> None:
        """Enable or disable the output on a channel."""
        self._check(channel).output = bool(enabled)

    def get_status(self, channel: Optional[int] = None) -> dict:
        """Return the configuration and output state for one or all channels."""
        if channel is not None:
            ch = self._check(channel)
            return {
                "freq_hz": ch.freq_hz,
                "amplitude_v": ch.amplitude_v,
                "waveform": ch.waveform,
                "output": ch.output,
            }
        return {n: self.get_status(n) for n in sorted(self.channels)}

    def value_at(self, channel: int, t: float) -> float:
        """Instantaneous output voltage on a channel at time ``t`` seconds.

        Returns 0.0 when the output is disabled. This is the function the
        patch cable samples, so it is what a downstream DAQ actually sees.
        """
        ch = self._check(channel)
        if not ch.output:
            return 0.0
        phase = (ch.freq_hz * t) % 1.0
        a = ch.amplitude_v
        if ch.waveform == "sine":
            return a * math.sin(2.0 * math.pi * phase)
        if ch.waveform == "square":
            return a if phase < 0.5 else -a
        if ch.waveform == "triangle":
            return a * (4.0 * phase - 1.0) if phase < 0.5 else a * (3.0 - 4.0 * phase)
        # sawtooth
        return a * (2.0 * phase - 1.0)

    def sample(self, channel: int, n_points: int, sample_rate_hz: float) -> list[float]:
        """Generate ``n_points`` samples of the configured waveform.

        Values are clipped to +/- the configured amplitude, matching the
        behavior of a real generator driving into its rated load.
        """
        if n_points <= 0:
            raise SimError(f"n_points={n_points} must be positive")
        if sample_rate_hz <= 0:
            raise SimError(f"sample_rate_hz={sample_rate_hz} must be positive")
        ch = self._check(channel)
        dt = 1.0 / sample_rate_hz
        return [
            max(-ch.amplitude_v, min(ch.amplitude_v, self.value_at(channel, i * dt)))
            for i in range(int(n_points))
        ]

    def elapsed(self) -> float:
        """Seconds since this generator was constructed."""
        return time.monotonic() - self._t0


# ---------------------------------------------------------------------------
# Data acquisition module
# ---------------------------------------------------------------------------


@dataclass
class _DaqChannel:
    low_v: float = -10.0
    high_v: float = 10.0
    bias_v: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=4096))


class DAQ:
    """Simulated multi-channel data acquisition module.

    Args:
        model: Model name, reported in ``*IDN?``.
        n_channels: Number of input channels.
    """

    def __init__(self, model: str = "DAQ-8", n_channels: int = 8) -> None:
        self.model = model
        self.n_channels = int(n_channels)
        self.channels: dict[int, _DaqChannel] = {
            ch: _DaqChannel() for ch in range(self.n_channels)
        }

    def _check(self, channel: int) -> _DaqChannel:
        if channel not in self.channels:
            raise SimError(
                f"Channel {channel} not available on {self.model}. "
                f"Valid channels: 0-{self.n_channels - 1}"
            )
        return self.channels[channel]

    def set_range(self, channel: int, low_v: float, high_v: float) -> None:
        """Set the input voltage range for a channel."""
        ch = self._check(channel)
        if low_v >= high_v:
            raise SimError(f"low_v ({low_v}) must be less than high_v ({high_v})")
        ch.low_v, ch.high_v = float(low_v), float(high_v)

    def set_bias(self, channel: int, bias_v: float) -> None:
        """Override the simulated DC bias on a channel (for test scripting)."""
        self._check(channel).bias_v = float(bias_v)

    def get_range(self, channel: int) -> tuple[float, float]:
        """Return the configured (low_v, high_v) input range for a channel."""
        ch = self._check(channel)
        return ch.low_v, ch.high_v

    def measure(self, channel: int, driven_v: float = 0.0) -> float:
        """Sample one channel and append the reading to its history.

        Args:
            channel: Channel index.
            driven_v: Voltage applied by an upstream source through a patch
                cable. Supplied by :class:`Bench`; zero for a floating input.

        Returns:
            The reading in volts, clamped to the configured input range.
        """
        ch = self._check(channel)
        raw = driven_v + ch.bias_v + random.gauss(0.0, _NOISE_V)
        value = max(ch.low_v, min(ch.high_v, raw))
        ch.history.append(value)
        return value

    def read_history(self, channel: int, n: int = 100) -> list[float]:
        """Return the last ``n`` readings on a channel."""
        ch = self._check(channel)
        return list(ch.history)[-int(n):]

    def stats(self, channel: int) -> dict:
        """Return count, mean, min, max and RMS over a channel's history."""
        vals = list(self._check(channel).history)
        if not vals:
            return {"n": 0, "mean": None, "min": None, "max": None, "rms": None}
        n = len(vals)
        mean = sum(vals) / n
        return {
            "n": n,
            "mean": mean,
            "min": min(vals),
            "max": max(vals),
            "rms": math.sqrt(sum(v * v for v in vals) / n),
        }


# ---------------------------------------------------------------------------
# The bench: instruments plus the cables between them
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Patch:
    """One simulated cable from a generator channel to a DAQ channel."""

    src_channel: int
    dst_channel: int
    attenuation_db: float = 0.0

    @property
    def gain(self) -> float:
        """Linear voltage gain implied by ``attenuation_db``."""
        return 10.0 ** (-self.attenuation_db / 20.0)


class Bench:
    """A simulated bench: one generator, one DAQ, and the patch between them.

    This is the object every protocol front-end shares, so a change made
    over SCPI is visible over REST and serial in the same instant — which is
    the point of the multi-protocol demo.
    """

    def __init__(self, seed: Optional[int] = None) -> None:
        if seed is not None:
            random.seed(seed)
        self.gen = WaveformGen("FG-100")
        self.daq = DAQ("DAQ-8", 8)
        # FG-100 CH1 ──coax, 0 dB──> DAQ-8 CH0. Mirrors examples/topology_sim.toml.
        self.patches: list[Patch] = [Patch(src_channel=1, dst_channel=0)]

    def driven_voltage(self, daq_channel: int) -> float:
        """Voltage arriving at a DAQ channel through the patch list.

        Returns 0.0 for an unpatched (floating) channel.
        """
        total = 0.0
        t = self.gen.elapsed()
        for patch in self.patches:
            if patch.dst_channel == daq_channel:
                total += self.gen.value_at(patch.src_channel, t) * patch.gain
        return total

    def read(self, daq_channel: int) -> float:
        """Measure a DAQ channel, including anything patched into it."""
        return self.daq.measure(daq_channel, driven_v=self.driven_voltage(daq_channel))

    def read_all(self) -> dict[int, float]:
        """Measure every DAQ channel."""
        return {ch: self.read(ch) for ch in sorted(self.daq.channels)}

    def reset(self) -> None:
        """Return both instruments to their power-on state."""
        self.gen = WaveformGen(self.gen.model)
        self.daq = DAQ(self.daq.model, self.daq.n_channels)
