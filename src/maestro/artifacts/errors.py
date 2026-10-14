"""Sanitized failures raised by artifact filesystem operations."""

from maestro.common.errors import MaestroError


class ArtifactIntegrityError(MaestroError):
    """Artifact bytes did not match the caller's integrity expectations."""

    def __init__(self) -> None:
        super().__init__("Artifact integrity verification failed.")


class ArtifactFileError(MaestroError):
    """A local artifact file operation failed; its final state may be uncertain."""

    def __init__(self) -> None:
        super().__init__("Artifact file operation failed; the artifact may be present.")
