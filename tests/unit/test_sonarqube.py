import pytest

from maestro.common.errors import ConfigurationError, ProtocolError
from maestro.sonarqube import QualityGateStatus, SonarQubeClient, SonarQubeConfig, SonarQubeError


@pytest.fixture
def sonar(http_transport):
    return SonarQubeClient(
        SonarQubeConfig("https://sonar.company.local/sonarqube", "private-token"),
        transport=http_transport,
    )


def gate(status="OK"):
    return {
        "projectStatus": {
            "status": status,
            "ignoredConditions": False,
            "conditions": [
                {
                    "metricKey": "new_coverage",
                    "status": "OK",
                    "comparator": "LT",
                    "errorThreshold": "80",
                    "actualValue": "92.0",
                }
            ],
        }
    }


def test_project_get_preserves_key_and_bearer_auth(sonar, http_transport):
    http_transport.respond(
        {"component": {"key": "org:project", "name": "My Project", "qualifier": "TRK"}}
    )
    result = sonar.projects.get("org:project")
    assert result.key == "org:project" and result.name == "My Project"
    assert (
        http_transport.calls[0]["url"]
        == "https://sonar.company.local/sonarqube/api/components/show"
    )
    assert http_transport.calls[0]["params"] == {"component": "org:project"}
    assert http_transport.calls[0]["headers"]["Authorization"] == "Bearer private-token"


def test_a_component_file_is_not_silently_treated_as_a_project(sonar, http_transport):
    http_transport.respond({"component": {"key": "file", "name": "file", "qualifier": "FIL"}})
    with pytest.raises(SonarQubeError):
        sonar.projects.get("file")


def test_wrong_project_result_is_not_accepted(sonar, http_transport):
    http_transport.respond({"component": {"key": "wrong", "name": "project", "qualifier": "TRK"}})
    with pytest.raises(ProtocolError):
        sonar.projects.get("requested")


@pytest.mark.parametrize("status", list(QualityGateStatus))
def test_gate_status_is_typed_and_none_is_not_ok(sonar, http_transport, status):
    http_transport.respond(gate(status.value))
    result = sonar.quality_gates.get_status("org:project", branch="feature/a")
    assert result.status is status
    assert result.conditions[0].metric == "new_coverage"
    assert result.conditions[0].error_threshold == "80"
    assert result.conditions[0].actual_value == "92.0"
    assert not result.ignored_conditions
    assert result.project_key == "org:project" and result.branch == "feature/a"
    assert http_transport.calls[0]["params"] == {"projectKey": "org:project", "branch": "feature/a"}


def test_analysis_id_selects_an_exact_gate_without_project_fallback(sonar, http_transport):
    http_transport.respond(gate("ERROR"))
    result = sonar.quality_gates.get_status(analysis_id="completed-analysis")
    assert result.analysis_id == "completed-analysis" and result.project_key is None
    assert result.status is QualityGateStatus.ERROR
    assert http_transport.calls[0]["params"] == {"analysisId": "completed-analysis"}


def test_pull_request_selection_is_forwarded_explicitly(sonar, http_transport):
    http_transport.respond(gate())
    result = sonar.quality_gates.get_status("org:project", pull_request="22")
    assert result.pull_request == "22"
    assert http_transport.calls[0]["params"]["pullRequest"] == "22"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"project_key": "project", "analysis_id": "analysis"},
        {"analysis_id": "analysis", "branch": "main"},
        {"analysis_id": "analysis", "pull_request": "22"},
        {"project_key": "project", "branch": "main", "pull_request": "22"},
        {"project_key": ""},
        {"analysis_id": ""},
        {"project_key": "project", "branch": ""},
    ],
)
def test_ambiguous_or_empty_gate_selectors_fail_before_io(sonar, http_transport, arguments):
    with pytest.raises(ConfigurationError):
        sonar.quality_gates.get_status(**arguments)
    assert http_transport.calls == []


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"projectStatus": {"status": "UNKNOWN"}},
        {"projectStatus": {"status": "OK", "ignoredConditions": "false"}},
        {"projectStatus": {"status": "OK", "conditions": [{"metricKey": "coverage"}]}},
    ],
)
def test_malformed_quality_results_are_not_interpreted_as_passed(sonar, http_transport, data):
    http_transport.respond(data)
    with pytest.raises(ProtocolError):
        sonar.quality_gates.get_status("project")
