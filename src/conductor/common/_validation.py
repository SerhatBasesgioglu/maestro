"""Internal validation shared by polling and remoting configuration."""

import math
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from conductor.common.errors import ConfigurationError


def positive_seconds(value: float, name: str) -> float:
    """Reject unbounded, non-numeric, boolean, and non-positive time budgets."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(f"{name} must be a finite positive number of seconds.")
    try:
        seconds = float(value)
    except OverflowError:
        raise ConfigurationError(f"{name} must be a finite positive number of seconds.") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ConfigurationError(f"{name} must be a finite positive number of seconds.")
    return seconds


def non_empty(value: str, name: str) -> str:
    """Validate identifiers without echoing caller data in an error."""
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ConfigurationError(f"{name} must be a non-empty string without control characters.")
    return value


def secret_token(value: str) -> str:
    """Tokens must be non-empty printable ASCII without whitespace/header injection."""
    if not isinstance(value, str) or not value or any(not 33 <= ord(char) <= 126 for char in value):
        raise ConfigurationError("token must be non-empty printable ASCII without whitespace.")
    return value


def path_segment(value: str, name: str) -> str:
    """Encode a single resource identifier, rejecting traversal and double-decoding risks."""
    non_empty(value, name)
    if value in (".", "..") or any(char in value for char in "/\\%"):
        raise ConfigurationError(f"{name} must be a literal identifier, not a path or encoded URL.")
    return quote(value, safe="")


def https_base_url(value: str) -> str:
    """Validate an explicit credential-free HTTPS target, retaining a deployment base path."""
    message = "server URL must be explicit HTTPS without credentials, query, or fragment."
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 for char in value):
        raise ConfigurationError(message)
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "?" in value
            or "#" in value
            or "\\" in value
            or (parsed.port is not None and not 1 <= parsed.port <= 65535)
            or any(char in parsed.hostname for char in "/\\%@")
            or any(part in (".", "..") for part in unquote(parsed.path).split("/"))
        ):
            raise ConfigurationError(message)
    except ValueError:
        raise ConfigurationError(message) from None
    return value.rstrip("/")


def ca_file(value: str | Path | None) -> None:
    """Require an explicit CA file, never a boolean certificate-verification bypass."""
    if value is None:
        return
    if not isinstance(value, (str, Path)) or not value:
        raise ConfigurationError("ca_bundle must name a PEM CA file.")
    try:
        valid = Path(value).is_file()
    except (OSError, ValueError):
        valid = False
    if not valid:
        raise ConfigurationError("ca_bundle must name a PEM CA file.")


def remote_posix_path(value: str, name: str, *, file: bool = False) -> str:
    """Require an explicit remote Linux path, not a URL, glob, or relative path."""
    non_empty(value, name)
    if (
        not value.startswith("/")
        or value.startswith("//")
        or any(part in (".", "..") for part in value.split("/"))
        or any(char in value for char in "*?[]")
        or (file and (value == "/" or value.endswith("/")))
    ):
        raise ConfigurationError(f"{name} must be an absolute literal remote POSIX path.")
    return value


def local_regular_file(value: str | Path, name: str) -> Path:
    """Validate one literal local file, refusing a final-component symlink."""
    if not isinstance(value, (str, Path)) or not str(value):
        raise ConfigurationError(f"{name} must name an existing local regular file.")
    try:
        path = Path(value).absolute()
        valid = path.is_file() and not path.is_symlink()
    except (OSError, ValueError):
        valid = False
    if not valid or any(ord(char) < 32 or ord(char) == 127 for char in str(value)):
        raise ConfigurationError(f"{name} must name an existing local regular file.")
    return path
