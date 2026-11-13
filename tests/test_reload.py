"""Stat stays on the hot path. inotify does not stat when nothing happened."""

from __future__ import annotations

from pathlib import Path

import pytest

from order_trees import FileMap
from order_trees.reload import file_signature


def test_stat_strategy_stats_every_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload="stat")
    mapping.create({"a": 1})
    calls = {"n": 0}
    real = file_signature

    def wrapped(target: Path):
        calls["n"] += 1
        return real(target)

    monkeypatch.setattr("order_trees.reload.file_signature", wrapped)
    for _ in range(5):
        mapping["a"]
    assert calls["n"] >= 5


def test_inotify_skips_stat_when_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "state.json"
    try:
        mapping = FileMap(path, reload="inotify")
    except OSError as exc:
        pytest.skip(f"inotify unavailable: {exc}")
    mapping.create({"a": 1})
    assert mapping["a"] == 1
    calls = {"n": 0}
    real = file_signature

    def wrapped(target: Path):
        calls["n"] += 1
        return real(target)

    monkeypatch.setattr("order_trees.reload.file_signature", wrapped)
    for _ in range(20):
        assert mapping["a"] == 1
    assert calls["n"] == 0
    mapping.close()
