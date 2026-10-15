#!/usr/bin/env python3
"""Validate project release metadata without importing the package or contacting GitHub."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import TypeAlias

ReleaseResult: TypeAlias = dict[str, str | bool]

_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?",
    re.ASCII,
)
_PRERELEASE_PATTERN = re.compile(r"(?:a|b|rc)(?:0|[1-9][0-9]*)\Z", re.ASCII)


class ReleaseValidationError(ValueError):
    """Raised when the repository's release metadata is invalid."""


def _check_version(value: object, source: str) -> str:
    if not isinstance(value, str):
        raise ReleaseValidationError(f"{source} must be a string")
    if _VERSION_PATTERN.fullmatch(value) is None:
        raise ReleaseValidationError(
            f"{source} must be a canonical MAJOR.MINOR.PATCH version "
            "with an optional aN, bN, or rcN prerelease suffix"
        )
    return value


def _read_project_version(root: Path) -> str:
    project_file = root / "pyproject.toml"
    try:
        with project_file.open("rb") as stream:
            metadata = tomllib.load(stream)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise ReleaseValidationError(f"cannot read {project_file}: {error}") from error

    project = metadata.get("project")
    if not isinstance(project, dict) or "version" not in project:
        raise ReleaseValidationError("pyproject.toml must define a static [project].version")
    return _check_version(project["version"], "[project].version")


def _version_targets(target: ast.expr) -> bool:
    if isinstance(target, ast.Name):
        return target.id == "__version__"
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(_version_targets(element) for element in target.elts)
    return False


def _read_runtime_version(root: Path) -> str:
    version_file = root / "src" / "maestro" / "__init__.py"
    try:
        source = version_file.read_text(encoding="utf-8")
        module = ast.parse(source, filename=str(version_file))
    except (OSError, UnicodeError, SyntaxError) as error:
        raise ReleaseValidationError(
            f"cannot read runtime version from {version_file}: {error}"
        ) from error

    assignments: list[ast.expr] = []
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            if any(_version_targets(target) for target in statement.targets):
                if len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                    assignments.append(statement.value)
                else:
                    assignments.append(ast.Name(id="<non-literal assignment>"))
        elif isinstance(statement, ast.AnnAssign) and _version_targets(statement.target):
            assignments.append(statement.value or ast.Name(id="<missing value>"))

    if not assignments:
        raise ReleaseValidationError(
            "src/maestro/__init__.py must define __version__ as a string literal"
        )
    if len(assignments) != 1:
        raise ReleaseValidationError("src/maestro/__init__.py must define __version__ exactly once")
    value = assignments[0]
    if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
        raise ReleaseValidationError("src/maestro/__init__.py __version__ must be a string literal")
    return _check_version(value.value, "src/maestro/__init__.py __version__")


def validate_release(root: Path, tag: str | None = None) -> ReleaseResult:
    """Validate static package versions and return the release data for a workflow."""
    project_version = _read_project_version(Path(root))
    runtime_version = _read_runtime_version(Path(root))
    if runtime_version != project_version:
        raise ReleaseValidationError(
            f"runtime __version__ {runtime_version!r} does not match "
            f"[project].version {project_version!r}"
        )

    expected_tag = f"v{project_version}"
    if tag is not None and tag != expected_tag:
        raise ReleaseValidationError(f"tag must be exactly {expected_tag!r}, got {tag!r}")

    return {
        "version": project_version,
        "tag": expected_tag,
        "prerelease": _PRERELEASE_PATTERN.search(project_version) is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (defaults to the parent of this script's directory)",
    )
    parser.add_argument("--tag", help="require this exact v-prefixed release tag")
    arguments = parser.parse_args(argv)

    try:
        result = validate_release(arguments.root, arguments.tag)
    except ReleaseValidationError as error:
        print(f"release validation failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
