from src.symbols.ztype import *
from .zint import*
from .zlist import *


class ZFloat(ZType):
    def __init__(self, expr=None, value=None) -> None:
        if value is None:
            if isinstance(expr, float):
                self.value = expr
            else:
                self.value = float(expr)
        else:
            self.value = value

        self.expr = expr
    
    def __str__(self):
        return "{ZFloat, value: %s, expr: %s)" % (self.value, self.expr)

    def negate(self):
        self.value = - self.value
        self.expr = 0 - self.expr
        # ["-", 0, self.expr]
    
    def do_abs(self):
        value = abs(self.value)
        expr = ["ite", [">=", self.expr, 0], self.expr, ["-", 0, self.expr]]
        return ZFloat(expr, value)
    

ops = [("add", "+", "+"),
       ("sub", "-", "-"),
       ("mul", "*", "*"),
       ("mod", "%", "mod"),
       ("truediv", "/", "div"),
       ("radd", "+", "+"),
       ("rsub", "-", "-"),
       ("rmul", "*", "*"),
       ("rmod", "%", "mod"),
       ("rtruediv", "/", "div"),
       ("floordiv", "//", "div"),
       ("and", "&", "&"),
       ("or", "|", "|"),
       ("xor", "^", "^"),
       ("lshift", "<<", "bvshl"),
       ("rshift", ">>", "bcshr")]


def make_method(method, op, op_smt):
    code = "def %s(self, other):\n" % method
    code += "   if isinstance(other, int):\n"
    code += "      other = ZFloat(other)\n"
    code += "   value = float(self.value %s other.value)\n" % op
    code += "   expr = [\"%s\", self.expr, other.expr]\n" % op_smt
    code += "   return ZFloat(expr, value)"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(ZFloat, method, locals_dict[method])

for (name, op, op_smt) in ops:
    method = "__%s__" % name
    make_method(method, op, op_smt)
    rmethod = "__r%s__" % name
    make_method(rmethod, op, op_smt)