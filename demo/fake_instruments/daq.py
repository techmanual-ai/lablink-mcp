"""Simulated 8-channel data acquisition module."""

from collections import deque

import numpy as np
import pint

ureg = pint.UnitRegistry()


class DAQ:
    """Simulates an 8-channel analog input DAQ.

    Reads return pint.Quantity values (volts). Each channel has a configurable
    input range, a fixed simulated DC bias, and a small Gaussian noise floor.
    History is stored per-channel so statistical analysis across multiple reads
    is possible within a single session.

    Example:
        daq = DAQ("DAQ-8")
        v = daq.read_channel(0)          # pint.Quantity in volts
        print(v.to("millivolt"))
        all_ch = daq.read_all()          # dict[int, Quantity]
        hist = daq.read_history(0, n=50) # numpy array of last 50 reads
    """

    def __init__(self, model: str = "DAQ-8", n_channels: int = 8) -> None:
        self.model = model
        self.n_channels = n_channels
        self._ranges: dict[int, tuple[float, float]] = {
            ch: (-10.0, 10.0) for ch in range(n_channels)
        }
        # Simulated per-channel DC bias (varies by channel so reads aren't all identical).
        self._bias: dict[int, float] = {
            ch: round((ch - n_channels / 2) * 0.15, 4) for ch in range(n_channels)
        }
        self._noise_v: float = 0.0005  # 500 µV RMS noise floor
        self._history: dict[int, deque] = {
            ch: deque(maxlen=1000) for ch in range(n_channels)
        }

    # --- configuration ---

    def set_range(self, channel: int, low_v: float, high_v: float) -> None:
        """Set the input voltage range for a channel."""
        self._check(channel)
        if low_v >= high_v:
            raise ValueError(f"low_v ({low_v}) must be less than high_v ({high_v})")
        self._ranges[channel] = (low_v, high_v)

    def set_bias(self, channel: int, bias_v: float) -> None:
        """Override the simulated DC bias on a channel (for test scripting)."""
        self._check(channel)
        self._bias[channel] = float(bias_v)

    # --- readback ---

    def read_channel(self, channel: int) -> pint.Quantity:
        """Read the current voltage on one channel.

        Returns a pint.Quantity in volts. The value is clamped to the
        configured input range. Each call appends to the channel's history.
        """
        self._check(channel)
        lo, hi = self._ranges[channel]
        noise = float(np.random.normal(0.0, self._noise_v))
        raw = self._bias[channel] + noise
        clamped = max(lo, min(hi, raw))
        self._history[channel].append(clamped)
        return clamped * ureg.volt

    def read_all(self) -> dict[int, pint.Quantity]:
        """Read all channels and return a dict keyed by channel number."""
        return {ch: self.read_channel(ch) for ch in range(self.n_channels)}

    def read_history(self, channel: int, n: int = 10) -> np.ndarray:
        """Return the last n readings on a channel as a float64 numpy array."""
        self._check(channel)
        hist = list(self._history[channel])
        return np.array(hist[-n:], dtype=np.float64)

    def stats(self, channel: int) -> dict[str, float]:
        """Return mean, std, min, max of the full history for a channel."""
        self._check(channel)
        arr = np.array(list(self._history[channel]), dtype=np.float64)
        if arr.size == 0:
            return {"n": 0, "mean": float("nan"), "std": float("nan"),
                    "min": float("nan"), "max": float("nan")}
        return {
            "n": int(arr.size),
            "mean": float(arr.mean()),
            "std": float(arr.std()),
            "min": float(arr.min()),
            "max": float(arr.max()),
        }

    # --- private ---

    def _check(self, channel: int) -> None:
        if channel not in self._ranges:
            raise ValueError(
                f"Channel {channel} not available on {self.model}. "
                f"Valid channels: 0–{self.n_channels - 1}"
            )
