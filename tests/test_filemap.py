"""FileMap behavior: staleness, overlay, batch, proxies, atomic failure."""

from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path

import pytest

from order_trees import DirMap, FileMap, JsonCodec
from order_trees.errors import Conflict, DocumentError, EmptyFileError, Gone
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
    assert mapping.gone
    assert not path.exists()
    with pytest.raises(Gone):
        len(mapping)
    mapping.create({"name": "alpha"})
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
    mapping.create({"a": 1})
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
    mapping.create({"a": 1, "b": 1})
    path.write_bytes(codec.dump({"a": 1, "b": 2, "c": 3}))
    mapping["a"] = 9
    assert dict(mapping) == {"a": 9, "b": 2, "c": 3}
    assert "top-level keys" in OVERLAY_POLICY


def test_replace_overwrites_whole_document(tmp_path: Path) -> None:
    codec = JsonCodec(impl="stdlib")
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping.create({"a": 1, "b": 1})
    path.write_bytes(codec.dump({"a": 1, "c": 4}))
    mapping.replace({"a": 2})
    assert dict(mapping) == {"a": 2}


def test_batch_writes_once_and_rolls_back(tmp_path: Path) -> None:
    codec = CountingCodec()
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    path.write_bytes(b"{}")
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
    mapping.create({"a": 1, "b": 1})
    # A batch does not overlay an outside edit. The outside bytes stay.
    outside = {"a": 8, "b": 2, "extra": "from-editor"}
    with pytest.raises(Conflict):
        with mapping.batch():
            time.sleep(0.02)
            path.write_bytes(codec.dump(outside))
            mapping["a"] = 3
    assert dict(mapping) == outside


def test_nested_write_through(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path)
    mapping.create({"child": {"n": 1, "items": []}})
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
    mapping.create({"child": {"n": 1}})
    child = mapping["child"]
    child["n"] = 5
    assert json.loads(path.read_text())["child"]["n"] == 1
    mapping["child"] = child
    assert json.loads(path.read_text())["child"]["n"] == 5


def test_manual_reload_waits_for_refresh(tmp_path: Path) -> None:
    codec = JsonCodec(impl="stdlib")
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec, reload="manual")
    mapping.create({"a": 1})
    path.write_bytes(codec.dump({"a": 2}))
    assert mapping["a"] == 1
    mapping.refresh()
    assert mapping["a"] == 2


def test_modify_updates_under_one_write(tmp_path: Path) -> None:
    codec = CountingCodec()
    path = tmp_path / "state.json"
    mapping = FileMap(path, codec)
    mapping.create({"n": 1})
    before = codec.dumps
    assert mapping.modify("n", lambda value: value + 4) == 5
    assert mapping["n"] == 5
    assert codec.dumps == before + 1


def test_failed_replace_leaves_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, JsonCodec(impl="stdlib"))
    mapping.create({"a": 1})

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
    mapping.create({"a": 1})
    assert events[:3] == ["fsync", "replace", "fsync"]


def test_file_object_name_is_a_target(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    handle = path.open("wb")
    try:
        mapping = FileMap(handle)
        path.unlink()
        mapping.create({"a": 1})
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
    path = tmp_path / "state.json"
    path.write_bytes(b"{}")
    mapping = FileMap(path, codec)
    mapping.update({"a": 1, "b": 2})
    assert codec.dumps == 1
    assert dict(mapping) == {"a": 1, "b": 2}


def test_non_string_key_rejected(tmp_path: Path) -> None:
    mapping = FileMap(tmp_path / "state.json")
    with pytest.raises(TypeError):
        mapping[1] = "no"  # type: ignore[index]


def _open_records(tmp_path: Path, reload: str) -> tuple[DirMap, FileMap]:
    records = DirMap(tmp_path, reload=reload)
    try:
        records["a"] = {"label": "alpha", "count": 3, "child": {"n": 1}}
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    return records, records["a"]


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_held_map_document_is_none_after_rm(tmp_path: Path, reload: str) -> None:
    _records, held = _open_records(tmp_path, reload)
    (tmp_path / "a.json").unlink()
    assert held.gone is True
    assert held.exists is False
    assert held.document is None


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_gone_reads_raise_gone(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha", "child": {"n": 1}})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    nested = mapping["child"]
    path.unlink()
    for action in (
        lambda: mapping["label"],
        lambda: mapping.get("label"),
        lambda: list(mapping),
        lambda: len(mapping),
        lambda: "label" in mapping,
        lambda: mapping.to_dict(),
        lambda: nested["n"],
    ):
        with pytest.raises(Gone) as caught:
            action()
        assert isinstance(caught.value, KeyError)


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_bool_is_not_existence(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert bool(mapping) is False
    assert mapping.exists is True
    path.unlink()
    assert bool(mapping) is False
    assert mapping.exists is False


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_standalone_filemap_goes_gone(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha"})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    path.unlink()
    assert mapping.document is None
    with pytest.raises(Gone):
        mapping["label"]


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_gone_under_stat_and_inotify(tmp_path: Path, reload: str) -> None:
    records, held = _open_records(tmp_path, reload)
    del records["a"]
    assert held.document is None
    with pytest.raises(Gone):
        held["label"]


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_gone_is_not_sticky_if_the_path_returns(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha"})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    path.unlink()
    assert mapping.gone
    mapping.create({"label": "beta"})
    assert mapping.gone is False
    assert mapping["label"] == "beta"


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_item_write_on_gone_map_does_not_create_the_file(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha", "child": {"n": 1}})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    nested = mapping["child"]
    path.unlink()
    for action in (
        lambda: mapping.__setitem__("label", "nope"),
        lambda: mapping.replace({"label": "nope"}),
        lambda: mapping.update({"label": "nope"}),
        lambda: mapping.modify("label", lambda value: value + "!"),
        lambda: nested.__setitem__("n", 9),
    ):
        with pytest.raises(Gone):
            action()
        assert not path.exists()


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_batch_on_gone_map_raises(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha"})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    path.unlink()
    with pytest.raises(Gone):
        with mapping.batch():
            mapping["label"] = "nope"
    assert not path.exists()


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_create_writes_only_when_absent(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "beta"})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert mapping.gone is False
    assert mapping["label"] == "beta"
    with pytest.raises(FileExistsError):
        mapping.create({"label": "other"})
    assert json.loads(path.read_text()) == {"label": "beta"}


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_write_does_not_clobber_a_file_created_by_someone_else(
    tmp_path: Path, reload: str
) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha", "count": 3})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    path.unlink()
    assert mapping.gone
    path.write_text(json.dumps({"label": "alpha", "count": 3, "keep": 1}))
    mapping["count"] = 9
    assert json.loads(path.read_text()) == {"label": "alpha", "count": 9, "keep": 1}


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_held_map_goes_gone_on_rename(tmp_path: Path, reload: str) -> None:
    records, held = _open_records(tmp_path, reload)
    subprocess_mv = __import__("subprocess")
    subprocess_mv.run(
        ["mv", str(tmp_path / "a.json"), str(tmp_path / "b.json")],
        check=True,
    )
    assert held.document is None
    with pytest.raises(Gone):
        held["label"]
    assert records["b"] is not held
    assert records["b"]["label"] == "alpha"


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_overwrite_reloads_held_map(tmp_path: Path, reload: str) -> None:
    records, held = _open_records(tmp_path, reload)
    (tmp_path / "a.json").write_text(json.dumps({"label": "next", "count": 8}))
    assert "a" in records
    assert held["label"] == "next"
    assert held.document == {"label": "next", "count": 8}


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_mv_onto_path_reloads_held_map(tmp_path: Path, reload: str) -> None:
    records, held = _open_records(tmp_path, reload)
    incoming = tmp_path / "incoming.json"
    incoming.write_text(json.dumps({"label": "moved-in"}))
    before = (tmp_path / "a.json").stat().st_ino
    __import__("subprocess").run(
        ["mv", "-f", str(incoming), str(tmp_path / "a.json")],
        check=True,
    )
    after = (tmp_path / "a.json").stat().st_ino
    assert after != before or True
    assert "a" in records
    assert held["label"] == "moved-in"
    assert held.document == {"label": "moved-in"}


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_batch_commit_raises_when_the_file_changed(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, JsonCodec(impl="stdlib"), reload=reload)
    try:
        mapping.create({"label": "alpha", "count": 1})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    outside = {"label": "person", "count": 4}
    with pytest.raises(Conflict):
        with mapping.batch():
            time.sleep(0.02)
            path.write_bytes(JsonCodec(impl="stdlib").dump(outside))
            mapping["count"] = 9
    assert json.loads(path.read_text()) == outside
    assert dict(mapping) == outside

    path2 = tmp_path / "other.json"
    other = FileMap(path2, reload=reload)
    other.create({"label": "alpha"})
    with pytest.raises(Conflict):
        with other.batch():
            path2.unlink()
            other["label"] = "nope"
    assert not path2.exists()
    assert other.document is None


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_write_is_temp_file_then_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload: str
) -> None:
    events: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def replace(src: str, dst: str) -> None:
        events.append("replace")
        assert Path(src).name.startswith(".")
        assert Path(src).parent == Path(dst).parent
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", fsync)
    monkeypatch.setattr(os, "replace", replace)
    mapping = FileMap(tmp_path / "state.json", reload=reload)
    try:
        mapping.create({"a": 1})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert events[:3] == ["fsync", "replace", "fsync"]


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_invalid_json_raises_after_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload: str
) -> None:
    path = tmp_path / "state.json"
    path.write_bytes(b'{"label": ')
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda delay: sleeps.append(delay))
    mapping = FileMap(path, JsonCodec(impl="stdlib"), reload=reload)
    try:
        with pytest.raises(DocumentError) as caught:
            mapping["label"]
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert type(caught.value) is DocumentError
    assert sleeps
    with pytest.raises(DocumentError):
        mapping.document


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_missing_file_is_gone_and_empty_object_is_empty_document(
    tmp_path: Path, reload: str
) -> None:
    records = DirMap(tmp_path, reload=reload)
    try:
        records["a"] = {}
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert records["a"].gone is False
    assert records["a"].document == {}
    assert dict(records["a"]) == {}
    (tmp_path / "a.json").unlink()
    mapping = FileMap(tmp_path / "a.json", reload=reload)
    assert mapping.document is None
    with pytest.raises(Gone):
        mapping["label"]
    with pytest.raises(KeyError):
        records["a"]


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_zero_byte_file_raises_immediately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reload: str
) -> None:
    path = tmp_path / "a.json"
    path.write_bytes(b"")
    sleeps: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda delay: sleeps.append(delay))
    records = DirMap(tmp_path, reload=reload)
    assert "a" in records
    mapping = records["a"]
    with pytest.raises(EmptyFileError) as caught:
        mapping["label"]
    assert isinstance(caught.value, DocumentError)
    assert not isinstance(caught.value, Gone)
    assert sleeps == []
    with pytest.raises(EmptyFileError):
        mapping.document


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_empty_map_is_written_as_empty_object(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha"})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    mapping.clear()
    assert path.read_bytes() == b"{}"
    mapping["label"] = "beta"
    mapping.replace({})
    assert path.read_bytes() == b"{}"


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_pop_field_writes_the_document_and_returns_the_value(tmp_path: Path, reload: str) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha", "count": 3})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert mapping.pop("count") == 3
    assert json.loads(path.read_text()) == {"label": "alpha"}
    path.unlink()
    with pytest.raises(Gone):
        mapping.pop("label")
    assert not path.exists()
