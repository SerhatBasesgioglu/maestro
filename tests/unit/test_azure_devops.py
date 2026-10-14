import base64

import pytest

from maestro.azure_devops import (
    AzureDevOpsClient,
    AzureDevOpsConfig,
    BranchConflictError,
    BranchUpdateError,
    CommitChange,
    GitChangeType,
    GitContentType,
    PipelineRunResult,
    PipelineRunState,
    PipelineVariable,
    PullRequestStatus,
)
from maestro.common.errors import (
    ConfigurationError,
    ConflictError,
    PermissionDeniedError,
    ProtocolError,
    TransportError,
)

OLD = "a" * 40
NEW = "b" * 40


@pytest.fixture
def ado(http_transport):
    return AzureDevOpsClient(
        AzureDevOpsConfig("https://ado.company.local/Collection", "My Project", "private-token"),
        transport=http_transport,
    )


def ref_result(*, status="succeeded", success=True, old=OLD, new=NEW, name="refs/heads/main"):
    return {
        "value": [
            {
                "name": name,
                "oldObjectId": old,
                "newObjectId": new,
                "updateStatus": status,
                "success": success,
                "customMessage": "SECRET",
            }
        ]
    }


def run_result(*, pipeline_id=7, run_id=22, state="inProgress", result=None):
    return {
        "id": run_id,
        "pipeline": {"id": pipeline_id},
        "state": state,
        "result": result,
        "variables": {"secret": {"value": "SECRET"}},
    }


def test_repository_get_builds_encoded_target_and_pat_auth(ado, http_transport):
    http_transport.respond({"id": "repo-id", "name": "My Repo", "defaultBranch": "refs/heads/main"})
    result = ado.repositories.get("My Repo", timeout=4)
    assert (result.id, result.name, result.default_branch) == (
        "repo-id",
        "My Repo",
        "refs/heads/main",
    )
    call = http_transport.calls[0]
    assert (
        call["url"]
        == "https://ado.company.local/Collection/My%20Project/_apis/git/repositories/My%20Repo"
    )
    assert call["params"] == {"api-version": "7.1"}
    assert (
        call["headers"]["Authorization"] == "Basic " + base64.b64encode(b":private-token").decode()
    )
    assert call["timeout"] == 4


def test_bearer_auth_is_platform_explicit(http_transport):
    http_transport.respond({"id": "repo-id", "name": "repo"})
    with AzureDevOpsClient(
        AzureDevOpsConfig("https://dev.azure.com/myorg", "project", "TOKEN", auth="bearer"),
        transport=http_transport,
    ) as ado:
        ado.repositories.get("repo")
    assert http_transport.calls[0]["headers"]["Authorization"] == "Bearer TOKEN"
    assert http_transport.closes == 0


def test_get_branch_reads_exact_stats_not_prefix_matches(ado, http_transport):
    http_transport.respond({"name": "feature/main", "commit": {"commitId": OLD}})
    branch = ado.repositories.get_branch("repo", "refs/heads/feature/main")
    assert (branch.name, branch.commit_id) == ("refs/heads/feature/main", OLD)
    assert http_transport.calls[0]["url"].endswith("/repo/stats/branches")
    assert http_transport.calls[0]["params"]["name"] == "feature/main"


def test_get_branch_rejects_a_different_result(ado, http_transport):
    http_transport.respond({"name": "main-other", "commit": {"commitId": OLD}})
    with pytest.raises(ProtocolError):
        ado.repositories.get_branch("repo", "main")


def test_create_branch_uses_zero_old_commit_without_overwriting(ado, http_transport):
    http_transport.respond(ref_result(old="0" * 40))
    result = ado.repositories.create_branch("repo", "main", from_commit=NEW)
    assert result.commit_id == NEW
    assert http_transport.calls[0]["body"] == [
        {"name": "refs/heads/main", "oldObjectId": "0" * 40, "newObjectId": NEW}
    ]


def test_move_branch_requires_expected_commit_and_confirms_server_result(ado, http_transport):
    http_transport.respond(ref_result())
    result = ado.repositories.move_branch_ref("repo", "main", expected_commit=OLD, new_commit=NEW)
    assert (result.name, result.commit_id) == ("refs/heads/main", NEW)
    assert http_transport.calls[0]["body"][0]["oldObjectId"] == OLD
    assert http_transport.calls[0]["method"] == "POST"


@pytest.mark.parametrize("status", ["staleOldObjectId", "locked", "refNameConflict"])
def test_http_200_ref_conflicts_are_failures_and_never_replayed(ado, http_transport, status):
    http_transport.respond(ref_result(status=status, success=False))
    with pytest.raises(BranchConflictError) as failure:
        ado.repositories.move_branch_ref("repo", "main", expected_commit=OLD, new_commit=NEW)
    assert isinstance(failure.value, ConflictError)
    assert failure.value.update_status == status
    assert "SECRET" not in repr(failure.value)
    assert len(http_transport.calls) == 1


@pytest.mark.parametrize(
    "status", ["writePermissionRequired", "createBranchPermissionRequired", "forcePushRequired"]
)
def test_http_200_permission_rejections_are_not_success(ado, http_transport, status):
    http_transport.respond(ref_result(status=status, success=False))
    with pytest.raises(PermissionDeniedError):
        ado.repositories.move_branch_ref("repo", "main", expected_commit=OLD, new_commit=NEW)
    assert len(http_transport.calls) == 1


def test_policy_rejections_do_not_expose_server_messages(ado, http_transport):
    http_transport.respond(ref_result(status="rejectedByPolicy", success=False))
    with pytest.raises(BranchUpdateError) as failure:
        ado.repositories.move_branch_ref("repo", "main", expected_commit=OLD, new_commit=NEW)
    assert failure.value.update_status == "rejectedByPolicy"
    assert "SECRET" not in str(failure.value)


@pytest.mark.parametrize(
    "result",
    [
        ref_result(status="succeededNonExistentRef"),
        ref_result(name="refs/heads/wrong"),
        ref_result(new=OLD),
        {"value": []},
        ref_result(success="true"),
    ],
)
def test_ref_results_must_confirm_exact_requested_update(ado, http_transport, result):
    http_transport.respond(result)
    with pytest.raises(ProtocolError):
        ado.repositories.move_branch_ref("repo", "main", expected_commit=OLD, new_commit=NEW)


@pytest.mark.parametrize(
    "branch",
    [
        "",
        "refs/tags/main",
        "refs/heads/",
        "/main",
        "-main",
        "a..b",
        "a.lock",
        ".main",
        "a//b",
        "main@{x",
        "a?b",
        "a\\b",
        "main\n",
    ],
)
def test_invalid_branches_fail_before_io(ado, http_transport, branch):
    with pytest.raises(ConfigurationError):
        ado.repositories.create_branch("repo", branch, from_commit=NEW)
    assert http_transport.calls == []


@pytest.mark.parametrize("sha", ["", "HEAD", "a" * 39, "g" * 40, "0" * 40, None])
def test_invalid_commits_cannot_delete_or_implicitly_select_a_revision(ado, http_transport, sha):
    with pytest.raises(ConfigurationError):
        ado.repositories.move_branch_ref("repo", "main", expected_commit=sha, new_commit=NEW)
    assert http_transport.calls == []


def test_create_commit_is_a_single_atomic_expected_ref_push(ado, http_transport):
    changes = (
        CommitChange("/text.txt", GitChangeType.ADD, "SECRET"),
        CommitChange("/binary.bin", GitChangeType.EDIT, "AAE=", GitContentType.BASE64),
        CommitChange("/old.txt", GitChangeType.DELETE),
    )
    http_transport.respond(
        {
            "commits": [{"commitId": NEW}],
            "refUpdates": [{"name": "refs/heads/main", "oldObjectId": OLD, "newObjectId": NEW}],
        },
        status=201,
    )
    result = ado.repositories.create_commit(
        "repo", "main", expected_commit=OLD, message="Heading\n\nDetails", changes=changes
    )
    assert result.commit_id == NEW
    assert result.branch == "refs/heads/main"
    assert "SECRET" not in repr(changes)
    body = http_transport.calls[0]["body"]
    assert body["refUpdates"] == [{"name": "refs/heads/main", "oldObjectId": OLD}]
    items = body["commits"][0]["changes"]
    assert items[0]["newContent"] == {"content": "SECRET", "contentType": "rawText"}
    assert items[1]["newContent"]["contentType"] == "base64Encoded"
    assert "newContent" not in items[2]
    assert len(http_transport.calls) == 1


@pytest.mark.parametrize("changes", [[], ["wrong"], [CommitChange("/a", GitChangeType.DELETE)] * 2])
def test_invalid_commit_changes_fail_before_io(ado, http_transport, changes):
    with pytest.raises(ConfigurationError):
        ado.repositories.create_commit(
            "repo", "main", expected_commit=OLD, message="message", changes=changes
        )
    assert http_transport.calls == []


def test_uncertain_commit_failure_is_not_replayed(ado, http_transport):
    error = TransportError("uncertain")
    http_transport.responses.append(error)
    with pytest.raises(TransportError) as failure:
        ado.repositories.create_commit(
            "repo",
            "main",
            expected_commit=OLD,
            message="message",
            changes=[CommitChange("/a", GitChangeType.ADD, "")],
        )
    assert failure.value is error
    assert len(http_transport.calls) == 1


def test_create_pull_request_does_not_merge_or_enable_auto_complete(ado, http_transport):
    http_transport.respond(
        {
            "pullRequestId": 22,
            "repository": {"id": "repo-id"},
            "title": "Feature",
            "sourceRefName": "refs/heads/feature",
            "targetRefName": "refs/heads/main",
            "status": "active",
            "isDraft": True,
        },
        status=201,
    )
    result = ado.repositories.create_pull_request(
        "repo",
        source_branch="feature",
        target_branch="main",
        title="Feature",
        description="Details",
        is_draft=True,
    )
    assert result.id == 22 and result.status is PullRequestStatus.ACTIVE and result.is_draft
    body = http_transport.calls[0]["body"]
    assert body == {
        "sourceRefName": "refs/heads/feature",
        "targetRefName": "refs/heads/main",
        "title": "Feature",
        "description": "Details",
        "isDraft": True,
    }


def test_pull_request_source_must_differ_from_target(ado, http_transport):
    with pytest.raises(ConfigurationError):
        ado.repositories.create_pull_request(
            "repo", source_branch="main", target_branch="main", title="Title"
        )
    assert http_transport.calls == []


def test_pipeline_run_selects_explicit_branch_commit_and_typed_secret_variables(
    ado, http_transport
):
    http_transport.respond(run_result())
    secret = PipelineVariable("SECRET", is_secret=True)
    result = ado.pipelines.run(
        7,
        branch="release/main",
        commit=NEW,
        template_parameters={"deploy": True, "targets": ["test"]},
        variables={"Password": secret},
    )
    body = http_transport.calls[0]["body"]
    assert body["resources"]["repositories"]["self"] == {
        "refName": "refs/heads/release/main",
        "version": NEW,
    }
    assert body["variables"] == {"Password": {"value": "SECRET", "isSecret": True}}
    assert (
        result.id == 22 and result.state is PipelineRunState.IN_PROGRESS and result.result is None
    )
    assert "SECRET" not in repr(result) and "SECRET" not in repr(secret)


def test_pipeline_get_converts_completed_result(ado, http_transport):
    http_transport.respond(run_result(state="completed", result="succeeded"))
    result = ado.pipelines.get_run(7, 22)
    assert result.state is PipelineRunState.COMPLETED
    assert result.result is PipelineRunResult.SUCCEEDED
    assert http_transport.calls[0]["url"].endswith("/pipelines/7/runs/22")


@pytest.mark.parametrize("value", [0, -1, True, "7", None])
def test_pipeline_ids_are_explicit_positive_integers(ado, http_transport, value):
    with pytest.raises(ConfigurationError):
        ado.pipelines.run(value, branch="main")
    assert http_transport.calls == []


@pytest.mark.parametrize(
    "data", [run_result(pipeline_id=8), run_result(state="invalid"), run_result(run_id=23)]
)
def test_get_run_rejects_malformed_or_wrong_resource_results(ado, http_transport, data):
    http_transport.respond(data)
    with pytest.raises(ProtocolError):
        ado.pipelines.get_run(7, 22)


@pytest.mark.parametrize(
    "path,kind,content,encoding",
    [
        ("relative", GitChangeType.ADD, "x", GitContentType.RAW_TEXT),
        ("/../x", GitChangeType.ADD, "x", GitContentType.RAW_TEXT),
        ("/x", GitChangeType.DELETE, "x", GitContentType.RAW_TEXT),
        ("/x", GitChangeType.ADD, None, GitContentType.RAW_TEXT),
        ("/x", GitChangeType.ADD, "invalid-base64!", GitContentType.BASE64),
    ],
)
def test_commit_change_validation_is_local_and_secret_safe(path, kind, content, encoding):
    with pytest.raises(ConfigurationError):
        CommitChange(path, kind, content, encoding)


@pytest.mark.parametrize(
    "data",
    [
        {"commits": [], "refUpdates": []},
        {"commits": [{"commitId": "invalid"}], "refUpdates": []},
        {"commits": [{"commitId": NEW}], "refUpdates": []},
        {
            "commits": [{"commitId": NEW}],
            "refUpdates": [{"name": "refs/heads/main", "oldObjectId": NEW, "newObjectId": NEW}],
        },
        {
            "commits": [{"commitId": NEW}],
            "refUpdates": [{"name": "refs/heads/main", "oldObjectId": OLD, "newObjectId": OLD}],
        },
    ],
)
def test_push_response_must_confirm_commit_and_exact_expected_ref(ado, http_transport, data):
    http_transport.respond(data, status=201)
    with pytest.raises(ProtocolError):
        ado.repositories.create_commit(
            "repo",
            "main",
            expected_commit=OLD,
            message="message",
            changes=[CommitChange("/a", GitChangeType.ADD, "")],
        )
    assert len(http_transport.calls) == 1


@pytest.mark.parametrize("status,source", [("invalid", "feature"), ("active", "wrong-branch")])
def test_pull_request_response_cannot_misrepresent_status_or_branches(
    ado, http_transport, status, source
):
    http_transport.respond(
        {
            "pullRequestId": 22,
            "repository": {"id": "repo-id"},
            "title": "Feature",
            "sourceRefName": f"refs/heads/{source}",
            "targetRefName": "refs/heads/main",
            "status": status,
        },
        status=201,
    )
    with pytest.raises(ProtocolError):
        ado.repositories.create_pull_request(
            "repo", source_branch="feature", target_branch="main", title="Feature"
        )
    assert len(http_transport.calls) == 1


@pytest.mark.parametrize("arguments", [{"auth": "automatic"}, {"api_version": "latest"}])
def test_authentication_and_api_versions_are_explicit(arguments):
    with pytest.raises(ConfigurationError):
        AzureDevOpsConfig("https://dev.azure.com/org", "project", "TOKEN", **arguments)


def test_pipeline_variables_require_typed_inputs(ado, http_transport):
    with pytest.raises(ConfigurationError):
        ado.pipelines.run(7, branch="main", variables={"variable": "not-typed"})
    with pytest.raises(ConfigurationError):
        PipelineVariable("SECRET", is_secret="true")
    assert http_transport.calls == []
