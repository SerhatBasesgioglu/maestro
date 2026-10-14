import time
import traceback
from types import SimpleNamespace

import pytest

pytest.importorskip("pypsrp")

import requests
from pypsrp.complex_objects import PSInvocationState as P
from pypsrp.exceptions import AuthenticationError as SdkAuthenticationError
from pypsrp.exceptions import WinRMTransportError, WSManFaultError
from spnego.exceptions import SpnegoError

import conductor.transports._pypsrp as adapter
from conductor.common.errors import (
    AuthenticationError,
    MissingDependencyError,
    PermissionDeniedError,
    TlsError,
    TransportError,
    TransportTimeoutError,
)
from conductor.transports import PowerShellErrorCategory as C
from conductor.transports import PowerShellRemotingConfig

ENDPOINT = "https://iis-test.company.local:5986/wsman"
HTTP_ENDPOINT = "http://iis-test.company.local:5985/wsman"


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(time, "monotonic", clock.monotonic)
    return clock


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Adapter unit tests must never contact a remote target.")

    monkeypatch.setattr(requests.Session, "send", forbidden)


def test_wsman_configuration_is_secure_and_preserves_explicit_target(monkeypatch, tmp_path):
    captured = {}

    def init(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(adapter.WSMan, "__init__", init)
    ca = tmp_path / "ca.pem"
    ca.write_text("placeholder")
    config = PowerShellRemotingConfig(endpoint=ENDPOINT, ca_bundle=ca)
    adapter._DeadlineWSMan(config)
    assert captured["server"] == "iis-test.company.local"
    assert captured["port"] == 5986
    assert captured["path"] == "wsman"
    assert captured["ssl"] is True
    assert captured["auth"] == "kerberos"
    assert captured["encryption"] == "auto"
    assert captured["username"] is captured["password"] is None
    assert captured["cert_validation"] == str(ca)
    assert captured["reconnection_retries"] == 0
    assert captured["negotiate_delegate"] is False
    assert captured["negotiate_send_cbt"] is True
    assert captured["no_proxy"] is True


@pytest.mark.parametrize("auth", ["kerberos", "negotiate", "ntlm"])
def test_http_wsman_configuration_is_explicit_and_keeps_encryption(monkeypatch, auth):
    captured = {}

    def init(self, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(adapter.WSMan, "__init__", init)
    credentials = {"username": "test-user", "password": "test-password"} if auth == "ntlm" else {}
    adapter._DeadlineWSMan(
        PowerShellRemotingConfig(endpoint=HTTP_ENDPOINT, auth=auth, **credentials)
    )
    assert captured["server"] == "iis-test.company.local"
    assert captured["port"] == 5985
    assert captured["path"] == "wsman"
    assert captured["ssl"] is False
    assert captured["auth"] == auth
    assert captured["encryption"] == "auto"
    assert captured["cert_validation"] is True
    assert captured["reconnection_retries"] == 0
    assert captured["no_proxy"] is True
    assert captured["negotiate_delegate"] is False


@pytest.mark.parametrize("auth", ["kerberos", "negotiate", "ntlm"])
def test_real_sdk_http_transport_uses_endpoint_and_requires_message_wrapping(auth):
    credentials = {"username": "test-user", "password": "test-password"} if auth == "ntlm" else {}
    connection = adapter._DeadlineWSMan(
        PowerShellRemotingConfig(endpoint=HTTP_ENDPOINT, auth=auth, **credentials)
    )
    assert connection.transport.endpoint == "http://iis-test.company.local:5985/wsman"
    assert connection.transport.ssl is False
    assert connection.transport.auth == auth
    assert connection.transport.wrap_required is True
    connection.close()


def test_tls_verification_is_on_by_default():
    connection = adapter._DeadlineWSMan(PowerShellRemotingConfig(endpoint=ENDPOINT))
    assert connection.transport.cert_validation is True
    assert connection.transport.ssl is True
    assert connection.transport.wrap_required is False
    connection.close()


def test_receive_retries_and_timeout_overrides_cannot_escape_deadline(monkeypatch, clock):
    calls = []

    def invoke(self, *args, **kwargs):
        calls.append((args, kwargs))
        return "response"

    monkeypatch.setattr(adapter.WSMan, "invoke", invoke)
    connection = adapter._DeadlineWSMan(PowerShellRemotingConfig(endpoint=ENDPOINT))
    with connection.budget(5):
        clock.now = 4.5
        assert (
            connection.invoke("receive", "uri", timeout=100, retries_on_read_timeout=5)
            == "response"
        )
        assert connection.transport.connection_timeout == 0.25
        assert connection.transport.read_timeout == 0.25
        assert connection.operation_timeout == 1
        assert calls[0][1] == {"timeout": None, "retries_on_read_timeout": 0}
        connection.invoke("receive", "uri", None, None, None, 100)
        assert calls[1][0][-1] is None
        clock.now = 5
        with pytest.raises(adapter._DeadlineExceeded):
            connection.invoke("receive", "uri")
    assert len(calls) == 2
    connection.close()


def test_requests_without_a_budget_are_rejected():
    connection = adapter._DeadlineWSMan(PowerShellRemotingConfig(endpoint=ENDPOINT))
    with pytest.raises(TransportError, match="without a time budget"):
        connection.invoke("receive", "uri")
    connection.close()


def test_nested_budgets_restore_outer_deadline(clock):
    connection = adapter._DeadlineWSMan(PowerShellRemotingConfig(endpoint=ENDPOINT))
    with connection.budget(10):
        with connection.budget(2):
            assert connection.remaining() == 2
        assert connection.remaining() == 10
    with pytest.raises(TransportError):
        connection.remaining()
    connection.close()


@pytest.mark.parametrize(
    "external,expected",
    [
        (SdkAuthenticationError("SECRET"), AuthenticationError),
        (SpnegoError(error_code=1, context_msg="SECRET"), AuthenticationError),
        (requests.exceptions.SSLError("SECRET"), TlsError),
        (requests.exceptions.ReadTimeout("SECRET"), TransportTimeoutError),
        (requests.exceptions.ConnectTimeout("SECRET"), TransportTimeoutError),
        (TimeoutError("SECRET"), TransportTimeoutError),
        (adapter._DeadlineExceeded("SECRET"), TransportTimeoutError),
        (WinRMTransportError("http", 401, "SECRET"), AuthenticationError),
        (WinRMTransportError("http", 403, "SECRET"), PermissionDeniedError),
        (WinRMTransportError("http", 500, "SECRET"), TransportError),
        (WSManFaultError(5, None, "SECRET", None, None, None), PermissionDeniedError),
        (WSManFaultError(0x80070005, None, "SECRET", None, None, None), PermissionDeniedError),
        (WSManFaultError(0x80338029, None, "SECRET", None, None, None), TransportTimeoutError),
        (WSManFaultError(1, None, "SECRET", None, None, None), TransportError),
        (ImportError("SECRET"), MissingDependencyError),
        (requests.exceptions.ConnectionError("SECRET"), TransportError),
        (ValueError("SECRET"), TransportError),
    ],
)
def test_exception_categories_do_not_retain_external_text(external, expected):
    result = adapter._translate(external, "command")
    assert isinstance(result, expected)
    assert "SECRET" not in repr(result)
    assert "SECRET" not in str(result)


@pytest.mark.parametrize(
    "category,expected",
    [
        (13, C.OBJECT_NOT_FOUND),
        (17, C.PERMISSION_DENIED),
        (18, C.PERMISSION_DENIED),
        (24, C.AUTHENTICATION),
        (0, C.COMMAND_FAILED),
        (None, C.COMMAND_FAILED),
    ],
)
def test_error_snapshots_drop_messages_and_invocation_details(category, expected):
    record = SimpleNamespace(
        category=category,
        fq_error="SECRET",
        message="SECRET",
        invocation_bound_parameters={"password": "SECRET"},
    )
    result = adapter._error_snapshot(record)
    assert result.category is expected
    assert result.error_id == "SECRET"
    assert "SECRET" not in repr(result)
    assert not hasattr(result, "message")


class FakePool:
    def __init__(self, connection, **kwargs):
        self.connection = connection
        self.kwargs = kwargs
        self.opens = 0
        self.closes = 0
        self.open_error = None
        self.close_error = None

    def open(self):
        self.opens += 1
        if self.open_error:
            raise self.open_error

    def close(self):
        self.closes += 1
        if self.close_error:
            raise self.close_error


class FakePipeline:
    def __init__(
        self, *, clock, states=(P.COMPLETED,), output=("result",), errors=(), had_errors=False
    ):
        self.clock = clock
        self.states = iter(states)
        self.output = list(output)
        self.streams = SimpleNamespace(error=list(errors))
        self.had_errors = had_errors
        self.state = P.NOT_STARTED
        self.script = None
        self.parameters = {}
        self.begins = 0
        self.polls = 0
        self.closes = 0

    def add_script(self, script):
        self.script = script
        return self

    def add_parameter(self, name, value):
        self.parameters[name] = value
        return self

    def begin_invoke(self):
        self.begins += 1
        self.state = P.RUNNING

    def poll_invoke(self):
        self.polls += 1
        self.clock.now += 1
        self.state = next(self.states, P.RUNNING)

    def close(self):
        self.closes += 1


@pytest.fixture
def sdk_session(monkeypatch):
    monkeypatch.setattr(adapter, "RunspacePool", FakePool)
    return adapter._PypsrpSession(PowerShellRemotingConfig(endpoint=ENDPOINT))


def test_runspace_is_profile_free_and_uses_the_configured_endpoint(sdk_session):
    assert sdk_session.pool.kwargs["configuration_name"] == "Microsoft.PowerShell"
    assert sdk_session.pool.kwargs["min_runspaces"] == 1
    assert sdk_session.pool.kwargs["max_runspaces"] == 1
    assert sdk_session.pool.kwargs["no_profile"] is True
    sdk_session.open()
    sdk_session.close()
    sdk_session.close()
    assert sdk_session.pool.opens == sdk_session.pool.closes == 1


def test_pipeline_binds_parameters_and_returns_only_library_models(monkeypatch, clock, sdk_session):
    pipeline = FakePipeline(clock=clock, output=("result", True, 5, 0.5, None))
    monkeypatch.setattr(adapter, "PowerShell", lambda pool: pipeline)
    source = "param($Name) $Name"
    name = "pool'; throw 'injected"
    result = sdk_session.execute(source, {"Name": name}, 10)
    assert pipeline.script == source
    assert pipeline.parameters == {"Name": name}
    assert name not in pipeline.script
    assert result.output == ("result", True, 5, 0.5, None)
    assert result.errors == ()
    assert pipeline.begins == pipeline.polls == pipeline.closes == 1
    sdk_session.close()


def test_unending_remote_pipeline_has_a_checked_deadline(monkeypatch, clock, sdk_session):
    pipeline = FakePipeline(clock=clock, states=())
    monkeypatch.setattr(adapter, "PowerShell", lambda pool: pipeline)
    with pytest.raises(TransportTimeoutError):
        sdk_session.execute("'source'", {}, 2.5)
    assert pipeline.begins == 1
    assert pipeline.polls == 3
    # Public transport closes/invalidate the entire session on failure.
    sdk_session.close()
    assert sdk_session.pool.closes == 1


@pytest.mark.parametrize(
    "state,had_errors", [(P.FAILED, False), (P.STOPPED, False), (P.COMPLETED, True)]
)
def test_failed_pipeline_without_error_records_is_still_a_failure(
    monkeypatch, clock, sdk_session, state, had_errors
):
    pipeline = FakePipeline(clock=clock, states=(state,), output=(), had_errors=had_errors)
    monkeypatch.setattr(adapter, "PowerShell", lambda pool: pipeline)
    result = sdk_session.execute("'source'", {}, 10)
    assert result.errors[0].category is C.COMMAND_FAILED
    sdk_session.close()


def test_error_stream_is_normalized_not_printed(monkeypatch, clock, sdk_session, caplog):
    error = SimpleNamespace(category=18, fq_error="AccessDenied", message="SECRET")
    pipeline = FakePipeline(clock=clock, states=(P.FAILED,), output=(), errors=(error,))
    monkeypatch.setattr(adapter, "PowerShell", lambda pool: pipeline)
    result = sdk_session.execute("'source'", {}, 10)
    assert result.errors[0].category is C.PERMISSION_DENIED
    assert "SECRET" not in repr(result)
    assert "SECRET" not in caplog.text
    sdk_session.close()


@pytest.mark.parametrize("output", [(object(),), (SimpleNamespace(secret="SECRET"),)])
def test_sdk_objects_do_not_escape_public_transport(monkeypatch, clock, sdk_session, output):
    pipeline = FakePipeline(clock=clock, output=output)
    monkeypatch.setattr(adapter, "PowerShell", lambda pool: pipeline)
    with pytest.raises(TransportError, match="scalars"):
        sdk_session.execute("'source'", {}, 10)
    sdk_session.close()


def test_external_exception_tracebacks_are_sanitized(monkeypatch, clock, sdk_session):
    pipeline = FakePipeline(clock=clock)

    def fail():
        raise requests.exceptions.ConnectionError("SECRET")

    pipeline.begin_invoke = fail
    monkeypatch.setattr(adapter, "PowerShell", lambda pool: pipeline)
    with pytest.raises(TransportError) as failure:
        sdk_session.execute("'source'", {}, 10)
    assert failure.value.__suppress_context__
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))
    assert pipeline.begins == 0
    sdk_session.close()


def test_failed_session_cleanup_still_closes_http_resources(monkeypatch, sdk_session):
    closed = []
    monkeypatch.setattr(sdk_session.connection, "close", lambda: closed.append(True))
    sdk_session.pool.close_error = requests.exceptions.ReadTimeout("SECRET")
    with pytest.raises(TransportTimeoutError) as failure:
        sdk_session.close()
    assert closed == [True]
    assert "SECRET" not in str(failure.value)


def test_http_cleanup_failure_is_sanitized_and_does_not_replace_pool_failure(
    monkeypatch, sdk_session
):
    def fail_close():
        raise requests.exceptions.ConnectionError("SECRET")

    monkeypatch.setattr(sdk_session.connection, "close", fail_close)
    sdk_session.pool.close_error = requests.exceptions.ReadTimeout("primary-secret")
    with pytest.raises(TransportTimeoutError) as failure:
        sdk_session.close()
    formatted = "".join(traceback.format_exception(failure.value))
    assert "SECRET" not in formatted
    assert "primary-secret" not in formatted


def test_http_cleanup_failure_alone_is_also_sanitized(monkeypatch, sdk_session):
    def fail_close():
        raise OSError("SECRET")

    monkeypatch.setattr(sdk_session.connection, "close", fail_close)
    with pytest.raises(TransportError) as failure:
        sdk_session.close()
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_open_failure_is_mapped_without_external_traceback(sdk_session):
    sdk_session.pool.open_error = SdkAuthenticationError("SECRET")
    with pytest.raises(AuthenticationError) as failure:
        sdk_session.open()
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))
    sdk_session.close()
