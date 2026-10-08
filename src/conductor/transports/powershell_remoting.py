"""Explicit, HTTPS-only PowerShell remoting with lazy optional dependencies."""

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol, Self
from urllib.parse import urlsplit

from conductor.common._validation import positive_seconds
from conductor.common.errors import (
    ConductorError,
    ConfigurationError,
    MissingDependencyError,
    TransportError,
)
from conductor.transports.models import PowerShellResult, PowerShellValue

log = logging.getLogger(__name__)
RemotingAuth = Literal["kerberos", "negotiate", "ntlm"]


@dataclass(frozen=True)
class PowerShellRemotingConfig:
    """Validated target and identity, with no process-environment discovery.

    Kerberos uses the caller's existing Windows/domain identity when credentials
    are omitted. ``negotiate`` may fall back to NTLM. NTLM requires credentials.
    TLS verification cannot be disabled; ``ca_bundle`` supplies trusted PEM CAs.
    """

    endpoint: str
    auth: RemotingAuth = "kerberos"
    username: str | None = field(default=None, repr=False)
    password: str | None = field(default=None, repr=False)
    ca_bundle: str | Path | None = None
    configuration_name: str = "Microsoft.PowerShell"
    connection_timeout: float = 5.0
    request_timeout: float = 10.0
    command_timeout: float = 30.0
    cleanup_timeout: float = 5.0

    def __post_init__(self) -> None:
        _parse_endpoint(self.endpoint)
        if self.auth not in ("kerberos", "negotiate", "ntlm"):
            raise ConfigurationError("auth must be kerberos, negotiate, or ntlm.")
        for credential in (self.username, self.password):
            if credential is not None and (not isinstance(credential, str) or not credential):
                raise ConfigurationError("Supplied credentials must be non-empty strings.")
        if (self.username is None) != (self.password is None):
            raise ConfigurationError("Supply both username and password, or neither.")
        if self.auth == "ntlm" and self.username is None:
            raise ConfigurationError("NTLM requires explicit username and password.")
        if not isinstance(self.configuration_name, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+", self.configuration_name
        ):
            raise ConfigurationError("configuration_name must be a simple endpoint name.")
        if self.ca_bundle is not None:
            if not isinstance(self.ca_bundle, (str, Path)) or not self.ca_bundle:
                raise ConfigurationError("ca_bundle must name a readable PEM CA file.")
            try:
                valid_ca = Path(self.ca_bundle).is_file()
            except (OSError, ValueError):
                valid_ca = False
            if not valid_ca:
                raise ConfigurationError("ca_bundle must name a readable PEM CA file.")
        for name in ("connection_timeout", "request_timeout", "command_timeout", "cleanup_timeout"):
            object.__setattr__(self, name, positive_seconds(getattr(self, name), name))


def _parse_endpoint(endpoint: str) -> tuple[str, int, str]:
    message = "endpoint must be an explicit HTTPS URL with a host and remoting path."
    if not isinstance(endpoint, str) or any(char.isspace() or ord(char) < 32 for char in endpoint):
        raise ConfigurationError(message)
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port if parsed.port is not None else 443
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in endpoint
            or "#" in endpoint
            or not 1 <= port <= 65535
            or not re.fullmatch(r"/[A-Za-z0-9/_-]+", parsed.path)
        ):
            raise ConfigurationError(message)
    except ValueError:
        raise ConfigurationError(message) from None
    return parsed.hostname, port, parsed.path[1:]


class _Session(Protocol):
    def open(self) -> None: ...

    def execute(
        self, script: str, parameters: Mapping[str, PowerShellValue], timeout: float
    ) -> PowerShellResult: ...

    def close(self) -> None: ...


def _make_session(config: PowerShellRemotingConfig) -> _Session:
    try:
        from conductor.transports._pypsrp import _PypsrpSession
    except ImportError:
        raise MissingDependencyError(
            "Install conductor[windows] or conductor[iis] for PowerShell remoting. "
            "Non-Windows Kerberos also requires conductor[windows,kerberos] or "
            "conductor[iis,kerberos] and a configured system Kerberos installation."
        ) from None
    return _PypsrpSession(config)


class PowerShellRemoting:
    """An owned remote session; construct without network I/O, then open or use ``with``.

    Calls are synchronous and instances are not thread-safe. IIS clients borrow
    this transport and never close it. A transport failure invalidates the
    session: reopening is explicit, and the failed operation is never replayed.
    Per-call timeouts may tighten, but not extend, ``command_timeout``.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        auth: RemotingAuth = "kerberos",
        username: str | None = None,
        password: str | None = None,
        ca_bundle: str | Path | None = None,
        configuration_name: str = "Microsoft.PowerShell",
        connection_timeout: float = 5.0,
        request_timeout: float = 10.0,
        command_timeout: float = 30.0,
        cleanup_timeout: float = 5.0,
    ) -> None:
        self.config = PowerShellRemotingConfig(
            endpoint=endpoint,
            auth=auth,
            username=username,
            password=password,
            ca_bundle=ca_bundle,
            configuration_name=configuration_name,
            connection_timeout=connection_timeout,
            request_timeout=request_timeout,
            command_timeout=command_timeout,
            cleanup_timeout=cleanup_timeout,
        )
        self._session: _Session | None = None

    @classmethod
    def from_config(cls, config: PowerShellRemotingConfig) -> Self:
        """Construct from an explicit validated configuration, without opening it."""
        return cls(
            endpoint=config.endpoint,
            auth=config.auth,
            username=config.username,
            password=config.password,
            ca_bundle=config.ca_bundle,
            configuration_name=config.configuration_name,
            connection_timeout=config.connection_timeout,
            request_timeout=config.request_timeout,
            command_timeout=config.command_timeout,
            cleanup_timeout=config.cleanup_timeout,
        )

    @property
    def is_open(self) -> bool:
        """Whether this instance owns an open, usable session."""
        return self._session is not None

    def __repr__(self) -> str:
        return f"PowerShellRemoting(auth={self.config.auth!r}, is_open={self.is_open})"

    def __enter__(self) -> Self:
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            self.close()
        except ConductorError:
            if exc_type is None:
                raise
            log.warning("Remoting cleanup failed; the original error is preserved.")

    def open(self) -> None:
        """Open the explicit remote endpoint; repeated opens are no-ops.

        Raises MissingDependencyError, AuthenticationError, PermissionDeniedError,
        TlsError, TransportTimeoutError, or TransportError. No local fallback exists.
        """
        if self._session is not None:
            return
        session = _make_session(self.config)
        try:
            session.open()
        except BaseException:
            _close_quietly(session)
            raise
        self._session = session

    def close(self) -> None:
        """Close owned resources once; surface cleanup failures on normal exit."""
        session, self._session = self._session, None
        if session is not None:
            session.close()

    def execute(
        self,
        script: str,
        *,
        parameters: Mapping[str, PowerShellValue] | None = None,
        timeout: float | None = None,
    ) -> PowerShellResult:
        """Run trusted source with separately serialized scalar parameters.

        Returns library-owned output/error snapshots, not SDK objects. Transport
        failures invalidate the session and never replay a dispatched command.
        Read/connection timeouts and deadline checks are cooperative; see README
        for in-flight request and cleanup limitations. This is not a sandbox.
        """
        if not isinstance(script, str) or not script.strip():
            raise ConfigurationError("script must be non-empty trusted PowerShell source.")
        budget = self.config.command_timeout
        if timeout is not None:
            budget = min(budget, positive_seconds(timeout, "timeout"))
        bound_parameters = dict(parameters or {})
        for name, value in bound_parameters.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ConfigurationError("PowerShell parameter names must be simple identifiers.")
            if value is not None and not isinstance(value, (str, bool, int, float)):
                raise ConfigurationError("PowerShell parameters must be scalar values.")
        session = self._session
        if session is None:
            raise TransportError(
                "The remote transport is not open; use a context manager or open()."
            )
        try:
            return session.execute(script, bound_parameters, budget)
        except BaseException:
            self._session = None
            _close_quietly(session)
            raise


def _close_quietly(session: _Session) -> None:
    try:
        session.close()
    except Exception:
        log.warning("Remoting cleanup failed; the original error is preserved.")
