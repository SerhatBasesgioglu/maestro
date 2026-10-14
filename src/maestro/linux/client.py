"""Borrowed-transport entry point for independent Linux APIs."""

from maestro.common._validation import positive_seconds
from maestro.common.errors import ConfigurationError
from maestro.linux.compose import DockerCompose
from maestro.linux.files import RsyncFiles
from maestro.linux.services import SystemdServices
from maestro.transports.protocols import SshTransport


class LinuxClient:
    """Independent service, legacy-file, and Compose APIs, not a deployment service.

    Borrow the transport without opening/closing it. sudo explicitly requests
    non-interactive elevation for systemd/Compose/file edits, never rsync uploads.
    The timeout is a per-operation ceiling, also limited by the transport ceiling.
    """

    def __init__(
        self, *, transport: SshTransport, sudo: bool = False, timeout: float = 60.0
    ) -> None:
        if type(sudo) is not bool:
            raise ConfigurationError("sudo must be a boolean.")
        budget = positive_seconds(timeout, "timeout")
        self.services = SystemdServices(transport, sudo=sudo, timeout=budget)
        self.files = RsyncFiles(transport, timeout=budget)
        self.compose = DockerCompose(transport, sudo=sudo, timeout=budget)
