"""FileMap behavior: staleness, overlay, batch, proxies, atomic failure."""

from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from order_trees import FileMap, JsonCodec
from order_trees.errors import DocumentError
from order_trees.filemap import OVERLAY_POLICY
from tests.helpers import CountingCodec


def test_constructor_is_generic() -> None:
    params = list(inspect.signature(FileMap.__init__).parameters)
    assert params[:3] == ["self", "target", "codec"]
    assert set(params[3:]) <= {"reload", "lock", "write_through"}
    for banned in ("symbol", "order_id", "directory", "data", "use_cache"):
        assert banned not in params


def test_set_get_delete_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path)
    assert len(mapping) == 0
    assert not path.exists()
    mapping["name"] = "alpha"
    mapping["count"] = 3
    assert json.loads(path.read_text()) == {"name": "alpha", "count": 3}
    assert mapping["name"] == "alpha"
    del mapping["count"]
    assert "count" not in mapping
    assert json.loads(path.read_text()) == {"name": "alpha"}
    mapping.close()


def test_unchanged_lookup_does_not_reparse(tmp_path: Path) -> None:
    codec = CountingCodec()
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping["a"] = 1
    assert codec.dumps == 1
    assert codec.loads == 0
    assert mapping["a"] == 1
    assert mapping["a"] == 1
    assert codec.loads == 0
    path.write_bytes(codec.dump({"a": 2, "b": 3}))
    assert mapping["a"] == 2
    assert mapping["b"] == 3
    assert codec.loads == 1
    assert mapping["b"] == 3
    assert codec.loads == 1


def test_overlay_keeps_other_keys(tmp_path: Path) -> None:
    codec = JsonCodec(impl="stdlib")
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping.replace({"a": 1, "b": 1})
    path.write_bytes(codec.dump({"a": 1, "b": 2, "c": 3}))
    mapping["a"] = 9
    assert dict(mapping) == {"a": 9, "b": 2, "c": 3}
    assert "top-level keys" in OVERLAY_POLICY


def test_replace_overwrites_whole_document(tmp_path: Path) -> None:
    codec = JsonCodec(impl="stdlib")
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping.replace({"a": 1, "b": 1})
    path.write_bytes(codec.dump({"a": 1, "c": 4}))
    mapping.replace({"a": 2})
    assert dict(mapping) == {"a": 2}


def test_batch_writes_once_and_rolls_back(tmp_path: Path) -> None:
    codec = CountingCodec()
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    with mapping.batch():
        mapping["a"] = 1
        mapping["b"] = 2
    assert codec.dumps == 1
    assert json.loads(path.read_text()) == {"a": 1, "b": 2}

    with pytest.raises(RuntimeError, match="nope"):
        with mapping.batch():
            mapping["c"] = 3
            raise RuntimeError("nope")
    assert "c" not in mapping
    assert json.loads(path.read_text()) == {"a": 1, "b": 2}


def test_batch_overlays_external_edit(tmp_path: Path) -> None:
    codec = JsonCodec(impl="stdlib")
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping.replace({"a": 1, "b": 1})
    # Different size and inode-stable rewrite. Detection uses mtime_ns,
    # size, and inode; keep the payload visibly different.
    with mapping.batch():
        path.write_bytes(codec.dump({"a": 8, "b": 2, "extra": "from-editor"}))
        mapping["a"] = 3
    assert dict(mapping) == {"a": 3, "b": 2, "extra": "from-editor"}


def test_nested_write_through(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path)
    mapping["child"] = {"n": 1, "items": []}
    mapping["child"]["n"] = 2
    mapping["child"]["items"].append({"k": 1})
    mapping["child"]["items"][0]["k"] = 9
    on_disk = json.loads(path.read_text())
    assert on_disk["child"]["n"] == 2
    assert on_disk["child"]["items"] == [{"k": 9}]
    assert mapping["child"] == {"n": 2, "items": [{"k": 9}]}


def test_write_through_disabled_needs_reassignment(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, write_through=False)
    mapping["child"] = {"n": 1}
    child = mapping["child"]
    child["n"] = 5
    assert json.loads(path.read_text())["child"]["n"] == 1
    mapping["child"] = child
    assert json.loads(path.read_text())["child"]["n"] == 5


def test_manual_reload_waits_for_refresh(tmp_path: Path) -> None:
    codec = JsonCodec(impl="stdlib")
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec, reload="manual")
    mapping["a"] = 1
    path.write_bytes(codec.dump({"a": 2}))
    assert mapping["a"] == 1
    mapping.refresh()
    assert mapping["a"] == 2


def test_modify_updates_under_one_write(tmp_path: Path) -> None:
    codec = CountingCodec()
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping["n"] = 1
    before = codec.dumps
    assert mapping.modify("n", lambda value: value + 4) == 5
    assert mapping["n"] == 5
    assert codec.dumps == before + 1


def test_failed_replace_leaves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, JsonCodec(impl="stdlib"))
    mapping["a"] = 1

    def boom(src, dst):
        raise RuntimeError("crash before rename")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError, match="crash"):
        mapping["b"] = 2
    assert json.loads(path.read_text()) == {"a": 1}
    assert dict(mapping) == {"a": 1}
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


def test_fsync_before_rename_then_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def replace(src, dst) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    mapping = FileMap(tmp_path / "state.json")
    mapping["a"] = 1
    assert events[:3] == ["fsync", "replace", "fsync"]


def test_file_object_name_is_a_target(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    handle = path.open("wb")
    try:
        mapping = FileMap(handle)
        mapping["a"] = 1
    finally:
        handle.close()
    assert json.loads(path.read_text()) == {"a": 1}


def test_root_must_be_object(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2]")
    mapping = FileMap(path)
    with pytest.raises(DocumentError):
        mapping["a"]


def test_directory_target_is_rejected(tmp_path: Path) -> None:
    mapping = FileMap(tmp_path)
    with pytest.raises(IsADirectoryError):
        mapping["a"] = 1


def test_update_is_one_write(tmp_path: Path) -> None:
    codec = CountingCodec()
    mapping = FileMap(tmp_path / "state.json", codec)
    mapping.update({"a": 1, "b": 2})
    assert codec.dumps == 1
    assert dict(mapping) == {"a": 1, "b": 2}


def test_non_string_key_rejected(tmp_path: Path) -> None:
    mapping = FileMap(tmp_path / "state.json")
    with pytest.raises(TypeError):
        mapping[1] = "no"  # type: ignore[index]
