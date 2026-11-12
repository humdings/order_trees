"""Patterns taken from the private ib_tools and cybermoney trees.

No account ids and no copies of those trees live in this file. The calls
are the ones those programs make.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from order_trees import (
    BUY,
    ID,
    ORDER_ID_FIELD,
    SELL,
    SIDE,
    STAGED,
    create_cache_for_directory,
    order_book_for_directory,
)
from order_trees.order_tree import (
    ACCOUNT,
    AMOUNT,
    ID as TREE_ID,
    NEXT_BUY_ID,
    STOP_PRICE,
    SYMBOL,
    TARGET_PRICE,
)


def test_order_tree_module_exports_id() -> None:
    """dca.py, trailing_stops.py, and target_weights.py import ID from the module."""
    assert TREE_ID == "_id"
    assert BUY == "buy" and SELL == "sell"


def test_symbol_book_and_metadata_cache(tmp_path: Path) -> None:
    """metadata.py and cybermoney orders/meta.py."""
    symbol = "AAPL"
    book = order_book_for_directory(tmp_path / "orders" / symbol, use_cache=False)
    assert (tmp_path / "orders" / symbol / "completed").is_dir()

    meta_cls = create_cache_for_directory(tmp_path / "meta", use_cache=False)
    assert not (tmp_path / "meta" / "completed").exists()
    meta = meta_cls.get_or_create(f"{symbol}_o-trees")
    defaults = {"rebid_on_buy": False, "target_return": 0.01, "note": None}
    missing = {key: value for key, value in defaults.items() if key not in meta}
    meta.update(missing)
    assert meta_cls.from_id(f"{symbol}_o-trees")["target_return"] == 0.01
    assert meta["rebid_on_buy"] is False

    other = order_book_for_directory(tmp_path / "orders" / "MSFT")
    assert book._CACHE is not other._CACHE


def test_stage_trigger_scale_and_shift(tmp_path: Path) -> None:
    """contract_watcher.stage_order, scale-book, and shift-book."""
    book = order_book_for_directory(tmp_path / "orders" / "AAPL", use_cache=False)
    staged = book.stage_order(
        symbol="AAPL",
        amount=10,
        target_price=100.0,
        side=BUY,
        staged=True,
        stop_price=90.0,
        order_type="LMT",
        account="ACCT",
        parent_id="root",
    )
    assert staged[SYMBOL] == "AAPL"
    assert staged[SIDE] == BUY
    assert staged.is_staged()
    assert staged.is_triggered(100)
    assert staged.is_triggered(99)
    assert not staged.is_limit_triggered(101)
    assert staged.get_previous_order() is None

    parent = book.stage_order("AAPL", 1, 10, SELL, account="ACCT", staged=False)
    child = book.stage_order(
        "AAPL",
        2,
        11,
        BUY,
        account="ACCT",
        parent_id=parent[ID],
    )
    assert child.get_previous_order()[ID] == parent[ID]

    visible = [
        order
        for order in book.staged_orders()
        if order[SYMBOL] == "AAPL" and order[ACCOUNT] == "ACCT"
    ]
    assert staged[ID] in {order[ID] for order in visible}

    current = min(staged["amount"], staged["remaining_amount"])
    staged.update(amount=current * 2, remaining_amount=current * 2)
    staged.update(target_price=101.5)
    reloaded = book.from_id(staged[ID])
    assert reloaded["amount"] == 20
    assert reloaded["remaining_amount"] == 20
    assert reloaded[TARGET_PRICE] == 101.5

    parent_id = parent._id
    book.delete_order_id(parent_id)
    assert parent_id not in book.all_order_ids()


def test_set_order_id_lookup_and_monitor_scan(tmp_path: Path) -> None:
    """set_order_id, lookup_order, and order_monitor's getattr(order, 'order_id')."""
    book = order_book_for_directory(tmp_path / "IBM", use_cache=True)
    order = book.stage_order("IBM", 3, 50, SELL, account="ACCT")
    order.set_order_id(4321)
    assert order.order_id == 4321
    assert getattr(order, "order_id") == 4321
    assert book.lookup_order("4321")[ID] == order[ID]
    assert book.from_order_id(4321)[ID] == order[ID]
    assert book.from_order_id("4321")[ID] == order[ID]

    book._ORDER_MAP.clear()
    book.reset_order_id_cache()
    assert book.from_order_id("4321")[SYMBOL] == "IBM"

    found = None
    for oid in book.all_order_ids():
        candidate = book.from_id(oid)
        if str(getattr(candidate, "order_id", "")) == "4321":
            found = candidate
    assert found is not None
    assert found[ID] == order[ID]


def test_keyword_update_and_complete(tmp_path: Path) -> None:
    """order_monitor update(**fields) and complete_order_id."""
    book = order_book_for_directory(tmp_path / "GLD", use_cache=False)
    order = book.stage_order(
        symbol="GLD",
        amount=4,
        target_price=20,
        side="buy",
        staged=True,
        stop_price=None,
        order_type=None,
        account="ACCT",
    )
    before = dict(order.data)
    order.update(target_price=21.0, staged=False, stop_price=18.0)
    fresh = book.from_id(order[ID])
    assert fresh is not order
    assert fresh["target_price"] == 21.0
    assert fresh[STAGED] is False
    assert fresh["stop_price"] == 18.0
    assert fresh["symbol"] == before["symbol"]

    order_id = order._id
    book.complete_order_id(order_id)
    assert order_id not in book.all_order_ids()
    assert (tmp_path / "GLD" / "completed" / f"{order_id}.json").is_file()


def test_stage_data_then_write_state(tmp_path: Path) -> None:
    """cybermoney ``stage_order(...).data`` then write_order_state."""
    book = order_book_for_directory(tmp_path / "coinbase" / "BTC-USD")
    payload = book.stage_order("BTC-USD", 0.1, 30000, "sell", order_type="limit").data
    assert isinstance(payload, dict)
    assert payload["order_id"] == payload["_id"]
    payload["sell_num"] = 0
    payload["previous_order"] = "parent-1"

    try:
        found = book.from_order_id(payload["order_id"])
        found.update(payload)
    except FileNotFoundError:
        found = book(payload)
        found.dump()
    assert book.from_id(payload["_id"])["sell_num"] == 0
    assert book.from_id(payload["_id"])["previous_order"] == "parent-1"

    exchange = {"id": "broker-55", "side": "buy", "price": "10", "size": "2"}
    exchange["order_id"] = exchange["id"]
    with pytest.raises(FileNotFoundError):
        book.from_order_id(exchange["order_id"])
    created = book(exchange)
    created.dump()
    assert created[ID] == "broker-55"
    assert json.loads((tmp_path / "coinbase" / "BTC-USD" / "broker-55.json").read_text())[
        "side"
    ] == "buy"


def test_exchange_update_keeps_existing_fields(tmp_path: Path) -> None:
    """from_order_id then update(msg) must not drop symbol or amount."""
    book = order_book_for_directory(tmp_path / "gemini" / "ETHUSD")
    order = book.stage_order("ETHUSD", 1.5, 2000, "buy")
    order.set_order_id("g-100")
    msg = {"order_id": "g-100", "id": "g-100", "status": "booked", "price": "2001"}
    found = book.from_order_id(msg["order_id"])
    found.update(staged=False, **{k: v for k, v in msg.items() if k != "staged"})
    saved = book.from_id(order[ID])
    assert saved[STAGED] is False
    assert saved["status"] == "booked"
    assert saved[SYMBOL] == "ETHUSD"
    assert saved[AMOUNT] == 1.5
    assert saved[ORDER_ID_FIELD] == "g-100"


def test_combine_orders_like_tree_strat(tmp_path: Path) -> None:
    book = order_book_for_directory(tmp_path / "gemini" / "BTCUSD", use_cache=False)
    first = book.stage_order("BTCUSD", 1, 10, "buy")
    second = book.stage_order("BTCUSD", 1, 30, "buy")
    combined = book.combine_orders(
        [first, second],
        size_prec=8,
        price_prec=4,
        complete=True,
    )
    assert combined[SIDE] == "buy"
    assert combined["amount"] == 2
    assert combined["target_price"] == 20
    assert combined.data["order_id"] == combined[ID]
    second_id = second._id
    assert second_id not in book.all_order_ids()
    assert (tmp_path / "gemini" / "BTCUSD" / "completed" / f"{second_id}.json").is_file()


def test_metadata_stop_assignment_round_trip(tmp_path: Path) -> None:
    """trailing_stops writes STOP_PRICE onto the metadata cache."""
    meta_cls = create_cache_for_directory(tmp_path / "meta", use_cache=False)
    meta = meta_cls.get_or_create("AAPL_trail")
    meta.update({SIDE: BUY, "trail_stop_pct_enabled": True})
    meta[STOP_PRICE] = 95.5
    again = meta_cls.from_id("AAPL_trail")
    assert again.get(STOP_PRICE) == 95.5
    assert again.get(SIDE) == BUY
    assert NEXT_BUY_ID == "next_buy_id"


def test_empty_book_frame_keeps_staged_column(tmp_path: Path) -> None:
    pandas = pytest.importorskip("pandas")
    book = order_book_for_directory(tmp_path / "EMPTY")
    frame = book.to_frame()
    assert list(frame.index) == []
    assert frame.index.name == "_id"
    assert "staged" in frame.columns
    filtered = frame.loc[frame["staged"] == True]  # noqa: E712
    assert filtered.empty
    reset = frame.reset_index()
    assert "_id" in reset.columns
    assert pandas.api.types.is_object_dtype(reset["_id"]) or reset.empty
