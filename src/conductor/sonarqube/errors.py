"""SonarQube-specific resource failures."""

from conductor.common.errors import ConductorError


class SonarQubeError(ConductorError):
    """A SonarQube resource operation failed."""
