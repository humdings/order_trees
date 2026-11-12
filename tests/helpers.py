"""Small test doubles."""

from __future__ import annotations

import json
from typing import Any

from order_trees.codec import JsonCodec


class CountingCodec:
    suffix = ".json"

    def __init__(self) -> None:
        self.loads = 0
        self.dumps = 0
        self.inner = JsonCodec(impl="stdlib")

    def load(self, data: bytes) -> Any:
        self.loads += 1
        return self.inner.load(data)

    def dump(self, obj: Any) -> bytes:
        self.dumps += 1
        return json.dumps(obj).encode("utf-8")
