"""Explicit Azure DevOps organization/project and platform-specific token authentication."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from maestro.common._validation import (
    ca_file,
    https_base_url,
    path_segment,
    positive_seconds,
    secret_token,
)
from maestro.common.errors import ConfigurationError


@dataclass(frozen=True)
class AzureDevOpsConfig:
    """Use an organization URL, or an on-premises collection URL, and an explicit project.

    PATs use Basic authentication with an empty username; bearer tokens are supplied
    by the consumer. Credentials are never discovered from a pipeline environment.
    REST 7.1 is the default; older servers need a supported explicit API version.
    """

    organization_url: str
    project: str
    token: str = field(repr=False)
    auth: Literal["pat", "bearer"] = "pat"
    api_version: str = "7.1"
    timeout: float = 30.0
    ca_bundle: str | Path | None = None

    def __post_init__(self) -> None:
        https_base_url(self.organization_url)
        path_segment(self.project, "project")
        secret_token(self.token)
        if self.auth not in ("pat", "bearer"):
            raise ConfigurationError("Azure DevOps auth must be pat or bearer.")
        if not isinstance(self.api_version, str) or not re.fullmatch(
            r"\d+\.\d+(?:-preview(?:\.\d+)?)?", self.api_version
        ):
            raise ConfigurationError("api_version must be an explicit REST API version.")
        ca_file(self.ca_bundle)
        object.__setattr__(self, "timeout", positive_seconds(self.timeout, "timeout"))
