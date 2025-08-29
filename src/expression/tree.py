from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional, Iterable, Dict, Any, Sequence, TYPE_CHECKING, Union
from enum import auto, Enum
import itertools
from sqlglot.planner import Plan

from .helper import negate_sql_condition

if TYPE_CHECKING:
    from sqlglot import exp

class _Node(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.kind = clsname.lower()[:-5]
        klass.__doc__ = klass.__doc__ or ""
        return klass

class Node(metaclass = _Node):
    _id_counter = itertools.count()
    kind = "node"
    def __init__(self, *, name: Optional[str] = None,
                sql_expr: Optional[Union[exp.Expression, List[exp.Expression]]] = None,
                metadata: Dict[str, Any] = field(default_factory=dict),
                schema_manager: Optional[Any] = None):
        self.name = name or f"{self.kind}_{next(self._id_counter)}"
        self.sql_expr = sql_expr
        self.metadata = metadata
        self.schema_manager = schema_manager
        self.children: Set[Node] = set()
        self.parent: Optional[Node] = None
        self.branch_type = None
        self.path = None

    def add_child(self, child: "Node") -> "Node":
        """Add a child node to this node."""
        self.children.add(child)
        child.parent = self
        return child

    def is_root(self) -> bool:
        return self.parent is None
    
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def find_by_expr(self, sql_expr: exp.Expression) -> Optional["Node"]:
        """Find a node by its SQL expression."""
        for dep in self.children:
            if self.sql_expr == sql_expr:
                return dep
        return None

    def find(self, name: str) -> Optional["Node"]:
        """Find a node by name in the current node and its dependencies."""
        if self.name == name:
            return self
        for dep in self.children:
            found = dep.find(name)
            if found:
                return found
        return None

    def enumerate_paths(self, path: Optional[List["Node"]] = None) -> Iterable[List["Node"]]:
        """Default DFS: single-linear expansion through dependencies."""
        if path is None:
            path = []
        here = path + [self]
        if not self.children:
            yield here
        else:
            for ch in self.children:
                yield from ch.enumerate_paths(here)

    def __repr__(self):
        return f"{self.kind}{self.name}({self.sql_expr})"

class TableRefNode(Node):

    def __init__(self, *, name, table: exp.Expression, alias: Optional[str] = None, schema_manager = None):
        super().__init__(name=name, sql_expr=table, metadata={"table": table, "alias": alias or table.alias_or_name}, schema_manager=schema_manager)
        self.table = table
        self.alias = alias or table.alias_or_name


class PredicateNode(Node):
    """Represents a predicate in the query.
        kind: {filter, having, on, join, case-branch, post-projection}
        relates_to: e.g., CASE alias for case-branch nodes.
    """
    def __init__(self, *, name, sql_expr, relates_to: Optional[str] = None, flipped: bool = False, schema_manager: Optional[Any] = None):
        super().__init__(name=name, sql_expr=sql_expr, metadata={"relates_to": relates_to, "flipped": flipped}, schema_manager= schema_manager)
        self.flipped = flipped
    
    def flip(self, branch_type = None) -> "PredicateNode":
        return PredicateNode(
            sql_expr=negate_sql_condition(self.sql_expr),
            relates_to=self.metadata.get("relates_to"),
            flipped=not self.flipped,
            schema_manager=self.schema_manager
        )

class ProjectionNode(Node):
    """Projection including DISTINCT and derived columns (aliases).
    derived: alias -> {"type": "CASE", "alias": <case_alias>} (can be extended later)
    """
    def __init__(self, *, name = None, columns: List[exp.Expression], derived: Optional[Dict[str, Dict[str, Any]]], schema_manager = None):
        super().__init__(name=name, sql_expr=columns, metadata={"columns": columns, "derived": derived or {}}, schema_manager= schema_manager)
        self.columns = columns
        self.derived = derived or {}

