"""Single-file artifact storage on an explicitly selected local filesystem path."""

from pathlib import Path

from conductor.artifacts._files import file_chunks, write_file
from conductor.artifacts._validation import validate_key, validate_sha256
from conductor.artifacts.models import ArtifactInfo
from conductor.common.errors import ConfigurationError


class FilesystemArtifacts:
    """Publish and retrieve files beneath an already-existing trusted directory.

    The root and each key parent must already exist. This class does not discover or
    mount shares, provision permissions, or make hostile concurrent directory
    substitution safe. Filesystem operations can block according to the mounted
    filesystem; no application-level timeout is implied. File permissions and share
    ACLs remain the caller's policy: staging uses filesystem defaults and inherited
    ACLs, subject to the process umask. Replacing a file does not preserve its previous
    mode or ACL, and staging does not silently chmod artifacts. Same-directory commit
    primitives are used when available, but universal server-side atomicity or crash
    durability is not guaranteed.
    """

    def __init__(self, root: str | Path) -> None:
        if not isinstance(root, (str, Path)) or not str(root) or "\x00" in str(root):
            raise ConfigurationError("root must be a valid local directory path.")
        if any(part == ".." for part in Path(root).parts):
            raise ConfigurationError("root must not contain parent traversal.")
        try:
            self._root = Path(root).absolute()
        except (OSError, ValueError):
            raise ConfigurationError("root must be a valid local directory path.") from None

    def publish(
        self,
        source: str | Path,
        key: str,
        *,
        expected_sha256: str | None = None,
        overwrite: bool = False,
    ) -> ArtifactInfo:
        """Stream and verify one local regular file into the selected artifact root."""
        key = validate_key(key)
        digest = validate_sha256(expected_sha256) if expected_sha256 is not None else None
        _validate_overwrite(overwrite)
        _validate_path_input(source, "source")
        target = self._target(key)
        return write_file(
            file_chunks(Path(source)),
            target,
            key,
            expected_sha256=digest,
            overwrite=overwrite,
        )

    def retrieve(
        self,
        key: str,
        destination: str | Path,
        *,
        expected_sha256: str,
        overwrite: bool = False,
    ) -> ArtifactInfo:
        """Stream and verify one artifact into an explicit local destination file."""
        key = validate_key(key)
        digest = validate_sha256(expected_sha256)
        _validate_overwrite(overwrite)
        _validate_path_input(destination, "destination")
        source = self._target(key)
        return write_file(
            file_chunks(source),
            destination,
            key,
            expected_sha256=digest,
            overwrite=overwrite,
        )

    def _target(self, key: str) -> Path:
        root = self._root
        target = root.joinpath(*key.split("/"))
        try:
            target.relative_to(root)
        except ValueError:
            raise ConfigurationError(
                "artifact key must remain beneath the configured root."
            ) from None
        return target


def _validate_path_input(value: str | Path, name: str) -> None:
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise ConfigurationError(f"{name} must be a valid local path.")
    if any(part == ".." for part in Path(value).parts):
        raise ConfigurationError(f"{name} must not contain parent traversal.")


def _validate_overwrite(overwrite: bool) -> None:
    if not isinstance(overwrite, bool):
        raise ConfigurationError("overwrite must be a boolean.")
