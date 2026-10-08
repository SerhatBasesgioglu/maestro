"""Azure DevOps Git and pipeline operations; optional HTTP dependencies load lazily."""

from conductor.azure_devops.client import AzureDevOpsClient
from conductor.azure_devops.config import AzureDevOpsConfig
from conductor.azure_devops.errors import AzureDevOpsError, BranchConflictError, BranchUpdateError
from conductor.azure_devops.models import (
    CommitChange,
    GitBranch,
    GitChangeType,
    GitCommit,
    GitContentType,
    GitRepository,
    PipelineRun,
    PipelineRunResult,
    PipelineRunState,
    PipelineVariable,
    PullRequest,
    PullRequestStatus,
)

__all__ = [
    "AzureDevOpsClient",
    "AzureDevOpsConfig",
    "AzureDevOpsError",
    "BranchConflictError",
    "BranchUpdateError",
    "CommitChange",
    "GitBranch",
    "GitChangeType",
    "GitCommit",
    "GitContentType",
    "GitRepository",
    "PipelineRun",
    "PipelineRunResult",
    "PipelineRunState",
    "PipelineVariable",
    "PullRequest",
    "PullRequestStatus",
]
