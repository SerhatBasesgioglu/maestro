import json
import socket
import subprocess
from collections import deque
from pathlib import Path

import pytest

from maestro.common.http import HttpResponse


class FakeHttpTransport:
    """A durable, dependency-free seam: request capture plus controlled wire responses."""

    def __init__(self):
        self.responses = deque()
        self.calls = []
        self.closes = 0

    def respond(self, data, *, status=200, headers=None):
        self.responses.append(HttpResponse(status, json.dumps(data).encode(), headers or {}))

    def request(self, method, url, *, headers, params=None, json_body=None, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "params": dict(params or {}),
                "body": json_body,
                "timeout": timeout,
            }
        )
        assert self.responses, "Unexpected HTTP request"
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closes += 1


@pytest.fixture
def http_transport():
    return FakeHttpTransport()


@pytest.fixture(autouse=True)
def forbid_unit_test_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unit tests must not open network connections.")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture(autouse=True)
def forbid_unit_test_ssh_processes(monkeypatch):
    original = subprocess.Popen

    def guarded(arguments, *args, **kwargs):
        executable = arguments if isinstance(arguments, str) else arguments[0]
        if Path(executable).name in ("ssh", "rsync"):
            raise AssertionError("Unit tests must not launch live SSH/rsync processes.")
        return original(arguments, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", guarded)
