"""Configured Azure DevOps entry point with independent resource APIs."""

import base64
from types import TracebackType
from typing import Self

from conductor.azure_devops.config import AzureDevOpsConfig
from conductor.azure_devops.pipelines import Pipelines
from conductor.azure_devops.repositories import Repositories
from conductor.common.http import HttpTransport, _JsonConnection


class AzureDevOpsClient:
    """Synchronous Git/pipeline client; owns default HTTP, borrows injected transports.

    Construction/import performs no network I/O. Use with/close for owned HTTP
    connections. Instances are not thread-safe; authorization belongs to callers.
    """

    def __init__(
        self, config: AzureDevOpsConfig, *, transport: HttpTransport | None = None
    ) -> None:
        self.config = config
        authorization = (
            "Basic " + base64.b64encode(f":{config.token}".encode()).decode("ascii")
            if config.auth == "pat"
            else f"Bearer {config.token}"
        )
        self._connection = _JsonConnection(
            base_url=config.organization_url,
            headers={"Authorization": authorization},
            timeout=config.timeout,
            ca_bundle=config.ca_bundle,
            transport=transport,
        )
        self.repositories = Repositories(self._connection, config)
        self.pipelines = Pipelines(self._connection, config)

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
