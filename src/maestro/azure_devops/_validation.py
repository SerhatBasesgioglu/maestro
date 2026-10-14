"""Git-specific validation; no external commands or credentials are involved."""

import re

from maestro.common._validation import non_empty
from maestro.common.errors import ConfigurationError

ZERO_COMMIT = "0" * 40


def commit_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9a-fA-F]{40}", value)
        or value == ZERO_COMMIT
    ):
        raise ConfigurationError(
            "commit identifiers must be non-zero 40-character SHA-1 hex strings."
        )
    return value.lower()


def branch_ref(value: str) -> str:
    non_empty(value, "branch")
    name = value.removeprefix("refs/heads/")
    if (
        name.startswith(("refs/", "-", "/"))
        or name.endswith(("/", "."))
        or any(char in name for char in " ~^:?*[\\")
        or ".." in name
        or "@{" in name
        or name == "@"
        or any(
            not part or part.startswith(".") or part.endswith(".lock") for part in name.split("/")
        )
    ):
        raise ConfigurationError("branch must be a valid Git branch name or refs/heads reference.")
    return f"refs/heads/{name}"


def positive_id(value: int, name: str) -> int:
    if type(value) is not int or value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer.")
    return value
