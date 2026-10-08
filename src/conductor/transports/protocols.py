"""A small synchronous test seam for remote PowerShell execution."""

from collections.abc import Mapping
from typing import Protocol

from conductor.transports.models import PowerShellResult, PowerShellValue


class PowerShellTransport(Protocol):
    """A configured remote transport, owned and opened by its caller.

    Implementations must bind parameters as data, honor finite time budgets,
    avoid automatic write replays, and raise Conductor's transport exceptions.
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
