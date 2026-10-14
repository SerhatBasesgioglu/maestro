"""Windows Service Control Manager exception categories."""

from maestro.common.errors import MaestroError, ResourceNotFoundError


class WindowsServiceError(MaestroError):
    """A Windows Service Control Manager operation failed."""


class ServiceNotFoundError(WindowsServiceError, ResourceNotFoundError):
    """The requested service name does not exist on the remote target."""


class ServiceStateError(WindowsServiceError):
    """The observed service state does not permit the requested operation."""


class ServiceCommandError(WindowsServiceError):
    """A remote service-management command failed for another reason."""


class ServiceProtocolError(WindowsServiceError):
    """The remote command returned an unexpected library-owned schema."""


class ServiceDependencyError(WindowsServiceError):
    """Dependent services prevent the requested service from stopping."""


class ServiceDisabledError(WindowsServiceError):
    """The requested service is disabled and cannot be started."""
