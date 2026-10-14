"""Portable artifact-key and integrity-input validation."""

import re

from conductor.common.errors import ConfigurationError

_KEY_COMPONENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z", re.ASCII)
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"}


def validate_key(key: str) -> str:
    """Require a conservative portable relative key, without echoing caller input."""
    message = "artifact key must be a portable relative slash-separated path."
    if not isinstance(key, str) or not key or key.startswith("/") or key.endswith("/"):
        raise ConfigurationError(message)

    components = key.split("/")
    if any(not _valid_component(component) for component in components):
        raise ConfigurationError(message)
    return key


def _valid_component(component: str) -> bool:
    if (
        component in (".", "..")
        or component.endswith((".", " "))
        or _KEY_COMPONENT.fullmatch(component) is None
    ):
        return False
    basename = component.split(".", 1)[0].upper()
    return not (
        basename in _WINDOWS_RESERVED
        or (len(basename) == 4 and basename[:3] in ("COM", "LPT") and basename[3] in "123456789")
    )


def validate_sha256(value: str) -> str:
    """Validate and normalize a hexadecimal SHA-256 digest."""
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ConfigurationError("sha256 must be exactly 64 hexadecimal characters.")
    return value.lower()
