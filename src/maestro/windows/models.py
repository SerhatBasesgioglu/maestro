"""Public Windows Service Control Manager result types."""

from dataclasses import dataclass, field
from enum import StrEnum


class ServiceState(StrEnum):
    """Native Windows service states plus an observable unknown sentinel."""

    STOPPED = "Stopped"
    START_PENDING = "StartPending"
    STOP_PENDING = "StopPending"
    RUNNING = "Running"
    CONTINUE_PENDING = "ContinuePending"
    PAUSE_PENDING = "PausePending"
    PAUSED = "Paused"
    UNKNOWN = "Unknown"


@dataclass(frozen=True)
class ServiceChangeResult:
    """One remote state observation before and after an optional dispatch.

    ``changed`` means the remote script dispatched the requested command; it
    does not indicate application readiness or that a pending state completed.
    """

    name: str = field(repr=False)
    initial_state: ServiceState
    final_state: ServiceState
    changed: bool
