import base64
import builtins
import hashlib
import ssl
import tempfile
import traceback
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from maestro.artifacts import ArtifactInfo
from maestro.artifacts._files import CHUNK_SIZE
from maestro.artifacts.errors import ArtifactFileError, ArtifactIntegrityError
from maestro.artifacts.nexus import NexusRawArtifacts, NexusRawConfig
from maestro.artifacts.transport import ArtifactHttpResponse
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
    TransportTimeoutError,
)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def make_config(**kwargs):
    values = {
        "base_url": "https://nexus.example/deployment/nexus/",
        "repository": "raw hosted",
        "username": "build-user",
        "password": "build-secret",
    }
    values.update(kwargs)
    return NexusRawConfig(**values)


class FakeResponseStream:
    def __init__(self, chunks=(), *, failure=None):
        self.chunks = chunks
        self.failure = failure
        self.started = False

    def __iter__(self):
        self.started = True
        yield from self.chunks
        if self.failure is not None:
            raise self.failure


class FakeArtifactTransport:
    def __init__(self, *, on_put=None):
        self.responses = deque()
        self.calls = []
        self.closed_responses = []
        self.closes = 0
        self.on_put = on_put

    @contextmanager
    def stream(
        self,
        method,
        url,
        *,
        headers,
        body,
        content_length,
        timeout,
    ):
        call = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "content_length": content_length,
            "timeout": timeout,
        }
        self.calls.append(call)
        if method == "PUT":
            if self.on_put is not None:
                self.on_put()
            assert body is not None
            call["body_bytes"] = b"".join(body)
        assert self.responses, "Unexpected Nexus HTTP request"
        response, stream = self.responses.popleft()
        try:
            yield ArtifactHttpResponse(response.status_code, response.headers, iter(stream))
        finally:
            self.closed_responses.append(stream)

    def close(self):
        self.closes += 1

    def respond(self, status, chunks=(), *, headers=None, failure=None):
        self.responses.append(
            (
                _Response(status, headers or {}),
                FakeResponseStream(chunks, failure=failure),
            )
        )


class _Response:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = headers


def test_config_validates_secure_endpoint_repository_credentials_and_timeout():
    config = make_config(timeout=15)
    assert config.base_url == "https://nexus.example/deployment/nexus"
    assert config.repository == "raw hosted"
    assert config.timeout == 15.0
    assert "build-secret" not in repr(config)
    assert "build-user" not in repr(config)

    for kwargs in (
        {"base_url": "http://nexus.example"},
        {"base_url": "https://user:secret@nexus.example"},
        {"base_url": "https://nexus.example?token=secret"},
        {"repository": "../raw"},
        {"repository": "raw/hosted"},
        {"username": ""},
        {"username": "build:user"},
        {"username": "bad\nuser"},
        {"username": "bad\x85user"},
        {"password": ""},
        {"password": None},
        {"password": "\ud800"},
        {"timeout": True},
        {"timeout": 0},
        {"timeout": float("inf")},
    ):
        with pytest.raises(ConfigurationError) as failure:
            make_config(**kwargs)
        assert "build-secret" not in str(failure.value)


def test_explicit_ca_must_be_a_file(tmp_path):
    with pytest.raises(ConfigurationError):
        make_config(ca_bundle=tmp_path / "missing.pem")
    pem = tmp_path / "ca.pem"
    pem.write_text("placeholder")
    config = make_config(ca_bundle=pem)
    assert config.ca_bundle == pem


def test_publish_sends_verified_snapshot_to_exact_raw_hosted_url(tmp_path):
    source = tmp_path / "upload.bin"
    source.write_bytes(b"original snapshot bytes")

    def modify_source_during_put():
        source.write_bytes(b"changed after snapshot")

    transport = FakeArtifactTransport(on_put=modify_source_during_put)
    transport.respond(201)
    client = NexusRawArtifacts(make_config(timeout=12), transport=transport)
    result = client.publish(source, "releases/app/v1.bin", timeout=60)

    assert result == ArtifactInfo(
        "releases/app/v1.bin",
        len(b"original snapshot bytes"),
        sha256(b"original snapshot bytes"),
    )
    request = transport.calls[0]
    assert request["method"] == "PUT"
    assert request["url"] == (
        "https://nexus.example/deployment/nexus/repository/raw%20hosted/releases/app/v1.bin"
    )
    assert request["headers"]["Authorization"] == "Basic " + base64.b64encode(
        b"build-user:build-secret"
    ).decode("ascii")
    assert request["headers"]["Content-Type"] == "application/octet-stream"
    assert request["content_length"] == len(b"original snapshot bytes")
    assert request["body_bytes"] == b"original snapshot bytes"
    assert request["timeout"] == 12
    assert source.read_bytes() == b"changed after snapshot"
    assert transport.closed_responses


def test_publish_checksum_failure_precedes_http_and_snapshot_cleanup(tmp_path):
    source = tmp_path / "wrong.bin"
    source.write_bytes(b"not the expected value")
    transport = FakeArtifactTransport()
    client = NexusRawArtifacts(make_config(), transport=transport)

    with pytest.raises(ArtifactIntegrityError):
        client.publish(source, "a.bin", expected_sha256=sha256(b"expected"))
    assert transport.calls == []
    assert list(tmp_path.iterdir()) == [source]


def test_publish_requires_exact_201_and_maps_status_without_reading_response(tmp_path):
    for status, error in (
        (200, ProtocolError),
        (204, ProtocolError),
        (302, ProtocolError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, ResourceNotFoundError),
        (409, ConflictError),
        (412, ConflictError),
        (429, RateLimitError),
        (500, HttpResponseError),
    ):
        transport = FakeArtifactTransport()
        stream = FakeResponseStream([b"SECRET body"])
        transport.responses.append((_Response(status, {}), stream))
        source = tmp_path / f"upload-{status}.bin"
        source.write_bytes(b"verified")
        client = NexusRawArtifacts(make_config(), transport=transport)
        with pytest.raises(error) as failure:
            client.publish(source, "a.bin")
        assert not stream.started
        assert transport.closed_responses == [stream]
        assert "SECRET body" not in str(failure.value)
        assert "a.bin" not in str(failure.value)


def test_retrieve_streams_exact_get_and_verifies_before_commit(tmp_path):
    payload = b"complete downloaded artifact"
    destination = tmp_path / "download.bin"
    transport = FakeArtifactTransport()
    response_stream = FakeResponseStream([payload[:7], payload[7:]])
    transport.responses.append(
        (
            _Response(
                200,
                {"Content-Length": str(len(payload)), "Content-Encoding": "identity"},
            ),
            response_stream,
        )
    )
    client = NexusRawArtifacts(make_config(), transport=transport)

    result = client.retrieve(
        "team/build/artifact.bin", destination, expected_sha256=sha256(payload)
    )
    assert result == ArtifactInfo("team/build/artifact.bin", len(payload), sha256(payload))
    assert destination.read_bytes() == payload
    request = transport.calls[0]
    assert request["method"] == "GET"
    assert request["url"] == (
        "https://nexus.example/deployment/nexus/repository/raw%20hosted/team/build/artifact.bin"
    )
    assert request["body"] is None and request["content_length"] is None
    assert request["headers"]["Authorization"].startswith("Basic ")
    assert request["timeout"] == 30.0
    assert response_stream.started and transport.closed_responses == [response_stream]


def test_retrieve_requires_exact_200_and_maps_status_without_consuming_body(tmp_path):
    destination = tmp_path / "download.bin"
    for status, error in (
        (201, ProtocolError),
        (206, ProtocolError),
        (302, ProtocolError),
        (401, AuthenticationError),
        (403, PermissionDeniedError),
        (404, ResourceNotFoundError),
        (409, ConflictError),
        (412, ConflictError),
        (429, RateLimitError),
        (500, HttpResponseError),
    ):
        transport = FakeArtifactTransport()
        stream = FakeResponseStream([b"SECRET body"])
        transport.responses.append((_Response(status, {}), stream))
        client = NexusRawArtifacts(make_config(), transport=transport)
        with pytest.raises(error) as failure:
            client.retrieve("a.bin", destination, expected_sha256=sha256(b"value"))
        assert not stream.started
        assert transport.closed_responses == [stream]
        assert "SECRET body" not in str(failure.value)


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Encoding": "gzip"},
        {"content-encoding": "identity, gzip"},
        {"Content-Encoding": ""},
        {"Content-Encoding": "identity", "content-encoding": "identity"},
        {"Content-Length": "-1"},
        {"Content-Length": "+5"},
        {"Content-Length": "1, 1"},
        {"Content-Length": "not-a-number"},
        {"Content-Length": "999999999999999999999999999999999999999999999999999999999999" * 100},
        {"Content-Length": "4", "content-length": "4"},
    ],
)
def test_retrieve_rejects_bad_integrity_headers_and_closes_response(tmp_path, headers):
    transport = FakeArtifactTransport()
    stream = FakeResponseStream([b"data"])
    transport.responses.append((_Response(200, headers), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)

    with pytest.raises(ProtocolError):
        client.retrieve("a.bin", tmp_path / "artifact", expected_sha256=sha256(b"data"))
    assert not stream.started
    assert transport.closed_responses == [stream]
    assert not (tmp_path / "artifact").exists()


def test_bad_digest_and_content_length_mismatch_preserve_existing_file(tmp_path):
    destination = tmp_path / "existing.bin"
    destination.write_bytes(b"old value")
    payload = b"new bytes"
    transport = FakeArtifactTransport()
    stream = FakeResponseStream([payload])
    transport.responses.append((_Response(200, {"Content-Length": "3"}), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)

    with pytest.raises(ArtifactIntegrityError):
        client.retrieve("a.bin", destination, expected_sha256=sha256(payload), overwrite=True)
    assert destination.read_bytes() == b"old value"
    assert transport.closed_responses == [stream]

    transport = FakeArtifactTransport()
    stream = FakeResponseStream([payload])
    transport.responses.append((_Response(200, {}), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)
    with pytest.raises(ArtifactIntegrityError):
        client.retrieve("a.bin", destination, expected_sha256=sha256(b"wrong"), overwrite=True)
    assert destination.read_bytes() == b"old value"
    assert transport.closed_responses == [stream]


def test_retrieve_overwrite_local_conflict_and_explicit_replace(tmp_path):
    destination = tmp_path / "artifact.bin"
    destination.write_bytes(b"old")
    payload = b"new"
    transport = FakeArtifactTransport()
    stream = FakeResponseStream([payload])
    transport.responses.append((_Response(200, {}), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)

    with pytest.raises(ConflictError):
        client.retrieve("a.bin", destination, expected_sha256=sha256(payload))
    assert destination.read_bytes() == b"old"
    assert transport.closed_responses == [stream]

    transport = FakeArtifactTransport()
    stream = FakeResponseStream([payload])
    transport.responses.append((_Response(200, {}), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)
    client.retrieve("a.bin", destination, expected_sha256=sha256(payload), overwrite=True)
    assert destination.read_bytes() == payload


def test_retrieve_transport_iteration_failure_is_propagated_and_closes_response(tmp_path):
    transport = FakeArtifactTransport()
    stream = FakeResponseStream([b"prefix"], failure=TransportTimeoutError("sanitized timeout"))
    transport.responses.append((_Response(200, {}), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)

    with pytest.raises(TransportTimeoutError):
        client.retrieve("a.bin", tmp_path / "artifact", expected_sha256=sha256(b"prefix"))
    assert len(transport.calls) == 1
    assert transport.closed_responses == [stream]
    assert not (tmp_path / "artifact").exists()


def test_retrieve_write_failure_closes_response(tmp_path):
    transport = FakeArtifactTransport()
    stream = FakeResponseStream([b"data"])
    transport.responses.append((_Response(200, {}), stream))
    client = NexusRawArtifacts(make_config(), transport=transport)

    with pytest.raises(ArtifactFileError):
        client.retrieve("a.bin", tmp_path / "missing" / "artifact", expected_sha256=sha256(b"data"))
    assert not stream.started
    assert transport.closed_responses == [stream]


def test_invalid_operation_inputs_fail_before_transport_io(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"data")
    transport = FakeArtifactTransport()
    client = NexusRawArtifacts(make_config(timeout=5), transport=transport)

    invalid = (
        lambda: client.publish(source, "../bad"),
        lambda: client.publish(source, "a.bin", expected_sha256="bad"),
        lambda: client.publish(source, "a.bin", timeout=0),
        lambda: client.retrieve("a.bin", tmp_path / "target", expected_sha256="bad"),
        lambda: client.retrieve(
            "a.bin", tmp_path / "target", expected_sha256=sha256(b"data"), timeout=True
        ),
        lambda: client.retrieve("a.bin", tmp_path / "../target", expected_sha256=sha256(b"data")),
        lambda: client.retrieve(
            "a.bin", tmp_path / "target", expected_sha256=sha256(b"data"), overwrite=1
        ),
    )
    for operation in invalid:
        with pytest.raises(ConfigurationError):
            operation()
    assert transport.calls == []


def test_borrowed_and_owned_transport_lifetimes_and_closed_state(caplog, monkeypatch):
    borrowed = FakeArtifactTransport()
    client = NexusRawArtifacts(make_config(), transport=borrowed)
    client.close()
    client.close()
    assert borrowed.closes == 0
    with pytest.raises(TransportError, match="closed"):
        client.retrieve("a.bin", "out.bin", expected_sha256=sha256(b"x"))

    class OwnedTransport(FakeArtifactTransport):
        def close(self):
            self.closes += 1
            raise TransportError("cleanup SECRET")

    owned = OwnedTransport()
    monkeypatch.setattr(
        "maestro.artifacts.nexus.HttpxArtifactTransport",
        lambda **kwargs: owned,
    )
    monkeypatched = NexusRawArtifacts(make_config())
    primary = TransportTimeoutError("primary SECRET")
    with pytest.raises(TransportTimeoutError) as failure, monkeypatched:
        raise primary
    assert failure.value is primary
    assert "original error is preserved" in caplog.text
    assert "SECRET" not in caplog.text
    assert owned.closes == 1


def test_default_transport_construction_is_lazy_and_missing_dependency_actionable(
    monkeypatch, tmp_path
):
    real_import = builtins.__import__
    transport = NexusRawArtifacts(make_config())
    transport.close()

    def without_httpx_adapter(name, *args, **kwargs):
        if name == "maestro.artifacts._httpx":
            raise ImportError("private SECRET")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_httpx_adapter)
    source = tmp_path / "source.bin"
    source.write_bytes(b"artifact")
    client = NexusRawArtifacts(make_config())
    with pytest.raises(MissingDependencyError) as failure:
        client.publish(source, "a.bin")
    assert "maestro[nexus]" in str(failure.value)
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_actual_httpx_adapter_emits_nexus_basic_auth_requests_and_streams_bodies(
    monkeypatch, tmp_path
):
    real_client = httpx.Client
    responses = deque(
        [
            httpx.Response(201, stream=httpx.ByteStream(b"")),
            httpx.Response(
                200,
                headers={"Content-Length": "5", "Content-Encoding": "identity"},
                stream=httpx.ByteStream(b"hello"),
            ),
        ]
    )
    requests = []
    configurations = []

    def handle(request):
        requests.append(request)
        return responses.popleft()

    def client_factory(**kwargs):
        configurations.append(kwargs)
        return real_client(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    client = NexusRawArtifacts(make_config())
    source = tmp_path / "wire-upload.bin"
    source.write_bytes(b"upload")
    uploaded = client.publish(source, "releases/a.bin")
    retrieved_path = tmp_path / "wire-download.bin"
    retrieved = client.retrieve("releases/a.bin", retrieved_path, expected_sha256=sha256(b"hello"))

    assert uploaded.sha256 == sha256(b"upload")
    assert retrieved.sha256 == sha256(b"hello")
    assert retrieved_path.read_bytes() == b"hello"
    assert [str(request.url) for request in requests] == [
        "https://nexus.example/deployment/nexus/repository/raw%20hosted/releases/a.bin",
        "https://nexus.example/deployment/nexus/repository/raw%20hosted/releases/a.bin",
    ]
    assert [request.method for request in requests] == ["PUT", "GET"]
    expected_auth = "Basic " + base64.b64encode(b"build-user:build-secret").decode("ascii")
    assert all(request.headers["authorization"] == expected_auth for request in requests)
    assert requests[0].headers.get_list("content-length") == ["6"]
    assert requests[0].content == b"upload"
    assert requests[1].headers["accept-encoding"] == "identity"
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False
    context = configurations[0]["verify"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    client.close()


class _TrackedReader:
    def __init__(self, stream, *, fail_close=False):
        self._stream = stream
        self._fail_close = fail_close
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback_object):
        self.close()

    def read(self, size=-1):
        return self._stream.read(size)

    def fileno(self):
        return self._stream.fileno()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._stream.close()
        if self._fail_close:
            raise OSError("reader cleanup SECRET")


class _PartialWriteTransport(httpx.BaseTransport):
    def __init__(self, *, fail_after_one_chunk):
        self.fail_after_one_chunk = fail_after_one_chunk
        self.chunk_sizes = []

    def handle_request(self, request):
        assert request.method == "PUT"
        body = iter(request.stream)
        self.chunk_sizes.append(len(next(body)))
        if self.fail_after_one_chunk:
            raise httpx.WriteError("wire SECRET")
        self.chunk_sizes.extend(len(chunk) for chunk in body)
        return httpx.Response(201)


class _EarlyResponseTransport(httpx.BaseTransport):
    def __init__(self):
        self.chunk_sizes = []

    def handle_request(self, request):
        assert request.method == "PUT"
        body = iter(request.stream)
        self.chunk_sizes.append(len(next(body)))
        return httpx.Response(201)


def _track_snapshot_reader(monkeypatch, *, fail_close=False):
    real_open = Path.open
    real_unlink = Path.unlink
    readers = []
    closed_before_unlink = []

    def tracked_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        mode = args[0] if args else kwargs.get("mode", "r")
        if path.name == "snapshot.bin" and mode == "rb":
            reader = _TrackedReader(stream, fail_close=fail_close)
            readers.append(reader)
            return reader
        return stream

    def tracked_unlink(path, *args, **kwargs):
        if path.name == "snapshot.bin":
            closed_before_unlink.append(bool(readers and readers[-1].closed))
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(Path, "unlink", tracked_unlink)
    return readers, closed_before_unlink


def _install_base_transport(monkeypatch, wire_transport):
    real_client = httpx.Client

    def client_factory(**kwargs):
        return real_client(transport=wire_transport, **kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)


def _write_large_source(source):
    block = b"x" * (64 * 1024)
    with source.open("wb") as output:
        for _ in range((3 * CHUNK_SIZE) // len(block)):
            output.write(block)


def test_publish_closes_upload_reader_before_snapshot_cleanup_after_partial_write(
    monkeypatch, tmp_path, caplog
):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    source = tmp_path / "large-upload.bin"
    _write_large_source(source)

    wire = _PartialWriteTransport(fail_after_one_chunk=True)
    _install_base_transport(monkeypatch, wire)
    readers, closed_before_unlink = _track_snapshot_reader(monkeypatch, fail_close=True)
    client = NexusRawArtifacts(make_config())

    with pytest.raises(TransportError) as failure:
        client.publish(source, "releases/large.bin")

    assert wire.chunk_sizes == [CHUNK_SIZE]
    assert readers and readers[0].closed
    assert closed_before_unlink == [True]
    assert list(scratch.iterdir()) == []
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))
    assert "SECRET" not in caplog.text


def test_publish_upload_reader_closes_before_snapshot_cleanup_on_success(monkeypatch, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    source = tmp_path / "large-upload.bin"
    _write_large_source(source)

    wire = _PartialWriteTransport(fail_after_one_chunk=False)
    _install_base_transport(monkeypatch, wire)
    readers, closed_before_unlink = _track_snapshot_reader(monkeypatch)
    client = NexusRawArtifacts(make_config())

    result = client.publish(source, "releases/large.bin")

    assert result.size == 3 * CHUNK_SIZE
    assert wire.chunk_sizes == [CHUNK_SIZE, CHUNK_SIZE, CHUNK_SIZE]
    assert readers and readers[0].closed
    assert closed_before_unlink == [True]
    assert list(scratch.iterdir()) == []


def test_publish_surfaces_sanitized_upload_reader_close_failure_without_primary(
    monkeypatch, tmp_path
):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    source = tmp_path / "large-upload.bin"
    _write_large_source(source)

    # This transport accepts a partial request, so client-side cleanup is the only
    # reason the incomplete local stream cannot be reported as a successful publish.
    wire = _EarlyResponseTransport()
    _install_base_transport(monkeypatch, wire)
    readers, closed_before_unlink = _track_snapshot_reader(monkeypatch, fail_close=True)
    client = NexusRawArtifacts(make_config())

    with pytest.raises(ArtifactFileError) as failure:
        client.publish(source, "releases/large.bin")

    assert isinstance(failure.value, ArtifactFileError)
    assert wire.chunk_sizes == [CHUNK_SIZE]
    assert readers and readers[0].closed
    assert closed_before_unlink == [True]
    assert list(scratch.iterdir()) == []
