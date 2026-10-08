import json
import os
import shlex
import signal
import subprocess
import traceback
from dataclasses import replace

import pytest

import conductor.transports.ssh as adapter
from conductor.common.errors import (
    ConfigurationError,
    MissingDependencyError,
    ProtocolError,
    TransportError,
    TransportTimeoutError,
)
from conductor.transports import CommandResult, OpenSshTransport, SshConfig

pytestmark = pytest.mark.skipif(os.name != "posix", reason="The process adapter requires POSIX.")


@pytest.fixture
def config(tmp_path):
    key = tmp_path / "private key"
    hosts = tmp_path / 'known "hosts"'
    key.write_text("FAKE_KEY_FOR_OFFLINE_TESTS")
    hosts.write_text("FAKE_KNOWN_HOST_FOR_OFFLINE_TESTS")
    return SshConfig("linux.example.invalid", "deploy", key, hosts, port=2222)


class FakeProcess:
    pid = 424242

    def __init__(self, stdout=b"", stderr=b"", exit_code=0, error=None):
        self.returncode = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.calls = []

    def communicate(self, *, input=None, timeout=None):
        self.calls.append((input, timeout))
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        return self.stdout, self.stderr


@pytest.fixture
def process(monkeypatch):
    calls = []
    child = FakeProcess()

    def create(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return child

    monkeypatch.setattr(adapter.subprocess, "Popen", create)
    monkeypatch.setattr(adapter.shutil, "which", lambda name: f"/trusted/{name}")
    return child, calls


def test_constructor_is_lazy_and_representations_exclude_identity(config, process):
    transport = OpenSshTransport(config)
    assert process[1] == []
    for value in (config.username, str(config.identity_file), str(config.known_hosts)):
        assert value not in repr(config)
        assert value not in repr(transport)
    assert "PRIVATE" not in repr(CommandResult(0, "PRIVATE", "PRIVATE"))


def test_ssh_quotes_remote_tokens_and_requires_explicit_trust_and_identity(config, process):
    child, calls = process
    child.stdout, child.stderr = b"ok", b"PRIVATE_DIAGNOSTIC"
    remote = ["printf", "%s", "literal'; touch /unexpected", "space here", ""]
    result = OpenSshTransport(config).execute(remote, input=b"DATA", timeout=2.25)
    command, kwargs = calls[0]
    assert command[:4] == ["/trusted/ssh", "-F", os.devnull, "-T"]
    assert command[-3:-1] == ["--", config.host]
    assert shlex.split(command[-1]) == remote
    assert command[-9:-3] == ["-i", str(config.identity_file), "-l", "deploy", "-p", "2222"]
    options = dict(argument.split("=", 1) for argument in command if "=" in argument)
    assert options["StrictHostKeyChecking"] == "yes"
    assert json.loads(options["UserKnownHostsFile"]) == str(config.known_hosts)
    assert options["GlobalKnownHostsFile"] == os.devnull
    assert options["IdentityAgent"] == "none"
    assert options["CertificateFile"] == "none"
    assert options["ConnectionAttempts"] == "1"
    assert options["ConnectTimeout"] == "3"
    for name in ("ForwardAgent", "ForwardX11", "PermitLocalCommand", "PasswordAuthentication"):
        assert options[name] == "no"
    assert kwargs["start_new_session"] is True
    assert "shell" not in kwargs
    assert child.calls == [(b"DATA", 2.25)]
    assert result.stdout == "ok"
    assert "PRIVATE_DIAGNOSTIC" not in repr(result)


def test_process_environment_cannot_enable_implicit_agent_or_rsync_behavior(
    config, process, monkeypatch
):
    monkeypatch.setenv("SSH_AUTH_SOCK", "/private/agent")
    monkeypatch.setenv("SSH_ASKPASS", "/private/program")
    monkeypatch.setenv("RSYNC_RSH", "untrusted-shell")
    monkeypatch.setenv("RSYNC_OLD_ARGS", "1")
    OpenSshTransport(config).execute(["true"])
    environment = process[1][0][1]["env"]
    assert not any(
        name in environment
        for name in ("SSH_AUTH_SOCK", "SSH_ASKPASS", "RSYNC_RSH", "RSYNC_OLD_ARGS")
    )
    assert environment["SSH_ASKPASS_REQUIRE"] == "never"
    assert environment["LC_ALL"] == "C"


def test_timeout_override_cannot_extend_transport_ceiling(config, process):
    OpenSshTransport(replace(config, command_timeout=3)).execute(["true"], timeout=10)
    assert process[0].calls == [(None, 3)]


@pytest.mark.parametrize("status", [1, 127])
def test_remote_command_failures_are_returned_without_replay(config, process, status):
    process[0].returncode = status
    result = OpenSshTransport(config).execute(["false"])
    assert result.exit_code == status
    assert len(process[1]) == 1


def test_ssh_255_is_a_sanitized_transport_failure(config, process):
    process[0].returncode = 255
    process[0].stderr = b"PRIVATE_DIAGNOSTIC"
    with pytest.raises(TransportError) as failure:
        OpenSshTransport(config).execute(["true"])
    assert "PRIVATE_DIAGNOSTIC" not in "".join(traceback.format_exception(failure.value))
    assert len(process[1]) == 1


@pytest.mark.parametrize(
    "error,expected",
    [
        (subprocess.TimeoutExpired("PRIVATE", 1), TransportTimeoutError),
        (OSError("PRIVATE"), TransportError),
        (KeyboardInterrupt(), KeyboardInterrupt),
    ],
)
def test_timeout_and_interruption_kill_local_process_group_without_replay(
    config, process, monkeypatch, error, expected
):
    child, calls = process
    child.error = error
    killed = []
    monkeypatch.setattr(adapter.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    with pytest.raises(expected) as failure:
        OpenSshTransport(config).execute(["mutating-command"], timeout=1)
    assert killed == [(child.pid, signal.SIGKILL)]
    assert child.calls == [(None, 1), (None, None)]
    assert len(calls) == 1
    assert "PRIVATE" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize(
    "error,expected",
    [
        (FileNotFoundError("PRIVATE"), MissingDependencyError),
        (PermissionError("PRIVATE"), TransportError),
    ],
)
def test_launch_failures_are_sanitized(config, process, monkeypatch, error, expected):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(adapter.subprocess, "Popen", fail)
    with pytest.raises(expected) as failure:
        OpenSshTransport(config).execute(["true"])
    assert "PRIVATE" not in "".join(traceback.format_exception(failure.value))


def test_missing_executable_fails_lazily(config, process, monkeypatch):
    monkeypatch.setattr(adapter.shutil, "which", lambda name: None)
    transport = OpenSshTransport(config)
    with pytest.raises(MissingDependencyError):
        transport.execute(["true"])
    assert process[1] == []


def test_non_utf8_output_is_a_sanitized_protocol_error(config, process):
    process[0].stdout = b"\xff"
    with pytest.raises(ProtocolError):
        OpenSshTransport(config).execute(["true"])


@pytest.mark.parametrize("arguments", [[], "true", [1], [""], ["true", "\x00"]])
def test_invalid_commands_fail_before_execution(config, process, arguments):
    with pytest.raises(ConfigurationError):
        OpenSshTransport(config).execute(arguments)
    assert process[1] == []


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "10"])
def test_invalid_budgets_fail_before_execution(config, process, value):
    with pytest.raises(ConfigurationError):
        replace(config, command_timeout=value)
    with pytest.raises(ConfigurationError):
        OpenSshTransport(config).execute(["true"], timeout=value)
    assert process[1] == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("host", "-option"),
        ("host", "user@host"),
        ("host", "host;true"),
        ("host", "ssh://host"),
        ("username", "-option"),
        ("username", "user name"),
        ("port", True),
        ("port", 0),
        ("port", 65536),
        ("identity_file", "/missing-key"),
        ("known_hosts", "/missing-hosts"),
        ("ssh_executable", ""),
        ("rsync_executable", "x\n"),
    ],
)
def test_invalid_configuration(config, field, value):
    with pytest.raises(ConfigurationError):
        replace(config, **{field: value})


def test_ssh_expansion_tokens_and_symlink_keys_are_refused(config, tmp_path):
    for name in ("key%h", "key${HOME}"):
        path = tmp_path / name
        path.write_text("FAKE")
        with pytest.raises(ConfigurationError):
            replace(config, identity_file=path)
    link = tmp_path / "link"
    link.symlink_to(config.identity_file)
    with pytest.raises(ConfigurationError):
        replace(config, identity_file=link)


def test_rsync_uses_same_ssh_policy_protects_paths_and_never_deletes(config, process, tmp_path):
    source = tmp_path / "legacy file';touch unwanted"
    source.write_text("payload")
    destination = "/opt/legacy dir/file';touch unwanted"
    OpenSshTransport(config).upload(source, destination, dry_run=True, timeout=4)
    arguments, kwargs = process[1][0]
    assert arguments[0] == "/trusted/rsync"
    assert "--protect-args" in arguments and "--checksum" in arguments
    assert "--dry-run" in arguments
    assert arguments[-3:] == ["--", str(source), f"{config.host}:{destination}"]
    assert not any(flag in arguments for flag in ("--delete", "--inplace", "--remove-source-files"))
    rsh = arguments[arguments.index("--rsh") + 1]
    assert '"StrictHostKeyChecking=yes"' in rsh
    assert '"-p" "2222"' in rsh
    assert '"IdentityAgent=none"' in rsh
    assert '""' in rsh  # Embedded quotes use rsync's doubled-quote convention.
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert process[0].calls == [(None, 4)]


@pytest.mark.parametrize("destination", ["relative", "host:/path", "/../path", "/x/*", "/x\n"])
def test_invalid_upload_destination_is_rejected(config, process, tmp_path, destination):
    source = tmp_path / "source"
    source.write_text("payload")
    with pytest.raises(ConfigurationError):
        OpenSshTransport(config).upload(source, destination)
    assert process[1] == []


def test_upload_validation_and_input_validation(config, process, tmp_path):
    source = tmp_path / "source"
    source.write_text("payload")
    transport = OpenSshTransport(config)
    for value in (tmp_path, tmp_path / "missing", None, ""):
        with pytest.raises(ConfigurationError):
            transport.upload(value, "/target")
    with pytest.raises(ConfigurationError):
        transport.upload(source, "/target", dry_run=1)
    with pytest.raises(ConfigurationError):
        transport.execute(["true"], input="not bytes")
    assert process[1] == []
