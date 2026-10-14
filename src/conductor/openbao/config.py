"""Explicit OpenBao server and caller-supplied authentication configuration."""

from dataclasses import dataclass, field
from pathlib import Path

from conductor.common._validation import ca_file, https_base_url, positive_seconds, secret_token
from conductor.common.errors import ConfigurationError


@dataclass(frozen=True)
class OpenBaoConfig:
    """Configure one HTTPS OpenBao target; authentication lifecycle stays with the caller."""

    server_url: str
    token: str = field(repr=False)
    namespace: str | None = None
    timeout: float = 30.0
    ca_bundle: str | Path | None = None

    def __post_init__(self) -> None:
        https_base_url(self.server_url)
        secret_token(self.token)
        if self.namespace is not None and (
            not isinstance(self.namespace, str)
            or not self.namespace
            or any(not 33 <= ord(char) <= 126 for char in self.namespace)
        ):
            raise ConfigurationError(
                "namespace must be non-empty printable ASCII without whitespace."
            )
        ca_file(self.ca_bundle)
        object.__setattr__(self, "timeout", positive_seconds(self.timeout, "timeout"))
