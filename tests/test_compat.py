"""Legacy order-tree API still drives orders/<symbol>/<id>.json."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from order_trees import (
    ORDER_FIELDS,
    create_cache_for_directory,
    new_order_record,
    order_book_for_directory,
)
from order_trees.constants import (
    ACCOUNT,
    AMOUNT,
    ID,
    NEXT_BUY_ID,
    NEXT_SELL_ID,
    ORDER_ID_FIELD,
    PARENT_ID,
    REMAINING_AMOUNT,
    SIDE,
    STAGED,
    STOP_PRICE,
    SYMBOL,
    TARGET_PRICE,
)
from order_trees.legacy import JsonCache, OrderTree


def test_legacy_constructors_keep_their_arguments() -> None:
    order_params = list(inspect.signature(OrderTree.__init__).parameters)
    assert order_params[:4] == ["self", "data", "order_id", "directory"]
    cache_params = list(inspect.signature(JsonCache.__init__).parameters)
    assert cache_params[:3] == ["self", "data", "directory"]


def test_stage_order_layout_and_fields(tmp_path: Path) -> None:
    directory = tmp_path / "orders" / "AAPL"
    book = order_book_for_directory(directory)
    order = book.stage_order(
        "AAPL",
        10,
        100.5,
        "buy",
        stop_price=90,
        account="DU123",
        order_type="LMT",
        parent_id="parent",
        next_buy_id="buy-next",
        next_sell_id="sell-next",
    )
    path = Path(order.filename)
    assert path.parent == directory
    assert path.name == f"{order._id}.json"
    assert (directory / "completed").is_dir()
    data = json.loads(path.read_text())
    for field in (
        ID,
        ORDER_ID_FIELD,
        SYMBOL,
        SIDE,
        AMOUNT,
        REMAINING_AMOUNT,
        TARGET_PRICE,
        STOP_PRICE,
        STAGED,
        ACCOUNT,
        "size",
        "original_amount",
        "original_side",
        "_order_type",
        "_dt",
        PARENT_ID,
        NEXT_BUY_ID,
        NEXT_SELL_ID,
    ):
        assert field in data
        assert field in ORDER_FIELDS
    assert data["symbol"] == "AAPL"
    assert data["remaining_amount"] == 10
    assert data["size"] == 10
    assert data["original_side"] == "buy"
    assert data["original_amount"] == 10
    assert data["_order_type"] == "LMT"
    assert data["account"] == "DU123"
    assert data["order_id"] == data["_id"]
    assert order.is_staged()
    assert order.is_limit_triggered(100)
    assert not order.is_limit_triggered(101)
    assert order.is_stop_triggered(91)
    assert order.is_triggered(101)
    assert not order.is_triggered(100) or order.is_limit_triggered(100)

    order["remaining_amount"] = 4
    assert book.from_id(order._id)["remaining_amount"] == 4

    data = json.loads(path.read_text())
    data["remaining_amount"] = 1
    path.write_text(json.dumps(data))
    assert order["remaining_amount"] == 1


def test_lookup_links_complete_and_delete(tmp_path: Path) -> None:
    book = order_book_for_directory(tmp_path / "orders" / "IBM")
    parent = book.stage_order("IBM", 1, 10, "buy", staged=False)
    child = book.stage_order(
        "IBM",
        2,
        9,
        "buy",
        parent_id=parent._id,
        order_id="broker-1",
    )
    parent["next_buy_id"] = child._id
    found = book.lookup_order("broker-1")
    assert found is not None
    assert found._id == child._id
    assert parent.get_next_buy()._id == child._id
    assert child.get_previous_order()._id == parent._id
    assert child.find_root()._id == parent._id
    assert parent.is_root()
    assert not parent.is_leaf()
    assert child.is_leaf()

    moved = book.complete_order_id(child._id)
    assert moved is not None
    assert moved["_done"] is True
    completed = tmp_path / "orders" / "IBM" / "completed" / f"{child._id}.json"
    assert completed.is_file()
    assert child._id not in book.all_order_ids()

    book.delete_order_id(parent._id)
    assert parent._id not in book.all_order_ids()
    with pytest.raises(FileNotFoundError):
        book.from_id(parent._id)


def test_context_manager_dumps_data_edits(tmp_path: Path) -> None:
    book = order_book_for_directory(tmp_path / "book")
    order = book.stage_order("AAPL", 1, 1, "sell", staged=False)
    with order:
        order.data["note"] = "desk"
    assert json.loads(Path(order.filename).read_text())["note"] == "desk"


def test_combine_orders(tmp_path: Path) -> None:
    book = order_book_for_directory(tmp_path / "book")
    first = book.stage_order("AAPL", 2, 10, "buy")
    second = book.stage_order("AAPL", 2, 30, "buy")
    crossed = book.stage_order("AAPL", 5, 1, "sell")
    combined = book.combine_orders([first, second, crossed])
    assert combined._id == first._id
    assert combined["amount"] == 4
    assert combined["target_price"] == 20
    assert second._id not in book.all_order_ids()
    assert crossed._id in book.all_order_ids()


def test_cache_directory_has_no_order_defaults(tmp_path: Path) -> None:
    cache_cls = create_cache_for_directory(tmp_path / "meta")
    made = cache_cls.get_or_create("session")
    made["note"] = "hi"
    loaded = cache_cls.from_id("session")
    assert loaded["note"] == "hi"
    assert "symbol" not in loaded
    record = new_order_record("AAPL", 1, 1, "buy")
    assert not (tmp_path / "meta" / f"{record['_id']}.json").exists()


def test_use_cache_false_reloads_from_id(tmp_path: Path) -> None:
    book = order_book_for_directory(tmp_path / "book", use_cache=False)
    order = book.stage_order("AAPL", 1, 5, "buy")
    path = Path(order.filename)
    data = json.loads(path.read_text())
    data["amount"] = 8
    path.write_text(json.dumps(data))
    first = book.from_id(order._id)
    data["amount"] = 9
    path.write_text(json.dumps(data))
    second = book.from_id(order._id)
    assert first is not second
    assert second["amount"] == 9


def test_staged_filter(tmp_path: Path) -> None:
    book = order_book_for_directory(tmp_path / "book")
    live = book.stage_order("AAPL", 1, 1, "buy", staged=True)
    book.stage_order("AAPL", 1, 1, "sell", staged=False)
    staged = book.staged_orders()
    assert [item._id for item in staged] == [live._id]
