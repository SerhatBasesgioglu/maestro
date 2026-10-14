"""Common command validation, not deployment orchestration."""

from collections.abc import Sequence

from maestro.linux.errors import LinuxCommandError
from maestro.transports.models import CommandResult
from maestro.transports.protocols import SshTransport


def checked_execute(
    transport: SshTransport,
    arguments: Sequence[str],
    *,
    sudo: bool,
    timeout: float,
    input: bytes | None = None,
) -> CommandResult:
    command = ["sudo", "-n", "--", *arguments] if sudo else list(arguments)
    result = transport.execute(command, input=input, timeout=timeout)
    if result.exit_code != 0:
        raise LinuxCommandError(result.exit_code)
    return result
