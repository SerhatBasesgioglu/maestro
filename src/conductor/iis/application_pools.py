"""State-oriented, bounded, remote-only IIS application-pool operations."""

import json
import time
from dataclasses import dataclass
from typing import Literal

from conductor.common._validation import positive_seconds
from conductor.common.errors import AuthenticationError
from conductor.iis._scripts import APPLICATION_POOL
from conductor.iis.errors import (
    ApplicationPoolNotFoundError,
    ApplicationPoolStateError,
    ApplicationPoolTimeoutError,
    IisCommandError,
    IisPermissionDeniedError,
    IisProtocolError,
)
from conductor.iis.models import ApplicationPoolChangeResult, ApplicationPoolState
from conductor.transports.models import PowerShellErrorCategory, PowerShellResult
from conductor.transports.protocols import PowerShellTransport

_TRANSITIONAL = {ApplicationPoolState.STARTING, ApplicationPoolState.STOPPING}
_INVALID_NAME_CHARACTERS = set('\\/:*?[]"<>|')


@dataclass(frozen=True)
class _Snapshot:
    state: ApplicationPoolState
    changed: bool


def _validate_name(name: str) -> None:
    if (
        not isinstance(name, str)
        or not name.strip()
        or any(
            char in _INVALID_NAME_CHARACTERS or ord(char) < 32 or ord(char) == 127 for char in name
        )
    ):
        raise ValueError("Pool names must be non-empty literal names without paths or wildcards.")


def _decode(result: PowerShellResult) -> _Snapshot:
    # Prioritize denied/authentication errors over any additional missing errors.
    if any(error.category == PowerShellErrorCategory.AUTHENTICATION for error in result.errors):
        raise AuthenticationError("Remote authentication failed.")
    if any(error.category == PowerShellErrorCategory.PERMISSION_DENIED for error in result.errors):
        raise IisPermissionDeniedError("Access to the remote IIS application pool was denied.")
    if any(
        error.error_id.split(",", 1)[0] == "Conductor.ApplicationPoolNotFound"
        for error in result.errors
    ):
        raise ApplicationPoolNotFoundError(
            "The requested remote IIS application pool does not exist."
        )
    if result.errors:
        raise IisCommandError(
            "Remote IIS management failed; check server prerequisites and permissions."
        )
    if len(result.output) != 1 or not isinstance(result.output[0], str):
        raise IisProtocolError("Remote IIS returned an invalid state result.")
    try:
        payload = json.loads(result.output[0])
        if (
            not isinstance(payload, dict)
            or set(payload) != {"state", "changed"}
            or not isinstance(payload["state"], str)
            or type(payload["changed"]) is not bool
        ):
            raise ValueError
        return _Snapshot(ApplicationPoolState(payload["state"]), payload["changed"])
    except (ValueError, TypeError):
        raise IisProtocolError("Remote IIS returned an invalid state result.") from None


class ApplicationPools:
    """Pool state/start/stop operations on one explicitly configured remote target.

    These methods require WebAdministration, the IIS provider, and permission to
    read/manage the named pool. They do not approve deployments or serialize
    independent callers. Callers must coordinate deployments targeting one pool.
    """

    def __init__(
        self,
        *,
        transport: PowerShellTransport,
        timeout: float = 60.0,
        poll_interval: float = 1.0,
    ) -> None:
        self._transport = transport
        self._timeout = positive_seconds(timeout, "timeout")
        self._poll_interval = positive_seconds(poll_interval, "poll_interval")

    def get_state(self, name: str, *, timeout: float | None = None) -> ApplicationPoolState:
        """Read a pool's state without mutation, including transitional/unknown states.

        Raises ApplicationPoolNotFoundError, IisPermissionDeniedError,
        IisCommandError, IisProtocolError, or a shared transport/authentication
        exception. ``timeout`` bounds this read, subject to transport limits.
        """
        _validate_name(name)
        budget = self._timeout if timeout is None else positive_seconds(timeout, "timeout")
        snapshot = self._execute(name, "Get", budget)
        if snapshot.changed:
            raise IisProtocolError("Remote IIS reported a mutation during a state read.")
        return snapshot.state

    def start(self, name: str, *, timeout: float | None = None) -> ApplicationPoolChangeResult:
        """Ensure STARTED and wait for confirmation; already STARTED is a no-op.

        Wait through transitions before deciding whether to mutate. At most one
        Start request is dispatched. UNKNOWN fails without mutation. Raises the
        get_state exceptions, ApplicationPoolStateError, or
        ApplicationPoolTimeoutError. A transport failure may leave an uncertain
        outcome; this method never blindly replays it. No application-health check.
        """
        return self._ensure(name, ApplicationPoolState.STARTED, "Start", timeout)

    def stop(self, name: str, *, timeout: float | None = None) -> ApplicationPoolChangeResult:
        """Ensure STOPPED and wait for confirmation; already STOPPED is a no-op.

        Wait through transitions before deciding whether to mutate. At most one
        Stop request is dispatched. UNKNOWN fails without mutation. Exceptions
        and timeout behavior match start(). Cleanup/restoration policy is owned
        by the caller, not by this method.
        """
        return self._ensure(name, ApplicationPoolState.STOPPED, "Stop", timeout)

    def _execute(
        self, name: str, operation: Literal["Get", "Start", "Stop"], timeout: float
    ) -> _Snapshot:
        return _decode(
            self._transport.execute(
                APPLICATION_POOL,
                parameters={"Name": name, "Operation": operation},
                timeout=timeout,
            )
        )

    def _ensure(
        self,
        name: str,
        desired: ApplicationPoolState,
        operation: Literal["Start", "Stop"],
        timeout: float | None,
    ) -> ApplicationPoolChangeResult:
        _validate_name(name)
        budget = self._timeout if timeout is None else positive_seconds(timeout, "timeout")
        deadline = time.monotonic() + budget
        last_state: ApplicationPoolState | None = None

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise ApplicationPoolTimeoutError(name, desired, last_state, budget)
            return seconds

        snapshot = self._execute(name, "Get", remaining())
        if snapshot.changed:
            raise IisProtocolError("Remote IIS reported a mutation during a state read.")
        initial = last_state = snapshot.state
        changed = False
        dispatched = False
        while True:
            remaining()
            if last_state == desired:
                return ApplicationPoolChangeResult(name, initial, last_state, changed)
            if last_state == ApplicationPoolState.UNKNOWN:
                raise ApplicationPoolStateError(
                    "Application pool state is Unknown; no further mutation will be attempted."
                )
            if last_state not in _TRANSITIONAL and not dispatched:
                dispatched = True
                snapshot = self._execute(name, operation, remaining())
                changed = snapshot.changed
                last_state = snapshot.state
                continue
            time.sleep(min(self._poll_interval, remaining()))
            snapshot = self._execute(name, "Get", remaining())
            if snapshot.changed:
                raise IisProtocolError("Remote IIS reported a mutation during a state read.")
            last_state = snapshot.state
