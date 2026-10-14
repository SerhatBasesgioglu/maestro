"""Sanitized failures for independent remote Linux operations."""

from maestro.common.errors import MaestroError, ProtocolError, ResourceNotFoundError


class LinuxCommandError(MaestroError):
    """A remote command failed; stderr/stdout and command arguments are never included."""

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        super().__init__(f"Remote Linux command failed with exit status {exit_code}.")


class ServiceNotFoundError(ResourceNotFoundError):
    """The explicitly named systemd service is not loaded/found."""


class RsyncError(MaestroError):
    """A transfer failed or was partial; it is never replayed or automatically rolled back."""

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        super().__init__(f"Rsync upload failed with exit status {exit_code}.")


class ComposeFileError(ProtocolError):
    """Compose YAML/file layout is malformed or unsafe for the supported edit contract."""
