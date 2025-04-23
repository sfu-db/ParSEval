import typing as t
if t.TYPE_CHECKING:
    from src.symbols.ztype import ZType
    # from core.common.symbolic.na import NULL
import z3
import datetime

SymbolLiterals = t.Union[z3.StringSort, z3.IntSort, z3.RealSort, z3.BoolSort, z3.Datatype]
TermLiterals = t.Union[str, float, int, bool, datetime.date, datetime.datetime]
TermOrLiteral = t.Union[
    ZType, str, float, int, bool, t.List, t.Tuple, datetime.date, datetime.datetime
]



