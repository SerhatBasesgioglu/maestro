"""Argo CD application-specific failures."""

from conductor.argocd.models import Application
from conductor.common.errors import ConductorError, OperationTimeoutError


class ArgoCdError(ConductorError):
    """An Argo CD resource operation failed."""


class ApplicationHealthTimeoutError(ArgoCdError, OperationTimeoutError):
    """Requested health/sync/revision conditions were not confirmed in time."""

    def __init__(self, name: str, timeout: float, last_application: Application | None) -> None:
        self.name = name
        self.timeout = timeout
        self.last_application = last_application
        super().__init__(f"Application health conditions were not reached within {timeout:g}s.")
