"""Library-owned exception categories; external response bodies are never included."""


class ConductorError(Exception):
    """Base class for operational failures reported by Conductor."""


class ConfigurationError(ConductorError, ValueError):
    """Explicit caller configuration is invalid."""


class MissingDependencyError(ConductorError, ImportError):
    """An optional dependency required for the requested operation is unavailable."""


class TransportError(ConductorError):
    """Remote communication or protocol handling failed; a write may have completed."""


class AuthenticationError(TransportError):
    """The remote identity could not be authenticated."""


class PermissionDeniedError(ConductorError):
    """The authenticated identity is not allowed to perform the operation."""


class TlsError(TransportError):
    """TLS certificate verification or negotiation failed."""


class OperationTimeoutError(ConductorError, TimeoutError):
    """An operation exceeded its configured time budget."""


class TransportTimeoutError(TransportError, OperationTimeoutError):
    """Remote communication timed out; the outcome of a dispatched write may be unknown."""


class ResourceNotFoundError(ConductorError):
    """The explicitly requested resource does not exist."""


class ConflictError(ConductorError):
    """An operation conflicts with current resource state or a concurrency check."""


class ProtocolError(ConductorError):
    """An external service returned an unexpected response schema."""


class HttpResponseError(ConductorError):
    """An unsuccessful HTTP response, without URL, headers, or response body."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Remote API returned HTTP {status_code}.")


class RateLimitError(HttpResponseError):
    """The remote service rate-limited this request; no write was replayed."""
