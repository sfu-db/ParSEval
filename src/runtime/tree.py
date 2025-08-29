from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional, Iterable, Dict, Any, Sequence, TYPE_CHECKING, Union
from enum import auto, Enum
import itertools
from sqlglot.planner import Plan

from .helper import negate_sql_condition

if TYPE_CHECKING:
    from sqlglot import exp

# self.name: t.Optional[str] = None
# self.dependencies: t.Set[Step] = set()
# self.dependents: t.Set[Step] = set()
# self.projections: t.Sequence[exp.Expression] = []
# self.limit: float = math.inf
# self.condition: t.Optional[exp.Expression] = None

class _Node(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.kind = clsname.lower()[:-5]
        klass.__doc__ = klass.__doc__ or ""
        return klass

class Node(metaclass = _Node):
    _id_counter = itertools.count()
    kind = "node"
    def __init__(self, sql_expr: Optional[Union[exp.Expression, List[exp.Expression]]] = None,
                 *,
                name: Optional[str] = None,
                metadata: Dict[str, Any] = field(default_factory=dict),
                schema_manager: Optional[Any] = None):
        self.sql_expr = sql_expr
        self.name = name or f"{self.kind}_{next(self._id_counter)}"
        self.metadata = metadata
        self.schema_manager = schema_manager

        self.dependencies: Set[Node] = set()
        self.dependents: Set[Node] = set()
        self.path = None

    def add_dependency(self, dependency: "Node") -> "Node":
        self.dependencies.add(dependency)
        dependency.dependents.add(self)
        return dependency

    def is_root(self) -> bool:
        return self.parent is None
    def is_leaf(self) -> bool:
        return len(self.dependencies) == 0


    def enumerate_paths(self, path: Optional[List["Node"]] = None) -> Iterable[List["Node"]]:
        """Default DFS: single-linear expansion through dependencies."""
        if path is None:
            path = []
        here = path + [self]
        if not self.dependencies:
            yield here
        else:
            for ch in self.dependencies:
                yield from ch.enumerate_paths(here)


class TableRefNode(Node):
    def __init__(self, table: exp.Expression, alias: Optional[str] = None):

        super().__init__(table, metadata={"table": table.name, "alias": alias or table.alias_or_name})
        self.table = table
        self.alias = alias or table.alias_or_name

class PredicateNode(Node):
    """Represents a predicate in the query.
    kind: {filter, having, on, join, case-branch, post-projection}
    relates_to: e.g., CASE alias for case-branch nodes.
    """
    def __init__(self, sql_expr, *, relates_to: Optional[str] = None, flipped: bool = False, schema_manager: Optional[Any] = None):
        super().__init__(sql_expr, metadata={"relates_to": relates_to, "flipped": flipped}, schema_manager= schema_manager)
        self.flipped = flipped

    def flip(self, label = None) -> "PredicateNode":
        return PredicateNode(
            sql_expr=negate_sql_condition(self.sql_expr),
            label=label or self.label,
            name= None,
            relates_to=self.metadata.get("relates_to"),
            flipped=not self.flipped
        )

class ProjectionNode(Node):
    """Projection including DISTINCT and derived columns (aliases).
    derived: alias -> {"type": "CASE", "alias": <case_alias>} (can be extended later)
    """
    def __init__(self, columns: List[exp.Expression], *, label, name = None, distinct: bool = False, derived: Optional[Dict[str, Dict[str, Any]]] = None, schema_manager: Optional[Any] = None):
        super().__init__(columns, label=label, name=name, metadata={"columns": columns, "distinct": distinct, "derived": derived or {}}, schema_manager= schema_manager)
        self.columns = columns
        self.distinct = distinct
        self.derived = derived or {}    

class JoinNode(Node):
    def __init__(self, sql_expr, *, label, join_type, name = None, schema_manager: Optional[Any] = None):
        super().__init__(sql_expr, label=label, name=name, metadata={"join_type": join_type}, schema_manager= schema_manager)
        self.join_type = join_type
        self.left: Optional[Node] = None
        self.right: Optional[Node] = None

class OrNode(Node):
    def __init__(self):
        super().__init__(name="Or")

    def enumerate_paths(self, path: Optional[List["Node"]] = None) -> Iterable[List["Node"]]:
        base = [] if path is None else list(path)
        base.append(self)
        if not self.dependencies:
            yield list(base)
            return
        for ch in self.dependencies:
            for p in ch.enumerate_paths(list(base)):
                yield p

# =========================================================
# Path specs + Coverage
# =========================================================

@dataclass(frozen=True)
class PathKey:
    sequence: Tuple[str, ...]    # ordered labels (constraints + set-ops names)

@dataclass
class PathSpec:
    key: PathKey
    constraints: List[str]                   # display-only
    constraint_tuples: List[Tuple[str,str,Any]]  # normalized for planning
    projection: Set[str]
    setops: List[str]                        # e.g. ["UNION"] or ["INTERSECT"] or []

# @dataclass
# class TuplePlan:
#     """A plan for tuples to generate for a single path."""
#     path: PathSpec
#     tuples: List[Dict[str, Any]] = field(default_factory=list)
#     note: str = ""

