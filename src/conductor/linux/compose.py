"""Independent remote Docker Compose commands and explicit image-file edits."""

import json
import re
import time
from typing import Literal

from conductor.common._validation import positive_seconds
from conductor.common.errors import (
    ConfigurationError,
    ConflictError,
    MissingDependencyError,
    PermissionDeniedError,
    ProtocolError,
    ResourceNotFoundError,
    TransportTimeoutError,
)
from conductor.linux._commands import checked_execute
from conductor.linux._scripts import COMPOSE_FILE
from conductor.linux.errors import ComposeFileError, LinuxCommandError
from conductor.linux.models import ComposeImageChangeResult, ComposeOperationResult, ComposeProject
from conductor.transports.models import CommandResult
from conductor.transports.protocols import SshTransport


class DockerCompose:
    """Command APIs only; no down/edit/up workflow, rollback, rsync, or readiness polling."""

    def __init__(self, transport: SshTransport, *, sudo: bool, timeout: float) -> None:
        self._transport = transport
        self._sudo = sudo
        self._timeout = timeout

    def _budget(self, timeout: float | None) -> float:
        return (
            self._timeout
            if timeout is None
            else min(self._timeout, positive_seconds(timeout, "timeout"))
        )

    def _command(
        self, project: ComposeProject, operation: Literal["up", "down"], timeout: float | None
    ) -> ComposeOperationResult:
        arguments = [
            "env",
            "-u",
            "DOCKER_HOST",
            "-u",
            "DOCKER_CONTEXT",
            "-u",
            "COMPOSE_PROFILES",
            "-u",
            "COMPOSE_ENV_FILES",
            "COMPOSE_REMOVE_ORPHANS=false",
            "docker",
            "--host",
            f"unix://{project.docker_socket}",
            "compose",
            "--ansi",
            "never",
            "--file",
            project.compose_file,
            "--project-name",
            project.project_name,
            "--project-directory",
            project.project_directory,
            "--env-file",
            project.env_file or "/dev/null",
            operation,
        ]
        if operation == "up":
            arguments.extend(("--detach", "--no-build"))
        checked_execute(self._transport, arguments, sudo=self._sudo, timeout=self._budget(timeout))
        return ComposeOperationResult(project.project_name, operation)

    def up(
        self, project: ComposeProject, *, timeout: float | None = None
    ) -> ComposeOperationResult:
        """Create/start using detached Compose up --no-build; success is not readiness."""
        return self._command(project, "up", timeout)

    def down(
        self, project: ComposeProject, *, timeout: float | None = None
    ) -> ComposeOperationResult:
        """Stop/remove this project's containers/networks, never request volume/image deletion."""
        return self._command(project, "down", timeout)

    def _file_command(
        self,
        project: ComposeProject,
        action: str,
        timeout: float,
        *,
        fingerprint: str | None = None,
        input: bytes | None = None,
    ) -> CommandResult:
        arguments = ["python3", "-c", COMPOSE_FILE, action, project.compose_file]
        if fingerprint is not None:
            arguments.append(fingerprint)
        try:
            return checked_execute(
                self._transport, arguments, sudo=self._sudo, timeout=timeout, input=input
            )
        except LinuxCommandError as error:
            if error.exit_code == 10:
                raise ResourceNotFoundError("The remote Compose file was not found.") from None
            if error.exit_code == 11:
                raise ConflictError(
                    "The remote Compose file changed or its edit lock is busy."
                ) from None
            if error.exit_code == 13:
                raise PermissionDeniedError("Remote Compose file access was denied.") from None
            if error.exit_code == 14:
                raise ComposeFileError(
                    "The remote Compose file is unsafe or unsupported for editing."
                ) from None
            raise

    def set_image(
        self,
        project: ComposeProject,
        service: str,
        image: str,
        *,
        expected_image: str | None = None,
        timeout: float | None = None,
    ) -> ComposeImageChangeResult:
        """Edit one service image in the remote YAML without restarting anything.

        Requires conductor[linux] on the caller and Python 3 on the target. A
        fingerprint and advisory sibling lock protect cooperating edits; external
        writers must still serialize with this API. Symlinks, hard links, special
        modes, and whole-service YAML anchors/aliases are refused. Comments and
        quotes are retained; whitespace may be normalized. No mutation is replayed.
        """
        for name, value in (("service", service), ("image", image)):
            pattern = (
                r"[A-Za-z0-9][A-Za-z0-9_.-]*"
                if name == "service"
                else r"[A-Za-z0-9][A-Za-z0-9._:/@-]*"
            )
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ConfigurationError(
                    f"{name} must be an explicit literal identifier/reference."
                )
        if expected_image is not None and (
            not isinstance(expected_image, str) or not expected_image.strip()
        ):
            raise ConfigurationError("expected_image must be a non-empty string when supplied.")
        budget = self._budget(timeout)
        try:
            from conductor.linux._yaml import edit_image
        except ImportError:
            raise MissingDependencyError(
                "Install conductor[linux] to edit Compose YAML images."
            ) from None
        deadline = time.monotonic() + budget

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TransportTimeoutError(
                    "Compose image editing exceeded its shared time budget."
                )
            return seconds

        observation = self._file_command(project, "get", remaining())
        try:
            payload = json.loads(observation.stdout)
            if (
                not isinstance(payload, dict)
                or set(payload) != {"content", "fingerprint"}
                or not isinstance(payload["content"], str)
                or len(payload["content"].encode("utf-8")) > 1024 * 1024
                or not isinstance(payload["fingerprint"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload["fingerprint"])
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise ProtocolError("Remote Compose returned an invalid file observation.") from None
        content, previous, changed = edit_image(payload["content"], service, image, expected_image)
        remaining()
        if changed:
            if len(content) > 1024 * 1024:
                raise ComposeFileError("The edited Compose file exceeds the supported size limit.")
            written = self._file_command(
                project, "put", remaining(), fingerprint=payload["fingerprint"], input=content
            )
            remaining()
            try:
                confirmation = json.loads(written.stdout)
                if (
                    not isinstance(confirmation, dict)
                    or set(confirmation) != {"changed"}
                    or confirmation["changed"] is not True
                ):
                    raise ValueError
            except (ValueError, TypeError):
                raise ProtocolError(
                    "Remote Compose returned an invalid edit confirmation."
                ) from None
        return ComposeImageChangeResult(project.compose_file, service, previous, image, changed)
