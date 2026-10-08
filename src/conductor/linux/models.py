"""Library-owned Linux service, file-transfer, and Compose contracts."""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from conductor.common._validation import remote_posix_path
from conductor.common.errors import ConfigurationError


class ServiceState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    ACTIVATING = "activating"
    DEACTIVATING = "deactivating"
    RELOADING = "reloading"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ServiceOperationResult:
    """The requested systemctl command exited successfully, not an application health check."""

    name: str
    operation: Literal["start", "stop"]


@dataclass(frozen=True)
class FileUploadResult:
    """Rsync completed (or previewed); no claim that the file changed or the app restarted."""

    source: str = field(repr=False)
    destination: str = field(repr=False)
    dry_run: bool


@dataclass(frozen=True)
class ComposeProject:
    """One explicit remote Compose file/project and local-to-that-host Docker socket.

    Only Docker Compose v2 is supported. No automatic file discovery or implicit
    .env loading; env_file is explicit, otherwise /dev/null is passed instead.
    """

    compose_file: str
    project_name: str
    env_file: str | None = None
    docker_socket: str = "/var/run/docker.sock"

    def __post_init__(self) -> None:
        remote_posix_path(self.compose_file, "compose_file", file=True)
        remote_posix_path(self.docker_socket, "docker_socket", file=True)
        if self.env_file is not None:
            remote_posix_path(self.env_file, "env_file", file=True)
        if not isinstance(self.project_name, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*", self.project_name
        ):
            raise ConfigurationError(
                "project_name must be a literal lowercase Compose project name."
            )

    @property
    def project_directory(self) -> str:
        return str(PurePosixPath(self.compose_file).parent)


@dataclass(frozen=True)
class ComposeOperationResult:
    """The Compose command exited successfully; up does not promise readiness."""

    project_name: str
    operation: Literal["up", "down"]


@dataclass(frozen=True)
class ComposeImageChangeResult:
    """An explicit file edit only; it never pulls images, restarts, or uses rsync."""

    compose_file: str = field(repr=False)
    service: str
    previous_image: str | None = field(repr=False)
    image: str = field(repr=False)
    changed: bool
