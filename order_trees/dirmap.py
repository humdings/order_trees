"""A dict whose keys are filenames in one directory.

``book["id"]`` is the document in ``id`` + the codec suffix (``.json`` by
default), as a ``FileMap``. Assigning ``book["id"] = {...}`` replaces that
file. Deleting the key removes the file.

Dotfiles, the lock file, temp files, and subdirectories are not keys.

``batch`` holds the directory lock and writes each touched file once on a
clean exit. Field-level batches on one file are ``with folder[key].batch()``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional, Union

from order_trees.atomic import durable_unlink
from order_trees.codec import Codec, JsonCodec
from order_trees.filemap import FileMap
from order_trees.locking import FcntlLock
from order_trees.reload import file_signature

_UNSEEN: Any = object()


def _check_key(key: object) -> str:
    if not isinstance(key, str) or not key or key in {".", ".."}:
        raise ValueError(f"invalid filename key {key!r}")
    if "/" in key or "\\" in key or key.startswith("."):
        raise ValueError(f"invalid filename key {key!r}")
    return key


class DirMap(MutableMapping[str, FileMap]):
    """Directory of documents. The key is the filename without the suffix.

    The constructor matches ``FileMap``: a path, a codec, and generic options.
    ``suffix`` defaults to the codec's ``suffix`` attribute or ``.json``.
    ``lock`` is an optional lock shared by every file. The default is one
    fcntl lock per file, so two writers updating different keys do not block
    each other. ``batch`` also takes a directory lock so two batches cannot
    interleave.
    """

    def __init__(
        self,
        target: Union[str, os.PathLike[str]],
        codec: Optional[Codec] = None,
        *,
        reload: Any = None,
        lock: Any = None,
        suffix: Optional[str] = None,
        write_through: bool = True,
    ) -> None:
        self.path = Path(target)
        if self.path.exists() and not self.path.is_dir():
            raise NotADirectoryError(self.path)
        self._codec = codec if codec is not None else JsonCodec()
        self._reload_spec = reload
        self._shared_lock = lock
        self._write_through = write_through
        chosen = suffix if suffix is not None else getattr(self._codec, "suffix", ".json")
        if not isinstance(chosen, str) or not chosen:
            raise ValueError("suffix must be a non-empty string")
        self._suffix = chosen if chosen.startswith(".") else "." + chosen
        self._maps: dict[str, FileMap] = {}
        self._keys: set[str] = set()
        self._dir_sig: Any = _UNSEEN
        self._batch_depth = 0
        self._deferred: dict[str, FileMap] = {}
        self._pending_deletes: set[str] = set()
        self._dir_lock = FcntlLock(self.path / ".order_trees.lock")

    def close(self) -> None:
        for fm in self._maps.values():
            fm.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            return None

    def _filename(self, key: str) -> str:
        return f"{key}{self._suffix}"

    def _file_path(self, key: str) -> Path:
        return self.path / self._filename(key)

    def _scan(self) -> None:
        signature = file_signature(self.path)
        if signature == self._dir_sig:
            return
        keys: set[str] = set()
        if self.path.is_dir():
            with os.scandir(self.path) as entries:
                for entry in entries:
                    name = entry.name
                    if name.startswith(".") or not name.endswith(self._suffix):
                        continue
                    if not entry.is_file():
                        continue
                    keys.add(name[: -len(self._suffix)])
        if self._batch_depth:
            keys -= self._pending_deletes
            keys.update(self._deferred)
        self._keys = keys
        self._dir_sig = signature

    def _open(self, key: str) -> FileMap:
        fm = self._maps.get(key)
        if fm is None:
            fm = FileMap(
                self._file_path(key),
                self._codec,
                reload=self._reload_spec,
                lock=self._shared_lock,
                write_through=self._write_through,
            )
            self._maps[key] = fm
        return fm

    def _defer(self, key: str, fm: FileMap) -> None:
        if self._batch_depth and key not in self._deferred:
            fm.begin_deferred()
            self._deferred[key] = fm

    def __getitem__(self, key: str) -> FileMap:
        key = _check_key(key)
        if self._batch_depth and key in self._pending_deletes:
            raise KeyError(key)
        self._scan()
        path = self._file_path(key)
        known = key in self._keys or key in self._maps
        if not known and not path.exists():
            raise KeyError(key)
        fm = self._open(key)
        if self._batch_depth:
            self._defer(key, fm)
        return fm

    def __setitem__(self, key: str, value: Mapping[Any, Any]) -> None:
        key = _check_key(key)
        if not isinstance(value, Mapping):
            raise TypeError("DirMap values are JSON objects (mappings)")
        fm = self._open(key)
        if self._batch_depth:
            self._pending_deletes.discard(key)
            self._defer(key, fm)
            fm.replace(value)
            self._keys.add(key)
            return
        fm.replace(value)
        self._keys.add(key)
        self._dir_sig = _UNSEEN

    def __delitem__(self, key: str) -> None:
        key = _check_key(key)
        self._scan()
        path = self._file_path(key)
        if key not in self._keys and not path.exists() and key not in self._maps:
            raise KeyError(key)
        if self._batch_depth:
            fm = self._maps.get(key)
            if fm is not None:
                self._defer(key, fm)
            self._pending_deletes.add(key)
            self._keys.discard(key)
            return
        self._unlink(key)

    def __iter__(self) -> Iterator[str]:
        self._scan()
        keys = set(self._keys)
        if self._batch_depth:
            keys -= self._pending_deletes
            keys.update(key for key in self._deferred if key not in self._pending_deletes)
        return iter(sorted(keys))

    def __len__(self) -> int:
        return sum(1 for _ in self)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            checked = _check_key(key)
        except ValueError:
            return False
        if self._batch_depth and checked in self._pending_deletes:
            return False
        self._scan()
        return checked in self._keys or (
            self._batch_depth and checked in self._maps and checked not in self._pending_deletes
        )

    def __repr__(self) -> str:
        return f"DirMap({str(self.path)!r}, keys={sorted(self)})"

    @contextmanager
    def batch(self) -> Iterator["DirMap"]:
        """Hold the directory lock. Write each touched file once on success.

        An exception leaves the files unchanged and drops the deferred edits.
        """
        if self._batch_depth:
            self._batch_depth += 1
            try:
                yield self
            finally:
                self._batch_depth -= 1
            return
        self._dir_lock.__enter__()
        self._batch_depth = 1
        self._deferred = {}
        self._pending_deletes = set()
        try:
            yield self
            self._commit_batch()
        except BaseException:
            self._rollback_batch()
            raise
        finally:
            self._batch_depth = 0
            self._deferred = {}
            self._pending_deletes = set()
            self._dir_sig = _UNSEEN
            self._dir_lock.__exit__(None, None, None)

    def _commit_batch(self) -> None:
        deletes = set(self._pending_deletes)
        for key, fm in self._deferred.items():
            if key in deletes:
                fm.rollback_deferred()
                continue
            fm.commit_deferred()
        for key in deletes:
            self._unlink(key)

    def _rollback_batch(self) -> None:
        for fm in self._deferred.values():
            fm.rollback_deferred()
        self._dir_sig = _UNSEEN

    def _unlink(self, key: str) -> None:
        fm = self._maps.pop(key, None)
        if fm is not None:
            fm.close()
        durable_unlink(self._file_path(key))
        self._keys.discard(key)
        self._dir_sig = _UNSEEN
