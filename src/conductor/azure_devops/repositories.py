"""Focused Git operations with explicit expected-commit concurrency checks."""

from collections.abc import Sequence

from conductor.azure_devops._validation import ZERO_COMMIT, branch_ref, commit_id
from conductor.azure_devops.config import AzureDevOpsConfig
from conductor.azure_devops.errors import BranchConflictError, BranchUpdateError
from conductor.azure_devops.models import (
    CommitChange,
    GitBranch,
    GitChangeType,
    GitCommit,
    GitRepository,
    PullRequest,
    PullRequestStatus,
)
from conductor.common._json import (
    array_value,
    bool_value,
    int_value,
    object_value,
    optional_string,
    required_object,
    string_value,
)
from conductor.common._validation import non_empty, path_segment
from conductor.common.errors import ConfigurationError, PermissionDeniedError, ProtocolError
from conductor.common.http import JsonValue, _JsonConnection


class Repositories:
    """Operations require the appropriate code-read/code-write and branch/PR permissions.

    Writes are dispatched once. Transport failures may leave an uncertain outcome;
    consumers must inspect current state before choosing whether to attempt again.
    """

    def __init__(self, connection: _JsonConnection, config: AzureDevOpsConfig) -> None:
        self._connection = connection
        self._prefix = f"{path_segment(config.project, 'project')}/_apis/git/repositories"
        self._params = {"api-version": config.api_version}

    def _path(self, repository: str) -> str:
        return f"{self._prefix}/{path_segment(repository, 'repository')}"

    def get(self, repository: str, *, timeout: float | None = None) -> GitRepository:
        """Read one repository by explicit name or ID; never infer a current repository."""
        data = object_value(
            self._connection.request(
                "GET", self._path(repository), params=self._params, timeout=timeout
            )
        )
        return GitRepository(
            string_value(data.get("id")),
            string_value(data.get("name")),
            optional_string(data.get("defaultBranch")),
        )

    def get_branch(self, repository: str, name: str, *, timeout: float | None = None) -> GitBranch:
        """Read one exact branch via branch stats, not a prefix-filtered ref listing."""
        ref = branch_ref(name)
        data = object_value(
            self._connection.request(
                "GET",
                f"{self._path(repository)}/stats/branches",
                params={**self._params, "name": ref.removeprefix("refs/heads/")},
                timeout=timeout,
            )
        )
        returned_name = string_value(data.get("name"))
        if returned_name not in (ref, ref.removeprefix("refs/heads/")):
            raise ProtocolError("Azure DevOps returned a different branch than requested.")
        return GitBranch(ref, _response_commit(required_object(data, "commit").get("commitId")))

    def create_branch(
        self, repository: str, name: str, *, from_commit: str, timeout: float | None = None
    ) -> GitBranch:
        """Create only if absent, using the zero old-object ID; never replace an existing ref."""
        return self._update_ref(
            repository, branch_ref(name), ZERO_COMMIT, commit_id(from_commit), timeout
        )

    def move_branch_ref(
        self,
        repository: str,
        name: str,
        *,
        expected_commit: str,
        new_commit: str,
        timeout: float | None = None,
    ) -> GitBranch:
        """Move an existing branch with compare-and-swap and applicable ref/force-push permission.

        Both expected and new commits are required and non-zero; this API cannot
        delete refs. A stale expected commit raises BranchConflictError. HTTP 200
        is checked for per-ref rejection, and a rejected write is never replayed.
        """
        return self._update_ref(
            repository, branch_ref(name), commit_id(expected_commit), commit_id(new_commit), timeout
        )

    def _update_ref(
        self, repository: str, name: str, old_commit: str, new_commit: str, timeout: float | None
    ) -> GitBranch:
        data = object_value(
            self._connection.request(
                "POST",
                f"{self._path(repository)}/refs",
                params=self._params,
                json_body=[{"name": name, "oldObjectId": old_commit, "newObjectId": new_commit}],
                timeout=timeout,
            )
        )
        values = array_value(data.get("value"))
        if len(values) != 1:
            raise ProtocolError("Azure DevOps returned an unexpected ref-update result count.")
        result = object_value(values[0])
        status = string_value(result.get("updateStatus"))
        success = bool_value(result.get("success"))
        if not success:
            if status in ("staleOldObjectId", "locked", "refNameConflict"):
                raise BranchConflictError(status)
            if status in (
                "writePermissionRequired",
                "manageNotePermissionRequired",
                "createBranchPermissionRequired",
                "createTagPermissionRequired",
                "forcePushRequired",
            ):
                raise PermissionDeniedError("Azure DevOps denied the branch reference update.")
            raise BranchUpdateError(status)
        if (
            status != "succeeded"
            or result.get("name") != name
            or result.get("oldObjectId") != old_commit
            or result.get("newObjectId") != new_commit
        ):
            raise ProtocolError(
                "Azure DevOps did not confirm the requested branch reference update."
            )
        return GitBranch(name, new_commit)

    def create_commit(
        self,
        repository: str,
        branch: str,
        *,
        expected_commit: str,
        message: str,
        changes: Sequence[CommitChange],
        timeout: float | None = None,
    ) -> GitCommit:
        """Create/push one commit atomically to an existing branch with an expected previous commit.

        Requires code-write permission. This is a Git push, not creation of an
        unattached commit. Invalid/stale writes are not retried. Add/edit/delete
        are supported; rename/merge and automatic conflict resolution are not.
        """
        ref = branch_ref(branch)
        previous = commit_id(expected_commit)
        if not isinstance(message, str) or not message.strip() or "\x00" in message:
            raise ConfigurationError(
                "Commit messages must be non-blank text without NUL characters."
            )
        if not changes or any(not isinstance(change, CommitChange) for change in changes):
            raise ConfigurationError("changes must contain at least one typed CommitChange.")
        if len({change.path for change in changes}) != len(changes):
            raise ConfigurationError("A commit must not contain duplicate file paths.")
        encoded: list[JsonValue] = []
        for change in changes:
            item: dict[str, JsonValue] = {
                "changeType": change.change_type.value,
                "item": {"path": change.path},
            }
            if change.change_type != GitChangeType.DELETE:
                item["newContent"] = {
                    "content": change.content,
                    "contentType": change.content_type.value,
                }
            encoded.append(item)
        data = object_value(
            self._connection.request(
                "POST",
                f"{self._path(repository)}/pushes",
                params=self._params,
                json_body={
                    "refUpdates": [{"name": ref, "oldObjectId": previous}],
                    "commits": [{"comment": message, "changes": encoded}],
                },
                timeout=timeout,
            )
        )
        commits = array_value(data.get("commits"))
        if len(commits) != 1:
            raise ProtocolError("Azure DevOps returned an unexpected commit result count.")
        created = _response_commit(object_value(commits[0]).get("commitId"))
        refs = array_value(data.get("refUpdates"))
        if len(refs) != 1 or object_value(refs[0]).get("name") != ref:
            raise ProtocolError("Azure DevOps did not confirm the requested commit branch.")
        result = object_value(refs[0])
        if result.get("oldObjectId") != previous or result.get("newObjectId") != created:
            raise ProtocolError("Azure DevOps did not confirm the committed branch revision.")
        return GitCommit(created, ref, message)

    def create_pull_request(
        self,
        repository: str,
        *,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str = "",
        is_draft: bool = False,
        timeout: float | None = None,
    ) -> PullRequest:
        """Create, never merge, a pull request with PR permission; no retry or deduplication."""
        source, target = branch_ref(source_branch), branch_ref(target_branch)
        non_empty(title, "title")
        if source == target:
            raise ConfigurationError("Pull request source and target branches must differ.")
        if not isinstance(description, str) or type(is_draft) is not bool:
            raise ConfigurationError("description must be a string and is_draft must be boolean.")
        data = object_value(
            self._connection.request(
                "POST",
                f"{self._path(repository)}/pullrequests",
                params=self._params,
                json_body={
                    "sourceRefName": source,
                    "targetRefName": target,
                    "title": title,
                    "description": description,
                    "isDraft": is_draft,
                },
                timeout=timeout,
            )
        )
        if data.get("sourceRefName") != source or data.get("targetRefName") != target:
            raise ProtocolError(
                "Azure DevOps returned different pull request branches than requested."
            )
        try:
            status = PullRequestStatus(string_value(data.get("status")))
        except ValueError:
            raise ProtocolError("Azure DevOps returned an unknown pull request status.") from None
        return PullRequest(
            int_value(data.get("pullRequestId")),
            string_value(required_object(data, "repository").get("id")),
            string_value(data.get("title")),
            source,
            target,
            status,
            bool_value(data.get("isDraft", False)),
        )


def _response_commit(value: JsonValue) -> str:
    try:
        return commit_id(string_value(value))
    except ConfigurationError:
        raise ProtocolError("Azure DevOps returned an invalid commit identifier.") from None
