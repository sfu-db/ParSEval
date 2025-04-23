from src.symbols.ztype import *

class ZObject(ZType):
    def __init__(self):
        self.attrs = dict()

    def store_attr(self, name, value):
        self.attrs[name] = value

    def __str__(self):
        res = "{ZObj: ["
        for name, value in self.attrs.items():
            res += "<%s: %s>, " % (name, value)
        res += "]}"
        return res
            
    def has_attr(self, attr):
        if attr in self.attrs:
            return True
        else:
            return False

    def get_attr(self, attr):
        if attr in self.attrs:
            return self.attrs[attr]
        else:
            return None
