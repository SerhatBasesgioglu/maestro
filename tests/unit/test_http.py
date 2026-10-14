import builtins
import json
import ssl
import traceback

import httpx
import pytest

import maestro.common._httpx as adapter
import maestro.common.http as http_module
from maestro.argocd import ArgoCdClient, ArgoCdConfig
from maestro.azure_devops import AzureDevOpsClient, AzureDevOpsConfig
from maestro.common.errors import (
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    HttpResponseError,
    MissingDependencyError,
    PermissionDeniedError,
    ProtocolError,
    RateLimitError,
    ResourceNotFoundError,
    TlsError,
    TransportError,
    TransportTimeoutError,
)
from maestro.common.http import HttpResponse, HttpxTransport
from maestro.sonarqube import SonarQubeClient, SonarQubeConfig


@pytest.fixture
def mock_httpx(monkeypatch):
    """Exercise real HTTPX request construction without sockets or another mocking dependency."""
    real_client = httpx.Client
    calls, configurations, responses = [], [], []

    def handler(request):
        calls.append(request)
        assert responses, "Unexpected HTTPX request"
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def factory(**kwargs):
        configurations.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)
    return calls, configurations, responses


def test_real_httpx_adapter_encodes_identifiers_and_sets_tls_policy_without_environment(mock_httpx):
    calls, configurations, responses = mock_httpx
    responses.append(httpx.Response(200, json={"id": "repo-id", "name": "A & B"}))
    with AzureDevOpsClient(
        AzureDevOpsConfig("https://dev.azure.com/org", "My Project", "private-token")
    ) as ado:
        ado.repositories.get("A & B", timeout=2.5)
    request = calls[0]
    assert (
        request.url.raw_path
        == b"/org/My%20Project/_apis/git/repositories/A%20%26%20B?api-version=7.1"
    )
    assert request.extensions["timeout"] == {"connect": 2.5, "read": 2.5, "write": 2.5, "pool": 2.5}
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False
    context = configurations[0]["verify"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_redirects_cannot_forward_credentials_to_another_target(mock_httpx):
    calls, _, responses = mock_httpx
    responses.append(httpx.Response(302, headers={"Location": "https://attacker.invalid/SECRET"}))
    with (
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")) as sonar,
        pytest.raises(HttpResponseError) as failure,
    ):
        sonar.projects.get("project")
    assert failure.value.status_code == 302
    assert len(calls) == 1 and "attacker" not in str(calls[0].url)
    assert "SECRET" not in str(failure.value)


@pytest.mark.parametrize(
    "status,error",
    [
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, ResourceNotFoundError),
        (409, ConflictError),
        (412, ConflictError),
        (429, RateLimitError),
        (400, HttpResponseError),
        (500, HttpResponseError),
        (503, HttpResponseError),
    ],
)
def test_http_status_errors_precede_json_parsing_and_do_not_replay(mock_httpx, status, error):
    calls, _, responses = mock_httpx
    responses.append(httpx.Response(status, content=b"SECRET is not JSON"))
    with (
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")) as sonar,
        pytest.raises(error) as failure,
    ):
        sonar.projects.get("project")
    assert "SECRET" not in repr(failure.value)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"SECRET", b'{"value":NaN}', b'{"value":Infinity}'])
def test_invalid_success_json_is_not_exposed(mock_httpx, body):
    _, _, responses = mock_httpx
    responses.append(httpx.Response(200, content=body))
    with (
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")) as sonar,
        pytest.raises(ProtocolError) as failure,
    ):
        sonar.projects.get("project")
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize(
    "external,error",
    [
        (httpx.ReadTimeout("SECRET"), TransportTimeoutError),
        (httpx.ConnectTimeout("SECRET"), TransportTimeoutError),
        (httpx.ConnectError("SECRET"), TransportError),
        (httpx.RemoteProtocolError("SECRET"), TransportError),
    ],
)
def test_transport_failures_are_sanitized_and_writes_are_not_replayed(mock_httpx, external, error):
    calls, _, responses = mock_httpx
    responses.append(external)
    with (
        ArgoCdClient(ArgoCdConfig("https://argo.company.local", "project", "TOKEN")) as argo,
        pytest.raises(error) as failure,
    ):
        argo.applications.sync("my-app")
    assert len(calls) == 1
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_tls_failure_is_distinguished_without_matching_secret_exception_text(mock_httpx):
    _, _, responses = mock_httpx
    external = httpx.ConnectError("SECRET")
    external.__cause__ = ssl.SSLCertVerificationError("SECRET")
    responses.append(external)
    with (
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")) as sonar,
        pytest.raises(TlsError) as failure,
    ):
        sonar.projects.get("project")
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_borrowed_transport_is_never_closed_by_integration_client(http_transport):
    client = SonarQubeClient(
        SonarQubeConfig("https://sonar.company.local", "TOKEN"), transport=http_transport
    )
    client.close()
    client.close()
    assert http_transport.closes == 0
    with pytest.raises(TransportError, match="closed"):
        client.projects.get("project")
    assert http_transport.calls == []


def test_owned_transport_closes_once_without_masking_primary_error(monkeypatch, caplog):
    class FakeOwned:
        closes = 0

        def close(self):
            self.closes += 1
            raise TransportError("SECRET")

    owned = FakeOwned()
    monkeypatch.setattr(http_module, "HttpxTransport", lambda **kwargs: owned)
    client = SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN"))
    with pytest.raises(ValueError, match="primary"), client:
        raise ValueError("primary")
    client.close()
    assert owned.closes == 1
    assert "original error is preserved" in caplog.text and "SECRET" not in caplog.text


def test_normal_cleanup_failure_is_visible(monkeypatch):
    backend = adapter._HttpxBackend(ca_bundle=None)

    def fail_close():
        raise OSError("SECRET")

    monkeypatch.setattr(backend._client, "close", fail_close)
    with pytest.raises(TransportError) as failure:
        backend.close()
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_new_clients_are_lazy_and_credentials_not_in_reprs(mock_httpx):
    _, configurations, _ = mock_httpx
    clients = [
        AzureDevOpsClient(AzureDevOpsConfig("https://dev.azure.com/org", "project", "SECRET")),
        ArgoCdClient(ArgoCdConfig("https://argo.company.local", "project", "SECRET")),
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "SECRET")),
    ]
    for client in clients:
        assert "SECRET" not in repr(client) and "SECRET" not in repr(client.config)
        client.close()
    assert configurations == []
    assert "SECRET" not in repr(HttpResponse(500, b"SECRET", {"secret": "SECRET"}))


def test_missing_http_extra_is_actionable_and_does_not_attempt_network(monkeypatch):
    real_import = builtins.__import__

    def no_httpx(name, *args, **kwargs):
        if name == "maestro.common._httpx":
            raise ImportError("SECRET")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_httpx)
    with (
        SonarQubeClient(SonarQubeConfig("https://example.invalid", "TOKEN")) as sonar,
        pytest.raises(MissingDependencyError) as failure,
    ):
        sonar.projects.get("project")
    assert "maestro[sonarqube]" in str(failure.value)
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize(
    "url",
    [
        "http://server",
        "",
        "https://user:SECRET@server",
        "https://server?SECRET",
        "https://server/#SECRET",
        "https://server:0",
        "https://server/../target",
        "https://server/%2e%2e/target",
        "https://server\\target",
        "https://server\n",
    ],
)
def test_all_new_configs_require_secure_explicit_urls(url):
    for constructor, args in (
        (AzureDevOpsConfig, (url, "project", "TOKEN")),
        (ArgoCdConfig, (url, "project", "TOKEN")),
        (SonarQubeConfig, (url, "TOKEN")),
    ):
        with pytest.raises(ConfigurationError) as failure:
            constructor(*args)
        assert "SECRET" not in str(failure.value)


@pytest.mark.parametrize("token", ["", "SECRET\n", "SECRET value", "秘密", None])
def test_tokens_reject_header_injection_without_echoing(token):
    with pytest.raises(ConfigurationError) as failure:
        SonarQubeConfig("https://sonar.company.local", token)
    assert "SECRET" not in str(failure.value)


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "30"])
def test_new_client_timeouts_are_finite_positive(value):
    with pytest.raises(ConfigurationError):
        SonarQubeConfig("https://sonar.company.local", "TOKEN", timeout=value)


def test_ca_configuration_cannot_disable_certificate_verification(tmp_path):
    ca = tmp_path / "ca.pem"
    ca.write_text("invalid certificate contents")
    for invalid in (False, True, "", tmp_path, tmp_path / "missing.pem"):
        with pytest.raises(ConfigurationError):
            SonarQubeConfig("https://sonar.company.local", "TOKEN", ca_bundle=invalid)
    config = SonarQubeConfig("https://sonar.company.local", "TOKEN", ca_bundle=ca)
    with SonarQubeClient(config) as sonar, pytest.raises(ConfigurationError) as failure:
        sonar.projects.get("project")
    assert "invalid certificate contents" not in str(failure.value)


def test_nan_request_data_fails_before_a_mutating_request(http_transport):
    ado = AzureDevOpsClient(
        AzureDevOpsConfig("https://dev.azure.com/org", "project", "TOKEN"), transport=http_transport
    )
    with pytest.raises(ConfigurationError):
        ado.pipelines.run(7, branch="main", template_parameters={"value": float("nan")})
    assert http_transport.calls == []


def test_http_transport_close_is_idempotent_and_prevents_later_requests():
    transport = HttpxTransport()
    transport.close()
    transport.close()
    with pytest.raises(TransportError):
        transport.request("GET", "https://example.invalid", headers={}, timeout=1)


def test_httpx_serializes_sync_body_and_reuses_owned_client(mock_httpx):
    calls, configurations, responses = mock_httpx
    application = {
        "metadata": {"name": "my-app"},
        "spec": {"project": "delivery"},
        "status": {"health": {"status": "Progressing"}},
    }
    responses.extend([httpx.Response(200, json=application)] * 2)
    with ArgoCdClient(ArgoCdConfig("https://argo.company.local", "delivery", "TOKEN")) as argo:
        argo.applications.sync("my-app", revision="abc123")
        argo.applications.get("my-app")
    assert len(configurations) == 1
    assert calls[0].headers["Content-Type"] == "application/json"
    assert json.loads(calls[0].content) == {
        "name": "my-app",
        "project": "delivery",
        "prune": False,
        "dryRun": False,
        "revision": "abc123",
    }
    assert calls[1].url.query == b"project=delivery"


def test_httpx_encodes_quality_gate_query_without_a_second_selector(mock_httpx):
    calls, _, responses = mock_httpx
    responses.append(httpx.Response(200, json={"projectStatus": {"status": "OK"}}))
    with SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")) as sonar:
        sonar.quality_gates.get_status("org:project", branch="feature/a&pullRequest=22")
    assert calls[0].url.params == httpx.QueryParams(
        {"projectKey": "org:project", "branch": "feature/a&pullRequest=22"}
    )
    assert "pullRequest" not in calls[0].url.params


@pytest.mark.parametrize("external", [httpx.InvalidURL("SECRET"), ValueError("SECRET")])
def test_request_encoding_errors_are_sanitized(mock_httpx, external):
    _, _, responses = mock_httpx
    responses.append(external)
    with (
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")) as sonar,
        pytest.raises(ConfigurationError) as failure,
    ):
        sonar.projects.get("project")
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_per_call_timeout_cannot_extend_configured_http_ceiling(http_transport):
    client = SonarQubeClient(
        SonarQubeConfig("https://sonar.company.local", "TOKEN", timeout=2),
        transport=http_transport,
    )
    http_transport.respond({"component": {"key": "project", "name": "Project", "qualifier": "TRK"}})
    client.projects.get("project", timeout=300)
    assert http_transport.calls[0]["timeout"] == 2


def test_integration_context_surfaces_normal_owned_cleanup_failure(monkeypatch):
    class FailingTransport:
        def close(self):
            raise TransportError("sanitized cleanup failure")

    monkeypatch.setattr(http_module, "HttpxTransport", lambda **kwargs: FailingTransport())
    with (
        pytest.raises(TransportError, match="cleanup"),
        SonarQubeClient(SonarQubeConfig("https://sonar.company.local", "TOKEN")),
    ):
        pass
