"""Speculative data generation via top-down constraint propagation.

The speculative component walks the Plan top-down — from "I want at least
one output row" backward through each operator — deriving what each table
needs. It produces requirements for BOTH positive and negative branches,
ensuring the generated database can distinguish equivalent from
non-equivalent queries.

Public API::

    from parseval.symbolic.speculate import speculate
    rows_per_table = speculate(plan, instance, dialect)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlglot import exp

from parseval.dtype import DataType, TypeFamily, type_family
from parseval.helper import normalize_name
from parseval.identity import (
    ColumnId,
    ColumnKind,
    RelationId,
    RelationKind,
    column_id,
    column_identity,
    identifier_name,
    physical_column,
    relation_id,
)
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.helper import to_literal
from parseval.plan.planner import (
    Aggregate,
    Filter,
    Having,
    Join,
    Limit,
    Project,
    Scan,
    SetOperation,
    Sort,
    SubPlan,
)
from parseval.plan.rex import Environment, column_meta, concrete, negate_predicate
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var, solver_var
from parseval.solver.types import col_type

from .evaluator import PlanEvaluator
from .types import BranchTree, BranchType

logger = logging.getLogger("parseval.speculate")


# =============================================================================
# Schema lookup helpers
# =============================================================================


def _table_name(relation: RelationId) -> str:
    """Extract normalized table name from a RelationId."""
    return relation.name.normalized if relation.name else ""


def _lookup_col_type(
    instance: Instance, relation: RelationId, col_name: str
) -> Optional[str]:
    """Look up column type with case-insensitive fallback."""
    table = _table_name(relation)
    schema = instance.tables.get(table)
    if not schema:
        return None
    # Direct lookup first.
    dtype = schema.get(normalize_name(col_name))
    if dtype:
        return dtype
    # Case-insensitive fallback.
    lower = col_name.lower()
    for schema_col, schema_dtype in schema.items():
        if schema_col.lower() == lower:
            return schema_dtype
    return None


def _column_type_family(
    instance: Instance,
    relation: RelationId,
    col_name: str,
    default: TypeFamily = TypeFamily.TEXT,
) -> TypeFamily:
    """Resolve the TypeFamily for a column from the instance schema."""
    col_type_str = _lookup_col_type(instance, relation, col_name)
    if not col_type_str:
        return default
    try:
        return type_family(DataType.build(col_type_str))
    except Exception:
        return default


def _match_column(
    instance: Instance, relation: RelationId, col_name: str
) -> Optional[str]:
    """Find the canonical column name in the instance (case-insensitive)."""
    table = _table_name(relation)
    if table not in instance.tables:
        return None
    lower = col_name.lower()
    return next((s for s in instance.tables[table] if s.lower() == lower), None)


# =============================================================================
# Identity-aware column creation helpers
# =============================================================================


def _relation_for_table(instance: Instance, name: str) -> RelationId:
    """Get RelationId for a table name, creating a synthetic one if needed."""
    normalized = normalize_name(name)
    try:
        return instance.table_id(normalized)
    except (KeyError, Exception):
        return relation_id(RelationKind.TABLE, identifier_name(normalized))


def _annotate_col_type(
    col_node: exp.Column,
    instance: Instance,
    relation: RelationId,
    col_name: str,
) -> None:
    """Set type annotation from instance schema."""
    col_type_str = _lookup_col_type(instance, relation, col_name)
    if col_type_str:
        try:
            col_node.type = DataType.build(col_type_str)
        except Exception:
            pass


def _solver_column(
    instance: Instance,
    table: str,
    col_name: str,
    row_scope: Optional[str] = None,
) -> exp.Column:
    """Create a Column annotated with SolverVar + type from the instance schema.

    This is the KEY helper that replaces all string-based column creation.
    Every Column that enters a solver constraint must go through here (or
    _ensure_solver_var for columns already in the plan).
    """
    relation = _relation_for_table(instance, table)
    col_id = physical_column(col_name, relation)
    var = SolverVar(column_id=col_id, relation_id=relation, row_scope=row_scope)

    table_display = relation.display
    col_node = exp.column(col_name, table=table_display)
    set_solver_var(col_node, var)
    _annotate_col_type(col_node, instance, relation, col_name)
    return col_node


def _ensure_solver_var(col: exp.Column, instance: Instance) -> None:
    """Ensure a Column has SolverVar metadata.

    For Columns that already exist in the plan (and thus carry
    PARSEVAL_COLUMN_ID metadata set by the planner), this reads the
    identity and attaches a SolverVar. The solver requires every
    Column in constraints to carry both a type annotation and SolverVar.
    """
    if solver_var(col) is not None:
        return
    col_id = column_identity(col)
    if col_id is None:
        return
    # Derive relation from the column identity.
    if col_id.relation is not None:
        relation = col_id.relation
    else:
        relation = _relation_for_table(instance, col.table or "")
    var = SolverVar(column_id=col_id, relation_id=relation)
    set_solver_var(col, var)


# =============================================================================
# Constraint helper functions
# =============================================================================


def _make_is_not_null(col_node: exp.Column) -> exp.Is:
    """Create an IS NOT NULL constraint for a column."""
    return exp.Is(this=col_node, expression=exp.Not(this=exp.Null()))


def _make_is_null(col_node: exp.Column) -> exp.Is:
    """Create an IS NULL constraint for a column."""
    return exp.Is(this=col_node, expression=exp.Null())


def _has_is_not_null(
    constraints: List[exp.Expression], col_name: str
) -> bool:
    """Check if constraints already have IS NOT NULL for the given column."""
    for expr in constraints:
        if (
            isinstance(expr, exp.Is)
            and isinstance(expr.expression, exp.Not)
            and isinstance(expr.expression.this, exp.Null)
        ):
            for col in expr.find_all(exp.Column):
                if col.name == col_name:
                    return True
    return False


def _has_is_null(constraints: List[exp.Expression], col_name: str) -> bool:
    """Check if constraints already have IS NULL for the given column."""
    for expr in constraints:
        if isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Null):
            for col in expr.find_all(exp.Column):
                if col.name == col_name:
                    return True
    return False


def _has_equality_constraint(
    constraints: List[exp.Expression], col_name: str
) -> bool:
    """Check if constraints already have an EQ for the given column."""
    for expr in constraints:
        if isinstance(expr, exp.EQ):
            left = expr.this
            right = expr.expression
            if isinstance(left, exp.Column) and left.name == col_name:
                return True
            if isinstance(right, exp.Column) and right.name == col_name:
                return True
    return False


def _extract_fixed_values(
    constraints: List[exp.Expression],
) -> Dict[str, Any]:
    """Extract column->value mappings from EQ(column, literal) constraints."""
    values: Dict[str, Any] = {}
    for expr in constraints:
        if not isinstance(expr, exp.EQ):
            continue
        left, right = expr.this, expr.expression
        if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
            val = concrete(right)
            if val is not None:
                values[left.name] = val
        elif isinstance(right, exp.Column) and not isinstance(left, exp.Column):
            val = concrete(left)
            if val is not None:
                values[right.name] = val
    return values


# =============================================================================
# Data structures
# =============================================================================


@dataclass(frozen=True)
class RowBinding:
    """Transient mapping from a solver table key to one physical witness row."""

    relation: RelationId
    row: int

    @property
    def table(self) -> str:
        return self.relation.name.normalized if self.relation.name else ""

    @property
    def alias(self) -> Optional[str]:
        return self.relation.alias.normalized if self.relation.alias else None


def _solver_table_key(binding: RowBinding) -> str:
    """Build a unique solver table key for a row binding."""
    alias = normalize_name(binding.alias or binding.table)
    table = normalize_name(binding.table)
    return f"{table}__{alias}__r{binding.row}"


class ColumnUnionFind:
    """Union-Find for tracking column equivalence classes (JOIN, GROUP BY)."""

    def __init__(self) -> None:
        self._parent: Dict[ColumnId, ColumnId] = {}
        self._rank: Dict[ColumnId, int] = {}

    def find(self, x: ColumnId) -> ColumnId:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: ColumnId, y: ColumnId) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            self._parent[rx] = ry
        elif self._rank[rx] > self._rank[ry]:
            self._parent[ry] = rx
        else:
            self._parent[ry] = rx
            self._rank[rx] += 1

    def same(self, x: ColumnId, y: ColumnId) -> bool:
        return self.find(x) == self.find(y)

    def groups(self) -> Dict[ColumnId, List[ColumnId]]:
        result: Dict[ColumnId, List[ColumnId]] = {}
        for x in self._parent:
            rep = self.find(x)
            result.setdefault(rep, []).append(x)
        return result

    def members(self) -> Set[ColumnId]:
        return set(self._parent.keys())


@dataclass
class TableConstraint:
    """Constraints on what one table needs for a specific branch."""

    relation: RelationId
    constraints: List[exp.Expression] = field(default_factory=list)
    min_rows: int = 1
    duplicate_columns: List[ColumnId] = field(default_factory=list)
    group_key_columns: List[ColumnId] = field(default_factory=list)
    boundary_rows: List[Dict[ColumnId, Any]] = field(default_factory=list)

    @property
    def table(self) -> str:
        return self.relation.name.normalized if self.relation.name else ""

    @property
    def alias(self) -> Optional[str]:
        return self.relation.alias.normalized if self.relation.alias else None


@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""

    branch: str
    requirements: Dict[RelationId, TableConstraint] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)
    deferred: List[exp.Expression] = field(default_factory=list)

    def require(self, relation: RelationId) -> TableConstraint:
        """Get or create the TableConstraint for a relation."""
        if relation not in self.requirements:
            self.requirements[relation] = TableConstraint(relation=relation)
        return self.requirements[relation]

    def equate(self, col_a: ColumnId, col_b: ColumnId) -> None:
        """Declare two columns must have the same value."""
        self.equivalences.union(col_a, col_b)


# =============================================================================
# SpeculateConfig
# =============================================================================


@dataclass
class SpeculateConfig:
    """Configuration for speculative data generation.

    Each field controls how many rows to generate for that branch type.
    Set to 0 to skip that branch type entirely.

    Attributes:
        positive: Number of positive witness rows (satisfy all conditions).
        negative: Number of negative rows per filter conjunct (violate WHERE).
        null: Number of NULL rows per nullable column.
        left_unmatched: Number of left-table rows with no join match.
        right_unmatched: Number of right-table rows with no join match.
        having_fail: Number of rows that fail HAVING conditions.
        case_else: Number of rows exercising CASE WHEN ELSE arms.
        boundary: Number of boundary value rows for edge-case testing.
    """

    positive: int = 1
    negative: int = 1
    null: int = 1
    left_unmatched: int = 1
    right_unmatched: int = 1
    having_fail: int = 1
    case_else: int = 1
    boundary: int = 1

    @classmethod
    def gold_non_empty(cls) -> SpeculateConfig:
        """Config for generating only positive witness rows."""
        return cls(
            positive=1,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=1,
            boundary=0,
        )

    @classmethod
    def full_coverage(cls) -> SpeculateConfig:
        """Config for full branch coverage (all branch types)."""
        return cls(
            positive=1,
            negative=1,
            null=1,
            left_unmatched=1,
            right_unmatched=1,
            having_fail=1,
            case_else=1,
            boundary=1,
        )

    @classmethod
    def from_thresholds(cls, thresholds) -> SpeculateConfig:
        """Derive a SpeculateConfig from CoverageThresholds.

        Maps coverage requirements to generation strategy:
        - atom_true, filter_true, join_match -> positive
        - atom_false, filter_false -> negative
        - atom_null -> null
        - join_no_match -> left_unmatched + right_unmatched
        - having_fail -> having_fail
        - case_arm_skipped -> case_else
        """
        from .types import CoverageThresholds

        if not isinstance(thresholds, CoverageThresholds):
            return cls.full_coverage()

        # Positive: need if any "pass" outcome is required
        positive = (
            1
            if any(
                [
                    thresholds.atom_true > 0,
                    thresholds.filter_true > 0,
                    thresholds.join_match > 0,
                    thresholds.having_pass > 0,
                    thresholds.case_arm_taken > 0,
                    thresholds.exists_true > 0,
                    thresholds.exists_false > 0,
                    thresholds.in_match > 0,
                    thresholds.in_no_match > 0,
                    thresholds.group_single > 0,
                    thresholds.group_multi > 0,
                    thresholds.distinct_unique > 0,
                    thresholds.distinct_duplicate > 0,
                ]
            )
            else 0
        )

        # Negative: need if any "fail" outcome is required
        negative = (
            1
            if any(
                [
                    thresholds.atom_false > 0,
                    thresholds.filter_false > 0,
                ]
            )
            else 0
        )

        # Null: need if atom_null is required
        null = 1 if thresholds.atom_null > 0 else 0

        # Unmatched joins: need if join_no_match is required
        left_unmatched = 1 if thresholds.join_no_match > 0 else 0
        right_unmatched = 1 if thresholds.join_no_match > 0 else 0

        # Having fail: need if having_fail is required
        having_fail = 1 if thresholds.having_fail > 0 else 0

        # Case else: need if case_arm_skipped is required
        case_else = 1 if thresholds.case_arm_skipped > 0 else 0

        # Boundary: always generate for edge-case testing
        boundary = 1 if positive > 0 else 0

        return cls(
            positive=positive,
            negative=negative,
            null=null,
            left_unmatched=left_unmatched,
            right_unmatched=right_unmatched,
            having_fail=having_fail,
            case_else=case_else,
            boundary=boundary,
        )

    def should_generate(self, branch_type: str) -> bool:
        """Check if a branch type should be generated based on config."""
        mapping = {
            "positive": self.positive,
            "negative": self.negative,
            "null": self.null,
            "left_unmatched": self.left_unmatched,
            "right_unmatched": self.right_unmatched,
            "having_fail": self.having_fail,
            "case_else": self.case_else,
            "boundary": self.boundary,
        }
        return mapping.get(branch_type, 0) > 0


# =============================================================================
# Propagator: top-down constraint derivation
# =============================================================================

# (Task 2: Propagator class will be added here)


# =============================================================================
# Resolver: solver integration and row materialization
# =============================================================================

# (Task 3: Resolver class will be added here)


# =============================================================================
# Public API
# =============================================================================

# (Task 3: speculate() function will be added here)
