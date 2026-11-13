"""Directory-of-files mapping."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from order_trees import DirMap
from order_trees.atomic import atomic_write
from order_trees.errors import Gone
from order_trees.reload import file_signature


def test_key_is_filename(tmp_path: Path) -> None:
    book = DirMap(tmp_path)
    book["a"] = {"name": "alpha", "count": 1}
    assert (tmp_path / "a.json").is_file()
    assert book["a"]["name"] == "alpha"
    book["a"]["count"] = 4
    assert json.loads((tmp_path / "a.json").read_text())["count"] == 4
    assert set(book) == {"a"}
    del book["a"]
    assert "a" not in book
    assert not (tmp_path / "a.json").exists()


def test_ignores_subdirs_dotfiles_and_lock(tmp_path: Path) -> None:
    book = DirMap(tmp_path)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "gone.json").write_text("{}")
    (tmp_path / ".hidden.json").write_text("{}")
    (tmp_path / ".dirmap.lock").write_text("")
    book["live"] = {"n": 1}
    assert list(book) == ["live"]


def test_external_file_shows_up(tmp_path: Path) -> None:
    book = DirMap(tmp_path)
    book["a"] = {"n": 1}
    (tmp_path / "b.json").write_text(json.dumps({"n": 2}))
    assert "b" in book
    assert book["b"]["n"] == 2
    (tmp_path / "a.json").write_text(json.dumps({"n": 9}))
    assert book["a"]["n"] == 9


def test_batch_one_write_per_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []
    real = atomic_write

    def wrapped(path, payload):
        writes.append(Path(path).name)
        return real(path, payload)

    monkeypatch.setattr("order_trees.filemap.atomic_write", wrapped)
    book = DirMap(tmp_path)
    with book.batch():
        book["a"] = {"n": 1}
        book["a"] = {"n": 2}
        book["b"] = {"n": 3}
    assert writes.count("a.json") == 1
    assert writes.count("b.json") == 1
    assert book["a"]["n"] == 2
    assert book["b"]["n"] == 3


def test_batch_field_edits_one_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = DirMap(tmp_path)
    book["a"] = {"n": 0}
    writes: list[str] = []
    real = atomic_write

    def wrapped(path, payload):
        writes.append(Path(path).name)
        return real(path, payload)

    monkeypatch.setattr("order_trees.filemap.atomic_write", wrapped)
    with book.batch():
        book["a"]["n"] = 1
        book["a"]["m"] = 2
    assert writes == ["a.json"]
    assert dict(book["a"]) == {"n": 1, "m": 2}


def test_batch_rollback(tmp_path: Path) -> None:
    book = DirMap(tmp_path)
    book["a"] = {"n": 1}
    with pytest.raises(RuntimeError):
        with book.batch():
            book["b"] = {"n": 2}
            raise RuntimeError("stop")
    assert list(book) == ["a"]
    assert not (tmp_path / "b.json").exists()


def test_file_batch_on_one_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    book = DirMap(tmp_path)
    book["a"] = {"n": 0, "m": 0}
    writes: list[str] = []
    real = atomic_write

    def wrapped(path, payload):
        writes.append(Path(path).name)
        return real(path, payload)

    monkeypatch.setattr("order_trees.filemap.atomic_write", wrapped)
    with book["a"].batch():
        book["a"]["n"] = 1
        book["a"]["m"] = 2
    assert writes == ["a.json"]
    assert book["a"]["n"] == 1


def test_bad_keys(tmp_path: Path) -> None:
    book = DirMap(tmp_path)
    with pytest.raises(ValueError):
        book["../x"] = {"n": 1}
    with pytest.raises(ValueError):
        book[".secret"] = {"n": 1}
    with pytest.raises(TypeError):
        book["a"] = "nope"  # type: ignore[assignment]


def test_not_a_directory(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("x")
    with pytest.raises(NotADirectoryError):
        DirMap(path)


def _records(tmp_path: Path, reload: str, **kwargs: object) -> DirMap:
    try:
        book = DirMap(tmp_path, reload=reload, **kwargs)  # type: ignore[arg-type]
        book["a"] = {"label": "alpha", "count": 3}
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    return book


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_external_rm_drops_key(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    subprocess.run(["rm", "-f", str(tmp_path / "a.json")], check=True)
    assert "a" not in records
    assert list(records) == []
    assert len(records) == 0
    with pytest.raises(KeyError) as caught:
        records["a"]
    assert type(caught.value) is KeyError
    assert records.get("a") is None
    assert not (tmp_path / "a.json").exists()


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_delete_through_map_matches_external_rm(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    del records["a"]
    assert "a" not in records
    assert list(records) == []
    assert len(records) == 0
    with pytest.raises(KeyError) as caught:
        records["a"]
    assert type(caught.value) is KeyError
    assert records.get("a") is None
    assert not (tmp_path / "a.json").exists()


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_other_process_delete_drops_key(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    subprocess.run(
        [sys.executable, "-c", "import os,sys; os.remove(sys.argv[1])", str(tmp_path / "a.json")],
        check=True,
    )
    assert "a" not in records
    assert list(records.keys()) == []
    assert len(records) == 0
    with pytest.raises(KeyError):
        records["a"]
    assert records.get("a") is None


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_opened_key_is_not_cached_as_a_member(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    held = records["a"]
    assert held["label"] == "alpha"
    subprocess.run(["rm", "-f", str(tmp_path / "a.json")], check=True)
    with pytest.raises(KeyError) as caught:
        records["a"]
    assert type(caught.value) is KeyError
    assert held.gone


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_assignment_recreates_a_missing_key(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    held = records["a"]
    subprocess.run(["rm", "-f", str(tmp_path / "a.json")], check=True)
    records["a"] = {"label": "beta"}
    assert "a" in records
    assert records["a"]["label"] == "beta"
    assert held["label"] == "beta"
    assert json.loads((tmp_path / "a.json").read_text())["label"] == "beta"


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_rename_drops_old_key_and_adds_new_key(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    subprocess.run(["mv", str(tmp_path / "a.json"), str(tmp_path / "b.json")], check=True)
    assert "a" not in records
    assert "b" in records
    assert records["b"]["label"] == "alpha"


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_same_path_replacement_keeps_the_key(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    held = records["a"]
    (tmp_path / "a.json").write_text(json.dumps({"label": "shell", "count": 1}))
    assert "a" in records
    assert records["a"] is held
    assert held["label"] == "shell"
    incoming = tmp_path / "incoming.json"
    incoming.write_text(json.dumps({"label": "moved", "count": 4}))
    subprocess.run(["mv", "-f", str(incoming), str(tmp_path / "a.json")], check=True)
    assert "a" in records
    assert records["a"] is held
    assert held["label"] == "moved"


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_delete_in_one_timestamp_tick_drops_the_key(
    tmp_path: Path, reload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _records(tmp_path, reload)
    frozen = file_signature(tmp_path)
    real = file_signature

    def wrapped(path: Path):
        if Path(path) == tmp_path:
            return frozen
        return real(path)

    monkeypatch.setattr("order_trees.dirmap.file_signature", wrapped)
    monkeypatch.setattr("order_trees.reload.file_signature", wrapped)
    os.remove(tmp_path / "a.json")
    assert "a" not in records
    assert list(records) == []
    assert len(records) == 0


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_create_in_one_timestamp_tick_adds_the_key(
    tmp_path: Path, reload: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = _records(tmp_path, reload)
    frozen = file_signature(tmp_path)
    real = file_signature

    def wrapped(path: Path):
        if Path(path) == tmp_path:
            return frozen
        return real(path)

    monkeypatch.setattr("order_trees.dirmap.file_signature", wrapped)
    monkeypatch.setattr("order_trees.reload.file_signature", wrapped)
    (tmp_path / "b.json").write_text(json.dumps({"label": "bee"}))
    assert "b" in list(records)


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_contains_stats_the_file(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    assert "a" in records
    os.remove(tmp_path / "a.json")
    assert "a" not in records


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_pop_returns_a_dict_copy_and_drops_the_key(tmp_path: Path, reload: str) -> None:
    records = _records(tmp_path, reload)
    held = records["a"]
    popped = records.pop("a")
    assert type(popped) is dict
    assert popped == {"label": "alpha", "count": 3}
    popped["label"] = "edited"
    assert not (tmp_path / "a.json").exists()
    assert "a" not in records
    assert held.document is None
    with pytest.raises(Gone):
        held["label"]


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_on_remove_fires_once_per_disappeared_key(tmp_path: Path, reload: str) -> None:
    quiet = _records(tmp_path / "quiet", reload)
    subprocess.run(["rm", "-f", str(quiet.path / "a.json")], check=True)
    assert list(quiet) == []

    seen: list[tuple[str, object]] = []

    def on_remove(key: str, last: object) -> None:
        seen.append((key, last))

    loaded = DirMap(tmp_path / "loaded", reload=reload, on_remove=on_remove)
    try:
        loaded["a"] = {"label": "alpha"}
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    assert loaded["a"]["label"] == "alpha"
    subprocess.run(["rm", "-f", str(loaded.path / "a.json")], check=True)
    assert list(loaded) == []
    assert seen == [("a", {"label": "alpha"})]
    assert list(loaded) == []
    assert seen == [("a", {"label": "alpha"})]

    names: list[tuple[str, object]] = []
    named = DirMap(tmp_path / "names", reload=reload, on_remove=lambda key, last: names.append((key, last)))
    (named.path).mkdir(parents=True, exist_ok=True)
    (named.path / "a.json").write_text(json.dumps({"label": "alpha"}))
    assert list(named) == ["a"]
    os.remove(named.path / "a.json")
    assert list(named) == []
    assert names == [("a", None)]

    removed: list[str] = []
    explicit = DirMap(tmp_path / "explicit", reload=reload, on_remove=lambda key, last: removed.append(key))
    explicit["a"] = {"label": "alpha", "count": 1}
    explicit["b"] = {"label": "beta"}
    del explicit["a"]
    explicit.pop("b")
    assert removed == ["a", "b"]
    explicit["a"] = {"label": "again"}
    del explicit["a"]
    assert removed == ["a", "b", "a"]

    replaced: list[str] = []
    stable = DirMap(tmp_path / "stable", reload=reload, on_remove=lambda key, last: replaced.append(key))
    stable["a"] = {"label": "alpha"}
    time.sleep(0.02)
    (stable.path / "a.json").write_text(json.dumps({"label": "next", "extra": "visible"}))
    assert list(stable) == ["a"]
    assert stable["a"]["label"] == "next"
    assert replaced == []

    def boom(key: str, last: object) -> None:
        raise RuntimeError(f"stop {key}")

    failing = DirMap(tmp_path / "failing", reload=reload, on_remove=boom)
    failing["a"] = {"label": "alpha"}
    os.remove(failing.path / "a.json")
    with pytest.raises(RuntimeError, match="stop a"):
        list(failing)
    assert "a" not in failing
