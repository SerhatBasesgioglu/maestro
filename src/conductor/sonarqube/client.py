"""Configured SonarQube project/quality-gate entry point, independent of scanner execution."""

from types import TracebackType
from typing import Self

from conductor.common.http import HttpTransport, _JsonConnection
from conductor.sonarqube.config import SonarQubeConfig
from conductor.sonarqube.projects import Projects
from conductor.sonarqube.quality_gates import QualityGates


class SonarQubeClient:
    """Synchronous client; owns default HTTP and borrows injected transports."""

    def __init__(self, config: SonarQubeConfig, *, transport: HttpTransport | None = None) -> None:
        self.config = config
        self._connection = _JsonConnection(
            base_url=config.server_url,
            headers={"Authorization": f"Bearer {config.token}"},
            timeout=config.timeout,
            ca_bundle=config.ca_bundle,
            transport=transport,
        )
        self.projects = Projects(self._connection)
        self.quality_gates = QualityGates(self._connection)

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
