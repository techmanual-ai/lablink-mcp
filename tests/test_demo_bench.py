"""Integration tests for the simulated bench.

These run the real protocol front-ends over real sockets, so they cover the
paths an evaluator actually exercises when following the quickstart. The
VISA leg is skipped when pyvisa is not installed.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

import pytest

from lablink.demo.instruments import Bench, SimError
from lablink.demo.rest import RestServer
from lablink.demo.scpi import ScpiInstrument, ScpiServer


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def bench() -> Bench:
    return Bench(seed=1234)


# ---------------------------------------------------------------------------
# Simulation core
# ---------------------------------------------------------------------------


def test_unpatched_channel_reads_noise_floor(bench: Bench) -> None:
    assert abs(bench.read(3)) < 0.01


def test_patched_channel_follows_generator(bench: Bench) -> None:
    """The FG->DAQ patch is what makes the topology demo real."""
    bench.gen.set_frequency(1, 1000.0)
    bench.gen.set_amplitude(1, 2.0)
    bench.gen.set_waveform(1, "sine")
    bench.gen.enable_output(1, True)

    readings = [bench.read(0) for _ in range(200)]
    assert max(readings) > 1.0, "patched channel should see the driven waveform"
    assert max(abs(v) for v in readings) <= 2.1, "readings should respect amplitude"
    # An unpatched channel on the same DAQ stays quiet.
    assert abs(bench.read(1)) < 0.01


def test_disabled_output_stops_driving_daq(bench: Bench) -> None:
    bench.gen.set_amplitude(1, 5.0)
    bench.gen.enable_output(1, True)
    assert max(abs(bench.read(0)) for _ in range(100)) > 1.0
    bench.gen.enable_output(1, False)
    assert max(abs(bench.read(0)) for _ in range(50)) < 0.01


def test_daq_clamps_to_configured_range(bench: Bench) -> None:
    bench.daq.set_range(0, -1.0, 1.0)
    bench.gen.set_amplitude(1, 5.0)
    bench.gen.enable_output(1, True)
    assert all(-1.0 <= bench.read(0) <= 1.0 for _ in range(100))


@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.gen.set_frequency(1, -5),
        lambda b: b.gen.set_amplitude(1, 999),
        lambda b: b.gen.set_waveform(1, "spiral"),
        lambda b: b.gen.set_frequency(99, 1000),
        lambda b: b.daq.set_range(0, 5.0, -5.0),
        lambda b: b.daq.measure(99),
    ],
)
def test_invalid_parameters_raise(bench: Bench, call) -> None:
    with pytest.raises(SimError):
        call(bench)


def test_attenuating_patch_scales_signal(bench: Bench) -> None:
    from lablink.demo.instruments import Patch

    bench.patches = [Patch(src_channel=1, dst_channel=0, attenuation_db=20.0)]
    bench.gen.set_amplitude(1, 10.0)
    bench.gen.enable_output(1, True)
    # 20 dB of attenuation is a factor of 10 in voltage.
    assert max(abs(bench.read(0)) for _ in range(200)) < 1.5


# ---------------------------------------------------------------------------
# SCPI front-end
# ---------------------------------------------------------------------------


@pytest.fixture
def scpi_pair(bench: Bench):
    """Generator and DAQ SCPI servers sharing one bench."""
    gen = ScpiServer("127.0.0.1", _free_port(), ScpiInstrument(bench, "gen"))
    daq = ScpiServer("127.0.0.1", _free_port(), ScpiInstrument(bench, "daq"))
    gen.serve_in_thread()
    daq.serve_in_thread()
    yield gen, daq
    for server in (gen, daq):
        server.shutdown()
        server.server_close()


def _scpi(server: ScpiServer, *commands: str) -> list[str]:
    """Send commands over a real socket; return replies to the queries."""
    replies: list[str] = []
    with socket.create_connection(server.server_address, timeout=5) as sock:
        sock.sendall(("".join(f"{c}\n" for c in commands)).encode())
        # A query is any command containing '?' — note that a channel-list
        # suffix means queries do not necessarily *end* with it, as in
        # "MEAS:VOLT? (@0)".
        expected = sum(1 for c in commands if "?" in c)
        buffer = b""
        while len(replies) < expected:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                replies.append(line.decode().strip())
    return replies


def test_scpi_idn(scpi_pair) -> None:
    gen, daq = scpi_pair
    assert _scpi(gen, "*IDN?")[0].startswith("LabLink,FG-100,")
    assert _scpi(daq, "*IDN?")[0].startswith("LabLink,DAQ-8,")


def test_scpi_roundtrip_setting_and_query(scpi_pair) -> None:
    gen, _ = scpi_pair
    replies = _scpi(
        gen, "SOUR1:FREQ 2500", "SOUR1:VOLT 3.5", "SOUR1:FUNC SQUARE", "OUTP1 ON",
        "SOUR1:FREQ?", "SOUR1:VOLT?", "SOUR1:FUNC?", "OUTP1?",
    )
    assert float(replies[0]) == pytest.approx(2500.0)
    assert float(replies[1]) == pytest.approx(3.5)
    assert replies[2] == "SQUA"
    assert replies[3] == "1"


def test_scpi_cross_instrument_patch(scpi_pair) -> None:
    """A setting written to the generator changes what the DAQ measures."""
    gen, daq = scpi_pair
    _scpi(gen, "SOUR1:VOLT 4.0", "SOUR1:FREQ 1000", "OUTP1 ON")
    # One connection, many queries — a fresh socket per reading is both slower
    # and unlike how a driver actually holds a session open.
    readings = [float(v) for v in _scpi(daq, *["MEAS:VOLT? (@0)"] * 60)]
    assert max(abs(v) for v in readings) > 1.0


def test_scpi_error_queue_reports_and_clears(scpi_pair) -> None:
    gen, _ = scpi_pair
    first, second = _scpi(gen, "SOUR1:VOLT 999", "SYST:ERR?", "SYST:ERR?")
    assert first.startswith("-222,")
    assert second.startswith("0,")


def test_scpi_undefined_header(scpi_pair) -> None:
    _, daq = scpi_pair
    assert _scpi(daq, "NOPE:NOPE 1", "SYST:ERR?")[0].startswith("-113,")


def test_scpi_read_all_returns_every_channel(scpi_pair) -> None:
    _, daq = scpi_pair
    assert len(_scpi(daq, "READ?")[0].split(",")) == 8


def test_scpi_rst_restores_defaults(scpi_pair) -> None:
    gen, _ = scpi_pair
    assert _scpi(gen, "SOUR1:VOLT 7.0", "OUTP1 ON", "*RST", "OUTP1?", "SOUR1:VOLT?")[0] == "0"


# ---------------------------------------------------------------------------
# REST front-end
# ---------------------------------------------------------------------------


@pytest.fixture
def rest_server(bench: Bench):
    server = RestServer("127.0.0.1", _free_port(), bench)
    server.serve_in_thread()
    yield f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    server.shutdown()
    server.server_close()


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.loads(response.read())


def _post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def test_rest_status_reports_patches(rest_server: str) -> None:
    status = _get(f"{rest_server}/status")
    assert status["daq"]["n_channels"] == 8
    assert status["patches"][0] == {"from": "FG-100:CH1", "to": "DAQ-8:CH0", "attenuation_db": 0.0}


def test_rest_channel_read(rest_server: str) -> None:
    body = _get(f"{rest_server}/channels/0")
    assert body["unit"] == "V" and isinstance(body["value"], float)


def test_rest_post_updates_generator(rest_server: str) -> None:
    body = _post(f"{rest_server}/generator/1", {"freq_hz": 440.0, "waveform": "triangle"})
    assert body["freq_hz"] == pytest.approx(440.0)
    assert body["waveform"] == "triangle"


def test_rest_rejects_out_of_range(rest_server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        _post(f"{rest_server}/generator/1", {"amplitude_v": 999})
    assert exc.value.code == 400


def test_rest_unknown_route_404(rest_server: str) -> None:
    with pytest.raises(urllib.error.HTTPError) as exc:
        _get(f"{rest_server}/nope")
    assert exc.value.code == 404


# ---------------------------------------------------------------------------
# Cross-protocol: the whole point of the shared bench
# ---------------------------------------------------------------------------


def test_scpi_write_visible_over_rest(bench: Bench, scpi_pair, rest_server: str) -> None:
    gen, _ = scpi_pair
    _scpi(gen, "SOUR1:FUNC SAWTOOTH", "SOUR1:VOLT 2.5")
    state = _get(f"{rest_server}/generator/1")
    assert state["waveform"] == "sawtooth"
    assert state["amplitude_v"] == pytest.approx(2.5)


def test_rest_write_visible_over_scpi(bench: Bench, scpi_pair, rest_server: str) -> None:
    gen, _ = scpi_pair
    _post(f"{rest_server}/generator/1", {"waveform": "square", "amplitude_v": 1.25})
    replies = _scpi(gen, "SOUR1:FUNC?", "SOUR1:VOLT?")
    assert replies[0] == "SQUA"
    assert float(replies[1]) == pytest.approx(1.25)


# ---------------------------------------------------------------------------
# VISA driver path — the one evaluators actually care about
# ---------------------------------------------------------------------------


def test_visa_driver_reaches_simulator(scpi_pair) -> None:
    """LabLink's real VISA driver talks to the simulator with no mocking."""
    pyvisa = pytest.importorskip("pyvisa")
    gen, daq = scpi_pair
    rm = pyvisa.ResourceManager("@py")
    resources = []
    try:
        for server in (gen, daq):
            resource = rm.open_resource(
                f"TCPIP0::127.0.0.1::{server.server_address[1]}::SOCKET"
            )
            resource.timeout = 5000
            resource.read_termination = "\n"
            resource.write_termination = "\n"
            resources.append(resource)
        gen_res, daq_res = resources
        assert "LabLink" in gen_res.query("*IDN?")

        gen_res.write("SOUR1:VOLT 3.0")
        gen_res.write("SOUR1:FREQ 1000")
        gen_res.write("OUTP1 ON")
        readings = [float(daq_res.query("MEAS:VOLT? (@0)")) for _ in range(60)]
        assert max(abs(v) for v in readings) > 1.0
    finally:
        for resource in resources:
            resource.close()
