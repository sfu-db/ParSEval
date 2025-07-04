from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Union, List, Tuple

if TYPE_CHECKING:
    from .symbol import Symbol
  

SymbolOrName = Union['Symbol', str]

SymbolOrLiteral = Union['Symbol', str, float, int, bool, List, Tuple, datetime.date, datetime.datetime]

SymbolLiterals = Union[str, float, int, bool, List, Tuple, datetime.date, datetime.datetime, None]
