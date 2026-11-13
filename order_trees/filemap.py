"""A dict whose contents are one file.

Lookups use the in-memory document. The reload strategy decides whether
that document is stale. A set or delete writes the file under a cross-process
lock.

Conflict policy (overlay)
-------------------------
On a partial write, if the file changed since the last load, it is read
again and this write's top-level keys are applied on top. Those keys win,
including deletes. Every other key keeps the value from the file. There is
no merge inside one key: a nested assignment replaces the whole top-level
value.

``replace`` and a directory-mapping assignment overwrite the whole document.
They do not keep keys the caller left out.

Cooperating writers take the fcntl lock. ``modify`` and ``batch`` hold that
lock across the read and the write, so a read-modify-write inside them is
atomic against other cooperating writers. ``m[k] = m[k] + 1`` is not: the
read happens before the lock.

``batch`` holds the lock and writes once, when the block exits cleanly.
An exception rolls the memory image back and does not write.

Nested dicts and lists
----------------------
By default ``m["child"]["k"] = 1`` writes through a proxy. Set
``write_through=False`` to get a deep copy instead; persisting that copy
requires assigning it back to the key.
"""

from __future__ import annotations

import copy
import os
import time
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional, Union

from order_trees.atomic import atomic_write
from order_trees.codec import Codec, JsonCodec
from order_trees.errors import Conflict, DocumentError, EmptyFileError, Gone
from order_trees.locking import FcntlLock, lock_path_for
from order_trees.proxy import wrap
from order_trees.reload import file_signature, is_directory, is_regular_file, make_reload
from order_trees.values import plain

_MISSING = object()

OVERLAY_POLICY = (
    "On a partial write, if the file changed since the last load, read it "
    "again and apply this write's top-level keys on top. Those keys win, "
    "including deletes. Every other key keeps the value from the latest "
    "file. There is no merge inside a single key. replace() overwrites the "
    "whole document. Cooperating writers take the fcntl lock. "
    "Read-modify-write of one key is atomic in modify() and in batch()."
)


def resolve_target(target: Union[str, os.PathLike[str], Any]) -> Path:
    """Path of a filesystem target.

    A file object is accepted when ``name`` is a real path (not ``<stdin>``
    or similar). The object is not read or written; the path is.
    """
    if isinstance(target, (str, os.PathLike)):
        return Path(os.fspath(target))
    name = getattr(target, "name", None)
    if isinstance(name, (str, bytes, os.PathLike)):
        text = os.fsdecode(name)
        if text and not text.startswith("<"):
            return Path(text)
    raise TypeError(
        "target must be a filesystem path or a file object whose .name is a path"
    )


def _as_document(data: Mapping[Any, Any]) -> dict[str, Any]:
    if type(data).__name__ == "FileMap":
        data = data.to_dict()
    document: dict[str, Any] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise TypeError(
                f"JSON object keys must be strings, got {type(key).__name__}"
            )
        document[key] = plain(value)
    return document


class FileMap(MutableMapping[str, Any]):
    """Mutable mapping stored as one document on disk.

    Parameters
    ----------
    target:
        Filesystem path, or a file object whose ``name`` is a path.
    codec:
        Object with ``load(bytes)`` and ``dump(obj) -> bytes``. The default
        is JSON, using orjson when it is installed.
    reload:
        ``"stat"`` (default), ``"inotify"``, ``"watchfiles"``, ``"manual"``,
        ``"always"``, or a strategy instance bound to this path.
    lock:
        Context manager used as the writer lock. The default is an fcntl
        lock beside the file.
    write_through:
        When true (the default), nested dicts and lists are proxies that
        write on mutation. When false, lookups of those values return copies.
    """

    def __init__(
        self,
        target: Union[str, os.PathLike[str], Any],
        codec: Optional[Codec] = None,
        *,
        reload: Any = None,
        lock: Any = None,
        write_through: bool = True,
    ) -> None:
        self._path = resolve_target(target)
        self._codec = codec if codec is not None else JsonCodec()
        self._reload = make_reload(reload, self._path)
        self._lock = lock if lock is not None else FcntlLock(lock_path_for(self._path))
        self._write_through = write_through
        self._data: dict[str, Any] = {}
        self._overlay: dict[str, tuple[Any, ...]] = {}
        self._overwrite = False
        self._base_sig: Any = None
        self._loaded = False
        self._batch_depth = 0
        self._external_batch = False
        self._force = False
        self._snapshot: Optional[dict[str, Any]] = None
        self._snapshot_overlay: dict[str, tuple[Any, ...]] = {}
        self._snapshot_overwrite = False
        self._creating = False
        self._pending_first_dump = False
        self._batch_mtime: Optional[int] = None
        self._batch_ino: Optional[int] = None

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        """Release watcher resources. The file is left as it is."""
        closer = getattr(self._reload, "close", None)
        if closer is not None:
            closer()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return None

    def refresh(self) -> None:
        """Reload from disk even if the strategy thinks nothing changed."""
        invalidate = getattr(self._reload, "invalidate", None)
        if callable(invalidate):
            invalidate()
        self._force = True
        self._reload_if_needed()

    @property
    def exists(self) -> bool:
        """True when the path is a regular file. Stats on every read."""
        return is_regular_file(self._path)

    @property
    def gone(self) -> bool:
        """True when the path is not a regular file."""
        return not self.exists

    @property
    def document(self) -> Optional[dict[str, Any]]:
        """Plain copy of the document, or ``None`` when the path is gone."""
        if not is_regular_file(self._path):
            return None
        return self.to_dict()

    def to_dict(self) -> dict[str, Any]:
        """Deep copy of the current document."""
        self._prepare_read()
        return copy.deepcopy(self._data)

    def __bool__(self) -> bool:
        if not is_regular_file(self._path):
            return False
        self._prepare_read()
        return bool(self._data)

    def __getitem__(self, key: str) -> Any:
        self._prepare_read()
        try:
            value = self._data[key]
        except KeyError:
            raise KeyError(key) from None
        return wrap(self, value, (key,))

    def __setitem__(self, key: str, value: Any) -> None:
        if not isinstance(key, str):
            raise TypeError(
                f"JSON object keys must be strings, got {type(key).__name__}"
            )
        value = plain(value)
        if self._batch_depth:
            self._put(key, value)
            return
        with self._lock:
            self._refuse_if_absent()
            self._load()
            self._put(key, value)
            self._write_locked()

    def __delitem__(self, key: str) -> None:
        if self._batch_depth:
            self._remove(key)
            return
        with self._lock:
            self._refuse_if_absent()
            self._load()
            self._remove(key)
            self._write_locked()

    def __iter__(self) -> Iterator[str]:
        self._prepare_read()
        return iter(list(self._data))

    def __len__(self) -> int:
        self._prepare_read()
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        self._prepare_read()
        return key in self._data

    def get(self, key: str, default: Any = None) -> Any:
        if not is_regular_file(self._path):
            raise Gone(str(self._path))
        return MutableMapping.get(self, key, default)

    def pop(self, key: str, default: Any = _MISSING) -> Any:
        if not is_regular_file(self._path):
            raise Gone(str(self._path))
        if default is _MISSING:
            return MutableMapping.pop(self, key)
        return MutableMapping.pop(self, key, default)

    def __repr__(self) -> str:
        self._reload_if_needed()
        return f"FileMap({str(self._path)!r}, {self._data!r})"

    def update(self, *args: Any, **kwargs: Any) -> None:
        with self.batch():
            MutableMapping.update(self, *args, **kwargs)

    def clear(self) -> None:
        with self.batch():
            for key in list(self._data):
                del self[key]

    def pop(self, key: str, default: Any = _MISSING) -> Any:
        with self.batch():
            if key not in self._data:
                if default is _MISSING:
                    raise KeyError(key)
                return default
            value = plain(self._data[key])
            del self[key]
            return value

    def setdefault(self, key: str, default: Any = None) -> Any:
        with self.batch():
            if key in self._data:
                return wrap(self, self._data[key], (key,))
            self[key] = default
            return wrap(self, self._data[key], (key,))

    def replace(self, data: Mapping[Any, Any]) -> None:
        """Overwrite the document. Keys omitted from *data* are removed.

        This does not overlay. Partial updates belong on individual keys.
        """
        document = _as_document(data)
        if self._batch_depth:
            self._data = document
            self._overwrite = True
            self._overlay.clear()
            return
        with self._lock:
            self._refuse_if_absent()
            self._data = document
            self._overwrite = True
            self._overlay.clear()
            self._write_locked()

    def modify(
        self,
        key: str,
        fn: Callable[[Any], Any],
        default: Any = _MISSING,
    ) -> Any:
        """Under the lock, set ``key`` to ``fn(current)``.

        *fn* receives a copy when the current value is a dict or list.
        The value it returns is stored. Against other cooperating writers
        this read and write are one critical section.
        """
        with self.batch():
            if key in self._data:
                current = self._data[key]
                if isinstance(current, (dict, list)):
                    current = copy.deepcopy(current)
            elif default is _MISSING:
                raise KeyError(key)
            else:
                current = default
            updated = fn(current)
            self[key] = updated
            return updated

    @contextmanager
    def batch(self) -> Iterator["FileMap"]:
        """Hold the lock and apply every change in this block as one write.

        Nested ``batch`` calls join the outer one. An exception restores the
        document from the start of the outermost block and skips the write.
        """
        if self._batch_depth:
            self._batch_depth += 1
            try:
                yield self
            finally:
                self._batch_depth -= 1
            return
        self._lock.__enter__()
        try:
            self._refuse_if_absent()
            # Parse under the lock. A stat collision must not feed modify()
            # a stale value.
            self._load()
            self._remember_batch_stamp()
            self._batch_depth = 1
            snapshot = dict(self._data)
            snapshot_overlay = dict(self._overlay)
            snapshot_overwrite = self._overwrite
            try:
                yield self
            except BaseException:
                self._data = snapshot
                self._overlay = snapshot_overlay
                self._overwrite = snapshot_overwrite
                raise
            else:
                try:
                    if self._overlay or self._overwrite:
                        self._write_locked()
                except Conflict:
                    self._data = snapshot
                    self._overlay = snapshot_overlay
                    self._overwrite = snapshot_overwrite
                    self._force = True
                    raise
            finally:
                self._batch_depth = 0
        finally:
            self._lock.__exit__(None, None, None)

    def seed(self, data: Mapping[Any, Any], *, write: bool) -> None:
        """Install *data* as the memory image.

        Used by the legacy layer. When *write* is false the file is not
        touched and the current signature is treated as already seen, so a
        later lookup does not throw the image away.
        """
        document = _as_document(data)
        self._data = document
        self._overlay.clear()
        self._overwrite = False
        self._loaded = True
        if write:
            with self._lock:
                self._overwrite = True
                self._creating = True
                try:
                    self._write_locked()
                finally:
                    self._creating = False
            self._pending_first_dump = False
            return
        signature = file_signature(self._path)
        self._pending_first_dump = signature is None
        self._base_sig = signature
        self._reload.observe(signature)

    def write_memory(self) -> None:
        """Write the current memory image over the file, without merging.

        Legacy ``dump()`` uses this so in-place edits of ``.data`` are the
        bytes that hit disk. A missing file is not recreated.
        """
        with self._lock:
            if not is_regular_file(self._path):
                if not self._pending_first_dump:
                    self._refuse_if_absent()
                self._overwrite = True
                self._overlay.clear()
                self._creating = True
                try:
                    self._write_locked()
                finally:
                    self._creating = False
                self._pending_first_dump = False
                return
            self._pending_first_dump = False
            self._overwrite = True
            self._overlay.clear()
            self._write_locked()

    def create(self, document: Mapping[Any, Any]) -> None:
        """Write *document* only when the path is absent.

        This is the explicit recreate. If the path is already a file,
        raise ``FileExistsError`` and leave it unchanged.
        """
        payload = _as_document(document)
        with self._lock:
            if is_directory(self._path):
                raise IsADirectoryError(self._path)
            if is_regular_file(self._path):
                raise FileExistsError(self._path)
            self._data = payload
            self._overlay.clear()
            self._overwrite = True
            self._creating = True
            try:
                self._write_locked()
            finally:
                self._creating = False

    def begin_deferred(self) -> None:
        """Start deferring writes without taking this file's lock.

        ``DirMap.batch`` holds the directory lock and commits each file once
        at the end. Not used by ``FileMap.batch``, which holds the file lock
        for the whole block.
        """
        if self._external_batch or self._batch_depth:
            return
        self._load()
        self._remember_batch_stamp()
        self._snapshot = dict(self._data)
        self._snapshot_overlay = dict(self._overlay)
        self._snapshot_overwrite = self._overwrite
        self._batch_depth = 1
        self._external_batch = True

    def commit_deferred(self) -> None:
        if not self._external_batch:
            return
        try:
            if self._overlay or self._overwrite:
                with self._lock:
                    self._write_locked()
        finally:
            self._batch_depth = max(0, self._batch_depth - 1)
            self._external_batch = False
            self._snapshot = None

    def rollback_deferred(self) -> None:
        if not self._external_batch:
            return
        if self._snapshot is not None:
            self._data = self._snapshot
        self._overlay = dict(self._snapshot_overlay)
        self._overwrite = self._snapshot_overwrite
        self._batch_depth = max(0, self._batch_depth - 1)
        self._external_batch = False
        self._snapshot = None

    def set_path(self, path: tuple[Any, ...], value: Any) -> None:
        value = plain(value)
        if self._batch_depth:
            self._set_path_memory(path, value)
            return
        with self._lock:
            self._refuse_if_absent()
            self._load()
            self._set_path_memory(path, value)
            self._write_locked()

    def delete_path(self, path: tuple[Any, ...]) -> None:
        if self._batch_depth:
            self._delete_path_memory(path)
            return
        with self._lock:
            self._refuse_if_absent()
            self._load()
            self._delete_path_memory(path)
            self._write_locked()

    def conflicts_with_batch(self) -> bool:
        """True when the file changed or vanished since this batch loaded it."""
        sig = file_signature(self._path)
        if self._batch_mtime is None and self._batch_ino is None:
            return sig is not None
        if sig is None:
            return True
        return sig[0] != self._batch_mtime or sig[3] != self._batch_ino

    def _reload_if_needed(self) -> None:
        if self._batch_depth and not self._force:
            return
        if not self._force and self._loaded and not self._reload.needs_reload():
            return
        self._force = False
        self._load()

    def _prepare_read(self) -> None:
        self._reload_if_needed()
        if self._batch_depth:
            return
        if not is_regular_file(self._path):
            raise Gone(str(self._path))

    def _refuse_if_absent(self) -> None:
        if is_directory(self._path):
            raise IsADirectoryError(self._path)
        if not is_regular_file(self._path):
            raise Gone(str(self._path))

    def _remember_batch_stamp(self) -> None:
        sig = file_signature(self._path)
        if sig is None:
            self._batch_mtime = None
            self._batch_ino = None
            return
        self._batch_mtime = sig[0]
        self._batch_ino = sig[3]

    def _ensure_loaded(self) -> None:
        if not self._loaded or self._reload.needs_reload():
            self._load()

    def _mark_absent(self) -> None:
        self._data = {}
        self._base_sig = None
        self._overlay.clear()
        self._overwrite = False
        self._loaded = True
        self._reload.observe(None)

    def _load(self) -> None:
        last: Optional[BaseException] = None
        for attempt in range(4):
            signature = file_signature(self._path)
            try:
                blob = self._path.read_bytes()
            except FileNotFoundError:
                if file_signature(self._path) is None:
                    self._mark_absent()
                    return
                continue
            if blob == b"":
                raise EmptyFileError(f"{self._path} is empty")
            signature_after = file_signature(self._path)
            if signature != signature_after:
                continue
            try:
                data = self._codec.load(blob)
            except Exception as exc:
                last = exc
                if attempt == 3:
                    break
                time.sleep(0.002)
                continue
            if not isinstance(data, dict):
                raise DocumentError(
                    f"{self._path} contains a {type(data).__name__}, not a JSON object"
                )
            self._install(data, signature_after)
            return
        raise DocumentError(f"{self._path} is not a JSON object") from last

    def _install(self, data: dict[str, Any], signature: Any) -> None:
        self._data = data
        self._base_sig = signature
        self._overlay.clear()
        self._overwrite = False
        self._loaded = True
        self._reload.observe(signature)

    def _put(self, key: str, value: Any) -> None:
        data = dict(self._data)
        data[key] = value
        self._data = data
        if not self._overwrite:
            self._overlay[key] = ("set", value)

    def _remove(self, key: str) -> None:
        if key not in self._data:
            raise KeyError(key)
        data = dict(self._data)
        del data[key]
        self._data = data
        if not self._overwrite:
            self._overlay[key] = ("del",)

    def _set_path_memory(self, path: tuple[Any, ...], value: Any) -> None:
        if not path:
            raise ValueError("empty path")
        top = path[0]
        if not isinstance(top, str):
            raise TypeError("JSON object keys must be strings")
        if len(path) == 1:
            self._put(top, value)
            return
        current = self._data.get(top)
        if not isinstance(current, (dict, list)):
            raise TypeError(
                f"cannot descend into {type(current).__name__} at {top!r}"
            )
        cloned = copy.deepcopy(current)
        node = cloned
        for part in path[1:-1]:
            node = node[part]
        node[path[-1]] = value
        self._put(top, cloned)

    def _delete_path_memory(self, path: tuple[Any, ...]) -> None:
        if len(path) == 1:
            self._remove(path[0])
            return
        top = path[0]
        cloned = copy.deepcopy(self._data[top])
        node = cloned
        for part in path[1:-1]:
            node = node[part]
        del node[path[-1]]
        self._put(top, cloned)

    def _read_document(self) -> dict[str, Any]:
        blob = self._path.read_bytes()
        if blob == b"":
            raise EmptyFileError(f"{self._path} is empty")
        data = self._codec.load(blob)
        if not isinstance(data, dict):
            raise DocumentError(
                f"{self._path} contains a {type(data).__name__}, not a JSON object"
            )
        return data

    def _write_locked(self) -> None:
        if not self._overlay and not self._overwrite:
            return
        if self._batch_depth and not self._creating:
            if self.conflicts_with_batch():
                raise Conflict(f"{self._path} changed during the batch")
        saved = dict(self._data)
        saved_overlay = dict(self._overlay)
        saved_overwrite = self._overwrite
        try:
            for _ in range(8):
                signature = file_signature(self._path)
                if self._overwrite or signature is None:
                    if signature is None and not (self._creating or self._overwrite):
                        raise Gone(str(self._path))
                    document = dict(self._data)
                else:
                    # Always re-read. mtime_ns, ctime_ns, size, and inode can
                    # collide with an older observation when a clock is coarse
                    # and inodes are reused, so a writer must not trust them.
                    try:
                        document = self._read_document()
                    except FileNotFoundError:
                        raise Gone(str(self._path)) from None
                    if file_signature(self._path) != signature:
                        continue
                    for key, op in self._overlay.items():
                        if op[0] == "set":
                            document[key] = op[1]
                        else:
                            document.pop(key, None)
                payload = self._codec.dump(document)
                atomic_write(self._path, payload)
                new_sig = file_signature(self._path)
                self._data = document
                self._base_sig = new_sig
                self._overlay.clear()
                self._overwrite = False
                self._loaded = True
                self._reload.observe(new_sig)
                return
        except Exception:
            # The target was not replaced (or a later step failed). Match
            # memory to whatever is on disk now so a failed set does not
            # linger as a phantom value.
            try:
                self._load()
            except Exception:
                self._data = saved
                self._overlay = saved_overlay
                self._overwrite = saved_overwrite
            raise
        raise OSError(f"could not commit a stable write to {self._path}")
