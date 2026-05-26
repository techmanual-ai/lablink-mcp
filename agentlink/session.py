"""VISA session lifecycle management.

Maintains a module-level dict of open sessions keyed by alias. Sessions are
held open between MCP tool calls — not opened and closed per call.
"""

import os
from typing import Any, Optional

import pyvisa

from agentlink.config import InstrumentConfig
from agentlink.exceptions import SessionError

_sessions: dict[str, Any] = {}

_DEFAULT_VISA_BACKEND = "@py"

_resource_manager: Optional[pyvisa.ResourceManager] = None


def _get_resource_manager() -> pyvisa.ResourceManager:
    """Return the shared ResourceManager, creating it on first call."""
    global _resource_manager
    if _resource_manager is None:
        backend = os.environ.get("AGENTLINK_VISA_BACKEND", _DEFAULT_VISA_BACKEND)
        _resource_manager = pyvisa.ResourceManager(backend)
    return _resource_manager


def is_connected(alias: str) -> bool:
    """Return True if a session is currently open for the given alias."""
    return alias in _sessions


def open_session(config: InstrumentConfig) -> Any:
    """Open a VISA session for the given instrument config and register it.

    Args:
        config: Validated InstrumentConfig for the instrument.

    Returns:
        The open pyvisa Resource object.

    Raises:
        pyvisa.Error: If the resource cannot be opened (propagated to tool layer).
    """
    rm = _get_resource_manager()
    resource = rm.open_resource(config.resource_string)
    resource.timeout = config.timeout_ms
    resource.read_termination = config.read_termination
    resource.write_termination = config.write_termination
    _sessions[config.alias] = resource
    return resource


def close_session(alias: str) -> None:
    """Close the VISA session for the given alias and remove it from the registry.

    Args:
        alias: Instrument alias.

    Raises:
        SessionError: If no session is open for the alias.
    """
    resource = _sessions.pop(alias, None)
    if resource is None:
        raise SessionError(f"No open session for alias '{alias}'.")
    resource.close()


def get_session(alias: str) -> Any:
    """Return the open VISA resource for the given alias.

    Args:
        alias: Instrument alias.

    Returns:
        The open pyvisa Resource object.

    Raises:
        SessionError: If no session is open for the alias.
    """
    resource = _sessions.get(alias)
    if resource is None:
        raise SessionError(
            f"No open session for alias '{alias}'. Call connect() first."
        )
    return resource
