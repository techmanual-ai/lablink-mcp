"""MCP tool implementations for AgentLink-Visa.

These four functions implement the v0.1 tool surface. They never interact with
pyvisa directly — all VISA access goes through session.py. All exceptions are
caught and converted to structured error dicts so the agent can reason about
and retry failures.
"""

from typing import Any

import pyvisa

from agentlink.config import load_config
from agentlink.exceptions import ConfigError, SessionError
from agentlink import session as _session


def connect(alias: str) -> dict[str, Any]:
    """Open a VISA session to the instrument and verify communication with *IDN?.

    Args:
        alias: Instrument alias matching a <alias>.toml config file.

    Returns:
        On success: {"success": True, "alias": str, "idn": str,
                     "manufacturer": str, "model_number": str,
                     "techmanual_document_id": int | None}
        On failure: {"success": False, "error": str, "hint": str}
    """
    if _session.is_connected(alias):
        return {
            "success": False,
            "error": f"Session already open for alias '{alias}'.",
            "hint": "Call disconnect_instrument() first, or use the existing session.",
        }

    try:
        config = load_config(alias)
    except ConfigError as exc:
        return {
            "success": False,
            "error": str(exc),
            "hint": f"Check that ~/.agentlink/instruments/{alias}.toml exists and has all required fields.",
        }

    try:
        resource = _session.open_session(config)
        idn = resource.query("*IDN?").strip()
    except pyvisa.Error as exc:
        return {
            "success": False,
            "error": f"VISA error: {exc}",
            "hint": "Check that the instrument is powered on, the resource string is correct, and the VISA backend is installed.",
        }

    return {
        "success": True,
        "alias": config.alias,
        "idn": idn,
        "manufacturer": config.manufacturer,
        "model_number": config.model_number,
        "techmanual_document_id": config.techmanual_document_id,
    }


def disconnect(alias: str) -> dict[str, Any]:
    """Close the VISA session for the given alias.

    Args:
        alias: Instrument alias of an open session.

    Returns:
        On success: {"success": True, "alias": str}
        On failure: {"success": False, "error": str, "hint": str}
    """
    try:
        _session.close_session(alias)
    except SessionError as exc:
        return {
            "success": False,
            "error": str(exc),
            "hint": f"Call connect('{alias}') before disconnect().",
        }
    except pyvisa.Error as exc:
        return {
            "success": False,
            "error": f"VISA error closing session: {exc}",
            "hint": "Session may already be closed. Proceeding is safe.",
        }

    return {"success": True, "alias": alias}


def query(alias: str, command: str) -> dict[str, Any]:
    """Send a SCPI query and return the response string.

    Args:
        alias: Instrument alias of an open session.
        command: SCPI query string (e.g. "MEAS:FREQ? CH1").

    Returns:
        On success: {"success": True, "alias": str, "command": str, "response": str}
        On failure: {"success": False, "error": str, "hint": str}
    """
    try:
        resource = _session.get_session(alias)
    except SessionError as exc:
        return {
            "success": False,
            "error": str(exc),
            "hint": f"Call connect('{alias}') before query().",
        }

    try:
        response = resource.query(command).strip()
    except pyvisa.errors.VisaIOError as exc:
        return {
            "success": False,
            "error": f"VISA I/O error: {exc}",
            "hint": "Check the command syntax and that the instrument is ready. A timeout may indicate the instrument does not respond to this query.",
        }
    except pyvisa.Error as exc:
        return {
            "success": False,
            "error": f"VISA error: {exc}",
            "hint": "Unexpected VISA error. Try disconnect() and reconnect().",
        }

    return {"success": True, "alias": alias, "command": command, "response": response}


def write(alias: str, command: str) -> dict[str, Any]:
    """Send a SCPI write command with no response expected.

    Args:
        alias: Instrument alias of an open session.
        command: SCPI command string (e.g. "CH1:SCALE 0.5").

    Returns:
        On success: {"success": True, "alias": str, "command": str}
        On failure: {"success": False, "error": str, "hint": str}
    """
    try:
        resource = _session.get_session(alias)
    except SessionError as exc:
        return {
            "success": False,
            "error": str(exc),
            "hint": f"Call connect('{alias}') before write().",
        }

    try:
        resource.write(command)
    except pyvisa.errors.VisaIOError as exc:
        return {
            "success": False,
            "error": f"VISA I/O error: {exc}",
            "hint": "Check the command syntax and that the instrument is connected.",
        }
    except pyvisa.Error as exc:
        return {
            "success": False,
            "error": f"VISA error: {exc}",
            "hint": "Unexpected VISA error. Try disconnect() and reconnect().",
        }

    return {"success": True, "alias": alias, "command": command}
