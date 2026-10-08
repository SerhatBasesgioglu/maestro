"""Remote Windows Service Control Manager operations; no local Windows APIs."""

from conductor.windows.client import WindowsClient
from conductor.windows.errors import (
    ServiceCommandError,
    ServiceDependencyError,
    ServiceDisabledError,
    ServiceNotFoundError,
    ServiceProtocolError,
    ServiceStateError,
)
from conductor.windows.models import ServiceChangeResult, ServiceState

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
