"""Simulated bench for trying LabLink without hardware.

Starts a two-instrument bench behind real protocol front-ends so the agent
reaches it through the genuine visa / rest / serial drivers:

    lablink-sim --write-configs ~/.lablink/devices

The generator's CH1 is patched into the DAQ's CH0, so a waveform set on one
instrument changes what the other measures — which is what makes
``system_topology`` mean something on a bench with no cables in it.
"""

from lablink.demo.instruments import DAQ, Bench, Patch, SimError, WaveformGen

__all__ = ["Bench", "DAQ", "WaveformGen", "Patch", "SimError"]
