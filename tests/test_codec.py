"""JSON codec, with and without orjson."""

from __future__ import annotations

import datetime as dt
import decimal
import json

import pytest

from order_trees import JsonCodec, RobustJSONEncoder
from order_trees.errors import CodecError


def test_stdlib_round_trip_and_defaults() -> None:
    codec = JsonCodec(impl="stdlib")
    payload = codec.dump(
        {
            "n": 1,
            "d": decimal.Decimal("1.50"),
            "when": dt.datetime(2024, 1, 2, 3, 4, 5, tzinfo=dt.timezone.utc),
        }
    )
    data = codec.load(payload)
    assert data["n"] == 1
    assert data["d"] == "1.50"
    assert data["when"].startswith("2024-01-02T03:04:05")


def test_bad_json() -> None:
    codec = JsonCodec(impl="stdlib")
    with pytest.raises(CodecError):
        codec.load(b"{")


def test_encoder_still_handles_decimal() -> None:
    text = json.dumps({"d": decimal.Decimal("2")}, cls=RobustJSONEncoder)
    assert json.loads(text) == {"d": "2"}


def test_orjson_when_installed() -> None:
    pytest.importorskip("orjson")
    codec = JsonCodec(impl="orjson")
    assert codec.load(codec.dump({"a": 1})) == {"a": 1}
    auto = JsonCodec(impl="auto")
    assert auto.load(auto.dump({"a": [1, None, True]})) == {"a": [1, None, True]}
