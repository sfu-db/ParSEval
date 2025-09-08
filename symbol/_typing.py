from typing import TYPE_CHECKING, Union, List, Tuple
if TYPE_CHECKING:
    from .term import Term
    from .na import NULL
import z3
import datetime

TermLiterals = Union[str, float, int, bool, datetime.date, datetime.datetime, NULL]
TermOrLiteral = Union[
    Term, str, float, int, bool, List, Tuple, datetime.date, datetime.datetime
]

SymbolLiterals = Union[z3.StringSort, z3.IntSort, z3.RealSort, z3.BoolSort, z3.Datatype]

