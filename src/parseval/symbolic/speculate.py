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
import sqlite3
from datetime import date, datetime, time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.dtype import (
    DataType,
    TypeFamily,
    StorageLiteral,
    date_to_epoch_day,
    datetime_to_epoch_second,
    parse_date,
    parse_datetime,
    type_family,
)
from parseval.identity import (
    ColumnKind,
    ColumnId,
    PARSEVAL_COLUMN_ID,
    RelationId,
    column_id,
    column_identity,
    identifier_name,
    physical_column,
)
from parseval.instance import Instance
from parseval.domain.exceptions import ConstraintViolationError
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
    _step_expressions,
)
from parseval.plan.meta import column_meta
from parseval.plan.rex import concrete, negate_predicate
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var, solver_var
from parseval.solver.types import col_type
from .types import CoverageThresholds

logger = logging.getLogger("parseval.speculate")

_ROW_INTENT_GROUP_A = "group_a"
_ROW_INTENT_GROUP_B = "group_b"
_ROW_INTENT_MATCHED_RANK_LOWER = "matched_rank_lower"
_ROW_INTENT_UNMATCHED_RANK_TOP = "unmatched_rank_top"


# =============================================================================
# Schema lookup helpers
# =============================================================================


def _build_plan_meta_cache(plan: Plan) -> Dict[Tuple[str, str], dict]:
    """Scan plan step expressions for enriched columns and build a metadata cache.

    Returns a dict mapping (table_normalized, col_normalized) -> column_meta dict.
    Both keys are already normalized by the planner via identifier_name.
    """
    cache: Dict[Tuple[str, str], dict] = {}
    for step in plan.ordered_steps:
        for expr in _step_expressions(step):
            for col in expr.find_all(exp.Column):
                meta = column_meta(col)
                if meta is None:
                    continue
                col_id = column_identity(col)
                if col_id is None or col_id.relation is None or col_id.relation.name is None:
                    continue
                key = (col_id.relation.name.normalized, col_id.name.normalized)
                cache[key] = meta
                src = col_id.source_column_id
                if src and src.relation and src.relation.name:
                    cache.setdefault(
                        (col_id.relation.name.normalized, src.name.normalized), meta
                    )
    return cache



# =============================================================================
# Identity-aware column creation helpers
# =============================================================================

def _solver_column(
    instance: Instance,
    col_id: ColumnId,
    row_scope: Optional[str] = None,
    meta: Optional[dict] = None,
) -> exp.Column:
    """Create a Column annotated with SolverVar + type from plan metadata or instance schema.

    If ``meta`` is provided (from column_meta), its ``domain`` key is used for
    the column type. Otherwise, follows source_column_id chain for type lookup.
    """
    relation = col_id.relation
    col_name = col_id.name.normalized
    var = SolverVar(column_id=col_id, relation_id=relation, row_scope=row_scope)
    table_display = relation.display if relation else ""
    col_node = exp.column(col_name, table=table_display)
    col_node.meta[PARSEVAL_COLUMN_ID] = col_id
    set_solver_var(col_node, var)
    # Use provided meta["domain"] if available.
    if meta is not None and "domain" in meta:
        col_node.type = meta["domain"]
        return col_node
    # Fall back to source_column_id chain lookup.
    current = col_id
    for _ in range(10):
        if current is None:
            break
        table = current.relation.name.normalized if current.relation and current.relation.name else ""
        dtype = instance.tables.get(table, {}).get(current.name.normalized)
        if dtype:
            col_node.type = DataType.build(dtype)
            return col_node
        current = current.source_column_id
    return col_node



# =============================================================================
# Constraint helper functions
# =============================================================================


def _make_is_not_null(col_node: exp.Column) -> exp.Is:
    """Create an IS NOT NULL constraint for a column."""
    return exp.Is(this=col_node, expression=exp.Not(this=exp.Null()))


def _make_is_null(col_node: exp.Column) -> exp.Is:
    """Create an IS NULL constraint for a column."""
    return exp.Is(this=col_node, expression=exp.Null())


def _update_solver_var_identity(
    constraints: List[exp.Expression], col_name: str, plan_cid: ColumnId
) -> None:
    """Update existing IS NOT NULL constraint's SolverVar with plan identity."""
    for expr in constraints:
        if not isinstance(expr, exp.Is):
            continue
        if not isinstance(expr.expression, exp.Not):
            continue
        if not isinstance(expr.expression.this, exp.Null):
            continue
        col = expr.this
        if not isinstance(col, exp.Column):
            continue
        if col.name != col_name:
            continue
        sv = solver_var(col)
        if sv is not None and sv.column_id.scope_id is None:
            plan_sv = SolverVar(column_id=plan_cid, relation_id=sv.relation_id, row_scope=sv.row_scope)
            set_solver_var(col, plan_sv)
            return


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
    alias = binding.alias or binding.table
    table = binding.table
    scope = binding.relation.scope_id or ""
    return f"{table}__{alias}__{scope}__r{binding.row}"


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

    def groups(self) -> Dict[ColumnId, List[ColumnId]]:
        result: Dict[ColumnId, List[ColumnId]] = {}
        for x in self._parent:
            rep = self.find(x)
            result.setdefault(rep, []).append(x)
        return result



@dataclass
class TableConstraint:
    """Constraints on what one table needs for a specific branch."""

    relation: RelationId
    constraints: List[exp.Expression] = field(default_factory=list)
    min_rows: int = 1
    duplicate_columns: List[ColumnId] = field(default_factory=list)
    group_key_columns: List[ColumnId] = field(default_factory=list)
    ordered_columns: List[ColumnId] = field(default_factory=list)
    contrast_columns: List[ColumnId] = field(default_factory=list)
    distinct_columns: List[ColumnId] = field(default_factory=list)
    row_intents: Dict[int, Set[str]] = field(default_factory=dict)
    boundary_rows: List[Dict[ColumnId, Any]] = field(default_factory=list)

    @property
    def table(self) -> str:
        return self.relation.name.normalized if self.relation.name else ""

    @property
    def alias(self) -> Optional[str]:
        return self.relation.alias.normalized if self.relation.alias else None

    def mark_row(self, row_index: int, purpose: str) -> None:
        self.row_intents.setdefault(row_index, set()).add(purpose)


@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""

    branch: str
    goals: Set[str] = field(default_factory=set)
    requirements: Dict[RelationId, TableConstraint] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)
    deferred: List[exp.Expression] = field(default_factory=list)
    unsupported_reason: Optional[str] = None
    validation_expectation: Optional[str] = None

    def has_goal(self, goal: str) -> bool:
        return goal in self.goals

    def require(self, relation: RelationId) -> TableConstraint:
        """Get or create the TableConstraint for a relation."""
        if relation not in self.requirements:
            self.requirements[relation] = TableConstraint(relation=relation)
        return self.requirements[relation]

    def equate(self, col_a: ColumnId, col_b: ColumnId) -> None:
        """Declare two columns must have the same value."""
        self.equivalences.union(col_a, col_b)


# =============================================================================
# SpeculateConfig — thin wrapper around CoverageThresholds
# =============================================================================


@dataclass
class SpeculateConfig:
    """Configuration for speculative data generation.
    Derives branch generation thresholds from :class:`CoverageThresholds`.
    Each field controls how many rows to generate for that branch type.
    Set to 0 to skip that branch type entirely.
    """

    positive: int = 1
    negative: int = 1
    null: int = 1
    left_unmatched: int = 1
    right_unmatched: int = 1
    having_fail: int = 1
    case_else: int = 1
    boundary: int = 1
    join_antimatch: int = 1
    join_fanout: int = 1
    aggregate_contrast: int = 1
    rank_contrast: int = 1
    project_duplicate: int = 1

    @classmethod
    def from_thresholds(cls, thresholds: CoverageThresholds) -> "SpeculateConfig":
        """Derive speculate config from coverage thresholds."""
        return cls(
            positive=max(thresholds.atom_true, 1),
            negative=thresholds.atom_false,
            null=thresholds.atom_null,
            left_unmatched=thresholds.join_no_match,
            right_unmatched=thresholds.join_no_match,
            having_fail=thresholds.having_fail,
            case_else=thresholds.case_arm_skipped,
            boundary=1,
            join_antimatch=1,
            join_fanout=1,
            aggregate_contrast=1,
            rank_contrast=1,
            project_duplicate=1,
        )

    @classmethod
    def gold_non_empty(cls) -> SpeculateConfig:
        """Config for generating positive witness rows plus negative/null/false branches."""
        return cls(
            positive=1,
            negative=1,
            null=1,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=1,
            case_else=1,
            boundary=1,
            join_antimatch=1,
            join_fanout=1,
            aggregate_contrast=1,
            rank_contrast=1,
            project_duplicate=1,
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
            join_antimatch=1,
            join_fanout=1,
            aggregate_contrast=1,
            rank_contrast=1,
            project_duplicate=1,
        )



# =============================================================================
# Propagator: top-down constraint derivation
# =============================================================================


_COMPARISON_NODES = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)

def _relation_is_materializable(instance: Instance, relation: RelationId) -> bool:
    return bool(relation.name and relation.name.normalized in instance.tables)


def _physical_source_id(col_id: ColumnId) -> ColumnId:
    if col_id.kind is ColumnKind.PHYSICAL:
        return col_id
    current = col_id
    for _ in range(10):
        source = current.source_column_id
        if source is None:
            return current
        if (
            source.kind is ColumnKind.PHYSICAL
            and source.relation is not None
            and source.relation.name is not None
        ):
            return source
        current = source
    return current


def _is_zero_literal(expression: exp.Expression) -> bool:
    value = concrete(expression)
    return value == 0


def _contains_count_subquery(expression: exp.Expression) -> bool:
    return expression.find(exp.Subquery) is not None and expression.find(exp.Count) is not None


class Propagator:
    """Walk the Plan top-down, deriving table requirements for each branch.

    The Propagator stores constraints as ``exp.Expression`` objects
    directly.  Every column entering a constraint carries a ``SolverVar``
    annotation (via ``_solver_column`` or identity-based inline setup).
    """

    _HANDLER_MAP = {
        Scan: "_derive_scan",
        Filter: "_derive_filter",
        Join: "_derive_join",
        Aggregate: "_derive_aggregate",
        Having: "_derive_having",
        Project: "_derive_project",
        Sort: "_derive_sort",
        Limit: "_derive_limit",
        SetOperation: "_derive_set_op",
    }

    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        dialect: str,
        config: Optional[SpeculateConfig] = None,
    ):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.config = config or SpeculateConfig.gold_non_empty()
        _ = self.plan.annotations
        self._is_gold_mode = (
            self.config.negative == 0
            and self.config.null == 0
            and self.config.left_unmatched == 0
            and self.config.right_unmatched == 0
            and self.config.having_fail == 0
        )
        self._negate_step: Optional[Step] = None
        self._negate_conjunct: int = 0
        self._selected_scalar_subplan: Optional[SubPlan] = None
        self._plan_meta_cache = _build_plan_meta_cache(plan)
        self._virtual_projection_cache = self._build_virtual_projection_cache()

    def _solver_col(
        self, col_id: ColumnId, row_scope: Optional[str] = None,
    ) -> exp.Column:
        """Create a solver column from a ColumnId, using plan metadata when available."""
        meta = None
        if col_id.relation and col_id.relation.name:
            key = (col_id.relation.name.normalized, col_id.name.normalized)
            meta = self._plan_meta_cache.get(key)
        return _solver_column(self.instance, col_id, row_scope=row_scope, meta=meta)

    # -----------------------------------------------------------------
    # Dispatch infrastructure
    # -----------------------------------------------------------------

    def _walk_step(self, step: Step, spec: BranchSpec) -> None:
        """Walk the plan top-down, dispatching to step handlers."""
        # Recurse into chain dependencies first (bottom-up data flow).
        for dep in step.chain_dependencies:
            self._walk_step(dep, spec)
        handler_name = self._HANDLER_MAP.get(type(step))
        if handler_name:
            getattr(self, handler_name)(step, spec)
        for sub in step.subplan_dependencies:
            self._derive_subplan(
                sub, spec,
                parent_condition=getattr(step, "condition", None),
            )

    def _walk_plan(self, spec: BranchSpec) -> None:
        """Walk the full plan from root."""
        self._walk_step(self.plan.root, spec)

    # -----------------------------------------------------------------
    # Step handlers
    # -----------------------------------------------------------------

    def _derive_scan(self, step: Scan, spec: BranchSpec) -> None:
        """Register table requirements for a Scan step."""
        relations = self.plan.annotation_for(step).source_relations
        for relation in relations:
            if _relation_is_materializable(self.instance, relation):
                spec.require(relation)
        for sub in step.subplan_dependencies:
            if sub.inner:
                self._walk_step(sub.inner, spec)

    def _derive_filter(self, step: Filter, spec: BranchSpec) -> None:
        """Store WHERE conditions, handle negation."""
        if step.condition:
            if step is self._negate_step:
                conjuncts = self._split_conjuncts(step.condition)
                if len(conjuncts) > 1:
                    for idx, conjunct in enumerate(conjuncts):
                        if idx == self._negate_conjunct:
                            negated = negate_predicate(conjunct.copy())
                            self._store_expression(negated, spec)
                        else:
                            self._store_expression(conjunct, spec)
                else:
                    negated = negate_predicate(step.condition.copy())
                    self._store_expression(negated, spec)
            else:
                self._store_expression(step.condition, spec)
            self._extract_column_equalities(step.condition, spec)
            for atom in self._iter_scalar_subquery_atoms(step.condition):
                spec.deferred.append(atom)

    def _derive_join(self, step: Join, spec: BranchSpec) -> None:
        """Link join keys via equivalences and store join equalities."""
        residual_conditions = []
        for join_rel, join_data in (step.joins or {}).items():
            source_keys = join_data.get("source_key", [])
            join_keys = join_data.get("join_key", [])
            for sk, jk in zip(source_keys, join_keys):
                if not isinstance(sk, exp.Column) or not isinstance(jk, exp.Column):
                    self._derive_expression_join_key(sk, spec)
                    self._derive_expression_join_key(jk, spec)
                    continue
                sk_id = column_identity(sk)
                jk_id = column_identity(jk)
                sk_rel = sk_id.relation
                jk_rel = jk_id.relation
                spec.require(sk_rel)
                spec.require(jk_rel)
                spec.equate(sk_id, jk_id)
                eq_expr = exp.EQ(
                    this=_solver_column(self.instance, sk_id),
                    expression=_solver_column(self.instance, jk_id),
                )
                spec.requirements[sk_rel].constraints.append(eq_expr)
                spec.requirements[jk_rel].constraints.append(eq_expr)
                req_jk = spec.require(jk_rel)
                if jk_id not in req_jk.group_key_columns:
                    req_jk.group_key_columns.append(jk_id)
            # Forward residual ON-clause literals (e.g. S.ACTIVITY_TYPE='START')
            # to the per-alias table constraints so the solver produces rows
            # that actually satisfy the join predicate.
            condition = join_data.get("condition")
            if condition is not None and isinstance(condition, exp.Expression):
                residual_conditions.append(condition)
        for condition in residual_conditions:
            self._store_expression(condition, spec)

    def _derive_expression_join_key(
        self, expr: exp.Expression,
        spec: BranchSpec,
    ) -> None:
        """Require materializable columns referenced by a non-column join key."""
        if isinstance(expr, exp.Column):
            col_id = column_identity(expr) #_required_column_identity(expr, "Join key")
            source_id = _physical_source_id(col_id)
            if source_id.relation and self._is_materializable_relation(source_id.relation):
                req = spec.require(source_id.relation)
                col_name = source_id.name.normalized
                if not _has_is_not_null(req.constraints, col_name):
                    req.constraints.append(_make_is_not_null(self._solver_col(source_id)))
            return
        for col in expr.find_all(exp.Column):
            col_id = column_identity(col) #_required_column_identity(col, "Join key expression")
            source_id = _physical_source_id(col_id)
            if source_id.relation is None:
                continue
            if not self._is_materializable_relation(source_id.relation):
                continue
            req = spec.require(source_id.relation)
            col_name = source_id.name.normalized
            if not _has_is_not_null(req.constraints, col_name):
                req.constraints.append(_make_is_not_null(self._solver_col(source_id)))

    def _derive_aggregate(self, step: Aggregate, spec: BranchSpec) -> None:
        """Mark group key columns and add aggregate NULL constraints."""
        metadata = self.plan.annotation_for(step).metadata.get("aggregation", {})
        group_sources = metadata.get("group_sources", {})
        for sources in group_sources.values():
            for source_id in sources:
                if source_id.relation is None:
                    raise ValueError(f"Group source lacks relation: {source_id}")
                if not _relation_is_materializable(self.instance, source_id.relation):
                    continue
                req = spec.require(source_id.relation)
                spec.equivalences.find(source_id)
                if source_id not in req.group_key_columns:
                    req.group_key_columns.append(source_id)
                req.min_rows = max(req.min_rows, 3)
        # Aggregate NULL detection belongs to branches that intentionally probe
        # null sensitivity. Value witnesses need real aggregate arguments.
        if not self._is_positive_value_witness(spec):
            for agg_expr in step.aggregations:
                self._add_aggregate_null_constraints(agg_expr, spec)
        else:
            for agg_expr in step.aggregations:
                for count_node in agg_expr.find_all(exp.Count):
                    if isinstance(count_node.this, exp.Star):
                        continue
                    if count_node.args.get("distinct"):
                        continue
                    for col in count_node.find_all(exp.Column):
                        col_id = column_identity(col)
                        if col_id is None:
                            continue
                        relation = col_id.relation
                        matched = col_id.name.normalized
                        if (
                            matched
                            and relation.name
                            and relation.name.normalized
                            in self.instance.tables
                        ):
                            req = spec.require(relation)
                            if not _has_is_null(
                                req.constraints, matched
                            ) and not _has_is_not_null(
                                req.constraints, matched
                            ):
                                col_node = self._solver_col(col_id)
                                req.constraints.append(
                                    _make_is_not_null(col_node)
                                )

    def _derive_having(self, step: Having, spec: BranchSpec) -> None:
        """Store HAVING conditions and extract min group size."""
        if step.condition and step is not self._negate_step:
            if self._is_gold_mode:
                for scalar_cond in self._gold_having_scalar_constraints(
                    step.condition
                ):
                    self._store_expression(scalar_cond, spec)
            else:
                self._store_expression(step.condition, spec)
            self._apply_having_constraints(step, spec)
        elif step is self._negate_step and step.condition:
            # Negate the HAVING condition.
            negated = negate_predicate(step.condition.copy())
            self._store_expression(negated, spec)

    def _derive_project(self, step: Project, spec: BranchSpec) -> None:
        """Add IS NOT NULL for projected columns and handle DISTINCT."""
        projected_ids = self._project_source_columns(
            step, include_referenced=True,
        )
        for source_id in projected_ids:
            relation = source_id.relation
            assert relation is not None
            col_name = source_id.name.normalized
            tc = spec.require(relation)
            if not _has_is_not_null(tc.constraints, col_name):
                tc.constraints.append(_make_is_not_null(self._solver_col(source_id)))
        # Duplicate / DISTINCT handling.
        duplicate_sources = self._project_source_columns_by_relation(
            step, include_referenced=True,
        )
        for relation_id, tc in spec.requirements.items():
            dup_ids = duplicate_sources.get(relation_id, [])
            if step.distinct and dup_ids:
                tc.duplicate_columns = self._merge_column_ids(
                    tc.duplicate_columns,
                    dup_ids,
                )
                tc.min_rows = max(tc.min_rows, 2)
                for _rep, members in spec.equivalences.groups().items():
                    if len(members) < 2:
                        continue
                    member_relations = {
                        m.relation for m in members if m.relation
                    }
                    if relation_id in member_relations:
                        for other_rel in member_relations:
                            if (
                                other_rel != relation_id
                                and other_rel in spec.requirements
                            ):
                                spec.requirements[
                                    other_rel
                                ].min_rows = max(
                                    spec.requirements[other_rel].min_rows,
                                    2,
                                )

    def _derive_sort(self, step: Sort, spec: BranchSpec) -> None:
        """Record simple ORDER BY keys for ranking witnesses."""
        if not spec.has_goal("rank"):
            return
        for ordered in step.key or ():
            col_id = self._order_column_id(ordered)
            if col_id is None or col_id.relation is None:
                continue
            source_id = _physical_source_id(col_id)
            source_id = self._spec_requirement_column(source_id, spec)
            if source_id.relation is None:
                continue
            if not _relation_is_materializable(self.instance, source_id.relation):
                continue
            req = spec.require(source_id.relation)
            req.min_rows = max(req.min_rows, 2)
            req.ordered_columns = self._merge_column_ids(
                req.ordered_columns,
                [source_id],
            )
            comparison = (
                exp.GT
                if self._order_descending(ordered)
                else exp.LT
            )
            req.constraints.append(
                comparison(
                    this=self._solver_col(source_id, row_scope="r0"),
                    expression=self._solver_col(source_id, row_scope="r1"),
                )
            )

    def _derive_limit(self, step: Limit, spec: BranchSpec) -> None:
        """Set min_rows on driving table."""
        offset = getattr(step, "offset", 0) or 0
        limit_val = step.limit if step.limit != float("inf") else 1
        if self._is_gold_mode:
            needed = offset + 1 if int(limit_val) > 0 else 0
        else:
            needed = offset + int(limit_val)
        # Apply min_rows to the driving table, resolving alias to real relation.
        for relation in self.plan.annotation_for(step).source_relations:
            if relation in spec.requirements:
                spec.requirements[relation].min_rows = max(
                    spec.requirements[relation].min_rows, needed
                )

    def _derive_set_op(self, step: SetOperation, spec: BranchSpec) -> None:
        """No-op for SetOperation steps."""
        pass

    def _derive_subplan(
        self,
        sub: SubPlan,
        spec: BranchSpec,
        parent_condition: Optional[exp.Expression] = None,
    ) -> None:
        """Handle EXISTS/IN/SCALAR subplan correlation."""
        metadata = self.plan.annotation_for(sub).metadata.get("subquery")
        if metadata is None:
            raise ValueError("SubPlan lacks planner subquery metadata")

        if (
            metadata["kind"] == "scalar"
            and self._selected_scalar_subplan is not None
            and sub is not self._selected_scalar_subplan
        ):
            return

        is_anti = (
            metadata["polarity"] == "negative"
            or self._is_count_zero_subplan(sub, parent_condition)
        )
        if is_anti:
            predicate_id = metadata.get("predicate_column")
            if metadata["kind"] == "in" and predicate_id is not None:
                outer_id = _physical_source_id(predicate_id)
                if outer_id.relation is not None:
                    req = spec.require(outer_id.relation)
                    req.constraints.append(_make_is_not_null(self._solver_col(outer_id)))
                    return
            if metadata.get("polarity") == "negative":
                if metadata["cardinality"] != "zero" and sub.inner is not None:
                    self._walk_step(sub.inner, spec)
                for output_id in metadata.get("output_columns", ()):
                    source_id = _physical_source_id(output_id)
                    if source_id.relation and _relation_is_materializable(self.instance, source_id.relation):
                        spec.require(source_id.relation)
            return

        if metadata["cardinality"] != "zero" and sub.inner is not None:
            self._walk_step(sub.inner, spec)

        for output_id in metadata.get("output_columns", ()):
            source_id = _physical_source_id(output_id)
            if source_id.relation and _relation_is_materializable(self.instance, source_id.relation):
                spec.require(source_id.relation)

        predicate_id = metadata.get("predicate_column")
        output_columns = metadata.get("output_columns", ())
        if (
            metadata["kind"] == "in"
            and metadata["polarity"] == "positive"
            and predicate_id is not None
            and output_columns
        ):
            self._add_join_equality(
                _physical_source_id(predicate_id),
                _physical_source_id(output_columns[0]),
                spec,
            )

        for correlation in metadata.get("correlations", ()):
            if correlation.get("operator") != "eq":
                continue
            inner_id = _physical_source_id(correlation["inner"])
            outer_id = _physical_source_id(correlation["outer"])
            self._add_join_equality(outer_id, inner_id, spec)

    @staticmethod
    def _is_count_zero_subquery(
        condition: Optional[exp.Expression],
    ) -> bool:
        if condition is None:
            return False
        for eq_node in condition.find_all(exp.EQ):
            left, right = eq_node.this, eq_node.expression
            if _is_zero_literal(left) and _contains_count_subquery(right):
                return True
            if _is_zero_literal(right) and _contains_count_subquery(left):
                return True
        return False

    def _is_count_zero_subplan(
        self,
        sub: SubPlan,
        condition: Optional[exp.Expression],
    ) -> bool:
        if condition is None:
            return False
        for eq_node in condition.find_all(exp.EQ):
            left, right = eq_node.this, eq_node.expression
            if _is_zero_literal(left) and self._expression_contains_anchor_subquery(
                right,
                sub.anchor,
            ):
                return True
            if _is_zero_literal(right) and self._expression_contains_anchor_subquery(
                left,
                sub.anchor,
            ):
                return True
        return False

    @staticmethod
    def _expression_contains_anchor_subquery(
        expression: exp.Expression,
        anchor: exp.Expression,
    ) -> bool:
        if expression is anchor:
            return True
        anchor_sql = anchor.sql()
        for subquery in expression.find_all(exp.Subquery):
            if subquery is anchor or subquery.sql() == anchor_sql:
                return True
        return False

    # -----------------------------------------------------------------
    # Top-level propagation
    # -----------------------------------------------------------------

    def _positive_spec(self) -> Optional[BranchSpec]:
        """Build the positive branch spec."""
        if self.config.positive <= 0:
            return None
        try:
            pos = BranchSpec(branch="positive", goals={"value"})
            self._walk_plan(pos)
            self._name_deferred_positive_spec(pos)
            if self.config.boundary > 0:
                self._collect_boundary_values(pos)
            return pos
        except Exception as exc:
            logger.debug("positive spec propagation failed: %s", exc)
            return None

    def _negative_specs(self) -> List[BranchSpec]:
        """Build negative branch specs per filter conjunct."""
        specs: List[BranchSpec] = []
        if self.config.negative > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Filter) and step.condition:
                        conjuncts = self._split_conjuncts(step.condition)
                        for idx in range(len(conjuncts)):
                            neg = BranchSpec(
                                branch=f"negative_c{idx}",
                                goals={"negative"},
                            )
                            self._negate_step = step
                            self._negate_conjunct = idx
                            self._walk_plan(neg)
                            specs.append(neg)
                except Exception as exc:
                    logger.debug(
                        "negative spec propagation failed for step %s: %s",
                        type(step).__name__,
                        exc,
                    )
        self._negate_step = None
        self._negate_conjunct = 0
        return specs

    def _unmatched_join_specs(self) -> List[BranchSpec]:
        """Build left_unmatched and right_unmatched branch specs."""
        specs: List[BranchSpec] = []
        if self.config.left_unmatched <= 0 and self.config.right_unmatched <= 0:
            return specs
        for step in self.plan.ordered_steps:
            try:
                if isinstance(step, Join):
                    if self.config.left_unmatched > 0:
                        left_un = BranchSpec(
                            branch="left_unmatched",
                            goals={"join_unmatched"},
                        )
                        self._propagate_unmatched_left(step, left_un)
                        specs.append(left_un)
                    if self.config.right_unmatched > 0:
                        for join_rel in step.joins or {}:
                            join_display = join_rel.alias.normalized if join_rel.alias else (join_rel.name.normalized if join_rel.name else "?")
                            right_un = BranchSpec(
                                branch=f"right_unmatched_{join_display}",
                                goals={"join_unmatched"},
                            )
                            self._propagate_unmatched_right(
                                step, join_rel, right_un
                            )
                            specs.append(right_un)
            except Exception as exc:
                logger.debug(
                    "unmatched join propagation failed: %s", exc
                )
        return specs

    def _having_fail_specs(self) -> List[BranchSpec]:
        """Build having_fail branch specs."""
        specs: List[BranchSpec] = []
        if self.config.having_fail > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Having) and step.condition:
                        fail = BranchSpec(
                            branch="having_fail",
                            goals={"having_fail"},
                        )
                        self._negate_step = step
                        self._walk_plan(fail)
                        specs.append(fail)
                except Exception as exc:
                    logger.debug("having_fail propagation failed: %s", exc)
        self._negate_step = None
        return specs

    def _null_specs(self, pos: Optional[BranchSpec]) -> List[BranchSpec]:
        """Build null branch specs."""
        specs: List[BranchSpec] = []
        if self.config.null <= 0:
            return specs
        try:
            pos = pos or BranchSpec(branch="positive", goals={"value"})
            null_targets = self._collect_null_target_columns(pos)
            if null_targets:
                for table, cols in null_targets.items():
                    for col_name in cols:
                        null_spec = BranchSpec(
                            branch=f"null_{table}.{col_name}",
                            goals={"null"},
                        )
                        self._walk_plan(null_spec)
                        self._apply_single_null_override(
                            null_spec, table, col_name
                        )
                        specs.append(null_spec)
            else:
                null_spec = BranchSpec(
                    branch="null_branch",
                    goals={"null"},
                )
                self._walk_plan(null_spec)
                self._apply_null_overrides(null_spec)
                specs.append(null_spec)
        except Exception as exc:
            logger.debug("null branch propagation failed: %s", exc)
        return specs

    def _case_else_specs(self) -> List[BranchSpec]:
        """Build CASE WHEN ELSE branch specs."""
        specs: List[BranchSpec] = []
        if self.config.case_else <= 0:
            return specs
        try:
            for case_idx, when_conditions in enumerate(
                self._collect_case_when_condition_groups()
            ):
                case_spec = BranchSpec(
                    branch=f"case_else_{case_idx}",
                    goals={"case_else"},
                )
                self._walk_plan(case_spec)
                for cond in when_conditions:
                    negated = negate_predicate(cond.copy())
                    self._store_expression(negated, case_spec)
                specs.append(case_spec)
        except Exception as exc:
            logger.debug("CASE WHEN propagation failed: %s", exc)
        return specs

    def _semantic_case_contrast_specs(self) -> List[BranchSpec]:
        """Build non-neutral CASE witness specs from expression internals."""
        specs: List[BranchSpec] = []
        if self.config.positive <= 0:
            return specs
        try:
            for case_idx, when_conditions in enumerate(
                self._collect_case_when_condition_groups()
            ):
                if not when_conditions:
                    continue
                spec = BranchSpec(
                    branch=f"semantic_case_contrast_{case_idx}",
                    goals={"value", "case"},
                )
                self._walk_plan(spec)
                self._store_expression(when_conditions[0], spec)
                for cond in when_conditions[1:]:
                    self._store_expression(negate_predicate(cond.copy()), spec)
                specs.append(spec)
        except Exception as exc:
            logger.debug("semantic CASE contrast propagation failed: %s", exc)
        return specs

    def _semantic_project_duplicate_specs(self) -> List[BranchSpec]:
        """Build bag duplicate witnesses for non-DISTINCT projections."""
        specs: List[BranchSpec] = []
        if self.config.positive <= 0 or self.config.project_duplicate <= 0:
            return specs
        spec_index = 0
        for step in self.plan.ordered_steps:
            if not isinstance(step, Project) or step.distinct:
                continue
            duplicate_sources = self._project_source_columns_by_relation(
                step, include_referenced=False,
            )
            if not duplicate_sources:
                continue
            duplicate_columns = [
                column
                for columns in duplicate_sources.values()
                for column in columns
            ]
            if any(
                self._project_source_column_is_unique(column)
                for column in duplicate_columns
            ):
                continue
            try:
                spec = BranchSpec(
                    branch=f"semantic_project_duplicate_{spec_index}",
                    goals={"value", "duplicate"},
                )
                self._walk_plan(spec)
                for req in spec.requirements.values():
                    req.min_rows = max(req.min_rows, 2)
                for relation, columns in duplicate_sources.items():
                    if relation not in spec.requirements:
                        continue
                    req = spec.requirements[relation]
                    req.duplicate_columns = self._merge_column_ids(
                        req.duplicate_columns,
                        columns,
                    )
                    req.min_rows = max(req.min_rows, 2)
                self._bind_duplicate_scalar_avg_filters(spec)
                specs.append(spec)
                spec_index += 1
            except Exception as exc:
                logger.debug(
                    "semantic project duplicate propagation failed: %s", exc
                )
        return specs

    def _semantic_join_antimatch_specs(self) -> List[BranchSpec]:
        """Build semantic join specs with non-null unequal join keys."""
        specs: List[BranchSpec] = []
        if self.config.join_antimatch <= 0:
            return specs
        spec_index = 0
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Join):
                continue
            for _join_rel, join_data in (step.joins or {}).items():
                for left_col, right_col in self._simple_join_key_pairs(join_data):
                    try:
                        spec = BranchSpec(
                            branch=f"semantic_join_antimatch_{spec_index}",
                            goals={"value", "join_antimatch", "rank"},
                        )
                        self._walk_plan(spec)
                        if not self._add_ranked_join_antimatch_intent(
                            spec, left_col, right_col,
                        ):
                            self._remove_join_equality_constraints(
                                spec, left_col, right_col,
                            )
                            self._add_join_antimatch_constraints(
                                spec, left_col, right_col,
                            )
                        specs.append(spec)
                        spec_index += 1
                    except Exception as exc:
                        logger.debug(
                            "semantic join antimatch propagation failed: %s",
                            exc,
                        )
        return specs

    def _semantic_join_fanout_specs(self) -> List[BranchSpec]:
        """Build semantic join specs with two many-side rows per match."""
        specs: List[BranchSpec] = []
        if self.config.join_fanout <= 0:
            return specs
        spec_index = 0
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Join):
                continue
            for _join_rel, join_data in (step.joins or {}).items():
                for left_col, right_col in self._simple_join_key_pairs(join_data):
                    try:
                        many_col, one_col = self._fanout_columns(left_col, right_col)
                        if many_col is None or one_col is None:
                            continue
                        spec = BranchSpec(
                            branch=f"semantic_join_fanout_{spec_index}",
                            goals={"value", "join_fanout"},
                        )
                        self._walk_plan(spec)
                        many_req = spec.require(many_col.relation)
                        one_req = spec.require(one_col.relation)
                        many_req.min_rows = max(many_req.min_rows, 2)
                        one_req.min_rows = max(one_req.min_rows, 1)
                        many_req.duplicate_columns = self._merge_column_ids(
                            many_req.duplicate_columns,
                            [many_col],
                        )
                        specs.append(spec)
                        spec_index += 1
                    except Exception as exc:
                        logger.debug(
                            "semantic join fanout propagation failed: %s",
                            exc,
                        )
        return specs

    def _semantic_rank_contrast_specs(self) -> List[BranchSpec]:
        """Build semantic ORDER BY/LIMIT challenger specs."""
        specs: List[BranchSpec] = []
        if self.config.rank_contrast <= 0:
            return specs
        spec_index = 0
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Sort):
                continue
            if not self._sort_feeds_limit(step):
                continue
            try:
                spec = BranchSpec(
                    branch=f"semantic_rank_contrast_{spec_index}",
                    goals={"value", "rank"},
                )
                self._walk_plan(spec)
                self._expand_ordered_join_requirements(spec)
                for req in spec.requirements.values():
                    if req.ordered_columns:
                        req.min_rows = max(req.min_rows, 2)
                if spec.requirements:
                    specs.append(spec)
                    spec_index += 1
            except Exception as exc:
                logger.debug("semantic rank contrast propagation failed: %s", exc)
        return specs

    def _semantic_aggregate_contrast_specs(self) -> List[BranchSpec]:
        """Build semantic grouped aggregate contrast specs."""
        specs: List[BranchSpec] = []
        if self.config.aggregate_contrast <= 0:
            return specs
        spec_index = 0
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Aggregate):
                continue
            if not self._aggregate_feeds_rank_limit(step):
                continue
            metadata = self.plan.annotation_for(step).metadata.get("aggregation", {})
            group_sources = metadata.get("group_sources", {})
            if not group_sources:
                continue
            aggregate_outputs = metadata.get("aggregate_outputs", {})
            aggregate_argument_relations = {
                output.get("argument").relation
                for output in aggregate_outputs.values()
                if isinstance(output.get("argument"), ColumnId)
                and output.get("argument").relation is not None
            }
            try:
                spec = BranchSpec(
                    branch=f"semantic_aggregate_contrast_{spec_index}",
                    goals={"value", "aggregate"},
                )
                self._walk_plan(spec)
                materialized = False
                for sources in group_sources.values():
                    for source_id in sources:
                        source_id = self._spec_requirement_column(source_id, spec)
                        if source_id.relation is None:
                            continue
                        if not _relation_is_materializable(
                            self.instance, source_id.relation,
                        ):
                            continue
                        req = spec.require(source_id.relation)
                        min_rows = (
                            3
                            if not aggregate_argument_relations
                            or source_id.relation in aggregate_argument_relations
                            else 2
                        )
                        req.min_rows = max(req.min_rows, min_rows)
                        req.group_key_columns = self._merge_column_ids(
                            req.group_key_columns,
                            [source_id],
                        )
                        req.contrast_columns = self._merge_column_ids(
                            req.contrast_columns,
                            [source_id],
                        )
                        self._mark_aggregate_group_rows(req, min_rows)
                        materialized = True
                for output in aggregate_outputs.values():
                    argument = output.get("argument")
                    if not isinstance(argument, ColumnId) or argument.relation is None:
                        continue
                    argument = self._spec_requirement_column(argument, spec)
                    if output.get("function") != "sum":
                        continue
                    if not _relation_is_materializable(
                        self.instance, argument.relation,
                    ):
                        continue
                    req = spec.require(argument.relation)
                    req.min_rows = max(req.min_rows, 3)
                    req.contrast_columns = self._merge_column_ids(
                        req.contrast_columns,
                        [argument],
                    )
                    self._mark_sum_contrast_rows(req)
                if materialized:
                    specs.append(spec)
                    spec_index += 1
            except Exception as exc:
                logger.debug(
                    "semantic aggregate contrast propagation failed: %s",
                    exc,
                )
        return specs

    @staticmethod
    def _mark_aggregate_group_rows(req: TableConstraint, min_rows: int) -> None:
        req.mark_row(0, _ROW_INTENT_GROUP_A)
        req.mark_row(1, _ROW_INTENT_GROUP_B if min_rows == 2 else _ROW_INTENT_GROUP_A)
        if min_rows >= 3:
            req.mark_row(2, _ROW_INTENT_GROUP_B)

    @staticmethod
    def _mark_sum_contrast_rows(req: TableConstraint) -> None:
        req.mark_row(0, _ROW_INTENT_GROUP_A)
        req.mark_row(1, _ROW_INTENT_GROUP_A)
        req.mark_row(2, _ROW_INTENT_GROUP_B)

    def propagate(self) -> List[BranchSpec]:
        """Produce specs for branches based on config thresholds."""
        specs: List[BranchSpec] = []
        pos = self._positive_spec()
        if pos:
            specs.append(pos)
        specs.extend(self._semantic_case_contrast_specs())
        specs.extend(self._semantic_scalar_subquery_specs())
        specs.extend(self._semantic_project_duplicate_specs())
        specs.extend(self._semantic_join_antimatch_specs())
        specs.extend(self._semantic_join_fanout_specs())
        specs.extend(self._semantic_rank_contrast_specs())
        specs.extend(self._semantic_aggregate_contrast_specs())
        specs.extend(self._negative_specs())
        specs.extend(self._unmatched_join_specs())
        specs.extend(self._having_fail_specs())
        specs.extend(self._null_specs(pos))
        specs.extend(self._case_else_specs())
        for spec in specs:
            self._add_schema_constraints(spec)
            self._push_virtual_requirements(spec)
            # Remove requirements for virtual tables (SubPlan aliases).
            # These are not real tables — their rows come from inner plans.
            virtual_keys = [
                rel for rel, tc in spec.requirements.items()
                if identifier_name(tc.table, dialect=self.dialect).normalized not in self.instance.tables
            ]
            for key in virtual_keys:
                del spec.requirements[key]
        return specs

    def _name_deferred_positive_spec(self, spec: BranchSpec) -> None:
        if not spec.deferred:
            return
        if self._has_negative_subquery(kind="in"):
            spec.branch = "positive_semantic_not_in"
        elif self._has_negative_subquery(kind="exists"):
            spec.branch = "positive_semantic_not_exists"
        elif any(
            self._is_count_zero_subquery(getattr(step, "condition", None))
            for step in self.plan.ordered_steps
            if isinstance(step, Filter)
        ):
            spec.branch = "positive_semantic_count_zero"
        elif self._has_positive_subquery(kind="in"):
            spec.branch = "positive_semantic_in"
        else:
            spec.branch = "positive_seed_deferred"
            spec.unsupported_reason = "unsupported_lowering"

    def _has_negative_subquery(self, *, kind: str) -> bool:
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, SubPlan):
                continue
            metadata = self.plan.annotation_for(step).metadata.get("subquery", {})
            if metadata.get("kind") == kind and metadata.get("polarity") == "negative":
                return True
        return False

    def _has_positive_subquery(self, *, kind: str) -> bool:
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, SubPlan):
                continue
            metadata = self.plan.annotation_for(step).metadata.get("subquery", {})
            if metadata.get("kind") == kind and metadata.get("polarity") == "positive":
                return True
        return False

    def _semantic_scalar_subquery_specs(self) -> List[BranchSpec]:
        specs: List[BranchSpec] = []
        if self.config.positive <= 0:
            return specs
        spec_index = 0
        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or step.condition is None:
                continue
            atoms = list(self._iter_scalar_subquery_atoms(step.condition))
            if not atoms:
                continue
            subplans = [
                sub for sub in step.subplan_dependencies
                if self._is_simple_scalar_row_lookup(sub)
            ]
            for atom in atoms:
                subplan = self._scalar_subplan_for_atom(atom, subplans)
                if subplan is None:
                    continue
                try:
                    spec = BranchSpec(
                        branch=f"positive_semantic_scalar_subquery_{spec_index}",
                        goals={"value"},
                    )
                    self._selected_scalar_subplan = subplan
                    self._walk_plan(spec)
                    self._add_scalar_subquery_atom_constraint(spec, atom, subplan)
                    if spec.requirements:
                        specs.append(spec)
                        spec_index += 1
                except Exception as exc:
                    logger.debug(
                        "semantic scalar subquery propagation failed: %s", exc
                    )
                finally:
                    self._selected_scalar_subplan = None
        return specs

    def _is_simple_scalar_row_lookup(self, subplan: SubPlan) -> bool:
        metadata = self.plan.annotation_for(subplan).metadata.get("subquery", {})
        if metadata.get("kind") != "scalar" or subplan.inner is None:
            return False
        output_columns = metadata.get("output_columns", ())
        if not output_columns:
            return False
        output_id = _physical_source_id(output_columns[0])
        if output_id.relation is None:
            return False
        if not _relation_is_materializable(self.instance, output_id.relation):
            return False
        return not any(isinstance(step, Aggregate) for step in _iter_steps_with_subplans(subplan))

    def _scalar_subplan_for_atom(
        self,
        atom: exp.Expression,
        subplans: List[SubPlan],
    ) -> Optional[SubPlan]:
        for subplan in subplans:
            if self._expression_contains_anchor_subquery(atom, subplan.anchor):
                return subplan
        return None

    def _add_scalar_subquery_atom_constraint(
        self,
        spec: BranchSpec,
        atom: exp.Expression,
        subplan: SubPlan,
    ) -> None:
        metadata = self.plan.annotation_for(subplan).metadata.get("subquery", {})
        output_columns = metadata.get("output_columns", ())
        if not output_columns:
            return
        output_id = _physical_source_id(output_columns[0])
        if output_id.relation is None:
            return
        literal = self._scalar_atom_literal(atom)
        if literal is None:
            return
        req = spec.require(output_id.relation)
        req.constraints.append(
            exp.EQ(
                this=self._solver_col(output_id),
                expression=literal.copy(),
            )
        )

    @staticmethod
    def _scalar_atom_literal(atom: exp.Expression) -> Optional[exp.Expression]:
        if not isinstance(atom, exp.EQ):
            return None
        left, right = atom.this, atom.expression
        if left.find(exp.Subquery) and not right.find(exp.Subquery):
            return right
        if right.find(exp.Subquery) and not left.find(exp.Subquery):
            return left
        return None

    # -----------------------------------------------------------------
    # Expression storage
    # -----------------------------------------------------------------

    def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
        """Decompose AND, ensure SolverVar, store per-table."""
        conjuncts = self._split_conjuncts(expr)
        for conjunct in conjuncts:
            if conjunct.find(exp.Exists) or conjunct.find(exp.Subquery):
                spec.deferred.append(conjunct.copy())
                continue
            # Ensure SolverVar on all columns.
            for col in conjunct.find_all(exp.Column):
                if solver_var(col) is None:
                    col_id = column_identity(col) # _required_column_identity(col, "Stored expression")
                    set_solver_var(col, SolverVar(column_id=col_id, relation_id=col_id.relation))
            # Find the primary relation from the first column with identity.
            relation = None
            for col in conjunct.find_all(exp.Column):
                col_id = column_identity(col) # _required_column_identity(col, "Stored expression")
                if self._is_materializable_relation(col_id.relation):
                    relation = col_id.relation
                    break
            if relation:
                tc = spec.require(relation)
                tc.constraints.append(conjunct)

    def _is_materializable_relation(self, relation: RelationId) -> bool:
        if relation.name and relation.name.normalized in self.instance.tables:
            return True
        return self._virtual_projection_for_relation(relation) is not None

    def _virtual_projection_for_relation(
        self, relation: RelationId
    ) -> Optional[Dict[str, exp.Expression]]:
        candidates = [
            relation.alias.normalized if relation.alias else None,
            relation.name.normalized if relation.name else None,
        ]
        for candidate in candidates:
            if candidate and candidate in self._virtual_projection_cache:
                return self._virtual_projection_cache[candidate]
        return None

    def _build_virtual_projection_cache(self) -> Dict[str, Dict[str, exp.Expression]]:
        """Map CTE/subquery output columns to producer expressions."""
        cache: Dict[str, Dict[str, exp.Expression]] = {}
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, SubPlan) or step.inner is None:
                continue
            name = step.name
            if not name:
                continue
            sub_output_columns = self.plan.annotation_for(step).metadata.get(
                "subquery", {}
            ).get("output_columns", ())
            sub_outputs = {
                column.name.normalized: column
                for column in sub_output_columns
            }
            projections: Dict[str, exp.Expression] = {}
            for inner_step in _iter_steps_with_subplans(step.inner):
                if not isinstance(inner_step, Project):
                    continue
                projected_ids = list(getattr(inner_step, "output_column_ids", ()) or ())
                fallback_columns = self._step_referenced_column_ids(inner_step)
                for idx, projection in enumerate(inner_step.projections):
                    if isinstance(projection, exp.Alias):
                        output_name = projection.alias_or_name
                        output_id = (
                            projected_ids[idx]
                            if idx < len(projected_ids)
                            else sub_outputs.get(identifier_name(output_name).normalized)
                        )
                        projections[projection.alias_or_name] = (
                            self._copy_projection_expression(
                                projection.this,
                                output_id=output_id,
                                fallback_columns=fallback_columns,
                            )
                        )
                    elif isinstance(projection, exp.Column):
                        output_name = projection.name
                        output_id = (
                            projected_ids[idx]
                            if idx < len(projected_ids)
                            else sub_outputs.get(identifier_name(output_name).normalized)
                        )
                        projections[projection.name] = (
                            self._copy_projection_expression(
                                projection,
                                output_id=output_id,
                                fallback_columns=fallback_columns,
                            )
                        )
                if projections:
                    break
            if projections:
                cache[name] = projections
        return cache

    def _step_referenced_column_ids(self, step: Step) -> Tuple[ColumnId, ...]:
        result: List[ColumnId] = []
        for expression in _step_expressions(step):
            for column in expression.find_all(exp.Column):
                col_id = column_identity(column)
                if col_id is not None:
                    result.append(col_id)
        return tuple(result)

    def _copy_projection_expression(
        self,
        expression: exp.Expression,
        *,
        output_id: Optional[ColumnId] = None,
        fallback_columns: Tuple[ColumnId, ...] = (),
    ) -> exp.Expression:
        if isinstance(expression, exp.Column):
            expression_id = column_identity(expression)
            if expression_id is not None and expression_id.relation is not None:
                return self._solver_col(expression_id)
            if output_id is not None:
                source_id = _physical_source_id(output_id)
                if source_id.relation is not None:
                    return self._solver_col(source_id)
        copied = expression.copy()
        original_columns = list(expression.find_all(exp.Column))
        copied_columns = list(copied.find_all(exp.Column))
        for idx, (original, copied_col) in enumerate(zip(original_columns, copied_columns)):
            original_id = column_identity(original)
            if original_id is None and idx < len(fallback_columns):
                original_id = _physical_source_id(fallback_columns[idx])
            if original_id is None or original_id.relation is None:
                raise ValueError(
                    f"Virtual projection column lacks planner identity: {original.sql()}"
                )
            replacement = self._solver_col(_physical_source_id(original_id))
            copied_col.meta.update(replacement.meta)
            set_solver_var(copied_col, solver_var(replacement))
            if hasattr(replacement, "type") and replacement.type is not None:
                copied_col.type = replacement.type
        return copied

    def _push_virtual_requirements(self, spec: BranchSpec) -> None:
        """Push constraints on virtual CTE/subquery outputs into their producers."""
        pushed: Set[Tuple[str, str]] = set()
        changed = True
        while changed:
            changed = False
            for relation, tc in list(spec.requirements.items()):
                if identifier_name(tc.table, dialect=self.dialect).normalized in self.instance.tables:
                    continue
                projections = self._virtual_projections_for(relation, tc)
                if not projections:
                    continue
                relation_key = tc.alias or tc.table
                for constraint in list(tc.constraints):
                    push_key = (
                        relation_key,
                        constraint.sql(dialect=self.dialect),
                    )
                    if push_key in pushed:
                        continue
                    pushed.add(push_key)
                    rewritten = self._rewrite_virtual_constraint(
                        constraint, relation, tc, projections
                    )
                    if rewritten is not None:
                        self._store_expression(rewritten, spec)
                        changed = True

    def _virtual_projections_for(
        self, relation: RelationId, tc: TableConstraint
    ) -> Optional[Dict[str, exp.Expression]]:
        candidates = [
            tc.alias,
            tc.table,
            relation.alias.normalized if relation.alias else None,
            relation.name.normalized if relation.name else None,
        ]
        for candidate in candidates:
            if candidate and candidate in self._virtual_projection_cache:
                return self._virtual_projection_cache[candidate]
        return None

    def _rewrite_virtual_constraint(
        self,
        constraint: exp.Expression,
        relation: RelationId,
        tc: TableConstraint,
        projections: Dict[str, exp.Expression],
    ) -> Optional[exp.Expression]:
        derived_not_null = self._rewrite_derived_not_null_constraint(
            constraint, relation, tc, projections
        )
        if derived_not_null is not None:
            return derived_not_null

        rewritten = constraint.copy()
        matched = False
        virtual_names = {
            name
            for name in (
                tc.alias,
                tc.table,
                relation.alias.normalized if relation.alias else None,
                relation.name.normalized if relation.name else None,
            )
            if name
        }

        def replace(node):
            nonlocal matched
            if not isinstance(node, exp.Column):
                return node
            col_id = column_identity(node)
            col_relation = col_id.relation if col_id is not None else None
            col_names = {
                node.table,
                col_relation.alias.normalized
                if col_relation is not None and col_relation.alias
                else None,
                col_relation.name.normalized
                if col_relation is not None and col_relation.name
                else None,
            }
            if not (virtual_names & {name for name in col_names if name}):
                return node
            projection = projections.get(node.name)
            if projection is None:
                return node
            matched = True
            return projection.copy()

        rewritten = rewritten.transform(replace)
        return rewritten if matched else None

    def _rewrite_derived_not_null_constraint(
        self,
        constraint: exp.Expression,
        relation: RelationId,
        tc: TableConstraint,
        projections: Dict[str, exp.Expression],
    ) -> Optional[exp.Expression]:
        target: Optional[exp.Expression] = None
        if (
            isinstance(constraint, exp.Is)
            and isinstance(constraint.expression, exp.Not)
            and isinstance(constraint.expression.this, exp.Null)
        ):
            target = constraint.this
        elif (
            isinstance(constraint, exp.Not)
            and isinstance(constraint.this, exp.Is)
            and isinstance(constraint.this.expression, exp.Null)
        ):
            target = constraint.this.this
        if target is None:
            return None
        if not isinstance(target, exp.Column):
            return None
        virtual_names = {
            name
            for name in (
                tc.alias,
                tc.table,
                relation.alias.normalized if relation.alias else None,
                relation.name.normalized if relation.name else None,
            )
            if name
        }
        col_id = column_identity(target)
        col_relation = col_id.relation if col_id is not None else None
        col_names = {
            target.table,
            col_relation.alias.normalized
            if col_relation is not None and col_relation.alias
            else None,
            col_relation.name.normalized
            if col_relation is not None and col_relation.name
            else None,
        }
        if not (virtual_names & {name for name in col_names if name}):
            return None
        projection = projections.get(target.name)
        if projection is None or isinstance(projection, exp.Column):
            return None
        source_constraints: List[exp.Expression] = []
        seen: Set[str] = set()
        for source_col in projection.find_all(exp.Column):
            col_copy = source_col.copy()
            key = col_copy.sql(dialect=self.dialect)
            if key in seen:
                continue
            seen.add(key)
            source_constraints.append(_make_is_not_null(col_copy))
        if not source_constraints:
            return None
        return exp.and_(*source_constraints)

    def _add_join_equality(
        self, outer_id: ColumnId, inner_cid: ColumnId, spec: BranchSpec
    ) -> None:
        """Add a join equality between outer and inner columns."""
        inner_col_node = _solver_column(self.instance, inner_cid)
        outer_col_node = _solver_column(self.instance, outer_id)
        eq_expr = exp.EQ(this=outer_col_node, expression=inner_col_node)
        spec.require(outer_id.relation).constraints.append(eq_expr)
        spec.require(inner_cid.relation).constraints.append(eq_expr)
        spec.equate(outer_id, inner_cid)

    def _split_conjuncts(self, expr: exp.Expression) -> List[exp.Expression]:
        """Split a conjunction into its top-level conjuncts."""
        parts: List[exp.Expression] = []
        if isinstance(expr, exp.And):
            parts.extend(self._split_conjuncts(expr.left))
            parts.extend(self._split_conjuncts(expr.right))
        elif isinstance(expr, exp.Paren):
            parts.extend(self._split_conjuncts(expr.this))
        else:
            parts.append(expr)
        return parts

    # -----------------------------------------------------------------
    # Schema constraints
    # -----------------------------------------------------------------

    def _add_schema_constraints(self, spec: BranchSpec):
        """Add NOT NULL, UNIQUE, FK as expression constraints."""
        # Build (table, col_name) -> ColumnId mapping from plan annotations.
        # This ensures we use the planner's full identity (with scope_id, ordinal,
        # source_column_id) rather than minimal physical_column() identities.
        plan_col_ids: dict[tuple[str, str], ColumnId] = {}
        for step in self.plan.ordered_steps:
            ann = self.plan.annotations.get(id(step))
            if ann is None:
                continue
            for col_id in ann.referenced_columns + ann.projected_columns:
                if col_id.relation and col_id.relation.name:
                    key = (col_id.relation.name.normalized, col_id.name.normalized)
                    plan_col_ids[key] = col_id
                src = col_id.source_column_id
                if src and src.relation and src.relation.name and col_id.relation and col_id.relation.name:
                    plan_col_ids[(col_id.relation.name.normalized, src.name.normalized)] = col_id

        for relation_id, tc in list(spec.requirements.items()):
            table = tc.table
            if table not in self.instance.tables:
                continue

            # NOT NULL columns.
            for col_name in self.instance.tables[table]:
                plan_cid = plan_col_ids.get((table, col_name))
                # Update existing IS NOT NULL with plan identity (regardless of nullability).
                if _has_is_not_null(tc.constraints, col_name):
                    if plan_cid is not None:
                        _update_solver_var_identity(tc.constraints, col_name, plan_cid)
                    continue
                meta_key = (table, col_name)
                meta = self._plan_meta_cache.get(meta_key)
                if meta is not None and not meta["nullable"]:
                    if _has_is_null(tc.constraints, col_name):
                        continue
                    if plan_cid is not None:
                        col_node = _solver_column(
                            self.instance, plan_cid, meta=meta,
                        )
                        sv = solver_var(col_node)
                        if sv is not None:
                            plan_sv = SolverVar(column_id=plan_cid, relation_id=sv.relation_id, row_scope=sv.row_scope)
                            set_solver_var(col_node, plan_sv)
                    else:
                        col_node = self._solver_col(physical_column(col_name, relation_id))
                    tc.constraints.append(_make_is_not_null(col_node))

            # UNIQUE columns with existing data -> exclude existing values.
            existing_rows = self.instance.get_rows(relation_id)
            if existing_rows:
                for col_name in self.instance.tables[table]:
                    meta_key = (table, col_name)
                    meta = self._plan_meta_cache.get(meta_key)
                    if meta is not None and meta["unique"]:
                        existing_vals: list = []
                        for row in existing_rows:
                            try:
                                sym = row[col_name]
                                if (
                                    sym is not None
                                    and hasattr(sym, "concrete")
                                    and sym.concrete is not None
                                ):
                                    existing_vals.append(sym.concrete)
                            except (KeyError, TypeError):
                                pass
                        if existing_vals:
                            col_node = self._solver_col(
                                 physical_column(col_name, relation_id)
                            )
                            literals = [
                                (
                                    exp.Literal.number(v)
                                    if isinstance(v, (int, float))
                                    else exp.Literal.string(str(v))
                                )
                                for v in existing_vals
                            ]
                            not_in = exp.Not(
                                this=exp.In(
                                    this=col_node, expressions=literals
                                )
                            )
                            tc.constraints.append(not_in)

            # FK constraints -> parent values must be present.
            for fk_spec in self.instance.get_foreign_keys_by_relation_id(relation_id):
                if not fk_spec.target_table_id or not fk_spec.target_column_ids:
                    continue
                if not fk_spec.source_column_ids:
                    continue
                ref_relation = fk_spec.target_table_id
                ref_col_id = fk_spec.target_column_ids[0]
                src_col_id = fk_spec.source_column_ids[0]
                parent_rows = self.instance.get_rows(ref_relation)
                if parent_rows:
                    parent_vals: list = []
                    ref_col_name = ref_col_id.name.normalized
                    for row in parent_rows:
                        try:
                            sym = row[ref_col_name]
                            if (
                                sym is not None
                                and hasattr(sym, "concrete")
                                and sym.concrete is not None
                            ):
                                parent_vals.append(sym.concrete)
                        except (KeyError, TypeError):
                            pass
                    if parent_vals:
                        col_node = self._solver_col(src_col_id)
                        literals = [
                            (
                                exp.Literal.number(v)
                                if isinstance(v, (int, float))
                                else exp.Literal.string(str(v))
                            )
                            for v in parent_vals
                        ]
                        in_expr = exp.In(
                            this=col_node, expressions=literals
                        )
                        tc.constraints.append(in_expr)

    # -----------------------------------------------------------------
    # NULL branch generation
    # -----------------------------------------------------------------

    def _collect_null_target_columns(
        self, spec: BranchSpec
    ) -> Dict[str, Set[str]]:
        """Collect columns that should get NULL values in the null branch."""
        targets: Dict[str, Set[str]] = {}

        # 1. Columns with IS NOT NULL in constraints.
        for _relation_id, tc in spec.requirements.items():
            for constraint in tc.constraints:
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(
                        right.this, exp.Null
                    ):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            col_id = column_identity(col)
                            if col_id is None or col_id.relation is None:
                                continue
                            tname = col_id.relation.name.normalized if col_id.relation.name else ""
                            matched = col_id.name.normalized
                            if matched and tname:
                                targets.setdefault(tname, set()).add(
                                    matched
                                )

        # 2. Columns in SELECT projections.
        for step in self.plan.ordered_steps:
            if isinstance(step, Project):
                for proj in step.projections:
                    if isinstance(proj, exp.Expression):
                        for col in proj.find_all(exp.Column):
                            col_id = column_identity(col)
                            if col_id is None or col_id.relation is None:
                                continue
                            tname = col_id.relation.name.normalized if col_id.relation.name else ""
                            matched = col_id.name.normalized
                            if (
                                matched
                                and tname
                                and tname in self.instance.tables
                            ):
                                targets.setdefault(tname, set()).add(
                                    matched
                                )

            if isinstance(step, Filter) and step.condition is not None:
                for col in step.condition.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id is None or col_id.relation is None:
                        continue
                    tname = (
                        col_id.relation.name.normalized
                        if col_id.relation.name
                        else ""
                    )
                    matched = col_id.name.normalized
                    if (
                        matched
                        and tname
                        and tname in self.instance.tables
                    ):
                        targets.setdefault(tname, set()).add(matched)

            # 3. Columns in aggregate function arguments.
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for col in agg_expr.find_all(exp.Column):
                        col_id = column_identity(col)
                        if col_id is None or col_id.relation is None:
                            continue
                        tname = col_id.relation.name.normalized if col_id.relation.name else ""
                        matched = col_id.name.normalized
                        if (
                            matched
                            and tname
                            and tname in self.instance.tables
                        ):
                            targets.setdefault(tname, set()).add(matched)

        # 4. Exclude schema NOT NULL columns.
        for table in list(targets.keys()):
            if table not in self.instance.tables:
                continue
            # Find the RelationId from spec requirements.
            rel = None
            for r in spec.requirements:
                if r.name and r.name.normalized == table:
                    rel = r
                    break
            if rel is None:
                continue
            filtered = set()
            for col in targets[table]:
                meta = self._plan_meta_cache.get((table, col))
                if meta is not None and meta["nullable"]:
                    filtered.add(col)
            targets[table] = filtered
            if not targets[table]:
                del targets[table]

        return targets

    def _apply_single_null_override(
        self, spec: BranchSpec, target_table: str, target_col: str
    ):
        """Replace IS NOT NULL with IS NULL for a single target column."""
        for _relation_id, tc in spec.requirements.items():
            table = tc.table
            if table != target_table:
                continue

            new_constraints = []
            for constraint in tc.constraints:
                remove = False
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(
                        right.this, exp.Null
                    ):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            col_id = column_identity(col)
                            if col_id:
                                matched = col_id.name.normalized
                            else:
                                matched = col.name
                            if matched == target_col:
                                remove = True
                if not remove and isinstance(constraint, exp.Not):
                    inner = constraint.this
                    if isinstance(inner, exp.Is) and isinstance(
                        inner.expression, exp.Null
                    ):
                        if isinstance(inner.this, exp.Column):
                            col = inner.this
                            col_id = column_identity(col)
                            if col_id:
                                matched = col_id.name.normalized
                            else:
                                matched = col.name
                            if matched == target_col:
                                remove = True
                if not remove:
                    new_constraints.append(constraint)
            tc.constraints = new_constraints

            # Add IS NULL for the target column.
            col_node = self._solver_col(
                 physical_column(target_col, _relation_id)
            )
            tc.constraints.append(_make_is_null(col_node))

    def _apply_null_overrides(self, spec: BranchSpec):
        """Replace IS NOT NULL with IS NULL for all target columns."""
        targets = self._collect_null_target_columns(spec)
        if not targets:
            return

        for _relation_id, tc in spec.requirements.items():
            table = tc.table
            if table not in targets:
                continue

            new_constraints = []
            for constraint in tc.constraints:
                remove = False
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(
                        right.this, exp.Null
                    ):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            col_id = column_identity(col)
                            if col_id:
                                matched = col_id.name.normalized
                            else:
                                matched = col.name
                            if matched and matched in targets.get(
                                table, set()
                            ):
                                remove = True
                if not remove and isinstance(constraint, exp.Not):
                    inner = constraint.this
                    if isinstance(inner, exp.Is) and isinstance(
                        inner.expression, exp.Null
                    ):
                        if isinstance(inner.this, exp.Column):
                            col = inner.this
                            col_id = column_identity(col)
                            if col_id:
                                matched = col_id.name.normalized
                            else:
                                matched = col.name
                            if matched and matched in targets.get(
                                table, set()
                            ):
                                remove = True
                if not remove:
                    new_constraints.append(constraint)
            tc.constraints = new_constraints

            for col_name in targets[table]:
                col_node = self._solver_col(
                     physical_column(col_name, _relation_id)
                )
                tc.constraints.append(_make_is_null(col_node))

    # -----------------------------------------------------------------
    # Boundary value collection
    # -----------------------------------------------------------------

    def _collect_boundary_values(self, spec: BranchSpec):
        """Collect boundary values from filter comparison predicates."""
        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or not step.condition:
                continue
            conjuncts = self._split_conjuncts(step.condition)
            for conjunct in conjuncts:
                self._extract_boundary_from_conjunct(conjunct, spec)

    def _extract_boundary_from_conjunct(
        self, conjunct: exp.Expression, spec: BranchSpec
    ):
        """Extract boundary values from a single comparison conjunct."""
        if not isinstance(conjunct, _COMPARISON_NODES):
            return

        left, right = conjunct.this, conjunct.expression
        col_node, lit_node = None, None
        if isinstance(left, exp.Column) and not isinstance(
            right, exp.Column
        ):
            col_node, lit_node = left, right
        elif isinstance(right, exp.Column) and not isinstance(
            left, exp.Column
        ):
            col_node, lit_node = right, left
        if col_node is None or lit_node is None:
            return

        threshold = concrete(lit_node)
        if threshold is None:
            return

        col_id = column_identity(col_node)
        if col_id is None or col_id.relation is None:
            return
        relation = col_id.relation
        matched = col_id.name.normalized
        if not matched:
            return
        table_name = relation.name.normalized if relation.name else ""
        if table_name not in self.instance.tables:
            return

        # Determine type family from column_meta.
        meta = column_meta(col_node)
        dtype = meta["domain"] if meta is not None and "domain" in meta else None
        family = type_family(dtype) if dtype is not None else TypeFamily.UNKNOWN

        boundary_val = None
        op_type = type(conjunct)

        if family in (TypeFamily.DATE, TypeFamily.DATETIME):
            # Temporal boundary arithmetic using epoch conversion.
            if family == TypeFamily.DATE:
                parsed = parse_date(threshold) if isinstance(threshold, str) else threshold
                if parsed is None:
                    return
                epoch = date_to_epoch_day(parsed)
            else:
                parsed = parse_datetime(threshold) if isinstance(threshold, str) else threshold
                if parsed is None:
                    return
                epoch = datetime_to_epoch_second(parsed)
            if op_type is exp.GT:
                boundary_val = epoch
            elif op_type is exp.GTE:
                boundary_val = epoch - 1
            elif op_type is exp.LT:
                boundary_val = epoch
            elif op_type is exp.LTE:
                boundary_val = epoch + 1
            elif op_type is exp.EQ:
                boundary_val = epoch + 1
        elif isinstance(threshold, str):
            # String threshold with no temporal type -- no arithmetic boundary.
            return
        else:
            # Numeric arithmetic.
            if op_type is exp.GT:
                boundary_val = threshold
            elif op_type is exp.GTE:
                boundary_val = threshold - 1
            elif op_type is exp.LT:
                boundary_val = threshold
            elif op_type is exp.LTE:
                boundary_val = threshold + 1
            elif op_type is exp.EQ:
                boundary_val = threshold + 1
            elif op_type is exp.NEQ:
                boundary_val = threshold

        if boundary_val is not None:
            tc = spec.require(relation)
            tc.boundary_rows.append({col_id: boundary_val})

    # -----------------------------------------------------------------
    # Column type annotation
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # Aggregate NULL constraints
    # -----------------------------------------------------------------

    def _add_aggregate_null_constraints(
        self, agg_expr: exp.Expression, spec: BranchSpec
    ):
        """Add IS NULL for COUNT/SUM/AVG/MIN/MAX columns."""
        # COUNT columns
        for count_node in agg_expr.find_all(exp.Count):
            if isinstance(count_node.this, exp.Star):
                continue
            if count_node.args.get("distinct"):
                continue
            for col in count_node.find_all(exp.Column):
                self._add_null_constraint_for_col(col, spec)

        # SUM/AVG/MIN/MAX columns
        for agg_type in (exp.Sum, exp.Avg, exp.Min, exp.Max):
            for agg_node in agg_expr.find_all(agg_type):
                for col in agg_node.find_all(exp.Column):
                    self._add_null_constraint_for_col(col, spec)

    def _add_null_constraint_for_col(
        self, col: exp.Column, spec: BranchSpec
    ) -> None:
        """Add IS NULL constraint for a single column."""
        col_id = column_identity(col)
        if col_id is None or col_id.relation is None:
            return
        relation = col_id.relation
        matched = col_id.name.normalized
        table_name = relation.name.normalized if relation.name else ""
        if matched and table_name in self.instance.tables:
            req = spec.require(relation)
            if not _has_equality_constraint(req.constraints, matched):
                col_node = self._solver_col(col_id)
                req.constraints.append(_make_is_null(col_node))
                req.min_rows = max(req.min_rows, 2)

    # -----------------------------------------------------------------
    # Join / SubPlan handling
    # -----------------------------------------------------------------

    def _propagate_unmatched_left(
        self, join_step: Join, spec: BranchSpec
    ):
        """Generate a left-table row with no matching right-table row."""
        # Get source relation from the Join step's first chain dependency.
        source_relation = None
        for dep in join_step.chain_dependencies:
            rid = getattr(dep, 'relation_id', None)
            if rid is not None:
                source_relation = rid
                break
        if source_relation is None:
            return
        source_table = (
            source_relation.name.normalized
            if source_relation.name
            else ""
        )
        if source_table not in self.instance.tables:
            return
        req = spec.require(source_relation)
        for join_rel, join_data in (join_step.joins or {}).items():
            join_relation = join_rel
            join_table = (
                join_relation.name.normalized
                if join_relation.name
                else ""
            )
            source_keys = join_data.get("source_key", [])
            for sk in source_keys:
                sk_id = column_identity(sk) if isinstance(sk, exp.Column) else None
                if sk_id is None:
                    raise ValueError(f"Unmatched left join key lacks identity: {sk}")
                if (
                    sk_id
                    and join_table in self.instance.tables
                ):
                    existing_vals = []
                    for row in self.instance.get_rows(join_relation):
                        try:
                            sym = row[sk_id.name.normalized]
                            if (
                                sym is not None
                                and hasattr(sym, "concrete")
                                and sym.concrete is not None
                            ):
                                existing_vals.append(sym.concrete)
                        except (KeyError, TypeError):
                            pass
                    if existing_vals:
                        col_node = self._solver_col(sk_id)
                        literals = [
                            (
                                exp.Literal.number(v)
                                if isinstance(v, (int, float))
                                else exp.Literal.string(str(v))
                            )
                            for v in existing_vals
                        ]
                        not_in = exp.Not(
                            this=exp.In(
                                this=col_node, expressions=literals
                            )
                        )
                        req.constraints.append(not_in)

    def _propagate_unmatched_right(
        self, join_step: Join, join_rel: RelationId, spec: BranchSpec
    ):
        """Generate a right-table row with no matching left-table row."""
        join_relation = join_rel
        join_table = (
            join_relation.name.normalized if join_relation.name else ""
        )
        if join_table not in self.instance.tables:
            return
        req = spec.require(join_relation)
        # Get source relation from the Join step's first chain dependency.
        source_relation = None
        for dep in join_step.chain_dependencies:
            rid = getattr(dep, 'relation_id', None)
            if rid is not None:
                source_relation = rid
                break
        if source_relation is None:
            return
        join_data = (join_step.joins or {}).get(join_rel, {})
        join_keys = join_data.get("join_key", [])
        for jk in join_keys:
            jk_id = column_identity(jk) if isinstance(jk, exp.Column) else None
            if jk_id is None:
                raise ValueError(f"Unmatched right join key lacks identity: {jk}")
            if (
                jk_id
                and source_relation.name
                and source_relation.name.normalized in self.instance.tables
            ):
                existing_vals = []
                for row in self.instance.get_rows(source_relation):
                    try:
                        sym = row[jk_id.name.normalized]
                        if (
                            sym is not None
                            and hasattr(sym, "concrete")
                            and sym.concrete is not None
                        ):
                            existing_vals.append(sym.concrete)
                    except (KeyError, TypeError):
                        pass
                if existing_vals:
                    col_node = self._solver_col(jk_id)
                    literals = [
                        (
                            exp.Literal.number(v)
                            if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                        )
                        for v in existing_vals
                    ]
                    not_in = exp.Not(
                        this=exp.In(
                            this=col_node, expressions=literals
                        )
                    )
                    req.constraints.append(not_in)

    # -----------------------------------------------------------------
    # Column equality extraction (for Union-Find)
    # -----------------------------------------------------------------

    def _extract_column_equalities(
        self, condition: exp.Expression, spec: BranchSpec
    ):
        """Extract col1 = col2 patterns and link them via Union-Find."""
        for eq_node in condition.find_all(exp.EQ):
            if eq_node.find_ancestor(exp.Exists) is not None:
                continue
            if eq_node.find_ancestor(exp.Subquery) is not None:
                continue
            left, right = eq_node.this, eq_node.expression
            if isinstance(left, exp.Column) and isinstance(
                right, exp.Column
            ):
                l_id = column_identity(left)
                r_id = column_identity(right)
                if l_id is None or r_id is None:
                    continue
                if l_id.relation and r_id.relation:
                    spec.require(l_id.relation)
                    spec.require(r_id.relation)
                    spec.equate(l_id, r_id)

    # -----------------------------------------------------------------
    # HAVING helpers
    # -----------------------------------------------------------------

    def _apply_having_constraints(self, step: Having, spec: BranchSpec) -> None:
        constraints = self.plan.annotation_for(step).metadata.get(
            "having_constraints", ()
        )
        if not constraints:
            return
        for constraint in constraints:
            argument = constraint.get("argument")
            if isinstance(argument, ColumnId) and argument.relation is not None:
                target_requirements = [
                    spec.requirements[argument.relation]
                ] if argument.relation in spec.requirements else []
            else:
                target_requirements = [
                    req
                    for req in spec.requirements.values()
                    if _relation_is_materializable(self.instance, req.relation)
                ]
            required_rows = constraint.get("required_rows")
            if isinstance(required_rows, int):
                for req in target_requirements:
                    req.min_rows = max(req.min_rows, required_rows)

            if not isinstance(argument, ColumnId) or argument.relation is None:
                continue
            argument = self._spec_requirement_column(argument, spec)
            relation = argument.relation
            if relation not in spec.requirements:
                continue
            if (
                constraint.get("function") == "count"
                and constraint.get("distinct")
            ):
                req = spec.requirements[relation]
                req.distinct_columns = self._merge_column_ids(
                    req.distinct_columns,
                    [argument],
                )
                if not _has_is_not_null(req.constraints, argument.name.normalized):
                    req.constraints.append(_make_is_not_null(self._solver_col(argument)))
                continue
            if constraint.get("function") not in {"sum", "avg", "min", "max"}:
                continue
            row_count = max(spec.requirements[relation].min_rows, 1)
            if self._append_min_max_having_constraints(
                spec.requirements[relation],
                argument,
                constraint,
                row_count,
            ):
                continue
            self._append_having_value_constraint(
                spec.requirements[relation],
                argument,
                constraint,
                row_count,
            )

    def _append_min_max_having_constraints(
        self,
        req: TableConstraint,
        argument: ColumnId,
        constraint: dict,
        row_count: int,
    ) -> bool:
        function = constraint.get("function")
        operator = constraint.get("operator")
        if function not in {"min", "max"}:
            return False
        if not (
            operator == "eq"
            or (function == "min" and operator in {"gt", "gte"})
            or (function == "max" and operator in {"lt", "lte"})
        ):
            return False

        nullable = self._column_is_nullable(argument)
        if nullable:
            row_count = max(row_count, 2)
            req.min_rows = max(req.min_rows, row_count)
            req.constraints.append(
                _make_is_null(
                    self._solver_col(argument, row_scope=f"r{row_count - 1}")
                )
            )

        literal = to_literal(constraint["value"])
        bounded_rows = row_count - 1 if nullable else row_count
        for row in range(max(bounded_rows, 1)):
            row_col = self._solver_col(argument, row_scope=f"r{row}")
            if operator == "gt":
                expr = exp.GT(this=row_col, expression=literal.copy())
            elif operator == "gte":
                expr = exp.GTE(this=row_col, expression=literal.copy())
            elif operator == "lt":
                expr = exp.LT(this=row_col, expression=literal.copy())
            elif operator == "lte":
                expr = exp.LTE(this=row_col, expression=literal.copy())
            elif operator == "eq":
                expr = exp.EQ(this=row_col, expression=literal.copy())
            else:
                return False
            req.constraints.append(expr)
        return True

    def _column_is_nullable(self, column: ColumnId) -> bool:
        current: Optional[ColumnId] = column
        for _ in range(10):
            if current is None:
                break
            relation = current.relation
            if relation is not None and relation.name is not None:
                table = relation.name.normalized
                col_name = current.name.normalized
                if (
                    table in self.instance.tables
                    and col_name in self.instance.tables[table]
                ):
                    return self.instance.nullable(table, col_name)
            current = current.source_column_id
        return False

    def _append_having_value_constraint(
        self,
        req: TableConstraint,
        argument: ColumnId,
        constraint: dict,
        row_count: int,
    ) -> None:
        aggregate_expr = None
        for row in range(row_count):
            row_col = self._solver_col(argument, row_scope=f"r{row}")
            aggregate_expr = (
                row_col
                if aggregate_expr is None
                else exp.Add(this=aggregate_expr, expression=row_col)
            )
        if aggregate_expr is None:
            return
        if constraint.get("function") == "avg":
            aggregate_expr = exp.Div(
                this=aggregate_expr,
                expression=to_literal(row_count),
            )

        literal = to_literal(constraint["value"])
        operator = constraint["operator"]
        if operator == "gt":
            expr = exp.GT(this=aggregate_expr, expression=literal)
        elif operator == "gte":
            expr = exp.GTE(this=aggregate_expr, expression=literal)
        elif operator == "lt":
            expr = exp.LT(this=aggregate_expr, expression=literal)
        elif operator == "lte":
            expr = exp.LTE(this=aggregate_expr, expression=literal)
        elif operator == "eq":
            expr = exp.EQ(this=aggregate_expr, expression=literal)
        else:
            raise ValueError(f"Unsupported HAVING operator: {operator}")
        req.constraints.append(expr)

    def _gold_having_scalar_constraints(
        self, condition: exp.Expression
    ) -> List[exp.Expression]:
        """Return non-aggregate HAVING predicates for gold witnesses."""
        source = condition.copy()
        if self._is_synthetic_having_alias(condition):
            source = self._find_having_alias_expression(condition)
            if source is None:
                return []
        scalar_conditions: List[exp.Expression] = []
        for conjunct in self._split_conjuncts(source):
            if conjunct.find(
                (exp.Avg, exp.Sum, exp.Count, exp.Min, exp.Max)
            ):
                continue
            scalar_conditions.append(conjunct)
        return scalar_conditions

    @staticmethod
    def _is_synthetic_having_alias(condition: exp.Expression) -> bool:
        """Return True for planner-generated HAVING aggregate alias columns."""
        if not isinstance(condition, exp.Column):
            return False
        return condition.name.startswith("_h")

    def _find_having_alias_expression(
        self, condition: exp.Column
    ) -> Optional[exp.Expression]:
        """Find the expression behind a planner-generated HAVING alias."""
        alias = condition.name
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            for agg_expr in step.aggregations:
                if isinstance(agg_expr, exp.Alias) and agg_expr.alias_or_name == alias:
                    return agg_expr.this.copy()
        return None

    # -----------------------------------------------------------------
    # CASE WHEN arm coverage
    # -----------------------------------------------------------------

    def _collect_case_when_condition_groups(self) -> List[List[exp.Expression]]:
        """Collect CASE WHEN predicates grouped by output expression."""
        groups: List[List[exp.Expression]] = []
        seen: Set[Tuple[str, ...]] = set()
        for expr in self._semantic_expression_sources():
            conditions = self._case_conditions_for_expression(expr)
            if not conditions:
                continue
            key = tuple(cond.sql(dialect=self.dialect) for cond in conditions)
            if key in seen:
                continue
            seen.add(key)
            groups.append(conditions)
        return groups

    def _semantic_expression_sources(self) -> List[exp.Expression]:
        """Return plan expressions whose internals affect generated values."""
        expressions: List[exp.Expression] = []
        for step in self.plan.ordered_steps:
            condition = getattr(step, "condition", None)
            if isinstance(condition, exp.Expression):
                expressions.append(condition)
            for projection in getattr(step, "projections", None) or ():
                if isinstance(projection, exp.Expression):
                    expressions.append(projection)
            if isinstance(step, Aggregate):
                for aggregation in getattr(step, "aggregations", None) or ():
                    if isinstance(aggregation, exp.Expression):
                        expressions.append(
                            self._expand_aggregate_operands(step, aggregation)
                        )
                for operand in getattr(step, "operands", None) or ():
                    if isinstance(operand, exp.Expression):
                        expressions.append(
                            operand.this if isinstance(operand, exp.Alias) else operand
                        )
        return expressions

    def _expand_aggregate_operands(
        self,
        step: Aggregate,
        expression: exp.Expression,
    ) -> exp.Expression:
        operands = {
            operand.alias_or_name: (
                operand.this if isinstance(operand, exp.Alias) else operand
            )
            for operand in (getattr(step, "operands", None) or ())
            if isinstance(operand, exp.Expression) and operand.alias_or_name
        }

        def expand(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Column) and not node.table:
                operand = operands.get(node.name)
                if operand is not None:
                    return operand.copy()
            return node

        return expression.copy().transform(expand)

    def _case_conditions_for_expression(
        self,
        expression: exp.Expression,
    ) -> List[exp.Expression]:
        conditions: List[exp.Expression] = []
        for case_expr in expression.find_all(exp.Case):
            case_operand = case_expr.this
            for if_node in case_expr.args.get("ifs") or ():
                cond = if_node.this
                if cond is None:
                    continue
                if case_operand is not None:
                    cond = exp.EQ(
                        this=case_operand.copy(),
                        expression=cond.copy(),
                    )
                conditions.append(cond)
        return conditions

    def _is_positive_value_witness(self, spec: BranchSpec) -> bool:
        return spec.has_goal("value")

    def _bind_duplicate_scalar_avg_filters(self, spec: BranchSpec) -> None:
        if not spec.has_goal("duplicate"):
            return
        for atom in spec.deferred:
            binding = self._duplicate_avg_filter_binding(atom)
            if binding is None:
                continue
            outer_col, avg_col, positive = binding
            outer_relation = outer_col.relation
            avg_relation = avg_col.relation
            if outer_relation is None or outer_relation not in spec.requirements:
                continue
            self._bind_duplicate_avg_column(spec, outer_col, positive)
            if avg_relation is not None and avg_relation in spec.requirements:
                self._bind_duplicate_avg_column(spec, avg_col, positive)
                eq_expr = exp.EQ(
                    this=self._solver_col(outer_col),
                    expression=self._solver_col(avg_col),
                )
                spec.requirements[outer_relation].constraints.append(eq_expr)
                spec.requirements[avg_relation].constraints.append(eq_expr.copy())

    def _bind_duplicate_avg_column(
        self,
        spec: BranchSpec,
        col_id: ColumnId,
        positive: bool,
    ) -> None:
        relation = col_id.relation
        if relation is None or relation not in spec.requirements:
            return
        req = spec.requirements[relation]
        req.duplicate_columns = self._merge_column_ids(
            req.duplicate_columns,
            [col_id],
        )
        req.min_rows = max(req.min_rows, 2)
        col_node = self._solver_col(col_id)
        zero = exp.Literal.number(0)
        constraint = (
            exp.GT(this=col_node, expression=zero)
            if positive
            else exp.LT(this=col_node, expression=zero)
        )
        req.constraints.append(constraint)

    def _duplicate_avg_filter_binding(
        self,
        atom: exp.Expression,
    ) -> Optional[Tuple[ColumnId, ColumnId, bool]]:
        if not isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return None
        left_outer = self._scaled_outer_column(atom.this)
        right_avg = self._scaled_avg_subquery_column(atom.expression)
        if left_outer is not None and right_avg is not None:
            return self._avg_filter_sign(atom, left_outer, right_avg)

        right_outer = self._scaled_outer_column(atom.expression)
        left_avg = self._scaled_avg_subquery_column(atom.this)
        if right_outer is not None and left_avg is not None:
            flipped = atom.copy()
            flipped.set("this", atom.expression.copy())
            flipped.set("expression", atom.this.copy())
            flipped = self._flip_comparison(flipped)
            return self._avg_filter_sign(flipped, right_outer, left_avg)
        return None

    def _avg_filter_sign(
        self,
        atom: exp.Expression,
        outer: Tuple[ColumnId, float],
        avg: Tuple[ColumnId, float],
    ) -> Optional[Tuple[ColumnId, ColumnId, bool]]:
        outer_col, outer_scale = outer
        avg_col, avg_scale = avg
        if not self._same_physical_column(outer_col, avg_col):
            return None
        if outer_scale == avg_scale:
            return None
        if isinstance(atom, (exp.GT, exp.GTE)):
            return outer_col, avg_col, outer_scale > avg_scale
        if isinstance(atom, (exp.LT, exp.LTE)):
            return outer_col, avg_col, outer_scale < avg_scale
        return None

    def _scaled_outer_column(
        self,
        expression: exp.Expression,
    ) -> Optional[Tuple[ColumnId, float]]:
        if expression.find(exp.Subquery):
            return None
        return self._scaled_column(expression)

    def _scaled_avg_subquery_column(
        self,
        expression: exp.Expression,
    ) -> Optional[Tuple[ColumnId, float]]:
        if not expression.find(exp.Subquery):
            return None
        if isinstance(expression, exp.Mul):
            left = self._scaled_avg_subquery_column(expression.this)
            right_literal = self._numeric_literal(expression.expression)
            if left is not None and right_literal is not None:
                col_id, scale = left
                return col_id, scale * right_literal
            right = self._scaled_avg_subquery_column(expression.expression)
            left_literal = self._numeric_literal(expression.this)
            if right is not None and left_literal is not None:
                col_id, scale = right
                return col_id, scale * left_literal
            return None
        subquery = (
            expression
            if isinstance(expression, exp.Subquery)
            else expression.find(exp.Subquery)
        )
        if subquery is None:
            return None
        avg_node = next(subquery.find_all(exp.Avg), None)
        if avg_node is None:
            return None
        avg_col = next(avg_node.find_all(exp.Column), None)
        if avg_col is None:
            return None
        col_id = column_identity(avg_col)
        if col_id is None:
            return None
        source_id = _physical_source_id(col_id)
        if source_id.relation is None:
            return None
        return source_id, 1.0

    def _scaled_column(
        self,
        expression: exp.Expression,
    ) -> Optional[Tuple[ColumnId, float]]:
        if isinstance(expression, exp.Column):
            col_id = column_identity(expression)
            if col_id is None:
                return None
            source_id = _physical_source_id(col_id)
            if source_id.relation is None:
                return None
            return source_id, 1.0
        if not isinstance(expression, exp.Mul):
            return None
        left = self._scaled_column(expression.this)
        right_literal = self._numeric_literal(expression.expression)
        if left is not None and right_literal is not None:
            col_id, scale = left
            return col_id, scale * right_literal
        right = self._scaled_column(expression.expression)
        left_literal = self._numeric_literal(expression.this)
        if right is not None and left_literal is not None:
            col_id, scale = right
            return col_id, scale * left_literal
        return None

    @staticmethod
    def _numeric_literal(expression: exp.Expression) -> Optional[float]:
        if not isinstance(expression, exp.Literal) or expression.is_string:
            return None
        try:
            return float(expression.this)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _same_physical_column(left: ColumnId, right: ColumnId) -> bool:
        if left.relation is None or right.relation is None:
            return False
        if left.name.normalized != right.name.normalized:
            return False
        if left.relation.name is None or right.relation.name is None:
            return False
        return left.relation.name.normalized == right.relation.name.normalized

    @staticmethod
    def _flip_comparison(atom: exp.Expression) -> exp.Expression:
        if isinstance(atom, exp.GT):
            return exp.LT(this=atom.this, expression=atom.expression)
        if isinstance(atom, exp.GTE):
            return exp.LTE(this=atom.this, expression=atom.expression)
        if isinstance(atom, exp.LT):
            return exp.GT(this=atom.this, expression=atom.expression)
        if isinstance(atom, exp.LTE):
            return exp.GTE(this=atom.this, expression=atom.expression)
        return atom

    def _project_source_columns(
        self,
        step: Project,
        *,
        include_referenced: bool,
    ) -> List[ColumnId]:
        ann = self.plan.annotation_for(step)
        candidates = list(ann.projected_columns)
        if include_referenced:
            candidates.extend(ann.referenced_columns)
        sources: List[ColumnId] = []
        for col_id in candidates:
            source_id = self._project_source_column(col_id)
            if source_id is None:
                continue
            sources = self._merge_column_ids(sources, [source_id])
        return sources

    def _project_source_columns_by_relation(
        self,
        step: Project,
        *,
        include_referenced: bool,
    ) -> Dict[RelationId, List[ColumnId]]:
        columns_by_relation: Dict[RelationId, List[ColumnId]] = {}
        for source_id in self._project_source_columns(
            step, include_referenced=include_referenced,
        ):
            relation = source_id.relation
            if relation is None:
                continue
            columns_by_relation[relation] = self._merge_column_ids(
                columns_by_relation.get(relation, []),
                [source_id],
            )
        return columns_by_relation

    def _project_source_column(self, col_id: ColumnId) -> Optional[ColumnId]:
        if col_id.kind is ColumnKind.AGGREGATE:
            return None
        if (
            col_id.relation is not None
            and not _relation_is_materializable(self.instance, col_id.relation)
            and self._virtual_projection_for_relation(col_id.relation) is not None
        ):
            source_id = col_id
        else:
            source_id = _physical_source_id(col_id)
        if source_id.kind is ColumnKind.AGGREGATE:
            return None
        relation = source_id.relation
        if relation is None or not self._is_materializable_relation(relation):
            return None
        if source_id.name.normalized.startswith("_"):
            return None
        return source_id

    def _project_source_column_is_unique(self, col_id: ColumnId) -> bool:
        relation = col_id.relation
        if relation is None or relation.name is None:
            return False
        if relation.name.normalized not in self.instance.tables:
            return False
        return self.instance.is_unique(
            relation.name.normalized,
            col_id.name.normalized,
        )

    @staticmethod
    def _merge_column_ids(
        existing: List[ColumnId],
        additions: List[ColumnId],
    ) -> List[ColumnId]:
        merged = list(existing)
        seen = {
            (
                col.relation,
                col.name.normalized if col.name else "",
                col.scope_id,
            )
            for col in merged
        }
        for col in additions:
            key = (
                col.relation,
                col.name.normalized if col.name else "",
                col.scope_id,
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(col)
        return merged

    # -----------------------------------------------------------------
    # Scalar subquery detection
    # -----------------------------------------------------------------

    def _iter_scalar_subquery_atoms(
        self, predicate: exp.Expression
    ):
        """Yield atoms that contain a scalar subquery comparison."""
        if isinstance(predicate, exp.And):
            yield from self._iter_scalar_subquery_atoms(predicate.left)
            yield from self._iter_scalar_subquery_atoms(predicate.right)
        elif isinstance(predicate, exp.Paren):
            yield from self._iter_scalar_subquery_atoms(predicate.this)
        elif isinstance(predicate, exp.Or):
            yield from self._iter_scalar_subquery_atoms(predicate.left)
            yield from self._iter_scalar_subquery_atoms(predicate.right)
        else:
            if predicate.find(exp.Subquery) and isinstance(
                predicate, _COMPARISON_NODES
            ):
                yield predicate

    # -----------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------

    def _projected_columns(self, step: Project) -> List[Tuple[str, str]]:
        """Get projected columns as (col_name, table_alias) pairs."""
        cols: List[Tuple[str, str]] = []
        for proj in step.projections:
            if isinstance(proj, exp.Expression):
                for col in proj.find_all(exp.Column):
                    cols.append((col.name, col.table or ""))
        return cols

    def _simple_join_key_pairs(
        self,
        join_data: dict,
    ) -> List[Tuple[ColumnId, ColumnId]]:
        pairs: List[Tuple[ColumnId, ColumnId]] = []
        source_keys = join_data.get("source_key", [])
        join_keys = join_data.get("join_key", [])
        for source_key, join_key in zip(source_keys, join_keys):
            if not isinstance(source_key, exp.Column):
                continue
            if not isinstance(join_key, exp.Column):
                continue
            source_id = column_identity(source_key)
            join_id = column_identity(join_key)
            if source_id is None or join_id is None:
                continue
            source_id = _physical_source_id(source_id)
            join_id = _physical_source_id(join_id)
            if source_id.relation is None or join_id.relation is None:
                continue
            if not _relation_is_materializable(self.instance, source_id.relation):
                continue
            if not _relation_is_materializable(self.instance, join_id.relation):
                continue
            pairs.append((source_id, join_id))
        return pairs

    def _remove_join_equality_constraints(
        self,
        spec: BranchSpec,
        left_col: ColumnId,
        right_col: ColumnId,
    ) -> None:
        pair = {
            (left_col.relation, left_col.name.normalized),
            (right_col.relation, right_col.name.normalized),
        }
        for req in spec.requirements.values():
            retained: List[exp.Expression] = []
            for constraint in req.constraints:
                if not isinstance(constraint, exp.EQ):
                    retained.append(constraint)
                    continue
                columns = list(constraint.find_all(exp.Column))
                if len(columns) != 2:
                    retained.append(constraint)
                    continue
                found = set()
                for col in columns:
                    col_id = column_identity(col)
                    if col_id is None:
                        retained.append(constraint)
                        break
                    source_id = _physical_source_id(col_id)
                    found.add((source_id.relation, source_id.name.normalized))
                else:
                    if found == pair:
                        continue
                    retained.append(constraint)
            req.constraints = retained

    def _add_join_antimatch_constraints(
        self,
        spec: BranchSpec,
        left_col: ColumnId,
        right_col: ColumnId,
    ) -> None:
        left_req = spec.require(left_col.relation)
        right_req = spec.require(right_col.relation)
        left_node = self._solver_col(left_col)
        right_node = self._solver_col(right_col)
        neq = exp.NEQ(this=left_node, expression=right_node)
        for req, node in ((left_req, left_node), (right_req, right_node)):
            if not _has_is_not_null(req.constraints, node.name):
                req.constraints.append(_make_is_not_null(node.copy()))
            req.constraints.append(neq.copy())

    def _add_ranked_join_antimatch_intent(
        self,
        spec: BranchSpec,
        left_col: ColumnId,
        right_col: ColumnId,
    ) -> bool:
        ordering_col = self._join_rank_order_column(left_col, right_col)
        if ordering_col is None or ordering_col.relation is None:
            return False
        if self._same_relation(ordering_col.relation, left_col.relation):
            ordering_join_col = left_col
            joined_col = right_col
        elif self._same_relation(ordering_col.relation, right_col.relation):
            ordering_join_col = right_col
            joined_col = left_col
        else:
            return False

        ordering_req = spec.require(ordering_join_col.relation)
        joined_req = spec.require(joined_col.relation)
        ordering_req.min_rows = max(ordering_req.min_rows, 2)
        joined_req.min_rows = max(joined_req.min_rows, 1)
        ordering_req.mark_row(0, _ROW_INTENT_UNMATCHED_RANK_TOP)
        ordering_req.mark_row(1, _ROW_INTENT_MATCHED_RANK_LOWER)
        joined_req.mark_row(0, _ROW_INTENT_MATCHED_RANK_LOWER)

        top_join_key = self._solver_col(ordering_join_col, row_scope="r0")
        matched_join_key = self._solver_col(joined_col, row_scope="r0")
        ordering_req.constraints.append(
            exp.NEQ(this=top_join_key, expression=matched_join_key)
        )
        ordering_req.constraints.append(_make_is_not_null(top_join_key.copy()))
        joined_req.constraints.append(_make_is_not_null(matched_join_key.copy()))
        return True

    def _fanout_columns(
        self,
        left_col: ColumnId,
        right_col: ColumnId,
    ) -> Tuple[Optional[ColumnId], Optional[ColumnId]]:
        left_unique = self._column_is_unique(left_col)
        right_unique = self._column_is_unique(right_col)
        if right_unique and not left_unique:
            return left_col, right_col
        if left_unique and not right_unique:
            return right_col, left_col
        if not left_unique:
            return left_col, right_col
        if not right_unique:
            return right_col, left_col
        return None, None

    def _column_is_unique(self, col_id: ColumnId) -> bool:
        relation = col_id.relation
        if relation is None or relation.name is None:
            return False
        table = relation.name.normalized
        if table not in self.instance.tables:
            return False
        return self.instance.is_unique(table, col_id.name.normalized)

    def _spec_requirement_column(
        self,
        col_id: ColumnId,
        spec: BranchSpec,
    ) -> ColumnId:
        relation = col_id.relation
        if relation in spec.requirements:
            return col_id
        table = relation.name.normalized if relation and relation.name else ""
        if not table:
            return col_id
        for req_relation in spec.requirements:
            req_table = (
                req_relation.name.normalized
                if req_relation.name
                else ""
            )
            if req_table != table:
                continue
            return column_id(
                ColumnKind.PHYSICAL,
                col_id.name,
                req_relation,
                scope_id=req_relation.scope_id,
                source_column_id=col_id.source_column_id or col_id,
            )
        return col_id

    def _expand_ordered_join_requirements(self, spec: BranchSpec) -> None:
        ordered_relations = {
            relation
            for relation, req in spec.requirements.items()
            if req.ordered_columns and req.min_rows >= 2
        }
        if not ordered_relations:
            return
        for _rep, members in spec.equivalences.groups().items():
            member_relations = {member.relation for member in members if member.relation}
            if not (ordered_relations & member_relations):
                continue
            for relation in member_relations:
                if relation in spec.requirements:
                    spec.requirements[relation].min_rows = max(
                        spec.requirements[relation].min_rows,
                        2,
                    )

    def _sort_feeds_limit(self, sort_step: Sort) -> bool:
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Limit):
                continue
            if self._step_depends_on(step, sort_step):
                return True
        return False

    def _aggregate_feeds_rank_limit(self, aggregate_step: Aggregate) -> bool:
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Sort):
                continue
            if not self._sort_feeds_limit(step):
                continue
            if self._step_depends_on(step, aggregate_step):
                return True
        return False

    def _step_depends_on(self, step: Step, target: Step) -> bool:
        if step is target:
            return True
        for dep in step.chain_dependencies:
            if self._step_depends_on(dep, target):
                return True
        return False

    def _order_column_id(self, ordered: exp.Expression) -> Optional[ColumnId]:
        expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if not isinstance(expression, exp.Column):
            return None
        col_id = column_identity(expression)
        if col_id is None:
            return None
        return _physical_source_id(col_id)

    def _join_rank_order_column(
        self,
        left_col: ColumnId,
        right_col: ColumnId,
    ) -> Optional[ColumnId]:
        for step in _iter_all_plan_steps(self.plan):
            if not isinstance(step, Sort) or not self._sort_feeds_limit(step):
                continue
            for ordered in step.key or ():
                ordered_col = self._order_column_id(ordered)
                if ordered_col is None or ordered_col.relation is None:
                    continue
                if self._same_relation(ordered_col.relation, left_col.relation):
                    return ordered_col
                if self._same_relation(ordered_col.relation, right_col.relation):
                    return ordered_col
        return None

    @staticmethod
    def _same_relation(
        left: Optional[RelationId],
        right: Optional[RelationId],
    ) -> bool:
        if left is None or right is None:
            return False
        return (
            left.name == right.name
            and left.alias == right.alias
            and left.scope_id == right.scope_id
        )

    @staticmethod
    def _order_descending(ordered: exp.Expression) -> bool:
        return isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))


# =============================================================================
# Resolver: solver integration and row materialization
# =============================================================================

def _row_value_dict(row) -> Dict[str, Any]:
    """Convert a Row to a plain dict keyed by column name string."""
    values: Dict[str, Any] = {}
    for column, value in row.items():
        key = column.name.normalized if isinstance(column, ColumnId) else str(column)
        concrete_value = value.concrete if hasattr(value, "concrete") else value
        if isinstance(concrete_value, Decimal):
            concrete_value = float(concrete_value)
        if isinstance(concrete_value, (date, datetime, time)):
            concrete_value = concrete_value.isoformat()
        values[key] = concrete_value
    return values


# ---------------------------------------------------------------------------
# RowBinding construction
# ---------------------------------------------------------------------------


def _build_gold_row_bindings(spec: BranchSpec) -> Dict[str, RowBinding]:
    """Build RowBinding objects for every table x row in the spec.

    Each requirement (including different aliases of the same physical table)
    gets its own bindings. This supports self-joins where the same table
    appears with different aliases and different constraints.
    """
    bindings: Dict[str, RowBinding] = {}
    for _relation, req in spec.requirements.items():
        binding_relation = req.relation
        for row_index in range(max(req.min_rows, 1)):
            binding = RowBinding(relation=binding_relation, row=row_index)
            bindings[_solver_table_key(binding)] = binding
    return bindings


def _bindings_for_requirement(
    _relation: RelationId,
    req: TableConstraint,
    row_bindings: Dict[str, RowBinding],
) -> List[RowBinding]:
    """Find bindings matching a requirement."""
    target_table = req.table
    target_alias = req.alias
    target_scope = req.relation.scope_id
    return [
        binding
        for binding in row_bindings.values()
        if binding.table == target_table
        and binding.alias == target_alias
        and binding.relation.scope_id == target_scope
    ]


def _find_binding_for_column(
    table_name: str,
    row_bindings: Dict[str, RowBinding],
) -> Optional[RowBinding]:
    """Find the first RowBinding for a physical table name."""
    normalized = table_name
    for binding in row_bindings.values():
        if binding.table == normalized:
            return binding
    return None


def _row_binding_sort_key(binding: RowBinding) -> Tuple[str, str, str, int]:
    return (
        binding.table,
        binding.alias or "",
        binding.relation.scope_id or "",
        binding.row,
    )


def _scoped_var_key(binding: RowBinding, column_name: str) -> Tuple[str, str, str, int, str]:
    table, alias, scope, row = _row_binding_sort_key(binding)
    return (table, alias, scope, row, column_name)


PathVariableIndex = Dict[Tuple[str, str, str, int, str], SolverVar]


def _requirement_for_binding(
    spec: BranchSpec,
    binding: RowBinding,
) -> Optional[TableConstraint]:
    """Find the TableConstraint for a binding."""
    for _relation, req in spec.requirements.items():
        if (
            req.table == binding.table
            and req.alias == binding.alias
            and req.relation.scope_id == binding.relation.scope_id
        ):
            return req
    return None


def _row_bound_column_id(column: ColumnId, binding: RowBinding) -> ColumnId:
    if column.kind not in {ColumnKind.PHYSICAL, ColumnKind.PROJECTED}:
        return column
    source = column.source_column_id or column
    if source.kind is not ColumnKind.PHYSICAL:
        return column
    base_source = source.source_column_id or source
    return column_id(
        ColumnKind.PHYSICAL,
        source.name,
        binding.relation,
        scope_id=binding.relation.scope_id,
        source_column_id=base_source,
    )


def _row_bound_solver_var(var: SolverVar, binding: RowBinding) -> SolverVar:
    column = _row_bound_column_id(var.column_id, binding)
    relation = column.relation or binding.relation
    return SolverVar(
        column_id=column,
        relation_id=relation,
        row_scope=f"r{binding.row}",
    )


def _solver_var_matches_binding(var: SolverVar, binding: RowBinding) -> bool:
    relation = var.relation_id
    table = relation.name.normalized if relation.name else ""
    alias = relation.alias.normalized if relation.alias else None
    scope = relation.scope_id
    return (
        table == binding.table
        and alias == binding.alias
        and scope == binding.relation.scope_id
    )


def _binding_for_solver_var_row(
    var: SolverVar,
    preferred_row: int,
    row_bindings: Optional[Dict[str, RowBinding]],
) -> Optional[RowBinding]:
    if row_bindings is None:
        return None
    matches = _bindings_for_solver_var(var, row_bindings)
    if not matches:
        return None
    for candidate in matches:
        if candidate.row == preferred_row:
            return candidate
    return matches[0]


# ---------------------------------------------------------------------------
# Constraint rewriting for row scoping
# ---------------------------------------------------------------------------


def _rewrite_constraint_for_binding(
    constraint: exp.Expression,
    binding: RowBinding,
    instance: Instance,
    row_bindings: Optional[Dict[str, RowBinding]] = None,
    scoped_vars: Optional[Dict[Tuple[str, str, str, int, str], SolverVar]] = None,
) -> Optional[exp.Expression]:
    """Copy constraint, replace Column nodes with _solver_column scoped to the binding.

    Returns None if no columns match the binding's table.

    Args:
        scoped_vars: Optional mapping of (table, col_name) -> scoped SolverVar.
            When provided, unscoped columns (e.g. IS NOT NULL from
            _add_schema_constraints) reuse the scoped variable instead of
            creating a new unscoped one.  The dict is updated in-place with
            newly created scoped variables so later calls can reuse them.
    """
    rewritten = constraint.copy()
    matched = False
    for col in rewritten.find_all(exp.Column):
        if col.name not in instance.tables.get(binding.table, {}):
            continue
        sv = solver_var(col)
        if sv is not None:
            if not _solver_var_matches_binding(sv, binding):
                target_binding = _binding_for_solver_var_row(
                    sv,
                    binding.row,
                    row_bindings,
                )
                if target_binding is None:
                    target_binding = binding
                scoped = _row_bound_solver_var(sv, target_binding)
                set_solver_var(col, scoped)
                ct = col_type(col)
                if ct is None:
                    meta = column_meta(col)
                    if meta is not None and "domain" in meta:
                        col.type = meta["domain"]
                continue
            new_var = _row_bound_solver_var(sv, binding)
            set_solver_var(col, new_var)
            ct = col_type(col)
            if ct is None:
                meta = column_meta(col)
                if meta is not None and "domain" in meta:
                    col.type = meta["domain"]
            if scoped_vars is not None:
                scoped_vars[_scoped_var_key(binding, col.name)] = new_var
            matched = True
        else:
            col_table = col.table or ""
            if col_table and col_table != binding.table:
                if binding.alias and col_table != binding.alias:
                    continue
                elif not binding.alias:
                    continue
            # Reuse scoped variable if available.
            key = _scoped_var_key(binding, col.name)
            existing = scoped_vars.get(key) if scoped_vars is not None else None
            if existing is not None:
                set_solver_var(col, existing)
                ct = col_type(col)
                if ct is None:
                    meta = column_meta(col)
                    if meta is not None and "domain" in meta:
                        col.type = meta["domain"]
            else:
                column = _row_bound_column_id(
                    physical_column(col.name, binding.relation),
                    binding,
                )
                new_col = _solver_column(
                    instance,
                    column,
                    row_scope=f"r{binding.row}",
                )
                set_solver_var(col, solver_var(new_col))
                if hasattr(new_col, "type") and new_col.type is not None:
                    col.type = new_col.type
            matched = True
    return rewritten if matched else None


def _database_check_constraints_for_binding(
    instance: Instance,
    binding: RowBinding,
    path_variables: PathVariableIndex,
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for check in instance.database_constraints(binding.relation).checks:
        if not check.supported:
            raise ValueError(
                f"unsupported_database_check:{check.reason or 'unknown'}"
            )
        column_by_name = {
            column.name.normalized: column
            for column in check.referenced_columns
        }
        variable_by_name = {
            column.name.normalized: _path_variable_for_binding_column(
                path_variables,
                binding,
                column,
            )
            for column in check.referenced_columns
        }
        if any(variable is None for variable in variable_by_name.values()):
            continue
        rewritten = check.expression.copy()
        matched = False
        for col in rewritten.find_all(exp.Column):
            column = column_by_name.get(
                identifier_name(col.name, dialect=instance.dialect).normalized
            )
            if column is None:
                continue
            variable = variable_by_name[column.name.normalized]
            assert variable is not None
            set_solver_var(col, variable)
            dtype = _dtype_for_solver_var(variable, instance)
            if dtype is not None:
                col.type = dtype
            matched = True
        if matched:
            constraints.append(rewritten)
    return constraints


def _database_not_null_constraints_for_binding(
    instance: Instance,
    binding: RowBinding,
    path_variables: PathVariableIndex,
) -> List[exp.Expression]:
    constraints = instance.database_constraints(binding.relation)
    required_columns = dict.fromkeys(
        tuple(constraints.not_null_columns) + tuple(constraints.primary_key)
    )
    expressions: List[exp.Expression] = []
    for column in required_columns:
        variable = _path_variable_for_binding_column(
            path_variables,
            binding,
            column,
        )
        if variable is None:
            continue
        col_node = _solver_column(
            instance,
            variable.column_id,
            row_scope=variable.row_scope,
        )
        set_solver_var(col_node, variable)
        expressions.append(_make_is_not_null(col_node))
    return expressions


def _path_variable_index_for_constraints(
    path_constraints: List[exp.Expression],
    row_bindings: Dict[str, RowBinding],
) -> PathVariableIndex:
    binding_keys = {
        (
            binding.table,
            binding.alias or "",
            binding.relation.scope_id or "",
            binding.row,
        )
        for binding in row_bindings.values()
    }
    variables: PathVariableIndex = {}
    for constraint in path_constraints:
        for col in constraint.find_all(exp.Column):
            variable = solver_var(col)
            if variable is None or variable.row_scope is None:
                continue
            if variable.relation_id.name is None:
                continue
            if not variable.row_scope.startswith("r"):
                continue
            try:
                row = int(variable.row_scope[1:])
            except ValueError:
                continue
            key_prefix = (
                variable.relation_id.name.normalized,
                variable.relation_id.alias.normalized if variable.relation_id.alias else "",
                variable.relation_id.scope_id or "",
                row,
            )
            if key_prefix not in binding_keys:
                continue
            source = variable.column_id.source_column_id or variable.column_id
            variables.setdefault((*key_prefix, source.name.normalized), variable)
    return variables


def _path_variable_for_binding_column(
    path_variables: PathVariableIndex,
    binding: RowBinding,
    column: ColumnId,
) -> Optional[SolverVar]:
    expected_source = column.source_column_id or column
    return path_variables.get(
        _scoped_var_key(binding, expected_source.name.normalized)
    )


def _collect_solver_vars(
    expr: exp.Expression,
) -> Dict[SolverVar, DataType]:
    """Collect SolverVar + DataType from all columns in expression."""
    variables: Dict[SolverVar, DataType] = {}
    for col in expr.find_all(exp.Column):
        sv = solver_var(col)
        if sv is None:
            continue
        dt = col_type(col)
        if dt is not None:
            variables[sv] = dt
    return variables


def _has_explicit_row_scope(expr: exp.Expression) -> bool:
    return any(
        (solver_var(col) is not None and solver_var(col).row_scope is not None)
        for col in expr.find_all(exp.Column)
    )


def _dtype_for_solver_var(var: SolverVar, instance: Instance) -> Optional[DataType]:
    """Look up DataType for a SolverVar from instance schema."""
    table = var.relation_id.name.normalized if var.relation_id.name else ""
    schema = instance.tables.get(table)
    if schema:
        dtype = schema.get(var.column_id.name.normalized)
        if dtype:
            try:
                return DataType.build(dtype)
            except Exception:
                return None
    return None


# ---------------------------------------------------------------------------
# Join equalities from ColumnUnionFind
# ---------------------------------------------------------------------------


def _build_join_equalities(
    spec: BranchSpec,
    row_bindings: Dict[str, RowBinding]
) -> List[Tuple[SolverVar, SolverVar]]:
    """Extract solver join equalities from join EQ expressions in constraints.

    Rather than rebuilding SolverVar from Union-Find (which loses plan identity),
    extract SolverVar pairs directly from the EQ expressions that _derive_join
    already stored in spec.requirements.
    """
    equalities: List[Tuple[SolverVar, SolverVar]] = []
    seen: Set[Tuple[str, str]] = set()

    for _relation, req in spec.requirements.items():
        for constraint in req.constraints:
            if not isinstance(constraint, exp.EQ):
                continue
            left, right = constraint.this, constraint.expression
            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue
            left_sv = solver_var(left)
            right_sv = solver_var(right)
            if left_sv is None or right_sv is None:
                continue
            # Only cross-table equalities are join equalities.
            if left_sv.relation_id == right_sv.relation_id:
                continue
            for left_scoped, right_scoped in _scope_join_equality(
                spec, left_sv, right_sv, row_bindings
            ):
                pair_key = (
                    min(left_scoped.display, right_scoped.display),
                    max(left_scoped.display, right_scoped.display),
                )
                if pair_key in seen:
                    continue
                seen.add(pair_key)
                equalities.append((left_scoped, right_scoped))

    return equalities


def _scope_join_equality(
    spec: BranchSpec,
    left_var: SolverVar,
    right_var: SolverVar,
    row_bindings: Dict[str, RowBinding],
) -> List[Tuple[SolverVar, SolverVar]]:
    """Scope a plan-level join equality to the witness rows it constrains."""

    left_bindings = _bindings_for_solver_var(left_var, row_bindings)
    right_bindings = _bindings_for_solver_var(right_var, row_bindings)
    if not left_bindings or not right_bindings:
        return []

    intent_pairs = _intent_join_binding_pairs(
        spec, left_bindings, right_bindings,
    )
    if intent_pairs:
        return [
            (
                _scoped_solver_var(left_var, left_binding),
                _scoped_solver_var(right_var, right_binding),
            )
            for left_binding, right_binding in intent_pairs
        ]

    pairs: List[Tuple[RowBinding, RowBinding]] = []
    if len(left_bindings) == len(right_bindings):
        pairs = list(zip(left_bindings, right_bindings))
    elif len(left_bindings) == 1:
        pairs = [(left_bindings[0], binding) for binding in right_bindings]
    elif len(right_bindings) == 1:
        pairs = [(binding, right_bindings[0]) for binding in left_bindings]
    else:
        pairs = list(zip(left_bindings, right_bindings))

    return [
        (
            _scoped_solver_var(left_var, left_binding),
            _scoped_solver_var(right_var, right_binding),
        )
        for left_binding, right_binding in pairs
    ]


def _intent_join_binding_pairs(
    spec: BranchSpec,
    left_bindings: List[RowBinding],
    right_bindings: List[RowBinding],
) -> List[Tuple[RowBinding, RowBinding]]:
    purposes = (
        _ROW_INTENT_MATCHED_RANK_LOWER,
        _ROW_INTENT_GROUP_A,
        _ROW_INTENT_GROUP_B,
    )
    pairs: List[Tuple[RowBinding, RowBinding]] = []
    seen: Set[Tuple[Tuple[str, str, str, int], Tuple[str, str, str, int]]] = set()
    for purpose in purposes:
        left_matches = [
            binding for binding in left_bindings
            if _binding_has_intent(spec, binding, purpose)
        ]
        right_matches = [
            binding for binding in right_bindings
            if _binding_has_intent(spec, binding, purpose)
        ]
        if not left_matches or not right_matches:
            continue
        for left_binding in left_matches:
            for right_binding in right_matches:
                key = (
                    _row_binding_sort_key(left_binding),
                    _row_binding_sort_key(right_binding),
                )
                if key in seen:
                    continue
                seen.add(key)
                pairs.append((left_binding, right_binding))
    return pairs


def _binding_has_intent(
    spec: BranchSpec,
    binding: RowBinding,
    purpose: str,
) -> bool:
    req = _requirement_for_binding(spec, binding)
    if req is None:
        return False
    return purpose in req.row_intents.get(binding.row, set())


def _bindings_for_solver_var(
    var: SolverVar,
    row_bindings: Dict[str, RowBinding],
) -> List[RowBinding]:
    table = var.relation_id.name.normalized if var.relation_id.name else ""
    alias = var.relation_id.alias.normalized if var.relation_id.alias else None
    scope = var.relation_id.scope_id
    return sorted(
        (
            binding
            for binding in row_bindings.values()
            if binding.table == table
            and binding.alias == alias
            and binding.relation.scope_id == scope
        ),
        key=lambda binding: binding.row,
    )


def _scoped_solver_var(var: SolverVar, binding: RowBinding) -> SolverVar:
    return _row_bound_solver_var(var, binding)


def _duplicate_column_constraints(
    instance: Instance,
    req: TableConstraint,
    req_bindings: List[RowBinding],
) -> List[exp.Expression]:
    if len(req_bindings) < 2 or not req.duplicate_columns:
        return []
    first, second = sorted(req_bindings, key=lambda binding: binding.row)[:2]
    constraints: List[exp.Expression] = []
    for col_id in req.duplicate_columns:
        left_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, first),
            row_scope=f"r{first.row}",
        )
        right_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, second),
            row_scope=f"r{second.row}",
        )
        constraints.append(
            exp.EQ(this=left_column, expression=right_column)
        )
        constraints.append(_make_is_not_null(left_column.copy()))
        constraints.append(_make_is_not_null(right_column.copy()))
    duplicate_names = {
        col_id.name.normalized for col_id in req.duplicate_columns
    }
    for col_name in instance.tables.get(req.table, {}):
        if col_name in duplicate_names:
            continue
        if not instance.is_unique(req.table, col_name):
            continue
        col_id = physical_column(col_name, req.relation)
        left_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, first),
            row_scope=f"r{first.row}",
        )
        right_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, second),
            row_scope=f"r{second.row}",
        )
        constraints.append(
            exp.NEQ(this=left_column, expression=right_column)
        )
        constraints.append(_make_is_not_null(left_column.copy()))
        constraints.append(_make_is_not_null(right_column.copy()))
    return constraints


def _contrast_column_constraints(
    instance: Instance,
    req: TableConstraint,
    req_bindings: List[RowBinding],
) -> List[exp.Expression]:
    if len(req_bindings) < 2 or not req.contrast_columns:
        return []
    ordered = sorted(req_bindings, key=lambda binding: binding.row)
    first, second = ordered[:2]
    third = ordered[2] if len(ordered) > 2 else second
    constraints: List[exp.Expression] = []
    group_names = {col_id.name.normalized for col_id in req.group_key_columns}
    for col_id in req.contrast_columns:
        intent_constraints = _aggregate_intent_contrast_constraints(
            instance, req, ordered, col_id, group_names,
        )
        if intent_constraints:
            constraints.extend(intent_constraints)
            continue
        first_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, first),
            row_scope=f"r{first.row}",
        )
        second_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, second),
            row_scope=f"r{second.row}",
        )
        third_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, third),
            row_scope=f"r{third.row}",
        )
        if col_id.name.normalized in group_names and len(ordered) > 2:
            constraints.append(
                exp.EQ(this=first_column.copy(), expression=second_column.copy())
            )
            constraints.append(exp.NEQ(this=first_column, expression=third_column))
            constraints.append(_make_is_not_null(third_column.copy()))
        else:
            constraints.append(exp.NEQ(this=first_column, expression=second_column))
        constraints.append(_make_is_not_null(first_column.copy()))
        constraints.append(_make_is_not_null(second_column.copy()))
    return constraints


def _distinct_column_constraints(
    instance: Instance,
    req: TableConstraint,
    req_bindings: List[RowBinding],
) -> List[exp.Expression]:
    if len(req_bindings) < 2 or not req.distinct_columns:
        return []
    ordered = sorted(req_bindings, key=lambda binding: binding.row)
    constraints: List[exp.Expression] = []
    for col_id in req.distinct_columns:
        columns = [
            _solver_column(
                instance,
                _row_bound_column_id(col_id, binding),
                row_scope=f"r{binding.row}",
            )
            for binding in ordered
        ]
        for column in columns:
            constraints.append(_make_is_not_null(column.copy()))
        for left_index, left_column in enumerate(columns):
            for right_column in columns[left_index + 1:]:
                constraints.append(
                    exp.NEQ(this=left_column.copy(), expression=right_column.copy())
                )
    return constraints


def _aggregate_intent_contrast_constraints(
    instance: Instance,
    req: TableConstraint,
    bindings: List[RowBinding],
    col_id: ColumnId,
    group_names: Set[str],
) -> List[exp.Expression]:
    group_a = [
        binding for binding in bindings
        if _ROW_INTENT_GROUP_A in req.row_intents.get(binding.row, set())
    ]
    group_b = [
        binding for binding in bindings
        if _ROW_INTENT_GROUP_B in req.row_intents.get(binding.row, set())
    ]
    if not group_a or not group_b:
        return []

    constraints: List[exp.Expression] = []
    if col_id.name.normalized in group_names:
        first = group_a[0]
        second = group_a[1] if len(group_a) > 1 else group_a[0]
        third = group_b[0]
        first_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, first),
            row_scope=f"r{first.row}",
        )
        second_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, second),
            row_scope=f"r{second.row}",
        )
        third_column = _solver_column(
            instance,
            _row_bound_column_id(col_id, third),
            row_scope=f"r{third.row}",
        )
        constraints.append(
            exp.EQ(this=first_column.copy(), expression=second_column.copy())
        )
        constraints.append(exp.NEQ(this=first_column, expression=third_column))
        constraints.append(_make_is_not_null(second_column.copy()))
        constraints.append(_make_is_not_null(third_column.copy()))
        return constraints

    if len(group_a) < 2:
        return []

    for binding, value in (
        (group_a[0], 2),
        (group_a[1], 2),
        (group_b[0], 3),
    ):
        column = _solver_column(
            instance,
            _row_bound_column_id(col_id, binding),
            row_scope=f"r{binding.row}",
        )
        constraints.append(
            exp.EQ(this=column.copy(), expression=exp.Literal.number(value))
        )
        constraints.append(_make_is_not_null(column))
    return constraints


# ---------------------------------------------------------------------------
# Row extraction from solver results
# ---------------------------------------------------------------------------


def _rows_from_solver_result(
    assignments: Dict[SolverVar, Any],
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
    """Extract concrete rows from solver assignments.

    Groups by (table, row_index). Maps SolverVar fields to table/row/column.
    Skips boundary rows (row_idx >= 1000).
    """
    rows_by_slot: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
    bindings_by_slot = {
        _row_binding_sort_key(binding): binding
        for binding in row_bindings.values()
    }

    for var, value in assignments.items():
        table_name = var.relation_id.name.normalized if var.relation_id.name else ""
        if not table_name:
            continue

        # Parse row index from row_scope (e.g., "r0" -> 0).
        row_scope = var.row_scope or "r0"
        if not row_scope.startswith("r"):
            continue
        try:
            row_idx = int(row_scope[1:])
        except ValueError:
            continue

        # Skip boundary rows.
        if row_idx >= 1000:
            continue

        column_name = var.column_id.name.normalized if var.column_id.name else ""
        if not column_name:
            continue

        # Verify column exists in schema.
        schema = instance.tables.get(table_name)
        if schema is None or column_name not in schema:
            continue

        alias = var.relation_id.alias.normalized if var.relation_id.alias else ""
        scope = var.relation_id.scope_id or ""
        slot = (table_name, alias, scope, row_idx)
        if slot not in bindings_by_slot:
            continue
        row = rows_by_slot.setdefault(slot, {})
        row[column_name] = value

    return dict(sorted(rows_by_slot.items()))


def _slot_for_solver_var(
    var: SolverVar,
    row_bindings: Dict[str, RowBinding],
) -> Optional[Tuple[str, str, str, int]]:
    table_name = var.relation_id.name.normalized if var.relation_id.name else ""
    if not table_name:
        return None
    row_scope = var.row_scope or "r0"
    if not row_scope.startswith("r"):
        return None
    try:
        row_idx = int(row_scope[1:])
    except ValueError:
        return None
    alias = var.relation_id.alias.normalized if var.relation_id.alias else ""
    scope = var.relation_id.scope_id or ""
    slot = (table_name, alias, scope, row_idx)
    if slot not in {
        _row_binding_sort_key(binding)
        for binding in row_bindings.values()
    }:
        return None
    return slot


def _join_value_preference(
    left_var: SolverVar,
    right_var: SolverVar,
    instance: Instance,
) -> str:
    left_table = left_var.relation_id.name.normalized if left_var.relation_id.name else ""
    right_table = right_var.relation_id.name.normalized if right_var.relation_id.name else ""
    left_column = left_var.column_id.name.normalized if left_var.column_id.name else ""
    right_column = right_var.column_id.name.normalized if right_var.column_id.name else ""
    left_unique = bool(left_table and left_column and instance.is_unique(left_table, left_column))
    right_unique = bool(right_table and right_column and instance.is_unique(right_table, right_column))
    if right_unique and not left_unique:
        return "right"
    return "left"


def _enforce_join_equalities_on_rows(
    rows: Dict[Tuple[str, str, str, int], Dict[str, Any]],
    join_equalities: List[Tuple[SolverVar, SolverVar]],
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> None:
    for left_var, right_var in join_equalities:
        left_slot = _slot_for_solver_var(left_var, row_bindings)
        right_slot = _slot_for_solver_var(right_var, row_bindings)
        if left_slot is None or right_slot is None:
            continue
        left_column = left_var.column_id.name.normalized if left_var.column_id.name else ""
        right_column = right_var.column_id.name.normalized if right_var.column_id.name else ""
        if not left_column or not right_column:
            continue
        left_row = rows.setdefault(left_slot, {})
        right_row = rows.setdefault(right_slot, {})
        left_value = left_row.get(left_column)
        right_value = right_row.get(right_column)
        if left_value is None and right_value is None:
            continue
        if left_value is None:
            value = right_value
        elif right_value is None:
            value = left_value
        elif left_value == right_value:
            value = left_value
        elif _join_value_preference(left_var, right_var, instance) == "right":
            value = right_value
        else:
            value = left_value
        left_row[left_column] = value
        right_row[right_column] = value


def _completed_rows_by_slot(
    completed: Dict[str, List[Dict[str, Any]]],
    row_bindings: Dict[str, RowBinding],
) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
    slot_rows: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
    table_positions: Dict[str, int] = {}
    for binding in sorted(row_bindings.values(), key=_row_binding_sort_key):
        table_rows = completed.get(binding.table, [])
        position = table_positions.get(binding.table, 0)
        table_positions[binding.table] = position + 1
        if position >= len(table_rows):
            continue
        slot_rows[_row_binding_sort_key(binding)] = table_rows[position]
    return slot_rows


# ---------------------------------------------------------------------------
# Fallback row generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Row completion
# ---------------------------------------------------------------------------


def _apply_literal_constraints_to_row(
    row: Dict[str, Any],
    binding: RowBinding,
    req: TableConstraint,
    instance: Instance,
) -> None:
    for constraint in req.constraints:
        assignment = _literal_assignment_for_constraint(
            constraint, binding, instance,
        )
        if assignment is None:
            continue
        col_name, value = assignment
        row[col_name] = value
    _apply_inequality_constraints_to_row(row, binding, req, instance)


def _apply_inequality_constraints_to_row(
    row: Dict[str, Any],
    binding: RowBinding,
    req: TableConstraint,
    instance: Instance,
) -> None:
    for constraint in req.constraints:
        if not isinstance(constraint, exp.NEQ):
            continue
        if not (
            isinstance(constraint.this, exp.Column)
            and isinstance(constraint.expression, exp.Column)
        ):
            continue
        left = _binding_column_name(constraint.this, binding, instance)
        right = _binding_column_name(constraint.expression, binding, instance)
        if left is None or right is None:
            continue
        left_value = row.get(left)
        right_value = row.get(right)
        if left_value is None and right_value is None:
            continue
        if left_value is None:
            row[left] = _different_column_value(
                instance,
                binding.table,
                left,
                row,
                right_value,
            )
        elif right_value is None:
            row[right] = _different_column_value(
                instance,
                binding.table,
                right,
                row,
                left_value,
            )
        elif _storage_equivalent_value_key(
            instance,
            binding.table,
            left,
            left_value,
        ) == _storage_equivalent_value_key(
            instance,
            binding.table,
            left,
            right_value,
        ):
            row[right] = _different_column_value(
                instance,
                binding.table,
                right,
                row,
                left_value,
            )


def _binding_column_name(
    column: exp.Column,
    binding: RowBinding,
    instance: Instance,
) -> Optional[str]:
    if not _column_matches_binding(column, binding, instance):
        return None
    normalized = identifier_name(column.name, dialect=instance.dialect).normalized
    if normalized in instance.tables.get(binding.table, {}):
        return normalized
    if column.name in instance.tables.get(binding.table, {}):
        return column.name
    return None


def _different_column_value(
    instance: Instance,
    table: str,
    column: str,
    row: Dict[str, Any],
    forbidden: Any,
) -> Any:
    context = dict(row)
    context.pop(column, None)
    forbidden_key = _storage_equivalent_value_key(instance, table, column, forbidden)
    for _attempt in range(16):
        value = instance.generate_value(
            table,
            column,
            row_context=context,
        )
        if _storage_equivalent_value_key(instance, table, column, value) != forbidden_key:
            return value
    if isinstance(forbidden, str):
        return _coerce_literal_for_column(instance, table, column, f"{forbidden}_neq")
    if isinstance(forbidden, bool):
        return _coerce_literal_for_column(instance, table, column, int(not forbidden))
    if isinstance(forbidden, (int, float)):
        return _coerce_literal_for_column(instance, table, column, forbidden + 1)
    return instance.generate_value(table, column, row_context=context)


def _literal_assignment_for_constraint(
    constraint: exp.Expression,
    binding: RowBinding,
    instance: Instance,
) -> Optional[Tuple[str, Any]]:
    if isinstance(constraint, exp.EQ):
        return _literal_assignment_from_binary(
            constraint.this, constraint.expression, binding, instance,
        )
    if isinstance(constraint, exp.Between) and isinstance(constraint.this, exp.Column):
        col = constraint.this
        if not _column_matches_binding(col, binding, instance):
            return None
        low = concrete(constraint.args.get("low"))
        if low is None:
            return None
        high = concrete(constraint.args.get("high"))
        value = _between_literal_for_row(low, high, binding.row)
        return (
            col.name,
            _coerce_literal_for_column(instance, binding.table, col.name, value),
        )
    return None


def _literal_assignment_from_binary(
    left: exp.Expression,
    right: exp.Expression,
    binding: RowBinding,
    instance: Instance,
) -> Optional[Tuple[str, Any]]:
    if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
        col, literal = left, right
    elif isinstance(right, exp.Column) and not isinstance(left, exp.Column):
        col, literal = right, left
    else:
        return None
    if not _column_matches_binding(col, binding, instance):
        return None
    value = concrete(literal)
    if value is None:
        return None
    return col.name, _coerce_literal_for_column(instance, binding.table, col.name, value)


def _column_matches_binding(
    col: exp.Column,
    binding: RowBinding,
    instance: Instance,
) -> bool:
    if col.name not in instance.tables.get(binding.table, {}):
        return False
    col_table = col.table or ""
    if not col_table:
        return True
    return col_table == binding.table or (
        binding.alias is not None and col_table == binding.alias
    )


def _coerce_literal_for_column(
    instance: Instance,
    table: str,
    column: str,
    value: Any,
) -> Any:
    dtype = instance.tables.get(table, {}).get(column, "")
    normalized = str(dtype).lower()
    try:
        family = type_family(DataType.build(dtype))
    except Exception:
        family = TypeFamily.UNKNOWN
    if family in {TypeFamily.DATE, TypeFamily.DATETIME, TypeFamily.TIME}:
        if isinstance(value, str):
            return StorageLiteral(value)
    if any(token in normalized for token in ("char", "text", "clob", "varchar")):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)
    return value


def _between_literal_for_row(low: Any, high: Any, row_index: int) -> Any:
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        candidate = low + row_index
        if candidate <= high:
            return candidate
    if isinstance(low, str) and isinstance(high, str):
        try:
            low_int = int(low)
            high_int = int(high)
        except ValueError:
            return low
        candidate = low_int + row_index
        if candidate <= high_int:
            return str(candidate)
    return low


def _fk_ordered_bindings(
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> List[RowBinding]:
    """Sort bindings by FK dependency order (parent tables first).

    instance.data keys are already in FK topological order because
    Instance.create_rows inserts via _creation_order.
    """
    fk_order = {name: idx for idx, name in enumerate(instance.data.keys())}
    return sorted(
        row_bindings.values(),
        key=lambda b: (fk_order.get(b.table, 9999), _row_binding_sort_key(b)),
    )


def _pre_fill_group_key_values(
    row: Dict[str, Any],
    binding: RowBinding,
    req: TableConstraint,
    instance: Instance,
    group_cache: Dict[Tuple[str, Optional[str], Optional[str], str], Any],
) -> None:
    """Pre-fill GROUP BY column values to ensure consistency across rows.

    Rows sharing the same (table, alias, scope, column_name) group key
    get the same generated value, ensuring GROUP BY produces meaningful groupings.
    """
    contrast_names = {col_id.name.normalized for col_id in req.contrast_columns}
    for cid in req.group_key_columns:
        col_name = cid.name.normalized
        if col_name not in instance.tables.get(binding.table, {}):
            continue
        if col_name in contrast_names:
            continue
        key = (
            binding.table,
            binding.alias,
            binding.relation.scope_id,
            col_name,
        )
        if col_name in row:
            group_cache.setdefault(key, row[col_name])
        if key not in group_cache:
            group_cache[key] = instance.generate_value(
                binding.table, col_name, row_context=row,
            )
        if key in group_cache:
            row[col_name] = group_cache[key]


def _storage_equivalent_value_key(
    instance: Instance,
    table: str,
    column: str,
    value: Any,
) -> Any:
    relation = instance.table_id(table)
    column_id = instance.column_id(relation, column)
    return instance._column_storage_value(relation, column_id, value)


def _pre_fill_plan_distinct_values(
    row: Dict[str, Any],
    binding: RowBinding,
    req: TableConstraint,
    instance: Instance,
    seen_values: Dict[Tuple[str, str], Set[Any]],
    *,
    force: bool = False,
) -> None:
    """Pre-fill unique column values to ensure distinctness across rows.

    Ensures that unique columns get different values across all rows in the spec,
    not just within a single table insertion.
    """
    if not (
        force
        or req.duplicate_columns
        or req.contrast_columns
        or req.distinct_columns
        or req.ordered_columns
        or req.row_intents
    ):
        return
    protected_columns = tuple(req.duplicate_columns)
    if not force:
        protected_columns += tuple(req.group_key_columns)
    protected = {column.name.normalized for column in protected_columns}
    for col_name in instance.tables.get(binding.table, {}):
        if col_name in protected or not instance.is_unique(binding.table, col_name):
            continue
        key = (binding.table, col_name)
        seen = seen_values.setdefault(key, set())
        value = row.get(col_name)
        if value is not None:
            storage_key = _storage_equivalent_value_key(
                instance, binding.table, col_name, value,
            )
            if storage_key not in seen:
                seen.add(storage_key)
                continue
        context = dict(row)
        context.pop(col_name, None)
        for _attempt in range(16):
            value = instance.generate_value(
                binding.table,
                col_name,
                row_context=context,
            )
            storage_key = _storage_equivalent_value_key(
                instance, binding.table, col_name, value,
            )
            if storage_key not in seen:
                row[col_name] = value
                seen.add(storage_key)
                break


def _clone_rows_for_high_limit(
    rows: Dict[Tuple[str, str, str, int], Dict[str, Any]],
    row_bindings: Dict[str, RowBinding],
    spec: BranchSpec,
    instance: Instance,
) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
    """Clone rows to satisfy min_rows requirements for high LIMIT queries.

    When the spec requires more rows than currently exist (due to LIMIT + OFFSET),
    this function clones existing rows and regenerates unique column values to
    produce additional rows.
    """
    MAX_TOTAL_ROWS = 500
    cloned: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}

    # Count existing rows per table
    table_row_counts: Dict[str, int] = {}
    for (table, _alias, _scope, _row_idx) in rows:
        table_row_counts[table] = table_row_counts.get(table, 0) + 1

    for _relation, req in spec.requirements.items():
        physical = req.table
        current_count = table_row_counts.get(physical, 0)
        target = min(req.min_rows, MAX_TOTAL_ROWS)
        if current_count >= target:
            continue

        # Find the last row for this table to clone
        last_slot = None
        for slot in sorted(rows.keys()):
            if slot[0] == physical:
                last_slot = slot
        if last_slot is None:
            continue

        base_row = rows[last_slot]
        while current_count < target:
            new_row = {
                col_name: value
                for col_name, value in base_row.items()
                if col_name in instance.tables.get(physical, {})
            }
            for col_name in instance.tables.get(physical, {}):
                if instance.is_unique(physical, col_name):
                    context = dict(new_row)
                    context.pop(col_name, None)
                    new_row[col_name] = instance.generate_value(
                        physical, col_name, row_context=context,
                    )
            # Create new slot with next row index
            new_row_idx = current_count
            new_slot = (physical, last_slot[1], last_slot[2], new_row_idx)
            cloned[new_slot] = new_row
            current_count += 1
            table_row_counts[physical] = current_count

    return cloned


MaterializationColumnMap = Dict[Tuple[str, str, str, str], str]


def _materialization_column_map(
    spec: BranchSpec,
    instance: Instance,
) -> MaterializationColumnMap:
    aliases: MaterializationColumnMap = {}
    for req in spec.requirements.values():
        for constraint in req.constraints:
            for col in constraint.find_all(exp.Column):
                col_id = column_identity(col)
                if col_id is None:
                    variable = solver_var(col)
                    col_id = variable.column_id if variable is not None else None
                if col_id is None:
                    continue
                relation = col_id.relation
                source = _physical_source_id(col_id)
                if (
                    relation is None
                    or relation.name is None
                    or source.kind is not ColumnKind.PHYSICAL
                    or source.relation is None
                    or source.relation.name is None
                ):
                    continue
                table = relation.name.normalized
                source_table = source.relation.name.normalized
                source_name = source.name.normalized
                if table != source_table:
                    continue
                physical_columns = instance.tables.get(table, {})
                if col.name in physical_columns or source_name not in physical_columns:
                    continue
                alias = relation.alias.normalized if relation.alias else ""
                scope = relation.scope_id or ""
                aliases[(table, alias, scope, col.name)] = source_name
    return aliases


def _resolve_materialization_column(
    slot: Tuple[str, str, str, int],
    column: str,
    aliases: MaterializationColumnMap,
) -> Optional[str]:
    table, alias, scope, _row_idx = slot
    return aliases.get((table, alias, scope, column))


def _normalize_pending_rows_for_materialization(
    rows: Dict[Tuple[str, str, str, int], Dict[str, Any]],
    spec: BranchSpec,
    instance: Instance,
) -> Dict[Tuple[str, str, str, int], Dict[str, Any]]:
    aliases = _materialization_column_map(spec, instance)
    normalized_rows: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
    for slot, row in rows.items():
        table = slot[0]
        physical_columns = instance.tables.get(table, {})
        normalized: Dict[str, Any] = {}
        for column, value in row.items():
            if column in physical_columns:
                if value is None and not instance.nullable(table, column):
                    logger.debug(
                        "speculate_materialization_skip_non_nullable_null table=%s column=%s",
                        table,
                        column,
                    )
                    continue
                normalized[column] = value
                continue
            target = _resolve_materialization_column(slot, column, aliases)
            if target is not None and target in physical_columns:
                if value is None and not instance.nullable(table, target):
                    logger.debug(
                        "speculate_materialization_skip_non_nullable_null table=%s column=%s source=%s",
                        table,
                        column,
                        target,
                    )
                    continue
                normalized.setdefault(target, value)
                logger.debug(
                    "speculate_materialization_rewrite table=%s column=%s source=%s",
                    table,
                    column,
                    target,
                )
                continue
            logger.debug(
                "speculate_materialization_skip_non_physical table=%s column=%s",
                table,
                column,
            )
        normalized_rows[slot] = normalized
    return normalized_rows


def _complete_gold_rows(
    rows: Dict[Tuple[str, str, str, int], Dict[str, Any]],
    row_bindings: Dict[str, RowBinding],
    spec: BranchSpec,
    instance: Instance,
    join_equalities: Optional[List[Tuple[SolverVar, SolverVar]]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fill missing columns using instance.create_rows.

    This function pre-fills special values (group keys, plan distinct values,
    high LIMIT clones) and then delegates to instance.create_rows for the rest.
    """
    import time as _time
    _t0 = _time.monotonic()

    pending_rows = {slot: dict(row) for slot, row in rows.items()}
    group_values: Dict[Tuple[str, Optional[str], Optional[str], str], Any] = {}
    plan_distinct_values: Dict[Tuple[str, str], Set[Any]] = {}

    _t_fill_start = _time.monotonic()
    ordered_bindings = _fk_ordered_bindings(row_bindings, instance)

    # Phase 1: Pre-fill special values
    for binding in ordered_bindings:
        row = pending_rows.get(_row_binding_sort_key(binding), {})
        req = _requirement_for_binding(spec, binding)

        if req is not None:
            _apply_literal_constraints_to_row(row, binding, req, instance)
            _pre_fill_group_key_values(row, binding, req, instance, group_values)
            _pre_fill_plan_distinct_values(
                row, binding, req, instance, plan_distinct_values,
                force=spec.has_goal("duplicate"),
            )
            _apply_literal_constraints_to_row(row, binding, req, instance)

    _t_fill_end = _time.monotonic()
    _t_post_fill_start = _time.monotonic()

    # Phase 2: Clone rows for high LIMIT
    cloned_rows = _clone_rows_for_high_limit(pending_rows, row_bindings, spec, instance)
    pending_rows.update(cloned_rows)
    pending_rows = _normalize_pending_rows_for_materialization(
        pending_rows,
        spec,
        instance,
    )

    # Phase 3: Group rows by table for create_rows
    rows_by_table: Dict[str, List[Dict[str, Any]]] = {}
    for (table, _alias, _scope, _row_idx), row in sorted(pending_rows.items()):
        rows_by_table.setdefault(table, []).append(row)

    # Phase 4: Use instance.create_rows to fill in remaining columns
    completed: Dict[str, List[Dict[str, Any]]] = {}
    if rows_by_table:
        completed = _materialize_rows(instance, rows_by_table)

    # Phase 5: Enforce join equalities on completed rows
    if join_equalities:
        _enforce_join_equalities_on_rows(
            _completed_rows_by_slot(completed, row_bindings),
            join_equalities,
            row_bindings,
            instance,
        )

    _t_end = _time.monotonic()
    logger.debug(
        "_complete_gold_rows: fill=%.3fs clone=%.3fs create_rows=%.3fs total=%.3fs",
        _t_fill_end - _t_fill_start,
        _t_post_fill_start - _t_fill_start,
        _t_end - _t_post_fill_start,
        _t_end - _t0,
    )
    return completed


def _finite_domain_constraints_for_bindings(
    instance: Instance,
    row_bindings: Dict[str, RowBinding],
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    seen: Set[Tuple[SolverVar, Tuple[Any, ...]]] = set()
    for binding in row_bindings.values():
        try:
            table = instance.schema_spec.get_table(binding.table)
        except KeyError:
            continue
        for column in table.columns:
            allowed_values = instance.builder.compiler.compile(column).allowed_values
            if not allowed_values:
                continue
            col_node = _solver_column(
                instance,
                _row_bound_column_id(column.id, binding),
                row_scope=f"r{binding.row}",
            )
            variable = solver_var(col_node)
            if variable is None:
                continue
            key = (variable, tuple(allowed_values))
            if key in seen:
                continue
            seen.add(key)
            constraints.append(
                exp.In(
                    this=col_node,
                    expressions=[to_literal(value) for value in allowed_values],
                )
            )
    return constraints


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialize_rows(
    instance: Instance,
    rows: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Write rows to instance in FK dependency order.

    Returns a dict mapping table_name → list of completed row dicts.
    """
    import time as _time
    _t0 = _time.monotonic()
    materialized = instance.create_rows(rows)
    _t1 = _time.monotonic()
    completed: Dict[str, List[Dict[str, Any]]] = {}
    for table_name, table_rows in rows.items():
        relation = instance.table_id(table_name)
        created_rows = materialized.get(relation, [])
        for row, created in zip(table_rows, created_rows):
            if relation not in created.positions:
                continue
            completed_row = _row_value_dict(instance.get_row(relation, created.positions[relation]))
            for column_name, value in completed_row.items():
                row.setdefault(column_name, value)
            completed.setdefault(table_name, []).append(row)
    _t2 = _time.monotonic()
    logger.debug(
        "_materialize_rows: create_rows=%.3fs sync_return=%.3fs n_rows=%d total=%.3fs",
        _t1 - _t0, _t2 - _t1, len(completed), _t2 - _t0,
    )
    return completed


# ---------------------------------------------------------------------------
# Scalar subquery satisfaction
# ---------------------------------------------------------------------------


def _iter_steps_with_subplans(step: Step):
    """Yield all steps including those inside SubPlans."""
    seen: Set[int] = set()

    def walk(current: Step):
        if id(current) in seen:
            return
        seen.add(id(current))
        yield current
        if isinstance(current, SubPlan) and current.inner is not None:
            yield from walk(current.inner)
        for subplan in current.subplan_dependencies:
            yield subplan
            if subplan.inner is not None:
                yield from walk(subplan.inner)
        for dep in current.chain_dependencies:
            yield from walk(dep)

    yield from walk(step)


def _iter_all_plan_steps(plan: Plan):
    for step in plan.ordered_steps:
        yield from _iter_steps_with_subplans(step)


# ---------------------------------------------------------------------------
# Resolver class
# ---------------------------------------------------------------------------


class Resolver:
    """Turn BranchSpec into concrete row values via global constraint solving."""

    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        dialect: str = "sqlite",
        solver=None,
    ):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.solver = solver

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        """Produce concrete rows for each table in the spec."""
        if spec.unsupported_reason is not None:
            logger.debug(
                "unsupported lowering for spec=%s reason=%s",
                spec.branch,
                spec.unsupported_reason,
            )
            return {}

        # Build global constraint.
        constraint, row_bindings = self._build_global_constraint(spec)

        # Solve. Unsatisfied constraints mean this branch cannot produce a witness.
        if self.solver is None:
            return {}
        result = self.solver.solve(constraint)
        if not result.sat:
            logger.debug(
                "Solver failed for spec=%s reason=%s",
                spec.branch, result.reason,
            )
            return {}
        rows = _rows_from_solver_result(result.assignments, row_bindings, self.instance)
        _enforce_join_equalities_on_rows(
            rows, constraint.join_equalities, row_bindings, self.instance,
        )

        # Complete missing columns for satisfiable row bindings.
        # This also handles materialization and join equality enforcement.
        try:
            rows = _complete_gold_rows(
                rows, row_bindings, spec, self.instance,
                join_equalities=constraint.join_equalities,
            )
        except ConstraintViolationError as exc:
            logger.debug(
                "materialization failed for spec=%s reason=%s",
                spec.branch,
                exc,
            )
            return {}

        if not rows:
            return {}

        return rows

    def _build_global_constraint(
        self,
        spec: BranchSpec,
    ) -> Tuple[SolverConstraint, Dict[str, RowBinding]]:
        """Build a single SolverConstraint for ALL tables in the spec."""
        row_bindings = _build_gold_row_bindings(spec)
        constraints: List[exp.Expression] = []
        # Shared scoped-variable map: keyed by row binding and storage column.
        # Populated by annotated constraints (WHERE), reused by unscoped ones
        # (IS NOT NULL from _add_schema_constraints).
        scoped_vars: PathVariableIndex = {}

        # Two-pass: annotated constraints first (to populate scoped_vars),
        # then unscoped ones (IS NOT NULL from _add_schema_constraints).
        all_constraint_items: List[Tuple[RelationId, TableConstraint, List[RowBinding]]] = []
        for relation, req in spec.requirements.items():
            req_bindings = _bindings_for_requirement(relation, req, row_bindings)
            if not req_bindings:
                continue
            all_constraint_items.append((relation, req, req_bindings))

        for pass_annotated in (True, False):
            for relation, req, req_bindings in all_constraint_items:
                for constraint_expr in req.constraints:
                    if _unsupported_solver_constraint_expression(constraint_expr):
                        continue
                    if _has_explicit_row_scope(constraint_expr):
                        if pass_annotated:
                            constraints.append(constraint_expr.copy())
                        continue
                    has_annotated = any(
                        solver_var(col) is not None
                        for col in constraint_expr.find_all(exp.Column)
                    )
                    if pass_annotated != has_annotated:
                        continue
                    # Skip subquery constraints.
                    if constraint_expr.find(exp.Subquery):
                        continue
                    # Skip cross-table EQ constraints (handled by join equalities).
                    if (
                        isinstance(constraint_expr, exp.EQ)
                        and isinstance(constraint_expr.this, exp.Column)
                        and isinstance(constraint_expr.expression, exp.Column)
                    ):
                        this_sv = solver_var(constraint_expr.this)
                        expr_sv = solver_var(constraint_expr.expression)
                        if this_sv and expr_sv and this_sv.relation_id != expr_sv.relation_id:
                            continue

                    for binding in req_bindings:
                        rewritten = _rewrite_constraint_for_binding(
                            constraint_expr, binding, self.instance,
                            row_bindings=row_bindings,
                            scoped_vars=scoped_vars,
                        )
                        if rewritten is not None:
                            constraints.append(rewritten)

        path_variables = _path_variable_index_for_constraints(
            constraints,
            row_bindings,
        )
        for _relation, _req, req_bindings in all_constraint_items:
            for binding in req_bindings:
                constraints.extend(
                    _database_not_null_constraints_for_binding(
                        self.instance,
                        binding,
                        path_variables=path_variables,
                    )
                )
                constraints.extend(
                    _database_check_constraints_for_binding(
                        self.instance,
                        binding,
                        path_variables=path_variables,
                    )
                )

        for _relation, req, req_bindings in all_constraint_items:
            constraints.extend(
                _duplicate_column_constraints(
                    self.instance,
                    req,
                    req_bindings,
                )
            )
            constraints.extend(
                _contrast_column_constraints(
                    self.instance,
                    req,
                    req_bindings,
                )
            )
            constraints.extend(
                _distinct_column_constraints(
                    self.instance,
                    req,
                    req_bindings,
                )
            )

        # Build join equalities.
        join_equalities = _build_join_equalities(spec, row_bindings)

        # Add IS NOT NULL for join equality columns.
        seen_not_null: Set[str] = set()
        for left_var, right_var in join_equalities:
            for var in (left_var, right_var):
                key = var.display
                if key in seen_not_null:
                    continue
                seen_not_null.add(key)
                col_node = _solver_column(
                    self.instance,
                    var.column_id,
                    row_scope=var.row_scope,
                )
                constraints.append(_make_is_not_null(col_node))

        constraints.extend(
            _finite_domain_constraints_for_bindings(
                self.instance,
                row_bindings,
            )
        )

        # Collect variables.
        variables: Dict[SolverVar, DataType] = {}
        for expr in constraints:
            variables.update(_collect_solver_vars(expr))
        for left_var, right_var in join_equalities:
            if left_var not in variables:
                dt = _dtype_for_solver_var(left_var, self.instance)
                if dt is not None:
                    variables[left_var] = dt
            if right_var not in variables:
                dt = _dtype_for_solver_var(right_var, self.instance)
                if dt is not None:
                    variables[right_var] = dt

        # Build target relations.
        target_relations: Set[RelationId] = set()
        for binding in row_bindings.values():
            target_relations.add(binding.relation)

        solver_constraint = SolverConstraint(
            target_relations=tuple(target_relations),
            constraints=constraints,
            join_equalities=join_equalities,
            variables=variables,
        )
        return solver_constraint, row_bindings


def _unsupported_solver_constraint_expression(expr: exp.Expression) -> bool:
    return expr.find(exp.Subquery) is not None or expr.find(exp.Exists) is not None


def _branch_objective_observed(
    plan: Plan,
    instance: Instance,
    dialect: str,
    spec: BranchSpec,
    rows: Dict[str, List[Dict[str, Any]]],
) -> bool:
    if spec.validation_expectation is None:
        return True
    if spec.validation_expectation != "query_non_empty":
        return False
    if dialect != "sqlite":
        return True
    return _sqlite_query_returns_rows(plan, instance, rows)


def _sqlite_query_returns_rows(
    plan: Plan,
    instance: Instance,
    rows: Dict[str, List[Dict[str, Any]]],
) -> bool:
    try:
        with sqlite3.connect(":memory:") as connection:
            for ddl in instance.ddls.split(";"):
                ddl = ddl.strip()
                if ddl:
                    connection.execute(ddl)
            for table_name in instance.data:
                table_rows = rows.get(table_name, [])
                for row in table_rows:
                    columns = [
                        column
                        for column in row
                        if column in instance.tables.get(table_name, {})
                    ]
                    if not columns:
                        continue
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _column in columns)
                    statement = (
                        f'INSERT INTO "{table_name}" ({quoted}) '
                        f"VALUES ({placeholders})"
                    )
                    values = [
                        _sqlite_validation_value(row[column])
                        for column in columns
                    ]
                    connection.execute(statement, values)
            connection.commit()
            sql = plan.expression.sql(dialect="sqlite")
            return bool(connection.execute(sql).fetchone())
    except Exception as exc:
        logger.debug("branch validation failed: %s", exc)
        return False


def _sqlite_validation_value(value: Any) -> Any:
    if isinstance(value, StorageLiteral):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


# =============================================================================
# Public API
# =============================================================================

def speculate(
    plan: Plan,
    instance: Instance,
    dialect: str = "sqlite",
    config: Optional[SpeculateConfig] = None,
    thresholds: Optional[CoverageThresholds] = None,
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve.

    Returns one entry per branch (positive + negatives).
    Each entry is (branch_name, rows_per_table).

    Args:
        plan: The query plan to generate data for.
        instance: The database instance to materialize rows into.
        dialect: SQL dialect (default: "sqlite").
        config: SpeculateConfig controlling which branches to generate.
            If None, derived from thresholds or uses gold_non_empty().
        thresholds: CoverageThresholds to derive config from.
            Takes precedence over config if both are provided.

    Returns:
        List of (branch_name, rows_per_table) tuples.
    """
    if thresholds is not None:
        config = SpeculateConfig.from_thresholds(thresholds)
    elif config is None:
        config = SpeculateConfig.gold_non_empty()

    _ = plan.annotations  # Ensure identity is prepared on all steps
    propagator = Propagator(plan, instance, dialect, config=config)
    solver = Solver(dialect=dialect)
    resolver = Resolver(plan, instance, dialect, solver=solver)

    branch_specs = propagator.propagate()
    branch_specs = sorted(
        branch_specs,
        key=lambda spec: 1 if spec.branch == "positive_seed_deferred" else 0,
    )
    logger.info("Generated %d branch specs", len(branch_specs))

    results = []
    for spec in branch_specs:
        if not spec.requirements:
            continue
        rows = resolver.resolve(spec)
        if rows:
            if not _branch_objective_observed(
                plan, instance, dialect, spec, rows,
            ):
                logger.debug(
                    "generated rows did not observe branch objective: %s",
                    spec.branch,
                )
                continue
            results.append((spec.branch, rows))

    return results


__all__ = [
    "BranchSpec",
    "Propagator",
    "Resolver",
    "SpeculateConfig",
    "TableConstraint",
    "speculate",
]
