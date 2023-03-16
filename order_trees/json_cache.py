import decimal
from pandas import Timestamp
import json
import os
import uuid

FILE_EXT = ".json"
ID = "_id"


class RobustJSONEncoder(json.JSONEncoder):
    """
    JSON Encoder that can handle Decimal and Timestamp objects.
    """

    def default(self, obj):

        if isinstance(obj, Timestamp):
            return str(obj)

        if isinstance(obj, decimal.Decimal):
            return str(obj)

        return super(RobustJSONEncoder, self).default(obj)


class JsonCache(object):
    """
    A dict-like class where state changes get written to a json file.

    Using the cache means changes in the file may not be seen by the program. 
    But it doesn't have to read files so often.
    """
    DIRECTORY = ''
    _CACHE = {}
    _USE_CACHE = True

    def __init__(self, data=None, directory=None):
        if directory is not None:
            self.DIRECTORY = directory
        if data is None:
            data = {}
        self._data = data
        if ID not in self:
            self[ID] = self.make_id()

    @property
    def data(self):
        return self._data

    @property
    def filename(self):
        return os.path.join(self.DIRECTORY, self[ID] + FILE_EXT)

    @property
    def _id(self):
        return self[ID]

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return other.data == self.data
        return other == self.data

    def __hash__(self):
        """
        This is a little funky but...
        """
        return hash(self[ID])

    def __repr__(self):
        return repr(self._data)

    def __getitem__(self, key):
        return self._data.__getitem__(key)

    def __setitem__(self, key, item):
        with self:
            return self._data.__setitem__(key, item)

    def __contains__(self, item):
        return self._data.__contains__(item)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.dump()

    def update(self, *args, **kwargs):
        with self:
            self.data.update(*args, **kwargs)

    def get(self, key, default=None):
        return self._data.get(key, default)

    @staticmethod
    def make_id():
        return uuid.uuid4().hex

    @classmethod
    def from_id(cls, id_hash):
        if (id_hash not in cls._CACHE) or (not cls._USE_CACHE):
            path = os.path.join(cls.DIRECTORY, id_hash + FILE_EXT)
            obj = cls.load(path)
            cls._CACHE[id_hash] = obj
        return cls._CACHE[id_hash]

    @classmethod
    def load(cls, filepath):
        with open(filepath, 'r') as f:
            data = json.load(f)
        return cls(data=data)

    def dump(self):
        with open(self.filename, 'w') as f:
            json.dump(self._data, f, cls=RobustJSONEncoder)

    @classmethod
    def get_or_create(cls, id_hash):
        try:
            return cls.from_id(id_hash)
        except FileNotFoundError:
            cache = cls(data={ID: id_hash})
            cache.dump()
            return cache
