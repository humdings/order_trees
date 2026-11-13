# File disappearance protocol

This is the behavior when a mapped file is removed, renamed, or replaced at the same path. It is the agreed protocol. Nothing in this tree implements it yet.

Examples use a directory mapping `records` and documents shaped like `{"label": "alpha", "count": 3}`. `a.json` is the file for key `"a"`.

`Gone` will be an exception in `order_trees.errors`, a subclass of `KeyError`. Its message includes the path.

## Agreed

### 1. A missing file is an implicit delete from the directory map

There is no tombstone and no placeholder file.

If `records/a.json` is not a file, then on the next membership check:

- `"a" in records` is false
- `"a"` is absent from `keys()`, iteration, and `len`
- `records["a"]` raises `KeyError` (the builtin, not `Gone`)
- `records.get("a")` is `None`

The check is the next lookup, `in`, iteration, or `len`. There is no hook that runs at the moment an outside `rm` happens. A caller that does not touch the map cannot observe the delete.

This is the same outcome when this process deletes the key (`del records["a"]`) and when another process or a shell removes the file. After either one, a key that was already opened is not a member. The map must not answer the lookup from its cache.

Tests, not implemented:

- `tests/test_dirmap.py::test_external_rm_drops_key` — after `rm records/a.json`, the four checks above hold, and no tombstone file is left behind.
- `tests/test_dirmap.py::test_delete_through_map_matches_external_rm` — `del records["a"]` has the same membership result as the external `rm`.
- `tests/test_dirmap.py::test_other_process_delete_drops_key` — a second process removes the file; the first map sees the same membership result on its next check.
- `tests/test_dirmap.py::test_opened_key_is_not_cached_as_a_member` — the key was already returned by `records["a"]`, then the file is removed; the next `records["a"]` still raises `KeyError`.

### 2. A held file map reports that it is gone

`FileMap.exists` and `FileMap.gone` are properties. Each read stats the path.

- `exists` is true when the path is a regular file.
- `gone` is the opposite of `exists`.

`FileMap.document` is `None` when `gone` is true. When the path is a file, `document` is a plain dict copy of that document (`to_dict()`).

While `gone` is true, reads raise `Gone`:

- `__getitem__`, `__contains__`, `__iter__`, `__len__`, `to_dict`
- `get` is overridden so it raises `Gone` instead of returning the default. `None` from `get` means "this live document has no such field" (or the caller-supplied default). It does not mean the file is missing.
- a nested proxy read raises `Gone` as well

`bool(mapping)` is false when `gone` is true. It is also false for a live empty document `{}`. `bool` is not an existence test. Callers use `exists` or `gone`.

`gone` is the state of the path at this call, not a sticky flag. A missing path is gone. The same object does not keep the last document in memory for later reads.

A closed inotify watch is not a special error. After `del records["a"]` with `reload="inotify"`, reads on the held object raise `Gone`, not `OSError`.

Tests, not implemented:

- `tests/test_filemap.py::test_held_map_document_is_none_after_rm` — hold `records["a"]`, `rm` the file, then `gone` is true, `exists` is false, and `document` is `None`.
- `tests/test_filemap.py::test_gone_reads_raise_gone` — `__getitem__`, `get`, iteration, `len`, `in`, and `to_dict` raise `Gone`. `Gone` is a `KeyError`.
- `tests/test_filemap.py::test_bool_is_not_existence` — `bool` is false for a gone map and for a live `{}`; `exists` distinguishes them.
- `tests/test_filemap.py::test_standalone_filemap_goes_gone` — the same reads on `FileMap("state.json")` after `rm`.
- `tests/test_filemap.py::test_gone_under_stat_and_inotify` — parametrized over `reload="stat"` and `reload="inotify"`, including a held object whose watch was closed by `del` on the directory map.
- `tests/test_filemap.py::test_gone_is_not_sticky_if_the_path_returns` — after the path is a file again, `gone` is false and reads load that file. See `create` below for who is allowed to create it.

### 3. Writes to a gone file raise, except an explicit create

While the path is not a file, these raise `Gone` and leave the path absent:

- `__setitem__`, `__delitem__`, `update`, `pop`, `clear`, `setdefault`, `replace`, `modify`
- `batch`, when the path is already absent as the block is entered
- a nested proxy write
- `write_memory`

A file that was loaded for `batch` and then edited or removed before commit is rule 7. That commit does not recreate the file.

The explicit recreate on a file map is `FileMap.create(document)`:

- `document` is a mapping, with the same key rules as any other write.
- Under the file lock, if the path is already a file, raise `FileExistsError` and do not change it.
- If the path is absent, write `document` and the same object is live: `gone` is false and reads return `document`.

`DirMap.__setitem__(key, document)` is the explicit create for a directory key. It writes that file even when the key was missing. It does not raise `Gone`. If the cached file map for that key was gone, the assignment is the recreate: that object is live and shows `document`.

A write that finds the path already present is a normal update of that file (someone else created it). It is not `create`, and it must not treat the path as empty just because this object last saw it missing.

Tests, not implemented:

- `tests/test_filemap.py::test_item_write_on_gone_map_does_not_create_the_file` — `__setitem__`, `replace`, `update`, `modify`, and a nested write raise `Gone`, and the path is still absent.
- `tests/test_filemap.py::test_batch_on_gone_map_raises` — entering `batch` raises `Gone` and does not create the file.
- `tests/test_filemap.py::test_create_writes_only_when_absent` — `create({"label": "beta"})` writes that document and clears `gone`; a second `create` raises `FileExistsError` and the file is unchanged.
- `tests/test_dirmap.py::test_assignment_recreates_a_missing_key` — after `rm`, `records["a"] = {"label": "beta"}` creates the file, the key is a member, and a held file map for `"a"` reads `beta`.
- `tests/test_filemap.py::test_write_does_not_clobber_a_file_created_by_someone_else` — the path is recreated underneath a gone object; the next item write updates that file and keeps keys the write did not set.

### 4. A rename is a delete of the old key and a new key

`mv records/a.json records/b.json` is an implicit delete of `"a"` plus the appearance of `"b"`.

- `"a"` fails the membership checks in rule 1.
- `"b"` is a member. Its document is the moved bytes.
- The file map that was handed out for `"a"` is gone. It stays bound to `a.json` and does not follow the file to `b.json`.
- `"b"` is a different object from the held `"a"` map.

Tests, not implemented:

- `tests/test_dirmap.py::test_rename_drops_old_key_and_adds_new_key` — after `mv a.json b.json`, `"a"` is not a member, `"b"` is, and `records["b"]["label"]` is the value that was in `a`.
- `tests/test_filemap.py::test_held_map_goes_gone_on_rename` — the object previously returned for `"a"` has `document is None` and raises `Gone` on read. It is not `records["b"]`.

### 5. A replacement at the same path loads the new bytes

If `a.json` is replaced and the path is still `a.json`, the held file map for `"a"` loads the new document on its next read. That includes a shell overwrite and `mv other.json records/a.json` (the inode may change).

- `"a"` stays a member. The held object stays bound to `a.json`. It does not go gone.
- `held["label"]`, iteration, and `document` are the bytes now in that file.
- A nested proxy reads those bytes on its next use.
- There is no generation counter.

This is not rule 4. `mv a.json b.json` removes `a`. `mv other.json a.json` keeps `a` and changes its contents.

Tests, not implemented:

- `tests/test_filemap.py::test_overwrite_reloads_held_map` — overwrite `a.json` with a new document; the same held object reads the new `label` under `reload="stat"` and `reload="inotify"`. `"a"` is still a member.
- `tests/test_filemap.py::test_mv_onto_path_reloads_held_map` — `mv` a different file onto `a.json`; the held object reads that file's document, including when the inode changes.
- `tests/test_dirmap.py::test_same_path_replacement_keeps_the_key` — after either replacement, `"a" in records` is true and `records["a"]` is the held object.

### 6. Membership matches the disk on every check

A directory signature of `(mtime_ns, ctime_ns, size, inode)` is not enough to decide the key set. On some filesystems that tuple does not move when a child is added or removed inside one timestamp tick, and a cache keyed only on it lags the disk.

Every membership operation checks the filesystem on that call:

- `"k" in records` and `records["k"]` stat `k` plus the suffix. The key is a member only if that path is a regular file.
- Iteration and `len` read the directory and keep names that are regular files with the map's suffix, skipping dotfiles, the lock file, and directories, as today.
- An internal cache of names is not an answer. It may be a hint only if the keys reported on this call are exactly the files present now.

A delete and a create in the same timestamp tick are visible on the next check. A new file shows up. A removed file drops out.

Tests, not implemented:

- `tests/test_dirmap.py::test_delete_in_one_timestamp_tick_drops_the_key` — write the file, read the directory signature, remove the file before that signature would change, then `in`, iteration, and `len` omit the key.
- `tests/test_dirmap.py::test_create_in_one_timestamp_tick_adds_the_key` — the same setup, then add `b.json` before the directory signature changes; iteration includes `"b"`.
- `tests/test_dirmap.py::test_contains_stats_the_file` — `"a" in records` is false when `a.json` is absent even if a previous scan cached `"a"`.

### 7. A batch commit loses to an outside edit

This race should be rare. Two library users already exclude each other with the file lock. The case that remains is an outside program, treated as a person editing or removing the file, changing the path after `batch` has loaded it.

`batch` records the inode and `mtime_ns` from that load. On commit, if the inode differs, `mtime_ns` differs, or the file is gone, raise `Conflict` (`order_trees.errors.Conflict`) and do not write. The outside version wins:

- The bytes on disk are left as they are. A removal stays a removal. The batch does not merge its keys onto the new file and does not recreate a deleted file.
- Edits made inside the block are discarded. After the exception, a read follows the disk: the outside document, or `Gone` if the path is absent.

Item assignment outside `batch` is not this rule.

`DirMap.batch` uses the same check for every file it would write. If any of those files conflicts, the commit raises `Conflict` before writing any of them.

Test, not implemented:

- `tests/test_filemap.py::test_batch_commit_raises_when_the_file_changed` — inside `batch`, an outside overwrite changes inode or `mtime_ns`, and an outside `rm` removes the file. Commit raises `Conflict`. The overwrite is still the file's bytes. The removed path is still absent.

### 8. Writes are atomic, and a bad read is an error

A library write goes to a temp file in the same directory, fsyncs it, renames it over the target, then fsyncs the directory. A crash before the rename leaves the previous file in place.

Documents are small. A reader that sees partial or invalid JSON retries a few times, with a short pause between tries, so it can catch the file just as a rename lands. If the bytes are still not a JSON object after those tries, the read raises `DocumentError`. It does not install `{}` or any other stand-in document.

A missing path is rule 2 (`Gone`), not an invalid document and not `{}`. A zero-byte file is rule 9: it raises `EmptyFileError` on the first read, with no retries. Non-empty partial or invalid JSON is what this rule retries.

Tests, not implemented:

- `tests/test_filemap.py::test_write_is_temp_file_then_atomic_rename` — a write renames a temp file in the same directory over the target, and fsyncs the file before the rename and the directory after it.
- `tests/test_filemap.py::test_invalid_json_raises_after_retries` — a partial or invalid payload still fails after the short retries, the error is `DocumentError`, and the map's document is not `{}`.

### 9. `{}` is an empty document, and a missing file is not

A file whose bytes are `{}` is a live empty document. `gone` is false, `document == {}`, and `bool` is false because the document is empty. In a directory map the key is a member. Lookup returns that empty document. It does not raise `KeyError`.

A missing path is the other case. The directory key is not a member (`KeyError` on lookup, `get` is `None`). A held file map is gone: `document` is `None` and reads raise `Gone`.

A zero-byte file is not an empty document and not a reason to retry. There is no valid library file of length zero: the library never writes one, and an empty map is written as the two bytes `{}`. A zero-byte path means a broken write or a truncation. The first read raises `EmptyFileError`, a subclass of `DocumentError`, and does not try again. The error propagates. It is not `{}`, not `Gone`, and not `DocumentError` from the retry path. The path exists, so the directory key stays a member.

Tests, not implemented:

- `tests/test_filemap.py::test_missing_file_is_gone_and_empty_object_is_empty_document` — bytes `{}` load as `{}` with `gone` false. After the path is removed, `document` is `None`, reads raise `Gone`, and the directory key raises `KeyError`.
- `tests/test_filemap.py::test_zero_byte_file_raises_immediately` — a zero-length file raises `EmptyFileError` on the first read, with no retry. The error is a `DocumentError` and is not `Gone`. The document is not `{}`. The directory key is still a member.
- `tests/test_filemap.py::test_empty_map_is_written_as_empty_object` — clearing a document or writing `{}` stores `{}`, not a zero-length file.

### 10. `pop` on a directory key returns a dict, and `pop` of a field writes

`DirMap.pop(key)` deletes that file, drops `key` from the map, and returns a plain dict copy of the document. The copy is not a `FileMap`. Editing it does not write. A `FileMap` already held for `key` then follows rule 2: `document` is `None` and reads raise `Gone`. A missing key raises `KeyError`. `pop(key, default)` returns `default` and does not change the disk.

`FileMap.pop(field)` is an ordinary mapping pop on a live document. It removes `field`, writes the remaining document with the atomic rename from rule 8, and returns the value that was stored. A missing field raises `KeyError` unless a default is passed. If the path is already gone, `pop` raises `Gone` and does not create the file.

Tests, not implemented:

- `tests/test_dirmap.py::test_pop_returns_a_dict_copy_and_drops_the_key` — `pop("a")` returns `{"label": "alpha", "count": 3}` as a `dict`, the file is gone, `"a"` is not a member, and a held file map for `"a"` has `document is None`.
- `tests/test_filemap.py::test_pop_field_writes_the_document_and_returns_the_value` — `pop("count")` returns `3`, the file is the atomic-rename result without `count`, and `pop` on a gone map raises `Gone` without creating the path.

### 11. Legacy cache objects follow the gone rule

Per-file objects from the legacy constructors follow rules 2 and 3 for both `use_cache=True` and `use_cache=False`. A missing file makes reads raise `Gone` and `document` return `None`. Item assignment does not recreate the file. `dump()` does not recreate it either. Recreate goes through `create` on the underlying file map, or through a directory-map assignment when the caller has one.

`from_id` on an id whose file is absent raises `FileNotFoundError` when it has to open the path. It does not construct an empty document. With `use_cache=True`, a later `from_id` for an object already cached returns that object, which is gone.

Test, not implemented: `tests/test_compat.py::test_legacy_object_goes_gone_for_both_cache_settings`.

## Open proposals

David has not decided these. They are not part of the agreed protocol above.

### Removed-key callback

Optional `DirMap(..., on_remove=fn)`. `fn(key)` runs when a membership check sees that a key from the previous check is gone. No callback is the default. The callback does not run for a key this map never observed. Failures in `fn` propagate and do not change membership.

Test, not implemented: `tests/test_dirmap.py::test_on_remove_fires_once_per_disappeared_key`.

### Locks coordinate library users only

`flock` on the sibling lock file is the guarantee between users of this library. A holder of the lock can rely on other library writers waiting. The lock does not stop a shell `rm`, `mv`, or overwrite. Those are the implicit deletes and replacements in the rules above. The lock file is not the document.

Test, not implemented: `tests/test_concurrency.py::test_library_writers_exclude_each_other_and_rm_does_not_take_the_lock`.
