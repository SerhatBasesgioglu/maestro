"""Stable transport results, independent of pypsrp's response classes."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TypeAlias

PowerShellValue: TypeAlias = str | bool | int | float | None


class PowerShellErrorCategory(StrEnum):
    """Normalized PowerShell error categories; raw remote messages are discarded."""

    PERMISSION_DENIED = "permission_denied"
    AUTHENTICATION = "authentication"
    OBJECT_NOT_FOUND = "object_not_found"
    COMMAND_FAILED = "command_failed"


@dataclass(frozen=True)
class PowerShellError:
    """A remote error's category and identifier, without invocation details.

    Identifiers are untrusted remote data and intentionally excluded from repr.
    An object-not-found category alone does not identify which resource is missing.
    """

    category: PowerShellErrorCategory
    error_id: str = field(default="", repr=False)


@dataclass(frozen=True)
class PowerShellResult:
    """Scalar output and normalized errors; output is omitted from repr for safety.

    Return complex data as JSON in PowerShell and decode it in the integration.
    Warning, verbose, debug, and invocation streams are not exposed or logged.
    """

    output: tuple[PowerShellValue, ...] = field(default=(), repr=False)
    errors: tuple[PowerShellError, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    """An SSH/rsync exit status; potentially sensitive output is never represented."""

    exit_code: int
    stdout: str = field(default="", repr=False)
    stderr: str = field(default="", repr=False)
