"""File-mirrored mappings.

``FileMap`` and ``DirMap`` are dicts backed by files. Older constructors
that take a record and a directory live next to them and do not change
how those mappings work.
"""

from order_trees.codec import Codec, JsonCodec, RobustJSONEncoder
from order_trees.constants import (
    ACCOUNT,
    AMOUNT,
    BUY,
    FILE_EXT,
    ID,
    NEXT_BUY_ID,
    NEXT_SELL_ID,
    ORDER_FIELDS,
    ORDER_ID_FIELD,
    PARENT_ID,
    REMAINING_AMOUNT,
    SELL,
    SIDE,
    STAGED,
    STOP_PRICE,
    SYMBOL,
    TARGET_PRICE,
)
from order_trees.dirmap import DirMap
from order_trees.errors import CodecError, DocumentError, OrderTreesError
from order_trees.factory import (
    create_cache_for_directory,
    ensure_directory,
    open_order_dir,
    order_book_for_directory,
    symbol_order_dir,
)
from order_trees.filemap import OVERLAY_POLICY, FileMap
from order_trees.legacy import JsonCache, OrderTree
from order_trees.locking import FcntlLock, NullLock
from order_trees.orders import new_order_record, stage_order
from order_trees.reload import (
    AlwaysReload,
    InotifyReload,
    ManualReload,
    StatReload,
    WatchfilesReload,
)

__all__ = [
    "FileMap",
    "DirMap",
    "OVERLAY_POLICY",
    "Codec",
    "JsonCodec",
    "RobustJSONEncoder",
    "CodecError",
    "DocumentError",
    "OrderTreesError",
    "FcntlLock",
    "NullLock",
    "StatReload",
    "InotifyReload",
    "WatchfilesReload",
    "ManualReload",
    "AlwaysReload",
    "JsonCache",
    "OrderTree",
    "order_book_for_directory",
    "create_cache_for_directory",
    "open_order_dir",
    "symbol_order_dir",
    "ensure_directory",
    "new_order_record",
    "stage_order",
    "FILE_EXT",
    "ID",
    "ORDER_FIELDS",
    "ORDER_ID_FIELD",
    "PARENT_ID",
    "NEXT_BUY_ID",
    "NEXT_SELL_ID",
    "SIDE",
    "BUY",
    "SELL",
    "STAGED",
    "TARGET_PRICE",
    "STOP_PRICE",
    "AMOUNT",
    "REMAINING_AMOUNT",
    "SYMBOL",
    "ACCOUNT",
]

__version__ = "0.1.0"
