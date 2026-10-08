"""Explicit SonarQube target and user bearer token; no scanner/pipeline discovery."""

from dataclasses import dataclass, field
from pathlib import Path

from conductor.common._validation import ca_file, https_base_url, positive_seconds, secret_token


@dataclass(frozen=True)
class SonarQubeConfig:
    """Targets the Server Web API component/quality-gate endpoints with bearer authentication.

    The consuming application supplies and rotates an appropriate token. API V2
    migration and supported server versions need target-specific verification.
    """

    server_url: str
    token: str = field(repr=False)
    timeout: float = 30.0
    ca_bundle: str | Path | None = None

    def __post_init__(self) -> None:
        https_base_url(self.server_url)
        secret_token(self.token)
        ca_file(self.ca_bundle)
        object.__setattr__(self, "timeout", positive_seconds(self.timeout, "timeout"))
