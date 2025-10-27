from __future__ import annotations
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from src.parseval.symbol import Variable, Symbol, Condition


@dataclass
class ValueAssignment:
    column: str
    alias: str
    value: Any
    data_type: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SolverResult:
    status: str
    assignments: List[ValueAssignment] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SolverAdapter:
    def __init__(self, name: str):
        self.name = name

    def supports(
        self,
        variables: List[Variable],
        constraints: List[Condition],
        context: Dict[str, Any],
    ) -> bool:
        pass

    def solve(
        self,
        variables: List[Variable],
        constraints: List[Condition],
        context: Dict[str, Any],
    ) -> SolverResult:
        pass
