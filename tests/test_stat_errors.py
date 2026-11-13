"""A stat failure is not a missing file.

``Path.is_file()`` treats some ``OSError``s as "not a file". ``PermissionError``
must stay a ``PermissionError``. Only a path that is not there is ``Gone``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from order_trees import FileMap, create_cache_for_directory
from order_trees.errors import Gone


def test_unreadable_directory_is_permission_error_not_gone(tmp_path: Path) -> None:
    directory = tmp_path / "meta"
    directory.mkdir()
    path = directory / "BTCUSD_BinaryTader.json"
    path.write_text(json.dumps({"_id": "BTCUSD_BinaryTader", "dt": "x"}))
    kind = create_cache_for_directory(directory, use_cache=False)
    os.chmod(directory, 0)
    try:
        with pytest.raises(PermissionError):
            "dt" in FileMap(path)
        with pytest.raises(PermissionError):
            kind.get_or_create("BTCUSD_BinaryTader")
    finally:
        os.chmod(directory, 0o755)


def test_get_or_create_writes_a_missing_meta_file(tmp_path: Path) -> None:
    kind = create_cache_for_directory(tmp_path / "meta", use_cache=False)
    made = kind.get_or_create("BTCUSD_BinaryTader")
    path = tmp_path / "meta" / "BTCUSD_BinaryTader.json"
    assert path.is_file()
    assert json.loads(path.read_text()) == {"_id": "BTCUSD_BinaryTader"}
    # The notebook does `'dt' not in self.metadata`, and metadata reloads.
    again = made.from_id("BTCUSD_BinaryTader")
    assert again is not made
    assert "dt" not in again
    again["dt"] = "1970-01-01"
    assert json.loads(path.read_text())["dt"] == "1970-01-01"


def test_missing_file_is_still_gone(tmp_path: Path) -> None:
    mapping = FileMap(tmp_path / "absent.json")
    with pytest.raises(Gone):
        "dt" in mapping
