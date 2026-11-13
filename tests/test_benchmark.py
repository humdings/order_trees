"""Benchmark both lookup paths. Correctness of the fast path is the load count."""

from __future__ import annotations

from pathlib import Path

import pytest

from order_trees import FileMap
from order_trees.benchmark import bench_changed, bench_unchanged
from tests.helpers import CountingCodec


def test_stat_benchmark_paths(tmp_path: Path) -> None:
    codec = CountingCodec()
    path = tmp_path / "bench.json"
    mapping = FileMap(path, codec, reload="stat")
    mapping.create({"k": 1, "pad": "x" * 32})
    # Writes re-read the file. Lookups of an unchanged file must not.
    baseline = codec.loads
    for _ in range(800):
        mapping["k"]
    assert codec.loads == baseline
    mapping.close()

    unchanged = bench_unchanged(path, "stat", rounds=800, codec=codec)
    assert unchanged["ns_per_lookup"] < 1_000_000
    loads_after_unchanged = codec.loads

    changed = bench_changed(path, "stat", rounds=40, codec=codec)
    assert codec.loads - loads_after_unchanged >= 40
    assert changed["ns_per_lookup"] > 0
    assert changed["ns_per_lookup"] < 50_000_000


def test_inotify_benchmark_paths(tmp_path: Path) -> None:
    path = tmp_path / "bench.json"
    try:
        unchanged = bench_unchanged(path, "inotify", rounds=400)
    except OSError as exc:
        pytest.skip(f"inotify unavailable: {exc}")
    changed = bench_changed(path, "inotify", rounds=20)
    assert unchanged["ns_per_lookup"] < 1_000_000
    assert changed["ns_per_lookup"] > 0
