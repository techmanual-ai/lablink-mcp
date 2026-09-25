"""Device discovery — the bus sweep behind `lablink scan`.

Enumerates what is physically attached (VISA resources, then serial ports),
asks each candidate ``*IDN?``, and returns a :class:`ScanResult`. Discovery
crosses two drivers, so it is a shared subsystem alongside ``diagnose()``
rather than a driver method (docs/ARCHITECTURE.md §17). The logic lives here,
not in the CLI command body, so it is reachable without click.

All third-party imports are lazy and a missing one degrades to a skipped sweep
plus an install action item — the same contract ``diagnose()`` honors (§9).
Probing is deliberately forgiving: a candidate that cannot be opened, or that
never answers, is reported as found-but-unidentified instead of aborting the
sweep.

``write_configs()`` turns a sweep into device configs the user can connect to
immediately. It never overwrites a file it did not write this run.
"""

import os
import re
from pathlib import Path
from typing import Any, Optional

from lablink.base import ConfigWriteOutcome, DiscoveredDevice, ScanResult
from lablink.event_logger import log_event

# The single definition of the VISA resource-string -> interface mapping.
from lablink.interfaces.visa.driver import _DEFAULT_VISA_BACKEND, _interface_type

# Short enough that one dead resource does not stall the whole sweep.
DEFAULT_PROBE_TIMEOUT_S = 2.0

# Matches SerialDriverConfig.baud_rate — a probe has no config to read.
_PROBE_BAUD = 115200

_IDN_QUERY = "*IDN?"

EMPTY_SCAN_HINT = (
    "Nothing answered on any bus LabLink can see. That usually means the "
    "instrument is powered off, its cable is unplugged, or it sits on a bus "
    "whose driver is not installed. Run `lablink diagnose` for the dependency "
    "audit."
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# *IDN? parsing and alias suggestion
# ---------------------------------------------------------------------------


def parse_idn(
    reply: str,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Split an ``*IDN?`` reply into (manufacturer, model, serial, firmware).

    ``*IDN?`` returns the four fields comma-separated. Missing trailing fields
    come back as None. Only the first three commas are split on, so a firmware
    string that itself contains commas stays intact.
    """
    parts = [p.strip() for p in reply.split(",", 3)]
    parts += [""] * (4 - len(parts))
    return tuple(p or None for p in parts)  # type: ignore[return-value]


def _slug(text: str) -> str:
    """Lowercase `text` with every run of non-alphanumerics folded to '_'."""
    return _NON_ALNUM.sub("_", text.lower()).strip("_")


def suggest_alias(manufacturer: Optional[str], model: Optional[str]) -> Optional[str]:
    """Return a `<vendor>_<model>` alias suggestion, lowercase with underscores.

    The instrument alias convention from README/`mcp_server` instructions.
    Returns None when neither field is usable.
    """
    parts = [_slug(part) for part in (manufacturer or "", model or "")]
    return "_".join(p for p in parts if p) or None


def _device(
    *,
    resource: str,
    driver_type: str,
    interface_type: str,
    idn: Optional[str],
    detail: Optional[str],
) -> DiscoveredDevice:
    """Build a DiscoveredDevice, parsing `idn` when the probe answered."""
    manufacturer = model = serial_number = firmware = None
    if idn is not None:
        manufacturer, model, serial_number, firmware = parse_idn(idn)
    return DiscoveredDevice(
        resource=resource,
        driver_type=driver_type,
        interface_type=interface_type,
        identified=idn is not None,
        idn=idn,
        manufacturer=manufacturer,
        model=model,
        serial_number=serial_number,
        firmware=firmware,
        suggested_alias=suggest_alias(manufacturer, model) if idn is not None else None,
        detail=detail or None,
    )


# ---------------------------------------------------------------------------
# VISA sweep
# ---------------------------------------------------------------------------


def _probe_visa(
    rm: Any, resource_string: str, timeout_s: float
) -> tuple[Optional[str], Optional[str]]:
    """Open `resource_string`, ask ``*IDN?``, close it.

    Returns (idn, error) with exactly one side populated. Every exception is
    caught: a probe is a question, not an operation, and a resource that is
    locked by other software or simply not a message-based device must not end
    the sweep.
    """
    try:
        resource = rm.open_resource(resource_string)
    except Exception as exc:
        return None, f"could not open: {exc}"
    try:
        resource.timeout = int(timeout_s * 1000)
        # SOCKET and ASRL resources carry no framing of their own — without a
        # read termination the query blocks until the timeout even when the
        # instrument did answer. Same defaults as VisaDriverConfig.
        resource.read_termination = "\n"
        resource.write_termination = "\n"
        return resource.query(_IDN_QUERY).strip(), None
    except Exception as exc:
        return None, f"no *IDN? reply: {exc}"
    finally:
        try:
            resource.close()
        except Exception:
            pass


def _scan_visa(timeout_s: float) -> tuple[list[DiscoveredDevice], list[str]]:
    """Sweep VISA resources. Returns (devices, action_items)."""
    try:
        import pyvisa
    except ImportError:
        return [], [
            "pyvisa is not installed, so the VISA sweep was skipped (USB, GPIB "
            "and LAN instruments will not appear). "
            "Run: pip install lablink-mcp[visa]"
        ]

    backend = os.environ.get("LABLINK_VISA_BACKEND", _DEFAULT_VISA_BACKEND)
    try:
        rm = pyvisa.ResourceManager(backend)
        resources = list(rm.list_resources())
    except Exception as exc:
        return [], [
            f"VISA sweep failed: {exc}. Run `lablink diagnose` for the backend audit."
        ]

    devices = []
    for resource_string in resources:
        idn, error = _probe_visa(rm, resource_string, timeout_s)
        devices.append(
            _device(
                resource=resource_string,
                driver_type="visa",
                interface_type=_interface_type(resource_string),
                idn=idn,
                detail=error,
            )
        )
    return devices, []


# ---------------------------------------------------------------------------
# Serial sweep
# ---------------------------------------------------------------------------


def _port_detail(port: Any) -> str:
    """Summarize a pyserial ListPortInfo: description, VID:PID, manufacturer."""
    bits = []
    if port.description and port.description != "n/a":
        bits.append(port.description)
    if port.vid is not None and port.pid is not None:
        bits.append(f"VID:PID={port.vid:04x}:{port.pid:04x}")
    if port.manufacturer:
        bits.append(port.manufacturer)
    return ", ".join(bits)


def _probe_serial(
    serial_mod: Any, port_path: str, timeout_s: float
) -> tuple[Optional[str], Optional[str]]:
    """Open `port_path`, write ``*IDN?``, read one line, close it.

    Returns (idn, error) with exactly one side populated.
    """
    try:
        conn = serial_mod.Serial(port=port_path, baudrate=_PROBE_BAUD, timeout=timeout_s)
    except Exception as exc:
        return None, f"could not open: {exc}"
    try:
        conn.write(f"{_IDN_QUERY}\n".encode())
        reply = conn.read_until(b"\n").decode("utf-8", errors="replace").strip()
    except Exception as exc:
        return None, f"no *IDN? reply: {exc}"
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return (reply, None) if reply else (None, "no *IDN? reply")


def _scan_serial(timeout_s: float) -> tuple[list[DiscoveredDevice], list[str]]:
    """Sweep OS serial ports. Returns (devices, action_items)."""
    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        return [], [
            "pyserial is not installed, so the serial-port sweep was skipped. "
            "Run: pip install lablink-mcp[serial]"
        ]

    try:
        ports = list(list_ports.comports())
    except Exception as exc:
        return [], [f"Serial-port sweep failed: {exc}."]

    devices = []
    for port in ports:
        idn, error = _probe_serial(serial, port.device, timeout_s)
        detail = _port_detail(port)
        if error is not None:
            detail = f"{detail} ({error})" if detail else error
        devices.append(
            _device(
                resource=port.device,
                driver_type="serial",
                interface_type="serial",
                idn=idn,
                detail=detail,
            )
        )
    return devices, []


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def scan(timeout_s: float = DEFAULT_PROBE_TIMEOUT_S) -> ScanResult:
    """Enumerate every reachable bus, probe each candidate, and report.

    Args:
        timeout_s: Per-probe ``*IDN?`` timeout in seconds. Applies to each
            candidate individually, so the worst case is bounded by the number
            of candidates, not by the slowest one.

    A sweep whose driver is not installed contributes an action item naming the
    extra to install instead of failing the scan.
    """
    devices: list[DiscoveredDevice] = []
    action_items: list[str] = []
    for sweep in (_scan_visa, _scan_serial):
        found, items = sweep(timeout_s)
        devices.extend(found)
        action_items.extend(items)

    log_event(
        op="scan",
        alias=None,
        success=True,
        found=len(devices),
        identified=sum(1 for d in devices if d.identified),
    )
    return ScanResult(devices=devices, action_items=action_items)


# ---------------------------------------------------------------------------
# Config writing (`lablink scan --write-configs`)
# ---------------------------------------------------------------------------

UNIDENTIFIED_SKIP_REASON = (
    "found but not identified — nothing to write without a manufacturer and model"
)

# Same defaults `lablink-sim --write-configs` writes. timeout_ms has no default
# on DriverConfig, so a generated config must carry it.
_VISA_TIMEOUT_MS = 5000
_SERIAL_TIMEOUT_MS = 2000

_VISA_TEMPLATE = '''# {alias} — written by `lablink scan --write-configs`.
# *IDN? -> {idn}
# Only the keys this device reported are written. Everything else keeps its
# default (read_termination, write_termination, document_ids, description);
# see README.md and examples/configs/visa_scope.toml for the full schema.
type            = "visa"
alias           = "{alias}"
resource_string = "{resource}"
manufacturer    = "{manufacturer}"
model_number    = "{model}"
timeout_ms      = {timeout_ms}
'''

_SERIAL_TEMPLATE = '''# {alias} — written by `lablink scan --write-configs`.
# *IDN? -> {idn}
# The identity above came back at {baud} baud. Only the keys this device
# reported are written; everything else keeps its default (baud_rate, data_bits,
# parity, stop_bits, terminations) — see examples/configs/serial_device.toml.
type        = "serial"
alias       = "{alias}"
serial_port = "{resource}"
timeout_ms  = {timeout_ms}
'''


def _toml_escape(value: str) -> str:
    """Escape `value` for a TOML basic string.

    *IDN? text is whatever the device chose to send, so a stray quote, backslash
    or newline in a manufacturer name must not produce a config file that will
    not parse.
    """
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def _config_body(device: DiscoveredDevice, alias: str) -> str:
    """Render the TOML config for one identified device.

    The header comment is the point: the first config a user opens should
    explain where it came from and that the unwritten keys have defaults.
    ``scan`` only produces "visa" and "serial" candidates, so those are the two
    layouts.
    """
    template, timeout_ms = (
        (_SERIAL_TEMPLATE, _SERIAL_TIMEOUT_MS)
        if device.driver_type == "serial"
        else (_VISA_TEMPLATE, _VISA_TIMEOUT_MS)
    )
    return template.format(
        alias=alias,
        # The reply goes into a comment, so it must stay on one line.
        idn=(device.idn or "").replace("\r", " ").replace("\n", " "),
        resource=_toml_escape(device.resource),
        manufacturer=_toml_escape(device.manufacturer or ""),
        model=_toml_escape(device.model or ""),
        timeout_ms=timeout_ms,
        baud=_PROBE_BAUD,
    )


def _unique_alias(device: DiscoveredDevice, taken: set[str]) -> str:
    """Return `device.suggested_alias`, disambiguated if `taken` already has it.

    Two identical instruments on one bench suggest the same alias. The serial
    number from ``*IDN?`` is the natural tiebreak; a device that reported none
    falls back to a numeric suffix.
    """
    base = device.suggested_alias or ""
    if base not in taken:
        return base
    serial_slug = _slug(device.serial_number or "")
    if serial_slug and f"{base}_{serial_slug}" not in taken:
        return f"{base}_{serial_slug}"
    suffix = 2
    while f"{base}_{suffix}" in taken:
        suffix += 1
    return f"{base}_{suffix}"


def write_configs(
    devices: list[DiscoveredDevice], target: Path, *, force: bool = False
) -> list[ConfigWriteOutcome]:
    """Write one device config per identified device into `target`.

    Args:
        devices: The devices from a :func:`scan`, in scan order.
        target: Directory to write into. Created if it does not exist.
        force: Overwrite a config file that is already there.

    Returns:
        One :class:`ConfigWriteOutcome` per device, in the order given. A
        device that never identified, and a device whose file already exists
        without `force`, are both returned as skips with a reason rather than
        dropped.
    """
    target.mkdir(parents=True, exist_ok=True)
    outcomes: list[ConfigWriteOutcome] = []
    taken: set[str] = set()

    for device in devices:
        if not device.identified or not device.suggested_alias:
            outcomes.append(
                ConfigWriteOutcome(
                    resource=device.resource, reason=UNIDENTIFIED_SKIP_REASON
                )
            )
            continue

        alias = _unique_alias(device, taken)
        taken.add(alias)
        path = target / f"{alias}.toml"
        if path.exists() and not force:
            outcomes.append(
                ConfigWriteOutcome(
                    resource=device.resource,
                    alias=alias,
                    reason=f"{path} already exists (pass --force to overwrite)",
                )
            )
            continue

        path.write_text(_config_body(device, alias))
        outcomes.append(
            ConfigWriteOutcome(resource=device.resource, alias=alias, path=path)
        )

    return outcomes
