"""Other processes, concurrent writers, and a kill during write."""

from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from order_trees import FileMap, JsonCodec
from order_trees.errors import Conflict, Gone
from order_trees.reload import InotifyReload
from tests.mp_workers import bump, replace_file, set_key, spin_write


def _spawn():
    return multiprocessing.get_context("spawn")


def test_external_process_rewrite_is_visible(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    codec = JsonCodec(impl="stdlib")
    mapping = FileMap(path, codec)
    mapping.create({"a": 1})
    payload = codec.dump({"a": 2, "b": 3})
    proc = _spawn().Process(target=replace_file, args=(str(path), payload))
    proc.start()
    proc.join(10)
    assert proc.exitcode == 0
    assert mapping["a"] == 2
    assert mapping["b"] == 3


def test_inotify_sees_external_rewrite(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    try:
        InotifyReload(path)
    except OSError as exc:
        pytest.skip(f"inotify unavailable: {exc}")
    codec = JsonCodec(impl="stdlib")
    mapping = FileMap(path, codec, reload="inotify")
    mapping.create({"a": 1})
    payload = codec.dump({"a": 7})
    proc = _spawn().Process(target=replace_file, args=(str(path), payload))
    proc.start()
    proc.join(10)
    assert proc.exitcode == 0
    deadline = time.perf_counter() + 2
    seen = mapping["a"]
    while seen != 7 and time.perf_counter() < deadline:
        time.sleep(0.01)
        seen = mapping["a"]
    assert seen == 7
    mapping.close()


def test_distinct_keys_merge(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    FileMap(path).create({"seed": 1})
    procs = [
        _spawn().Process(target=set_key, args=(str(path), f"k{i}", i))
        for i in range(4)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(20)
        assert proc.exitcode == 0
    mapping = FileMap(path)
    assert mapping["seed"] == 1
    for i in range(4):
        assert mapping[f"k{i}"] == i


def test_cooperating_increments(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    FileMap(path).create({"n": 0})
    workers = 4
    each = 12
    procs = [
        _spawn().Process(target=bump, args=(str(path), each)) for _ in range(workers)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(60)
        assert proc.exitcode == 0
    assert FileMap(path)["n"] == workers * each


@pytest.mark.parametrize("reload", ["stat", "inotify"])
def test_library_writers_exclude_each_other_and_rm_does_not_take_the_lock(
    tmp_path: Path, reload: str
) -> None:
    path = tmp_path / "state.json"
    mapping = FileMap(path, reload=reload)
    try:
        mapping.create({"label": "alpha", "count": 1})
    except OSError as exc:
        pytest.skip(f"{reload} unavailable: {exc}")
    flag = tmp_path / "go"
    done = tmp_path / "rm-done"
    proc = _spawn().Process(
        target=_rm_when_ready,
        args=(str(flag), str(path), str(done)),
    )
    proc.start()
    with pytest.raises(Conflict):
        with mapping.batch():
            mapping["count"] = 9
            flag.write_text("go")
            for _ in range(100):
                if done.exists():
                    break
                time.sleep(0.01)
            time.sleep(0.05)
    proc.join(5)
    assert done.exists()
    assert float(done.read_text()) < 0.3
    assert not path.exists()
    assert mapping.document is None
    with pytest.raises(Gone):
        mapping["label"]


def _rm_when_ready(flag: str, target: str, done: str) -> None:
    import subprocess
    import time as time_mod
    from pathlib import Path

    while not Path(flag).exists():
        time_mod.sleep(0.01)
    started = time_mod.monotonic()
    subprocess.run(["rm", "-f", target], check=True)
    Path(done).write_text(f"{time_mod.monotonic() - started:.4f}")


def test_kill_during_write_leaves_parseable_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    FileMap(path).create({"v": "x" * 80})
    proc = _spawn().Process(target=spin_write, args=(str(path),))
    proc.start()
    time.sleep(0.25)
    proc.kill()
    proc.join(10)
    data = json.loads(path.read_text())
    assert "v" in data
    assert isinstance(data["v"], str) and data["v"]
