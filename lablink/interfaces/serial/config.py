"""Serial driver config.

SerialDriverConfig inherits only DriverConfig — serial devices have no
network-style auth. RS232 / RS422 / RS485 are all electrical variants of the
same byte-stream model; they are all handled by this driver via pyserial.
The distinction lives in wiring, not in the config.
"""

from dataclasses import dataclass

from lablink.base import DriverConfig


@dataclass(kw_only=True)
class SerialDriverConfig(DriverConfig):
    """Config for a serial (RS232 / RS422 / RS485) device.

    serial_port is the OS path to the port. Examples:
        macOS / Linux: "/dev/ttyUSB0", "/dev/tty.usbserial-XXXX"
        Windows:       "COM3"

    The field is named serial_port (not port) to avoid ambiguity when serial
    and SSH configs are read side-by-side — SSH port is an int; serial_port is
    a string path.

    Parity values (case-insensitive at connect time): none, even, odd, mark, space.
    Stop bits: 1 or 2 (1.5 is legal for pyserial but uncommon; use int).
    Data bits: 5–8.
    """

    serial_port: str = ""
    baud_rate: int = 115200
    data_bits: int = 8
    parity: str = "none"
    stop_bits: int = 1
    read_termination: str = "\n"
    write_termination: str = "\n"
