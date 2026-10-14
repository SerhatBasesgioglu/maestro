"""Execute trusted remote-helper source against temp files, never a network target."""

import json
import os
import stat
import subprocess
import sys

import pytest

from maestro.linux._scripts import COMPOSE_FILE

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Remote helper uses POSIX file APIs.")


def run_helper(action, path, fingerprint=None, content=None):
    # Python exposes xattr APIs only on Linux. On other POSIX callers this seam
    # covers ordinary files; the real xattr check below is explicitly skipped.
    script = COMPOSE_FILE
    if not hasattr(os, "listxattr"):
        script = "import os\nos.listxattr = lambda *args, **kwargs: []\n" + script
    arguments = [sys.executable, "-c", script, action, str(path)]
    if fingerprint is not None:
        arguments.append(fingerprint)
    return subprocess.run(arguments, input=content, capture_output=True, timeout=5, check=False)


def test_helper_reads_and_atomically_replaces_one_regular_file_with_metadata(tmp_path):
    path = tmp_path / "compose file';literal.yaml"
    path.write_text("services: {}\n")
    path.chmod(0o640)
    before = path.stat()
    observed = run_helper("get", path)
    assert observed.returncode == 0
    observation = json.loads(observed.stdout)
    assert observation["content"] == "services: {}\n"
    replacement = b"services:\n  api:\n    image: app:new\n"
    result = run_helper("put", path, observation["fingerprint"], replacement)
    assert result.returncode == 0 and json.loads(result.stdout) == {"changed": True}
    assert path.read_bytes() == replacement
    after = path.stat()
    assert after.st_ino != before.st_ino
    assert stat.S_IMODE(after.st_mode) == 0o640
    assert (after.st_uid, after.st_gid) == (before.st_uid, before.st_gid)
    assert list(tmp_path.glob(".maestro-*")) == []
    assert (tmp_path / (path.name + ".maestro.lock")).is_file()


def test_helper_refuses_a_stale_fingerprint_without_changing_file(tmp_path):
    path = tmp_path / "compose.yaml"
    path.write_text("services: {}\n")
    observed = json.loads(run_helper("get", path).stdout)
    path.write_text("external edit\n")
    result = run_helper("put", path, observed["fingerprint"], b"unwanted write")
    assert result.returncode == 11
    assert result.stderr == b"" and path.read_text() == "external edit\n"
    assert list(tmp_path.glob(".maestro-*")) == []


@pytest.mark.parametrize(
    "kind",
    ["symlink", "hardlink", "directory", "special-mode", "too-large", "invalid-utf8", "missing"],
)
def test_helper_refuses_unsafe_or_unsupported_file_layouts(tmp_path, kind):
    path = tmp_path / "compose.yaml"
    if kind == "missing":
        assert run_helper("get", path).returncode == 10
        return
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        original = tmp_path / "original"
        original.write_text("original")
        path.symlink_to(original)
    else:
        path.write_bytes(b"\xff" if kind == "invalid-utf8" else b"services: {}\n")
        if kind == "hardlink":
            os.link(path, tmp_path / "other")
        elif kind == "special-mode":
            path.chmod(0o1640)
        elif kind == "too-large":
            path.write_bytes(b"x" * (1024 * 1024 + 1))
    result = run_helper("get", path)
    assert result.returncode in (14, 20)
    assert result.stderr == b""


def test_helper_refuses_a_symlink_edit_lock(tmp_path):
    path = tmp_path / "compose.yaml"
    path.write_text("services: {}\n")
    fingerprint = json.loads(run_helper("get", path).stdout)["fingerprint"]
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("unchanged")
    (tmp_path / "compose.yaml.maestro.lock").symlink_to(sentinel)
    result = run_helper("put", path, fingerprint, b"unwanted write")
    assert result.returncode == 20
    assert sentinel.read_text() == "unchanged"
    assert path.read_text() == "services: {}\n"


def test_helper_refuses_busy_advisory_lock_without_waiting(tmp_path):
    import fcntl

    path = tmp_path / "compose.yaml"
    path.write_text("services: {}\n")
    fingerprint = json.loads(run_helper("get", path).stdout)["fingerprint"]
    with (tmp_path / "compose.yaml.maestro.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_helper("put", path, fingerprint, b"unwanted write")
    assert result.returncode == 11
    assert path.read_text() == "services: {}\n"


def test_helper_preserves_extended_attributes_when_supported(tmp_path):
    if not hasattr(os, "setxattr"):
        pytest.skip("Python exposes the remote Linux xattr API only on Linux.")
    path = tmp_path / "compose.yaml"
    path.write_text("services: {}\n")
    try:
        os.setxattr(path, "user.maestro-test", b"value")
    except OSError:
        pytest.skip("Temporary filesystem does not support user extended attributes.")
    fingerprint = json.loads(run_helper("get", path).stdout)["fingerprint"]
    result = run_helper("put", path, fingerprint, b"services: {} # new\n")
    assert result.returncode == 0
    assert os.getxattr(path, "user.maestro-test") == b"value"
