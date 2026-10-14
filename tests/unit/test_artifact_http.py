import builtins
import ssl
import traceback
from collections import deque

import httpx
import pytest

from conductor.artifacts.transport import (
    ArtifactHttpResponse,
    HttpxArtifactTransport,
)
from conductor.common.errors import (
    ConfigurationError,
    MissingDependencyError,
    TlsError,
    TransportError,
    TransportTimeoutError,
)

RAW_CHUNK_SIZE = 1024 * 1024


class TrackingByteStream(httpx.SyncByteStream):
    def __init__(self, chunks, *, failure=None, close_failure=None):
        self.chunks = chunks
        self.failure = failure
        self.close_failure = close_failure
        self.closed = False
        self.started = False

    def __iter__(self):
        self.started = True
        yield from self.chunks
        if self.failure is not None:
            raise self.failure

    def close(self):
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


class ReadRequestBody:
    def __init__(self, response):
        self.response = response


@pytest.fixture
def mock_httpx(monkeypatch):
    real_client = httpx.Client
    responses = deque()
    requests = []
    configurations = []
    clients = []

    def handle(request):
        requests.append(request)
        assert responses, "Unexpected HTTPX request"
        response = responses.popleft()
        if isinstance(response, Exception):
            raise response
        if isinstance(response, ReadRequestBody):
            request.read()
            return response.response
        return response

    def client_factory(**kwargs):
        configurations.append(kwargs)
        client = real_client(transport=httpx.MockTransport(handle), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "Client", client_factory)
    return requests, responses, configurations, clients


def get(transport, url="https://nexus.example/context/raw"):
    return transport.stream(
        "GET",
        url,
        headers={"Authorization": "Bearer SECRET"},
        body=None,
        content_length=None,
        timeout=2,
    )


def test_public_response_repr_hides_headers_and_stream_and_is_frozen():
    response = ArtifactHttpResponse(200, {"X-Secret": "SECRET"}, iter([b"SECRET"]))
    assert "SECRET" not in repr(response)
    with pytest.raises(AttributeError):
        response.status_code = 201


def test_transport_is_lazy_and_close_is_idempotent():
    transport = HttpxArtifactTransport()
    transport.close()
    transport.close()
    with pytest.raises(TransportError, match="closed"):
        get(transport)


def test_get_streams_unmodified_raw_bytes_with_fixed_boundaries_and_security(mock_httpx):
    requests, responses, configurations, clients = mock_httpx
    raw = b"x" * (RAW_CHUNK_SIZE - 5) + b"y" * 10
    stream = TrackingByteStream([raw[: RAW_CHUNK_SIZE - 5], raw[RAW_CHUNK_SIZE - 5 :]])
    responses.append(
        httpx.Response(
            200,
            headers={"Content-Encoding": "gzip", "X-Secret": "SECRET"},
            stream=stream,
        )
    )
    transport = HttpxArtifactTransport()
    assert configurations == []
    with get(transport) as response:
        assert response.status_code == 200
        assert response.headers["x-secret"] == "SECRET"
        assert list(response.chunks) == [
            b"x" * (RAW_CHUNK_SIZE - 5) + b"y" * 5,
            b"y" * 5,
        ]
    assert stream.closed
    assert len(requests) == 1
    assert str(requests[0].url) == "https://nexus.example/context/raw"
    assert requests[0].headers["accept-encoding"] == "identity"
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False
    context = configurations[0]["verify"]
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    transport.close()
    assert clients[0].is_closed


def test_response_remains_unconsumed_until_chunks_are_requested(mock_httpx):
    _, responses, _, _ = mock_httpx
    stream = TrackingByteStream([b"first", b"second"])
    responses.append(httpx.Response(200, stream=stream))
    with get(HttpxArtifactTransport()) as response:
        assert not stream.started
        chunks = iter(response.chunks)
        assert next(chunks) == b"firstsecond"
        assert stream.started
    assert stream.closed


def test_put_streams_body_and_sets_one_authoritative_content_length(mock_httpx):
    requests, responses, _, _ = mock_httpx
    responses.append(ReadRequestBody(httpx.Response(201, stream=TrackingByteStream([]))))
    yielded = []

    def body():
        yielded.append(b"abc")
        yield b"abc"
        yielded.append(b"def")
        yield b"def"

    with HttpxArtifactTransport().stream(
        "PUT",
        "https://nexus.example/context/raw",
        headers={"X-Request": "upload"},
        body=body(),
        content_length=6,
        timeout=2,
    ) as response:
        assert response.status_code == 201
    request = requests[0]
    assert request.headers.get_list("content-length") == ["6"]
    assert "transfer-encoding" not in request.headers
    assert request.content == b"abcdef"
    assert yielded == [b"abc", b"def"]


@pytest.mark.parametrize(
    "args",
    [
        {"method": "POST", "url": "https://server"},
        {"method": "GET", "url": "http://server"},
        {"method": "GET", "url": "https://user:secret@server"},
        {"method": "GET", "url": "https://server/context/../raw"},
        {"method": "GET", "url": "https://server?secret"},
        {"method": "GET", "url": "https://server", "timeout": 0},
        {"method": "GET", "url": "https://server", "timeout": float("inf")},
        {"method": "GET", "url": "https://server", "body": [b"body"]},
        {"method": "PUT", "url": "https://server", "body": None, "length": 0},
        {"method": "PUT", "url": "https://server", "body": [b"body"], "length": None},
        {"method": "PUT", "url": "https://server", "body": [b"body"], "length": -1},
        {"method": "PUT", "url": "https://server", "body": [b"body"], "length": True},
        {"method": "GET", "url": "https://server", "headers": {"content-length": "0"}},
        {"method": "PUT", "url": "https://server", "headers": {"Transfer-Encoding": "chunked"}},
        {
            "method": "GET",
            "url": "https://server",
            "headers": {"Accept-Encoding": "gzip", "accept-encoding": "br"},
        },
    ],
)
def test_request_validation_precedes_httpx_initialization(mock_httpx, args):
    _, _, configurations, _ = mock_httpx
    transport = HttpxArtifactTransport()
    method = args.get("method", "GET")
    url = args.get("url", "https://server")
    headers = args.get("headers", {})
    body = args.get("body")
    length = args.get("length")
    timeout = args.get("timeout", 2)
    with pytest.raises(ConfigurationError):
        transport.stream(
            method,
            url,
            headers=headers,
            body=body,
            content_length=length,
            timeout=timeout,
        )
    assert configurations == []


def test_upload_body_chunks_and_declared_length_are_checked_while_streaming(mock_httpx):
    requests, responses, _, _ = mock_httpx
    responses.append(ReadRequestBody(httpx.Response(200, stream=TrackingByteStream([]))))
    with (
        pytest.raises(ConfigurationError, match="does not match"),
        HttpxArtifactTransport().stream(
            "PUT",
            "https://nexus.example/raw",
            headers={},
            body=iter([b"short"]),
            content_length=10,
            timeout=2,
        ),
    ):
        pass
    assert requests == []


def test_body_callback_exception_is_not_rewritten_or_replayed(mock_httpx):
    requests, responses, _, _ = mock_httpx
    responses.append(ReadRequestBody(httpx.Response(200, stream=TrackingByteStream([]))))

    def body():
        yield b"a"
        raise ValueError("caller SECRET failure")

    with (
        pytest.raises(ValueError, match="caller SECRET failure"),
        HttpxArtifactTransport().stream(
            "PUT",
            "https://nexus.example/raw",
            headers={},
            body=body(),
            content_length=2,
            timeout=2,
        ),
    ):
        pass
    assert requests == []


def test_redirects_are_not_followed_and_response_status_is_left_to_caller(mock_httpx):
    requests, responses, _, _ = mock_httpx
    responses.append(
        httpx.Response(
            302,
            headers={"Location": "https://attacker.invalid/secret"},
            stream=TrackingByteStream([]),
        )
    )
    with get(HttpxArtifactTransport()) as response:
        assert response.status_code == 302
        assert list(response.chunks) == []
    assert len(requests) == 1


@pytest.mark.parametrize(
    "error,expected",
    [
        (httpx.ReadTimeout("SECRET"), TransportTimeoutError),
        (httpx.ConnectError("SECRET"), TransportError),
    ],
)
def test_open_transport_failures_are_sanitized_and_not_replayed(mock_httpx, error, expected):
    requests, responses, _, _ = mock_httpx
    responses.append(error)
    with pytest.raises(expected) as failure, get(HttpxArtifactTransport()):
        pytest.fail("request unexpectedly succeeded")
    assert len(requests) == 1
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_tls_failure_is_distinguished_from_cause_chain_without_leaking_details(mock_httpx):
    requests, responses, _, _ = mock_httpx
    error = httpx.ConnectError("wire SECRET")
    error.__cause__ = ssl.SSLCertVerificationError("certificate SECRET")
    responses.append(error)
    with pytest.raises(TlsError) as failure, get(HttpxArtifactTransport()):
        pytest.fail("request unexpectedly succeeded")
    assert len(requests) == 1
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_iterator_transport_failure_is_sanitized_and_closes_response(mock_httpx):
    requests, responses, _, _ = mock_httpx
    stream = TrackingByteStream([b"prefix"], failure=httpx.ReadTimeout("wire SECRET"))
    responses.append(httpx.Response(200, stream=stream))
    with (
        pytest.raises(TransportTimeoutError) as failure,
        get(HttpxArtifactTransport()) as response,
    ):
        chunks = iter(response.chunks)
        assert next(chunks) == b"prefix"
        next(chunks)
    assert stream.closed
    assert len(requests) == 1
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_tls_failure_while_iterating_is_sanitized(mock_httpx):
    requests, responses, _, _ = mock_httpx
    error = httpx.ReadError("wire SECRET")
    error.__cause__ = ssl.SSLError("TLS SECRET")
    stream = TrackingByteStream([], failure=error)
    responses.append(httpx.Response(200, stream=stream))
    with pytest.raises(TlsError) as failure, get(HttpxArtifactTransport()) as response:
        next(iter(response.chunks))
    assert stream.closed
    assert len(requests) == 1
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_caller_failure_closes_response_and_is_not_masked_by_cleanup(mock_httpx, caplog):
    _, responses, _, _ = mock_httpx
    stream = TrackingByteStream([], close_failure=OSError("cleanup SECRET"))
    responses.append(httpx.Response(200, stream=stream))
    with pytest.raises(ValueError, match="caller SECRET"), get(HttpxArtifactTransport()):
        raise ValueError("caller SECRET")
    assert stream.closed
    assert "original error is preserved" in caplog.text
    assert "cleanup SECRET" not in caplog.text


def test_normal_cleanup_failure_is_sanitized(mock_httpx):
    _, responses, _, _ = mock_httpx
    stream = TrackingByteStream([], close_failure=OSError("cleanup SECRET"))
    responses.append(httpx.Response(200, stream=stream))
    with pytest.raises(TransportError) as failure, get(HttpxArtifactTransport()):
        pass
    assert stream.closed
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))


def test_explicit_ca_is_used_without_environment_or_redirects(mock_httpx, monkeypatch, tmp_path):
    _, responses, configurations, _ = mock_httpx
    ca_bundle = tmp_path / "private-ca.pem"
    ca_bundle.write_text("placeholder")
    actual_context = ssl.create_default_context
    passed = []

    def explicit_context(*, cafile=None):
        passed.append(cafile)
        return actual_context()

    monkeypatch.setattr(ssl, "create_default_context", explicit_context)
    responses.append(httpx.Response(200, stream=TrackingByteStream([])))
    with get(HttpxArtifactTransport(ca_bundle=ca_bundle)):
        pass
    assert passed == [str(ca_bundle)]
    assert configurations[0]["trust_env"] is False
    assert configurations[0]["follow_redirects"] is False


def test_invalid_ca_is_rejected_at_construction(tmp_path):
    transport = HttpxArtifactTransport
    with pytest.raises(ConfigurationError):
        transport(ca_bundle=tmp_path / "missing.pem")
    with pytest.raises(ConfigurationError):
        transport(ca_bundle=True)


def test_missing_optional_http_dependency_is_actionable_and_lazy(monkeypatch):
    real_import = builtins.__import__

    def without_adapter(name, *args, **kwargs):
        if name == "conductor.artifacts._httpx":
            raise ImportError("private SECRET")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_adapter)
    transport = HttpxArtifactTransport()
    with pytest.raises(MissingDependencyError) as failure:
        get(transport)
    assert "conductor[nexus]" in str(failure.value)
    assert "SECRET" not in "".join(traceback.format_exception(failure.value))
