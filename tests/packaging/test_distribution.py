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
        for integration in ("azure_devops", "argocd", "sonarqube"):
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
    }
    assert all("extra ==" in requirement for requirement in metadata.get_all("Requires-Dist"))
    with tarfile.open(sdist) as archive:
        names = archive.getnames()
    assert any(name.endswith("/LIBRARY_DESIGN.md") for name in names)
    assert any(name.endswith("/tests/unit/test_application_pools.py") for name in names)


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
import importlib.metadata
import importlib.util
import json
import logging
import sys

root_handlers = list(logging.getLogger().handlers)
assert importlib.util.find_spec("pypsrp") is None
assert importlib.util.find_spec("httpx") is None
import conductor
from conductor.argocd import ArgoCdClient, ArgoCdConfig, ApplicationHealth
from conductor.azure_devops import AzureDevOpsClient, AzureDevOpsConfig
from conductor.common.errors import MissingDependencyError
from conductor.common.http import HttpResponse
from conductor.iis import ApplicationPoolState, IisClient
from conductor.sonarqube import SonarQubeClient, SonarQubeConfig, QualityGateStatus
from conductor.transports import PowerShellRemoting, PowerShellResult

assert conductor.__version__ == importlib.metadata.version("conductor") == "0.1.0"
assert list(logging.getLogger().handlers) == root_handlers
assert not any(name == "pypsrp" or name.startswith("pypsrp.") for name in sys.modules)
assert not any(
    name in sys.modules for name in ("fastapi", "mcp", "kubernetes", "requests", "httpx")
)

class FakeTransport:
    def execute(self, script, *, parameters=None, timeout=None):
        assert parameters == {"Name": "Disposable", "Operation": "Get"}
        return PowerShellResult(output=(' {"state":"Stopped","changed":false} ',))

result = IisClient(transport=FakeTransport()).application_pools.stop("Disposable")
assert result.final_state is ApplicationPoolState.STOPPED
assert result.changed is False
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
print(json.dumps({
    "base_import": "ok", "iis_without_sdk": "ok", "fake_operation": "ok",
    "http_without_sdk": "ok"
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
        "fake_operation": "ok",
        "http_without_sdk": "ok",
    }
