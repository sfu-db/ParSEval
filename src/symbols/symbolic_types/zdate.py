from src.symbols.ztype import *
from .zint import*
from .zlist import *


class ZDate(ZInt):
    def __init__(self, expr=None, value=None) -> None:
        super().__init__(expr, value)