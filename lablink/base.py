"""Core data models and the driver ABC for LabLink.

This module houses every tool return type, the config dataclasses (base +
mixins), the ``Session`` record, and the ``LabLinkDriver`` ABC. It is the one
module that all drivers depend on and depends on nothing else in ``lablink`` —
keeping it import-cycle-free.

All dataclasses are declared ``kw_only=True``. See ``lablink_plan.md`` §3.1:
without it, field ordering across the config mixins (DriverConfig + AuthConfig
+ DocumentedConfig) breaks the moment a subclass adds a required field after a
mixin contributes a defaulted one.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from queue import Queue
from threading import Thread
from typing import Any, ClassVar, Generic, Optional, TypeVar

ConfigT = TypeVar("ConfigT", bound="DriverConfig")


# ---------------------------------------------------------------------------
# Tool result types
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class Result:
    """Generic tool result — used by disconnect() and write-style tools with
    no payload. Reserved for operations whose entire signal is success/failure.
    """

    success: bool
    error: Optional[str] = None
    hint: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass(kw_only=True)
class ReadResult:
    """Tool result for any tool that returns data — queries, reads, exec
    stdout, REST bodies, etc.

    Read-style semantics (locked, see lablink_plan.md §3):
      Data arrived:       ReadResult(success=True, raw=<data>, timed_out=False)
      Timeout, no data:   ReadResult(success=True, raw=None, timed_out=True)
      Broken/dead stream: ReadResult(success=False, error=..., hint=...)
    """

    success: bool
    raw: Any = None                       # str | bytes | list | None (§6.5 batching)
    decoded: Any = None                   # driver-parsed form when applicable
    format: str = "text"                  # "text" | "json" | "bytes"
    encoding: str = "utf-8"
    timed_out: bool = False
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None
    hint: Optional[str] = None


@dataclass(kw_only=True)
class ConnectResult:
    """Result of a connect() call — carries identity, device memory, and
    documentation pointers populated once per session.
    """

    success: bool
    alias: str
    interface_type: str                   # "visa" | "ssh" | "rest" | ...
    identity: Optional[str] = None        # *IDN?, SSH banner, HTTP server header, etc.
    device_memory: Optional[str] = None   # content of <alias>.md if present
    instrument_memory: Optional[str] = None  # DEPRECATED alias of device_memory;
                                          # auto-populated by __post_init__ for
                                          # back-compat through Phase 1; removed in
                                          # Phase 2. Do not read in new code.
    techmanual_document_ids: list[int] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    error: Optional[str] = None
    hint: Optional[str] = None

    def __post_init__(self) -> None:
        # Mirror device_memory -> instrument_memory at construction time.
        # IMPORTANT: __post_init__ runs once, at construction. Post-hoc attribute
        # writes (result.device_memory = "...") do NOT re-trigger this and leave
        # instrument_memory stale at None. Code that populates device_memory
        # after construction must use dataclasses.replace() (see §6.3.1).
        # This method is deleted when instrument_memory is removed in Phase 2.
        if self.device_memory is not None and self.instrument_memory is None:
            self.instrument_memory = self.device_memory


@dataclass(kw_only=True)
class DiagnosticResult:
    """Result of a diagnose() call — per-alias diagnosis or system audit."""

    ready: bool
    alias: Optional[str] = None           # None for the no-alias system audit
    interface_type: Optional[str] = None
    checks: dict = field(default_factory=dict)   # per-alias: {check_name: {status, detail}}
    drivers: dict = field(default_factory=dict)  # system audit: {type: {python_deps, system_deps, status}}
    action_items: list[str] = field(default_factory=list)  # most-blocking first
    device_memory: Optional[str] = None
    error: Optional[str] = None


@dataclass(kw_only=True)
class SystemDepStatus:
    """One OS-level dependency's status, as reported by a driver's
    system_dep_check().
    """

    name: str                             # e.g. "libusb", "NI-VISA"
    present: bool
    version: Optional[str] = None
    install_hint: Optional[str] = None    # platform-appropriate install command


# ---------------------------------------------------------------------------
# Config types
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class DriverConfig:
    """Base config — fields shared by every driver. Driver-specific subclasses
    add their own fields and may mix in AuthConfig / DocumentedConfig.
    """

    alias: str
    type: str
    timeout_ms: int
    description: Optional[str] = None


@dataclass(kw_only=True)
class AuthConfig:
    """Mixin for drivers that need authentication (SSH, REST). Drivers without
    auth (VISA, serial, python_shell) do not inherit this.
    """

    auth_type: str = "none"               # none | bearer | api_key | basic | ssh_key | ssh_password
    auth_token_env: Optional[str] = None
    auth_username_env: Optional[str] = None
    auth_password_env: Optional[str] = None
    auth_ssh_key_path: Optional[str] = None   # tilde-expanded by config.py
    auth_ssh_passphrase_env: Optional[str] = None


@dataclass(kw_only=True)
class DocumentedConfig:
    """Mixin for drivers that connect to documented devices (T&M instruments).
    Carries techmanual.ai document pointers. Inherited by VisaDriverConfig.
    """

    techmanual_document_ids: list[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


@dataclass(kw_only=True)
class Session(Generic[ConfigT]):
    """A live connection registered in the session registry.

    Generic[ConfigT] so each driver declares its config type once
    (class VisaDriver(LabLinkDriver[VisaDriverConfig])) and gets type-safe
    access to driver-specific config fields without cast() boilerplate.
    """

    alias: str
    interface_type: str
    raw: Any                              # native connection object (pyvisa.Resource, etc.)
    config: ConfigT
    buffer: Optional[Queue] = None        # streaming-aware drivers only; None for request/response
    buffer_thread: Optional[Thread] = None
    metadata: dict = field(default_factory=dict)  # e.g. {"stream_error": "..."}


# ---------------------------------------------------------------------------
# Driver ABC
# ---------------------------------------------------------------------------


class LabLinkDriver(ABC, Generic[ConfigT]):
    """Base class for all LabLink drivers. Generic over the driver's config
    subclass. The ABC is a code-sharing contract, not a tool-uniformity
    contract — drivers register their own MCP tools via register_tools().
    """

    type_name: ClassVar[str]              # "visa", "ssh", ... — must match the DRIVER_REGISTRY key

    # --- Lifecycle ---

    @abstractmethod
    def connect(self, config: ConfigT) -> ConnectResult:
        """Open the connection and register a Session in the session registry.

        On success: construct a Session, call session_registry.register(session),
        and return ConnectResult(success=True, ...). Do not populate
        device_memory — the shared connect tool injects it (§6.3.1).
        On failure: return ConnectResult(success=False, error=..., hint=...);
        do NOT register a session; clean up partial state.

        Lazy-import all third-party deps inside this method. A missing dep
        returns ConnectResult(success=False, error="Missing dependency: <pkg>",
        hint="Run: pip install lablink-mcp[<extra>]").
        """
        ...

    @abstractmethod
    def disconnect(self, session: "Session[ConfigT]") -> Result:
        """Close the native connection and tear down any buffer thread.

        After this returns (success or failure), the shared disconnect() tool
        deregisters the alias regardless.
        """
        ...

    @abstractmethod
    def diagnose(self, config: ConfigT) -> DiagnosticResult:
        """Per-alias diagnosis. Receives a config (NOT a session) — diagnostics
        are stateless and work whether or not a session is open. May perform
        fresh test connections; does not inspect any existing open session.
        """
        ...

    # --- Tool / CLI registration ---

    @abstractmethod
    def register_tools(self, mcp) -> None:
        """Register this driver's operation tools with the FastMCP server.

        Called once at server startup iff check_python_deps() reports all deps
        present. Each registered tool looks up its session via
        session_registry.get(alias, expected_type=cls.type_name), returns a
        structured error on missing/wrong-type session, wraps native exceptions
        into ReadResult/Result dicts, and logs at every return point.
        """
        ...

    @abstractmethod
    def register_cli_commands(self, cli_group) -> None:
        """Register this driver's CLI subgroup with the root Click group.

        Same dep-presence gating as register_tools. A driver with no useful CLI
        surface may implement this as ``pass`` and document why.
        """
        ...

    # --- System audit hooks (called during the no-alias diagnose) ---

    @classmethod
    def check_python_deps(cls) -> list[tuple[str, bool]]:
        """Return [(package_name, is_available), ...] for each Python dep.

        Uses importlib.util.find_spec — not try/import — to avoid side effects.
        Default: empty list (driver has no Python-level deps).
        """
        return []

    @classmethod
    def system_dep_check(cls) -> list[SystemDepStatus]:
        """Return one SystemDepStatus per OS-level dep this driver requires.
        Default: empty list (no system deps).
        """
        return []
