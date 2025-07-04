from src.symbols.ztype import *
from .zint import *
from .zstr import *


class ZMap(ZType):
    def __init__(self, value=None):
        self.expr = "MAP"
        if value is None:
            self.value = dict()
            self.size = 0
            logger.debug("  MAP: empty")
            return
        elif isinstance(value, ZMap):
            self.value = value.value
            self.size = value.size
        else:
            self.value = value
            self.size = len(value)
        logger.debug("  Map: %s" % ",".join("<%s: %s>" % (name.__str__(), val.__str__()) for name, val in self.value.items()))

    def __str__(self):
        if self.size == 0:
            return "  Map: nil"
        return "  Map: %s" % ",".join("<%s: %s>" % (name.__str__(), val.__str__()) for name, val in self.value.items())

    def get(self, key, default=None):
        if key.value in self.value:
            return self.get_index(key)
        else:
            return default

    def get_index(self, name):
        if isinstance(name, ZInt) or \
           isinstance(name, ZStr):
            name = name.value
        return self.value[name]

    def get_iter_at(self, index):
        return self.value.keys()[index.value]

    def store(self, name, val):
        if isinstance(name, ZInt) or \
           isinstance(name, ZStr):
            name = name.value
        if name not in self.value:
            self.size += 1
        self.value[name] = val
        logger.debug("  Map store: <%s: %s>" % (name, val))

    def contains(self, other):
        return ZType('nil', other.value in self.value)

    def __len__(self):
        return ZInt(self.size)

    def len(self):
        return self.size
