"""VISA driver config.

VisaDriverConfig inherits DocumentedConfig (techmanual.ai document pointers)
because VISA targets are documented T&M instruments.
"""

from dataclasses import dataclass

from lablink.base import DocumentedConfig, DriverConfig


@dataclass(kw_only=True)
class VisaDriverConfig(DriverConfig, DocumentedConfig):
    """Config for a VISA/SCPI instrument.

    resource_string/manufacturer/model_number default to empty for dataclass
    construction safety (kw_only ordering across mixins); the VISA driver's
    connect() surfaces a clear error if resource_string is empty rather than
    failing deep inside pyvisa.
    """

    resource_string: str = ""
    manufacturer: str = ""
    model_number: str = ""
    read_termination: str = "\n"
    write_termination: str = "\n"
