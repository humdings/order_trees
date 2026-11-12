"""Directories of orders.

``order_book_for_directory`` and ``create_cache_for_directory`` still return
classes, which is what the legacy callers construct. ``open_order_dir`` and
``symbol_order_dir`` return a ``DirMap``.
"""

from __future__ import annotations

import os
from errno import EEXIST
from pathlib import Path
from typing import Any, Optional

from order_trees.dirmap import DirMap
from order_trees.legacy import JsonCache, OrderTree


def order_book_for_directory(path: str | os.PathLike[str], use_cache: bool = True):
    """Return an ``OrderTree`` subclass bound to *path*.

    Creates *path* and *path*/``completed``. ``use_cache=False`` rebuilds the
    object on every ``from_id`` and parses on every lookup. ``use_cache=True``
    keeps the object but still reloads when the file changes.
    """
    path = os.fspath(path)
    ensure_directory(path)
    ensure_directory(os.path.join(path, "completed"))

    class _OrderBook(OrderTree):
        DIRECTORY = path
        _CACHE: dict[str, OrderTree] = {}
        _ORDER_MAP: dict[Any, Any] = {}
        _USE_CACHE = use_cache

    _OrderBook.__name__ = "OrderBook"
    _OrderBook.__qualname__ = "OrderBook"
    return _OrderBook


def create_cache_for_directory(path: str | os.PathLike[str], use_cache: bool = True):
    """Return a ``JsonCache`` subclass bound to *path*. No order fields."""
    path = os.fspath(path)
    ensure_directory(path)

    class _Cache(JsonCache):
        DIRECTORY = path
        _CACHE: dict[str, JsonCache] = {}
        _USE_CACHE = use_cache

    _Cache.__name__ = "Cache"
    _Cache.__qualname__ = "Cache"
    return _Cache


def open_order_dir(
    path: str | os.PathLike[str],
    codec: Any = None,
    *,
    reload: Any = None,
    lock: Any = None,
    write_through: bool = True,
) -> DirMap:
    """Directory of order files. Does not create ``completed/`` or fill fields."""
    return DirMap(
        path,
        codec,
        reload=reload,
        lock=lock,
        write_through=write_through,
    )


def symbol_order_dir(
    root: str | os.PathLike[str],
    symbol: str,
    codec: Any = None,
    *,
    reload: Any = None,
    lock: Any = None,
    write_through: bool = True,
) -> DirMap:
    """``root/symbol/*.json``, plus a ``completed/`` folder that is not a key.

    *symbol* is one path segment. The order documents themselves are not
    given default fields; use ``new_order_record`` or ``stage_order``.
    """
    if not isinstance(symbol, str) or not symbol or "/" in symbol or "\\" in symbol:
        raise ValueError("symbol must be a single path segment")
    path = Path(root) / symbol
    ensure_directory(path)
    ensure_directory(path / "completed")
    return DirMap(
        path,
        codec,
        reload=reload,
        lock=lock,
        write_through=write_through,
    )


def ensure_directory(path: str | os.PathLike[str]) -> None:
    """Ensure that a directory named *path* exists."""
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == EEXIST and os.path.isdir(path):
            return
        raise
