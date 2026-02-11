from __future__ import annotations
from ..constants import PlausibleBit, PlausibleType
from typing import Optional, List, TYPE_CHECKING
import logging

if TYPE_CHECKING:
    from .ptree import UExprToConstraint

logger = logging.getLogger("parseval.uexpr")


class _Constraint:
    __slots__ = (
        "tree",
        "parent",
        "path",
        "_pattern",
        "_hash",
    )

    def __init__(self, tree, parent: Optional[_Constraint] = None):
        self.tree = tree
        self.parent = parent
        self.path = None
        self._pattern = None
        self._hash = None

    def bit(self) -> PlausibleBit:
        if self.parent is not None:
            for k, v in self.parent.children.items():
                if v is self:
                    return k

    def hit(self):
        if self.parent is not None and self.parent.step != "ROOT":
            bit = self.bit()
            return len(self.parent.symbolic_exprs[bit])
        return 0

    def get_path_to_root(self) -> List[_Constraint]:
        if self.path is not None:
            return self.path
        parent_path = []
        if self.parent is not None:
            parent_path = self.parent.get_path_to_root()
        self.path = parent_path + [self]
        return self.path

    def pattern(self):
        path = self.get_path_to_root()
        if self._pattern is not None:
            return self._pattern
        bits = [node.bit() for node in path[2:]]
        self._pattern = tuple(bits)
        return self._pattern

class _ScopeManager:
    """
    Internal context manager class to handle saving and restoring the Trace state.
    This ensures state is always reset correctly, even upon exception.
    """

    def __init__(self, trace_instance: "UExprToConstraint", step):
        self.trace = trace_instance
        self.step = step

    def __enter__(self) -> "UExprToConstraint":
        # 1. Notify Tracer we are entering an operator
        self.trace.on_scope_enter(self.step)
        return self.trace

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            logger.error(f"Scope failed for {self.step}. Rolling back state.")
            return False  # Propagate the exception upwards
        self.trace.on_scope_exit(self.step)
        return False
