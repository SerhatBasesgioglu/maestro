"""Secret and metadata observations returned by the OpenBao KV v2 API."""

from dataclasses import dataclass, field

from conductor.common.http import JsonValue


@dataclass(frozen=True)
class KvV2Metadata:
    version: int
    created_time: str
    deletion_time: str
    destroyed: bool
    custom_metadata: dict[str, JsonValue] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class KvV2Secret:
    data: dict[str, JsonValue] = field(repr=False)
    metadata: KvV2Metadata
