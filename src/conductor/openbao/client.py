"""Configured OpenBao KV v2 client with caller-managed token lifecycle."""

from types import TracebackType
from typing import Self

from conductor.common.http import HttpTransport, _JsonConnection
from conductor.openbao.config import OpenBaoConfig
from conductor.openbao.kv_v2 import KvV2


class OpenBaoClient:
    """Synchronous read-only client; owns default HTTP and borrows injected transports."""

    def __init__(self, config: OpenBaoConfig, *, transport: HttpTransport | None = None) -> None:
        self.config = config
        headers = {"X-Vault-Token": config.token}
        if config.namespace is not None:
            headers["X-Vault-Namespace"] = config.namespace
        self._connection = _JsonConnection(
            base_url=config.server_url,
            headers=headers,
            timeout=config.timeout,
            ca_bundle=config.ca_bundle,
            transport=transport,
        )
        self.kv_v2 = KvV2(self._connection)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._connection.exit(exc_type)

    def close(self) -> None:
        self._connection.close()
