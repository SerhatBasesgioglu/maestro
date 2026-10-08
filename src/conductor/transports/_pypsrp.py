"""Private pypsrp 0.9 adapter. Imported only when a caller opens a transport.

WSMan's receive normally retries HTTP read timeouts, and PowerShell.invoke loops
through WSMan operation timeouts indefinitely. The adapter disables the former
and surrounds open/dispatch/poll/cleanup with a monotonic deadline.
"""

import math
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import requests
from pypsrp.complex_objects import PSInvocationState
from pypsrp.exceptions import AuthenticationError as SdkAuthenticationError
from pypsrp.exceptions import WinRMTransportError, WSManFaultError
from pypsrp.powershell import PowerShell, RunspacePool
from pypsrp.wsman import WSMan
from spnego.exceptions import SpnegoError

from conductor.common.errors import (
    AuthenticationError,
    ConductorError,
    MissingDependencyError,
    PermissionDeniedError,
    TlsError,
    TransportError,
    TransportTimeoutError,
)
from conductor.transports.models import (
    PowerShellError,
    PowerShellErrorCategory,
    PowerShellResult,
    PowerShellValue,
)
from conductor.transports.powershell_remoting import PowerShellRemotingConfig, _parse_endpoint


class _DeadlineExceeded(TimeoutError):
    pass


class _DeadlineWSMan(WSMan):
    """Enforce a time budget at each WSMan boundary, including SDK open loops."""

    def __init__(self, config: PowerShellRemotingConfig) -> None:
        self._config = config
        self._deadline: float | None = None
        host, port, path = _parse_endpoint(config.endpoint)
        super().__init__(
            server=host,
            port=port,
            path=path,
            ssl=True,
            auth=config.auth,
            username=config.username,
            password=config.password,
            # SDK annotations omit its documented CA-path support and the
            # fractional timeouts accepted by requests underneath it.
            cert_validation=str(config.ca_bundle) if config.ca_bundle is not None else True,  # type: ignore[arg-type]
            connection_timeout=config.connection_timeout,  # type: ignore[arg-type]
            read_timeout=config.request_timeout,  # type: ignore[arg-type]
            operation_timeout=1,
            reconnection_retries=0,
            no_proxy=True,
            negotiate_delegate=False,
            negotiate_send_cbt=True,
        )

    @contextmanager
    def budget(self, seconds: float) -> Iterator[None]:
        previous = self._deadline
        self._deadline = time.monotonic() + seconds
        try:
            yield
        finally:
            self._deadline = previous

    def remaining(self) -> float:
        if self._deadline is None:
            raise TransportError("A remoting request was attempted without a time budget.")
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise _DeadlineExceeded
        return remaining

    def invoke(self, *args: Any, **kwargs: Any) -> Any:
        remaining = self.remaining()
        # Reserve a share for both connect and read. WSMan's timeout override
        # would reset requests' HTTP timeout to operation_timeout + 2, so don't
        # pass it through: configure the envelope and HTTP timeouts separately.
        self.transport.connection_timeout = min(self._config.connection_timeout, remaining / 2)  # type: ignore[assignment]
        self.transport.read_timeout = min(self._config.request_timeout, remaining / 2)  # type: ignore[assignment]
        self.operation_timeout = max(1, min(5, math.floor(self.transport.read_timeout - 0.5)))
        positional = list(args)
        if len(positional) > 5:
            positional[5] = None
            kwargs.pop("timeout", None)
        else:
            kwargs["timeout"] = None
        kwargs["retries_on_read_timeout"] = 0
        return super().invoke(*positional, **kwargs)


def _translate(exc: Exception, phase: str) -> ConductorError:
    if isinstance(exc, ConductorError):
        return exc
    if isinstance(exc, (SdkAuthenticationError, SpnegoError)):
        return AuthenticationError("Remote authentication failed.")
    if isinstance(exc, requests.exceptions.SSLError):
        return TlsError("Remote TLS verification or negotiation failed.")
    if isinstance(exc, (_DeadlineExceeded, requests.exceptions.Timeout, TimeoutError)):
        return TransportTimeoutError(
            f"PowerShell {phase} timed out; a dispatched command may have completed."
        )
    if isinstance(exc, WinRMTransportError):
        if exc.code == 401:
            return AuthenticationError("Remote authentication failed.")
        if exc.code == 403:
            return PermissionDeniedError("Remote access was denied.")
    if isinstance(exc, WSManFaultError):
        if exc.code in (5, 0x80070005):
            return PermissionDeniedError("Remote access was denied.")
        if exc.code == 0x80338029:
            return TransportTimeoutError(f"PowerShell {phase} timed out.")
    if isinstance(exc, ImportError):
        return MissingDependencyError(
            "Remoting authentication dependencies are unavailable. On non-Windows systems, "
            "install conductor[windows,kerberos] or conductor[iis,kerberos] and configure "
            "system Kerberos support."
        )
    # Do not chain external errors or retain their response bodies in exceptions.
    return TransportError("Remote communication failed; a dispatched command may have completed.")


def _error_snapshot(record: Any) -> PowerShellError:
    category = {
        13: PowerShellErrorCategory.OBJECT_NOT_FOUND,
        17: PowerShellErrorCategory.PERMISSION_DENIED,
        18: PowerShellErrorCategory.PERMISSION_DENIED,
        24: PowerShellErrorCategory.AUTHENTICATION,
    }.get(record.category, PowerShellErrorCategory.COMMAND_FAILED)
    error_id = record.fq_error if isinstance(record.fq_error, str) else ""
    return PowerShellError(category=category, error_id=error_id)


class _PypsrpSession:
    def __init__(self, config: PowerShellRemotingConfig) -> None:
        self.config = config
        self._closed = False
        try:
            self.connection = _DeadlineWSMan(config)
            self.pool = RunspacePool(
                self.connection,
                configuration_name=config.configuration_name,
                min_runspaces=1,
                max_runspaces=1,
                no_profile=True,
                session_key_timeout_ms=max(1, int(config.command_timeout * 1000)),
            )
        except Exception as exc:
            raise _translate(exc, "configuration") from None

    def open(self) -> None:
        try:
            with self.connection.budget(self.config.command_timeout):
                self.pool.open()
                self.connection.remaining()
        except Exception as exc:
            raise _translate(exc, "connection") from None

    def execute(
        self, script: str, parameters: Mapping[str, PowerShellValue], timeout: float
    ) -> PowerShellResult:
        phase = "command"
        try:
            with self.connection.budget(timeout):
                pipeline = PowerShell(self.pool)
                pipeline.add_script(script)
                for name, value in parameters.items():
                    pipeline.add_parameter(name, value)
                pipeline.begin_invoke()
                while pipeline.state == PSInvocationState.RUNNING:
                    self.connection.remaining()
                    pipeline.poll_invoke()
                self.connection.remaining()
                if pipeline.state not in (
                    PSInvocationState.COMPLETED,
                    PSInvocationState.FAILED,
                    PSInvocationState.STOPPED,
                ):
                    raise TransportError("The remote pipeline ended in an unexpected state.")
                output: list[PowerShellValue] = []
                for value in pipeline.output:
                    if value is not None and type(value) not in (str, bool, int, float):
                        raise TransportError(
                            "Remote output must contain scalars; return JSON for data."
                        )
                    output.append(value)
                errors = tuple(_error_snapshot(record) for record in pipeline.streams.error)
                if not errors and (
                    pipeline.had_errors or pipeline.state != PSInvocationState.COMPLETED
                ):
                    errors = (PowerShellError(PowerShellErrorCategory.COMMAND_FAILED),)
                result = PowerShellResult(output=tuple(output), errors=errors)
            phase = "cleanup"
            with self.connection.budget(self.config.cleanup_timeout):
                pipeline.close()
            return result
        except Exception as exc:
            raise _translate(exc, phase) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        failure: ConductorError | None = None
        try:
            with self.connection.budget(self.config.cleanup_timeout):
                self.pool.close()
        except Exception as exc:
            failure = _translate(exc, "cleanup")
        finally:
            try:
                self.connection.close()
            except Exception as exc:
                if failure is None:
                    failure = _translate(exc, "cleanup")
        if failure is not None:
            raise failure from None
