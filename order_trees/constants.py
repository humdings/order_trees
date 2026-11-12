"""String constants for the historical record class.

``FileMap`` and ``DirMap`` do not read these.
"""

FILE_EXT = ".json"
ID = "_id"

ORDER_ID_FIELD = "order_id"
PARENT_ID = "parent_id"
NEXT_BUY_ID = "next_buy_id"
NEXT_SELL_ID = "next_sell_id"
SIDE = "side"
BUY = "buy"
SELL = "sell"
STAGED = "staged"
TARGET_PRICE = "target_price"
STOP_PRICE = "stop_price"
AMOUNT = "amount"
REMAINING_AMOUNT = "remaining_amount"
SYMBOL = "symbol"
ACCOUNT = "account"

ORDER_FIELDS = (
    ID,
    ORDER_ID_FIELD,
    SYMBOL,
    SIDE,
    AMOUNT,
    REMAINING_AMOUNT,
    TARGET_PRICE,
    STOP_PRICE,
    STAGED,
    ACCOUNT,
    "size",
    "original_amount",
    "original_side",
    "_order_type",
    "_dt",
    PARENT_ID,
    NEXT_BUY_ID,
    NEXT_SELL_ID,
)
