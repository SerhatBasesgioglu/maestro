"""Quality-gate reads with explicit project/analysis and branch/PR selection."""

from conductor.common._json import (
    array_value,
    bool_value,
    object_value,
    optional_string,
    required_object,
    string_value,
)
from conductor.common._validation import non_empty
from conductor.common.errors import ConfigurationError, ProtocolError
from conductor.common.http import JsonValue, _JsonConnection
from conductor.sonarqube.models import QualityGateCondition, QualityGateResult, QualityGateStatus


class QualityGates:
    def __init__(self, connection: _JsonConnection) -> None:
        self._connection = connection

    def get_status(
        self,
        project_key: str | None = None,
        *,
        analysis_id: str | None = None,
        branch: str | None = None,
        pull_request: str | None = None,
        timeout: float | None = None,
    ) -> QualityGateResult:
        """Read a quality gate using exactly one project key or completed analysis ID.

        Requires the server's Browse/Administer/Execute Analysis permissions.
        Branch/PR selectors are mutually exclusive and require a project key and
        a supporting server edition. analysis_id is an analysis ID, NOT a Compute
        Engine task ID; prefer it to avoid a concurrent analysis changing a release
        gate. This call does not run a scanner, wait for CE, or approve a release.
        """
        if (project_key is None) == (analysis_id is None):
            raise ConfigurationError("Supply exactly one project_key or analysis_id.")
        if branch is not None and pull_request is not None:
            raise ConfigurationError("branch and pull_request are mutually exclusive.")
        if analysis_id is not None and (branch is not None or pull_request is not None):
            raise ConfigurationError("Branch/PR selectors require project_key, not analysis_id.")
        params: dict[str, str] = {}
        for field, value in (
            ("projectKey", project_key),
            ("analysisId", analysis_id),
            ("branch", branch),
            ("pullRequest", pull_request),
        ):
            if value is not None:
                params[field] = non_empty(value, field)
        data = object_value(
            self._connection.request(
                "GET", "api/qualitygates/project_status", params=params, timeout=timeout
            )
        )
        result = required_object(data, "projectStatus")
        conditions = tuple(_condition(item) for item in array_value(result.get("conditions", [])))
        return QualityGateResult(
            _status(result.get("status")),
            conditions,
            bool_value(result.get("ignoredConditions", False)),
            project_key,
            analysis_id,
            branch,
            pull_request,
        )


def _status(value: JsonValue) -> QualityGateStatus:
    try:
        return QualityGateStatus(string_value(value))
    except ValueError:
        raise ProtocolError("SonarQube returned an unknown quality-gate status.") from None


def _condition(value: JsonValue) -> QualityGateCondition:
    item = object_value(value)
    return QualityGateCondition(
        string_value(item.get("metricKey")),
        _status(item.get("status")),
        string_value(item.get("comparator")),
        optional_string(item.get("errorThreshold")),
        optional_string(item.get("actualValue")),
    )
