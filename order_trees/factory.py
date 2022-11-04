import os
from errno import EEXIST

from order_trees.json_cache import JsonCache
from order_trees.order_tree import OrderTree


def order_book_for_directory(path, use_cache=True):
    """
    Creates an Order book structure for a directory.
    """
    ensure_directory(path)
    ensure_directory(os.path.join(path, 'completed'))

    class _OrderBook(OrderTree):
        DIRECTORY = path
        _CACHE = {}
        _ORDER_MAP = {}
        _USE_CACHE = use_cache

    return _OrderBook


def create_cache_for_directory(path, use_cache=True):
    """
    Creates a generic cache directory with no order features.
    
    Useful for storing dynamic metadata.
    """
    ensure_directory(path)

    class _Cache(JsonCache):
        DIRECTORY = path
        _CACHE = {}
        _USE_CACHE = use_cache

    return _Cache


def ensure_directory(path):
    """
    Ensure that a directory named "path" exists.
    """
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == EEXIST and os.path.isdir(path):
            return
        raise
