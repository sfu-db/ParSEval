import typing as t

from src.symbols.base import SymbolicType
from src.symbols.ztypes.symbolic_bool import SymbolicBool
import z3
import datetime

Z3SymbolOrLiteral = t.Union[
    z3.StringSort, z3.IntSort, z3.RealSort, z3.BoolSort, str, float, int, bool, t.List, t.Tuple, datetime.date, datetime.datetime
]

Symbols = t.Union[SymbolicType, SymbolicBool]

Literals = t.Union[str, float, int, bool, datetime.date, datetime.datetime]


SymbolAndMultiplicity = t.NewType('SymbolAndMultiplicity', t.Tuple[Symbols, Symbols])

# multiplicity
