"""Lookup latency for a file that did not change, and one that did.

Run ``python -m order_trees.benchmark``. The unchanged path is a memory
hit after a cheap staleness check. The changed path includes that check
plus a parse. The rewrite itself is not included in the changed-path time.
"""

from __future__ import annotations

import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from order_trees.codec import JsonCodec
from order_trees.filemap import FileMap


def _median_ns(samples: list[int]) -> float:
    return float(statistics.median(samples)) if samples else 0.0


def bench_unchanged(
    path: Path,
    reload: Any,
    rounds: int = 4000,
    codec: Any = None,
) -> dict[str, Any]:
    m = FileMap(path, codec, reload=reload)
    m["k"] = 1
    m["pad"] = "x" * 32
    for _ in range(50):
        m["k"]
    start = time.perf_counter_ns()
    for _ in range(rounds):
        m["k"]
    elapsed = time.perf_counter_ns() - start
    m.close()
    return {
        "reload": reload if isinstance(reload, str) or reload is None else type(reload).__name__,
        "path": "unchanged",
        "rounds": rounds,
        "ns_per_lookup": elapsed / rounds,
    }


def bench_changed(
    path: Path,
    reload: Any,
    rounds: int = 400,
    codec: Any = None,
) -> dict[str, Any]:
    codec = codec if codec is not None else JsonCodec()
    m = FileMap(path, codec, reload=reload)
    m["k"] = 0
    blobs = [codec.dump({"k": i, "pad": "x" * 32}) for i in range(2)]
    # Warm the watcher so the first measured lookup is not the initial load.
    tmp = path.with_name(".bench-warm.tmp")
    tmp.write_bytes(blobs[1])
    os.replace(tmp, path)
    deadline = time.perf_counter() + 2.0
    while m["k"] != 1 and time.perf_counter() < deadline:
        time.sleep(0.01)
    samples: list[int] = []
    for i in range(rounds):
        blob = blobs[i % 2]
        tmp = path.with_name(f".bench-{i}.tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, path)
        start = time.perf_counter_ns()
        value = m["k"]
        samples.append(time.perf_counter_ns() - start)
        if value != i % 2:
            # watchfiles can lag a step; wait once so the sample still counts
            # a lookup that observed the new bytes.
            found = value
            wait_until = time.perf_counter() + 1.0
            while found != i % 2 and time.perf_counter() < wait_until:
                time.sleep(0.01)
                start = time.perf_counter_ns()
                found = m["k"]
                samples[-1] = time.perf_counter_ns() - start
            if found != i % 2:
                raise AssertionError(f"lookup missed change: got {found}, want {i % 2}")
    m.close()
    return {
        "reload": reload if isinstance(reload, str) or reload is None else type(reload).__name__,
        "path": "changed",
        "rounds": rounds,
        "ns_per_lookup": _median_ns(samples),
    }


def run(
    strategies: Optional[list[Any]] = None,
    unchanged_rounds: int = 4000,
    changed_rounds: int = 300,
) -> list[dict[str, Any]]:
    if strategies is None:
        strategies = ["stat", "inotify"]
    rows = []
    for spec in strategies:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bench.json"
            try:
                rows.append(bench_unchanged(path, spec, unchanged_rounds))
                rows.append(bench_changed(path, spec, changed_rounds))
            except OSError as exc:
                rows.append(
                    {
                        "reload": spec,
                        "path": "unavailable",
                        "rounds": 0,
                        "ns_per_lookup": None,
                        "error": str(exc),
                    }
                )
    return rows


def format_rows(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'reload':<12} {'path':<12} {'ns/lookup':>12} {'rounds':>8}"]
    for row in rows:
        ns = row.get("ns_per_lookup")
        shown = f"{ns:,.0f}" if isinstance(ns, (int, float)) else str(row.get("error", ""))
        lines.append(
            f"{str(row['reload']):<12} {str(row['path']):<12} {shown:>12} {row['rounds']:>8}"
        )
    return "\n".join(lines)


def main() -> None:
    rows = run()
    print(format_rows(rows))


if __name__ == "__main__":
    main()
