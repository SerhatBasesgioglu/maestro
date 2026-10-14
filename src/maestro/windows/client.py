"""The configured Windows service entry point, without transport ownership."""

from maestro.transports.protocols import PowerShellTransport
from maestro.windows.services import WindowsServices


class WindowsClient:
    """Borrow a caller-owned remote PowerShell transport; never open or close it."""

    def __init__(self, *, transport: PowerShellTransport, timeout: float = 60.0) -> None:
        self.services = WindowsServices(
            transport=transport,
            timeout=timeout,
        )
