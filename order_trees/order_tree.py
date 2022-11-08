import os
import logging
import shutil
import pandas as pd
from pprint import pprint

from order_trees.json_cache import JsonCache, FILE_EXT, ID

log = logging.getLogger(__name__)

# Various Constants/names used throughout.
# There's still some floating magic strings though.
ORDER_ID_FIELD = 'order_id'
PARENT_ID = 'parent_id'
NEXT_BUY_ID = 'next_buy_id'
NEXT_SELL_ID = 'next_sell_id'
SIDE = 'side'
BUY = 'buy'
SELL = 'sell'
STAGED = 'staged'
TARGET_PRICE = 'target_price'
STOP_PRICE = 'stop_price'
AMOUNT = 'amount'
REMAINING_AMOUNT = 'remaining_amount'
SYMBOL = 'symbol'


class OrderTree(JsonCache):
    """
    Order Book that manages a directory of JSON files containing order information.

    The _id field in the cache can be used to turn
    orders into tree and/or graph structures.

    Currently this is a tree structure:

    I wrote this pretty faded for fun. 
    It's very error prone if not careful. 
    """
    _CACHE = {}
    _ORDER_MAP = {}  # order_id ==> _id

    def __init__(self, data=None, order_id=None, directory=None):
        """
        I don't like the logic here, it was origianlly used 
        for a particular api and I'm too lazy to change it.
        """
        if order_id is None:
            order_id = data.get(ORDER_ID_FIELD) or data.get('id')
            # Should be rare, maybe it has an _ID
            if not order_id and ID in data:
                order_id = data[ID]
        if ORDER_ID_FIELD not in data and order_id is not None:
            data[ORDER_ID_FIELD] = order_id

        if ID not in data and ORDER_ID_FIELD in data:
            data[ID] = data[ORDER_ID_FIELD]

        super().__init__(data=data, directory=directory)

    @classmethod
    def all_order_ids(cls):
        return [
            f.strip(FILE_EXT) for f in os.listdir(cls.DIRECTORY)
            if f.endswith(FILE_EXT)
        ]

    @classmethod
    def staged_orders(cls):
        """
        Return all order with the `staged` field True
        """
        staged = []
        for oid in cls.all_order_ids():
            order = cls.from_order_id(oid)
            if order.is_staged():
                staged.append(order)

        return staged

    @classmethod
    def from_order_id(cls, order_id):
        order_id = cls._ORDER_MAP.get(order_id, order_id)
        return cls.from_id(order_id)

    @classmethod
    def lookup_order(cls, order_id):
        """
        Iterates through actual files to check if the
        id passed is the `order_id` and not the `_id`. 

        Choke point, but finds the order if it's there.

        Returns None if not found.
        """
        order_id = str(order_id)

        if order_id in cls._ORDER_MAP or order_id in cls._CACHE:
            return cls.from_order_id(order_id)

        all_ids = cls.all_order_ids()
        if order_id in all_ids:
            return cls.from_order_id(order_id)
        else:
            for oid in all_ids:
                order = cls.from_order_id(oid)
                other_oid = str(order[ORDER_ID_FIELD])
                # Make sure order_id is mapped.
                cls._ORDER_MAP[other_oid] = order[ID]
                if other_oid == order_id:

                    return order
        return None

    @classmethod
    def reset_order_id_cache(cls):
        """Iterate through actual files."""
        all_ids = cls.all_order_ids()
        for oid in all_ids:
            order = cls.from_order_id(oid)
            cls._ORDER_MAP[order[ORDER_ID_FIELD]] = oid

    @classmethod
    def delete_order_id(cls, order_id):
        try:
            order = cls.from_order_id(order_id)
            path = order.filename
            os.remove(path)
            del order
        except FileNotFoundError:
            pass

    @classmethod
    def complete_order_id(cls, order_id):
        """
        Move to /completed instead of deleting.
        """
        order = cls.lookup_order(order_id)
        if order is not None:
            order.move_completed()
        return order

    @classmethod
    def stage_order(cls,
                    symbol,
                    amount,
                    target_price,
                    side,
                    staged=True,
                    stop_price=None,
                    order_type=None,
                    account=None,
                    **kwargs):
        """
        Create a new order with arbitrary parameters.
        """
        id_ = cls.make_id()
        data = {
            ID: id_,
            ORDER_ID_FIELD: id_,  # Make it a default
            SIDE: side,
            TARGET_PRICE: target_price,
            STAGED: staged,
            AMOUNT: amount,
            REMAINING_AMOUNT: amount,
            SYMBOL: symbol,
            STOP_PRICE: stop_price,
            'size': amount,
            'original_amount': amount,
            'original_side': side,
            '_order_type': order_type,
            'account': account,
            '_dt': str(pd.Timestamp.utcnow())
        }
        data.update(kwargs)
        obj = cls(data=data)
        obj.dump()
        return obj

    @classmethod
    def combine_orders(cls,
                       order_list,
                       size_prec=7,
                       price_prec=2,
                       complete=False):
        """
        combines orders with the same side as 
        the first order in the list.

        Only price/size is considered.
        
        Returns a modified order with:
            target_price = size weighted average price
            amount = sum of the order sizes. 

        first order id in the list is kept,
        the rest are discarded.
        """
        if len(order_list) == 1:
            return order_list[0]

        def get_amount(order):
            """ helper to get size """
            amount = float(order[AMOUNT])
            if REMAINING_AMOUNT in order:
                amount = min(amount, float(order[REMAINING_AMOUNT]))
            return amount

        consuming_order = order_list[0]
        others = order_list[1:]

        total_size = get_amount(consuming_order)
        total_value = float(consuming_order[TARGET_PRICE]) * total_size
        side = consuming_order[SIDE]

        orders_included = []

        for order in others:
            if order[SIDE] != side:
                log.warn(f"[Crossed Order Sides combining] {order}")
                continue
            assert order[SIDE] == side
            amount = get_amount(order)
            price = float(order[TARGET_PRICE])
            total_size += amount
            total_value += amount * price

            orders_included.append(order)

        size = round(total_size, size_prec)
        target = round(total_value / total_size, price_prec)

        kwargs = {
            AMOUNT: size,
            TARGET_PRICE: target,
            '_dt': pd.Timestamp.utcnow(),
            'original_amount': size,
        }
        consuming_order.update(kwargs)
        if complete:
            for order in orders_included:
                cls.complete_order_id(order[ID])

        for order in orders_included:
            cls.delete_order_id(order[ID])

        return consuming_order

    def move_completed(self):
        self['_done'] = True
        src = self.filename
        dest = os.path.join(self.DIRECTORY, 'completed', self[ID] + FILE_EXT)
        try:
            shutil.move(src, dest)
        except FileNotFoundError:
            pass
        self._ORDER_MAP.pop(self[ORDER_ID_FIELD], None)
        self._ORDER_MAP.pop(self[ID], None)
        self._CACHE.pop(self[ID], None)
        return self

    def set_order_id(self, order_id):
        _id = self.data.get(ID, order_id)
        self._ORDER_MAP[order_id] = _id
        self[ORDER_ID_FIELD] = order_id

    def is_staged(self):
        return STAGED in self and self[STAGED]

    def is_triggered(self, price):
        """
        Returns True if the limit or stop price has been hit.
        """
        if not self.is_staged():
            return False

        if self.is_limit_triggered(price):
            return True

        if self.is_stop_triggered(price):
            return True

        return False

    def is_limit_triggered(self, price):
        if self[SIDE] == BUY:
            return float(price) <= float(self[TARGET_PRICE])
        elif self[SIDE] == SELL:
            return float(price) >= float(self[TARGET_PRICE])
        else:
            raise RuntimeError(f"Order Side not valid: {self.data}")

    def is_stop_triggered(self, price):
        stop = self.get(STOP_PRICE)
        if not stop:  # False, None, or 0 should work
            return False
        if self[SIDE] == BUY:
            return float(price) >= float(self[STOP_PRICE])
        elif self[SIDE] == SELL:
            return float(price) <= float(self[STOP_PRICE])
        else:
            raise RuntimeError(f"Order Side not valid: {self.data}")

    def get_next_buy(self):
        if NEXT_BUY_ID in self:
            return self.lookup_order(self[NEXT_BUY_ID])

    def get_next_sell(self):
        if NEXT_SELL_ID in self:
            return self.lookup_order(self[NEXT_SELL_ID])

    def get_children(self):
        return [self.get_next_buy(), self.get_next_sell()]

    def get_previous_order(self):
        if PARENT_ID in self:
            return self.lookup_order(self[PARENT_ID])

    def print_tree(self):
        buy = self.get_next_buy()
        if buy:
            buy.print_tree()
        pprint(self.data)
        sell = self.get_next_sell()
        if sell:
            sell.print_tree()

    def preorder_traverse(self, root):
        res = []
        if root:
            res.append(root.data)
            res = res + self.preorder_traverse(root.get_next_buy())
            res = res + self.preorder_traverse(root.get_next_sell())
        return res

    def find_root(self):
        previous = self.get_previous_order()
        if previous is None:
            return self
        return previous.find_root()

    def is_root(self):
        return PARENT_ID not in self.data

    def is_leaf(self, keep_closed=False):
        """
        Written for a specific api and probably not working.
        """
        if not keep_closed:
            if 'done_reason' in self:
                if self['done_reason'] in ('filled', 'canceled'):
                    return False
            if 'reason' in self:
                if self['reason'] in ('filled', 'canceled'):
                    return False
            if 'type' in self:
                if self['type'] == 'done':
                    return False
            if 'status' in self:
                if self['status'] in ('filled', 'done'):
                    return False
            if 'settled' in self:
                if self['settled']:
                    return False
        return (NEXT_BUY_ID not in self.data) and (NEXT_SELL_ID
                                                   not in self.data)
