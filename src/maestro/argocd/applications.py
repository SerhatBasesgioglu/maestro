"""Read, request sync, and poll health under one finite monotonic budget."""

import time

from maestro.argocd.config import ArgoCdConfig
from maestro.argocd.errors import ApplicationHealthTimeoutError, ArgoCdError
from maestro.argocd.models import (
    Application,
    ApplicationHealth,
    ApplicationSyncStatus,
    OperationPhase,
)
from maestro.common._json import (
    array_value,
    object_value,
    optional_string,
    required_object,
    string_value,
)
from maestro.common._validation import non_empty, path_segment, positive_seconds
from maestro.common.errors import ConfigurationError, ProtocolError
from maestro.common.http import JsonValue, _JsonConnection


class Applications:
    def __init__(self, connection: _JsonConnection, config: ArgoCdConfig) -> None:
        self._connection = connection
        self._config = config

    def _path(self, name: str) -> str:
        return f"api/v1/applications/{path_segment(name, 'application name')}"

    def _target_params(self) -> dict[str, str]:
        params = {"project": self._config.project}
        if self._config.application_namespace is not None:
            params["appNamespace"] = self._config.application_namespace
        return params

    def _decode(self, value: JsonValue, name: str) -> Application:
        data = object_value(value)
        metadata = required_object(data, "metadata")
        spec = required_object(data, "spec")
        status = object_value(data.get("status", {}))
        health = object_value(status.get("health", {}))
        sync = object_value(status.get("sync", {}))
        operation = object_value(status.get("operationState", {}))
        pending = data.get("operation")
        if pending is not None:
            object_value(pending)
        phase = optional_string(operation.get("phase"))
        try:
            result = Application(
                name=string_value(metadata.get("name")),
                project=string_value(spec.get("project")),
                namespace=optional_string(metadata.get("namespace")),
                health=ApplicationHealth(string_value(health.get("status", "Unknown"))),
                sync_status=ApplicationSyncStatus(string_value(sync.get("status", "Unknown"))),
                revision=optional_string(sync.get("revision")),
                revisions=tuple(
                    string_value(item) for item in array_value(sync.get("revisions", []))
                ),
                operation_phase=OperationPhase(phase) if phase is not None else None,
                operation_pending=pending is not None,
            )
        except ValueError:
            raise ProtocolError("Argo CD returned an unknown application state.") from None
        if result.name != name or result.project != self._config.project:
            raise ProtocolError(
                "Argo CD returned an application outside the requested target/project."
            )
        if (
            self._config.application_namespace is not None
            and result.namespace != self._config.application_namespace
        ):
            raise ProtocolError("Argo CD returned an application outside the requested namespace.")
        return result

    def get(self, name: str, *, timeout: float | None = None) -> Application:
        """Read an application with AppProject read permission; preserve denied-vs-missing errors.

        Requires the configured explicit project. Missing observed status becomes
        UNKNOWN; malformed status is a ProtocolError. Server error messages are
        not exposed. Does not refresh tokens, mutate, or follow redirects.
        """
        return self._decode(
            self._connection.request(
                "GET", self._path(name), params=self._target_params(), timeout=timeout
            ),
            name,
        )

    def sync(
        self,
        name: str,
        *,
        revision: str | None = None,
        prune: bool = False,
        dry_run: bool = False,
        timeout: float | None = None,
    ) -> Application:
        """Request one sync with project sync permission; does not wait for deployment success.

        Pruning is false by default and may delete managed resources when enabled.
        dry_run explicitly requests a server preview; neither replaces approval.
        revision is the single-source selector; omit it to use configured targets.
        Multi-source explicit revision vectors are not implemented. No write retries.
        """
        path = self._path(name)
        if type(prune) is not bool or type(dry_run) is not bool:
            raise ConfigurationError("prune and dry_run must be booleans.")
        body: dict[str, JsonValue] = {
            "name": name,
            "project": self._config.project,
            "prune": prune,
            "dryRun": dry_run,
        }
        if revision is not None:
            body["revision"] = non_empty(revision, "revision")
        if self._config.application_namespace is not None:
            body["appNamespace"] = self._config.application_namespace
        return self._decode(
            self._connection.request("POST", f"{path}/sync", json_body=body, timeout=timeout), name
        )

    def wait_for_health(
        self,
        name: str,
        *,
        timeout: float = 300.0,
        poll_interval: float = 2.0,
        require_synced: bool = True,
        revision: str | None = None,
    ) -> Application:
        """Wait for HEALTHY, optionally SYNCED and an exact observed single-source revision.

        Pending/running/terminating operations cannot satisfy the wait. Use an
        immutable commit revision to avoid observing an older healthy deployment;
        a branch name is not proof of a specific revision. This is a state check,
        not a deployment transaction or application endpoint-health probe.
        Auth/permission/transport failures propagate immediately without retries.
        """
        self._path(name)
        budget = positive_seconds(timeout, "timeout")
        interval = positive_seconds(poll_interval, "poll_interval")
        if type(require_synced) is not bool:
            raise ConfigurationError("require_synced must be boolean.")
        if revision is not None:
            non_empty(revision, "revision")
        deadline = time.monotonic() + budget
        last: Application | None = None

        def remaining() -> float:
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise ApplicationHealthTimeoutError(name, budget, last)
            return seconds

        while True:
            last = self.get(name, timeout=remaining())
            remaining()
            if revision is not None and len(last.revisions) > 1:
                raise ArgoCdError(
                    "A single revision health wait cannot verify a multi-source application."
                )
            if (
                last.health == ApplicationHealth.HEALTHY
                and (not require_synced or last.sync_status == ApplicationSyncStatus.SYNCED)
                and (revision is None or last.revision == revision)
                and not last.operation_pending
                and last.operation_phase not in (OperationPhase.RUNNING, OperationPhase.TERMINATING)
            ):
                return last
            time.sleep(min(interval, remaining()))
