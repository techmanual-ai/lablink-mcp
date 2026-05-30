"""Simulated two-channel waveform generator."""

import numpy as np


class WaveformGen:
    """Simulates a two-channel function/arbitrary waveform generator.

    Mimics the stateful API common in vendor SDKs: you configure the
    instrument across multiple method calls, then sample the output.
    State persists on the object for the lifetime of the python_shell session.

    Example:
        gen = WaveformGen("FG-100")
        gen.set_frequency(1, 1000)   # 1 kHz on CH1
        gen.set_waveform(1, "square")
        gen.enable_output(1)
        samples = gen.sample(1, n_points=512)
    """

    MODELS: dict[str, dict] = {
        "FG-100": {"max_freq_hz": 1_000_000, "channels": 2, "max_amp_v": 10.0},
        "FG-500": {"max_freq_hz": 50_000_000, "channels": 2, "max_amp_v": 20.0},
    }

    def __init__(self, model: str = "FG-100") -> None:
        if model not in self.MODELS:
            raise ValueError(f"Unknown model {model!r}. Available: {sorted(self.MODELS)}")
        self.model = model
        self._spec = self.MODELS[model]
        self._ch: dict[int, dict] = {
            ch: {"freq_hz": 1000.0, "amplitude_v": 1.0, "output": False, "waveform": "sine"}
            for ch in range(1, self._spec["channels"] + 1)
        }

    # --- configuration ---

    def set_frequency(self, channel: int, freq_hz: float) -> None:
        """Set the output frequency on channel (Hz)."""
        self._check(channel)
        max_f = self._spec["max_freq_hz"]
        if not (0 < freq_hz <= max_f):
            raise ValueError(f"freq_hz={freq_hz} out of range (0, {max_f}]")
        self._ch[channel]["freq_hz"] = float(freq_hz)

    def set_amplitude(self, channel: int, amplitude_v: float) -> None:
        """Set the peak amplitude on channel (volts)."""
        self._check(channel)
        max_a = self._spec["max_amp_v"]
        if not (0 < amplitude_v <= max_a):
            raise ValueError(f"amplitude_v={amplitude_v} out of range (0, {max_a}]")
        self._ch[channel]["amplitude_v"] = float(amplitude_v)

    def set_waveform(self, channel: int, waveform: str) -> None:
        """Set the waveform shape on channel.

        Valid shapes: sine, square, triangle, sawtooth.
        """
        self._check(channel)
        valid = {"sine", "square", "triangle", "sawtooth"}
        if waveform not in valid:
            raise ValueError(f"Unknown waveform {waveform!r}. Valid: {sorted(valid)}")
        self._ch[channel]["waveform"] = waveform

    def enable_output(self, channel: int, enabled: bool = True) -> None:
        """Enable or disable the output on channel."""
        self._check(channel)
        self._ch[channel]["output"] = bool(enabled)

    # --- readback ---

    def get_status(self) -> dict:
        """Return the current configuration and output state for all channels."""
        return {
            "model": self.model,
            "channels": {ch: dict(cfg) for ch, cfg in self._ch.items()},
        }

    def sample(
        self,
        channel: int,
        n_points: int = 256,
        sample_rate_hz: float = 100_000.0,
    ) -> np.ndarray:
        """Generate n_points samples of the configured waveform.

        Returns a float64 numpy array. The output is clipped to ±amplitude_v.
        Simulates the behaviour of querying a scope connected to the output.
        """
        self._check(channel)
        cfg = self._ch[channel]
        freq = cfg["freq_hz"]
        amp = cfg["amplitude_v"]
        wf = cfg["waveform"]
        t = np.linspace(0, n_points / sample_rate_hz, n_points, endpoint=False)

        if wf == "sine":
            wave = amp * np.sin(2 * np.pi * freq * t)
        elif wf == "square":
            wave = amp * np.sign(np.sin(2 * np.pi * freq * t))
        elif wf == "triangle":
            wave = amp * (2 / np.pi) * np.arcsin(np.sin(2 * np.pi * freq * t))
        elif wf == "sawtooth":
            wave = amp * (2 * (freq * t - np.floor(freq * t + 0.5)))
        else:
            wave = np.zeros(n_points)

        return np.clip(wave, -amp, amp)

    # --- private ---

    def _check(self, channel: int) -> None:
        if channel not in self._ch:
            raise ValueError(
                f"Channel {channel} not available on {self.model}. "
                f"Valid channels: {sorted(self._ch)}"
            )
