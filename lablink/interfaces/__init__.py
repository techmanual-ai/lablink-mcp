"""Driver registries.

Two parallel dicts map a config ``type`` to its driver class and its config
class. Adding a driver is one line in each (see lablink_plan.md §4, §11). The
parallel-dict form reads more clearly in PR diffs than a single tuple registry
when only one side changes; the import-time key-match check below guards against
drift.
"""

from lablink.base import DriverConfig, LabLinkDriver
from lablink.interfaces.external_mcp import ExternalMcpDriver, ExternalMcpDriverConfig
from lablink.interfaces.rest import RestDriver, RestDriverConfig
from lablink.interfaces.serial import SerialDriver, SerialDriverConfig
from lablink.interfaces.ssh import SshDriver, SshDriverConfig
from lablink.interfaces.visa import VisaDriver, VisaDriverConfig

DRIVER_REGISTRY: dict[str, type[LabLinkDriver]] = {
    "visa": VisaDriver,
    "ssh": SshDriver,
    "rest": RestDriver,
    "serial": SerialDriver,
    "external_mcp": ExternalMcpDriver,
}

DRIVER_CONFIG_REGISTRY: dict[str, type[DriverConfig]] = {
    "visa": VisaDriverConfig,
    "ssh": SshDriverConfig,
    "rest": RestDriverConfig,
    "serial": SerialDriverConfig,
    "external_mcp": ExternalMcpDriverConfig,
}

# Runtime check at import. Uses if/raise rather than assert because Python -O
# strips assertions, and a registry-sync check that disappears under
# optimization is worse than no check at all. Drift would otherwise surface as a
# confusing KeyError deep in config.py instead of a clear message at startup.
if DRIVER_REGISTRY.keys() != DRIVER_CONFIG_REGISTRY.keys():
    raise RuntimeError(
        "DRIVER_REGISTRY and DRIVER_CONFIG_REGISTRY key sets must match. "
        f"Diff: registry-only={DRIVER_REGISTRY.keys() - DRIVER_CONFIG_REGISTRY.keys()}, "
        f"config-only={DRIVER_CONFIG_REGISTRY.keys() - DRIVER_REGISTRY.keys()}."
    )
