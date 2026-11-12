"""Turn proxies and nested containers into plain JSON-able objects."""

from __future__ import annotations

from typing import Any


def plain(value: Any) -> Any:
    """Copy *value* into plain dicts and lists.

    Proxies and ``FileMap`` instances are unwrapped so the document stored
    in memory never points back at a live view.
    """
    kind = type(value).__name__
    if kind in {"DictProxy", "ListProxy"}:
        return plain(value._resolve())
    if kind == "FileMap":
        return plain(value.to_dict())
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    return value
