"""Sanitized failures raised by artifact filesystem operations."""

from conductor.common.errors import ConductorError


class ArtifactIntegrityError(ConductorError):
    """Artifact bytes did not match the caller's integrity expectations."""

    def __init__(self) -> None:
        super().__init__("Artifact integrity verification failed.")


class ArtifactFileError(ConductorError):
    """A local artifact file operation failed; its final state may be uncertain."""

    def __init__(self) -> None:
        super().__init__("Artifact file operation failed; the artifact may be present.")
