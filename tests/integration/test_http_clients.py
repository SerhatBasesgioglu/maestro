"""Read-only live checks. No repository, pipeline, sync, or project mutations."""

import json
from pathlib import Path

import pytest

from maestro.argocd import ArgoCdClient, ArgoCdConfig
from maestro.azure_devops import AzureDevOpsClient, AzureDevOpsConfig
from maestro.sonarqube import SonarQubeClient, SonarQubeConfig

pytestmark = [pytest.mark.integration, pytest.mark.http_integration]


@pytest.fixture(scope="module")
def target_config(request):
    path = request.config.getoption("--http-integration-config")
    if not path or not request.config.getoption("--http-confirm-nonproduction"):
        pytest.fail(
            "HTTP live tests require an explicit config file and --http-confirm-nonproduction."
        )
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pytest.fail("The caller-supplied HTTP integration config could not be read.", pytrace=False)
    if not isinstance(data, dict):
        pytest.fail("HTTP integration config must be an object.", pytrace=False)
    return data


def section(target_config, name):
    value = target_config.get(name)
    if value is None:
        pytest.skip("No explicit target was configured for this integration.")
    if not isinstance(value, dict) or not isinstance(value.get("config"), dict):
        pytest.fail("Each integration needs an explicit config object.", pytrace=False)
    return value


def test_azure_devops_read_only(target_config):
    target = section(target_config, "azure_devops")
    repository = target.get("repository")
    if not isinstance(repository, str) or not repository:
        pytest.fail("Azure DevOps live checks require an explicit repository.")
    with AzureDevOpsClient(AzureDevOpsConfig(**target["config"])) as ado:
        assert ado.repositories.get(repository).id


def test_argocd_read_only(target_config):
    target = section(target_config, "argocd")
    name = target.get("application")
    if not isinstance(name, str) or not name:
        pytest.fail("Argo CD live checks require an explicit application.")
    with ArgoCdClient(ArgoCdConfig(**target["config"])) as argo:
        assert argo.applications.get(name).name == name


def test_sonarqube_read_only(target_config):
    target = section(target_config, "sonarqube")
    key = target.get("project_key")
    if not isinstance(key, str) or not key:
        pytest.fail("SonarQube live checks require an explicit project key.")
    with SonarQubeClient(SonarQubeConfig(**target["config"])) as sonar:
        assert sonar.projects.get(key).key == key
        assert sonar.quality_gates.get_status(key).project_key == key
