"""Cross-process locks.

``FcntlLock`` is an ``flock`` on a lock file, plus a thread lock so two
threads in one process exclude each other. The same thread can enter the
lock again. The lock file is not the document: documents are replaced by
rename, which would drop a lock held on the document itself.
"""

from __future__ import annotations

import fcntl
import os
import threading
from pathlib import Path
from types import TracebackType
from typing import Optional


class FcntlLock:
    """Advisory exclusive lock shared by every process that opens this path."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._local = threading.RLock()
        self._fd: Optional[int] = None
        self._depth = 0

    def __enter__(self) -> "FcntlLock":
        self._local.acquire()
        try:
            if self._depth == 0:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except Exception:
                    os.close(fd)
                    raise
                self._fd = fd
            self._depth += 1
        except Exception:
            self._local.release()
            raise
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        try:
            self._depth -= 1
            if self._depth == 0 and self._fd is not None:
                fd = self._fd
                self._fd = None
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            self._local.release()


class NullLock:
    """No-op lock. Cooperating writers will not exclude each other."""

    def __enter__(self) -> "NullLock":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        return None


def lock_path_for(document: Path) -> Path:
    """Lock file sitting next to *document*, ignored by directory scans."""
    return document.parent / f".{document.name}.lock"
