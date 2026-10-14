import pytest

import maestro.argocd.applications as applications_module
from maestro.argocd import (
    ApplicationHealth,
    ApplicationHealthTimeoutError,
    ApplicationSyncStatus,
    ArgoCdClient,
    ArgoCdConfig,
    ArgoCdError,
    OperationPhase,
)
from maestro.common.errors import (
    AuthenticationError,
    ConfigurationError,
    OperationTimeoutError,
    PermissionDeniedError,
    ProtocolError,
    ResourceNotFoundError,
    TransportError,
)


def application(
    *,
    name="my-app",
    project="delivery",
    namespace="argocd",
    health="Healthy",
    synced="Synced",
    revision="commit-1",
    revisions=None,
    phase=None,
    pending=False,
):
    status = {"health": {"status": health}, "sync": {"status": synced, "revision": revision}}
    if revisions is not None:
        status["sync"]["revisions"] = revisions
    if phase is not None:
        status["operationState"] = {"phase": phase, "message": "SECRET"}
    data = {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"project": project},
        "status": status,
    }
    if pending:
        data["operation"] = {"sync": {"revision": "commit-2"}}
    return data


@pytest.fixture
def argo(http_transport):
    return ArgoCdClient(
        ArgoCdConfig("https://argo.company.local/base", "delivery", "private-token"),
        transport=http_transport,
    )


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            assert seconds > 0
            self.now += seconds

    clock = Clock()
    monkeypatch.setattr(applications_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(applications_module.time, "sleep", clock.sleep)
    return clock


def test_application_get_preserves_project_filter_and_typed_status(argo, http_transport):
    http_transport.respond(application())
    result = argo.applications.get("my-app")
    assert result.name == "my-app" and result.project == "delivery"
    assert result.health is ApplicationHealth.HEALTHY
    assert result.sync_status is ApplicationSyncStatus.SYNCED
    assert result.revision == "commit-1"
    assert (
        http_transport.calls[0]["url"]
        == "https://argo.company.local/base/api/v1/applications/my-app"
    )
    assert http_transport.calls[0]["params"] == {"project": "delivery"}
    assert http_transport.calls[0]["headers"]["Authorization"] == "Bearer private-token"


def test_explicit_application_namespace_is_forwarded_in_reads_and_writes(http_transport):
    argo = ArgoCdClient(
        ArgoCdConfig(
            "https://argo.company.local", "delivery", "TOKEN", application_namespace="team"
        ),
        transport=http_transport,
    )
    http_transport.respond(application(namespace="team"))
    http_transport.respond(application(namespace="team", pending=True))
    argo.applications.get("my-app")
    argo.applications.sync("my-app")
    assert http_transport.calls[0]["params"]["appNamespace"] == "team"
    assert http_transport.calls[1]["body"]["appNamespace"] == "team"


@pytest.mark.parametrize(
    "status,error",
    [(401, AuthenticationError), (403, PermissionDeniedError), (404, ResourceNotFoundError)],
)
def test_project_security_errors_are_not_masked_or_retried(argo, http_transport, status, error):
    http_transport.respond({"message": "SECRET"}, status=status)
    with pytest.raises(error) as failure:
        argo.applications.get("my-app")
    assert "SECRET" not in str(failure.value)
    assert len(http_transport.calls) == 1


def test_sync_is_an_acknowledged_request_not_health_success(argo, http_transport):
    http_transport.respond(application(health="Progressing", phase="Running", pending=True))
    result = argo.applications.sync("my-app", revision="commit-2")
    assert result.health is ApplicationHealth.PROGRESSING
    assert result.operation_phase is OperationPhase.RUNNING and result.operation_pending
    assert http_transport.calls[0]["body"] == {
        "name": "my-app",
        "project": "delivery",
        "revision": "commit-2",
        "prune": False,
        "dryRun": False,
    }
    assert http_transport.calls[0]["method"] == "POST"
    assert len(http_transport.calls) == 1


def test_pruning_and_server_preview_are_explicit(argo, http_transport):
    http_transport.respond(application())
    argo.applications.sync("my-app", prune=True, dry_run=True)
    assert http_transport.calls[0]["body"]["prune"] is True
    assert http_transport.calls[0]["body"]["dryRun"] is True


def test_uncertain_sync_failure_is_never_replayed(argo, http_transport):
    http_transport.responses.append(TransportError("uncertain"))
    with pytest.raises(TransportError):
        argo.applications.sync("my-app")
    assert len(http_transport.calls) == 1


def test_health_wait_uses_a_single_budget_and_does_not_sync(argo, http_transport, clock):
    http_transport.respond(application(health="Progressing", synced="OutOfSync"))
    http_transport.respond(application(health="Healthy", synced="OutOfSync"))
    http_transport.respond(application())
    result = argo.applications.wait_for_health("my-app", timeout=5, poll_interval=1)
    assert result.health is ApplicationHealth.HEALTHY
    assert clock.now == 2
    assert [call["timeout"] for call in http_transport.calls] == [5, 4, 3]
    assert {call["method"] for call in http_transport.calls} == {"GET"}


@pytest.mark.parametrize(
    "first",
    [application(pending=True), application(phase="Running"), application(phase="Terminating")],
)
def test_old_healthy_state_during_an_operation_does_not_satisfy_wait(
    argo, http_transport, clock, first
):
    http_transport.respond(first)
    http_transport.respond(application(revision="commit-2", phase="Succeeded"))
    result = argo.applications.wait_for_health("my-app", revision="commit-2", poll_interval=0.5)
    assert result.revision == "commit-2"
    assert clock.now == 0.5 and len(http_transport.calls) == 2


def test_an_exact_revision_can_be_required(argo, http_transport, clock):
    http_transport.respond(application(revision="old-commit"))
    http_transport.respond(application(revision="wanted-commit"))
    result = argo.applications.wait_for_health("my-app", revision="wanted-commit")
    assert result.revision == "wanted-commit" and len(http_transport.calls) == 2


def test_health_only_policy_is_explicit(argo, http_transport, clock):
    http_transport.respond(application(synced="OutOfSync"))
    result = argo.applications.wait_for_health("my-app", require_synced=False)
    assert result.sync_status is ApplicationSyncStatus.OUT_OF_SYNC
    assert clock.now == 0


def test_multi_source_single_revision_wait_is_not_misrepresented(argo, http_transport, clock):
    http_transport.respond(application(revisions=["one", "two"]))
    with pytest.raises(ArgoCdError, match="multi-source"):
        argo.applications.wait_for_health("my-app", revision="one")
    assert len(http_transport.calls) == 1


def test_health_timeout_includes_last_observation_and_does_not_poll_past_budget(
    argo, http_transport, clock
):
    http_transport.respond(application(health="Degraded"))
    http_transport.respond(application(health="Degraded"))
    with pytest.raises(ApplicationHealthTimeoutError) as failure:
        argo.applications.wait_for_health("my-app", timeout=2.5, poll_interval=2)
    assert isinstance(failure.value, OperationTimeoutError)
    assert failure.value.name == "my-app"
    assert failure.value.last_application.health is ApplicationHealth.DEGRADED
    assert clock.now == 2.5 and len(http_transport.calls) == 2


def test_missing_observed_status_is_unknown_not_healthy(argo, http_transport):
    http_transport.respond({"metadata": {"name": "my-app"}, "spec": {"project": "delivery"}})
    result = argo.applications.get("my-app")
    assert result.health is ApplicationHealth.UNKNOWN
    assert result.sync_status is ApplicationSyncStatus.UNKNOWN
    assert result.revision is None


@pytest.mark.parametrize(
    "data",
    [
        application(name="other"),
        application(project="other"),
        application(health="Invalid"),
        application(phase="Invalid"),
        application(phase=0),
        {"metadata": {}, "spec": {}},
    ],
)
def test_wrong_target_and_malformed_states_fail_safely(argo, http_transport, data):
    http_transport.respond(data)
    with pytest.raises(ProtocolError):
        argo.applications.get("my-app")


@pytest.mark.parametrize("name", ["", "../my-app", "app/other", "app%2Fother", "app\n"])
def test_application_identifiers_cannot_escape_path(argo, http_transport, name):
    with pytest.raises(ConfigurationError):
        argo.applications.sync(name)
    assert http_transport.calls == []


@pytest.mark.parametrize("arguments", [{"prune": "true"}, {"dry_run": 1}, {"revision": ""}])
def test_invalid_sync_arguments_fail_before_io(argo, http_transport, arguments):
    with pytest.raises(ConfigurationError):
        argo.applications.sync("my-app", **arguments)
    assert http_transport.calls == []


@pytest.mark.parametrize(
    "arguments",
    [{"timeout": 0}, {"poll_interval": float("inf")}, {"require_synced": 1}, {"revision": ""}],
)
def test_invalid_wait_arguments_fail_before_io(argo, http_transport, arguments):
    with pytest.raises(ConfigurationError):
        argo.applications.wait_for_health("my-app", **arguments)
    assert http_transport.calls == []


def test_response_arriving_after_deadline_is_not_accepted_as_success(
    argo, http_transport, clock, monkeypatch
):
    original = http_transport.request

    def late_response(*args, **kwargs):
        clock.now += 4
        return original(*args, **kwargs)

    monkeypatch.setattr(http_transport, "request", late_response)
    http_transport.respond(application())
    with pytest.raises(ApplicationHealthTimeoutError):
        argo.applications.wait_for_health("my-app", timeout=3)
    assert len(http_transport.calls) == 1


def test_application_namespace_mismatch_is_rejected(http_transport):
    argo = ArgoCdClient(
        ArgoCdConfig(
            "https://argo.company.local", "delivery", "TOKEN", application_namespace="team"
        ),
        transport=http_transport,
    )
    http_transport.respond(application(namespace="other"))
    with pytest.raises(ProtocolError):
        argo.applications.get("my-app")
