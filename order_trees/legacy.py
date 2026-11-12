"""Compatibility layer for the class-based order cache.

``FileMap`` does not generate ids or fill order fields. ``JsonCache`` and
``OrderTree`` still do, because existing callers construct them that way.

``use_cache=True`` (the old default) used to pin the first parse in memory
and ignore later edits to the file. Lookups now stat the file and reload
when ``mtime_ns``, size, or inode changes. ``use_cache=False`` still forces
``from_id`` to build a new object every call, and parses on every lookup.

``with obj:`` still writes on the way out, including when the block raises,
matching the old context manager. Nested edits of ``obj.data`` are not
write-through; call ``dump()`` or leave the ``with`` block. Item assignment
(``obj[key] = value``) writes immediately.

``move_completed`` still moves the file under ``completed/`` and leaves
``filename`` pointing at the active directory. A later ``dump()`` creates
the active file again, which is what the old ``dump`` did.
"""

from __future__ import annotations

import logging
import os
import shutil
import uuid
from collections.abc import MutableMapping
from typing import Any, Optional

from order_trees.codec import JsonCodec
from order_trees.constants import (
    ACCOUNT,
    AMOUNT,
    BUY,
    FILE_EXT,
    ID,
    NEXT_BUY_ID,
    NEXT_SELL_ID,
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
from order_trees.filemap import FileMap
from order_trees.orders import new_order_record

log = logging.getLogger(__name__)


class JsonCache(MutableMapping[str, Any]):
    """One JSON object, addressed by ``_id``, living in ``DIRECTORY``.

    The constructor is the legacy one (``data`` and ``directory``), not the
    generic file-map constructor. A missing ``_id`` is generated and the
    file is written immediately, as before.
    """

    DIRECTORY = ""
    _CACHE: dict[str, "JsonCache"] = {}
    _USE_CACHE = True

    def __init__(
        self,
        data: Optional[dict[str, Any]] = None,
        directory: Optional[str] = None,
    ) -> None:
        if directory is not None:
            self.DIRECTORY = directory
        if data is None:
            data = {}
        else:
            data = dict(data)
        write = False
        if ID not in data:
            data[ID] = self.make_id()
            write = True
        self._id_value = data[ID]
        reload = "stat" if self._USE_CACHE else "always"
        self._map = FileMap(self.filename, reload=reload)
        self._map.seed(data, write=write)

    @property
    def data(self) -> dict[str, Any]:
        """Live document. In-place edits stick when ``dump()`` runs.

        Item assignment does not go through this dict; it writes on its own.
        Do not keep the return value across a reload.
        """
        self._map._reload_if_needed()
        return self._map._data

    @property
    def filename(self) -> str:
        return os.path.join(self.DIRECTORY, str(self._id_value) + FILE_EXT)

    @property
    def _id(self) -> Any:
        return self._id_value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, JsonCache):
            return other.data == self.data
        return other == self.data

    def __hash__(self) -> int:
        return hash(self._id_value)

    def __repr__(self) -> str:
        return repr(self.data)

    def __getitem__(self, key: str) -> Any:
        return self._map[key]

    def __setitem__(self, key: str, item: Any) -> None:
        self._map[key] = item

    def __delitem__(self, key: str) -> None:
        del self._map[key]

    def __iter__(self):
        return iter(self._map)

    def __len__(self) -> int:
        return len(self._map)

    def __contains__(self, item: object) -> bool:
        return item in self._map

    def __enter__(self) -> "JsonCache":
        return self

    def __exit__(self, *args: Any) -> None:
        self.dump()

    def update(self, *args: Any, **kwargs: Any) -> None:
        self._map.update(*args, **kwargs)

    def get(self, key: str, default: Any = None) -> Any:
        return self._map.get(key, default)

    @staticmethod
    def make_id() -> str:
        return uuid.uuid4().hex

    @classmethod
    def from_id(cls, id_hash: str) -> "JsonCache":
        if (id_hash not in cls._CACHE) or (not cls._USE_CACHE):
            path = os.path.join(cls.DIRECTORY, str(id_hash) + FILE_EXT)
            obj = cls.load(path)
            cls._CACHE[id_hash] = obj
        return cls._CACHE[id_hash]

    @classmethod
    def load(cls, filepath: str) -> "JsonCache":
        data = JsonCodec().load(_read_bytes(filepath))
        if not isinstance(data, dict):
            raise TypeError(f"{filepath} is not a JSON object")
        return cls(data=data)

    def dump(self) -> None:
        """Write the current memory image. Does not merge concurrent edits."""
        self._map.write_memory()

    @classmethod
    def get_or_create(cls, id_hash: str) -> "JsonCache":
        try:
            return cls.from_id(id_hash)
        except FileNotFoundError:
            cache = cls(data={ID: id_hash})
            cache.dump()
            return cache


def _read_bytes(filepath: str) -> bytes:
    with open(filepath, "rb") as handle:
        return handle.read()


class OrderTree(JsonCache):
    """Legacy order object: one JSON file plus tree helpers.

    Field filling and id rules live here, not on ``FileMap``. ``data`` may
    be omitted; the old constructor required a dict and raised ``AttributeError``.
    """

    _CACHE: dict[str, "OrderTree"] = {}
    _ORDER_MAP: dict[Any, Any] = {}

    def __init__(
        self,
        data: Optional[dict[str, Any]] = None,
        order_id: Any = None,
        directory: Optional[str] = None,
    ) -> None:
        if data is None:
            data = {}
        else:
            data = dict(data)
        if order_id is None:
            order_id = data.get(ORDER_ID_FIELD) or data.get("id")
            if not order_id and ID in data:
                order_id = data[ID]
        if order_id is not None and ORDER_ID_FIELD not in data:
            data[ORDER_ID_FIELD] = order_id
        if ID not in data and ORDER_ID_FIELD in data and data[ORDER_ID_FIELD]:
            data[ID] = data[ORDER_ID_FIELD]
        super().__init__(data=data, directory=directory)

    @classmethod
    def all_order_ids(cls) -> list[str]:
        directory = cls.DIRECTORY
        if not directory or not os.path.isdir(directory):
            return []
        found = []
        for name in os.listdir(directory):
            if not name.endswith(FILE_EXT) or name.startswith("."):
                continue
            full = os.path.join(directory, name)
            if os.path.isfile(full):
                found.append(name[: -len(FILE_EXT)])
        return found

    @classmethod
    def to_frame(cls):
        """Load every order in this directory into a pandas DataFrame.

        Pandas is not a dependency of the package. Import it before calling.
        """
        try:
            import pandas as pd
        except ImportError as exc:
            raise ImportError("OrderTree.to_frame() requires pandas") from exc
        rows = [dict(cls.from_order_id(id_).data) for id_ in cls.all_order_ids()]
        frame = pd.DataFrame(rows)
        if rows:
            return frame.set_index(ID)
        return frame

    @classmethod
    def staged_orders(cls) -> list["OrderTree"]:
        staged = []
        for oid in cls.all_order_ids():
            order = cls.from_order_id(oid)
            if order.is_staged():
                staged.append(order)
        return staged

    @classmethod
    def from_order_id(cls, order_id: Any) -> "OrderTree":
        order_id = cls._ORDER_MAP.get(order_id, order_id)
        return cls.from_id(order_id)

    @classmethod
    def lookup_order(cls, order_id: Any) -> Optional["OrderTree"]:
        """Find an order by ``_id`` or ``order_id``. ``None`` when absent."""
        order_id = str(order_id)
        if order_id in cls._ORDER_MAP or order_id in cls._CACHE:
            return cls.from_order_id(order_id)
        all_ids = cls.all_order_ids()
        if order_id in all_ids:
            return cls.from_order_id(order_id)
        for oid in all_ids:
            order = cls.from_order_id(oid)
            other_oid = str(order[ORDER_ID_FIELD])
            cls._ORDER_MAP[other_oid] = order[ID]
            if other_oid == order_id:
                return order
        return None

    @classmethod
    def reset_order_id_cache(cls) -> None:
        for oid in cls.all_order_ids():
            order = cls.from_order_id(oid)
            cls._ORDER_MAP[order[ORDER_ID_FIELD]] = oid

    @classmethod
    def delete_order_id(cls, order_id: Any) -> None:
        try:
            order = cls.from_order_id(order_id)
        except FileNotFoundError:
            return
        # Read identifiers before unlinking. A missing file reloads as an
        # empty document, and the id would be gone.
        id_ = order.get(ID, order._id)
        broker_id = order.get(ORDER_ID_FIELD)
        path = order.filename
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        cls._CACHE.pop(id_, None)
        cls._ORDER_MAP.pop(broker_id, None)
        cls._ORDER_MAP.pop(id_, None)

    @classmethod
    def complete_order_id(cls, order_id: Any) -> Optional["OrderTree"]:
        order = cls.lookup_order(order_id)
        if order is not None:
            order.move_completed()
        return order

    @classmethod
    def stage_order(
        cls,
        symbol: str,
        amount: Any,
        target_price: Any,
        side: str,
        staged: bool = True,
        stop_price: Any = None,
        order_type: Any = None,
        account: Any = None,
        **kwargs: Any,
    ) -> "OrderTree":
        data = new_order_record(
            symbol,
            amount,
            target_price,
            side,
            staged=staged,
            stop_price=stop_price,
            order_type=order_type,
            account=account,
            **kwargs,
        )
        obj = cls(data=data)
        obj.dump()
        return obj

    @classmethod
    def combine_orders(
        cls,
        order_list: list["OrderTree"],
        size_prec: int = 7,
        price_prec: int = 2,
        complete: bool = False,
    ) -> "OrderTree":
        """Combine same-side orders into the first one.

        ``target_price`` becomes the size-weighted average. ``amount`` is the
        sum of sizes. The first id is kept. The others are deleted, after an
        optional move into ``completed/``.
        """
        if len(order_list) == 1:
            return order_list[0]

        def get_amount(order: "OrderTree") -> float:
            amount = float(order[AMOUNT])
            if REMAINING_AMOUNT in order:
                amount = min(amount, float(order[REMAINING_AMOUNT]))
            return amount

        consuming_order = order_list[0]
        others = order_list[1:]
        total_size = get_amount(consuming_order)
        total_value = float(consuming_order[TARGET_PRICE]) * total_size
        side = consuming_order[SIDE]
        orders_included = []

        for order in others:
            if order[SIDE] != side:
                log.warning("[Crossed Order Sides combining] %s", order)
                continue
            amount = get_amount(order)
            price = float(order[TARGET_PRICE])
            total_size += amount
            total_value += amount * price
            orders_included.append(order)

        size = round(total_size, size_prec)
        target = round(total_value / total_size, price_prec) if total_size else 0
        consuming_order.update(
            {
                AMOUNT: size,
                TARGET_PRICE: target,
                "_dt": _now_for_combine(),
                "original_amount": size,
            }
        )
        if complete:
            for order in orders_included:
                cls.complete_order_id(order[ID])
        for order in orders_included:
            cls.delete_order_id(order[ID])
        return consuming_order

    def move_completed(self) -> "OrderTree":
        self["_done"] = True
        # Snapshot before the file disappears. A reload of a missing file
        # would otherwise replace the document with {}.
        kept = dict(self._map._data)
        oid = kept.get(ORDER_ID_FIELD)
        id_ = kept[ID]
        src = self.filename
        dest_dir = os.path.join(self.DIRECTORY, "completed")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, str(id_) + FILE_EXT)
        try:
            shutil.move(src, dest)
        except FileNotFoundError:
            pass
        # filename stays in the active directory. A later dump() creates that
        # file again, which is what the old dump() did after the move.
        self._map.seed(kept, write=False)
        self._ORDER_MAP.pop(oid, None)
        self._ORDER_MAP.pop(id_, None)
        type(self)._CACHE.pop(id_, None)
        return self

    def set_order_id(self, order_id: Any) -> None:
        _id = self.data.get(ID, order_id)
        self._ORDER_MAP[order_id] = _id
        self[ORDER_ID_FIELD] = order_id

    def is_staged(self) -> bool:
        return STAGED in self and bool(self[STAGED])

    def is_triggered(self, price: Any) -> bool:
        if not self.is_staged():
            return False
        if self.is_limit_triggered(price):
            return True
        if self.is_stop_triggered(price):
            return True
        return False

    def is_limit_triggered(self, price: Any) -> bool:
        if self[SIDE] == BUY:
            return float(price) <= float(self[TARGET_PRICE])
        if self[SIDE] == SELL:
            return float(price) >= float(self[TARGET_PRICE])
        raise RuntimeError(f"Order Side not valid: {self.data}")

    def is_stop_triggered(self, price: Any) -> bool:
        stop = self.get(STOP_PRICE)
        if not stop:
            return False
        if self[SIDE] == BUY:
            return float(price) >= float(self[STOP_PRICE])
        if self[SIDE] == SELL:
            return float(price) <= float(self[STOP_PRICE])
        raise RuntimeError(f"Order Side not valid: {self.data}")

    def get_next_buy(self) -> Optional["OrderTree"]:
        if NEXT_BUY_ID in self:
            return self.lookup_order(self[NEXT_BUY_ID])
        return None

    def get_next_sell(self) -> Optional["OrderTree"]:
        if NEXT_SELL_ID in self:
            return self.lookup_order(self[NEXT_SELL_ID])
        return None

    def get_children(self) -> list[Optional["OrderTree"]]:
        return [self.get_next_buy(), self.get_next_sell()]

    def get_previous_order(self) -> Optional["OrderTree"]:
        if PARENT_ID in self:
            return self.lookup_order(self[PARENT_ID])
        return None

    def print_tree(self) -> None:
        from pprint import pprint

        buy = self.get_next_buy()
        if buy:
            buy.print_tree()
        pprint(self.data)
        sell = self.get_next_sell()
        if sell:
            sell.print_tree()

    def preorder_traverse(self, root: Optional["OrderTree"]) -> list[dict[str, Any]]:
        res: list[dict[str, Any]] = []
        if root:
            res.append(dict(root.data))
            res = res + self.preorder_traverse(root.get_next_buy())
            res = res + self.preorder_traverse(root.get_next_sell())
        return res

    def find_root(self) -> "OrderTree":
        previous = self.get_previous_order()
        if previous is None:
            return self
        return previous.find_root()

    def is_root(self) -> bool:
        return PARENT_ID not in self.data

    def is_leaf(self, keep_closed: bool = False) -> bool:
        if not keep_closed:
            if "done_reason" in self and self["done_reason"] in ("filled", "canceled"):
                return False
            if "reason" in self and self["reason"] in ("filled", "canceled"):
                return False
            if "type" in self and self["type"] == "done":
                return False
            if "status" in self and self["status"] in ("filled", "done"):
                return False
            if "settled" in self and self["settled"]:
                return False
        return (NEXT_BUY_ID not in self.data) and (NEXT_SELL_ID not in self.data)


def _now_for_combine() -> str:
    try:
        import pandas as pd
    except ImportError:
        from order_trees.orders import now_text

        return now_text()
    return pd.Timestamp.utcnow()
