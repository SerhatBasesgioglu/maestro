"""Trusted remote Python source; paths, fingerprints, and content are separate data.

The helper uses only the remote Linux Python 3 standard library. Statuses:
10 missing, 11 conflict/lock busy, 13 permission, 14 unsafe file, 20 I/O failure.
An advisory sibling lock coordinates Maestro writers; external editors must
also coordinate. Atomic replacement preserves owner/mode/xattrs, not inode IDs.
"""

COMPOSE_FILE = r"""
import fcntl
import hashlib
import json
import os
import stat
import sys
import tempfile

LIMIT = 1024 * 1024
action, path = sys.argv[1:3]

class UnsafeFile(Exception):
    pass

class Conflict(Exception):
    pass

def snapshot():
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise UnsafeFile()
        if before.st_size > LIMIT or stat.S_IMODE(before.st_mode) & 0o7000:
            raise UnsafeFile()
        content = stream.read(LIMIT + 1)
        after = os.fstat(stream.fileno())
    if len(content) > LIMIT:
        raise UnsafeFile()
    attributes = lambda value: [value.st_dev, value.st_ino, value.st_mode,
        value.st_uid, value.st_gid, value.st_size, value.st_mtime_ns, value.st_ctime_ns]
    if attributes(before) != attributes(after):
        raise Conflict()
    encoded = json.dumps(attributes(after)).encode() + b"\0" + content
    fingerprint = hashlib.sha256(encoded).hexdigest()
    return content, after, fingerprint

def replace():
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    lock = os.open(path + ".maestro.lock", flags, 0o600)
    temporary = None
    try:
        metadata = os.fstat(lock)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise UnsafeFile()
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        original, metadata, fingerprint = snapshot()
        if fingerprint != sys.argv[3]:
            raise Conflict()
        content = sys.stdin.buffer.read(LIMIT + 1)
        if len(content) > LIMIT:
            raise UnsafeFile()
        content.decode("utf-8")
        descriptor, temporary = tempfile.mkstemp(prefix=".maestro-", dir=os.path.dirname(path))
        with os.fdopen(descriptor, "wb") as stream:
            temporary_metadata = os.fstat(stream.fileno())
            owner = temporary_metadata.st_uid, temporary_metadata.st_gid
            if owner != (metadata.st_uid, metadata.st_gid):
                os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            stream.write(content)
            stream.flush()
            for name in os.listxattr(path, follow_symlinks=False):
                value = os.getxattr(path, name, follow_symlinks=False)
                os.setxattr(temporary, name, value, follow_symlinks=False)
            os.fsync(stream.fileno())
        if snapshot()[2] != fingerprint:
            raise Conflict()
        os.replace(temporary, path)
        temporary = None
        directory = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        print(json.dumps({"changed": True}))
    finally:
        if temporary is not None:
            os.unlink(temporary)
        os.close(lock)

try:
    if action == "get":
        content, metadata, fingerprint = snapshot()
        print(json.dumps({"content": content.decode("utf-8"), "fingerprint": fingerprint}))
    elif action == "put":
        replace()
    else:
        raise UnsafeFile()
except FileNotFoundError:
    sys.exit(10)
except PermissionError:
    sys.exit(13)
except (Conflict, BlockingIOError):
    sys.exit(11)
except (UnsafeFile, UnicodeError):
    sys.exit(14)
except OSError:
    sys.exit(20)
"""
