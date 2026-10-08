"""Explicit key-based OpenSSH and rsync processes, without Python SDK dependencies."""

import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from conductor.common._validation import local_regular_file, positive_seconds, remote_posix_path
from conductor.common.errors import (
    ConfigurationError,
    MissingDependencyError,
    ProtocolError,
    TransportError,
    TransportTimeoutError,
)
from conductor.transports.models import CommandResult


@dataclass(frozen=True)
class SshConfig:
    """A literal host/user/key and caller-approved known-hosts file.

    No password, agent, SSH config, proxy, forwarding, or trust-on-first-use
    discovery. The private key must work non-interactively without an agent.
    Real execution currently requires a POSIX caller with OpenSSH installed.
    """

    host: str
    username: str = field(repr=False)
    identity_file: str | Path = field(repr=False)
    known_hosts: str | Path = field(repr=False)
    port: int = 22
    connection_timeout: float = 10.0
    command_timeout: float = 60.0
    ssh_executable: str = "ssh"
    rsync_executable: str = "rsync"

    def __post_init__(self) -> None:
        if not isinstance(self.host, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9.-]*", self.host
        ):
            raise ConfigurationError("host must be a literal DNS name or IPv4 address.")
        if not isinstance(self.username, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.-]*[$]?", self.username
        ):
            raise ConfigurationError("username must be a literal Linux account name.")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ConfigurationError("port must be an integer between 1 and 65535.")
        for name in ("identity_file", "known_hosts"):
            path = local_regular_file(getattr(self, name), name)
            # OpenSSH expands these tokens even in explicitly supplied file paths.
            if any(char in str(path) for char in "%$"):
                raise ConfigurationError("SSH file paths cannot contain expansion tokens.")
            object.__setattr__(self, name, path)
        for name in ("ssh_executable", "rsync_executable"):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise ConfigurationError(f"{name} must name a trusted local executable.")
        for name in ("connection_timeout", "command_timeout"):
            object.__setattr__(self, name, positive_seconds(getattr(self, name), name))


def _executable(value: str) -> str:
    executable = shutil.which(value)
    if executable is None:
        raise MissingDependencyError("Install/configure the requested local OpenSSH or rsync tool.")
    return executable


def _rsync_shell(arguments: list[str]) -> str:
    # rsync's -e parser doubles quotes; it does NOT use shell backslash escaping.
    return " ".join('"' + argument.replace('"', '""') + '"' for argument in arguments)


def _run(arguments: list[str], *, input: bytes | None, timeout: float) -> CommandResult:
    if os.name != "posix":
        raise ConfigurationError("The OpenSSH/rsync process adapter requires a POSIX caller.")
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("SSH_", "RSYNC_"))
    }
    environment.update(LC_ALL="C", SSH_ASKPASS_REQUIRE="never")
    try:
        process = subprocess.Popen(
            arguments,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except FileNotFoundError:
        raise MissingDependencyError(
            "The configured local SSH/rsync executable is unavailable."
        ) from None
    except OSError:
        raise TransportError("Could not launch the configured local SSH/rsync process.") from None
    try:
        stdout, stderr = process.communicate(input=input, timeout=timeout)
    except BaseException as error:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        if isinstance(error, subprocess.TimeoutExpired):
            raise TransportTimeoutError(
                "SSH/rsync exceeded its process budget; a remote mutation may have completed."
            ) from None
        if isinstance(error, OSError):
            raise TransportError("SSH/rsync process communication failed.") from None
        raise
    try:
        return CommandResult(process.returncode, stdout.decode("utf-8"), stderr.decode("utf-8"))
    except UnicodeError:
        raise ProtocolError("SSH/rsync returned output that is not UTF-8.") from None


class OpenSshTransport:
    """Lazy per-call SSH processes; construction never opens a connection.

    There is no persistent session to open/close. Per-call timeouts tighten the
    command ceiling. Local process groups are killed on timeout/interruption,
    but neither that nor connection loss proves remote cancellation/rollback.
    """

    def __init__(self, config: SshConfig) -> None:
        self.config = config

    def __repr__(self) -> str:
        return "OpenSshTransport()"

    def _budget(self, timeout: float | None) -> float:
        return (
            self.config.command_timeout
            if timeout is None
            else min(self.config.command_timeout, positive_seconds(timeout, "timeout"))
        )

    def _ssh_arguments(self, budget: float) -> list[str]:
        config = self.config
        options = {
            "BatchMode": "yes",
            "StrictHostKeyChecking": "yes",
            "UserKnownHostsFile": json.dumps(str(config.known_hosts), ensure_ascii=False),
            "GlobalKnownHostsFile": os.devnull,
            "UpdateHostKeys": "no",
            "VerifyHostKeyDNS": "no",
            "IdentitiesOnly": "yes",
            "IdentityAgent": "none",
            "CertificateFile": "none",
            "PreferredAuthentications": "publickey",
            "PasswordAuthentication": "no",
            "KbdInteractiveAuthentication": "no",
            "GSSAPIAuthentication": "no",
            "HostbasedAuthentication": "no",
            "ForwardAgent": "no",
            "ForwardX11": "no",
            "ClearAllForwardings": "yes",
            "PermitLocalCommand": "no",
            "ProxyCommand": "none",
            "ControlMaster": "no",
            "ControlPath": "none",
            "ConnectionAttempts": "1",
            "ConnectTimeout": str(math.ceil(min(config.connection_timeout, budget))),
        }
        arguments = [_executable(config.ssh_executable), "-F", os.devnull, "-T"]
        for name, value in options.items():
            arguments.extend(("-o", f"{name}={value}"))
        arguments.extend(
            ("-i", str(config.identity_file), "-l", config.username, "-p", str(config.port))
        )
        return arguments

    def execute(
        self,
        arguments: Sequence[str],
        *,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute trusted argv remotely, quoting each token for the remote POSIX shell.

        Return nonzero remote statuses for the integration to interpret. SSH's
        ambiguous status 255 raises TransportError without exposing diagnostics.
        This is not a sandbox or an authorization boundary.
        """
        if (
            isinstance(arguments, (str, bytes))
            or not arguments
            or any(not isinstance(value, str) or "\x00" in value for value in arguments)
            or not arguments[0]
        ):
            raise ConfigurationError(
                "Remote commands must be a non-empty sequence of string tokens."
            )
        if input is not None and not isinstance(input, bytes):
            raise ConfigurationError("Remote command input must be bytes.")
        budget = self._budget(timeout)
        result = _run(
            [*self._ssh_arguments(budget), "--", self.config.host, shlex.join(arguments)],
            input=input,
            timeout=budget,
        )
        if result.exit_code == 255:
            raise TransportError("SSH connection/authentication/host-key verification failed.")
        return result

    def upload(
        self,
        source: str | Path,
        destination: str,
        *,
        dry_run: bool = False,
        timeout: float | None = None,
    ) -> CommandResult:
        """Rsync one regular file over the same verified SSH target; never delete files.

        Requires patched rsync 3.x on both ends. Paths travel using --protect-args;
        no remote sudo, source removal, directory mirroring, or local fallback.
        """
        source_path = local_regular_file(source, "source")
        remote_posix_path(destination, "destination")
        if type(dry_run) is not bool:
            raise ConfigurationError("dry_run must be a boolean.")
        budget = self._budget(timeout)
        arguments = [
            _executable(self.config.rsync_executable),
            "--protect-args",
            "--checksum",
            "--times",
            "--perms",
            "--no-owner",
            "--no-group",
            "--rsh",
            _rsync_shell(self._ssh_arguments(budget)),
        ]
        if dry_run:
            arguments.append("--dry-run")
        arguments.extend(("--", str(source_path), f"{self.config.host}:{destination}"))
        return _run(arguments, input=None, timeout=budget)
