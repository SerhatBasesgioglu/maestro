import builtins
import json
from collections import deque

import pytest
from ruamel.yaml import YAML

import conductor.linux.compose as compose_module
from conductor.common.errors import (
    ConfigurationError,
    ConflictError,
    MissingDependencyError,
    PermissionDeniedError,
    ProtocolError,
    ResourceNotFoundError,
    TransportTimeoutError,
)
from conductor.linux import (
    ComposeFileError,
    ComposeProject,
    LinuxClient,
    LinuxCommandError,
    RsyncError,
    ServiceNotFoundError,
    ServiceState,
)
from conductor.transports import CommandResult

PROJECT = ComposeProject("/opt/my app/compose.yaml", "my-app")
DOCUMENT = (
    '# deployment\nservices:\n  api:\n    image: "registry/api:old" # pinned\n'
    "  worker:\n    image: registry/worker:old\n"
)


class FakeSsh:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.calls = []
        self.uploads = []

    def execute(self, arguments, *, input=None, timeout=None):
        self.calls.append((list(arguments), input, timeout))
        assert self.responses, "Unexpected remote command"
        result = self.responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    def upload(self, source, destination, *, dry_run=False, timeout=None):
        self.uploads.append((source, destination, dry_run, timeout))
        return self.responses.popleft()


def observation(content=DOCUMENT):
    return CommandResult(0, json.dumps({"content": content, "fingerprint": "a" * 64}))


@pytest.mark.parametrize("state", list(ServiceState))
def test_systemd_read_returns_typed_state_without_mutation(state):
    transport = FakeSsh(CommandResult(0, f"LoadState=loaded\nActiveState={state.value}\n"))
    result = LinuxClient(transport=transport).services.get_state("legacy")
    assert result is state
    assert transport.calls == [
        (
            [
                "systemctl",
                "show",
                "--property=LoadState",
                "--property=ActiveState",
                "--",
                "legacy.service",
            ],
            None,
            60,
        )
    ]
    assert transport.uploads == []


@pytest.mark.parametrize("operation", ["start", "stop"])
@pytest.mark.parametrize("sudo", [False, True])
def test_systemd_mutations_dispatch_once_without_workflows(operation, sudo):
    transport = FakeSsh(CommandResult(0))
    client = LinuxClient(transport=transport, sudo=sudo, timeout=30)
    result = getattr(client.services, operation)("legacy@blue.service", timeout=3)
    prefix = ["sudo", "-n", "--"] if sudo else []
    assert transport.calls == [
        ([*prefix, "systemctl", operation, "--", "legacy@blue.service"], None, 3)
    ]
    assert (result.name, result.operation) == ("legacy@blue.service", operation)
    assert transport.uploads == []


@pytest.mark.parametrize(
    "output,expected",
    [
        ("LoadState=not-found\nActiveState=inactive\n", ServiceNotFoundError),
        ("PRIVATE_DIAGNOSTIC", ProtocolError),
        ("ActiveState=active\n", ProtocolError),
        ("LoadState=loaded\nActiveState=active\nActiveState=inactive", ProtocolError),
    ],
)
def test_systemd_missing_and_malformed_states(output, expected):
    with pytest.raises(expected) as failure:
        LinuxClient(transport=FakeSsh(CommandResult(0, output))).services.get_state("legacy")
    assert "PRIVATE_DIAGNOSTIC" not in str(failure.value)


def test_unknown_systemd_state_is_not_reported_as_active():
    transport = FakeSsh(CommandResult(0, "LoadState=loaded\nActiveState=future\n"))
    assert LinuxClient(transport=transport).services.get_state("legacy") is ServiceState.UNKNOWN


@pytest.mark.parametrize("name", ["", "-option", "../app", "app*", "app;true", "app\n", None])
@pytest.mark.parametrize("operation", ["get_state", "start", "stop"])
def test_systemd_bad_names_fail_before_io(name, operation):
    transport = FakeSsh()
    with pytest.raises(ConfigurationError):
        getattr(LinuxClient(transport=transport).services, operation)(name)
    assert transport.calls == []


def test_failed_service_mutation_is_not_replayed_or_sanitization_bypassed():
    transport = FakeSsh(CommandResult(1, "PRIVATE", "PRIVATE"))
    with pytest.raises(LinuxCommandError) as failure:
        LinuxClient(transport=transport).services.stop("legacy")
    assert failure.value.exit_code == 1
    assert "PRIVATE" not in str(failure.value)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("dry_run", [False, True])
def test_legacy_rsync_upload_is_independent_and_not_elevated(tmp_path, dry_run):
    source = tmp_path / "app.jar"
    source.write_bytes(b"payload")
    transport = FakeSsh(CommandResult(0))
    result = LinuxClient(transport=transport, sudo=True).files.upload(
        source, "/opt/legacy/", dry_run=dry_run, timeout=5
    )
    assert transport.uploads == [(source, "/opt/legacy/", dry_run, 5)]
    assert result.destination == "/opt/legacy/" and result.dry_run is dry_run
    assert transport.calls == []


def test_failed_or_partial_rsync_upload_is_not_replayed(tmp_path):
    source = tmp_path / "app.jar"
    source.write_bytes(b"payload")
    transport = FakeSsh(CommandResult(23, "PRIVATE", "PRIVATE"))
    with pytest.raises(RsyncError) as failure:
        LinuxClient(transport=transport).files.upload(source, "/opt/legacy/app.jar")
    assert failure.value.exit_code == 23
    assert "PRIVATE" not in str(failure.value)
    assert len(transport.uploads) == 1 and transport.calls == []


@pytest.mark.parametrize("operation", ["up", "down"])
def test_compose_commands_use_explicit_project_socket_and_no_implicit_env(operation):
    transport = FakeSsh(CommandResult(0))
    result = getattr(LinuxClient(transport=transport, sudo=True).compose, operation)(PROJECT)
    arguments, input, timeout = transport.calls[0]
    assert arguments[:3] == ["sudo", "-n", "--"]
    assert arguments[arguments.index("--file") + 1] == PROJECT.compose_file
    assert arguments[arguments.index("--project-name") + 1] == PROJECT.project_name
    assert arguments[arguments.index("--project-directory") + 1] == "/opt/my app"
    assert arguments[arguments.index("--host") + 1] == "unix:///var/run/docker.sock"
    assert arguments[arguments.index("--env-file") + 1] == "/dev/null"
    assert "DOCKER_CONTEXT" in arguments and "COMPOSE_REMOVE_ORPHANS=false" in arguments
    if operation == "up":
        assert arguments[-3:] == ["up", "--detach", "--no-build"]
    else:
        assert arguments[-1] == "down"
    for unsafe in ("--volumes", "--rmi", "--remove-orphans", "--build"):
        assert unsafe not in arguments
    assert result.operation == operation
    assert input is None and timeout == 60
    assert len(transport.calls) == 1 and transport.uploads == []


def test_compose_accepts_explicit_env_file_and_reports_uncertain_mutation_without_replay():
    project = ComposeProject("/app/compose.yaml", "app", env_file="/app/prod.env")
    error = TransportTimeoutError("uncertain")
    transport = FakeSsh(error)
    with pytest.raises(TransportTimeoutError) as failure:
        LinuxClient(transport=transport).compose.up(project)
    assert failure.value is error
    arguments = transport.calls[0][0]
    assert arguments[arguments.index("--env-file") + 1] == "/app/prod.env"
    assert len(transport.calls) == 1


def test_compose_image_edit_changes_only_one_service_and_never_restarts_or_uploads():
    transport = FakeSsh(observation(), CommandResult(0, '{"changed":true}'))
    result = LinuxClient(transport=transport).compose.set_image(
        PROJECT, "api", "registry/api:new", expected_image="registry/api:old"
    )
    assert result.changed and result.previous_image == "registry/api:old"
    read, write = transport.calls
    assert read[0][-2:] == ["get", PROJECT.compose_file]
    assert write[0][-3:] == ["put", PROJECT.compose_file, "a" * 64]
    content = write[1].decode()
    assert "# deployment" in content and "# pinned" in content
    assert 'image: "registry/api:new"' in content
    parsed = YAML(typ="safe").load(content)
    assert parsed["services"]["worker"]["image"] == "registry/worker:old"
    assert transport.uploads == []


def test_compose_unchanged_image_is_a_read_only_noop():
    transport = FakeSsh(observation())
    result = LinuxClient(transport=transport).compose.set_image(PROJECT, "api", "registry/api:old")
    assert not result.changed and len(transport.calls) == 1


@pytest.mark.parametrize(
    "content,expected",
    [
        (DOCUMENT, ConflictError),
        ("services: {}", ResourceNotFoundError),
        ("services: []", ComposeFileError),
        ("services:\n  api:\n    image: old\n    image: other", ComposeFileError),
        ("services:\n  api: &shared\n    image: old\n  worker: *shared", ComposeFileError),
        ("services:\n  api:\n    image: 42", ComposeFileError),
        ("[broken", ComposeFileError),
    ],
)
def test_image_preconditions_fail_without_writing(content, expected):
    transport = FakeSsh(observation(content))
    with pytest.raises(expected):
        LinuxClient(transport=transport).compose.set_image(
            PROJECT, "api", "registry/api:new", expected_image="unexpected"
        )
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "status,expected",
    [
        (10, ResourceNotFoundError),
        (11, ConflictError),
        (13, PermissionDeniedError),
        (14, ComposeFileError),
        (20, LinuxCommandError),
        (127, LinuxCommandError),
    ],
)
def test_remote_file_failures_are_typed_without_replay(status, expected):
    transport = FakeSsh(observation(), CommandResult(status, "PRIVATE", "PRIVATE"))
    with pytest.raises(expected) as failure:
        LinuxClient(transport=transport).compose.set_image(PROJECT, "api", "registry/api:new")
    assert "PRIVATE" not in str(failure.value)
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "response",
    ["null", "bad", "{}", '{"content":"services: {}","fingerprint":"not-a-fingerprint"}'],
)
def test_malformed_remote_file_observation_never_writes(response):
    transport = FakeSsh(CommandResult(0, response))
    with pytest.raises(ProtocolError):
        LinuxClient(transport=transport).compose.set_image(PROJECT, "api", "registry/api:new")
    assert len(transport.calls) == 1


@pytest.mark.parametrize("response", ["null", "{}", '{"changed":1}', '{"changed":false}'])
def test_malformed_edit_confirmation_is_uncertain_not_replayed(response):
    transport = FakeSsh(observation(), CommandResult(0, response))
    with pytest.raises(ProtocolError):
        LinuxClient(transport=transport).compose.set_image(PROJECT, "api", "registry/api:new")
    assert len(transport.calls) == 2


def test_yaml_dependency_is_lazy_and_checked_before_remote_io(monkeypatch):
    original = builtins.__import__

    def without_yaml(name, *args, **kwargs):
        if name == "conductor.linux._yaml":
            raise ImportError("PRIVATE")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_yaml)
    transport = FakeSsh()
    with pytest.raises(MissingDependencyError, match=r"conductor\[linux\]"):
        LinuxClient(transport=transport).compose.set_image(PROJECT, "api", "registry/api:new")
    assert transport.calls == []


def test_yaml_parser_warnings_do_not_leak_file_contents(capsys):
    document = (
        "x-one: &SECRET_ANCHOR {}\nx-two: &SECRET_ANCHOR {}\nservices:\n  api:\n    image: old\n"
    )
    transport = FakeSsh(observation(document))
    with pytest.raises(ComposeFileError) as failure:
        LinuxClient(transport=transport).compose.set_image(PROJECT, "api", "new")
    assert "SECRET_ANCHOR" not in str(failure.value)
    assert capsys.readouterr().err == ""
    assert len(transport.calls) == 1


def test_image_edit_budget_covers_read_parse_write_and_rejects_late_success(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(compose_module.time, "monotonic", lambda: clock[0])

    class SlowSsh(FakeSsh):
        def execute(self, *args, **kwargs):
            result = super().execute(*args, **kwargs)
            clock[0] += 2
            return result

    transport = SlowSsh(observation(), CommandResult(0, '{"changed":true}'))
    with pytest.raises(TransportTimeoutError):
        LinuxClient(transport=transport).compose.set_image(
            PROJECT, "api", "registry/api:new", timeout=3
        )
    assert [call[2] for call in transport.calls] == [3, 1]


@pytest.mark.parametrize(
    "field,value",
    [
        ("compose_file", "relative"),
        ("compose_file", "/x/../y"),
        ("compose_file", "/x/*"),
        ("compose_file", "/dir/"),
        ("project_name", "-option"),
        ("project_name", "Uppercase"),
        ("docker_socket", "tcp://host"),
        ("env_file", "relative"),
    ],
)
def test_compose_configuration_rejects_implicit_targets(field, value):
    values = {
        "compose_file": PROJECT.compose_file,
        "project_name": PROJECT.project_name,
        field: value,
    }
    with pytest.raises(ConfigurationError):
        ComposeProject(**values)


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "30"])
def test_linux_timeouts_are_finite_and_never_extended(value, tmp_path):
    transport = FakeSsh()
    with pytest.raises(ConfigurationError):
        LinuxClient(transport=transport, timeout=value)
    client = LinuxClient(transport=transport)
    for operation in (
        lambda: client.services.start("legacy", timeout=value),
        lambda: client.compose.down(PROJECT, timeout=value),
        lambda: client.compose.set_image(PROJECT, "api", "new", timeout=value),
    ):
        with pytest.raises(ConfigurationError):
            operation()
    assert transport.calls == []


def test_bad_image_identifiers_fail_before_io():
    transport = FakeSsh()
    client = LinuxClient(transport=transport)
    for service, image in (("-service", "valid"), ("api", "bad image"), ("api", "${IMAGE}")):
        with pytest.raises(ConfigurationError):
            client.compose.set_image(PROJECT, service, image)
    with pytest.raises(ConfigurationError):
        LinuxClient(transport=transport, sudo="yes")
    assert transport.calls == []
