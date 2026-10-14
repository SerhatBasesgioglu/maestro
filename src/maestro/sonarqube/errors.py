"""SonarQube-specific resource failures."""

from maestro.common.errors import MaestroError


class SonarQubeError(MaestroError):
    """A SonarQube resource operation failed."""
