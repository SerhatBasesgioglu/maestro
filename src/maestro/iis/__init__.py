"""Remote IIS application-pool operations; no local IIS or SDK is needed to import."""

from maestro.iis.client import IisClient
from maestro.iis.errors import (
    ApplicationPoolNotFoundError,
    ApplicationPoolStateError,
    ApplicationPoolTimeoutError,
    IisCommandError,
    IisError,
    IisPermissionDeniedError,
    IisProtocolError,
)
from maestro.iis.models import ApplicationPoolChangeResult, ApplicationPoolState

__all__ = [
    "ApplicationPoolChangeResult",
    "ApplicationPoolNotFoundError",
    "ApplicationPoolState",
    "ApplicationPoolStateError",
    "ApplicationPoolTimeoutError",
    "IisClient",
    "IisCommandError",
    "IisError",
    "IisPermissionDeniedError",
    "IisProtocolError",
]
