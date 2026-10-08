"""The configured IIS resource entry point, without transport ownership."""

from conductor.iis.application_pools import ApplicationPools
from conductor.transports.protocols import PowerShellTransport


class IisClient:
    """Borrow a caller-owned remote transport; never open/close it or use local IIS.

    ``timeout`` is the total pool-operation/polling budget. ``poll_interval`` is
    the maximum delay between state reads. Individual remote commands are also
    bounded by the transport's command timeout. Instances are not thread-safe.
    """

    def __init__(
        self,
        *,
        transport: PowerShellTransport,
        timeout: float = 60.0,
        poll_interval: float = 1.0,
    ) -> None:
        self.application_pools = ApplicationPools(
            transport=transport, timeout=timeout, poll_interval=poll_interval
        )
