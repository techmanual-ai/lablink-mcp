"""Typed exceptions for AgentLink-Visa."""


class ConfigError(Exception):
    """Raised when an instrument config file is missing or has invalid fields."""


class SessionError(Exception):
    """Raised when a tool is called for an alias with no open VISA session."""


class QueryError(Exception):
    """Raised on VISA-level communication failures before conversion to a structured error dict."""
