"""Independent systemd read/start/stop calls; no deployment or restoration workflow."""

import re
from typing import Literal

from conductor.common._validation import positive_seconds
from conductor.common.errors import ConfigurationError, ProtocolError
from conductor.linux._commands import checked_execute
from conductor.linux.errors import ServiceNotFoundError
from conductor.linux.models import ServiceOperationResult, ServiceState
from conductor.transports.protocols import SshTransport


def _unit(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]*", name):
        raise ConfigurationError(
            "name must be a literal systemd service name, not a path or pattern."
        )
    unit = name if name.endswith(".service") else f"{name}.service"
    return unit


class SystemdServices:
    """Command-level systemd APIs; blocking systemctl success is not app health.

    Native systemctl handles already-satisfied states. No pre-read, polling,
    deployment sequencing, restoration, authorization, or write replay is added.
    """

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

    def get_state(self, name: str, *, timeout: float | None = None) -> ServiceState:
        unit = _unit(name)
        result = checked_execute(
            self._transport,
            ["systemctl", "show", "--property=LoadState", "--property=ActiveState", "--", unit],
            sudo=self._sudo,
            timeout=self._budget(timeout),
        )
        properties: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key in properties or key not in ("LoadState", "ActiveState"):
                raise ProtocolError("systemd returned an invalid service observation.")
            properties[key] = value
        if set(properties) != {"LoadState", "ActiveState"}:
            raise ProtocolError("systemd returned an incomplete service observation.")
        if properties["LoadState"] == "not-found":
            raise ServiceNotFoundError("The requested remote systemd service was not found.")
        try:
            return ServiceState(properties["ActiveState"])
        except ValueError:
            return ServiceState.UNKNOWN

    def start(self, name: str, *, timeout: float | None = None) -> ServiceOperationResult:
        """Dispatch systemctl start once; caller must approve the mutation first."""
        return self._change(name, "start", timeout)

    def stop(self, name: str, *, timeout: float | None = None) -> ServiceOperationResult:
        """Dispatch systemctl stop once; caller owns any later restoration."""
        return self._change(name, "stop", timeout)

    def _change(
        self, name: str, operation: Literal["start", "stop"], timeout: float | None
    ) -> ServiceOperationResult:
        unit = _unit(name)
        checked_execute(
            self._transport,
            ["systemctl", operation, "--", unit],
            sudo=self._sudo,
            timeout=self._budget(timeout),
        )
        return ServiceOperationResult(unit, operation)
