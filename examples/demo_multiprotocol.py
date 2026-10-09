#!/usr/bin/env python3
"""Set a waveform over SCPI, read it back over REST, show the wiring.

Starts ``lablink-sim``, then calls LabLink's MCP tools the way an agent does:

1. ``rest_get`` samples DAQ-8 CH0 with the generator output off.
2. ``visa_write`` sets FG-100 CH1 to a 1 kHz, 2.5 V sine over SCPI.
3. ``rest_get`` samples DAQ-8 CH0 again. FG-100 CH1 is patched into DAQ-8 CH0,
   so the readings now peak at the amplitude set in step 2.
4. ``system_topology`` returns the patch cable that explains it.

The tools run in this process over fastmcp's in-memory transport, the same
tool-call path ``lablink-mcp`` serves over stdio. Device configs and the
topology go in a temporary directory; nothing under ~/.lablink is read or
written. The simulator is stopped on exit, Ctrl-C included. POSIX only.

Run from a clone with the visa, rest and demo extras installed:

    python examples/demo_multiprotocol.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time
from pathlib import Path

HOST = "127.0.0.1"
PORTS = {"FG-100 SCPI": 5025, "DAQ-8 SCPI": 5026, "DAQ-8 REST": 8080}
CONFIGS = ("sim_fgen.toml", "sim_daq.toml", "sim_daq_rest.toml")
FREQ_HZ = 1000
AMPLITUDE_V = 2.5
# One REST reading is the sine at one instant. Enough of them land near both
# peaks that min and max agree with the set amplitude to the printed precision.
N_READINGS = 500
PAUSE_S = 1.5
START_TIMEOUT_S = 20.0


def say(text: str = "") -> None:
    print(text, flush=True)


def echo(call: str) -> None:
    """Print a command before it runs."""
    print(f"> {call}", flush=True)


def pause() -> None:
    time.sleep(PAUSE_S)


def listening(port: int) -> bool:
    try:
        with socket.create_connection((HOST, port), timeout=0.2):
            return True
    except OSError:
        return False


def start_sim(workdir: Path) -> subprocess.Popen:
    """Launch lablink-sim in its own session, so Ctrl-C reaches only us."""
    busy = [port for port in PORTS.values() if listening(port)]
    if busy:
        sys.exit(f"Port(s) {busy} already in use. Is lablink-sim already "
                 "running? Stop it and retry.")
    sim = shutil.which("lablink-sim", path=sysconfig.get_path("scripts")) or "lablink-sim"
    args = ["--no-serial", "--seed", "1", "--write-configs", "devices"]
    say(f"$ lablink-sim {' '.join(args)}")
    log = open(workdir / "sim.log", "w")
    return subprocess.Popen([sim, *args], cwd=workdir, stdout=log,
                            stderr=subprocess.STDOUT, start_new_session=True)


def wait_for_sim(proc: subprocess.Popen, workdir: Path) -> None:
    """Poll until every port accepts a connection and every config exists."""
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit("lablink-sim exited during startup:\n"
                     + (workdir / "sim.log").read_text())
        if (all(listening(port) for port in PORTS.values())
                and all((workdir / "devices" / name).exists() for name in CONFIGS)):
            return
        time.sleep(0.1)
    sys.exit(f"lablink-sim was not listening after {START_TIMEOUT_S:.0f} s.")


def stop_sim(proc: subprocess.Popen) -> None:
    """Stop the simulator through its own SIGINT handler and wait for it.

    That handler joins the server threads before closing their fds; see
    commit 9baf2fd for the shutdown hang it avoids. SIGKILL is the last resort.
    """
    if proc.poll() is not None:
        return
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


async def run_demo() -> None:
    from fastmcp import Client

    from lablink.mcp_server import mcp, register_driver_tools

    register_driver_tools()
    async with Client(mcp) as client:

        async def tool(name: str, **kwargs) -> dict:
            result = await client.call_tool(name, kwargs, raise_on_error=False)
            data = result.structured_content or {}
            if result.is_error or not data.get("success"):
                raise SystemExit(f"{name} failed: {data or result.content}")
            return data

        async def sample_ch0() -> tuple[float, float]:
            echo(f'rest_get("sim_daq_rest", "/channels/0")  x{N_READINGS}')
            values = []
            for _ in range(N_READINGS):
                reply = await tool("rest_get", alias="sim_daq_rest", path="/channels/0")
                values.append(reply["decoded"]["value"])
            low, high = min(values), max(values)
            say(f"  min {low:+.3f} V   max {high:+.3f} V")
            return low, high

        echo('connect("sim_fgen")')
        say(f"  {(await tool('connect', alias='sim_fgen'))['identity']}")
        echo('connect("sim_daq_rest")')
        say(f"  {(await tool('connect', alias='sim_daq_rest'))['identity']}")
        pause()

        say()
        say("DAQ-8 CH0 over REST, generator output off:")
        await sample_ch0()
        pause()

        say()
        say("FG-100 CH1 over SCPI:")
        for command in ("SOUR1:FUNC SIN", f"SOUR1:FREQ {FREQ_HZ}",
                        f"SOUR1:VOLT {AMPLITUDE_V}", "OUTP1 ON"):
            echo(f'visa_write("sim_fgen", "{command}")')
            await tool("visa_write", alias="sim_fgen", command=command)
        say("  ok")
        pause()

        say()
        say("DAQ-8 CH0 over REST, generator output on:")
        low, high = await sample_ch0()
        say()
        say(f"  set over SCPI on FG-100 CH1:  {FREQ_HZ} Hz sine, "
            f"amplitude {AMPLITUDE_V:.3f} V")
        say(f"  read over REST on DAQ-8 CH0:  peaks {low:+.3f} V and {high:+.3f} V")
        pause()

        say()
        say("Why they agree:")
        echo('system_topology("sim_daq")')
        context = (await tool("system_topology", alias="sim_daq"))["topology_context"]
        for link in context["links"]:
            say(f"  link  {link['from_port']} -> {link['to_port']}"
                f"  ({link['params'].get('attenuation_db', 0)} dB)")
        for net in context["nets"]:
            ports = ", ".join(end["port"] for end in net["endpoints"])
            say(f"  net   {net['name']}: {net['signal']}")
            say(f"        {ports}")
        pause()


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="lablink-demo-"))
    shutil.copy(Path(__file__).with_name("topology_sim.toml"), workdir / "topology.toml")
    os.environ["LABLINK_CONFIG_DIR"] = str(workdir / "devices")
    os.environ["LABLINK_TOPOLOGY_FILE"] = str(workdir / "topology.toml")
    os.environ["LABLINK_LOG_DIR"] = ""
    # Treat SIGTERM as Ctrl-C, so it unwinds through asyncio.run cleanly and
    # reaches the finally below instead of killing us outright.
    signal.signal(signal.SIGTERM, lambda *_: signal.raise_signal(signal.SIGINT))

    proc = None
    try:
        proc = start_sim(workdir)
        wait_for_sim(proc, workdir)
        say("  " + ", ".join(f"{name} :{port}" for name, port in PORTS.items()))
        say()
        asyncio.run(run_demo())
    except KeyboardInterrupt:
        say("\nInterrupted.")
        return 130
    finally:
        if proc is not None:
            stop_sim(proc)
            say("Simulator stopped.")
        shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
