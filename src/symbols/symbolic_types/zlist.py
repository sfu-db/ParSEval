from src.symbols.ztype import *

from .zint import *

logger = logging.getLogger('app.smt')

class ZList(ZType):
    def __init__(self, value=None):
        self.expr = "LIST"
        if value is None:
            self.value = []
            self.size = 0
            logger.debug("  List: empty")
            return
        elif isinstance(value, ZList):
            self.value = value.value
            self.size = value.size
        else:
            self.value = value
            self.size = len(value)
        logger.debug("  ZList: %s" % ",".join(val.__str__() for val in self.value))

    def append(self, element):
        self.value.append(element)
        self.size += 1
        logger.debug("  List append: %s" % element)

    def get_index(self, index=0):
        if isinstance(index, ZInt):
            index = index.value
        return self.value[index]

    def get_slice(self, start=None, stop=None):
        if isinstance(start, ZInt):
            start = start.get_concrete()
        if isinstance(stop, ZInt):
            stop = stop.get_concrete()
        return ZList(self.value[start:stop])

class ZTuple(ZType):
    def __init__(self, value):
        self.expr = "Tuple"
        self.value = value
        logger.debug("  Tuple: %s" % str(self.value))

    def __str__(self):
        return "  Tuple: %s" % str(self.value)
