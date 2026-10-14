import hashlib
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from maestro.artifacts import (
    ArtifactFileError,
    ArtifactInfo,
    ArtifactIntegrityError,
    FilesystemArtifacts,
    _files,
)
from maestro.artifacts._files import CHUNK_SIZE, file_chunks, snapshot_file, write_file
from maestro.common.errors import (
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    HttpResponseError,
    PermissionDeniedError,
    TransportTimeoutError,
)


def test_publish_and_retrieve_preserve_bytes_and_return_metadata(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    (root / "release").mkdir()
    source = tmp_path / "source.bin"
    data = b"artifact\x00bytes" * 211
    source.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()

    published = FilesystemArtifacts(root).publish(
        source, "release/build.bin", expected_sha256=expected
    )

    assert published.key == "release/build.bin"
    assert published.size == len(data)
    assert published.sha256 == expected
    assert (root / "release" / "build.bin").read_bytes() == data

    destination = tmp_path / "retrieved.bin"
    retrieved = FilesystemArtifacts(root).retrieve(
        published.key, destination, expected_sha256=expected
    )
    assert destination.read_bytes() == data
    assert retrieved == published


def test_mismatched_hash_preserves_existing_file_and_cleans_staging(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    target = root / "artifact.bin"
    target.write_bytes(b"old")
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")

    with pytest.raises(ArtifactIntegrityError):
        FilesystemArtifacts(root).publish(
            source, "artifact.bin", expected_sha256="0" * 64, overwrite=True
        )

    assert target.read_bytes() == b"old"
    assert _staging_files(root) == []


def test_retrieve_mismatch_preserves_existing_destination(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    (root / "artifact.bin").write_bytes(b"published")
    destination = tmp_path / "destination.bin"
    destination.write_bytes(b"old destination")

    with pytest.raises(ArtifactIntegrityError):
        FilesystemArtifacts(root).retrieve(
            "artifact.bin", destination, expected_sha256="0" * 64, overwrite=True
        )

    assert destination.read_bytes() == b"old destination"
    assert _staging_files(tmp_path) == []


def test_no_overwrite_conflicts_and_overwrite_replaces_file(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    target = root / "artifact.bin"
    target.write_bytes(b"old")
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")

    with pytest.raises(ConflictError):
        FilesystemArtifacts(root).publish(source, "artifact.bin")
    assert target.read_bytes() == b"old"

    result = FilesystemArtifacts(root).publish(source, "artifact.bin", overwrite=True)
    assert result.size == 3
    assert target.read_bytes() == b"new"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only hard-link no-clobber primitive")
def test_atomic_no_overwrite_rejects_a_destination_created_at_commit(tmp_path, monkeypatch):
    destination = tmp_path / "result.bin"
    original_link = os.link

    def racing_link(temporary, target, *args, **kwargs):
        destination.write_bytes(b"racing writer")
        return original_link(temporary, target, *args, **kwargs)

    monkeypatch.setattr(os, "link", racing_link)
    with pytest.raises(ConflictError):
        write_file([b"staged bytes"], destination, "race.bin")

    assert destination.read_bytes() == b"racing writer"
    assert _staging_files(tmp_path) == []


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/leading",
        "trailing/",
        "a//b",
        "../escape",
        "a/./b",
        "a/../b",
        r"a\b",
        "a%2fb",
        "query?value",
        "fragment#value",
        "drive:C",
        "CON",
        "con.txt",
        "Lpt9.data",
        "trailing.",
        "white space",
        "é",
        None,
        5,
    ],
)
def test_invalid_keys_fail_before_root_io(tmp_path, key):
    root = tmp_path / "missing-root"

    with pytest.raises(ConfigurationError):
        FilesystemArtifacts(root).publish(tmp_path / "missing-source", key)

    assert not root.exists()


@pytest.mark.parametrize("digest", ["", "0" * 63, "g" * 64, None, 42, True])
def test_invalid_expected_hash_fails_before_filesystem_io(tmp_path, digest):
    root = tmp_path / "missing-root"

    with pytest.raises(ConfigurationError):
        FilesystemArtifacts(root).retrieve(
            "artifact.bin", tmp_path / "destination", expected_sha256=digest
        )

    assert not root.exists()
    assert not (tmp_path / "destination").exists()


@pytest.mark.parametrize("overwrite", [None, 0, 1, "yes"])
def test_invalid_overwrite_type_fails_before_filesystem_io(tmp_path, overwrite):
    root = tmp_path / "missing-root"

    with pytest.raises(ConfigurationError):
        FilesystemArtifacts(root).publish(
            tmp_path / "missing-source", "artifact.bin", overwrite=overwrite
        )

    assert not root.exists()


def test_invalid_local_path_types_fail_before_filesystem_io(tmp_path):
    root = tmp_path / "missing-root"

    with pytest.raises(ConfigurationError):
        FilesystemArtifacts(root).publish(b"not a path", "valid.bin")
    with pytest.raises(ConfigurationError):
        FilesystemArtifacts(root).retrieve("valid.bin", None, expected_sha256="0" * 64)
    with pytest.raises(ConfigurationError):
        FilesystemArtifacts(None)

    assert not root.exists()


def test_symlink_source_and_key_parent_are_rejected(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    actual = tmp_path / "actual.bin"
    actual.write_bytes(b"data")
    source_link = tmp_path / "source-link"
    parent_link = root / "linked-dir"
    try:
        source_link.symlink_to(actual)
        parent_link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable for this test user/platform")

    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).publish(source_link, "source-link.bin")
    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).publish(actual, "linked-dir/escaped.bin")

    assert not (tmp_path / "escaped.bin").exists()


def test_symlink_destination_is_never_followed_even_for_overwrite(tmp_path):
    root = tmp_path / "share"
    root.mkdir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"new")
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"important")
    destination = tmp_path / "destination.bin"
    try:
        destination.symlink_to(victim)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable for this test user/platform")

    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).retrieve(
            "missing.bin", destination, expected_sha256="0" * 64, overwrite=True
        )

    assert victim.read_bytes() == b"important"
    assert destination.is_symlink()


def test_windows_reparse_point_ancestor_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    original_lstat = Path.lstat

    def reparse_root(path):
        if path == root:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_file_attributes=0x0400)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", reparse_root)
    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).publish(source, "artifact.bin")


def test_missing_root_parents_sources_and_nonregular_endpoints_fail(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    store = FilesystemArtifacts(tmp_path / "absent-root")
    with pytest.raises(ArtifactFileError):
        store.publish(source, "artifact.bin")

    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).publish(source, "missing-parent/artifact.bin")
    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).publish(tmp_path / "absent-source", "artifact.bin")
    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).publish(tmp_path, "directory-source.bin")

    destination_directory = tmp_path / "destination-directory"
    destination_directory.mkdir()
    (root / "artifact.bin").write_bytes(b"data")
    digest = hashlib.sha256(b"data").hexdigest()
    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).retrieve(
            "artifact.bin",
            tmp_path / "missing-destination-parent" / "artifact.bin",
            expected_sha256=digest,
        )
    assert not (tmp_path / "missing-destination-parent").exists()

    with pytest.raises(ArtifactFileError):
        FilesystemArtifacts(root).retrieve(
            "artifact.bin",
            destination_directory,
            expected_sha256=digest,
        )


def test_source_reads_are_bounded_to_the_shared_chunk_size(tmp_path, monkeypatch):
    source = tmp_path / "large.bin"
    source.write_bytes(b"x" * (CHUNK_SIZE * 2 + 19))
    sizes = []
    original_open = Path.open

    class ReadSpy:
        def __init__(self, stream):
            self._stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._stream.__exit__(*args)

        def read(self, size=-1):
            sizes.append(size)
            return self._stream.read(size)

        def fileno(self):
            return self._stream.fileno()

    def open_spy(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return ReadSpy(stream) if path == source else stream

    monkeypatch.setattr(Path, "open", open_spy)
    assert sum(len(chunk) for chunk in file_chunks(source)) == source.stat().st_size
    assert sizes
    assert all(0 < size <= CHUNK_SIZE for size in sizes)


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only hard-link no-clobber primitive")
def test_unsupported_no_clobber_primitive_fails_closed_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "destination.bin"

    def unsupported(*args, **kwargs):
        raise OSError("secret path and server details")

    monkeypatch.setattr(os, "link", unsupported)
    with pytest.raises(ArtifactFileError) as error:
        write_file([b"content"], destination, "artifact.bin")

    assert not destination.exists()
    assert _staging_files(tmp_path) == []
    assert "secret path" not in repr(error.value)
    assert "destination.bin" not in repr(error.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only no-clobber rename primitive")
def test_unsupported_windows_rename_fails_closed_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "destination.bin"

    def unsupported(*args, **kwargs):
        raise OSError("secret path and server details")

    monkeypatch.setattr(os, "rename", unsupported)
    with pytest.raises(ArtifactFileError) as error:
        write_file([b"content"], destination, "artifact.bin")

    assert not destination.exists()
    assert _staging_files(tmp_path) == []
    assert "secret path" not in repr(error.value)


@pytest.mark.skipif(os.name != "nt", reason="Windows-only no-clobber rename primitive")
def test_windows_no_overwrite_rejects_destination_created_at_commit(tmp_path, monkeypatch):
    destination = tmp_path / "destination.bin"
    original_rename = os.rename

    def racing_rename(temporary, target, *args, **kwargs):
        destination.write_bytes(b"racing writer")
        return original_rename(temporary, target, *args, **kwargs)

    monkeypatch.setattr(os, "rename", racing_rename)
    with pytest.raises(ConflictError):
        write_file([b"staged bytes"], destination, "race.bin")

    assert destination.read_bytes() == b"racing writer"
    assert _staging_files(tmp_path) == []


def test_partial_stream_failure_cleans_temp_without_exposing_exception(tmp_path):
    destination = tmp_path / "destination.bin"

    def broken_chunks():
        yield b"partial"
        raise OSError("private file contents and /sensitive/path")

    with pytest.raises(ArtifactFileError) as error:
        write_file(broken_chunks(), destination, "artifact.bin")

    assert not destination.exists()
    assert _staging_files(tmp_path) == []
    assert "private file contents" not in repr(error.value)
    assert "/sensitive/path" not in repr(error.value)


def test_permission_errors_are_reported_with_sanitized_common_error(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    root = tmp_path / "root"
    root.mkdir()
    original_open = Path.open

    def denied(path, *args, **kwargs):
        if path == source:
            raise PermissionError("secret user and path")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(PermissionDeniedError) as error:
        FilesystemArtifacts(root).publish(source, "artifact.bin")
    assert "secret user" not in repr(error.value)
    assert str(source) not in repr(error.value)
    assert _staging_files(root) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX-only hard-link no-clobber primitive")
def test_no_clobber_link_success_cleanup_failure_does_not_claim_rollback(tmp_path, monkeypatch):
    destination = tmp_path / "destination.bin"
    original_unlink = Path.unlink

    def denied_staging_unlink(path, *args, **kwargs):
        if path.name.startswith(".maestro-"):
            raise PermissionError("hidden cleanup detail")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_staging_unlink)
    with pytest.raises(ArtifactFileError) as error:
        write_file([b"committed"], destination, "artifact.bin")

    assert destination.read_bytes() == b"committed"
    assert "hidden cleanup detail" not in repr(error.value)
    assert "may be present" in str(error.value)


def test_snapshot_is_independent_after_original_changes_and_cleans_up(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"original bytes")
    digest = hashlib.sha256(b"original bytes").hexdigest()

    with snapshot_file(source, "immutable.bin", expected_sha256=digest) as (snapshot, info):
        source.write_bytes(b"changed after snapshot")
        assert snapshot.read_bytes() == b"original bytes"
        assert info.sha256 == digest
        assert info.size == len(b"original bytes")
        snapshot_directory = snapshot.parent
        assert snapshot.exists()

    assert not snapshot.exists()
    assert not snapshot_directory.exists()


@pytest.mark.parametrize(
    "error",
    [
        TransportTimeoutError("safe test detail"),
        AuthenticationError("safe test detail"),
        HttpResponseError(503),
    ],
)
def test_write_file_preserves_common_errors_from_chunk_iterators(tmp_path, error):
    destination = tmp_path / "destination.bin"

    def chunks():
        yield b"partial"
        raise error

    with pytest.raises(type(error)) as caught:
        write_file(chunks(), destination, "artifact.bin")

    assert caught.value is error
    assert not destination.exists()
    assert _staging_files(tmp_path) == []


@pytest.mark.parametrize(
    "error",
    [
        TransportTimeoutError("safe test detail"),
        KeyboardInterrupt("interrupt"),
        SystemExit("exit"),
    ],
)
def test_write_file_close_failure_preserves_chunk_iterator_error(
    tmp_path, monkeypatch, caplog, error
):
    destination = tmp_path / "destination.bin"
    destination.write_bytes(b"old destination")
    original_open_temp = _files._open_temp
    close_attempted = False

    class CloseFailingStream:
        def __init__(self, stream):
            self._stream = stream

        def write(self, chunk):
            return self._stream.write(chunk)

        def flush(self):
            return self._stream.flush()

        def fileno(self):
            return self._stream.fileno()

        def close(self):
            nonlocal close_attempted
            close_attempted = True
            self._stream.close()
            raise OSError("private close detail")

    def open_temp(parent):
        path, stream = original_open_temp(parent)
        return path, CloseFailingStream(stream)

    monkeypatch.setattr(_files, "_open_temp", open_temp)

    def chunks():
        yield b"partial"
        raise error

    with pytest.raises(type(error)) as caught:
        write_file(chunks(), destination, "artifact.bin", overwrite=True)

    assert caught.value is error
    assert close_attempted
    assert destination.read_bytes() == b"old destination"
    assert _staging_files(tmp_path) == []
    assert caplog.text.count("Artifact temporary-file cleanup failed.") == 1
    assert "private close detail" not in caplog.text
    assert str(error) not in caplog.text


def test_write_file_normal_close_failure_is_sanitized_and_prevents_commit(tmp_path, monkeypatch):
    destination = tmp_path / "destination.bin"
    destination.write_bytes(b"old destination")
    original_open_temp = _files._open_temp
    close_attempted = False

    class CloseFailingStream:
        def __init__(self, stream):
            self._stream = stream

        def write(self, chunk):
            return self._stream.write(chunk)

        def flush(self):
            return self._stream.flush()

        def fileno(self):
            return self._stream.fileno()

        def close(self):
            nonlocal close_attempted
            close_attempted = True
            self._stream.close()
            raise OSError("private close detail")

    def open_temp(parent):
        path, stream = original_open_temp(parent)
        return path, CloseFailingStream(stream)

    monkeypatch.setattr(_files, "_open_temp", open_temp)
    with pytest.raises(ArtifactFileError) as caught:
        write_file([b"new content"], destination, "artifact.bin", overwrite=True)

    assert close_attempted
    assert "private close detail" not in repr(caught.value)
    assert destination.read_bytes() == b"old destination"
    assert _staging_files(tmp_path) == []


@pytest.mark.parametrize(
    "error",
    [
        TransportTimeoutError("safe test detail"),
        AuthenticationError("safe test detail"),
        HttpResponseError(503),
    ],
)
def test_snapshot_context_preserves_common_errors_from_caller(tmp_path, error):
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")

    with pytest.raises(type(error)) as caught, snapshot_file(source, "artifact.bin"):
        raise error

    assert caught.value is error


@pytest.mark.parametrize(
    "error",
    [TransportTimeoutError("safe test detail"), KeyboardInterrupt("interrupt")],
)
def test_write_file_cleanup_failure_preserves_primary_baseexception(
    tmp_path, monkeypatch, caplog, error
):
    destination = tmp_path / "destination.bin"
    original_unlink = Path.unlink

    def failed_cleanup(path, *args, **kwargs):
        if path.name.startswith(".maestro-"):
            raise OSError("private cleanup detail")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failed_cleanup)

    def chunks():
        yield b"partial"
        raise error

    with pytest.raises(type(error)) as caught:
        write_file(chunks(), destination, "artifact.bin")

    assert caught.value is error
    assert caplog.text.count("Artifact temporary-file cleanup failed.") == 1
    assert "private cleanup detail" not in caplog.text
    for path in _staging_files(tmp_path):
        original_unlink(path)


@pytest.mark.parametrize(
    "error",
    [TransportTimeoutError("safe test detail"), KeyboardInterrupt("interrupt")],
)
def test_snapshot_cleanup_failure_preserves_primary_baseexception(
    tmp_path, monkeypatch, caplog, error
):
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    original_unlink = Path.unlink
    original_rmdir = Path.rmdir
    snapshot_directory = None

    def failed_snapshot_cleanup(path, *args, **kwargs):
        if path.name == "snapshot.bin":
            raise OSError("private cleanup detail")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failed_snapshot_cleanup)

    with (
        pytest.raises(type(error)) as caught,
        snapshot_file(source, "artifact.bin") as (snapshot, _),
    ):
        snapshot_directory = snapshot.parent
        raise error

    assert caught.value is error
    assert caplog.text.count("Artifact temporary-file cleanup failed.") == 1
    assert "private cleanup detail" not in caplog.text
    assert snapshot_directory is not None
    original_unlink(snapshot_directory / "snapshot.bin")
    original_rmdir(snapshot_directory)


def test_snapshot_normal_cleanup_failure_surfaces_sanitized_error(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"data")
    original_unlink = Path.unlink
    original_rmdir = Path.rmdir
    snapshot_directory = None

    def failed_snapshot_cleanup(path, *args, **kwargs):
        if path.name == "snapshot.bin":
            raise PermissionError("private path and identity")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failed_snapshot_cleanup)
    with (
        pytest.raises(ArtifactFileError) as caught,
        snapshot_file(source, "artifact.bin") as (snapshot, _),
    ):
        snapshot_directory = snapshot.parent

    assert "private path" not in repr(caught.value)
    assert snapshot_directory is not None
    original_unlink(snapshot_directory / "snapshot.bin")
    original_rmdir(snapshot_directory)


def test_relative_filesystem_root_is_anchored_at_construction(tmp_path, monkeypatch):
    first_cwd = tmp_path / "first-cwd"
    second_cwd = tmp_path / "second-cwd"
    root = first_cwd / "artifacts"
    first_cwd.mkdir()
    second_cwd.mkdir()
    root.mkdir()
    source = tmp_path / "source.bin"
    source.write_bytes(b"anchored")

    monkeypatch.chdir(first_cwd)

    def forbidden_stat(*args, **kwargs):
        raise AssertionError("construction must not inspect the filesystem")

    with monkeypatch.context() as patcher:
        patcher.setattr(Path, "stat", forbidden_stat)
        patcher.setattr(Path, "lstat", forbidden_stat)
        store = FilesystemArtifacts("artifacts")
    monkeypatch.chdir(second_cwd)

    store.publish(source, "artifact.bin")

    assert (root / "artifact.bin").read_bytes() == b"anchored"
    assert not (second_cwd / "artifacts").exists()


@pytest.mark.parametrize("expected_size", [-1, True, 1.5, "3"])
def test_invalid_expected_size_fails_before_destination_io(tmp_path, expected_size):
    destination = tmp_path / "missing-parent" / "artifact.bin"
    with pytest.raises(ConfigurationError):
        write_file([b"data"], destination, "artifact.bin", expected_size=expected_size)
    assert not destination.parent.exists()


def test_artifact_models_are_immutable():
    from dataclasses import FrozenInstanceError

    info = ArtifactInfo("artifact.bin", 4, hashlib.sha256(b"data").hexdigest())
    with pytest.raises(FrozenInstanceError):
        info.size = 5


def _staging_files(directory):
    if not directory.exists():
        return []
    return list(directory.rglob(".maestro-*.tmp"))
