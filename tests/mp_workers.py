"""Process entry points. Kept importable so spawn can find them."""

from __future__ import annotations


def set_key(path: str, key: str, value: object) -> None:
    from order_trees import FileMap

    FileMap(path)[key] = value


def bump(path: str, times: int) -> None:
    from order_trees import FileMap

    mapping = FileMap(path)
    for _ in range(times):
        mapping.modify("n", lambda value: value + 1, default=0)
    mapping.close()


def spin_write(path: str) -> None:
    from order_trees import FileMap

    mapping = FileMap(path)
    index = 0
    while True:
        mapping["v"] = "y" * (64 + (index % 17))
        mapping["i"] = index
        index += 1


def replace_file(path: str, payload: bytes) -> None:
    import os
    from pathlib import Path

    target = Path(path)
    tmp = target.with_name(f".editor-{os.getpid()}.tmp")
    tmp.write_bytes(payload)
    os.replace(tmp, target)
