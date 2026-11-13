"""Legacy constructor forms, with neutral records."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from order_trees import create_cache_for_directory, order_book_for_directory
from order_trees.errors import Gone
from order_trees.legacy import JsonCache, OrderTree


def test_legacy_constructor_forms() -> None:
    record_params = list(inspect.signature(OrderTree.__init__).parameters)
    assert record_params[:4] == ["self", "data", "order_id", "directory"]
    cache_params = list(inspect.signature(JsonCache.__init__).parameters)
    assert cache_params[:3] == ["self", "data", "directory"]


def test_directory_class_round_trip(tmp_path: Path) -> None:
    kind = order_book_for_directory(tmp_path / "records")
    other = order_book_for_directory(tmp_path / "other")
    assert kind._CACHE is not other._CACHE

    record = kind(data={"_id": "rec-1", "label": "alpha", "count": 2})
    record.dump()
    assert kind.from_id("rec-1")["label"] == "alpha"

    path = tmp_path / "records" / "rec-1.json"
    payload = json.loads(path.read_text())
    payload["count"] = 9
    path.write_text(json.dumps(payload))
    assert record["count"] == 9


def test_secondary_id_and_keyword_update(tmp_path: Path) -> None:
    kind = order_book_for_directory(tmp_path / "records", use_cache=False)
    record = kind(data={"label": "beta"}, order_id="ext-7")
    record.dump()
    assert record["order_id"] == "ext-7"
    assert record._id == "ext-7"

    record.update(count=4, note="kept")
    fresh = kind.from_id("ext-7")
    assert fresh is not record
    assert fresh["count"] == 4
    assert fresh["label"] == "beta"
    assert fresh["note"] == "kept"


def test_cache_directory_and_context_manager(tmp_path: Path) -> None:
    cache_cls = create_cache_for_directory(tmp_path / "meta", use_cache=False)
    made = cache_cls.get_or_create("session")
    made["note"] = "hi"
    loaded = cache_cls.from_id("session")
    assert loaded is not made
    assert loaded["note"] == "hi"
    assert "label" not in loaded

    with loaded:
        loaded.data["flag"] = True
    assert json.loads((tmp_path / "meta" / "session.json").read_text())["flag"] is True


def test_missing_id_is_generated(tmp_path: Path) -> None:
    cache_cls = create_cache_for_directory(tmp_path / "meta")
    made = cache_cls(data={"label": "gamma"})
    assert made._id
    assert (tmp_path / "meta" / f"{made._id}.json").is_file()
    assert cache_cls.from_id(made._id)["label"] == "gamma"


@pytest.mark.parametrize("use_cache", [True, False])
def test_legacy_object_goes_gone_for_both_cache_settings(
    tmp_path: Path, use_cache: bool
) -> None:
    kind = create_cache_for_directory(tmp_path / "meta", use_cache=use_cache)
    made = kind(data={"label": "alpha"})
    ident = made._id
    cached = kind.from_id(ident)
    path = Path(made.filename)
    path.unlink()
    assert made.document is None
    with pytest.raises(Gone):
        made["label"]
    with pytest.raises(Gone):
        made["count"] = 1
    with pytest.raises(Gone):
        made.dump()
    if use_cache:
        again = kind.from_id(ident)
        assert again is cached
        assert again.document is None
        with pytest.raises(Gone):
            again["label"]
    else:
        with pytest.raises(FileNotFoundError):
            kind.from_id(ident)
    made._map.create({"_id": ident, "label": "beta"})
    assert made["label"] == "beta"
    assert made.document == {"_id": ident, "label": "beta"}
