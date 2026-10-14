"""Private HTTPX adapter; import only when an HTTP integration is used for real I/O."""

import ssl
from collections.abc import Mapping
from pathlib import Path

import httpx

from maestro.common.errors import (
    ConfigurationError,
    TlsError,
    TransportError,
    TransportTimeoutError,
)
from maestro.common.http import HttpMethod, HttpResponse, JsonValue


def _is_tls_failure(exc: BaseException) -> bool:
    for _ in range(20):
        if isinstance(exc, ssl.SSLError):
            return True
        cause = exc.__cause__ or exc.__context__
        if cause is None:
            return False
        exc = cause
    return False


class _HttpxBackend:
    def __init__(self, *, ca_bundle: str | Path | None) -> None:
        try:
            context = ssl.create_default_context(
                cafile=str(ca_bundle) if ca_bundle is not None else None
            )
            self._client = httpx.Client(verify=context, trust_env=False, follow_redirects=False)
        except (OSError, ValueError):
            raise ConfigurationError("HTTP TLS trust configuration could not be loaded.") from None

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
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                timeout=timeout,
                follow_redirects=False,
            )
            return HttpResponse(response.status_code, response.content, dict(response.headers))
        except httpx.TimeoutException:
            raise TransportTimeoutError(
                "HTTP request timed out; a dispatched write may have completed."
            ) from None
        except httpx.RequestError as exc:
            if _is_tls_failure(exc):
                raise TlsError("Remote TLS verification or negotiation failed.") from None
            raise TransportError(
                "HTTP communication failed; a dispatched write may have completed."
            ) from None
        except (httpx.InvalidURL, ValueError, TypeError):
            raise ConfigurationError("The HTTP request could not be encoded.") from None

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            raise TransportError("HTTP connection cleanup failed.") from None
