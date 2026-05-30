"""Utility helpers for fake_instruments output formatting."""

import numpy as np
import pint
from tabulate import tabulate


def format_readings(readings: dict[int, pint.Quantity], unit: str = "volt") -> str:
    """Format a dict of {channel: Quantity} as a text table.

    Args:
        readings: Output of DAQ.read_all().
        unit: pint unit string to convert values into before display.
    """
    rows = []
    for ch, qty in sorted(readings.items()):
        converted = qty.to(unit)
        rows.append((f"CH{ch}", f"{converted.magnitude:.6f}", unit))
    return tabulate(rows, headers=["Channel", "Value", "Unit"], tablefmt="simple")


def format_waveform_stats(samples: np.ndarray, label: str = "CH?") -> str:
    """Return a one-line summary of a sampled waveform array."""
    return (
        f"{label}: n={len(samples)}"
        f"  min={samples.min():.4f}"
        f"  max={samples.max():.4f}"
        f"  mean={samples.mean():.4f}"
        f"  rms={float(np.sqrt(np.mean(samples**2))):.4f}"
    )
