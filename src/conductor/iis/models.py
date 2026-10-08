"""Public IIS results, independent of PowerShell or SDK response types."""

from dataclasses import dataclass
from enum import StrEnum


class ApplicationPoolState(StrEnum):
    """IIS pool states; UNKNOWN is observable but is never mutated automatically."""

    STARTING = "Starting"
    STARTED = "Started"
    STOPPING = "Stopping"
    STOPPED = "Stopped"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class ApplicationPoolChangeResult:
    """A confirmed desired state, not an application-health or deployment result.

    ``changed`` means this call's remote command reported issuing a start/stop.
    It is false if the desired state was already satisfied or reached externally.
    """

    name: str
    initial_state: ApplicationPoolState
    final_state: ApplicationPoolState
    changed: bool
