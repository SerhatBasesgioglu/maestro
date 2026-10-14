"""Private, lazy HTTPX adapter for raw artifact streaming."""

from __future__ import annotations

import logging
import ssl
import sys
from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NoReturn

if TYPE_CHECKING:
    import httpx

from conductor.artifacts.transport import ArtifactHttpResponse
from conductor.common.errors import (
    ConfigurationError,
    TlsError,
    TransportError,
    TransportTimeoutError,
)

_LOGGER = logging.getLogger(__name__)
_RAW_CHUNK_SIZE = 1024 * 1024


def _is_tls_failure(exc: BaseException) -> bool:
    for _ in range(20):
        if isinstance(exc, ssl.SSLError):
            return True
        cause = exc.__cause__ or exc.__context__
        if cause is None:
            return False
        exc = cause
    return False


def _raise_request_error(exc: httpx.RequestError) -> NoReturn:
    if _is_tls_failure(exc):
        raise TlsError("Remote TLS verification or negotiation failed.") from None
    raise TransportError(
        "HTTP communication failed; a dispatched write may have completed."
    ) from None


def _body_chunks(body: Iterable[bytes], content_length: int) -> Iterator[bytes]:
    total = 0
    for chunk in body:
        if not isinstance(chunk, bytes):
            raise ConfigurationError("Artifact upload chunks must be bytes.")
        total += len(chunk)
        if total > content_length:
            raise ConfigurationError("Artifact upload body exceeds its declared length.")
        yield chunk
    if total != content_length:
        raise ConfigurationError("Artifact upload body does not match its declared length.")


class _HttpxArtifactBackend:
    """Own one verified HTTPX client, imported only when artifact I/O is requested."""

    def __init__(self, *, ca_bundle: str | Path | None) -> None:
        import httpx

        self._httpx = httpx
        try:
            context = ssl.create_default_context(
                cafile=str(ca_bundle) if ca_bundle is not None else None
            )
            self._client = httpx.Client(
                verify=context,
                trust_env=False,
                follow_redirects=False,
            )
        except (OSError, ValueError):
            raise ConfigurationError("HTTP TLS trust configuration could not be loaded.") from None

    @contextmanager
    def stream(
        self,
        method: Literal["GET", "PUT"],
        url: str,
        *,
        headers: Mapping[str, str],
        body: Iterable[bytes] | None,
        content_length: int | None,
        timeout: float,
    ) -> Generator[ArtifactHttpResponse, None, None]:
        content: Iterable[bytes] | None = None
        request_headers = dict(headers)
        if method == "PUT":
            assert body is not None and content_length is not None
            request_headers["Content-Length"] = str(content_length)
            content = _body_chunks(body, content_length)

        response_context = self._client.stream(
            method,
            url,
            headers=request_headers,
            content=content,
            timeout=timeout,
            follow_redirects=False,
        )
        try:
            response = response_context.__enter__()
        except self._httpx.TimeoutException:
            raise TransportTimeoutError(
                "HTTP request timed out; a dispatched write may have completed."
            ) from None
        except self._httpx.RequestError as exc:
            _raise_request_error(exc)
        except self._httpx.InvalidURL:
            raise ConfigurationError("The artifact HTTP request target is invalid.") from None

        result = ArtifactHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            chunks=self._response_chunks(response),
        )
        try:
            yield result
        except BaseException:
            try:
                response_context.__exit__(*sys.exc_info())
            except Exception:
                _LOGGER.warning(
                    "Artifact HTTP response cleanup failed; the original error is preserved."
                )
            raise
        else:
            try:
                response_context.__exit__(None, None, None)
            except self._httpx.TimeoutException:
                raise TransportTimeoutError("HTTP response cleanup timed out.") from None
            except self._httpx.RequestError as exc:
                _raise_request_error(exc)
            except Exception:
                raise TransportError("HTTP response cleanup failed.") from None

    def _response_chunks(self, response: httpx.Response) -> Iterator[bytes]:
        raw = iter(response.iter_raw(chunk_size=_RAW_CHUNK_SIZE))
        while True:
            try:
                chunk = next(raw)
            except StopIteration:
                return
            except self._httpx.TimeoutException:
                raise TransportTimeoutError(
                    "HTTP response streaming timed out; a dispatched write may have completed."
                ) from None
            except self._httpx.RequestError as exc:
                _raise_request_error(exc)
            yield chunk

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            raise TransportError("HTTP connection cleanup failed.") from None
