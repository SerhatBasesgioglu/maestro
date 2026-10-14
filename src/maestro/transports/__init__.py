"""Optional transports; importing this module does not import SDKs or open connections."""

from maestro.transports.models import (
    CommandResult,
    PowerShellError,
    PowerShellErrorCategory,
    PowerShellResult,
    PowerShellValue,
)
from maestro.transports.powershell_remoting import PowerShellRemoting, PowerShellRemotingConfig
from maestro.transports.protocols import PowerShellTransport, SshTransport
from maestro.transports.ssh import OpenSshTransport, SshConfig

__all__ = [
    "CommandResult",
    "OpenSshTransport",
    "PowerShellError",
    "PowerShellErrorCategory",
    "PowerShellRemoting",
    "PowerShellRemotingConfig",
    "PowerShellResult",
    "PowerShellTransport",
    "PowerShellValue",
    "SshConfig",
    "SshTransport",
]
