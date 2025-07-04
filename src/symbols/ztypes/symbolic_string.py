
from __future__ import annotations
from ..base import SymbolicType
from ..ssa_factory import  create_symbol
import typing as t
import logging, z3, string, random, re

logger = logging.getLogger(__name__)

class SymbolicString(SymbolicType):
    __slots__ = 'length'
    def __init__(self, context, expr, value=None) -> None:
        if isinstance(expr, str):
            expr = expr.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        if value is None:
            value = str(expr).replace("\"", "", 1).replace("\"", "", -1)
        value = str(value)
        super().__init__(context, expr, value)

        self.length = create_symbol('int', context, z3.Length(self.expr), value= len(value))

    def _zv(self, other):
        if isinstance(other, SymbolicString):
            return other.expr, other.value
        return z3.StringVal(str(other)) , str(other)

    def __contains__(self, other):
        e, v = self._zv(other)
        v_ = v in self.value
        e_ = z3.Contains(self.expr, e)
        return create_symbol('bool', self.context, e_, v_)

    def to_int(self):
        v_ = int(self.value)
        z3.SubString(self.expr, 0, 1) == "-"
        e_ = z3.If(z3.PrefixOf('-', self.expr), - z3.StrToInt(z3.SubString(self.expr, 1, z3.Length(self.expr) - 1)), z3.StrToInt(self.expr))
        return create_symbol('int', self.context, e_, v_)

    def startswith(self, prefix):
        e, v = self._zv(prefix)
        e_ = z3.PrefixOf( e, self.expr)
        v_ = self.value.startswith(v)
        return create_symbol('bool', self.context, e_, v_)

    def endswith(self, suffix):
        e, v = self._zv(suffix)
        e_ = z3.SuffixOf( e, self.expr)
        v_ = self.value.endswith(v)
        return create_symbol('bool', self.context, e_, v_)

    def substring(self, start, length):
        
        def sint(o):
            if isinstance(o, SymbolicType):
                return (o.expr, o.value)
            else:
                v = int(o)
                return (z3.IntVal(v), v)

        start_e_, start_v_ = sint(start)
        length_e_, length_v_ = sint(length)

        rz = z3.SubString(self.expr, start_e_, length_e_)
        rv = self.value[int(start_v_) - 1 : int(start_v_) + int(length_v_)]

        return create_symbol('string', self.context, rz, rv)

    def like(self, other):
        constraints = []      
        characters = string.ascii_letters + string.digits  # You can include other characters as needed
        start = 0
        base = ''
        for o in other:
            if o == '%':
                k = random.randint(1, 4)
                random_string = ''.join(random.choices(characters, k = k))
                if len(base):
                    constraints.append(z3.SubString(self.expr, start, len(base)) == str(base))
                    start = start + len(base)
                rz = z3.SubString(self.expr, start, k)
                constraints.append(rz == random_string)
                start = start + k
                base = ''
            elif o == '_':
                if len(base):
                    constraints.append(z3.SubString(self.expr, start, len(base)) == str(base))
                    start = start + len(base)
                constraints.append(z3.SubString(self.expr, start, 1) == random.choice(characters))
                start = start + 1
                base = ''
            else:
                base += o
        if not constraints:
            constraints.append(z3.Contains(self.expr, other))
        
        regex_pattern = other.replace('*', r'\*').replace('+', r'\+').replace('%', '.*').replace('_', '\w')
        if re.match(regex_pattern, str(self.value)):
            concrete = True
        else:
            concrete = False
        return create_symbol('bool', self.context, z3.And(constraints), concrete)
    

    
    
    def _make_binary(self, other: t.Any, op: str) -> SymbolicType:
        """
        Create a binary operation.
        
        Args:
            other: Value to compare with
            op: Comparison operator
            
        Returns:
            New symbolic boolean value
        """
        e, v = self._zv(other)
        expr = getattr(self.expr, f"__{op}__")(e)
        value = getattr(self.value, f"__{op}__")(v)
        return create_symbol(type(value).__name__, self.context, expr, value)

ops = [("add", "+")]
for (name, op) in ops:
    def make_method(op_name: str) -> t.Callable:
        def method(self: SymbolicType, other: t.Any) -> SymbolicType:
            return self._make_comparison(other, op_name)
        return method
    setattr(SymbolicString, f"__{name}__", make_method(name))
    setattr(SymbolicString, f"__r{name}__", make_method(name))

# for (name,op) in ops:
#     method  = "__%s__" % name
#     make_method(method,op)
#     rmethod  = "__r%s__" % name
#     make_method(rmethod,op)


