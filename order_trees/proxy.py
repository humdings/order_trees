"""Write-through views of nested dicts and lists.

``m["child"]["k"] = 1`` assigns through the root mapping, which writes the
file (or records the change inside ``batch()``). The stored value is the
whole top-level key: a conflict replaces that key, not the nested field.

With ``write_through=False`` the mapping returns a deep copy instead. Editing
the copy does nothing to the file. Put the copy back with ``m[key] = copy``.
"""

from __future__ import annotations

import copy
from collections.abc import MutableMapping
from typing import Any, Iterator

from order_trees.values import plain


def wrap(root: Any, value: Any, path: tuple[Any, ...]) -> Any:
    if isinstance(value, dict):
        if root._write_through:
            return DictProxy(root, path)
        return copy.deepcopy(value)
    if isinstance(value, list):
        if root._write_through:
            return ListProxy(root, path)
        return copy.deepcopy(value)
    return value


class DictProxy(MutableMapping[Any, Any]):
    """Nested dict whose writes go to the root document."""

    def __init__(self, root: Any, path: tuple[Any, ...]) -> None:
        self._root = root
        self._path = path

    def _resolve(self) -> dict[Any, Any]:
        self._root._reload_if_needed()
        node: Any = self._root._data
        for part in self._path:
            node = node[part]
        return node

    def __getitem__(self, key: Any) -> Any:
        value = self._resolve()[key]
        return wrap(self._root, value, self._path + (key,))

    def __setitem__(self, key: Any, value: Any) -> None:
        self._root.set_path(self._path + (key,), value)

    def __delitem__(self, key: Any) -> None:
        self._root.delete_path(self._path + (key,))

    def __iter__(self) -> Iterator[Any]:
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __eq__(self, other: object) -> bool:
        return self._resolve() == other

    def __repr__(self) -> str:
        return repr(self._resolve())

    def update(self, *args: Any, **kwargs: Any) -> None:
        with self._root.batch():
            MutableMapping.update(self, *args, **kwargs)


class ListProxy:
    """Nested list whose writes go to the root document."""

    def __init__(self, root: Any, path: tuple[Any, ...]) -> None:
        self._root = root
        self._path = path

    def _resolve(self) -> list[Any]:
        self._root._reload_if_needed()
        node: Any = self._root._data
        for part in self._path:
            node = node[part]
        return node

    def _store(self, items: list[Any]) -> None:
        self._root.set_path(self._path, items)

    def __getitem__(self, index: Any) -> Any:
        value = self._resolve()[index]
        if isinstance(index, slice):
            return [
                wrap(self._root, item, self._path + (pos,))
                for pos, item in enumerate(value, start=index.start or 0)
            ]
        return wrap(self._root, value, self._path + (index,))

    def __setitem__(self, index: Any, value: Any) -> None:
        if isinstance(index, slice):
            current = list(self._resolve())
            current[index] = [plain(item) for item in value]
            self._store(current)
            return
        self._root.set_path(self._path + (index,), value)

    def __delitem__(self, index: Any) -> None:
        self._root.delete_path(self._path + (index,))

    def __len__(self) -> int:
        return len(self._resolve())

    def __iter__(self) -> Iterator[Any]:
        for index, value in enumerate(self._resolve()):
            yield wrap(self._root, value, self._path + (index,))

    def __eq__(self, other: object) -> bool:
        return list(self._resolve()) == other

    def __repr__(self) -> str:
        return repr(self._resolve())

    def append(self, value: Any) -> None:
        current = list(self._resolve())
        current.append(plain(value))
        self._store(current)

    def extend(self, values: Any) -> None:
        current = list(self._resolve())
        current.extend(plain(item) for item in values)
        self._store(current)

    def insert(self, index: int, value: Any) -> None:
        current = list(self._resolve())
        current.insert(index, plain(value))
        self._store(current)

    def pop(self, index: int = -1) -> Any:
        current = list(self._resolve())
        value = current.pop(index)
        self._store(current)
        return value

    def remove(self, value: Any) -> None:
        current = list(self._resolve())
        current.remove(value)
        self._store(current)

    def clear(self) -> None:
        self._store([])
