"""Bounded, remote-only Windows Service Control Manager operations."""

import json
import unicodedata
from dataclasses import dataclass
from typing import Literal

from maestro.common._validation import positive_seconds
from maestro.common.errors import AuthenticationError, PermissionDeniedError
from maestro.transports.models import PowerShellErrorCategory, PowerShellResult
from maestro.transports.protocols import PowerShellTransport
from maestro.windows._scripts import WINDOWS_SERVICE
from maestro.windows.errors import (
    ServiceCommandError,
    ServiceDependencyError,
    ServiceDisabledError,
    ServiceNotFoundError,
    ServiceProtocolError,
    ServiceStateError,
)
from maestro.windows.models import ServiceChangeResult, ServiceState

_INVALID_NAME_CHARACTERS = set("/\\*?[]")
_SERVICE_NAME_MAX_LENGTH = 256
_ERROR_NOT_FOUND = "Maestro.WindowsServiceNotFound"
_ERROR_DENIED = "Maestro.WindowsServiceDenied"
_ERROR_DEPENDENTS = "Maestro.WindowsServiceDependentServices"
_ERROR_DISABLED = "Maestro.WindowsServiceDisabled"
_ERROR_INVALID_STATE = "Maestro.WindowsServiceInvalidState"


@dataclass(frozen=True)
class _Snapshot:
    initial_state: ServiceState
    final_state: ServiceState
    changed: bool


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            "Windows service names must be nonblank literal SCM names of at most 256 characters."
        )
    try:
        name_length = len(name.encode("utf-16-le", errors="strict")) // 2
    except UnicodeEncodeError:
        raise ValueError(
            "Windows service names must be nonblank literal SCM names of at most 256 characters."
        ) from None
    if name_length > _SERVICE_NAME_MAX_LENGTH or any(
        char in _INVALID_NAME_CHARACTERS or unicodedata.category(char) == "Cc" for char in name
    ):
        raise ValueError(
            "Windows service names must be nonblank literal SCM names of at most 256 characters."
        )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _reject_json_constant(_: str) -> None:
    raise ValueError


def _decode(result: PowerShellResult, operation: Literal["Get", "Start", "Stop"]) -> _Snapshot:
    # Prefer shared connection/authentication categories and access denial over
    # markers that could otherwise suggest a missing service.
    if any(error.category == PowerShellErrorCategory.AUTHENTICATION for error in result.errors):
        raise AuthenticationError("Remote authentication failed.")
    if any(error.category == PowerShellErrorCategory.PERMISSION_DENIED for error in result.errors):
        raise PermissionDeniedError("Access to the remote Windows service was denied.")
    identifiers = {error.error_id.split(",", 1)[0] for error in result.errors}
    if _ERROR_DENIED in identifiers:
        raise PermissionDeniedError("Access to the remote Windows service was denied.")
    if _ERROR_NOT_FOUND in identifiers:
        raise ServiceNotFoundError("The requested Windows service does not exist.")
    if _ERROR_DEPENDENTS in identifiers:
        raise ServiceDependencyError(
            "Dependent services prevent the requested Windows service from stopping."
        )
    if _ERROR_DISABLED in identifiers:
        raise ServiceDisabledError("The requested Windows service is disabled.")
    if _ERROR_INVALID_STATE in identifiers:
        raise ServiceStateError(
            "The observed Windows service state does not permit this operation."
        )
    if result.errors:
        raise ServiceCommandError(
            "Remote Windows service management failed; check permissions and service configuration."
        )
    if len(result.output) != 1 or not isinstance(result.output[0], str):
        raise ServiceProtocolError("Remote Windows service management returned an invalid result.")
    try:
        payload = json.loads(
            result.output[0],
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if (
            not isinstance(payload, dict)
            or set(payload) != {"initial_state", "final_state", "changed"}
            or not isinstance(payload["initial_state"], str)
            or not isinstance(payload["final_state"], str)
            or type(payload["changed"]) is not bool
        ):
            raise ValueError
        snapshot = _Snapshot(
            initial_state=ServiceState(payload["initial_state"]),
            final_state=ServiceState(payload["final_state"]),
            changed=payload["changed"],
        )
        if not _consistent(snapshot, operation):
            raise ValueError
        return snapshot
    except (ValueError, TypeError):
        raise ServiceProtocolError(
            "Remote Windows service management returned an invalid result."
        ) from None


def _consistent(snapshot: _Snapshot, operation: Literal["Get", "Start", "Stop"]) -> bool:
    if operation == "Get":
        return not snapshot.changed and snapshot.initial_state == snapshot.final_state
    if operation == "Start":
        if snapshot.changed:
            return snapshot.initial_state == ServiceState.STOPPED
        return (
            snapshot.initial_state == ServiceState.RUNNING
            and snapshot.final_state == ServiceState.RUNNING
        )
    if snapshot.changed:
        return snapshot.initial_state in (ServiceState.RUNNING, ServiceState.PAUSED)
    return (
        snapshot.initial_state == ServiceState.STOPPED
        and snapshot.final_state == ServiceState.STOPPED
    )


class WindowsServices:
    """Read, start, and stop exact remote SCM service names in one script call.

    Per-call timeouts are passed to the remoting transport and inherit its
    cooperative in-flight limits; a synchronous SCM command is not hard-cancelled.
    """

    def __init__(self, *, transport: PowerShellTransport, timeout: float = 60.0) -> None:
        self._transport = transport
        self._timeout = positive_seconds(timeout, "timeout")

    def get_state(self, name: str, *, timeout: float | None = None) -> ServiceState:
        """Return one observed native service state without mutation."""
        _validate_name(name)
        budget = self._budget(timeout)
        return self._execute(name, "Get", budget).initial_state

    def start(self, name: str, *, timeout: float | None = None) -> ServiceChangeResult:
        """Start a stopped service or no-op if running; never resume or enable it.

        One remote command observes the current state, optionally dispatches
        Start(), and observes again. ``changed`` reports dispatch, not readiness.
        """
        return self._change(name, "Start", timeout)

    def stop(self, name: str, *, timeout: float | None = None) -> ServiceChangeResult:
        """Stop a running or paused service or no-op if stopped.

        Dependent services are never force-stopped. One remote call performs the
        state check, optional dispatch, and final observation.
        """
        return self._change(name, "Stop", timeout)

    def _budget(self, timeout: float | None) -> float:
        return (
            self._timeout
            if timeout is None
            else min(self._timeout, positive_seconds(timeout, "timeout"))
        )

    def _execute(
        self, name: str, operation: Literal["Get", "Start", "Stop"], timeout: float
    ) -> _Snapshot:
        result = self._transport.execute(
            WINDOWS_SERVICE,
            parameters={"Name": name, "Operation": operation},
            timeout=timeout,
        )
        return _decode(result, operation)

    def _change(
        self, name: str, operation: Literal["Start", "Stop"], timeout: float | None
    ) -> ServiceChangeResult:
        _validate_name(name)
        snapshot = self._execute(name, operation, self._budget(timeout))
        return ServiceChangeResult(
            name=name,
            initial_state=snapshot.initial_state,
            final_state=snapshot.final_state,
            changed=snapshot.changed,
        )
