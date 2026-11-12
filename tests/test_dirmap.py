"""Directory-of-files mapping."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from order_trees import DirMap, symbol_order_dir, stage_order
from order_trees.atomic import atomic_write


def test_key_is_filename(tmp_path: Path) -> None:
    book = DirMap(tmp_path)
    book["a"] = {"symbol": "AAPL", "amount": 1}
    assert (tmp_path / "a.json").is_file()
    assert book["a"]["symbol"] == "AAPL"
    book["a"]["amount"] = 4
    assert json.loads((tmp_path / "a.json").read_text())["amount"] == 4
    assert set(book) == {"a"}
    del book["a"]
    assert "a" not in book
    assert not (tmp_path / "a.json").exists()


def test_ignores_subdirs_dotfiles_and_lock(tmp_path: Path) -> None:
    book = symbol_order_dir(tmp_path, "AAPL")
    (book.path / "completed" / "gone.json").write_text("{}")
    (book.path / ".hidden.json").write_text("{}")
    (book.path / ".order_trees.lock").write_text("")
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


def test_stage_order_helper(tmp_path: Path) -> None:
    book = symbol_order_dir(tmp_path / "orders", "MSFT")
    id_ = stage_order(book, "MSFT", 2, 50, "sell", account="DU1")
    assert (book.path / f"{id_}.json").is_file()
    assert book[id_]["symbol"] == "MSFT"
    assert book[id_]["remaining_amount"] == 2
    assert (book.path / "completed").is_dir()


def test_not_a_directory(tmp_path: Path) -> None:
    path = tmp_path / "file"
    path.write_text("x")
    with pytest.raises(NotADirectoryError):
        DirMap(path)
