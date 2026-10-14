"""Remote Windows Service Control Manager operations; no local Windows APIs."""

from maestro.windows.client import WindowsClient
from maestro.windows.errors import (
    ServiceCommandError,
    ServiceDependencyError,
    ServiceDisabledError,
    ServiceNotFoundError,
    ServiceProtocolError,
    ServiceStateError,
)
from maestro.windows.models import ServiceChangeResult, ServiceState

__all__ = [
    "ServiceChangeResult",
    "ServiceCommandError",
    "ServiceDependencyError",
    "ServiceDisabledError",
    "ServiceNotFoundError",
    "ServiceProtocolError",
    "ServiceState",
    "ServiceStateError",
    "WindowsClient",
]
