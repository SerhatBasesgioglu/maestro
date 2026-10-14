"""Library-owned SonarQube project and quality-gate observations."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class Project:
    key: str
    name: str


class QualityGateStatus(StrEnum):
    OK = "OK"
    WARN = "WARN"
    ERROR = "ERROR"
    NONE = "NONE"


@dataclass(frozen=True)
class QualityGateCondition:
    metric: str
    status: QualityGateStatus
    comparator: str
    error_threshold: str | None
    actual_value: str | None


@dataclass(frozen=True)
class QualityGateResult:
    """NONE/WARN/ERROR are not a passing gate; consumer policy decides release approval."""

    status: QualityGateStatus
    conditions: tuple[QualityGateCondition, ...]
    ignored_conditions: bool
    project_key: str | None
    analysis_id: str | None
    branch: str | None
    pull_request: str | None
