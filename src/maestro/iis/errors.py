"""IIS-specific exceptions with reusable shared categories."""

from maestro.common.errors import (
    MaestroError,
    OperationTimeoutError,
    PermissionDeniedError,
    ResourceNotFoundError,
)
from maestro.iis.models import ApplicationPoolState


class IisError(MaestroError):
    """An IIS resource operation failed."""


class ApplicationPoolNotFoundError(IisError, ResourceNotFoundError):
    """The requested application pool does not exist on the remote target."""


class IisPermissionDeniedError(IisError, PermissionDeniedError):
    """The remote identity is not allowed to manage the requested IIS resource."""


class IisCommandError(IisError):
    """An IIS management command failed for a reason other than missing/denied."""


class IisProtocolError(IisError):
    """The remote command did not return the expected library-owned schema."""


class ApplicationPoolStateError(IisError):
    """IIS reported UNKNOWN; no automatic state-changing command is safe."""


class ApplicationPoolTimeoutError(IisError, OperationTimeoutError):
    """Polling exhausted its time budget before confirming the desired state."""

    def __init__(
        self,
        name: str,
        desired_state: ApplicationPoolState,
        last_state: ApplicationPoolState | None,
        timeout: float,
    ) -> None:
        # Resource names are caller data; keep them out of exception messages.
        self.name = name
        self.desired_state = desired_state
        self.last_state = last_state
        self.timeout = timeout
        super().__init__(
            f"Application pool did not reach {desired_state.value} within {timeout:g}s."
        )
