from __future__ import annotations
from sqlglot import exp, parse_one, alias
from sqlglot.optimizer.eliminate_joins import join_condition
from sqlglot.helper import name_sequence
from sqlglot.optimizer.scope import Scope
from collections import defaultdict
from .constants import PlausibleBit, PlausibleType, PBit
from typing import Optional, List, TYPE_CHECKING, Dict, Any, Set, Sequence
import logging
from src.parseval.faker.domain import UnionFind

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

class PlausibleBranch(_Constraint):
    """Represents a plausible branch in the constraint tree."""
    def __init__(
        self,
        tree,
        parent,
        branch: bool,
        plausible_type: Optional[PlausibleType] = None,
        metadata: Dict[str, Any] = None,
    ):
        super().__init__(tree, parent)
        self.plausible_type = plausible_type
        self._branch = branch
        self.attempts = 0
        self.metadata = metadata or {}
        self.is_feasible: Optional[bool] = None
        self.plausible_type = plausible_type or PlausibleType.UNEXPLORED

class Constraint(_Constraint):
    PLAUSIBLE_CONFIGS = {
        "filter": (PBit.FALSE, PBit.TRUE),
        "join": (PBit.JOIN_TRUE, PBit.JOIN_LEFT, PBit.JOIN_RIGHT),
        "project": (PBit.TRUE, PBit.NULL, PBit.DUPLICATE),
        "groupby": (PBit.GROUP_SIZE, PBit.GROUP_COUNT),
        "aggregate": (PBit.GROUP_SIZE, PBit.NULL, PBit.DUPLICATE),
        "predicate": (PBit.FALSE, PBit.TRUE),
        "sort": (PBit.TRUE, PBit.MAX, PBit.MIN),
        "having": (PBit.HAVING_TRUE, PBit.HAVING_FALSE),
    }

    def __init__(
        self,
        tree,
        sql_condition: Optional[exp.Expression] = None,
        parent=None,
        children: Optional[Dict[str, Constraint]] = None,        
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(tree, parent)
        self.children = children or {}
        self.sql_condition = sql_condition
        self.metadata = metadata if metadata is not None else {}
        self.delta = defaultdict(list)


class CoverageConstraints:
    """Tracks coverage constraints for expressions."""

    def __init__(self, context: Dict[str, Any], scope: Scope, table_alias: List[exp.Table], dialect: str, limit: int = 10):
        self.scope = scope
        self.context = context
        self.table_alias = table_alias
        self.dialect = dialect
        
        self.scans = []
        self.projections: Sequence[exp.Expression] = []
        self.joins = {}
        self.table_predicates: Sequence[exp.Predicate] = []
        self.quantified_predicates = []
        self.group_by = {"by": [], "operands": [], "aggregations": []}
        self.having = []
        self.sort_by = []
        self.limit = limit
        self.offset = 0
        
        self.uf = UnionFind()
        
        self._dependencies: Set['CoverageConstraints'] = set()
        self._dependents: Set['CoverageConstraints'] = set()

    def _build2(self):
        from sqlglot.planner import Plan, Join, Scan, Aggregate, SetOperation, Sort
        plan = Plan(self.scope.expression)
        dag = {}
        nodes = {plan.root}
        self.limit = plan.root.limit if plan.root.limit is not None else self.limit
        self.projections = plan.root.projections
        while nodes:
            node = nodes.pop()
            if isinstance(node, Scan):
                self.scans.append(node.source)
                if node.condition:
                    self._extract_predicate(node.condition)
            elif isinstance(node, Join):
                self.joins.update(node.joins)
                if node.condition:
                    self._extract_predicate(node.condition)
            elif isinstance(node, Aggregate):
                for agg in node.aggregations:
                    if isinstance(agg, exp.Predicate):
                        self.having.append(agg)
                    elif isinstance(agg, exp.Alias):
                        if isinstance(agg.this, exp.Predicate):
                            self.having.append(agg.this)
                    else:
                        self.group_by["aggregations"].append(agg)
                for key, g in node.group.items():
                    self.group_by.setdefault("by", []).append(g)
            elif isinstance(node, SetOperation):
                raise NotImplementedError("Set operations not yet supported in _build2")
            elif isinstance(node, Sort):
                self.sort_by = node.key
            else:
                raise NotImplementedError(f"Node type {type(node)} not yet supported in _build2")
            dag[node] = set()
            for dep in node.dependencies:
                dag[node].add(dep)
                nodes.add(dep)
    
    def get_grouped_constraints(self, table_name: str):
        
        
        
        # for 
        ...
        
    def _extract_predicate(self, predicates: exp.Expression):
        # from sqlglot.optimizer import simplify
        stack = [predicates]
        visited = set()
        nodes = []
        # logging.info(stack)
        while stack:
            current = stack.pop()
            if current in visited:
                continue
            if isinstance(current, exp.SubqueryPredicate):
                self.quantified_predicates.append(current)
            elif isinstance(current, (exp.And, exp.Or)):
                stack.extend(current.flatten())
            elif isinstance(current, exp.Predicate):
                nodes.append(current)
                visited.add(current)
            elif isinstance(current, exp.Not):
                stack.append(current.this)
            else:
                raise ValueError(f"Unexpected predicate type: {type(current)}")
            visited.add(current)
        for n in nodes:
            self.table_predicates.append(n)


from src.parseval.query import preprocess_sql
from src.parseval.instance import Instance
from sqlglot.optimizer.scope import Scope, traverse_scope, walk_in_scope, find_all_in_scope, build_scope

class SpeculativeAssigner:
    """Handles speculative assignment of plausible branches."""
    def __init__(self, sql: str, ddls: str, dialect: str = 'sqlite'):
        self.sql = sql
        self.ddls = ddls
        self.dialect = dialect
        instance = Instance(ddls=ddls, name="test2", dialect=dialect)
        self.expr = preprocess_sql(sql, instance.catalog, dialect=dialect)
        self.scopes: List[Scope] = []
        self.table_aliases: Dict[str, str] = {}
        self.context = {}
    
    def _build(self):
        for t in self.expr.find_all(exp.Table):
            self.table_aliases[t.alias_or_name] = t.name
        self.scopes = list(traverse_scope(self.expr))
        
    def assign(self, constraints: CoverageConstraints):
        ...
    
    def generate(self):
        ...
    
    def _assign(self, scope: Scope):
        coverage_constraints = CoverageConstraints(context= self.context, scope=scope, table_alias=self.table_aliases, dialect=self.dialect)
        coverage_constraints._build2()
        
        # 1. Joins
        for join_alias, join in coverage_constraints.joins.values():
            pass
            # self._satisfy_join(join, db)
        ...
        
    
    def assign_join(self):
        ...
        
    def assign_table_predicate(self):
        ...