# order_trees

A dict that lives in a file. Lookups use memory. If the file changed, the next lookup loads it again. Setting or deleting a key writes the file.

```python
from order_trees import FileMap

m = FileMap("state.json")
m["symbol"] = "AAPL"
print(m["symbol"])
```

`DirMap` is the same idea for a folder. The key is the filename.

```python
from order_trees import DirMap, new_order_record

book = DirMap("orders/AAPL")
book["ord-1"] = new_order_record("AAPL", 10, 101.5, "buy", account="DU1")
book["ord-1"]["amount"] = 9
```

## Reads

The default reload strategy stats the file and compares `mtime_ns`, `ctime_ns`, size, and inode. It parses only when one of those changes. A writer does not trust that check: under the lock it reads the file again and then applies its keys, so two writers cannot lose an update just because a clock reused a timestamp. `reload="inotify"` (Linux) or `reload="watchfiles"` (needs the `watchfiles` package) skips that stat when the kernel reports no change. `reload="manual"` reloads only when you call `refresh()`. `reload="always"` parses every lookup.

JSON is the default codec. `orjson` is used when it is installed. Pass any object with `load(bytes)` and `dump(obj) -> bytes` as the codec.

## Writes

Writes go to a temp file in the same directory, fsync, rename over the target, then fsync the directory. A crash before the rename leaves the previous file in place.

A `fcntl` lock beside the file keeps two cooperating writers from clobbering each other. `DirMap` uses one lock per file, so different keys do not block each other.

Conflict policy is overlay. On a partial write, if the file changed since the last load, it is read again and this write's top-level keys are applied on top. Those keys win, including deletes. Every other key keeps the value from the file. There is no merge inside a single key. `replace()` and `book[key] = {...}` overwrite that whole document.

`modify(key, fn)` and `with m.batch():` hold the lock across the read and the write, and a batch writes once. An exception inside `batch()` does not write. `m[k] = m[k] + 1` is not atomic; the read happens before the lock.

Nested dicts and lists are proxies. `m["child"]["k"] = 1` writes the file. `write_through=False` returns a copy instead, and that copy is stored only if you assign it back.

## Orders

The old class API still works: `order_book_for_directory`, `create_cache_for_directory`, `stage_order`, `from_id`, `lookup_order`, and the rest. Id generation and the order fields live there, not in `FileMap`.

`use_cache=True` no longer ignores edits from other processes. The next lookup reloads when the file changes. See `docs/call-sites.md`.

## Develop

```
nix develop --command pytest
nix develop --command python -m order_trees.benchmark
```

The benchmark prints lookup time for an unchanged file and for a file that was just replaced. One run on this tree: stat 1.3 µs unchanged and 11 µs changed; inotify 0.9 µs unchanged and 18 µs changed.
