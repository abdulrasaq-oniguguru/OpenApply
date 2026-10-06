"""Crash- and concurrency-safe file writes for files in the OpenApply data directory."""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from pathlib import Path

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def _replace_with_retry(src: str, dst: Path, attempts: int = 10) -> None:
    """Atomic replace; on Windows a concurrent replace can briefly deny access."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.02 * (attempt + 1))


def ensure_private_dir(path: Path) -> Path:
    """Create ``path`` (and parents) and make ``path`` itself owner-only.

    Permission changes are best effort: they are a no-op on Windows.
    """
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(path, PRIVATE_DIR_MODE)
    return path


def atomic_write_bytes(target: Path, data: bytes, *, private: bool = False) -> Path:
    """Write ``data`` to ``target`` via a uniquely named temp file in the same directory.

    The temp file is created owner-only (0600 on POSIX) and removed on failure. With
    ``private=True`` the final file is explicitly chmod-ed 0600 as well.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.stem}-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        if private:
            with contextlib.suppress(OSError):
                os.chmod(tmp_name, PRIVATE_FILE_MODE)
        _replace_with_retry(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return target


def atomic_write_text(target: Path, text: str, *, private: bool = False) -> Path:
    return atomic_write_bytes(target, text.encode("utf-8"), private=private)
