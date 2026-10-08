import json
from collections import deque
from dataclasses import FrozenInstanceError

import pytest

import conductor.iis.application_pools as pools_module
from conductor.common.errors import (
    AuthenticationError,
    ConfigurationError,
    OperationTimeoutError,
    PermissionDeniedError,
    ResourceNotFoundError,
    TransportError,
    TransportTimeoutError,
)
from conductor.iis import (
    ApplicationPoolNotFoundError,
    ApplicationPoolState,
    ApplicationPoolStateError,
    ApplicationPoolTimeoutError,
    IisClient,
    IisCommandError,
    IisPermissionDeniedError,
    IisProtocolError,
)
from conductor.iis._scripts import APPLICATION_POOL
from conductor.transports import PowerShellError, PowerShellErrorCategory, PowerShellResult

S = ApplicationPoolState


def snapshot(state: S, changed: bool = False) -> PowerShellResult:
    return PowerShellResult(output=(json.dumps({"state": state.value, "changed": changed}),))


class FakeTransport:
    def __init__(
        self, *responses: PowerShellResult | Exception, repeat: PowerShellResult | None = None
    ):
        self.responses = deque(responses)
        self.repeat = repeat
        self.calls = []

    def execute(self, script, *, parameters=None, timeout=None):
        self.calls.append((script, dict(parameters or {}), timeout))
        result = self.responses.popleft() if self.responses else self.repeat
        assert result is not None, "Unexpected remote call"
        if isinstance(result, Exception):
            raise result
        return result

    @property
    def operations(self):
        return [parameters["Operation"] for _, parameters, _ in self.calls]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(pools_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(pools_module.time, "sleep", clock.sleep)
    return clock


@pytest.mark.parametrize("state", list(S))
def test_get_state_is_read_only(state):
    transport = FakeTransport(snapshot(state))
    assert IisClient(transport=transport).application_pools.get_state("MyPool") is state
    assert transport.operations == ["Get"]
    assert transport.calls[0][2] == 60


@pytest.mark.parametrize("method,state", [("start", S.STARTED), ("stop", S.STOPPED)])
def test_already_satisfied_is_noop(method, state, clock):
    transport = FakeTransport(snapshot(state))
    result = getattr(IisClient(transport=transport).application_pools, method)("MyPool")
    assert (result.name, result.initial_state, result.final_state, result.changed) == (
        "MyPool",
        state,
        state,
        False,
    )
    assert transport.operations == ["Get"]
    assert clock.sleeps == []
    with pytest.raises(FrozenInstanceError):
        result.changed = True


@pytest.mark.parametrize(
    "method,initial,transition,target,operation",
    [
        ("start", S.STOPPED, S.STARTING, S.STARTED, "Start"),
        ("stop", S.STARTED, S.STOPPING, S.STOPPED, "Stop"),
    ],
)
def test_mutation_waits_for_state_and_shares_budget(
    method, initial, transition, target, operation, clock
):
    transport = FakeTransport(snapshot(initial), snapshot(transition, True), snapshot(target))
    client = IisClient(transport=transport, timeout=8, poll_interval=0.5)
    result = getattr(client.application_pools, method)("MyPool")
    assert result.initial_state is initial
    assert result.final_state is target
    assert result.changed
    assert transport.operations == ["Get", operation, "Get"]
    assert [timeout for _, _, timeout in transport.calls] == [8, 8, 7.5]
    assert clock.sleeps == [0.5]


@pytest.mark.parametrize(
    "method,initial,target", [("start", S.STARTING, S.STARTED), ("stop", S.STOPPING, S.STOPPED)]
)
def test_transition_to_desired_state_needs_no_mutation(method, initial, target, clock):
    transport = FakeTransport(snapshot(initial), snapshot(target))
    result = getattr(IisClient(transport=transport).application_pools, method)("MyPool")
    assert result.initial_state is initial
    assert result.final_state is target
    assert not result.changed
    assert transport.operations == ["Get", "Get"]


@pytest.mark.parametrize(
    "method,initial,stable,target,operation",
    [
        ("start", S.STOPPING, S.STOPPED, S.STARTED, "Start"),
        ("stop", S.STARTING, S.STARTED, S.STOPPED, "Stop"),
    ],
)
def test_opposite_transition_finishes_before_mutation(
    method, initial, stable, target, operation, clock
):
    transport = FakeTransport(snapshot(initial), snapshot(stable), snapshot(target, True))
    result = getattr(IisClient(transport=transport).application_pools, method)("MyPool")
    assert result.changed
    assert transport.operations == ["Get", "Get", operation]


def test_remote_recheck_can_make_mutation_a_noop(clock):
    transport = FakeTransport(snapshot(S.STARTED), snapshot(S.STOPPED))
    result = IisClient(transport=transport).application_pools.stop("MyPool")
    assert not result.changed
    assert result.final_state is S.STOPPED
    assert transport.operations == ["Get", "Stop"]


@pytest.mark.parametrize("method", ["start", "stop"])
def test_unknown_state_is_not_mutated(method, clock):
    transport = FakeTransport(snapshot(S.UNKNOWN))
    with pytest.raises(ApplicationPoolStateError):
        getattr(IisClient(transport=transport).application_pools, method)("MyPool")
    assert transport.operations == ["Get"]


def test_timeout_reports_last_state_and_never_repeats_mutation(clock):
    transport = FakeTransport(
        snapshot(S.STARTED), snapshot(S.STOPPING, True), repeat=snapshot(S.STOPPING)
    )
    with pytest.raises(ApplicationPoolTimeoutError) as failure:
        IisClient(transport=transport, timeout=2.5).application_pools.stop("MyPool")
    error = failure.value
    assert isinstance(error, OperationTimeoutError)
    assert (error.name, error.desired_state, error.last_state, error.timeout) == (
        "MyPool",
        S.STOPPED,
        S.STOPPING,
        2.5,
    )
    assert transport.operations == ["Get", "Stop", "Get", "Get"]
    assert clock.sleeps == [1, 1, 0.5]
    assert clock.now == 2.5


def test_initial_transition_timeout_does_not_dispatch_a_write(clock):
    transport = FakeTransport(repeat=snapshot(S.STARTING))
    with pytest.raises(ApplicationPoolTimeoutError):
        IisClient(transport=transport, timeout=2).application_pools.stop("MyPool")
    assert transport.operations == ["Get", "Get"]


def test_concurrent_interference_is_not_fought_with_repeated_writes(clock):
    transport = FakeTransport(snapshot(S.STOPPED), snapshot(S.STOPPING), repeat=snapshot(S.STOPPED))
    with pytest.raises(ApplicationPoolTimeoutError):
        IisClient(transport=transport, timeout=2).application_pools.start("MyPool")
    assert transport.operations.count("Start") == 1


def test_override_timeout_uses_one_total_budget(clock):
    transport = FakeTransport(repeat=snapshot(S.STARTING))
    with pytest.raises(ApplicationPoolTimeoutError) as failure:
        IisClient(transport=transport, timeout=60).application_pools.start("MyPool", timeout=0.25)
    assert failure.value.timeout == 0.25
    assert transport.calls[0][2] == 0.25
    assert clock.now == 0.25


def test_success_after_deadline_is_not_reported_as_timely(clock):
    class SlowTransport(FakeTransport):
        def execute(self, *args, **kwargs):
            result = super().execute(*args, **kwargs)
            clock.now += 3
            return result

    transport = SlowTransport(snapshot(S.STOPPED))
    with pytest.raises(ApplicationPoolTimeoutError):
        IisClient(transport=transport, timeout=2).application_pools.stop("MyPool")


@pytest.mark.parametrize("error", [TransportError("failed"), TransportTimeoutError("uncertain")])
def test_failed_mutation_is_not_replayed(error, clock):
    transport = FakeTransport(snapshot(S.STARTED), error)
    with pytest.raises(type(error)) as failure:
        IisClient(transport=transport).application_pools.stop("MyPool")
    assert failure.value is error
    assert transport.operations == ["Get", "Stop"]


@pytest.mark.parametrize(
    "name", ["LiteralUserPool", "Pool with spaces", "プール", "pool'; Write-Output 'injected"]
)
def test_names_are_data_never_source(name):
    transport = FakeTransport(snapshot(S.STARTED))
    IisClient(transport=transport).application_pools.get_state(name)
    script, parameters, _ = transport.calls[0]
    assert script == APPLICATION_POOL
    assert parameters == {"Name": name, "Operation": "Get"}
    assert name not in script


@pytest.mark.parametrize(
    "name",
    [
        "",
        " ",
        "../Pool",
        "x/y",
        "x\\y",
        "*",
        "x?",
        "x[y]",
        "x:Pool",
        "x\x00",
        "x\n",
        "x\x7f",
        None,
        1,
    ],
)
@pytest.mark.parametrize("method", ["get_state", "start", "stop"])
def test_unsafe_names_fail_before_io(name, method):
    transport = FakeTransport()
    with pytest.raises(ValueError):
        getattr(IisClient(transport=transport).application_pools, method)(name)
    assert transport.calls == []


@pytest.mark.parametrize(
    "category,identifier,error_class",
    [
        (
            PowerShellErrorCategory.OBJECT_NOT_FOUND,
            "Conductor.ApplicationPoolNotFound,Write-Error",
            ApplicationPoolNotFoundError,
        ),
        (PowerShellErrorCategory.OBJECT_NOT_FOUND, "Modules_ModuleNotFound", IisCommandError),
        (
            PowerShellErrorCategory.PERMISSION_DENIED,
            "UnauthorizedAccessException",
            IisPermissionDeniedError,
        ),
        (PowerShellErrorCategory.AUTHENTICATION, "AuthenticationFailure", AuthenticationError),
        (PowerShellErrorCategory.COMMAND_FAILED, "other", IisCommandError),
    ],
)
def test_remote_failures_are_distinguishable(category, identifier, error_class):
    transport = FakeTransport(PowerShellResult(errors=(PowerShellError(category, identifier),)))
    with pytest.raises(error_class):
        IisClient(transport=transport).application_pools.get_state("MyPool")
    assert issubclass(ApplicationPoolNotFoundError, ResourceNotFoundError)
    assert issubclass(IisPermissionDeniedError, PermissionDeniedError)


def test_access_denied_is_not_masked_by_missing_resource():
    errors = (
        PowerShellError(
            PowerShellErrorCategory.OBJECT_NOT_FOUND, "Conductor.ApplicationPoolNotFound"
        ),
        PowerShellError(PowerShellErrorCategory.PERMISSION_DENIED),
    )
    transport = FakeTransport(PowerShellResult(errors=errors))
    with pytest.raises(IisPermissionDeniedError):
        IisClient(transport=transport).application_pools.get_state("MyPool")


@pytest.mark.parametrize(
    "output",
    [
        (),
        ("bad-json",),
        ("[]",),
        ("null",),
        ("{}",),
        ('{"state":"Paused","changed":false}',),
        ('{"state":true,"changed":false}',),
        ('{"state":"Started","changed":0}',),
        ('{"state":"Started","changed":false,"extra":1}',),
        ("Started", "Stopped"),
        (False,),
    ],
)
def test_invalid_remote_responses_are_not_exposed(output):
    transport = FakeTransport(PowerShellResult(output=output))
    with pytest.raises(IisProtocolError):
        IisClient(transport=transport).application_pools.get_state("MyPool")


def test_read_must_not_report_mutation():
    transport = FakeTransport(snapshot(S.STARTED, True))
    with pytest.raises(IisProtocolError):
        IisClient(transport=transport).application_pools.get_state("MyPool")


@pytest.mark.parametrize("during_poll", [False, True])
def test_state_reads_in_setters_must_not_report_mutation(during_poll, clock):
    responses = [snapshot(S.STARTED, True)]
    if during_poll:
        responses = [snapshot(S.STARTED), snapshot(S.STOPPING, True), snapshot(S.STOPPED, True)]
    transport = FakeTransport(*responses)
    with pytest.raises(IisProtocolError):
        IisClient(transport=transport).application_pools.stop("MyPool")
    assert transport.operations.count("Stop") == int(during_poll)


def test_unknown_state_after_dispatch_never_causes_further_writes(clock):
    transport = FakeTransport(snapshot(S.STOPPED), snapshot(S.UNKNOWN, True))
    with pytest.raises(ApplicationPoolStateError, match="no further mutation"):
        IisClient(transport=transport).application_pools.start("MyPool")
    assert transport.operations == ["Get", "Start"]


def test_sensitive_names_and_remote_data_are_not_in_errors_or_reprs(clock):
    secret = "sensitive-value-123"
    result = PowerShellResult(
        output=(secret,), errors=(PowerShellError(PowerShellErrorCategory.COMMAND_FAILED, secret),)
    )
    assert secret not in repr(result)
    transport = FakeTransport(result)
    with pytest.raises(IisCommandError) as failure:
        IisClient(transport=transport).application_pools.get_state(secret)
    assert secret not in str(failure.value)
    assert secret not in repr(failure.value)
    transport = FakeTransport(repeat=snapshot(S.STARTING))
    with pytest.raises(ApplicationPoolTimeoutError) as failure:
        IisClient(transport=transport, timeout=0.1).application_pools.start(secret)
    assert failure.value.name == secret
    assert secret not in repr(failure.value)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "30"])
def test_invalid_time_budgets_are_rejected(value):
    with pytest.raises(ConfigurationError):
        IisClient(transport=FakeTransport(), timeout=value)
    with pytest.raises(ConfigurationError):
        IisClient(transport=FakeTransport(), poll_interval=value)
    pools = IisClient(transport=FakeTransport()).application_pools
    for method in (pools.get_state, pools.start, pools.stop):
        with pytest.raises(ConfigurationError):
            method("MyPool", timeout=value)


def test_iis_client_does_not_open_or_close_borrowed_transport():
    transport = FakeTransport(snapshot(S.STOPPED))
    client = IisClient(transport=transport)
    assert client.application_pools.get_state("MyPool") is S.STOPPED
