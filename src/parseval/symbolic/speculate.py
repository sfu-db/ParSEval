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
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.dtype import (
    DataType,
    TypeFamily,
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
from parseval.plan.rex import column_meta, concrete, negate_predicate
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var, solver_var
from parseval.solver.types import col_type
from .types import BranchTree, CoverageThresholds

logger = logging.getLogger("parseval.speculate")


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
        self._is_gold_mode = (
            self.config.negative == 0
            and self.config.null == 0
            and self.config.left_unmatched == 0
            and self.config.right_unmatched == 0
            and self.config.having_fail == 0
        )
        self._negate_step: Optional[Step] = None
        self._negate_conjunct: int = 0
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
        # Aggregate NULL detection — only for non-positive specs.
        # Positive specs need real values for aggregates, not NULLs.
        if spec.branch != "positive":
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
        ann = self.plan.annotation_for(step)
        projected_ids = tuple(ann.projected_columns) + tuple(ann.referenced_columns)
        for col_id in projected_ids:
            if col_id.kind is ColumnKind.AGGREGATE:
                continue
            if (
                col_id.relation is not None
                and not _relation_is_materializable(self.instance, col_id.relation)
                and self._virtual_projection_for_relation(col_id.relation) is not None
            ):
                source_id = col_id
            else:
                source_id = _physical_source_id(col_id)
            if source_id.kind is ColumnKind.AGGREGATE:
                continue
            relation = source_id.relation
            if relation is None or not self._is_materializable_relation(relation):
                continue
            col_name = source_id.name.normalized
            if col_name.startswith("_"):
                continue
            tc = spec.require(relation)
            if not _has_is_not_null(tc.constraints, col_name):
                tc.constraints.append(_make_is_not_null(self._solver_col(source_id)))
        # Duplicate / DISTINCT handling.
        for relation_id, tc in spec.requirements.items():
            dup_ids = []
            for col_id in projected_ids:
                if col_id.kind is ColumnKind.AGGREGATE:
                    continue
                if (
                    col_id.relation is not None
                    and not _relation_is_materializable(self.instance, col_id.relation)
                    and self._virtual_projection_for_relation(col_id.relation) is not None
                ):
                    source_id = col_id
                else:
                    source_id = _physical_source_id(col_id)
                if source_id.kind is ColumnKind.AGGREGATE:
                    continue
                if source_id.relation == relation_id:
                    dup_ids.append(source_id)
            if step.distinct and dup_ids:
                tc.duplicate_columns = dup_ids
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
        """No-op for Sort steps."""
        pass

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

    # -----------------------------------------------------------------
    # Top-level propagation
    # -----------------------------------------------------------------

    def _positive_spec(self) -> Optional[BranchSpec]:
        """Build the positive branch spec."""
        if self.config.positive <= 0:
            return None
        try:
            pos = BranchSpec(branch="positive")
            self._walk_plan(pos)
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
                            neg = BranchSpec(branch=f"negative_c{idx}")
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
                        left_un = BranchSpec(branch="left_unmatched")
                        self._propagate_unmatched_left(step, left_un)
                        specs.append(left_un)
                    if self.config.right_unmatched > 0:
                        for join_rel in step.joins or {}:
                            join_display = join_rel.alias.normalized if join_rel.alias else (join_rel.name.normalized if join_rel.name else "?")
                            right_un = BranchSpec(
                                branch=f"right_unmatched_{join_display}"
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
                        fail = BranchSpec(branch="having_fail")
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
            pos = pos or BranchSpec(branch="positive")
            null_targets = self._collect_null_target_columns(pos)
            if null_targets:
                for table, cols in null_targets.items():
                    for col_name in cols:
                        null_spec = BranchSpec(
                            branch=f"null_{table}.{col_name}"
                        )
                        self._walk_plan(null_spec)
                        self._apply_single_null_override(
                            null_spec, table, col_name
                        )
                        specs.append(null_spec)
            else:
                null_spec = BranchSpec(branch="null_branch")
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
                self._collect_case_when_conditions()
            ):
                case_spec = BranchSpec(branch=f"case_else_{case_idx}")
                self._walk_plan(case_spec)
                for cond in when_conditions:
                    negated = negate_predicate(cond.copy())
                    self._store_expression(negated, case_spec)
                specs.append(case_spec)
        except Exception as exc:
            logger.debug("CASE WHEN propagation failed: %s", exc)
        return specs

    def propagate(self) -> List[BranchSpec]:
        """Produce specs for branches based on config thresholds."""
        specs: List[BranchSpec] = []
        pos = self._positive_spec()
        if pos:
            specs.append(pos)
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
            relation = argument.relation
            if relation not in spec.requirements:
                continue
            if constraint.get("function") not in {"sum", "avg", "min", "max"}:
                continue
            row_count = max(spec.requirements[relation].min_rows, 1)
            self._append_having_value_constraint(
                spec.requirements[relation],
                argument,
                constraint,
                row_count,
            )

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

    def _collect_case_when_conditions(
        self,
    ) -> List[List[exp.Expression]]:
        """Collect WHEN conditions from all CASE expressions."""
        result: List[List[exp.Expression]] = []
        for step in self.plan.ordered_steps:
            expressions: List[exp.Expression] = []
            condition = getattr(step, "condition", None)
            if condition is not None:
                expressions.append(condition)
            for proj in getattr(step, "projections", None) or []:
                if isinstance(proj, exp.Expression):
                    expressions.append(proj)
            for agg in getattr(step, "aggregations", None) or []:
                if isinstance(agg, exp.Expression):
                    expressions.append(agg)
            for expr in expressions:
                for case_expr in expr.find_all(exp.Case):
                    conditions = []
                    case_operand = case_expr.this
                    for if_node in case_expr.args.get("ifs") or []:
                        cond = if_node.this
                        if cond is not None:
                            if case_operand is not None:
                                cond = exp.EQ(
                                    this=case_operand.copy(),
                                    expression=cond.copy(),
                                )
                            conditions.append(cond)
                    if conditions:
                        result.append(conditions)
        return result

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


# =============================================================================
# Resolver: solver integration and row materialization
# =============================================================================

def _row_value_dict(row) -> Dict[str, Any]:
    """Convert a Row to a plain dict keyed by column name string."""
    values: Dict[str, Any] = {}
    for column, value in row.items():
        key = column.name.normalized if isinstance(column, ColumnId) else str(column)
        values[key] = value.concrete if hasattr(value, "concrete") else value
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


# ---------------------------------------------------------------------------
# Constraint rewriting for row scoping
# ---------------------------------------------------------------------------


def _rewrite_constraint_for_binding(
    constraint: exp.Expression,
    binding: RowBinding,
    instance: Instance,
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
            if sv.relation_id.name.normalized != binding.table:
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
    path_constraints: List[exp.Expression],
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
            column.name.normalized: _path_variable_for_check_column(
                path_constraints,
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
    path_constraints: List[exp.Expression],
) -> List[exp.Expression]:
    constraints = instance.database_constraints(binding.relation)
    required_columns = dict.fromkeys(
        tuple(constraints.not_null_columns) + tuple(constraints.primary_key)
    )
    expressions: List[exp.Expression] = []
    for column in required_columns:
        variable = _path_variable_for_check_column(
            path_constraints,
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


def _path_variable_for_check_column(
    path_constraints: List[exp.Expression],
    binding: RowBinding,
    column: ColumnId,
) -> Optional[SolverVar]:
    expected_scope = f"r{binding.row}"
    expected_source = column.source_column_id or column
    for constraint in path_constraints:
        for col in constraint.find_all(exp.Column):
            variable = solver_var(col)
            if variable is None or variable.row_scope != expected_scope:
                continue
            if variable.relation_id.name is None:
                continue
            if variable.relation_id.name.normalized != binding.table:
                continue
            if binding.alias and (
                variable.relation_id.alias is None
                or variable.relation_id.alias.normalized != binding.alias
            ):
                continue
            if variable.relation_id.scope_id != binding.relation.scope_id:
                continue
            variable_source = variable.column_id.source_column_id or variable.column_id
            if (
                variable_source.name.normalized == expected_source.name.normalized
                and variable_source.relation is not None
                and expected_source.relation is not None
                and variable_source.relation.name == expected_source.relation.name
            ):
                return variable
    return None


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
    row_bindings: Dict[str, RowBinding],
    _instance: Instance,
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
                left_sv, right_sv, row_bindings
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
    left_var: SolverVar,
    right_var: SolverVar,
    row_bindings: Dict[str, RowBinding],
) -> List[Tuple[SolverVar, SolverVar]]:
    """Scope a plan-level join equality to the witness rows it constrains."""

    left_bindings = _bindings_for_solver_var(left_var, row_bindings)
    right_bindings = _bindings_for_solver_var(right_var, row_bindings)
    if not left_bindings or not right_bindings:
        return []

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


# ---------------------------------------------------------------------------
# Fallback row generation
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Row completion
# ---------------------------------------------------------------------------


def _gold_fk_columns(instance: Instance, table: str) -> Set[str]:
    """Get FK column names for a table."""
    columns: Set[str] = set()
    try:
        rel_id = instance.table_id(table)
    except KeyError:
        return columns
    for fk_spec in instance.get_foreign_keys_by_relation_id(rel_id):
        for col_id in fk_spec.source_column_ids:
            columns.add(col_id.name.normalized)
    return columns


def _composite_unique_groups(
    instance: Instance,
    table: str,
) -> List[Tuple[str, ...]]:
    """Return composite primary/unique groups that must distinguish rows."""
    groups: List[Tuple[str, ...]] = []
    try:
        constraints = instance.database_constraints(instance.table_id(table))
    except KeyError:
        return groups
    if len(constraints.primary_key) > 1:
        groups.append(tuple(column.name.normalized for column in constraints.primary_key))
    for group in constraints.unique_constraints:
        if len(group) > 1:
            groups.append(tuple(column.name.normalized for column in group))
    return groups


def _collect_existing_composite_values(
    instance: Instance,
) -> Dict[Tuple[str, Tuple[str, ...]], Set[Tuple[Any, ...]]]:
    values: Dict[Tuple[str, Tuple[str, ...]], Set[Tuple[Any, ...]]] = {}
    for table in instance.tables:
        for group in _composite_unique_groups(instance, table):
            key = (table, group)
            seen = values.setdefault(key, set())
            for existing_row in instance.get_rows(table):
                row_values = _row_value_dict(existing_row)
                if all(column in row_values for column in group):
                    seen.add(_composite_storage_key(instance, table, group, row_values))
    return values


def _composite_storage_key(
    instance: Instance,
    table: str,
    group: Tuple[str, ...],
    row: Dict[str, Any],
) -> Tuple[Any, ...]:
    try:
        relation = instance.table_id(table)
        return tuple(
            instance._column_storage_value(
                relation,
                instance.column_id(relation, column),
                row[column],
            )
            for column in group
        )
    except Exception:
        return tuple(row[column] for column in group)


def _fresh_composite_value(value: Any, attempt: int) -> Any:
    if isinstance(value, bool):
        return int(value) + attempt + 1
    if isinstance(value, int):
        return value + attempt + 1
    if isinstance(value, float):
        return value + float(attempt + 1)
    if value is None:
        return f"value_{attempt + 1}"
    return f"{value}_{attempt + 1}"


def _ensure_composite_unique_rows(
    row: Dict[str, Any],
    binding: RowBinding,
    req: Optional[TableConstraint],
    instance: Instance,
    builder,
    composite_values: Dict[Tuple[str, Tuple[str, ...]], Set[Tuple[Any, ...]]],
) -> None:
    """Preserve solved group keys while making composite key rows distinct."""
    protected = {
        col_id.name.normalized
        for col_id in (req.group_key_columns if req is not None else ())
    }
    for group in _composite_unique_groups(instance, binding.table):
        if not all(column in row for column in group):
            continue
        key = (binding.table, group)
        seen = composite_values.setdefault(key, set())
        current = _composite_storage_key(instance, binding.table, group, row)
        if current not in seen:
            seen.add(current)
            continue
        candidates = [column for column in group if column not in protected]
        if not candidates:
            candidates = list(group)
        target = candidates[0]
        context = dict(row)
        context.pop(target, None)
        for attempt in range(32):
            try:
                row[target] = builder.generate_value(
                    binding.table,
                    target,
                    row_context=context,
                )
            except Exception:
                row[target] = _fresh_composite_value(current[group.index(target)], attempt)
            current = _composite_storage_key(instance, binding.table, group, row)
            if current not in seen:
                seen.add(current)
                break
        else:
            for attempt in range(32):
                row[target] = _fresh_composite_value(
                    current[group.index(target)],
                    attempt,
                )
                current = _composite_storage_key(instance, binding.table, group, row)
                if current not in seen:
                    seen.add(current)
                    break


def _complete_gold_rows(
    rows: Dict[Tuple[str, str, str, int], Dict[str, Any]],
    row_bindings: Dict[str, RowBinding],
    spec: BranchSpec,
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fill missing columns using instance.builder.generate_value."""
    builder = type(instance.builder)(instance.schema_spec)
    for table_name in instance.tables:
        for existing_row in instance.get_rows(table_name):
            builder.runtime.remember_row(table_name, _row_value_dict(existing_row))

    pending_rows = {slot: dict(row) for slot, row in rows.items()}
    completed: Dict[str, List[Dict[str, Any]]] = {}
    group_values: Dict[Tuple[str, Optional[str], Optional[str], str], Any] = {}
    unique_values: Dict[Tuple[str, str], Set[Any]] = {}
    composite_values = _collect_existing_composite_values(instance)

    # Collect existing unique values.
    for table_name in instance.tables:
        schema = instance.tables.get(table_name)
        if not schema:
            continue
        for col_name in schema:
            if not instance.is_unique(table_name, col_name):
                continue
            key = (table_name, col_name)
            values = unique_values.setdefault(key, set())
            for existing_row in instance.get_rows(table_name):
                if col_name in existing_row:
                    cell = existing_row[col_name]
                    if cell is not None:
                        values.add(cell.concrete if hasattr(cell, "concrete") else cell)

    ordered_bindings = sorted(
        row_bindings.values(),
        key=_row_binding_sort_key,
    )
    for binding in ordered_bindings:
        row = pending_rows.pop(_row_binding_sort_key(binding), {})
        req = _requirement_for_binding(spec, binding)

        if req is not None:
            fk_columns = _gold_fk_columns(instance, binding.table)

            # Apply group_key_columns.
            for cid in req.group_key_columns:
                col_name = cid.name.normalized
                key = (
                    binding.table,
                    binding.alias,
                    binding.relation.scope_id,
                    col_name,
                )
                if col_name in row:
                    group_values.setdefault(key, row[col_name])
                if key not in group_values:
                    try:
                        group_values[key] = builder.generate_value(
                            binding.table, col_name, row_context=row,
                        )
                    except Exception:
                        pass
                if key in group_values:
                    row[col_name] = group_values[key]

            # Fill missing columns from schema.
            for col_name in instance.tables.get(binding.table, {}):
                if col_name in row:
                    continue
                try:
                    row[col_name] = builder.generate_value(
                        binding.table, col_name, row_context=row,
                    )
                except Exception:
                    pass

            # Ensure unique columns have distinct values.
            for col_name in instance.tables.get(binding.table, {}):
                if not instance.is_unique(binding.table, col_name):
                    continue
                key = (binding.table, col_name)
                seen_values = unique_values.setdefault(key, set())
                value = row.get(col_name)
                if value is None or value in seen_values:
                    if col_name in fk_columns:
                        row.pop(col_name, None)
                        continue
                    context = dict(row)
                    context.pop(col_name, None)
                    generated = False
                    for _ in range(16):
                        try:
                            value = builder.generate_value(
                                binding.table, col_name, row_context=context,
                            )
                            if value not in seen_values:
                                row[col_name] = value
                                generated = True
                                break
                        except Exception:
                            break
                    if not generated:
                        row.pop(col_name, None)
                        continue
                if col_name in row:
                    seen_values.add(row[col_name])

            _ensure_composite_unique_rows(
                row, binding, req, instance, builder, composite_values,
            )

        builder.runtime.remember_row(binding.table, row)
        completed.setdefault(binding.table, []).append(row)

    # High LIMIT support: clone rows to satisfy min_rows.
    MAX_TOTAL_ROWS = 500
    for _relation, req in spec.requirements.items():
        physical = req.table
        if physical not in completed or not completed[physical]:
            continue
        target = min(req.min_rows, MAX_TOTAL_ROWS)
        current_rows = completed[physical]
        while len(current_rows) < target:
            base_row = current_rows[-1]
            new_row = dict(base_row)
            for col_name in instance.tables.get(physical, {}):
                if instance.is_unique(physical, col_name):
                    context = dict(new_row)
                    context.pop(col_name, None)
                    try:
                        new_row[col_name] = builder.generate_value(
                            physical, col_name, row_context=context,
                        )
                    except Exception:
                        pass
            builder.runtime.remember_row(physical, new_row)
            current_rows.append(new_row)

    for (table, _alias, _scope, _row_idx), row in sorted(pending_rows.items()):
        completed.setdefault(table, []).append(row)
    return completed


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _gold_materialization_order(
    instance: Instance,
    rows: Dict[str, List[Dict[str, Any]]],
) -> List[str]:
    """Topological sort of tables by FK dependencies."""
    requested = [table for table in rows if table in instance.tables]
    requested_set = set(requested)
    ordered: List[str] = []
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(table_name: str) -> None:
        if table_name in visited:
            return
        if table_name in visiting:
            return
        visiting.add(table_name)
        try:
            rel_id = instance.table_id(table_name)
        except KeyError:
            visiting.remove(table_name)
            visited.add(table_name)
            return
        for fk_spec in instance.get_foreign_keys_by_relation_id(rel_id):
            if fk_spec.target_table_id and fk_spec.target_table_id.name:
                ref_table = fk_spec.target_table_id.name.normalized
                if ref_table in requested_set:
                    visit(ref_table)
        visiting.remove(table_name)
        visited.add(table_name)
        ordered.append(table_name)

    for table_name in requested:
        visit(table_name)
    return ordered


def _materialize_rows(
    instance: Instance,
    rows: Dict[str, List[Dict[str, Any]]],
) -> None:
    """Write rows to instance in FK dependency order."""
    for table_name in _gold_materialization_order(instance, rows):
        for row in rows.get(table_name, []):
            instance.create_row(table_name, values=row)


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


def _scalar_subquery_operand_expression(
    subplan: Optional[SubPlan],
) -> Optional[exp.Expression]:
    """Extract the aggregate operand expression from a subplan."""
    if subplan is None or subplan.inner is None:
        return None
    for step in _iter_steps_with_subplans(subplan.inner):
        if not isinstance(step, Aggregate):
            continue
        for operand in getattr(step, "operands", ()) or ():
            if isinstance(operand, exp.Alias):
                return operand.this.copy()
        for agg_expr in step.aggregations:
            agg = agg_expr.this if isinstance(agg_expr, exp.Alias) else agg_expr
            if isinstance(agg, (exp.Avg, exp.Sum, exp.Min, exp.Max)):
                return agg.this.copy()
    return None


def _bindings_for_scalar_expression(
    expression: exp.Expression,
    instance: Instance,
    row_index: int,
) -> Dict[str, RowBinding]:
    """Build row bindings for columns in a scalar expression."""
    bindings: Dict[str, RowBinding] = {}
    for col in expression.find_all(exp.Column):
        raw_table = col.table or ""
        # Column name from sqlglot AST is already usable directly.
        physical = raw_table
        if raw_table not in instance.tables:
            # Search all tables for the column.
            for table_name in instance.tables:
                if col.name in instance.tables.get(table_name, {}):
                    physical = table_name
                    break
        if physical not in instance.tables:
            continue
        relation = instance.table_id(physical)
        binding = RowBinding(relation=relation, row=row_index)
        bindings[_solver_table_key(binding)] = binding
    return bindings


def _merge_scalar_solver_assignments(
    rows: Dict[str, List[Dict[str, Any]]],
    assignments: Dict[SolverVar, Any],
    _row_bindings: Dict[str, RowBinding],
) -> Set[Tuple[str, int]]:
    """Merge solver assignments into rows. Returns set of (table, row_index) touched.

    Only sets values for columns not already present in the row, to avoid
    clobbering values from the primary solver pass.
    """
    touched: Set[Tuple[str, int]] = set()
    for var, value in assignments.items():
        table_name = var.relation_id.name.normalized if var.relation_id.name else ""
        column_name = var.column_id.name.normalized if var.column_id.name else ""
        row_scope = var.row_scope or "r0"
        if not row_scope.startswith("r"):
            continue
        try:
            row_idx = int(row_scope[1:])
        except ValueError:
            continue
        if not table_name or not column_name:
            continue
        table_rows = rows.setdefault(table_name, [])
        while len(table_rows) <= row_idx:
            table_rows.append({})
        row = table_rows[row_idx]
        if column_name not in row:
            row[column_name] = value
            touched.add((table_name, row_idx))
    return touched


def _solve_scalar_witness_values(
    atom: exp.Expression,
    outer_expr: exp.Expression,
    inner_expr: exp.Expression,
    rows: Dict[str, List[Dict[str, Any]]],
    instance: Instance,
    dialect: str,
    inner_row_index: int,
) -> Set[Tuple[str, int]]:
    """Use the solver to find values satisfying a scalar subquery comparison."""
    outer_scoped = outer_expr.copy()
    inner_scoped = inner_expr.copy()

    # Ensure columns have type annotations.
    for expr in (outer_scoped, inner_scoped):
        for col in expr.find_all(exp.Column):
            if getattr(col, "type", None) is not None:
                continue
            table = col.table or ""
            schema = instance.tables.get(table)
            if schema:
                dtype = schema.get(col.name)
                if dtype:
                    try:
                        col.type = DataType.build(dtype)
                    except Exception:
                        pass

    outer_bindings = _bindings_for_scalar_expression(outer_scoped, instance, row_index=0)
    inner_bindings = _bindings_for_scalar_expression(inner_scoped, instance, row_index=inner_row_index)
    all_bindings = {**outer_bindings, **inner_bindings}
    if not all_bindings:
        return set()

    # Rewrite with row scopes.
    for col in outer_scoped.find_all(exp.Column):
        table = col.table or ""
        binding = _find_binding_for_column(table, all_bindings)
        if binding is None:
            # Resolve alias: search for column name in all tables.
            for table_name in instance.tables:
                if col.name in instance.tables.get(table_name, {}):
                    binding = _find_binding_for_column(table_name, all_bindings)
                    break
        if binding is None:
            continue
        new_col = _solver_column(instance, physical_column(col.name, binding.relation), row_scope=f"r{binding.row}")
        set_solver_var(col, solver_var(new_col))
        if hasattr(new_col, "type") and new_col.type is not None:
            col.type = new_col.type

    for col in inner_scoped.find_all(exp.Column):
        table = col.table or ""
        binding = _find_binding_for_column(table, all_bindings)
        if binding is None:
            # Resolve alias: search for column name in all tables.
            for table_name in instance.tables:
                if col.name in instance.tables.get(table_name, {}):
                    binding = _find_binding_for_column(table_name, all_bindings)
                    break
        if binding is None:
            continue
        new_col = _solver_column(instance, physical_column(col.name, binding.relation), row_scope=f"r{binding.row}")
        set_solver_var(col, solver_var(new_col))
        if hasattr(new_col, "type") and new_col.type is not None:
            col.type = new_col.type

    # Build the comparison expression.
    if isinstance(atom.this, exp.Subquery) or (atom.this and atom.this.find(exp.Subquery)):
        constraint_expr = type(atom)(this=inner_scoped, expression=outer_scoped)
    else:
        constraint_expr = type(atom)(this=outer_scoped, expression=inner_scoped)

    # Collect target relations.
    target_relations: Set[RelationId] = set()
    for binding in all_bindings.values():
        target_relations.add(binding.relation)

    result = Solver(dialect=dialect).solve(
        SolverConstraint(
            target_relations=tuple(target_relations),
            constraints=[constraint_expr],
        ),
    )
    if result.sat:
        return _merge_scalar_solver_assignments(rows, result.assignments, all_bindings)
    return set()

def _split_conjuncts_static(expr: exp.Expression) -> list:
    """Split a conjunction into its top-level conjuncts (static version)."""
    parts = []
    if isinstance(expr, exp.And):
        parts.extend(_split_conjuncts_static(expr.left))
        parts.extend(_split_conjuncts_static(expr.right))
    elif isinstance(expr, exp.Paren):
        parts.extend(_split_conjuncts_static(expr.this))
    else:
        parts.append(expr)
    return parts


def _satisfy_gold_scalar_subqueries(
    spec: BranchSpec,
    plan: Plan,
    rows: Dict[str, List[Dict[str, Any]]],
    instance: Instance,
    dialect: str,
) -> None:
    """Handle deferred scalar subquery atoms.

    Uses the evaluator to compute scalar subquery values (which handles
    alias-based contexts correctly), then updates rows to satisfy the
    comparison when possible.
    """
    from parseval.symbolic.branch_tree import decompose_atoms
    from parseval.symbolic.evaluator import PlanEvaluator
    from parseval.plan.rex import concrete as _concrete, Environment, Variable

    seen: Set[str] = set()
    for atom in spec.deferred:
        if not isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            continue
        atom_key = atom.sql(dialect=dialect)
        if atom_key in seen:
            continue
        seen.add(atom_key)

        left, right = atom.this, atom.expression
        if right and right.find(exp.Subquery):
            outer_expr, subquery_expr = left, right
        elif left and left.find(exp.Subquery):
            outer_expr, subquery_expr = right, left
        else:
            continue
        if outer_expr is None or subquery_expr is None:
            continue

        subquery = subquery_expr if isinstance(subquery_expr, exp.Subquery) else subquery_expr.find(exp.Subquery)
        if subquery is None:
            continue

        # Use the evaluator to resolve the scalar subquery.
        # The evaluator handles alias-based contexts correctly (t1 vs t3).
        checkpoint = instance.checkpoint()
        try:
            _materialize_rows(instance, rows)
            evaluator = PlanEvaluator(plan, instance, dialect)
            tree = BranchTree()
            ctx = evaluator.evaluate_context(tree)

            # Find a row in the outer context to evaluate against.
            outer_table = None
            for table_name, table in ctx.tables.items():
                if table.rows:
                    outer_table = table_name
                    break
            if outer_table is None:
                continue

            # Resolve subquery predicates for the first outer row.
            row = ctx.tables[outer_table].rows[0]
            bindings = {}
            for tname, table in ctx.tables.items():
                for col_id in table.columns:
                    val = row.get(col_id)
                    if val is not None:
                        bindings[col_id] = val
            env = Environment(bindings)

            resolved = evaluator._resolve_subquery_predicates(
                atom, ctx.tables[outer_table].rows[0] if ctx.tables.get(outer_table) else (),
                plan.root.subplan_dependencies if hasattr(plan.root, 'subplan_dependencies') else (),
                bindings, env,
            )
            scalar_val = _concrete(resolved, env)
            if scalar_val is not None:
                logger.debug("Scalar subquery evaluated: %s = %s", atom.sql()[:60], scalar_val)
        except Exception as exc:
            logger.debug("Scalar subquery evaluation failed: %s", exc)
        finally:
            instance.rollback(checkpoint)


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

        # Complete missing columns for satisfiable row bindings.
        rows = _complete_gold_rows(rows, row_bindings, spec, self.instance)

        if not rows:
            return {}

        # Satisfy scalar subqueries (post-solve fixup for comparison subqueries).
        _satisfy_gold_scalar_subqueries(
            spec, self.plan, rows, self.instance, self.dialect,
        )

        # Materialize generated witness rows into the shared instance.
        _materialize_rows(self.instance, rows)
        return rows

    def _build_global_constraint(
        self,
        spec: BranchSpec,
    ) -> Tuple[SolverConstraint, Dict[str, RowBinding]]:
        """Build a single SolverConstraint for ALL tables in the spec."""
        row_bindings = _build_gold_row_bindings(spec)
        constraints: List[exp.Expression] = []
        # Shared scoped-variable map: keyed by (table, col_name) -> SolverVar.
        # Populated by annotated constraints (WHERE), reused by unscoped ones
        # (IS NOT NULL from _add_schema_constraints).
        scoped_vars: Dict[Tuple[str, str], SolverVar] = {}

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
                            scoped_vars=scoped_vars,
                        )
                        if rewritten is not None:
                            constraints.append(rewritten)

        for _relation, _req, req_bindings in all_constraint_items:
            for binding in req_bindings:
                constraints.extend(
                    _database_not_null_constraints_for_binding(
                        self.instance,
                        binding,
                        path_constraints=constraints,
                    )
                )
                constraints.extend(
                    _database_check_constraints_for_binding(
                        self.instance,
                        binding,
                        path_constraints=constraints,
                    )
                )

        # Build join equalities.
        join_equalities = _build_join_equalities(spec, row_bindings, self.instance)

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
    logger.info("Generated %d branch specs", len(branch_specs))

    results = []
    for spec in branch_specs:
        if not spec.requirements:
            continue
        rows = resolver.resolve(spec)
        if rows:
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
