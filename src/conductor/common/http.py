"""Shared synchronous HTTP mechanics, not a universal platform client.

Authentication headers are built by each integration. HTTPX is loaded only on the
first real request; library-owned requests/results support dependency-free fakes.
"""

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, cast

from conductor.common._validation import ca_file, https_base_url, positive_seconds
from conductor.common.errors import (
    AuthenticationError,
    ConductorError,
    ConfigurationError,
    ConflictError,
    HttpResponseError,
    MissingDependencyError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    ResourceNotFoundError,
    TransportError,
)

JsonValue: TypeAlias = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None
HttpMethod: TypeAlias = Literal["GET", "POST"]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpResponse:
    """A library-owned response; untrusted headers and payload are excluded from repr."""

    status_code: int
    body: bytes = field(default=b"", repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)


class HttpTransport(Protocol):
    """Injectable synchronous seam. Implementations must not replay writes or follow redirects.

    Caller-supplied transports are borrowed; integration clients never close them.
    TLS, bounded I/O, error sanitization, and target fidelity are the implementer's
    responsibility when replacing the default transport.
    """

    def request(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json_body: JsonValue = None,
        timeout: float,
    ) -> HttpResponse: ...

    def close(self) -> None: ...


class HttpxTransport:
    """Lazy, owned HTTPS transport. No redirects, environment proxies, or automatic retries."""

    def __init__(self, *, ca_bundle: str | Path | None = None) -> None:
        ca_file(ca_bundle)
        self._ca_bundle = ca_bundle
        self._backend: HttpTransport | None = None
        self._closed = False

    def request(
        self,
        method: HttpMethod,
        url: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, str] | None = None,
        json_body: JsonValue = None,
        timeout: float,
    ) -> HttpResponse:
        if self._closed:
            raise TransportError("The HTTP transport is closed.")
        https_base_url(url)
        positive_seconds(timeout, "timeout")
        if self._backend is None:
            try:
                from conductor.common._httpx import _HttpxBackend
            except ImportError:
                raise MissingDependencyError(
                    "Install the requested conductor[azure-devops], conductor[argocd], "
                    "or conductor[sonarqube] extra for HTTP support."
                ) from None
            self._backend = _HttpxBackend(ca_bundle=self._ca_bundle)
        return self._backend.request(
            method, url, headers=headers, params=params, json_body=json_body, timeout=timeout
        )

    def close(self) -> None:
        """Close only the backend owned by this instance, at most once."""
        if self._closed:
            return
        self._closed = True
        if self._backend is not None:
            self._backend.close()


class _JsonConnection:
    """Composition helper for JSON APIs with platform-supplied targets/authentication."""

    def __init__(
        self,
        *,
        base_url: str,
        headers: Mapping[str, str],
        timeout: float,
        ca_bundle: str | Path | None,
        transport: HttpTransport | None,
    ) -> None:
        self._base_url = https_base_url(base_url)
        self._headers = {"Accept": "application/json", **headers}
        self.timeout = positive_seconds(timeout, "timeout")
        self._owned = transport is None
        self._transport = HttpxTransport(ca_bundle=ca_bundle) if transport is None else transport
        self._closed = False

    def request(
        self,
        method: HttpMethod,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: JsonValue = None,
        timeout: float | None = None,
    ) -> JsonValue:
        if self._closed:
            raise TransportError("The integration client is closed.")
        if (
            path.startswith("/")
            or ":" in path
            or any(part in (".", "..") for part in path.split("/"))
        ):
            raise ConfigurationError("API paths must be relative to the configured server.")
        budget = (
            self.timeout
            if timeout is None
            else min(self.timeout, positive_seconds(timeout, "timeout"))
        )
        try:
            json.dumps(json_body, allow_nan=False)
        except (ValueError, TypeError, RecursionError):
            raise ConfigurationError("Request data must be finite, serializable JSON.") from None
        response = self._transport.request(
            method,
            f"{self._base_url}/{path}",
            headers=dict(self._headers),
            params=params,
            json_body=json_body,
            timeout=budget,
        )
        _require_success(response.status_code)
        try:
            return cast(JsonValue, json.loads(response.body, parse_constant=_invalid_json_constant))
        except (ValueError, TypeError, RecursionError):
            raise ProtocolError("Remote API returned invalid JSON.") from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owned:
            self._transport.close()

    def exit(self, exc_type: type[BaseException] | None) -> None:
        try:
            self.close()
        except ConductorError:
            if exc_type is None:
                raise
            log.warning("HTTP cleanup failed; the original error is preserved.")


def _invalid_json_constant(value: str) -> None:
    raise ValueError("Invalid JSON constant")


def _require_success(status: int) -> None:
    if 200 <= status < 300:
        return
    if status == 401:
        raise AuthenticationError("Remote API authentication failed.")
    if status == 403:
        raise PermissionDeniedError("Remote API access was denied.")
    if status == 404:
        raise ResourceNotFoundError("The requested remote resource does not exist.")
    if status in (409, 412):
        raise ConflictError("Remote operation conflicted with the current resource state.")
    if status == 429:
        raise RateLimitError(status)
    raise HttpResponseError(status)
