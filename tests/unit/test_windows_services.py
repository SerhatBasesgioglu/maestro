import json
from collections import deque
from dataclasses import FrozenInstanceError

import pytest

from conductor.common.errors import (
    AuthenticationError,
    ConfigurationError,
    PermissionDeniedError,
    TransportError,
    TransportTimeoutError,
)
from conductor.transports import PowerShellError, PowerShellErrorCategory, PowerShellResult
from conductor.windows import (
    ServiceChangeResult,
    ServiceCommandError,
    ServiceDependencyError,
    ServiceDisabledError,
    ServiceNotFoundError,
    ServiceProtocolError,
    ServiceState,
    ServiceStateError,
    WindowsClient,
)
from conductor.windows._scripts import WINDOWS_SERVICE

S = ServiceState


def snapshot(initial: S, final: S | None = None, changed: bool = False) -> PowerShellResult:
    return PowerShellResult(
        output=(
            json.dumps(
                {
                    "initial_state": initial.value,
                    "final_state": initial.value if final is None else final.value,
                    "changed": changed,
                }
            ),
        )
    )


class FakeTransport:
    def __init__(
        self, *responses: PowerShellResult | Exception, repeat: PowerShellResult | None = None
    ) -> None:
        self.responses = deque(responses)
        self.repeat = repeat
        self.calls: list[tuple[str, dict[str, object], float | None]] = []
        self.opened = False
        self.closed = False

    def execute(self, script, *, parameters=None, timeout=None):
        self.calls.append((script, dict(parameters or {}), timeout))
        result = self.responses.popleft() if self.responses else self.repeat
        assert result is not None, "Unexpected remote call"
        if isinstance(result, Exception):
            raise result
        return result

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.closed = True

    @property
    def operations(self) -> list[object]:
        return [parameters["Operation"] for _, parameters, _ in self.calls]


@pytest.mark.parametrize("state", list(S))
def test_get_state_returns_every_native_and_unknown_state(state):
    transport = FakeTransport(snapshot(state))

    assert WindowsClient(transport=transport).services.get_state("MyService") is state
    assert transport.operations == ["Get"]
    assert transport.calls[0][2] == 60


@pytest.mark.parametrize(
    "method,initial,final,changed",
    [
        ("start", S.RUNNING, S.RUNNING, False),
        ("stop", S.STOPPED, S.STOPPED, False),
        ("start", S.STOPPED, S.START_PENDING, True),
        ("stop", S.PAUSED, S.STOP_PENDING, True),
    ],
)
def test_start_stop_are_single_call_and_return_remote_observations(method, initial, final, changed):
    transport = FakeTransport(snapshot(initial, final, changed))
    result = getattr(WindowsClient(transport=transport).services, method)("MyService")

    assert isinstance(result, ServiceChangeResult)
    assert (result.name, result.initial_state, result.final_state, result.changed) == (
        "MyService",
        initial,
        final,
        changed,
    )
    assert len(transport.calls) == 1
    assert transport.operations == [method.capitalize()]
    with pytest.raises(FrozenInstanceError):
        result.changed = not changed


@pytest.mark.parametrize(("method", "operation"), [("start", "Start"), ("stop", "Stop")])
def test_invalid_state_marker_is_translated_without_replay(method, operation):
    transport = FakeTransport(
        PowerShellResult(
            errors=(
                PowerShellError(
                    PowerShellErrorCategory.COMMAND_FAILED,
                    "Conductor.WindowsServiceInvalidState,Write-Error",
                ),
            )
        )
    )
    with pytest.raises(ServiceStateError):
        getattr(WindowsClient(transport=transport).services, method)("MyService")
    assert transport.operations == [operation]


@pytest.mark.parametrize(
    "name", ["LiteralService", "Service with spaces", "服務", "svc'; Write-Output 'injected"]
)
def test_names_are_bound_data_not_script_source(name):
    transport = FakeTransport(snapshot(S.RUNNING))
    WindowsClient(transport=transport).services.get_state(name)

    script, parameters, _ = transport.calls[0]
    assert script == WINDOWS_SERVICE
    assert parameters == {"Name": name, "Operation": "Get"}
    assert name not in script


@pytest.mark.parametrize("name", ["s", "s" * 256, "\U0001f642" * 128])
def test_names_at_supported_utf16_lengths_are_accepted(name):
    transport = FakeTransport(snapshot(S.RUNNING))
    assert WindowsClient(transport=transport).services.get_state(name) is S.RUNNING


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        "../Service",
        "x/y",
        "x\\y",
        "*",
        "x?",
        "x[y]",
        "x\x00",
        "x\n",
        "x\x7f",
        "x\x85",
        "\U0001f642" * 129,
        pytest.param("x\ud800", id="lone-high-surrogate"),
        pytest.param("x\udfff", id="lone-low-surrogate"),
        "s" * 257,
        None,
        1,
    ],
)
@pytest.mark.parametrize("method", ["get_state", "start", "stop"])
def test_invalid_names_fail_before_remote_io(name, method):
    transport = FakeTransport()
    with pytest.raises(ValueError):
        getattr(WindowsClient(transport=transport).services, method)(name)
    assert transport.calls == []


@pytest.mark.parametrize(
    "category,identifier,error_class",
    [
        (
            PowerShellErrorCategory.OBJECT_NOT_FOUND,
            "Conductor.WindowsServiceNotFound,Write-Error",
            ServiceNotFoundError,
        ),
        (
            PowerShellErrorCategory.COMMAND_FAILED,
            "Conductor.WindowsServiceDependentServices,Write-Error",
            ServiceDependencyError,
        ),
        (
            PowerShellErrorCategory.COMMAND_FAILED,
            "Conductor.WindowsServiceDisabled,Write-Error",
            ServiceDisabledError,
        ),
        (
            PowerShellErrorCategory.COMMAND_FAILED,
            "Conductor.WindowsServiceDenied,Write-Error",
            PermissionDeniedError,
        ),
        (
            PowerShellErrorCategory.COMMAND_FAILED,
            "Conductor.WindowsServiceInvalidState,Write-Error",
            ServiceStateError,
        ),
        (PowerShellErrorCategory.OBJECT_NOT_FOUND, "Other.ObjectNotFound", ServiceCommandError),
        (PowerShellErrorCategory.COMMAND_FAILED, "Other.Command", ServiceCommandError),
        (
            PowerShellErrorCategory.PERMISSION_DENIED,
            "Conductor.WindowsServiceNotFound,Write-Error",
            PermissionDeniedError,
        ),
        (
            PowerShellErrorCategory.AUTHENTICATION,
            "Conductor.WindowsServiceNotFound,Write-Error",
            AuthenticationError,
        ),
    ],
)
def test_remote_error_categories_and_own_markers_are_distinguished(
    category, identifier, error_class
):
    transport = FakeTransport(PowerShellResult(errors=(PowerShellError(category, identifier),)))
    with pytest.raises(error_class):
        WindowsClient(transport=transport).services.get_state("MyService")


@pytest.mark.parametrize(
    "output",
    [
        (),
        ("bad-json",),
        ("[]",),
        ("null",),
        ("{}",),
        ('{"initial_state":"Running","final_state":"Running","changed":0}',),
        ('{"initial_state":true,"final_state":"Running","changed":false}',),
        ('{"initial_state":"Running","final_state":"Running","changed":false,"extra":1}',),
        (
            '{"initial_state":"Running","initial_state":"Stopped","final_state":"Stopped","changed":false}',
        ),
        ('{"initial_state":"Unknown-ish","final_state":"Unknown-ish","changed":false}',),
        ('{"initial_state":"Running","final_state":"NaN","changed":false}',),
        ("Running", "Stopped"),
        (False,),
    ],
)
def test_malformed_remote_responses_are_not_exposed(output):
    transport = FakeTransport(PowerShellResult(output=output))
    with pytest.raises(ServiceProtocolError):
        WindowsClient(transport=transport).services.get_state("MyService")


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("Get", {"initial_state": "Running", "final_state": "Stopped", "changed": False}),
        ("Get", {"initial_state": "Running", "final_state": "Running", "changed": True}),
        ("Start", {"initial_state": "Running", "final_state": "Stopped", "changed": True}),
        ("Start", {"initial_state": "Stopped", "final_state": "Running", "changed": False}),
        ("Stop", {"initial_state": "Paused", "final_state": "Stopped", "changed": False}),
        ("Stop", {"initial_state": "Stopped", "final_state": "Running", "changed": False}),
    ],
)
def test_responses_must_match_operation_invariants(operation, payload):
    transport = FakeTransport(PowerShellResult(output=(json.dumps(payload),)))
    services = WindowsClient(transport=transport).services
    with pytest.raises(ServiceProtocolError):
        if operation == "Get":
            services.get_state("MyService")
        elif operation == "Start":
            services.start("MyService")
        else:
            services.stop("MyService")


def test_timeout_override_tightens_client_budget_and_never_extends_it():
    transport = FakeTransport(snapshot(S.RUNNING), snapshot(S.RUNNING))
    services = WindowsClient(transport=transport, timeout=8).services

    services.get_state("MyService", timeout=0.25)
    services.get_state("MyService", timeout=30)
    assert [timeout for _, _, timeout in transport.calls] == [0.25, 8]


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "30"])
def test_invalid_time_budgets_are_rejected_before_io(value):
    with pytest.raises(ConfigurationError):
        WindowsClient(transport=FakeTransport(), timeout=value)
    transport = FakeTransport()
    services = WindowsClient(transport=transport).services
    for method in (services.get_state, services.start, services.stop):
        with pytest.raises(ConfigurationError):
            method("MyService", timeout=value)
    assert transport.calls == []


@pytest.mark.parametrize("error", [TransportError("failed"), TransportTimeoutError("uncertain")])
def test_uncertain_transport_failures_are_not_replayed(error):
    transport = FakeTransport(error)
    with pytest.raises(type(error)) as failure:
        WindowsClient(transport=transport).services.start("MyService")
    assert failure.value is error
    assert len(transport.calls) == 1


def test_transport_is_borrowed_and_not_closed():
    transport = FakeTransport(snapshot(S.STOPPED))
    client = WindowsClient(transport=transport)

    assert client.services.get_state("MyService") is S.STOPPED
    assert len(transport.calls) == 1
    assert not transport.opened
    assert not transport.closed


def test_sanitized_errors_and_representations_do_not_leak_service_names_or_payloads():
    secret = "secret-service-name-314"
    result = PowerShellResult(
        output=(secret,),
        errors=(PowerShellError(PowerShellErrorCategory.COMMAND_FAILED, secret),),
    )
    assert secret not in repr(result)
    transport = FakeTransport(result)
    with pytest.raises(ServiceCommandError) as failure:
        WindowsClient(transport=transport).services.get_state(secret)
    assert secret not in str(failure.value)
    assert secret not in repr(failure.value)

    transport = FakeTransport(snapshot(S.STOPPED, S.START_PENDING, True))
    changed = WindowsClient(transport=transport).services.start(secret)
    assert changed.name == secret
    assert secret not in repr(changed)


def test_script_uses_exact_service_name_and_native_target_only_stop():
    assert "[System.ServiceProcess.ServiceController]::new()" in WINDOWS_SERVICE
    assert "$controller.ServiceName = $Name" in WINDOWS_SERVICE
    assert WINDOWS_SERVICE.index("$controller.ServiceName = $Name") < WINDOWS_SERVICE.index(
        "$controller.Refresh()"
    )
    assert "Stop-Service" not in WINDOWS_SERVICE
    assert "$controller.Stop(" not in WINDOWS_SERVICE
    assert "WaitForStatus" not in WINDOWS_SERVICE
    assert "EnumDependentServices" not in WINDOWS_SERVICE
    assert "Get-Service" not in WINDOWS_SERVICE
    assert "$controller.Dispose()" in WINDOWS_SERVICE

    helper = WINDOWS_SERVICE.split("$nativeStopSource = @'\n", 1)[1].split("\n'@", 1)[0]
    assert "Conductor.Windows.RemoteHelpers.V1.NativeServiceStop" in WINDOWS_SERVICE
    assert "if ($null -eq ($nativeStopTypeName -as [type]))" in WINDOWS_SERVICE
    assert "[StructLayout(LayoutKind.Sequential)]" in helper
    assert helper.count("public uint ") == 7
    assert 'EntryPoint = "OpenSCManagerW"' in helper
    assert "OpenSCManagerW(null, null, ScManagerConnect)" in helper
    assert 'EntryPoint = "OpenServiceW"' in helper
    assert "OpenServiceW(manager, serviceName, ServiceStop)" in helper
    assert 'EntryPoint = "ControlService"' in helper
    assert "ControlService(service, ServiceControlStop, out status)" in helper
    assert helper.count("CloseServiceHandle(") == 3  # declaration plus both finally closes
    assert "finally" in helper
    assert "Marshal.GetLastWin32Error()" in helper
    assert "new Win32Exception(errorCode)" in helper
    assert "EnumDependentServices" not in helper
    assert "WaitFor" not in helper
