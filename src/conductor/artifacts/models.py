"""Small value objects returned by artifact operations."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactInfo:
    """Metadata for one fully staged and verified artifact."""

    key: str
    size: int
    sha256: str
