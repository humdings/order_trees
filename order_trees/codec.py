"""Pluggable document codecs.

JSON is the default. ``orjson`` is used when it is installed and *impl* is
``"auto"`` or ``"orjson"``. The standard-library codec is always available.

``Decimal`` and ``datetime`` values are written as strings. They come back
as strings. A pandas ``Timestamp`` is written with ``str(timestamp)`` when
one is passed in, without importing pandas.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import json
from typing import Any, Protocol


class Codec(Protocol):
    """Read and write one document as bytes."""

    def load(self, data: bytes) -> Any:
        """Parse *data*."""

    def dump(self, obj: Any) -> bytes:
        """Serialize *obj*."""


def _json_default(obj: Any) -> Any:
    if isinstance(obj, decimal.Decimal):
        return str(obj)
    if isinstance(obj, _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    if type(obj).__name__ == "Timestamp":
        return str(obj)
    raise TypeError(f"cannot encode {type(obj).__name__}")


class JsonCodec:
    """JSON object codec.

    *impl* is ``"auto"`` (orjson if importable, otherwise the stdlib),
    ``"orjson"``, or ``"stdlib"``.
    """

    suffix = ".json"

    def __init__(self, impl: str = "auto") -> None:
        if impl not in {"auto", "orjson", "stdlib"}:
            raise ValueError(f"unknown json impl {impl!r}")
        self.impl = impl
        self._orjson: Any = None
        if impl == "stdlib":
            return
        try:
            import orjson
        except ImportError:
            if impl == "orjson":
                raise
            orjson = None
        self._orjson = orjson

    def load(self, data: bytes) -> Any:
        from order_trees.errors import CodecError

        try:
            if self._orjson is not None:
                return self._orjson.loads(data)
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise CodecError(str(exc)) from exc

    def dump(self, obj: Any) -> bytes:
        from order_trees.errors import CodecError

        try:
            if self._orjson is not None:
                return self._orjson.dumps(obj, default=_json_default)
            text = json.dumps(obj, default=_json_default, separators=(",", ":"))
            return text.encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise CodecError(str(exc)) from exc


class RobustJSONEncoder(json.JSONEncoder):
    """Stdlib encoder kept for callers that used to import it.

    ``Decimal`` and pandas ``Timestamp`` become strings, as they did before.
    """

    def default(self, o: Any) -> Any:
        try:
            return _json_default(o)
        except TypeError:
            return super().default(o)
