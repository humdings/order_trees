"""Crash-safe file replace.

A write finishes as a temp file in the same directory, is fsynced, then
renamed over the target. The directory is fsynced after the rename. A crash
before the rename leaves the previous file intact. Temp names start with a
dot so a directory mapping does not treat them as keys.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write(path: Path | str, payload: bytes) -> None:
    """Replace *path* with *payload*. The target is never left half-written."""
    path = Path(path)
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=directory,
    )
    tmp = Path(tmp_name)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written == 0:
                raise OSError(f"short write to {tmp}")
            view = view[written:]
        os.fchmod(fd, 0o644)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    try:
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    dir_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        dir_flags |= os.O_DIRECTORY
    dir_fd = os.open(directory, dir_flags)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def durable_unlink(path: Path | str) -> None:
    """Remove *path* and fsync the directory. Missing files are ignored."""
    path = Path(path)
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    directory = path.parent
    dir_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        dir_flags |= os.O_DIRECTORY
    dir_fd = os.open(directory, dir_flags)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
