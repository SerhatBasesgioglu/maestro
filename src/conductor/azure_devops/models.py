"""Stable Azure DevOps inputs/results; REST response bodies are not the public contract."""

import base64
from dataclasses import dataclass, field
from enum import StrEnum

from conductor.common._validation import non_empty
from conductor.common.errors import ConfigurationError


@dataclass(frozen=True)
class GitRepository:
    id: str
    name: str
    default_branch: str | None


@dataclass(frozen=True)
class GitBranch:
    """A canonical refs/heads name and its current commit."""

    name: str
    commit_id: str


class GitChangeType(StrEnum):
    ADD = "add"
    EDIT = "edit"
    DELETE = "delete"


class GitContentType(StrEnum):
    RAW_TEXT = "rawText"
    BASE64 = "base64Encoded"


@dataclass(frozen=True)
class CommitChange:
    """An add/edit/delete at an absolute repository path; content is never shown in repr."""

    path: str
    change_type: GitChangeType
    content: str | None = field(default=None, repr=False)
    content_type: GitContentType = GitContentType.RAW_TEXT

    def __post_init__(self) -> None:
        non_empty(self.path, "file path")
        if (
            not self.path.startswith("/")
            or self.path == "/"
            or "\\" in self.path
            or any(part in ("", ".", "..") for part in self.path[1:].split("/"))
        ):
            raise ConfigurationError(
                "file path must be absolute within the repository, without traversal."
            )
        if not isinstance(self.change_type, GitChangeType) or not isinstance(
            self.content_type, GitContentType
        ):
            raise ConfigurationError("Use documented Git change/content enums.")
        if self.change_type == GitChangeType.DELETE:
            if self.content is not None:
                raise ConfigurationError("Delete changes must not supply content.")
        elif not isinstance(self.content, str):
            raise ConfigurationError(
                "Add/edit changes require string content, including empty files."
            )
        elif self.content_type == GitContentType.BASE64:
            try:
                base64.b64decode(self.content, validate=True)
            except ValueError:
                raise ConfigurationError("Base64 file content is invalid.") from None


@dataclass(frozen=True)
class GitCommit:
    """A newly created commit which was atomically pushed to the named branch."""

    commit_id: str
    branch: str
    message: str = field(repr=False)


class PullRequestStatus(StrEnum):
    NOT_SET = "notSet"
    ACTIVE = "active"
    ABANDONED = "abandoned"
    COMPLETED = "completed"


@dataclass(frozen=True)
class PullRequest:
    id: int
    repository_id: str
    title: str
    source_branch: str
    target_branch: str
    status: PullRequestStatus
    is_draft: bool


class PipelineRunState(StrEnum):
    UNKNOWN = "unknown"
    IN_PROGRESS = "inProgress"
    CANCELING = "canceling"
    COMPLETED = "completed"


class PipelineRunResult(StrEnum):
    UNKNOWN = "unknown"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass(frozen=True)
class PipelineVariable:
    value: str = field(repr=False)
    is_secret: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or type(self.is_secret) is not bool:
            raise ConfigurationError(
                "Pipeline variables require string values and a boolean secret flag."
            )


@dataclass(frozen=True)
class PipelineRun:
    """A submitted/observed run, not proof of pipeline completion or success."""

    id: int
    pipeline_id: int
    state: PipelineRunState
    result: PipelineRunResult | None
