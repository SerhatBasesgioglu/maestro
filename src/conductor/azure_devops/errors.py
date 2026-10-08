"""Azure DevOps business failures, including errors delivered inside HTTP 200 responses."""

from conductor.common.errors import ConductorError, ConflictError


class AzureDevOpsError(ConductorError):
    """An Azure DevOps resource operation failed."""


class BranchUpdateError(AzureDevOpsError):
    """A ref update was rejected; server custom messages are deliberately discarded."""

    def __init__(self, update_status: str) -> None:
        self.update_status = update_status
        super().__init__("Azure DevOps rejected the branch reference update.")


class BranchConflictError(BranchUpdateError, ConflictError):
    """Expected previous commit, branch lock, or ref-name conflict prevented the update."""
