
from __future__ import annotations

from typing import Tuple, List, Set, List, Optional, Tuple, Callable, Union, NewType
import enum

class State(enum.Enum):
    NULL = 0
    UNINIT = -1
    INIT = 1

class CoverageType(enum.Enum):
    PREDICATE = "Predicate"
    MULTIPLICITY = "Multiplicity"
    GROUP = 'Group'
    EXISTENCE = 'Existence'
    PATH = "Path"

operator_key = NewType('OperatorKey', str)
operator_id =  NewType('OperatorId', str)
constraint_id = NewType('Constraint_Id', str)
Location = Tuple[operator_key, operator_id, constraint_id]

class Coverage(object):
    """
    Track coverage of each operator.
    """

    def __init__(self) -> None:
        self._trace: List[Location] = []
    
    def trace(self) -> List[Location]:
        """The list of executed lines, as (operator_name, line_number) pairs"""
        return self._trace

    def coverage(self) -> Set[Location]:
        """The set of executed constraints and operator, as (operator, constraint id) pairs"""
        return set(self.trace())

    def function_names(self) -> Set[str]:
        """The set of function names seen"""
        return set(function_name for (function_name, line_number) in self.coverage())
    
    def operator_names(self) -> Set[str]:
        return set(f"{operator_key}#{operator_id}" for (operator_key, operator_id, *_) in self.coverage())

    def traceit(self,operator_key, operator_id, constraint, event: Union[CoverageType, str] = CoverageType.PATH, label = True) -> Optional[Callable]:
        self._trace.append((operator_key, operator_id, constraint, label))
        return self.traceit
    
    def _asert_coverage_type(self, event):
        if isinstance(event, CoverageType):
            return event        
        return CoverageType[str(event).capitalize()]