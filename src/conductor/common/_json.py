"""Small strict readers for library-owned models; unknown extra server fields are ignored."""

from collections.abc import Mapping

from conductor.common.errors import ProtocolError
from conductor.common.http import JsonValue


def object_value(value: JsonValue) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ProtocolError("Remote API returned an unexpected object schema.")
    return value


def array_value(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ProtocolError("Remote API returned an unexpected array schema.")
    return value


def string_value(value: JsonValue) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError("Remote API returned an unexpected string field.")
    return value


def optional_string(value: JsonValue) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ProtocolError("Remote API returned an unexpected string field.")
    return value or None


def int_value(value: JsonValue) -> int:
    if type(value) is not int or value <= 0:
        raise ProtocolError("Remote API returned an unexpected positive integer field.")
    return value


def bool_value(value: JsonValue) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError("Remote API returned an unexpected boolean field.")
    return value


def required_object(data: Mapping[str, JsonValue], key: str) -> dict[str, JsonValue]:
    return object_value(data.get(key))
