"""A small synchronous test seam for remote PowerShell execution."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from maestro.transports.models import CommandResult, PowerShellResult, PowerShellValue


class PowerShellTransport(Protocol):
    """A configured remote transport, owned and opened by its caller.

    Implementations must bind parameters as data, honor finite time budgets,
    avoid automatic write replays, and raise Maestro's transport exceptions.
    """

    def execute(
        self,
        script: str,
        *,
        parameters: Mapping[str, PowerShellValue] | None = None,
        timeout: float | None = None,
    ) -> PowerShellResult:
        """Execute trusted source on the explicit remote target, never locally."""
        ...


class SshTransport(Protocol):
    """Borrowed remote command/file seam; no local fallback or automatic write replays.

    Implementers own host-key verification, credentials, argument quoting, and
    bounded process lifetime. Uploads must use SSH, never a plain rsync daemon.
    """

    def execute(
        self,
        arguments: Sequence[str],
        *,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult: ...

    def upload(
        self,
        source: str | Path,
        destination: str,
        *,
        dry_run: bool = False,
        timeout: float | None = None,
    ) -> CommandResult: ...
