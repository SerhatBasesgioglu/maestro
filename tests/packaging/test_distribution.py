import email
import json
import subprocess
import sys
import tarfile
import venv
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def distribution(tmp_path_factory):
    output = tmp_path_factory.mktemp("distribution")
    subprocess.run(
        [sys.executable, "-m", "build", "--no-isolation", "--outdir", str(output)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return next(output.glob("*.whl")), next(output.glob("*.tar.gz"))


def test_distribution_contains_typed_library_and_optional_only_runtime_dependencies(distribution):
    wheel, sdist = distribution
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "conductor/py.typed" in names
        assert "conductor/iis/application_pools.py" in names
        assert "conductor/windows/client.py" in names
        assert "conductor/windows/services.py" in names
        assert "conductor/artifacts/filesystem.py" in names
        assert "conductor/artifacts/transport.py" in names
        assert "conductor/openbao/client.py" in names
        assert "conductor/openbao/config.py" in names
        assert "conductor/openbao/__init__.py" in names
        assert "conductor/openbao/kv_v2.py" in names
        assert "conductor/openbao/models.py" in names
        assert any(
            name.startswith(("conductor/artifacts/nexus", "conductor/nexus"))
            and name.endswith(".py")
            for name in names
        )
        for integration in ("azure_devops", "argocd", "sonarqube", "linux"):
            assert f"conductor/{integration}/client.py" in names
        assert not any(name.startswith("tests/") for name in names)
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = email.message_from_bytes(archive.read(metadata_name))
    assert metadata["Name"] == "conductor"
    assert metadata["Version"] == "0.1.0"
    assert set(metadata.get_all("Provides-Extra")) == {
        "azure-devops",
        "argocd",
        "sonarqube",
        "iis",
        "kerberos",
        "linux",
        "openbao",
        "nexus",
        "windows",
    }
    assert all("extra ==" in requirement for requirement in metadata.get_all("Requires-Dist"))
    assert any(
        "httpx" in requirement and "openbao" in requirement
        for requirement in metadata.get_all("Requires-Dist")
    )
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
    assert any(name.endswith("/LIBRARY_DESIGN.md") for name in names)
    assert any(name.endswith("/tests/unit/test_application_pools.py") for name in names)
    for test_module in (
        "test_artifact_filesystem.py",
        "test_artifact_http.py",
        "test_nexus.py",
        "test_openbao.py",
    ):
        assert any(name.endswith(f"/tests/unit/{test_module}") for name in names)


def test_wheel_works_without_sdk_framework_or_live_target(distribution, tmp_path):
    wheel, _ = distribution
    environment = tmp_path / "isolated"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "--disable-pip-version-check",
            "install",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    consumer = r"""
import base64
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

root_handlers = list(logging.getLogger().handlers)
assert importlib.util.find_spec("pypsrp") is None
assert importlib.util.find_spec("httpx") is None
import conductor
from conductor.argocd import ArgoCdClient, ArgoCdConfig, ApplicationHealth
from conductor.azure_devops import AzureDevOpsClient, AzureDevOpsConfig
from conductor.artifacts import (
    ArtifactFileError, ArtifactHttpResponse, ArtifactHttpTransport,
    ArtifactInfo, ArtifactIntegrityError, FilesystemArtifacts,
    HttpxArtifactTransport, NexusRawArtifacts, NexusRawConfig,
)
from conductor.common.errors import MissingDependencyError
from conductor.common.http import HttpResponse
from conductor.openbao import (
    KvV2Metadata, KvV2Secret, OpenBaoClient, OpenBaoConfig,
)
from conductor.iis import ApplicationPoolState, IisClient
from conductor.linux import ComposeProject, LinuxClient, ServiceState
from conductor.sonarqube import SonarQubeClient, SonarQubeConfig, QualityGateStatus
from conductor.windows import (
    ServiceChangeResult, ServiceCommandError, ServiceDependencyError,
    ServiceDisabledError, ServiceNotFoundError, ServiceProtocolError,
    ServiceState as WindowsServiceState, ServiceStateError, WindowsClient,
)
from conductor.transports import (
    CommandResult, OpenSshTransport, PowerShellRemoting, PowerShellResult,
)

assert conductor.__version__ == importlib.metadata.version("conductor") == "0.1.0"
assert list(logging.getLogger().handlers) == root_handlers
assert not any(name == "pypsrp" or name.startswith("pypsrp.") for name in sys.modules)
assert importlib.util.find_spec("pypsrp") is None
assert not any(
    name in sys.modules for name in
    ("fastapi", "mcp", "kubernetes", "requests", "httpx", "ruamel.yaml")
)
assert "httpx" not in sys.modules

openbao_config = OpenBaoConfig(
    server_url="https://openbao.example.invalid/platform/",
    token="OPENBAO_FAKE_TOKEN",
    namespace="team/app",
    timeout=2,
)
assert "OPENBAO_FAKE_TOKEN" not in repr(openbao_config)
assert "httpx" not in sys.modules

class FakeOpenBaoHttp:
    def __init__(self):
        self.calls = []
        self.closed = 0

    def request(self, method, url, *, headers, params=None, json_body=None, timeout=None):
        self.calls.append({
            "method": method, "url": url, "headers": dict(headers),
            "params": dict(params or {}), "json_body": json_body, "timeout": timeout,
        })
        envelope = {
            "data": {
                "data": {"password": "distinctive-fake-only"},
                "metadata": {
                    "version": 2,
                    "created_time": "2026-01-02T03:04:05Z",
                    "deletion_time": "",
                    "destroyed": False,
                    "custom_metadata": {"probe": "fake-sensitive-metadata"},
                },
            }
        }
        return HttpResponse(200, json.dumps(envelope).encode("utf-8"))

    def close(self):
        self.closed += 1

openbao_transport = FakeOpenBaoHttp()
with OpenBaoClient(openbao_config, transport=openbao_transport) as openbao:
    openbao_secret = openbao.kv_v2.read(
        "secret", "apps/widget", version=2, timeout=10
    )
    selected_secret_value = openbao_secret.data["password"]
assert isinstance(openbao_secret, KvV2Secret)
assert isinstance(openbao_secret.metadata, KvV2Metadata)
assert selected_secret_value == "distinctive-fake-only"
assert openbao_secret.metadata.version == 2
assert openbao_secret.metadata.created_time
assert openbao_secret.metadata.deletion_time == ""
assert not openbao_secret.metadata.destroyed
assert openbao_secret.metadata.custom_metadata == {"probe": "fake-sensitive-metadata"}
assert "distinctive-fake-only" not in repr(openbao_secret)
assert "fake-sensitive-metadata" not in repr(openbao_secret.metadata)
assert "OPENBAO_FAKE_TOKEN" not in repr(openbao_secret)
assert openbao_transport.closed == 0
assert openbao_transport.calls == [{
    "method": "GET",
    "url": "https://openbao.example.invalid/platform/v1/secret/data/apps/widget",
    "headers": {
        "Accept": "application/json",
        "X-Vault-Token": "OPENBAO_FAKE_TOKEN",
        "X-Vault-Namespace": "team/app",
    },
    "params": {"version": "2"},
    "json_body": None,
    "timeout": 2.0,
}]
assert "httpx" not in sys.modules

scratch = Path("private-artifact-scratch")
scratch.mkdir()
tempfile.tempdir = str(scratch.resolve())
artifact_root = Path("artifact-share")
artifact_root.mkdir()
key = "releases/1.2.0/widget.zip"
(artifact_root / "releases" / "1.2.0").mkdir(parents=True)
source = Path("widget.zip")
source_bytes = b"clean-wheel filesystem artifact"
source.write_bytes(source_bytes)
stored = FilesystemArtifacts(artifact_root).publish(source, key)
assert isinstance(stored, ArtifactInfo)
assert stored.sha256 == hashlib.sha256(source_bytes).hexdigest()
download_directory = Path("filesystem-download")
download_directory.mkdir()
downloaded = download_directory / "widget.zip"
retrieved = FilesystemArtifacts(artifact_root).retrieve(
    key, downloaded, expected_sha256=stored.sha256
)
assert retrieved.sha256 == stored.sha256
assert downloaded.read_bytes() == source_bytes

nexus_bytes = b"injected Nexus artifact stream"
nexus_digest = hashlib.sha256(nexus_bytes).hexdigest()
nexus_key = "releases/1.2.0/widget.zip"
nexus_config = NexusRawConfig(
    base_url="https://nexus.example.invalid/context",
    repository="raw-hosted",
    username="artifact-reader",
    password="NEXUS_SECRET",
)
assert "NEXUS_SECRET" not in repr(nexus_config)

class FakeArtifactHttp:
    def __init__(self):
        self.responses_closed = 0
        self.transport_closed = 0

    @contextmanager
    def stream(
        self, method, url, *, headers, body, content_length, timeout
    ):
        assert method == "GET"
        assert url == (
            "https://nexus.example.invalid/context/repository/raw-hosted/"
            "releases/1.2.0/widget.zip"
        )
        assert body is None
        assert content_length is None
        assert timeout > 0
        expected_auth = "Basic " + base64.b64encode(
            b"artifact-reader:NEXUS_SECRET"
        ).decode("ascii")
        assert headers.get("Authorization") == expected_auth
        try:
            yield ArtifactHttpResponse(
                status_code=200,
                headers={
                    "Content-Length": str(len(nexus_bytes)),
                    "Content-Encoding": "identity",
                },
                chunks=iter((nexus_bytes[:9], nexus_bytes[9:])),
            )
        finally:
            self.responses_closed += 1

    def close(self):
        self.transport_closed += 1

nexus_transport = FakeArtifactHttp()
nexus_download_directory = Path("nexus-download")
nexus_download_directory.mkdir()
nexus_destination = nexus_download_directory / "widget.zip"
with NexusRawArtifacts(nexus_config, transport=nexus_transport) as nexus:
    nexus_info = nexus.retrieve(
        nexus_key, nexus_destination, expected_sha256=nexus_digest
    )
assert nexus_info.sha256 == nexus_digest
assert nexus_destination.read_bytes() == nexus_bytes
assert nexus_transport.responses_closed == 1
assert nexus_transport.transport_closed == 0
assert importlib.util.find_spec("httpx") is None
assert "httpx" not in sys.modules

default_openbao = OpenBaoClient(openbao_config)
assert "httpx" not in sys.modules
try:
    default_openbao.kv_v2.read("secret", "apps/widget", version=2)
except MissingDependencyError as error:
    assert "conductor[openbao]" in str(error)
else:
    raise AssertionError("Default OpenBao I/O must require optional HTTPX")
finally:
    default_openbao.close()
assert "httpx" not in sys.modules

lazy_httpx = HttpxArtifactTransport()
assert importlib.util.find_spec("httpx") is None
lazy_httpx.close()
missing_httpx_directory = Path("missing-httpx-download")
missing_httpx_directory.mkdir()
default_nexus = NexusRawArtifacts(nexus_config)
try:
    default_nexus.retrieve(
        nexus_key,
        missing_httpx_directory / "widget.zip",
        expected_sha256=nexus_digest,
    )
except MissingDependencyError as error:
    assert "conductor[nexus]" in str(error)
else:
    raise AssertionError("Default Nexus I/O must require optional HTTPX")
finally:
    default_nexus.close()
assert importlib.util.find_spec("httpx") is None
assert "httpx" not in sys.modules

class FakeTransport:
    def execute(self, script, *, parameters=None, timeout=None):
        assert parameters == {"Name": "Disposable", "Operation": "Get"}
        return PowerShellResult(output=(' {"state":"Stopped","changed":false} ',))

result = IisClient(transport=FakeTransport()).application_pools.stop("Disposable")
assert result.final_state is ApplicationPoolState.STOPPED
assert result.changed is False

class FakeWindowsTransport:
    def execute(self, script, *, parameters=None, timeout=None):
        assert parameters == {"Name": "ConductorDisposable", "Operation": "Get"}
        return PowerShellResult(
            output=(
                '{"initial_state":"Stopped","final_state":"Stopped",'
                '"changed":false}',
            )
        )

windows = WindowsClient(transport=FakeWindowsTransport(), timeout=60)
assert windows.services.get_state("ConductorDisposable") == WindowsServiceState("Stopped")
assert not any(name == "pypsrp" or name.startswith("pypsrp.") for name in sys.modules)
assert importlib.util.find_spec("pypsrp") is None

transport = PowerShellRemoting(endpoint="https://example.invalid:5986/wsman")
assert not transport.is_open
try:
    transport.open()
except MissingDependencyError:
    pass
else:
    raise AssertionError("Opening remoting must require its optional SDK")

class FakeHttp:
    def request(self, method, url, *, headers, params=None, json_body=None, timeout):
        if "/git/repositories/" in url:
            body = {"id": "repo-id", "name": "repo"}
        elif "/applications/" in url:
            body = {"metadata": {"name": "app"}, "spec": {"project": "delivery"},
                    "status": {"health": {"status": "Healthy"}, "sync": {"status": "Synced"}}}
        else:
            body = {"projectStatus": {"status": "OK"}}
        return HttpResponse(200, json.dumps(body).encode())

    def close(self):
        raise AssertionError("Borrowed transports must not be closed")

fake = FakeHttp()
with AzureDevOpsClient(
    AzureDevOpsConfig("https://example.invalid/org", "project", "TOKEN"), transport=fake
) as ado:
    assert ado.repositories.get("repo").id == "repo-id"
with ArgoCdClient(
    ArgoCdConfig("https://example.invalid", "delivery", "TOKEN"), transport=fake
) as argo:
    assert argo.applications.get("app").health is ApplicationHealth.HEALTHY
with SonarQubeClient(SonarQubeConfig("https://example.invalid", "TOKEN"), transport=fake) as sonar:
    assert sonar.quality_gates.get_status("project").status is QualityGateStatus.OK

with SonarQubeClient(SonarQubeConfig("https://example.invalid", "TOKEN")) as sonar:
    try:
        sonar.projects.get("project")
    except MissingDependencyError:
        pass
    else:
        raise AssertionError("HTTP requests must require their optional dependency")
assert "httpx" not in sys.modules

class FakeSsh:
    def execute(self, arguments, *, input=None, timeout=None):
        assert arguments[0] in ("systemctl", "env")
        return CommandResult(0, "LoadState=loaded\nActiveState=active\n")

    def upload(self, source, destination, *, dry_run=False, timeout=None):
        raise AssertionError("No file upload is part of this API smoke check")

linux = LinuxClient(transport=FakeSsh())
assert linux.services.get_state("legacy") is ServiceState.ACTIVE
assert linux.compose.up(ComposeProject("/app/compose.yaml", "app")).operation == "up"
try:
    linux.compose.set_image(ComposeProject("/app/compose.yaml", "app"), "api", "app:new")
except MissingDependencyError:
    pass
else:
    raise AssertionError("Compose image editing must require its optional YAML parser")
assert "ruamel.yaml" not in sys.modules
assert list(logging.getLogger().handlers) == root_handlers
print(json.dumps({
    "base_import": "ok", "iis_without_sdk": "ok", "windows_without_sdk": "ok",
    "artifact_filesystem": "ok", "nexus_fake_read": "ok",
    "nexus_without_httpx": "ok", "fake_operation": "ok",
    "openbao_fake_read": "ok", "openbao_without_httpx": "ok",
    "http_without_sdk": "ok", "linux_without_sdk": "ok"
}))
"""
    completed = subprocess.run(
        [str(python), "-I", "-c", consumer],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(completed.stdout) == {
        "base_import": "ok",
        "iis_without_sdk": "ok",
        "windows_without_sdk": "ok",
        "artifact_filesystem": "ok",
        "nexus_fake_read": "ok",
        "nexus_without_httpx": "ok",
        "fake_operation": "ok",
        "openbao_fake_read": "ok",
        "openbao_without_httpx": "ok",
        "http_without_sdk": "ok",
        "linux_without_sdk": "ok",
    }
