"""fake_instruments — dummy vendor SDK for exercising the LabLink python_shell driver.

Simulates a two-channel waveform generator and an 8-channel DAQ.
Import and use from a python_shell session exactly as you would a real vendor SDK.

    from fake_instruments import WaveformGen, DAQ, format_readings

    gen = WaveformGen("FG-100")
    daq = DAQ("DAQ-8")
"""

from fake_instruments.daq import DAQ
from fake_instruments.utils import format_readings, format_waveform_stats
from fake_instruments.waveform_gen import WaveformGen

__version__ = "0.1.0"
__all__ = ["WaveformGen", "DAQ", "format_readings", "format_waveform_stats"]
