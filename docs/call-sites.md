# Where `order_trees` is called

Two private trees were attached and read on 2026-11-12. They are not in this repository. Paths below are inside those archives. No account ids are copied here.

- ib_tools archive, package root `ib_tools/ib_tools/`. The user-facing paths `ib_tools/orders/metadata.py` and `ib_tools/strats/...` are `ib_tools/ib_tools/orders/...` and `ib_tools/ib_tools/strats/...` in the zip.
- cybermoney archive, root `cybermoney-belter-source/`. `run_gemini.py` lives at `cybermoney-belter-source/scripts/run_gemini.py`.

A public GitHub search on 2026-11-12 still found no other call sites. `humdings/humdesk`, `humdings/ib_tools`, `humdings/cybermoney`, `humdings/run_gemini`, and `humdings/humdings-legacy` returned 404 to this token before the archives were attached.

## Imports

| Archive path | Line | Import |
| --- | --- | --- |
| `ib_tools/ib_tools/orders/metadata.py` | 3 | `order_book_for_directory`, `create_cache_for_directory` |
| `ib_tools/ib_tools/contract_watcher.py` | 14 | `BUY`, `SELL`, `SIDE`, `STAGED`, `ID`, `ACCOUNT`, `AMOUNT`, `TARGET_PRICE`, `STOP_PRICE`, `SYMBOL`, `NEXT_BUY_ID`, `NEXT_SELL_ID` |
| `ib_tools/ib_tools/strats/dca.py` | 7 | same field constants from `order_trees.order_tree`, including `ID` |
| `ib_tools/ib_tools/strats/trailing_stops.py` | 7 | same module import as `dca.py` |
| `ib_tools/ib_tools/strats/target_weights.py` | 8 | same constants from `order_trees` |
| `ib_tools/ib_tools/strats/order_handlers.py` | 7 | `BUY`, `SELL` from `order_trees.order_tree` |
| `ib_tools/ib_tools/strats/tree_trader.py` | 18 | `BUY`, `SELL` from `order_trees.order_tree` |
| `ib_tools/ib_tools/strats/ibot.py` | 13 | `ORDER_ID_FIELD` |
| `ib_tools/scripts/scale-book.py` | 5 | `SIDE`, `BUY` |
| `ib_tools/scripts/shift-book.py` | 3 | `SIDE`, `BUY` |
| `cybermoney-belter-source/orders/meta.py` | 12 | `order_book_for_directory`, `create_cache_for_directory` |

`NEXT_BUY_ID` and `NEXT_SELL_ID` are imported in the strategy modules and not referenced again in those files. They still have to import.

## Constructors

| Path | Line | Call | Compat |
| --- | --- | --- | --- |
| `ib_tools/ib_tools/orders/metadata.py` | 13 | `create_cache_for_directory(ib_meta_directory(), use_cache=False)` bound to `IBMeta` | `JsonCache` subclass. `use_cache=False` reparses on lookup and rebuilds the object in `from_id`. |
| `ib_tools/ib_tools/orders/metadata.py` | 19 | `make_ib_order_cache(name, use_cache=True, order_directory=None)` joins `order_directory or ib_order_directory()` with `name` and calls `order_book_for_directory(path, use_cache=use_cache)` | `OrderTree` subclass. Creates `path` and `path/completed`. |
| `cybermoney-belter-source/orders/meta.py` | 18, 23, 28 | `make_coinbase_order_cache`, `make_gemini_order_cache`, `make_binance_order_cache`: `order_book_for_directory(os.path.join(root, name), use_cache=use_cache)` | same class factory |
| `cybermoney-belter-source/orders/meta.py` | 34–49 | module-level `CoinbaseOrder`, `GeminiOrder`, `BinanceUSOrder` on the exchange root, and `CoinbaseMeta`, `GeminiMeta`, `BinanceMeta` with `use_cache=False` | same |

`FileMap` and `DirMap` still do not take these arguments. The factories do.

## Methods and fields

Held by the legacy classes unless noted.

| Path | Line | Call | Covered by |
| --- | --- | --- | --- |
| `contract_watcher.py` | 84 | `make_ib_order_cache(localSymbol, use_cache=False)` | factory |
| `contract_watcher.py` | 86 | `IBMeta.get_or_create(f"{symbol}_{name}")` | `JsonCache.get_or_create` |
| `contract_watcher.py` | 89 | `k not in self._metadata`, then `update(missing_params)` | `__contains__`, `update(mapping)` |
| `contract_watcher.py` | 232 | `metadata` property calls `from_id` on every access | `from_id` |
| `contract_watcher.py` | 475 | `staged_orders()`, then `order[SYMBOL]`, `order[ACCOUNT]` | classmethod plus item access |
| `contract_watcher.py` | 487 | `order[SIDE]`, `order.is_triggered(price)` | `is_triggered` / limit and stop checks |
| `contract_watcher.py` | 504 | `staged_order[STAGED] = False` | `__setitem__` writes the file |
| `contract_watcher.py` | 531 | `set_order_id(order_id)` | updates `order_id` and the id map, including the string form of the id |
| `contract_watcher.py` | 541 | `stage_order(symbol=, amount=, target_price=, side=, account=, **kwargs)` | keyword `stage_order` |
| `strats/dca.py` | 72 | `stage_order(amount, price, side)` via the watcher | same |
| `strats/dca.py` | 77 | `new_staged_order[ID]` | `__getitem__` |
| `strats/dca.py` | 82 | `lookup_order(id)` | scans `_id` and `order_id` |
| `strats/dca.py` | 84 | `staged[STAGED]`, then assigns `AMOUNT` and `SIDE` | item access |
| `strats/target_weights.py` | 65–72 | same `ID`, `lookup_order`, `AMOUNT`, `SIDE` pattern | same |
| `strats/order_handlers.py` | 43, 60, 83 | `stage_order(amount, target_price, BUY or SELL)` | watcher method |
| `strats/trailing_stops.py` | 69 | `metadata.get(STOP_PRICE)`, `metadata.get(SIDE)`, `metadata[STOP_PRICE] = ...` | `JsonCache.get` / `__setitem__` |
| `strats/ibot.py` | 30 | `manager.metadata.data` | `.data` |
| `strats/ibot.py` | 44 | `staged_orders()`, then `x.data` into a DataFrame indexed by `ORDER_ID_FIELD` | `.data` is a dict |
| `scripts/place-order.py` | 20 | `stage_order(symbol=, amount=, target_price=, side=, staged=, stop_price=, order_type=, account=, **kwargs)` | keyword form |
| `scripts/join-inside.py` | 40 | `stage_order(...)` | same |
| `scripts/scale-book.py` | 26 | `staged_orders()`, `order[SIDE] == side`, `order.update(amount=, remaining_amount=)` | keyword `update` |
| `scripts/shift-book.py` | 23 | `staged_orders()`, sort by `target_price`, `update(target_price=)` | keyword `update` |
| `scripts/order_monitor.py` | 42 | book with `use_cache=False` | factory |
| `scripts/order_monitor.py` | 53 | `to_frame()` then `reset_index()`; expects the index name `_id` | `to_frame` indexes on `_id`. An empty book still has a `staged` column so filters do not raise. |
| `scripts/order_monitor.py` | 121 | `from_id` / `from_order_id`, then a scan of `all_order_ids` | both getters. `from_order_id` raises `FileNotFoundError` when the id is unknown, which this loop catches. |
| `scripts/order_monitor.py` | 136 | `getattr(order, "order_id", ...)` | `order_id` property. This import path did not used to have that attribute, so the scan compared `""`. |
| `scripts/order_monitor.py` | 186 | `order.update(**coerced)` | keyword `update` |
| `scripts/order_monitor.py` | 233, 236 | `delete_order_id`, `complete_order_id` | both. Complete moves the file under `completed/` and leaves `filename` on the active path. |
| `scripts/order_monitor.py` | 288 | `stage_order(...)` with the same keywords as `place-order.py` | keyword form |
| `scripts/order_dashboard.py` | 128, 138 | `use_cache=False`, `to_frame()`, then promotes `_id` from the index | same frame shape |
| `scripts/cancel-order.py` | 24 | `delete_order_id` / `complete_order_id` | both |
| `scripts/cleanup.py` | 22 | `all_order_ids`, `from_id`, `is_staged`, then delete or complete | all four |
| `scripts/print_book.py` | 12 | `to_frame()` | `to_frame` |
| `scripts/run_ib_web.py` | 133, 243, 253 | `staged_orders()`, `metadata.data`, `DataFrame(x.data for x in staged)` | `.data` |
| `cybermoney-belter-source/coinbase/strats/market_maker.py` | 99 | `set_order_id`, `update(new_order)`, `staged_order['order_id']`, `.data` | all |
| `market_maker.py` | 106, 116 | `staged_orders`, `order['side']`, `is_triggered(bid or ask)` | same checks, string keys instead of the constants |
| `market_maker.py` | 257, 371 | `lookup_order(oid)`, `get_previous_order()` | parent link is `parent_id`. These strategies store `previous_order` on the dict and only call `get_previous_order` when that other field was also written as `parent_id`. |
| `market_maker.py` | 291 | `stage_order(symbol, size, price, side).data` | `.data` is the live dict |
| `coinbase/strats/dca.py` | 109 | `stage_order(..., order_type=).data`, then the caller mutates the dict and `write_order_state` persists it | `dump` / `update` |
| `coinbase/crypto_pair.py` | 238 | `write_order_state`: `from_order_id`, `update(msg)`, or on `FileNotFoundError` construct `OrderTree(msg)` and `dump()` | constructor takes the dict. A missing `_id` is filled from `order_id` or `id`. |
| `gemini/strats/single_symbol_strategy.py` | 37, 92 | `GeminiMeta.get_or_create`, `from_id` | metadata cache |
| `gemini/strats/single_symbol_strategy.py` | 166 | same `write_order_state` pattern | same |
| `gemini/strats/tree_strat.py` | 168 | `combine_orders(orders, size_prec=8, price_prec=4, complete=True)` | ids are read from `order._id` before the files are moved, then completed and removed |
| `gemini/strats/tree_strat.py` | 213, 220 | `set_order_id`, `update(staged=False, **new_order)` | keyword `update` |
| `gemini/strats/dca.py` | 80 | `stage_order(...).data` | `.data` |
| `gemini/private_client.py` | 159 | `set_order_id` | same |
| `orders/states.py` | 23, 34 | `CoinbaseOrder` / `GeminiOrder` `from_order_id` + `update` or construct and `dump` | same create path |
| `scripts/run_coinbase.py` | 69 | `CoinbaseOrder.reset_order_id_cache()` | rebuilds the id map from files in that directory |
| `scripts/run_gemini.py` | 52 | `GeminiOrder.reset_order_id_cache()` | same. This is the exchange-root class, not the per-symbol class from `make_gemini_order_cache`. |
| `scripts/cancel-staged-order.py` | 35 | `delete_order_id` / `complete_order_id` | both |
| `scripts/scale-book.py` | 35 | `to_frame()`, then `df["staged"]` and `df.side` | empty frame still has those columns; `_id` is the index |
| `scripts/shift-book.py` | 39 | `to_frame()`, `from_id` | both |
| `scripts/stage-order.py` | 38 | keyword `stage_order` | same as `place-order.py` |
| `scripts/join-inside.py` | 74 | `stage_order` | same |

`get_previous_order` only follows `parent_id`. The coinbase and gemini strategies write a `previous_order` field and do not set `parent_id` in the calls above, so `get_previous_order()` stays `None` unless some other writer stored `parent_id`. That matches the old method, which also only read `parent_id`.

## Gaps closed while checking these calls

- `from order_trees.order_tree import ID` failed. `dca.py`, `trailing_stops.py`, and the import list in `target_weights.py`'s sibling module need it. `ID` is exported from that module again.
- `getattr(order, "order_id")` in `order_monitor.py` never saw the field, because the old class had no such attribute and the fallback only runs for a dict. `OrderTree.order_id` reads the field.
- `set_order_id(4321)` and a later `from_order_id("4321")` missed the map when the key types differed. The map now keeps both the original key and `str(order_id)`. `reset_order_id_cache` does the same, and it loads each file with `from_id` so it does not recurse through a half-built map.
- `combine_orders(..., complete=True)` moved the other files and then read `order["_id"]` from the object whose active file was already gone. Reloading that missing file produced an empty document and `KeyError`. The id is taken from `order._id` before the move.
- `to_frame()` on an empty directory used to have no columns, so `df["staged"]` in the cybermoney scale script raised. An empty frame now has the usual order columns and an `_id` index.
- A numpy scalar stored with `metadata[key] = price * numpy.exp(...)` is written as a plain int or float. The codec does not import numpy.

Tests for these patterns are in `tests/test_legacy_usage.py`.
