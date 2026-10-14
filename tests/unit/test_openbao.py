import builtins
import json
import ssl
import traceback

import httpx
import pytest

import maestro.common.http as http_module
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
    TransportError,
)
from maestro.common.http import HttpResponse
from maestro.openbao import KvV2Metadata, KvV2Secret, OpenBaoClient, OpenBaoConfig

_SECRET = "secret-value-marker"
_SECRET_KEY = "secret-key-marker"
_TOKEN = "token-value-marker"
_CUSTOM = "custom-metadata-marker"
_UNSET = object()


def response_data(
    secret=_UNSET,
    *,
    metadata=None,
    custom_metadata=None,
    include_custom_metadata=False,
):
    data = {"data": {"key": _SECRET} if secret is _UNSET else secret}
    details = {
        "version": 7,
        "created_time": "2024-01-02T03:04:05Z",
        "deletion_time": "",
        "destroyed": False,
        "ignored_metadata_field": _SECRET,
    }
    if include_custom_metadata:
        details["custom_metadata"] = custom_metadata
    if metadata is not None:
        details.update(metadata)
    return {"data": {"data": data["data"], "metadata": details, "ignored": _SECRET}}


@pytest.fixture
def openbao(http_transport):
    return OpenBaoClient(
        OpenBaoConfig(
            "https://bao.example.test/gateway/openbao", _TOKEN, namespace="team/app/", timeout=12
        ),
        transport=http_transport,
    )


def test_nested_mount_and_secret_paths_are_literal_encoded_and_read_once(openbao, http_transport):
    http_transport.respond(response_data())

    secret = openbao.kv_v2.read("team kv/kv?prod", "nested/ümlaut #/?k", version=3, timeout=4)

    assert isinstance(secret, KvV2Secret)
    assert secret.data == {"key": _SECRET}
    assert secret.metadata == KvV2Metadata(7, "2024-01-02T03:04:05Z", "", False)
    call = http_transport.calls[0]
    assert call["method"] == "GET"
    assert (
        call["url"] == "https://bao.example.test/gateway/openbao/v1/team%20kv/kv%3Fprod/data/"
        "nested/%C3%BCmlaut%20%23/%3Fk"
    )
    assert call["params"] == {"version": "3"}
    assert call["timeout"] == 4
    assert call["body"] is None
    assert call["headers"] == {
        "Accept": "application/json",
        "X-Vault-Token": _TOKEN,
        "X-Vault-Namespace": "team/app/",
    }
    assert "Authorization" not in call["headers"]
    assert len(http_transport.calls) == 1


def test_latest_version_omits_query_and_per_call_timeout_is_capped(http_transport):
    client = OpenBaoClient(
        OpenBaoConfig("https://bao.example.test/base/", _TOKEN, timeout=2),
        transport=http_transport,
    )
    http_transport.respond(response_data(secret={}))

    secret = client.kv_v2.read("kv", "one", timeout=50)

    assert secret.data == {}
    assert http_transport.calls[0]["url"] == "https://bao.example.test/base/v1/kv/data/one"
    assert http_transport.calls[0]["params"] == {}
    assert http_transport.calls[0]["timeout"] == 2
    assert "X-Vault-Namespace" not in http_transport.calls[0]["headers"]


def test_custom_metadata_is_decoded_but_secret_material_stays_out_of_reprs(openbao, http_transport):
    custom = {"owner": _CUSTOM, "nested": {"labels": ["private"]}}
    http_transport.respond(
        response_data(
            secret={_SECRET_KEY: _SECRET},
            custom_metadata=custom,
            include_custom_metadata=True,
        )
    )

    secret = openbao.kv_v2.read("kv", "one")

    assert secret.metadata.custom_metadata == custom
    for representation in (
        repr(secret),
        repr(secret.metadata),
        repr(openbao.config),
        repr(openbao),
    ):
        assert _SECRET not in representation
        assert _SECRET_KEY not in representation
        assert _CUSTOM not in representation
        assert _TOKEN not in representation


def test_null_custom_metadata_and_empty_secret_map_are_valid(openbao, http_transport):
    http_transport.respond(
        response_data(secret={}, custom_metadata=None, include_custom_metadata=True)
    )

    secret = openbao.kv_v2.read("kv", "one")

    assert secret.data == {}
    assert secret.metadata.custom_metadata is None


@pytest.mark.parametrize("secret_data", [{}, {"key": _SECRET}])
def test_future_deletion_time_keeps_returned_secret_data_readable(
    openbao, http_transport, secret_data
):
    deletion_time = "9999-12-31T23:59:59.999999Z"
    http_transport.respond(
        response_data(secret=secret_data, metadata={"deletion_time": deletion_time})
    )

    secret = openbao.kv_v2.read("kv", "one")

    assert secret.data == secret_data
    assert secret.metadata.deletion_time == deletion_time


@pytest.mark.parametrize("metadata", [{"destroyed": True}, {"deletion_time": "deleted-at"}])
def test_destroyed_or_deleted_versions_are_unreadable(openbao, http_transport, metadata):
    http_transport.respond(response_data(secret=None, metadata=metadata))

    with pytest.raises(ResourceNotFoundError) as failure:
        openbao.kv_v2.read("kv", "one")

    assert str(failure.value) == "Requested KV v2 secret version is absent or unreadable."
    assert _SECRET not in "".join(traceback.format_exception(failure.value))


def test_destroyed_version_with_returned_data_is_unreadable(openbao, http_transport):
    http_transport.respond(response_data(secret={"key": _SECRET}, metadata={"destroyed": True}))

    with pytest.raises(ResourceNotFoundError):
        openbao.kv_v2.read("kv", "one")


def test_null_secret_without_deleted_metadata_is_protocol_error(openbao, http_transport):
    http_transport.respond(response_data(secret=None))

    with pytest.raises(ProtocolError) as failure:
        openbao.kv_v2.read("kv", "one")

    assert _SECRET not in "".join(traceback.format_exception(failure.value))


def test_404_is_sanitized_without_assuming_why_a_version_is_unreadable(openbao, http_transport):
    http_transport.responses.append(HttpResponse(404, b'{"errors":["secret body marker"]}'))

    with pytest.raises(ResourceNotFoundError) as failure:
        openbao.kv_v2.read("kv", "one", version=2)

    assert str(failure.value) == "Requested KV v2 secret version is absent or unreadable."
    assert "secret body marker" not in "".join(traceback.format_exception(failure.value))


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({}, "schema"),
        ({"data": []}, "schema"),
        ({"data": {"data": {}, "metadata": []}}, "schema"),
        (response_data(metadata={"version": True}), "positive integer"),
        (response_data(metadata={"version": 0}), "positive integer"),
        (response_data(metadata={"version": "7"}), "positive integer"),
        (response_data(metadata={"created_time": ""}), "string"),
        (response_data(metadata={"created_time": 7}), "string"),
        (response_data(metadata={"deletion_time": None}), "metadata schema"),
        (response_data(metadata={"destroyed": 1}), "boolean"),
        (response_data(include_custom_metadata=True, custom_metadata=[]), "object schema"),
        (
            response_data(
                metadata={"version": True},
                custom_metadata={"private": _CUSTOM},
                include_custom_metadata=True,
            ),
            "positive integer",
        ),
    ],
)
def test_invalid_envelope_and_metadata_schema_are_rejected(
    openbao, http_transport, response, message
):
    http_transport.respond(response)

    with pytest.raises(ProtocolError, match=message) as failure:
        openbao.kv_v2.read("kv", "one")

    assert _SECRET not in "".join(traceback.format_exception(failure.value))
    assert _TOKEN not in "".join(traceback.format_exception(failure.value))
    assert _CUSTOM not in "".join(traceback.format_exception(failure.value))


def test_unknown_response_fields_are_ignored(openbao, http_transport):
    payload = response_data()
    payload["unknown_top_level"] = {"sensitive": _SECRET}
    http_transport.respond(payload)

    assert openbao.kv_v2.read("kv", "one").data == {"key": _SECRET}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/kv",
        "kv/",
        "kv//nested",
        ".",
        "..",
        "nested/../secret",
        "kv\\nested",
        "kv%2Fnested",
        "line\nbreak",
        "control\x7f",
        "surrogate\ud800",
    ],
)
@pytest.mark.parametrize("argument", ["mount", "path"])
def test_invalid_logical_paths_fail_before_io(openbao, http_transport, argument, value):
    options = {"mount": "kv", "path": "secret"}
    options[argument] = value

    with pytest.raises(ConfigurationError) as failure:
        openbao.kv_v2.read(**options)

    if value not in ("", "."):
        assert value not in str(failure.value)
    assert http_transport.calls == []


@pytest.mark.parametrize("version", [0, -1, True, False, 1.5, "1"])
def test_invalid_versions_fail_before_io(openbao, http_transport, version):
    with pytest.raises(ConfigurationError):
        openbao.kv_v2.read("kv", "secret", version=version)
    assert http_transport.calls == []


@pytest.mark.parametrize("token", ["", "TOKEN\n", "TOKEN value", "not-ascii-秘密", None])
def test_tokens_are_printable_ascii_and_not_exposed(token):
    with pytest.raises(ConfigurationError) as failure:
        OpenBaoConfig("https://bao.example.test", token)
    assert "TOKEN" not in str(failure.value)
    assert "秘密" not in str(failure.value)


@pytest.mark.parametrize("namespace", ["", "team space", "team\nother", "team\x7f", "名"])
def test_invalid_namespaces_are_rejected_without_echoing_values(namespace):
    with pytest.raises(ConfigurationError) as failure:
        OpenBaoConfig("https://bao.example.test", _TOKEN, namespace=namespace)
    if namespace:
        assert namespace not in str(failure.value)


@pytest.mark.parametrize("timeout", [0, -1, True, float("inf"), float("nan"), "5"])
def test_config_timeout_must_be_finite_and_positive(timeout):
    with pytest.raises(ConfigurationError):
        OpenBaoConfig("https://bao.example.test", _TOKEN, timeout=timeout)


@pytest.mark.parametrize(
    "url",
    [
        "http://bao.example.test",
        "",
        "https://user:secret@bao.example.test",
        "https://bao.example.test?secret",
        "https://bao.example.test/#fragment",
        "https://bao.example.test/../path",
        "https://bao.example.test/%2e%2e/path",
    ],
)
def test_config_requires_explicit_safe_https_url(url):
    with pytest.raises(ConfigurationError) as failure:
        OpenBaoConfig(url, _TOKEN)
    assert "secret" not in str(failure.value)


def test_ca_bundle_is_validated_explicitly(tmp_path):
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("not interpreted here")
    config = OpenBaoConfig("https://bao.example.test", _TOKEN, ca_bundle=ca_file)
    assert config.ca_bundle == ca_file
    for invalid in (False, True, "", tmp_path / "missing.pem"):
        with pytest.raises(ConfigurationError):
            OpenBaoConfig("https://bao.example.test", _TOKEN, ca_bundle=invalid)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (409, ConflictError),
        (412, ConflictError),
        (429, RateLimitError),
        (400, HttpResponseError),
        (500, HttpResponseError),
    ],
)
def test_shared_http_status_errors_never_expose_response_data(
    openbao, http_transport, status, error, caplog
):
    http_transport.responses.append(HttpResponse(status, f"{_SECRET} {_CUSTOM}".encode()))

    with pytest.raises(error) as failure:
        openbao.kv_v2.read("kv", "secret")

    assert _SECRET not in "".join(traceback.format_exception(failure.value))
    assert _CUSTOM not in "".join(traceback.format_exception(failure.value))
    assert _TOKEN not in "".join(traceback.format_exception(failure.value))
    assert all(marker not in caplog.text for marker in (_SECRET, _SECRET_KEY, _CUSTOM, _TOKEN))
    assert len(http_transport.calls) == 1


def test_invalid_json_and_nested_secret_metadata_are_sanitized(openbao, http_transport):
    http_transport.responses.append(HttpResponse(200, b'{"data":"secret-value-marker"'))

    with pytest.raises(ProtocolError) as failure:
        openbao.kv_v2.read("kv", "secret")

    rendered = "".join(traceback.format_exception(failure.value))
    assert _SECRET not in rendered and _TOKEN not in rendered


def test_injected_transport_is_borrowed_and_closed_client_fails_normally(http_transport):
    client = OpenBaoClient(
        OpenBaoConfig("https://bao.example.test", _TOKEN), transport=http_transport
    )
    client.close()
    client.close()

    assert http_transport.closes == 0
    with pytest.raises(TransportError, match="closed"):
        client.kv_v2.read("kv", "secret")
    assert http_transport.calls == []


def test_default_transport_is_lazy_and_owned(monkeypatch):
    class OwnedTransport:
        closes = 0

        def close(self):
            self.closes += 1

    owned = OwnedTransport()
    monkeypatch.setattr(http_module, "HttpxTransport", lambda **kwargs: owned)
    client = OpenBaoClient(OpenBaoConfig("https://bao.example.test", _TOKEN))
    assert owned.closes == 0
    client.close()
    client.close()
    assert owned.closes == 1
    with pytest.raises(TransportError, match="closed"):
        client.kv_v2.read("kv", "secret")


def test_missing_http_extra_has_actionable_openbao_hint(monkeypatch):
    real_import = builtins.__import__

    def no_httpx(name, *args, **kwargs):
        if name == "maestro.common._httpx":
            raise ImportError(_SECRET)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_httpx)
    client = OpenBaoClient(OpenBaoConfig("https://bao.example.test", _TOKEN))
    with pytest.raises(MissingDependencyError) as failure:
        client.kv_v2.read("kv", "secret")
    rendered = "".join(traceback.format_exception(failure.value))
    assert "maestro[openbao]" in str(failure.value)
    assert _SECRET not in rendered and _TOKEN not in rendered


def test_httpx_mock_transport_wire_contract(monkeypatch):
    calls = []
    configurations = []
    real_client = httpx.Client

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_data(secret={"value": "ok"}))

    def client_factory(**kwargs):
        configurations.append(kwargs)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    client = OpenBaoClient(OpenBaoConfig("https://bao.example.test/base", _TOKEN))
    assert calls == []

    with client:
        secret = client.kv_v2.read("mount/team", "name with space", version=4)

    assert secret.data == {"value": "ok"}
    request = calls[0]
    assert request.method == "GET"
    assert request.url.raw_path == b"/base/v1/mount/team/data/name%20with%20space?version=4"
    assert request.headers["x-vault-token"] == _TOKEN
    assert "authorization" not in request.headers
    assert request.content == b""
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False
    verify = configurations[0]["verify"]
    assert verify.verify_mode == ssl.CERT_REQUIRED and verify.check_hostname


def test_explicit_version_is_the_only_query_parameter(http_transport):
    client = OpenBaoClient(
        OpenBaoConfig("https://bao.example.test", _TOKEN), transport=http_transport
    )
    http_transport.respond(response_data())

    client.kv_v2.read("mount", "literal:one+two", version=12)

    assert (
        http_transport.calls[0]["url"]
        == "https://bao.example.test/v1/mount/data/literal%3Aone%2Btwo"
    )
    assert http_transport.calls[0]["params"] == {"version": "12"}


def test_unknown_secret_values_and_server_text_are_not_used_in_failures(openbao, http_transport):
    http_transport.responses.append(HttpResponse(200, json.dumps({"data": _SECRET}).encode()))

    with pytest.raises(ProtocolError) as failure:
        openbao.kv_v2.read("kv", "secret")

    assert _SECRET not in str(failure.value)
    assert _TOKEN not in str(failure.value)
