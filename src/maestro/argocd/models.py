"""Stable application state models, not Argo CD's entire Kubernetes resource schema."""

from dataclasses import dataclass
from enum import StrEnum


class ApplicationHealth(StrEnum):
    UNKNOWN = "Unknown"
    PROGRESSING = "Progressing"
    HEALTHY = "Healthy"
    DEGRADED = "Degraded"
    SUSPENDED = "Suspended"
    MISSING = "Missing"


class ApplicationSyncStatus(StrEnum):
    UNKNOWN = "Unknown"
    SYNCED = "Synced"
    OUT_OF_SYNC = "OutOfSync"


class OperationPhase(StrEnum):
    RUNNING = "Running"
    TERMINATING = "Terminating"
    FAILED = "Failed"
    ERROR = "Error"
    SUCCEEDED = "Succeeded"


@dataclass(frozen=True)
class Application:
    """An observation, not a guarantee about a particular deployment or future health."""

    name: str
    project: str
    namespace: str | None
    health: ApplicationHealth
    sync_status: ApplicationSyncStatus
    revision: str | None
    revisions: tuple[str, ...]
    operation_phase: OperationPhase | None
    operation_pending: bool
