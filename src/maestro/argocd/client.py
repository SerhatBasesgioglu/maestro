"""Configured Argo CD application API, without a deployment workflow dependency."""

from types import TracebackType
from typing import Self

from maestro.argocd.applications import Applications
from maestro.argocd.config import ArgoCdConfig
from maestro.common.http import HttpTransport, _JsonConnection


class ArgoCdClient:
    """Synchronous client with explicit targets and owned default/borrowed injected HTTP."""

    def __init__(self, config: ArgoCdConfig, *, transport: HttpTransport | None = None) -> None:
        self.config = config
        self._connection = _JsonConnection(
            base_url=config.server_url,
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=config.timeout,
            ca_bundle=config.ca_bundle,
            transport=transport,
        )
        self.applications = Applications(self._connection, config)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.exit(exc_type)

    def close(self) -> None:
        self._connection.close()
