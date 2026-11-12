# Where `order_trees` is called

Searched 2026-11-12 as GitHub user `humdings`. The token can see that account's public repositories. It cannot list private repositories (`GET /user/repos` returns 403). A missing private repository and a repository that does not exist both come back as 404, so the 404s below are **inaccessible**: either absent or not visible to this token.

`humdings` is a user account, not an organization. The public profile lists 43 repositories; 18 are originals and the rest are forks.

## Inaccessible

These names were requested and returned HTTP 404:

| Repository | HTTP |
| --- | --- |
| `humdings/humdings-legacy` | 404 |
| `humdings/legacy` | 404 |
| `humdings/ib_tools` | 404 |
| `humdings/ibtools` | 404 |
| `humdings/ib-tools` | 404 |
| `humdings/cybermoney` | 404 |
| `humdings/cyber-money` | 404 |
| `humdings/cyber_money` | 404 |
| `humdings/run_gemini` | 404 |
| `humdings/run-gemini` | 404 |
| `humdings/gemini` | 404 |
| `humdings/humdesk` | 404 |
| `humdings/humdesk-legacy` | 404 |
| `humdings/ib_tools_legacy` | 404 |
| `humdings/pynance` | 404 |

No public repository under `humdings` has `legacy`, `humdesk`, `ib_tools`, `cybermoney`, or `run_gemini` in its name. `docs/ops/legacy-ibtools.md` could not be opened because `humdings/humdesk` is inaccessible.

GitHub code search for `order_book_for_directory` and for `stage_order user:humdings` finds only this repository. A clone of the public Python projects below, then a search for `order_trees`, `JsonCache`, `order_book_for_directory`, `create_cache_for_directory`, and `next_buy_id`, found no call sites:

`notebooks`, `fin_apis`, `crypto_hub`, `factorflow`, `LimitBook`, `pynance-legacy`, `finnhub-python`, `Quantopian`, `IbPy3`, `pyU`, `algorithm-component-library`, `OrderBook`, `ib-controller`, `zipline`, `qlib`.

So there is no readable call site outside this repo. The humdesk layout below is taken from the description of that port (`orders/<symbol>/<id>.json` and the field list), not from source we could open.

## What the old public API did

These are the constructors and methods a caller had. Line numbers are from `main` before this rewrite (`d55ffd3`).

| Repo | Path | Line | What it called |
| --- | --- | --- | --- |
| `humdings/order_trees` | `order_trees/factory.py` | 8 | `order_book_for_directory(path, use_cache=True)` builds a subclass of `OrderTree` with `DIRECTORY`, `_CACHE`, `_ORDER_MAP`, `_USE_CACHE`, and ensures `completed/` |
| `humdings/order_trees` | `order_trees/factory.py` | 23 | `create_cache_for_directory(path, use_cache=True)` does the same for `JsonCache`, without order fields or `completed/` |
| `humdings/order_trees` | `order_trees/json_cache.py` | 38 | `JsonCache(data=None, directory=None)` generates `_id` when missing and writes on that assignment |
| `humdings/order_trees` | `order_trees/json_cache.py` | 73–96 | `__getitem__`, `__setitem__`, `update`, `get`; `__setitem__` writes the whole file |
| `humdings/order_trees` | `order_trees/json_cache.py` | 83–87 | `with cache:` writes on the way out |
| `humdings/order_trees` | `order_trees/json_cache.py` | 101–124 | `from_id`, `load`, `dump`, `get_or_create` |
| `humdings/order_trees` | `order_trees/order_tree.py` | 44 | `OrderTree(data, order_id=None, directory=None)` copies `order_id` / `id` / `_id` into each other |
| `humdings/order_trees` | `order_trees/order_tree.py` | 63–376 | `all_order_ids`, `to_frame`, `staged_orders`, `from_order_id`, `lookup_order`, `reset_order_id_cache`, `delete_order_id`, `complete_order_id`, `stage_order`, `combine_orders`, `move_completed`, `set_order_id`, `is_staged`, `is_triggered`, `is_limit_triggered`, `is_stop_triggered`, `get_next_buy`, `get_next_sell`, `get_children`, `get_previous_order`, `print_tree`, `preorder_traverse`, `find_root`, `is_root`, `is_leaf` |

`stage_order` filled `_id`, `order_id`, `side`, `target_price`, `staged`, `amount`, `remaining_amount`, `symbol`, `stop_price`, `account`, `size`, `original_amount`, `original_side`, `_order_type`, `_dt`, then `kwargs` (where `parent_id`, `next_buy_id`, and `next_sell_id` show up).

Inferred humdesk use, given that file layout and those methods:

```python
Book = order_book_for_directory(f"orders/{symbol}")
order = Book.stage_order(symbol, amount, target_price, side, account=account, ...)
order["remaining_amount"] = new_amount
Book.lookup_order(order_id)
Book.complete_order_id(order_id)   # orders/<symbol>/completed/<id>.json
```

That inferred block is not a located source line. Do not treat it as one.

## How that maps onto the new API

`FileMap` and `DirMap` take a path (or a file object whose `.name` is a path), a codec, and generic options: `reload`, `lock`, and `write_through`. They do not take an order id, a symbol, or a field dict.

| Old call | Still works? | New call |
| --- | --- | --- |
| `order_book_for_directory(path, use_cache=True)` | Yes. Returns a class with the same methods. | `DirMap(path)` or `symbol_order_dir(root, symbol)` when you do not want the class cache |
| `create_cache_for_directory(path, use_cache=True)` | Yes | `DirMap(path)` |
| `Book.stage_order(symbol, amount, price, side, **fields)` | Yes | `stage_order(book, ...)` or `book[id] = new_order_record(...)` |
| `Book.from_id(id)` / `from_order_id` / `lookup_order` | Yes | `book[id]`, and keep `lookup_order` if you still search by `order_id` |
| `order[field] = value` | Yes. Writes the file, overlaying that key if the file changed. | `book[id][field] = value` |
| `with order: order.data[k] = v` | Yes. `dump()` on the way out, including when the block raises. In-place `.data` edits do not write by themselves. | On a `FileMap`, `m[k] = v` or a nested proxy writes. `.data` is the legacy object only. |
| `order.dump()` / `JsonCache.get_or_create` | Yes | `FileMap.replace` or ordinary item assignment |
| `use_cache=True` | The object is reused, but the next lookup reloads when `mtime_ns`, `ctime_ns`, size, or inode changes. The old cache hid those edits. | default `reload="stat"` |
| `use_cache=False` | `from_id` builds a new object every call, and each lookup parses. | `reload="always"` |
| `Book.to_frame()` | Yes, if pandas is installed. It is no longer required to import the package. | read the `DirMap` yourself |
| `move_completed` | Moves the file under `completed/` and leaves `filename` on the active path. A later `dump()` creates that active file again, as the old `dump` did. | `shutil.move` the path `DirMap` already ignores `completed/` |

Nested mutation on a `FileMap` writes through. The legacy `.data` dict does not: assign the key, or exit the `with` block, to persist it.

Two writers of different top-level keys merge. Two writers of the same key: the write that commits last wins that key. `modify` and `batch` are the atomic read-modify-write. `book[id] = document` replaces the whole file and does not keep keys you left out.
