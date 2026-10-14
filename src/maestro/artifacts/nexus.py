"""Explicit Basic-authenticated Nexus 3 raw hosted artifact operations."""

from __future__ import annotations

import base64
import logging
import re
import unicodedata
from collections.abc import Generator, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Literal, cast
from urllib.parse import quote

from maestro.artifacts._files import file_chunks, snapshot_file, write_file
from maestro.artifacts._validation import validate_key, validate_sha256
from maestro.artifacts.errors import ArtifactFileError
from maestro.artifacts.models import ArtifactInfo
from maestro.artifacts.transport import ArtifactHttpTransport, HttpxArtifactTransport
from maestro.common._validation import (
    ca_file,
    https_base_url,
    non_empty,
    path_segment,
    positive_seconds,
)
from maestro.common.errors import (
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    HttpResponseError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    ResourceNotFoundError,
    TransportError,
)

_LOGGER = logging.getLogger(__name__)
_DECIMAL_LENGTH = re.compile(r"[0-9]+\Z", re.ASCII)


@dataclass(frozen=True)
class NexusRawConfig:
    """Explicit Nexus raw-repository endpoint and Basic credentials."""

    base_url: str
    repository: str
    username: str = field(repr=False)
    password: str = field(repr=False)
    timeout: float = 30.0
    ca_bundle: str | Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", https_base_url(self.base_url))
        path_segment(self.repository, "repository")
        object.__setattr__(self, "timeout", positive_seconds(self.timeout, "timeout"))
        ca_file(self.ca_bundle)

        username = _validate_username(self.username)
        password = _validate_password(self.password)
        _validate_utf8_credentials(username, password)


def _validate_username(value: str) -> str:
    username = non_empty(value, "username")
    if ":" in username or any(unicodedata.category(char) == "Cc" for char in username):
        raise ConfigurationError("username must not contain a colon or control characters.")
    return username


def _validate_password(value: str) -> str:
    if not isinstance(value, str) or value == "":
        raise ConfigurationError("password must be a non-empty string.")
    return value


def _validate_utf8_credentials(username: str, password: str) -> None:
    try:
        username.encode("utf-8")
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise ConfigurationError(
            "Basic authentication credentials must be valid Unicode text."
        ) from None


class NexusRawArtifacts:
    """Publish local snapshots and retrieve verified bytes from one Nexus raw repo."""

    def __init__(
        self,
        config: NexusRawConfig,
        *,
        transport: ArtifactHttpTransport | None = None,
    ) -> None:
        if not isinstance(config, NexusRawConfig):
            raise ConfigurationError("config must be a NexusRawConfig instance.")
        self.config = config
        self._transport = (
            transport
            if transport is not None
            else HttpxArtifactTransport(ca_bundle=config.ca_bundle)
        )
        self._owns_transport = transport is None
        self._closed = False

    def __enter__(self) -> NexusRawArtifacts:
        self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback_object: TracebackType | None,
    ) -> Literal[False]:
        try:
            self.close()
        except Exception:
            if exc is None:
                raise
            _LOGGER.warning("Nexus artifact cleanup failed; the original error is preserved.")
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_transport:
            self._transport.close()

    def publish(
        self,
        source: str | Path,
        key: str,
        *,
        expected_sha256: str | None = None,
        timeout: float | None = None,
    ) -> ArtifactInfo:
        self._ensure_open()
        canonical_key = validate_key(key)
        digest = validate_sha256(expected_sha256) if expected_sha256 is not None else None
        phase_timeout = self._phase_timeout(timeout)
        snapshot_context = snapshot_file(source, canonical_key, expected_sha256=digest)

        with snapshot_context as (snapshot, artifact):
            upload_chunks = cast(Generator[bytes, None, None], file_chunks(snapshot))
            try:
                with self._transport.stream(
                    "PUT",
                    self._url(canonical_key),
                    headers=self._request_headers(),
                    body=upload_chunks,
                    content_length=artifact.size,
                    timeout=phase_timeout,
                ) as response:
                    _require_status(response.status_code, 201)
            except BaseException as primary_error:
                _close_upload_chunks(upload_chunks, primary_error=primary_error)
                raise
            else:
                _close_upload_chunks(upload_chunks, primary_error=None)
            return artifact

    def retrieve(
        self,
        key: str,
        destination: str | Path,
        *,
        expected_sha256: str,
        overwrite: bool = False,
        timeout: float | None = None,
    ) -> ArtifactInfo:
        self._ensure_open()
        canonical_key = validate_key(key)
        digest = validate_sha256(expected_sha256)
        _validate_overwrite(overwrite)
        _validate_destination_argument(destination)
        phase_timeout = self._phase_timeout(timeout)

        with self._transport.stream(
            "GET",
            self._url(canonical_key),
            headers=self._request_headers(),
            body=None,
            content_length=None,
            timeout=phase_timeout,
        ) as response:
            _require_status(response.status_code, 200)
            _require_identity_encoding(response.headers)
            expected_size = _response_content_length(response.headers)
            return write_file(
                response.chunks,
                destination,
                canonical_key,
                expected_sha256=digest,
                expected_size=expected_size,
                overwrite=overwrite,
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise TransportError("Nexus raw artifact client is closed.")

    def _phase_timeout(self, override: float | None) -> float:
        if override is None:
            return self.config.timeout
        return min(self.config.timeout, positive_seconds(override, "timeout"))

    def _request_headers(self) -> dict[str, str]:
        credentials = f"{self.config.username}:{self.config.password}".encode()
        encoded = base64.b64encode(credentials).decode("ascii")
        return {
            "Authorization": f"Basic {encoded}",
            "Content-Type": "application/octet-stream",
        }

    def _url(self, key: str) -> str:
        repository = path_segment(self.config.repository, "repository")
        encoded_key = "/".join(quote(component, safe="") for component in key.split("/"))
        return f"{self.config.base_url}/repository/{repository}/{encoded_key}"


def _require_status(status_code: int, expected: int) -> None:
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise ProtocolError("Nexus returned an invalid HTTP status.")
    if status_code == expected:
        return
    if status_code == 401:
        raise AuthenticationError("Nexus rejected the configured credentials.")
    if status_code == 403:
        raise PermissionDeniedError("Nexus denied access to the raw repository.")
    if status_code == 404:
        raise ResourceNotFoundError("The requested Nexus raw artifact was not found.")
    if status_code in (409, 412):
        raise ConflictError("The Nexus raw artifact operation conflicted with repository state.")
    if status_code == 429:
        raise RateLimitError(status_code)
    if 200 <= status_code < 300 or 300 <= status_code < 400:
        raise ProtocolError(
            "Nexus returned an unexpected HTTP status for a raw artifact operation."
        )
    raise HttpResponseError(status_code)


def _header_values(headers: Mapping[str, str], name: str) -> list[str]:
    values: list[str] = []
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ProtocolError("Nexus returned invalid response headers.")
        if key.lower() == name:
            values.append(value)
    if len(values) > 1:
        raise ProtocolError("Nexus returned duplicate response headers.")
    return values


def _require_identity_encoding(headers: Mapping[str, str]) -> None:
    values = _header_values(headers, "content-encoding")
    if values and values[0].strip().lower() != "identity":
        raise ProtocolError("Nexus returned an encoded raw artifact response.")


def _response_content_length(headers: Mapping[str, str]) -> int | None:
    values = _header_values(headers, "content-length")
    if not values:
        return None
    value = values[0].strip()
    if _DECIMAL_LENGTH.fullmatch(value) is None:
        raise ProtocolError("Nexus returned an invalid Content-Length header.")
    try:
        return int(value)
    except ValueError:
        raise ProtocolError("Nexus returned an invalid Content-Length header.") from None


def _validate_overwrite(overwrite: bool) -> None:
    if not isinstance(overwrite, bool):
        raise ConfigurationError("overwrite must be a boolean.")


def _validate_destination_argument(destination: str | Path) -> None:
    if (
        not isinstance(destination, (str, Path))
        or not str(destination)
        or "\x00" in str(destination)
    ):
        raise ConfigurationError("destination must be a valid local path.")
    try:
        parts = Path(destination).parts
    except (OSError, ValueError):
        raise ConfigurationError("destination must be a valid local path.") from None
    if any(part == ".." for part in parts):
        raise ConfigurationError("destination must not contain parent traversal.")


def _close_upload_chunks(
    chunks: Generator[bytes, None, None], *, primary_error: BaseException | None
) -> None:
    try:
        chunks.close()
    except BaseException:
        if primary_error is None:
            raise ArtifactFileError() from None
        with suppress(BaseException):
            _LOGGER.warning("Nexus upload stream cleanup failed; the original error is preserved.")
