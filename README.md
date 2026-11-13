# File-mirrored mappings

A dict that lives in a file. Lookups use memory. If the file changed, the next lookup loads it again. Setting or deleting a key writes the file.

```python
from order_trees import FileMap

m = FileMap("state.json")
m["name"] = "alpha"
print(m["name"])
```

`DirMap` is the same idea for a folder. The key is the filename.

```python
from order_trees import DirMap

records = DirMap("records")
records["item-1"] = {"name": "alpha", "count": 10}
records["item-1"]["count"] = 9
```

## Reads

The default reload strategy stats the file and compares `mtime_ns`, `ctime_ns`, size, and inode. It parses only when one of those changes. A writer does not trust that check: under the lock it reads the file again and then applies its keys, so two writers cannot lose an update just because a clock reused a timestamp. `reload="inotify"` (Linux) or `reload="watchfiles"` (needs the `watchfiles` package) skips that stat when the kernel reports no change. `reload="manual"` reloads only when you call `refresh()`. `reload="always"` parses every lookup.

JSON is the default codec. `orjson` is used when it is installed. Pass any object with `load(bytes)` and `dump(obj) -> bytes` as the codec.

## Writes

Writes go to a temp file in the same directory, fsync, rename over the target, then fsync the directory. A crash before the rename leaves the previous file in place.

A `fcntl` lock beside the file keeps two cooperating writers from clobbering each other. `DirMap` uses one lock per file, so different keys do not block each other.

Conflict policy is overlay. On a partial write, if the file changed since the last load, it is read again and this write's top-level keys are applied on top. Those keys win, including deletes. Every other key keeps the value from the file. There is no merge inside a single key. `replace()` and `records[key] = {...}` overwrite that whole document.

`modify(key, fn)` and `with m.batch():` hold the lock across the read and the write, and a batch writes once. An exception inside `batch()` does not write. `m[k] = m[k] + 1` is not atomic; the read happens before the lock.

Nested dicts and lists are proxies. `m["child"]["k"] = 1` writes the file. `write_through=False` returns a copy instead, and that copy is stored only if you assign it back.

## When a file disappears

Agreed rules for a removed or renamed file are in [the file disappearance protocol](docs/protocol.md). This tree does not implement that protocol yet.

## Older constructors

`JsonCache(data, directory)` is the older per-file constructor. A directory factory returns a subclass bound to one path. Id generation stays in those constructors. `use_cache=True` keeps the object and still reloads when the file changes. `use_cache=False` builds a new object on every `from_id` and parses on every lookup.

In-place edits of `.data` are written on `dump()` or when a `with` block exits. Item assignment writes immediately.

## Develop

```
nix develop --command pytest
nix develop --command python -m order_trees.benchmark
```

The benchmark prints lookup time for an unchanged file and for a file that was just replaced. One run: stat 1.3 µs unchanged and 11 µs changed; inotify 0.9 µs unchanged and 18 µs changed.
