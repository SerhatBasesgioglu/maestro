"""Standalone rsync uploads for legacy files; no service/container coordination."""

from pathlib import Path

from conductor.common._validation import local_regular_file, positive_seconds, remote_posix_path
from conductor.common.errors import ConfigurationError
from conductor.linux.errors import RsyncError
from conductor.linux.models import FileUploadResult
from conductor.transports.protocols import SshTransport


class RsyncFiles:
    """Upload one regular file without directory mirroring or deletions.

    The SSH user must own access to the destination; LinuxClient's sudo switch
    does not elevate rsync. Destination trailing-slash semantics are native rsync.
    """

    def __init__(self, transport: SshTransport, *, timeout: float) -> None:
        self._transport = transport
        self._timeout = timeout

    def upload(
        self,
        source: str | Path,
        destination: str,
        *,
        dry_run: bool = False,
        timeout: float | None = None,
    ) -> FileUploadResult:
        source_path = local_regular_file(source, "source")
        remote_posix_path(destination, "destination")
        if type(dry_run) is not bool:
            raise ConfigurationError("dry_run must be a boolean.")
        budget = (
            self._timeout
            if timeout is None
            else min(self._timeout, positive_seconds(timeout, "timeout"))
        )
        result = self._transport.upload(source_path, destination, dry_run=dry_run, timeout=budget)
        if result.exit_code != 0:
            raise RsyncError(result.exit_code)
        return FileUploadResult(str(source_path), destination, dry_run)
