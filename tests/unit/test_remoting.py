import builtins
import traceback
from dataclasses import FrozenInstanceError

import pytest

import maestro.transports.powershell_remoting as remoting
from maestro.common.errors import (
    ConfigurationError,
    MissingDependencyError,
    TransportError,
    TransportTimeoutError,
)
from maestro.transports import PowerShellRemoting, PowerShellRemotingConfig, PowerShellResult

ENDPOINT = "https://iis-test.company.local:5986/wsman"
HTTP_ENDPOINT = "http://iis-test.company.local:5985/wsman"


class FakeSession:
    def __init__(self, *, open_error=None, execute_error=None, close_error=None):
        self.open_error = open_error
        self.execute_error = execute_error
        self.close_error = close_error
        self.opens = 0
        self.closes = 0
        self.calls = []

    def open(self):
        self.opens += 1
        if self.open_error:
            raise self.open_error

    def execute(self, script, parameters, timeout):
        self.calls.append((script, parameters, timeout))
        if self.execute_error:
            raise self.execute_error
        return PowerShellResult(output=("ok",))

    def close(self):
        self.closes += 1
        if self.close_error:
            raise self.close_error


@pytest.fixture
def session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(remoting, "_make_session", lambda config: session)
    return session


def test_construction_is_lazy_and_owns_no_connections(session):
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    assert not transport.is_open
    assert session.opens == session.closes == 0
    with pytest.raises(TransportError, match="not open"):
        transport.execute("'hello'")
    assert session.calls == []


def test_context_manager_open_and_close_are_idempotent(session):
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    with transport as opened:
        assert opened is transport
        assert opened.is_open
        opened.open()
        assert opened.execute("'hello'").output == ("ok",)
    assert not transport.is_open
    transport.close()
    assert session.opens == session.closes == 1


def test_failed_open_cleans_resources_and_remains_closed(session):
    error = TransportTimeoutError("safe failure")
    session.open_error = error
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    with pytest.raises(TransportTimeoutError) as failure:
        transport.open()
    assert failure.value is error
    assert not transport.is_open
    assert session.opens == session.closes == 1


@pytest.mark.parametrize("endpoint", [ENDPOINT, HTTP_ENDPOINT])
def test_execution_failure_invalidates_session_without_retry(session, endpoint):
    session.execute_error = TransportError("safe failure")
    transport = PowerShellRemoting(endpoint=endpoint)
    transport.open()
    with pytest.raises(TransportError, match="safe failure"):
        transport.execute("'hello'")
    assert not transport.is_open
    assert len(session.calls) == 1
    assert session.closes == 1
    with pytest.raises(TransportError, match="not open"):
        transport.execute("'hello'")
    assert len(session.calls) == 1


def test_keyboard_interrupt_also_cleans_owned_resources(session):
    session.execute_error = KeyboardInterrupt()
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    transport.open()
    with pytest.raises(KeyboardInterrupt):
        transport.execute("'hello'")
    assert not transport.is_open
    assert session.closes == 1


def test_reopening_is_explicit_and_does_not_replay(monkeypatch):
    failed = FakeSession(execute_error=TransportError("failure"))
    healthy = FakeSession()
    sessions = iter((failed, healthy))
    monkeypatch.setattr(remoting, "_make_session", lambda config: next(sessions))
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    transport.open()
    with pytest.raises(TransportError):
        transport.execute("write")
    transport.open()
    assert transport.is_open
    assert healthy.calls == []
    assert transport.execute("read").output == ("ok",)
    transport.close()


def test_normal_cleanup_error_is_surfaced(session):
    session.close_error = TransportError("cleanup failure")
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    with pytest.raises(TransportError, match="cleanup failure"), transport:
        pass
    assert not transport.is_open


def test_cleanup_does_not_mask_primary_failure_or_log_its_details(session, caplog):
    secret = "do-not-log-this"
    session.close_error = TransportError(secret)
    primary = ValueError("primary")
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    with pytest.raises(ValueError) as failure, transport:
        raise primary
    assert failure.value is primary
    assert "original error is preserved" in caplog.text
    assert secret not in caplog.text


def test_failed_open_cleanup_does_not_mask_primary_error(session, caplog):
    session.open_error = TransportTimeoutError("primary")
    session.close_error = TransportError("sensitive-cleanup")
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    with pytest.raises(TransportTimeoutError, match="primary"):
        transport.open()
    assert "sensitive-cleanup" not in caplog.text


@pytest.mark.parametrize("timeout,expected", [(None, 30), (2.5, 2.5), (100, 30)])
def test_bound_parameters_are_copied_and_command_ceiling_is_respected(session, timeout, expected):
    parameters = {
        "Name": "pool'; throw 'injected",
        "Enabled": True,
        "Count": 2,
        "Ratio": 0.2,
        "Switch": None,
    }
    transport = PowerShellRemoting(endpoint=ENDPOINT)
    with transport:
        transport.execute("param($Name) $Name", parameters=parameters, timeout=timeout)
    assert session.calls == [("param($Name) $Name", parameters, expected)]
    assert session.calls[0][1] is not parameters


@pytest.mark.parametrize("script", ["", " ", None, 1])
def test_invalid_scripts_fail_before_dispatch(session, script):
    with PowerShellRemoting(endpoint=ENDPOINT) as transport:
        with pytest.raises(ConfigurationError):
            transport.execute(script)
        assert transport.is_open
    assert session.calls == []


@pytest.mark.parametrize(
    "parameters",
    [{"Name;throw": "pool"}, {"": "pool"}, {1: "pool"}, {"Name": object()}, {"Name": []}],
)
def test_invalid_parameters_fail_before_dispatch(session, parameters):
    with PowerShellRemoting(endpoint=ENDPOINT) as transport, pytest.raises(ConfigurationError):
        transport.execute("param($Name) $Name", parameters=parameters)
    assert session.calls == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "iis.company.local",
        "http://iis.company.local",
        "http:///wsman",
        "http://iis.company.local/",
        "http://iis.company.local:0/wsman",
        "http://iis.company.local:65536/wsman",
        "http://iis.company.local:bad/wsman",
        "http://user:super-secret@iis.company.local/wsman",
        "http://iis.company.local/wsman?secret=super-secret",
        "http://iis.company.local/wsman#super-secret",
        "ftp://iis.company.local/wsman",
        "https:///wsman",
        "https://iis.company.local",
        "https://iis.company.local/",
        "https://iis.company.local:0/wsman",
        "https://iis.company.local:65536/wsman",
        "https://iis.company.local:bad/wsman",
        "https://[invalid/wsman",
        "https://user:super-secret@iis.company.local/wsman",
        "https://iis.company.local/wsman?secret=super-secret",
        "https://iis.company.local/wsman#super-secret",
        "https://iis.company.local/wsman?",
        " https://iis.company.local/wsman",
        "https://iis.\ncompany.local/wsman",
    ],
)
def test_invalid_endpoints_do_not_echo_caller_data(endpoint):
    with pytest.raises(ConfigurationError) as failure:
        PowerShellRemoting(endpoint=endpoint)
    assert "super-secret" not in str(failure.value)


def test_endpoint_parse_preserves_url_semantics():
    assert remoting._parse_endpoint(ENDPOINT) == (
        "iis-test.company.local",
        5986,
        "wsman",
        True,
    )
    assert remoting._parse_endpoint("https://iis.company.local/wsman") == (
        "iis.company.local",
        443,
        "wsman",
        True,
    )
    assert remoting._parse_endpoint("https://[::1]:5986/wsman") == (
        "::1",
        5986,
        "wsman",
        True,
    )
    assert remoting._parse_endpoint("https://iis.company.local//wsman") == (
        "iis.company.local",
        443,
        "/wsman",
        True,
    )


@pytest.mark.parametrize(
    "endpoint,expected",
    [
        (HTTP_ENDPOINT, ("iis-test.company.local", 5985, "wsman", False)),
        (
            "http://iis.company.local/wsman",
            ("iis.company.local", 5985, "wsman", False),
        ),
        (
            "http://iis.company.local:8080/custom/path",
            ("iis.company.local", 8080, "custom/path", False),
        ),
        ("http://[::1]/wsman", ("::1", 5985, "wsman", False)),
        ("http://iis.company.local//wsman", ("iis.company.local", 5985, "/wsman", False)),
    ],
)
def test_http_endpoint_parse_preserves_port_path_and_ipv6(endpoint, expected):
    assert remoting._parse_endpoint(endpoint) == expected


def test_http_config_construction_from_config_and_lazy_open():
    config = PowerShellRemotingConfig(endpoint=HTTP_ENDPOINT)
    transport = PowerShellRemoting.from_config(config)
    assert transport.config == config
    assert transport.config.endpoint == HTTP_ENDPOINT
    assert not transport.is_open
    assert not PowerShellRemoting(endpoint="http://iis.company.local/wsman").is_open


@pytest.mark.parametrize("auth", ["basic", "credssp", "certificate", "super-secret", None])
def test_unsupported_auth_is_rejected(auth):
    with pytest.raises(ConfigurationError) as failure:
        PowerShellRemoting(endpoint=ENDPOINT, auth=auth)
    assert "super-secret" not in str(failure.value)


@pytest.mark.parametrize(
    "credentials",
    [
        {"username": "user"},
        {"password": "secret"},
        {"username": "", "password": "secret"},
        {"username": 1, "password": "secret"},
        {"auth": "ntlm"},
    ],
)
def test_invalid_credentials_are_rejected_without_echoing(credentials):
    with pytest.raises(ConfigurationError) as failure:
        PowerShellRemoting(endpoint=ENDPOINT, **credentials)
    assert "secret" not in str(failure.value)


def test_credentials_are_not_in_representations_and_config_is_immutable():
    config = PowerShellRemotingConfig(
        endpoint=ENDPOINT, username="private-user", password="private-password"
    )
    transport = PowerShellRemoting.from_config(config)
    assert transport.config == config
    for value in (config, transport):
        assert "private-user" not in repr(value)
        assert "private-password" not in repr(value)
    with pytest.raises(FrozenInstanceError):
        config.password = "different"


def test_explicit_ca_bundle_must_be_a_file(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("test certificate placeholder")
    assert PowerShellRemoting(endpoint=ENDPOINT, ca_bundle=ca).config.ca_bundle == ca
    for invalid in (False, True, "", tmp_path, tmp_path / "missing.pem"):
        with pytest.raises(ConfigurationError):
            PowerShellRemoting(endpoint=ENDPOINT, ca_bundle=invalid)


def test_ca_bundle_is_rejected_for_http_with_clear_configuration_error(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("test certificate placeholder")
    with pytest.raises(ConfigurationError, match="only supported for HTTPS"):
        PowerShellRemoting(endpoint=HTTP_ENDPOINT, ca_bundle=ca)


@pytest.mark.parametrize(
    "configuration_name", ["", "My/Endpoint", "endpoint?secret", "endpoint\n", None]
)
def test_configuration_name_is_a_simple_endpoint_identifier(configuration_name):
    with pytest.raises(ConfigurationError):
        PowerShellRemoting(endpoint=ENDPOINT, configuration_name=configuration_name)


@pytest.mark.parametrize(
    "name", ["connection_timeout", "request_timeout", "command_timeout", "cleanup_timeout"]
)
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), True, "30", 10**1000])
def test_all_timeouts_are_finite_and_positive(name, value):
    with pytest.raises(ConfigurationError):
        PowerShellRemoting(endpoint=ENDPOINT, **{name: value})


def test_missing_optional_sdk_error_is_actionable_and_sanitized(monkeypatch):
    real_import = builtins.__import__

    def no_sdk(name, *args, **kwargs):
        if name == "maestro.transports._pypsrp":
            raise ImportError("sensitive-native-library-details")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_sdk)
    with pytest.raises(MissingDependencyError) as failure:
        remoting._make_session(PowerShellRemotingConfig(endpoint=ENDPOINT))
    assert "maestro[iis]" in str(failure.value)
    assert "sensitive-native-library-details" not in "".join(
        traceback.format_exception(failure.value)
    )
