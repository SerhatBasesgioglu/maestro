"""Bounded, transactional local-file helpers shared by artifact transports."""

import hashlib
import logging
import os
import secrets
import stat
import sys
import tempfile
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager, suppress
from pathlib import Path
from typing import BinaryIO

from conductor.artifacts._validation import validate_key, validate_sha256
from conductor.artifacts.errors import ArtifactFileError, ArtifactIntegrityError
from conductor.artifacts.models import ArtifactInfo
from conductor.common.errors import (
    ConductorError,
    ConfigurationError,
    ConflictError,
    PermissionDeniedError,
)

CHUNK_SIZE = 1024 * 1024
_REPARSE_POINT = 0x0400
_LOGGER = logging.getLogger(__name__)


def file_chunks(source: Path) -> Iterator[bytes]:
    """Yield a regular local file in fixed-size chunks, sanitizing local I/O errors."""
    _validate_path_argument(source, "source")
    path = _absolute_path(source, "source")
    _validate_existing_path(path, require_directory=False, must_exist=True)
    try:
        with path.open("rb") as stream:
            _validate_open_file(stream, path)
            while True:
                chunk = stream.read(CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    except (ArtifactFileError, PermissionDeniedError):
        raise
    except PermissionError:
        raise PermissionDeniedError("Access to artifact files was denied.") from None
    except (OSError, ValueError):
        raise ArtifactFileError() from None
    except Exception:
        raise ArtifactFileError() from None


def snapshot_file(
    source: str | Path,
    key: str,
    *,
    expected_sha256: str | None = None,
) -> AbstractContextManager[tuple[Path, ArtifactInfo]]:
    """Create a private immutable disk snapshot and keep it alive for the context."""
    canonical_key = validate_key(key)
    digest = validate_sha256(expected_sha256) if expected_sha256 is not None else None
    _validate_path_argument(source, "source")
    return _snapshot_context(Path(source), canonical_key, digest)


@contextmanager
def _snapshot_context(
    source: Path, key: str, expected_sha256: str | None
) -> Iterator[tuple[Path, ArtifactInfo]]:
    directory: str | None = None
    try:
        try:
            temporary_root = Path(tempfile.gettempdir()).resolve(strict=True)
            directory = tempfile.mkdtemp(prefix="conductor-artifact-", dir=temporary_root)
            snapshot = Path(directory) / "snapshot.bin"
            info = write_file(
                file_chunks(source),
                snapshot,
                key=key,
                expected_sha256=expected_sha256,
                overwrite=False,
            )
        except ConductorError:
            raise
        except PermissionError:
            raise PermissionDeniedError("Access to artifact files was denied.") from None
        except OSError:
            raise ArtifactFileError() from None
        except Exception:
            raise ArtifactFileError() from None
        yield snapshot, info
    finally:
        if directory is not None:
            _cleanup_snapshot(directory, sys.exc_info()[1])


def _cleanup_file(path: Path, primary_error: BaseException | None) -> None:
    try:
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        return
    except BaseException:
        _report_cleanup_failure(primary_error)


def _cleanup_snapshot(directory: str, primary_error: BaseException | None) -> None:
    failed = False
    try:
        (Path(directory) / "snapshot.bin").unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except BaseException:
        failed = True
    try:
        Path(directory).rmdir()
    except FileNotFoundError:
        pass
    except BaseException:
        failed = True
    if failed:
        _report_cleanup_failure(primary_error)


def _report_cleanup_failure(primary_error: BaseException | None) -> None:
    if primary_error is not None:
        with suppress(BaseException):
            _LOGGER.warning("Artifact temporary-file cleanup failed.")
        return
    raise ArtifactFileError() from None


def write_file(
    chunks: Iterable[bytes],
    destination: str | Path,
    key: str,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    overwrite: bool = False,
) -> ArtifactInfo:
    """Write, hash, verify, and atomically commit a bounded artifact stream."""
    canonical_key = validate_key(key)
    digest = validate_sha256(expected_sha256) if expected_sha256 is not None else None
    _validate_overwrite(overwrite)
    _validate_expected_size(expected_size)
    _validate_path_argument(destination, "destination")
    target = _absolute_path(Path(destination), "destination")
    parent = target.parent
    _validate_existing_path(parent, require_directory=True, must_exist=True)
    _validate_target(target, overwrite=overwrite)

    temporary: Path | None = None
    committed = False
    try:
        temporary, stream = _open_temp(parent)
        primary_error: BaseException | None = None
        try:
            hasher = hashlib.sha256()
            size = 0
            try:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ArtifactFileError()
                    stream.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            except ConductorError:
                raise
            except PermissionError:
                raise PermissionDeniedError("Access to artifact files was denied.") from None
            except OSError:
                raise ArtifactFileError() from None
            except Exception:
                raise ArtifactFileError() from None
        except BaseException as error:
            primary_error = error
            raise
        finally:
            try:
                stream.close()
            except BaseException:
                if primary_error is None:
                    raise ArtifactFileError() from None
                _report_cleanup_failure(primary_error)

        actual_sha256 = hasher.hexdigest()
        if (digest is not None and actual_sha256 != digest) or (
            expected_size is not None and size != expected_size
        ):
            raise ArtifactIntegrityError()

        _validate_existing_path(parent, require_directory=True, must_exist=True)
        _validate_target(target, overwrite=overwrite)
        try:
            if overwrite:
                os.replace(temporary, target)
                temporary = None
            elif os.name == "nt":
                # Windows rename refuses to replace an existing destination.
                os.rename(temporary, target)
                temporary = None
            else:
                # A same-directory hard link is atomic and does not clobber.
                os.link(temporary, target)
                committed = True
                temporary.unlink()
                temporary = None
            committed = True
        except FileExistsError:
            raise ConflictError("Artifact destination already exists.") from None
        except PermissionError:
            if committed:
                raise ArtifactFileError() from None
            raise PermissionDeniedError("Access to artifact files was denied.") from None
        except OSError:
            raise ArtifactFileError() from None
        except Exception:
            raise ArtifactFileError() from None

        return ArtifactInfo(canonical_key, size, actual_sha256)
    except ConductorError:
        raise
    except PermissionError:
        raise PermissionDeniedError("Access to artifact files was denied.") from None
    except (OSError, ValueError):
        raise ArtifactFileError() from None
    finally:
        if temporary is not None:
            _cleanup_file(temporary, sys.exc_info()[1])


def _open_temp(parent: Path) -> tuple[Path, BinaryIO]:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    for _ in range(10):
        path = parent / f".conductor-{secrets.token_hex(16)}.tmp"
        try:
            descriptor = os.open(path, flags, 0o666)
        except FileExistsError:
            continue
        except PermissionError:
            raise PermissionDeniedError("Access to artifact files was denied.") from None
        except OSError:
            raise ArtifactFileError() from None
        try:
            return path, os.fdopen(descriptor, "wb")
        except OSError:
            with suppress(OSError):
                os.close(descriptor)
            with suppress(OSError):
                path.unlink()
            raise ArtifactFileError() from None
    raise ArtifactFileError()


def _validate_target(target: Path, *, overwrite: bool) -> None:
    try:
        mode = _lstat_mode(target)
    except FileNotFoundError:
        return
    if mode is None:
        return
    if not stat.S_ISREG(mode):
        raise ArtifactFileError()
    if not overwrite:
        raise ConflictError("Artifact destination already exists.")


def _validate_path_argument(value: str | Path, name: str) -> None:
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise ConfigurationError(f"{name} must be a valid local path.")
    if any(part == ".." for part in Path(value).parts):
        raise ConfigurationError(f"{name} must not contain parent traversal.")


def _absolute_path(path: Path, name: str) -> Path:
    try:
        return path.absolute()
    except (OSError, ValueError):
        raise ConfigurationError(f"{name} must be a valid local path.") from None


def _validate_existing_path(path: Path, *, require_directory: bool, must_exist: bool) -> None:
    """Walk with lstat so no symlink or Windows reparse point is silently followed."""
    for item in (*reversed(path.parents), path):
        try:
            mode = _lstat_mode(item)
        except FileNotFoundError:
            if must_exist:
                raise ArtifactFileError() from None
            return
        if mode is None:
            if must_exist:
                raise ArtifactFileError()
            return
        if item == path and not require_directory:
            if not stat.S_ISREG(mode):
                raise ArtifactFileError()
        elif not stat.S_ISDIR(mode):
            raise ArtifactFileError()


def _lstat_mode(path: Path) -> int | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except PermissionError:
        raise PermissionDeniedError("Access to artifact files was denied.") from None
    except OSError:
        raise ArtifactFileError() from None
    except ValueError:
        raise ArtifactFileError() from None
    attributes = getattr(metadata, "st_file_attributes", 0)
    if stat.S_ISLNK(metadata.st_mode) or attributes & _REPARSE_POINT:
        raise ArtifactFileError()
    return metadata.st_mode


def _validate_open_file(stream: BinaryIO, path: Path) -> None:
    try:
        metadata = os.fstat(stream.fileno())
        entry = path.lstat()
    except PermissionError:
        raise PermissionDeniedError("Access to artifact files was denied.") from None
    except (OSError, ValueError):
        raise ArtifactFileError() from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(entry.st_mode)
        or getattr(entry, "st_file_attributes", 0) & _REPARSE_POINT
        or not stat.S_ISREG(entry.st_mode)
        or (metadata.st_dev, metadata.st_ino) != (entry.st_dev, entry.st_ino)
    ):
        raise ArtifactFileError()


def _validate_overwrite(overwrite: bool) -> None:
    if not isinstance(overwrite, bool):
        raise ConfigurationError("overwrite must be a boolean.")


def _validate_expected_size(expected_size: int | None) -> None:
    if expected_size is not None and (
        isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0
    ):
        raise ConfigurationError("expected_size must be a non-negative integer.")
