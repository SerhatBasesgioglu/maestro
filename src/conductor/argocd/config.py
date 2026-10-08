"""Explicit Argo CD server, bearer token, AppProject, and optional application namespace."""

from dataclasses import dataclass, field
from pathlib import Path

from conductor.common._validation import (
    ca_file,
    https_base_url,
    non_empty,
    positive_seconds,
    secret_token,
)


@dataclass(frozen=True)
class ArgoCdConfig:
    """The project is explicit to preserve Argo CD's 403/404 resource-security semantics.

    application_namespace selects Applications outside the server's control-plane
    namespace. When omitted, the server's normal application namespace is used.
    The consumer supplies/refreshes the token; this client does not log in globally.
    """

    server_url: str
    project: str
    token: str = field(repr=False)
    application_namespace: str | None = None
    timeout: float = 30.0
    ca_bundle: str | Path | None = None

    def __post_init__(self) -> None:
        https_base_url(self.server_url)
        non_empty(self.project, "project")
        secret_token(self.token)
        if self.application_namespace is not None:
            non_empty(self.application_namespace, "application_namespace")
        ca_file(self.ca_bundle)
        object.__setattr__(self, "timeout", positive_seconds(self.timeout, "timeout"))
