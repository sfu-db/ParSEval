from __future__ import annotations
from typing import Dict, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.parseval.symbol import FunctionDef


class FunctionRegistry:
    _registry: Dict[str, FunctionDef] = {}

    @classmethod
    def register(cls, f: FunctionDef):
        cls._registry[f.name] = f

    @classmethod
    def get(cls, name: str) -> Optional[FunctionDef]:
        return cls._registry.get(name)
