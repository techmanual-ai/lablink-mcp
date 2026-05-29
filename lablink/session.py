"""Session registry.

Maintains a process-wide dict of open sessions keyed by alias. Sessions are
held open between MCP tool calls — not opened and closed per call. The registry
is protocol-agnostic: drivers construct their own native connection, wrap it in
a Session, and register it here.

Lookup is three-state (see lablink_plan.md §6) so error messages can
distinguish "no session" from "wrong type" — a wrong-type result tells the
agent the alias is in use by a different driver, which connect() would clobber.
"""

from dataclasses import dataclass
from typing import Optional

from lablink.base import Session

_sessions: dict[str, Session] = {}


@dataclass
class SessionLookup:
    found: bool
    wrong_type: bool                          # True iff a session exists but interface_type != expected_type
    session: Optional[Session] = None         # populated only when found and not wrong_type
    actual_type: Optional[str] = None         # populated only when wrong_type


def register(session: Session) -> None:
    """Register a session under its alias, replacing any existing entry."""
    _sessions[session.alias] = session


def deregister(alias: str) -> None:
    """Remove a session from the registry. No-op if the alias is absent."""
    _sessions.pop(alias, None)


def is_registered(alias: str) -> bool:
    """Return True if a session is currently open for the given alias."""
    return alias in _sessions


def get_any(alias: str) -> Optional[Session]:
    """Return the session for an alias regardless of type, or None.

    Used by the shared disconnect() tool, which must resolve the owning driver
    from any open session without knowing its type up front.
    """
    return _sessions.get(alias)


def lookup(alias: str, expected_type: str) -> SessionLookup:
    """Three-state lookup distinguishing missing from wrong-type sessions."""
    session = _sessions.get(alias)
    if session is None:
        return SessionLookup(found=False, wrong_type=False)
    if session.interface_type != expected_type:
        return SessionLookup(
            found=False, wrong_type=True, actual_type=session.interface_type
        )
    return SessionLookup(found=True, wrong_type=False, session=session)


def get(alias: str, expected_type: str) -> Optional[Session]:
    """Return the Session iff it exists AND its type matches expected_type.

    Returns None in both the "no session" and "wrong type" cases. Drivers that
    need to disambiguate the error message use lookup() instead.
    """
    result = lookup(alias, expected_type)
    return result.session if result.found else None
