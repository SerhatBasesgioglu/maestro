"""Narrow synchronous streaming seam for raw artifact HTTP exchanges."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Literal, Protocol

from conductor.common._validation import ca_file, https_base_url, positive_seconds
from conductor.common.errors import ConfigurationError, MissingDependencyError, TransportError

if TYPE_CHECKING:
    from conductor.artifacts._httpx import _HttpxArtifactBackend


@dataclass(frozen=True)
class ArtifactHttpResponse:
    """A response whose payload remains a one-pass raw byte stream."""

    status_code: int
    headers: Mapping[str, str] = field(repr=False)
    chunks: Iterator[bytes] = field(repr=False)


class ArtifactHttpTransport(Protocol):
    """Transport contract for bounded-memory artifact downloads and uploads."""

    def stream(
        self,
        method: Literal["GET", "PUT"],
        url: str,
        *,
        headers: Mapping[str, str],
        body: Iterable[bytes] | None,
        content_length: int | None,
        timeout: float,
    ) -> AbstractContextManager[ArtifactHttpResponse]:
        """Open one non-replayed HTTP exchange and keep its response alive in context."""

    def close(self) -> None:
        """Close transport-owned resources; repeated calls are harmless."""


class HttpxArtifactTransport:
    """Stream raw HTTP bytes using an owned, lazy, verified HTTPX client."""

    def __init__(self, *, ca_bundle: str | Path | None = None) -> None:
        ca_file(ca_bundle)
        self._ca_bundle = ca_bundle
        self._backend: _HttpxArtifactBackend | None = None
        self._closed = False
        self._lock = RLock()

    def stream(
        self,
        method: Literal["GET", "PUT"],
        url: str,
        *,
        headers: Mapping[str, str],
        body: Iterable[bytes] | None,
        content_length: int | None,
        timeout: float,
    ) -> AbstractContextManager[ArtifactHttpResponse]:
        if method not in ("GET", "PUT"):
            raise ConfigurationError("Artifact HTTP method must be GET or PUT.")
        target = https_base_url(url)
        seconds = positive_seconds(timeout, "timeout")
        request_headers = self._validated_headers(headers, method)

        if method == "GET":
            if body is not None or content_length is not None:
                raise ConfigurationError("Artifact downloads cannot include a request body.")
        elif (
            body is None
            or not isinstance(body, Iterable)
            or isinstance(body, (str, bytes, bytearray))
            or isinstance(content_length, bool)
            or not isinstance(content_length, int)
            or content_length < 0
        ):
            raise ConfigurationError(
                "Artifact uploads require a byte iterable and a known non-negative content length."
            )

        backend = self._get_backend()
        return backend.stream(
            method,
            target,
            headers=request_headers,
            body=body,
            content_length=content_length,
            timeout=seconds,
        )

    @staticmethod
    def _validated_headers(headers: Mapping[str, str], method: str) -> dict[str, str]:
        if not isinstance(headers, Mapping):
            raise ConfigurationError("Artifact HTTP headers must be a mapping of strings.")

        normalized: dict[str, str] = {}
        seen: set[str] = set()
        forbidden = {"content-length", "transfer-encoding"}
        for name, value in headers.items():
            if not isinstance(name, str) or not isinstance(value, str):
                raise ConfigurationError("Artifact HTTP headers must be a mapping of strings.")
            lowered = name.lower()
            if (
                not name
                or lowered in seen
                or lowered in forbidden
                or any(
                    ord(char) <= 32 or ord(char) >= 127 or char in '()<>@,;:\\"/[]?={}'
                    for char in name
                )
                or any((ord(char) < 32 and char != "\t") or ord(char) == 127 for char in value)
            ):
                raise ConfigurationError(
                    "Artifact HTTP headers contain invalid or reserved values."
                )
            try:
                value.encode("latin-1")
            except UnicodeEncodeError:
                raise ConfigurationError(
                    "Artifact HTTP headers contain invalid or reserved values."
                ) from None
            seen.add(lowered)
            if method == "GET" and lowered == "accept-encoding":
                continue
            normalized[name] = value

        if method == "GET":
            normalized["Accept-Encoding"] = "identity"
        return normalized

    def _get_backend(self) -> _HttpxArtifactBackend:
        with self._lock:
            if self._closed:
                raise TransportError("Artifact HTTP transport is closed.")
            if self._backend is None:
                try:
                    from conductor.artifacts._httpx import _HttpxArtifactBackend

                    self._backend = _HttpxArtifactBackend(ca_bundle=self._ca_bundle)
                except ImportError:
                    raise MissingDependencyError(
                        "Raw artifact HTTP requires the optional dependency conductor[nexus]."
                    ) from None
            return self._backend

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            backend = self._backend
        if backend is not None:
            backend.close()
