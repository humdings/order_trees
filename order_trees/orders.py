"""Order-record factories.

Nothing in here is used by ``FileMap`` or ``DirMap``. Callers that want the
old field set build it with ``new_order_record`` and store it themselves.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, MutableMapping

from order_trees.constants import (
    ACCOUNT,
    AMOUNT,
    ID,
    ORDER_ID_FIELD,
    REMAINING_AMOUNT,
    SIDE,
    STAGED,
    STOP_PRICE,
    SYMBOL,
    TARGET_PRICE,
)


def now_text() -> str:
    """Timestamp string for ``_dt``.

    Uses ``str(pandas.Timestamp.utcnow())`` when pandas is installed, which
    is what the old ``stage_order`` wrote. Otherwise an ISO-8601 UTC time.
    """
    try:
        import pandas as pd
    except ImportError:
        return datetime.now(timezone.utc).isoformat()
    return str(pd.Timestamp.utcnow())


def new_order_record(
    symbol: str,
    amount: Any,
    target_price: Any,
    side: str,
    staged: bool = True,
    stop_price: Any = None,
    order_type: Any = None,
    account: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    """Return an order dict. Does not touch the filesystem.

    ``**extra`` is applied last, so it can override any field, including
    ``_id`` and the link fields ``parent_id``, ``next_buy_id``, and
    ``next_sell_id``.
    """
    id_ = uuid.uuid4().hex
    data: dict[str, Any] = {
        ID: id_,
        ORDER_ID_FIELD: id_,
        SIDE: side,
        TARGET_PRICE: target_price,
        STAGED: staged,
        AMOUNT: amount,
        REMAINING_AMOUNT: amount,
        SYMBOL: symbol,
        STOP_PRICE: stop_price,
        ACCOUNT: account,
        "size": amount,
        "original_amount": amount,
        "original_side": side,
        "_order_type": order_type,
        "_dt": now_text(),
    }
    data.update(extra)
    return data


def stage_order(
    book: MutableMapping[str, Any],
    symbol: str,
    amount: Any,
    target_price: Any,
    side: str,
    staged: bool = True,
    stop_price: Any = None,
    order_type: Any = None,
    account: Any = None,
    **extra: Any,
) -> str:
    """Write one order into a directory mapping. Returns the ``_id``."""
    record = new_order_record(
        symbol,
        amount,
        target_price,
        side,
        staged=staged,
        stop_price=stop_price,
        order_type=order_type,
        account=account,
        **extra,
    )
    book[str(record[ID])] = record
    return str(record[ID])


def record_from_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    """Copy *data* without interpreting order fields. Helper for tests."""
    return dict(data)
