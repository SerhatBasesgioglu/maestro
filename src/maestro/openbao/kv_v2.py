"""One-shot, read-only KV v2 secret reads."""

from unicodedata import category
from urllib.parse import quote

from maestro.common._json import (
    bool_value,
    int_value,
    object_value,
    required_object,
    string_value,
)
from maestro.common.errors import ConfigurationError, ProtocolError, ResourceNotFoundError
from maestro.common.http import JsonValue, _JsonConnection
from maestro.openbao.models import KvV2Metadata, KvV2Secret

_UNREADABLE_MESSAGE = "Requested KV v2 secret version is absent or unreadable."


class KvV2:
    """Read exactly one explicitly named KV v2 secret version per call."""

    def __init__(self, connection: _JsonConnection) -> None:
        self._connection = connection

    def read(
        self,
        mount: str,
        path: str,
        *,
        version: int | None = None,
        timeout: float | None = None,
    ) -> KvV2Secret:
        """Read a secret without logging or exposing its value through errors."""
        encoded_mount = _logical_path(mount, "mount")
        encoded_path = _logical_path(path, "path")
        if version is not None and (type(version) is not int or version <= 0):
            raise ConfigurationError("version must be a positive integer when provided.")
        params = None if version is None else {"version": str(version)}
        try:
            response = self._connection.request(
                "GET",
                f"v1/{encoded_mount}/data/{encoded_path}",
                params=params,
                timeout=timeout,
            )
        except ResourceNotFoundError:
            raise ResourceNotFoundError(_UNREADABLE_MESSAGE) from None

        envelope = object_value(response)
        payload = required_object(envelope, "data")
        metadata = _metadata(required_object(payload, "metadata"))
        secret_value = payload.get("data")
        if metadata.destroyed or (secret_value is None and metadata.deletion_time):
            raise ResourceNotFoundError(_UNREADABLE_MESSAGE)
        if secret_value is None:
            raise ProtocolError("OpenBao returned an unexpected KV v2 secret schema.")
        secret_data = object_value(secret_value)
        return KvV2Secret(secret_data, metadata)


def _logical_path(value: str, name: str) -> str:
    """Encode slash-delimited logical paths without allowing URL syntax or traversal."""
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a non-empty slash-separated logical path.")
    components = value.split("/")
    if any(
        not component
        or component in (".", "..")
        or any(char in component for char in ("\\", "%"))
        or any(category(char) in ("Cc", "Cs") for char in component)
        for component in components
    ):
        raise ConfigurationError(f"{name} must contain safe literal path components.")
    return "/".join(quote(component, safe="") for component in components)


def _metadata(data: dict[str, JsonValue]) -> KvV2Metadata:
    version = int_value(data.get("version"))
    created_time = string_value(data.get("created_time"))
    deletion_time_value = data.get("deletion_time")
    if not isinstance(deletion_time_value, str):
        raise ProtocolError("OpenBao returned an unexpected KV v2 metadata schema.")
    destroyed = bool_value(data.get("destroyed"))
    custom_metadata_value = data.get("custom_metadata")
    custom_metadata = None if custom_metadata_value is None else object_value(custom_metadata_value)
    return KvV2Metadata(version, created_time, deletion_time_value, destroyed, custom_metadata)
