"""Decide when a cached document is stale.

The default strategy stats the file and compares ``mtime_ns``, ``ctime_ns``,
size, and inode. That is one syscall on the unchanged path and no parse.

``inotify`` watches the parent directory (atomic replace changes the inode
of the path, so a watch on the file itself would go dead). When the kernel
has no event for this name, lookups do not stat. ``watchfiles`` is the same
idea behind an optional dependency.

``manual`` reloads only after ``FileMap.refresh()``. ``always`` parses on
every lookup.
"""

from __future__ import annotations

import errno
import os
import struct
import threading
from pathlib import Path
from typing import Any, Optional, Union

_UNSEEN: Any = object()

# struct inotify_event { int wd; uint32_t mask, cookie, len; char name[]; }
_EVENT = struct.Struct("iIII")

IN_NONBLOCK = 0x800
IN_CLOEXEC = 0x80000
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000

_INOTIFY_MASK = (
    IN_MODIFY
    | IN_ATTRIB
    | IN_CLOSE_WRITE
    | IN_MOVED_FROM
    | IN_MOVED_TO
    | IN_CREATE
    | IN_DELETE
    | IN_DELETE_SELF
    | IN_MOVE_SELF
    | IN_Q_OVERFLOW
)


def file_signature(path: Path) -> Optional[tuple[int, int, int, int]]:
    """``(mtime_ns, ctime_ns, size, inode)``, or ``None`` when *path* is absent.

    One ``stat`` call. ``ctime_ns`` is included because some clocks reuse a
    ``mtime_ns`` across several replaces, and inode numbers can be recycled.
    """
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return (st.st_mtime_ns, st.st_ctime_ns, st.st_size, st.st_ino)


class StatReload:
    """Reload when mtime_ns, size, or inode changes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._sig: Any = _UNSEEN

    def needs_reload(self) -> bool:
        return file_signature(self.path) != self._sig

    def observe(self, signature: Any) -> None:
        self._sig = signature

    def close(self) -> None:
        return None


class AlwaysReload:
    """Parse on every lookup."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def needs_reload(self) -> bool:
        return True

    def observe(self, signature: Any) -> None:
        return None

    def close(self) -> None:
        return None


class ManualReload:
    """Reload only when ``invalidate`` runs (``FileMap.refresh``)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._dirty = True

    def needs_reload(self) -> bool:
        return self._dirty

    def observe(self, signature: Any) -> None:
        self._dirty = False

    def invalidate(self) -> None:
        self._dirty = True

    def close(self) -> None:
        return None


def _libc() -> Any:
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_init1.restype = ctypes.c_int
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_add_watch.restype = ctypes.c_int
    return libc


class InotifyReload:
    """Skip the stat when inotify has no event for this filename.

    The watch is on the parent directory so an atomic rename is visible.
    An overflow, or a missing inotify, is not swallowed: overflow falls
    back to a stat, and a failed ``inotify_init`` raises ``OSError``.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._stat = StatReload(self.path)
        self._ready = False
        self._force = False
        self._fd = -1
        self._name = self.path.name
        self.path.parent.mkdir(parents=True, exist_ok=True)
        libc = _libc()
        fd = libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        if fd < 0:
            err = ctypes_errno()
            raise OSError(err, os.strerror(err), "inotify_init1")
        wd = libc.inotify_add_watch(fd, os.fsencode(self.path.parent), _INOTIFY_MASK)
        if wd < 0:
            err = ctypes_errno()
            os.close(fd)
            raise OSError(err, os.strerror(err), "inotify_add_watch")
        self._fd = fd

    def needs_reload(self) -> bool:
        if self._fd < 0:
            if not self._ready:
                return True
            return self._stat.needs_reload()
        if not self._ready or self._force:
            self._force = False
            return True
        status = self._drain()
        if status == "none":
            return False
        return self._stat.needs_reload()

    def observe(self, signature: Any) -> None:
        if self._fd >= 0:
            self._drain()
        current = file_signature(self.path)
        if current != signature:
            # An event arrived as we wrote, or a writer that ignores the
            # lock replaced the file. The next lookup must stat.
            self._force = True
            self._stat.observe(_UNSEEN)
        else:
            self._force = False
            self._stat.observe(current)
        self._ready = True

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def _drain(self) -> str:
        """Return ``none``, ``hit``, or ``overflow`` after emptying the queue."""
        if self._fd < 0:
            return "overflow"
        hit = False
        overflow = False
        while True:
            try:
                buf = os.read(self._fd, 8192)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    break
                raise
            if not buf:
                break
            offset = 0
            while offset + _EVENT.size <= len(buf):
                _wd, mask, _cookie, length = _EVENT.unpack_from(buf, offset)
                offset += _EVENT.size
                raw = buf[offset : offset + length]
                offset += length
                if mask & IN_Q_OVERFLOW:
                    overflow = True
                name = raw.split(b"\x00", 1)[0].decode("utf-8", "surrogateescape")
                if name == self._name or name == "":
                    hit = True
        if overflow:
            return "overflow"
        if hit:
            return "hit"
        return "none"

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return None


def ctypes_errno() -> int:
    import ctypes

    return ctypes.get_errno()


class WatchfilesReload:
    """inotify-or-poll via the optional ``watchfiles`` package.

    A daemon thread sets a flag when this filename changes. Lookups that
    see a clear flag do not stat. The first lookup still loads.
    """

    def __init__(self, path: Path) -> None:
        try:
            import watchfiles
        except ImportError as exc:
            raise ImportError(
                "reload='watchfiles' needs the watchfiles package"
            ) from exc
        self._watchfiles = watchfiles
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stat = StatReload(self.path)
        self._flag = threading.Event()
        self._stop = threading.Event()
        self._ready = False
        self._force = False
        self._broken = False
        self._thread = threading.Thread(
            target=self._run,
            name="order-trees-watchfiles",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            for changes in self._watchfiles.watch(
                self.path.parent,
                debounce=0,
                step=10,
                stop_event=self._stop,
                recursive=False,
                raise_interrupt=False,
            ):
                for _change, changed in changes:
                    if os.path.basename(changed) == self.path.name:
                        self._flag.set()
        except Exception:
            self._broken = True
            self._flag.set()

    def needs_reload(self) -> bool:
        if not self._ready or self._force or self._broken or self._flag.is_set():
            self._flag.clear()
            forced = self._force or not self._ready or self._broken
            self._force = False
            if forced:
                return True
            return self._stat.needs_reload()
        return False

    def observe(self, signature: Any) -> None:
        self._flag.clear()
        current = file_signature(self.path)
        if current != signature:
            self._force = True
            self._stat.observe(_UNSEEN)
        else:
            self._force = False
            self._stat.observe(current)
        self._ready = True

    def close(self) -> None:
        self._stop.set()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return None


ReloadStrategy = Union[
    StatReload,
    AlwaysReload,
    ManualReload,
    InotifyReload,
    WatchfilesReload,
]
ReloadSpec = Union[str, ReloadStrategy, None]


def make_reload(spec: ReloadSpec, path: Path) -> ReloadStrategy:
    """Build a strategy for *path*.

    Strings: ``stat`` (default), ``inotify``, ``watchfiles``, ``manual``,
    ``always``. Any other object is used as-is and must already watch *path*.
    """
    if spec is None or spec == "stat":
        return StatReload(path)
    if not isinstance(spec, str):
        return spec
    table = {
        "inotify": InotifyReload,
        "watchfiles": WatchfilesReload,
        "manual": ManualReload,
        "always": AlwaysReload,
    }
    try:
        cls = table[spec]
    except KeyError:
        raise ValueError(
            f"unknown reload strategy {spec!r}; "
            "expected stat, inotify, watchfiles, manual, or always"
        ) from None
    return cls(path)
