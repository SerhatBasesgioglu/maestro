import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.packaging
ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_release.py"
PROJECT_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
    "version"
]

SPEC = importlib.util.spec_from_file_location("release_validator", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release_validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_validator)


def _root(tmp_path, project_version="1.2.3", runtime_version="1.2.3"):
    (tmp_path / "src" / "maestro").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        f'[project]\nname = "maestro"\nversion = {json.dumps(project_version)}\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "maestro" / "__init__.py").write_text(
        f"__version__ = {json.dumps(runtime_version)}\n",
        encoding="utf-8",
    )
    return tmp_path


def test_current_repository_versions_match_and_generate_release_data():
    result = release_validator.validate_release(ROOT)

    assert result["version"] == PROJECT_VERSION
    assert result["tag"] == f"v{PROJECT_VERSION}"
    assert result["prerelease"] is (
        re.search(r"(?:a|b|rc)(?:0|[1-9][0-9]*)$", PROJECT_VERSION) is not None
    )


@pytest.mark.parametrize("version", ["0.1.0", "1.2.4", "1.3.0", "2.0.0"])
def test_future_patch_minor_and_major_releases(tmp_path, version):
    result = release_validator.validate_release(_root(tmp_path, version, version))

    assert result == {"version": version, "tag": f"v{version}", "prerelease": False}


@pytest.mark.parametrize("version", ["1.2.3a1", "1.2.3b2", "1.2.3rc3"])
def test_prereleases_are_marked(tmp_path, version):
    result = release_validator.validate_release(_root(tmp_path, version, version))

    assert result == {"version": version, "tag": f"v{version}", "prerelease": True}


def test_project_and_runtime_versions_must_match(tmp_path):
    with pytest.raises(release_validator.ReleaseValidationError, match="does not match"):
        release_validator.validate_release(_root(tmp_path, "1.2.3", "1.2.4"))


def test_project_version_must_be_a_string(tmp_path):
    root = _root(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "maestro"\nversion = 123\n', encoding="utf-8"
    )

    with pytest.raises(release_validator.ReleaseValidationError, match="must be a string"):
        release_validator.validate_release(root)


def test_static_project_version_is_required(tmp_path):
    root = _root(tmp_path)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "maestro"\ndynamic = ["version"]\n', encoding="utf-8"
    )

    with pytest.raises(release_validator.ReleaseValidationError, match="static "):
        release_validator.validate_release(root)


def test_runtime_version_must_be_a_string_literal(tmp_path):
    root = _root(tmp_path)
    (root / "src" / "maestro" / "__init__.py").write_text(
        "__version__ = get_version()\n", encoding="utf-8"
    )

    with pytest.raises(release_validator.ReleaseValidationError, match="string literal"):
        release_validator.validate_release(root)


def test_missing_runtime_version_is_rejected(tmp_path):
    root = _root(tmp_path)
    (root / "src" / "maestro" / "__init__.py").write_text("", encoding="utf-8")

    with pytest.raises(release_validator.ReleaseValidationError, match="must define __version__"):
        release_validator.validate_release(root)


@pytest.mark.parametrize(
    "version",
    [
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3.post1",
        "1.2.3.dev1",
        "1.2.3+local",
        "1.2.3\n",
        '1.2.3\n[tool.injected]\nvalue = "bad"',
    ],
)
def test_noncanonical_or_injected_versions_are_rejected(tmp_path, version):
    with pytest.raises(release_validator.ReleaseValidationError, match="canonical"):
        release_validator.validate_release(_root(tmp_path, version, version))


@pytest.mark.parametrize("tag", ["0.1.0", "v1.2.3", "v0.1.0\n", "v1.2.3; echo injected"])
def test_tag_must_exactly_match_project_version(tmp_path, tag):
    root = _root(tmp_path, "0.1.0", "0.1.0")

    with pytest.raises(release_validator.ReleaseValidationError, match="tag must be exactly"):
        release_validator.validate_release(root, tag)


@pytest.mark.parametrize("tag_args", [[], ["--tag", "v1.2.3rc1"]])
def test_cli_emits_json_for_valid_release(tmp_path, tag_args):
    root = _root(tmp_path, "1.2.3rc1", "1.2.3rc1")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *tag_args],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "version": "1.2.3rc1",
        "tag": "v1.2.3rc1",
        "prerelease": True,
    }
    assert completed.stderr == ""


def test_cli_errors_go_to_stderr_without_json(tmp_path):
    root = _root(tmp_path, "1.2.3", "1.2.4")
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "release validation failed:" in completed.stderr
