from src.symbols.ztype import *
from .zint import*
from .zlist import *


class ZStr(ZType):
    def __init__(self, expr, value=None):
        if isinstance(expr, str):
            expr = expr.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        self.expr = expr

        if value is None:
            self.value = expr.replace("\"", "", 1).replace("\"", "", -1)
        else:
            self.value = value
        logger.debug("  ZStr, value: %s, expr: %s" % (self.value, self.expr))

    def __add__(self, other):
        value = self.value + other.value
        expr = ["str.++", self.expr, other.expr]
        return ZStr(expr, value)

    def __contains__(self, other):
        value = self.value.contains(other.value)
        expr = ["str.contains", self.expr, other.expr]
        return ZType(expr, value)

    def __len__(self):
        value = len(self.value)
        expr = ["str.len", self.expr]
        return ZInt(expr, value)

    def length(self):
        value = len(self.value)
        expr = ["str.len", self.expr]
        return ZInt(expr, value)
    
    def integer(self):
        value = int(self.value)
        expr = ["ite", ["str.prefixof", "\"-\"", self.expr],
                ["-", ["str.to.int", 
                       ["str.substr", self.expr, "1", ["-", ["str.len", self.expr], "1"]]
                      ]
                ],
                ["str.to.int", self.expr]
               ]
        return ZInt(expr, value)
    
    def get_index(self, index):
        if isinstance(index, int):
            index = ZInt(index, index)
        value = self.value[index.value]
        if index.value < 0:
            expr = ["str.at", self.expr, ["+", ["str.len", self.expr], index.expr]]
        else:
            expr = ["str.at", self.expr, index.expr]
        return ZStr(expr, value)