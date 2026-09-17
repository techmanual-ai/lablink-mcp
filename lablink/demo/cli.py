"""``lablink-sim`` — launch the simulated bench.

Starts every protocol front-end over one shared :class:`Bench` and prints the
device configs needed to connect. Nothing here is special-cased inside
LabLink: the agent reaches the simulator through the same visa / rest /
serial drivers it uses for real hardware.
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path

import click

from lablink.demo.instruments import Bench
from lablink.demo.rest import RestServer
from lablink.demo.scpi import ScpiInstrument, ScpiServer
from lablink.demo.serial_port import PTY_AVAILABLE, SerialServer

DEFAULT_GEN_PORT = 5025
DEFAULT_DAQ_PORT = 5026
DEFAULT_REST_PORT = 8080


def _write_configs(target: Path, gen_port: int, daq_port: int, rest_port: int,
                   serial_path: str | None, host: str) -> list[Path]:
    """Write ready-to-use device configs into ``target``. Returns paths written."""
    target.mkdir(parents=True, exist_ok=True)
    configs = {
        "sim_fgen.toml": f'''# Simulated function generator — served by `lablink-sim`.
type            = "visa"
alias           = "sim_fgen"
description     = "Simulated FG-100 function generator (LabLink demo bench)"
resource_string = "TCPIP0::{host}::{gen_port}::SOCKET"
manufacturer    = "LabLink"
model_number    = "FG-100"
timeout_ms      = 5000
read_termination  = "\\n"
write_termination = "\\n"
''',
        "sim_daq.toml": f'''# Simulated DAQ — served by `lablink-sim`.
type            = "visa"
alias           = "sim_daq"
description     = "Simulated DAQ-8 data acquisition module (LabLink demo bench)"
resource_string = "TCPIP0::{host}::{daq_port}::SOCKET"
manufacturer    = "LabLink"
model_number    = "DAQ-8"
timeout_ms      = 5000
read_termination  = "\\n"
write_termination = "\\n"
''',
        "sim_daq_rest.toml": f'''# Same simulated DAQ, reached over REST instead of SCPI.
type        = "rest"
alias       = "sim_daq_rest"
description = "Simulated DAQ-8 REST API (LabLink demo bench)"
base_url    = "http://{host}:{rest_port}/api/v1"
timeout_ms  = 10000
verify_ssl  = false
auth_type   = "none"
''',
    }
    if serial_path:
        configs["sim_daq_serial.toml"] = f'''# Same simulated DAQ, reached over a pseudo-terminal.
# The pty path changes every time `lablink-sim` restarts — re-run it to refresh.
type        = "serial"
alias       = "sim_daq_serial"
description = "Simulated DAQ-8 over pty (LabLink demo bench)"
serial_port = "{serial_path}"
timeout_ms  = 2000
baud_rate   = 115200
data_bits   = 8
parity      = "none"
stop_bits   = 1
read_termination  = "\\n"
write_termination = "\\n"
'''
    written = []
    for name, body in configs.items():
        path = target / name
        path.write_text(body)
        written.append(path)
    return written


@click.command(name="lablink-sim")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Interface to bind. Keep the loopback default unless you know why not.")
@click.option("--gen-port", default=DEFAULT_GEN_PORT, show_default=True, help="SCPI port for FG-100.")
@click.option("--daq-port", default=DEFAULT_DAQ_PORT, show_default=True, help="SCPI port for DAQ-8.")
@click.option("--rest-port", default=DEFAULT_REST_PORT, show_default=True, help="REST API port.")
@click.option("--no-serial", is_flag=True, help="Skip the pty serial front-end.")
@click.option("--no-rest", is_flag=True, help="Skip the REST front-end.")
@click.option("--write-configs", "config_dir", type=click.Path(path_type=Path),
              help="Write device configs here (e.g. ~/.lablink/devices) and exit-ready.")
@click.option("--seed", type=int, help="Seed the noise generator for reproducible readings.")
def main(host: str, gen_port: int, daq_port: int, rest_port: int, no_serial: bool,
         no_rest: bool, config_dir: Path | None, seed: int | None) -> None:
    """Run a simulated two-instrument bench over SCPI, REST and serial.

    The generator's CH1 is patched into the DAQ's CH0, so setting a waveform
    on one instrument changes what the other measures.
    """
    bench = Bench(seed=seed)
    servers: list = []

    try:
        gen_server = ScpiServer(host, gen_port, ScpiInstrument(bench, "gen", "SIMFG001"))
        daq_server = ScpiServer(host, daq_port, ScpiInstrument(bench, "daq", "SIMDAQ01"))
    except OSError as exc:
        raise click.ClickException(
            f"Could not bind SCPI port ({exc}). Another process may be using "
            f"{gen_port} or {daq_port}; pass --gen-port/--daq-port to change them."
        ) from exc
    gen_server.serve_in_thread()
    daq_server.serve_in_thread()
    servers += [gen_server, daq_server]

    if not no_rest:
        try:
            rest_server = RestServer(host, rest_port, bench)
            rest_server.serve_in_thread()
            servers.append(rest_server)
        except OSError as exc:
            raise click.ClickException(
                f"Could not bind REST port {rest_port} ({exc}). Pass --rest-port or --no-rest."
            ) from exc

    serial_path = None
    serial_server = None
    if not no_serial and PTY_AVAILABLE:
        serial_server = SerialServer(bench, role="daq")
        serial_path = serial_server.start()

    click.echo("LabLink simulated bench\n")
    click.echo(f"  FG-100  SCPI    TCPIP0::{host}::{gen_port}::SOCKET")
    click.echo(f"  DAQ-8   SCPI    TCPIP0::{host}::{daq_port}::SOCKET")
    if not no_rest:
        click.echo(f"  DAQ-8   REST    http://{host}:{rest_port}/api/v1")
    if serial_path:
        click.echo(f"  DAQ-8   serial  {serial_path}")
    elif not no_serial and not PTY_AVAILABLE:
        click.echo("  DAQ-8   serial  (unavailable: pty is not supported on this platform)")
    click.echo("\n  wiring  FG-100:CH1 --coax--> DAQ-8:CH0")

    if config_dir:
        written = _write_configs(config_dir.expanduser(), gen_port, daq_port,
                                 rest_port, serial_path, host)
        click.echo(f"\nWrote {len(written)} device configs to {config_dir.expanduser()}:")
        for path in written:
            click.echo(f"  {path.name}")
    else:
        click.echo("\nTo generate device configs:")
        click.echo("  lablink-sim --write-configs ~/.lablink/devices")

    click.echo("\nTry it:")
    click.echo(f"  printf '*IDN?\\n' | nc {host} {gen_port}")
    click.echo("\nOr ask an agent: "
               '"set sim_fgen CH1 to a 1 kHz 2 V sine, enable it, then read sim_daq CH0"')
    click.echo("\nCtrl-C to stop.")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        stop.wait()
    finally:
        click.echo("\nShutting down.")
        for server in servers:
            server.shutdown()
            server.server_close()
        if serial_server:
            serial_server.stop()
    sys.exit(0)


if __name__ == "__main__":
    main()
