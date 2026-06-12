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

from parseval.dtype import DataType
from parseval.helper import normalize_name
from parseval.identity import (
    ColumnId,
    RelationId,
    RelationKind,
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
from parseval.plan.rex import column_meta, concrete, negate_predicate
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




# =============================================================================
# Identity-aware column creation helpers
# =============================================================================


def _relation_for_table(
    instance: Instance,
    name: str,
) -> RelationId:
    """Get RelationId for a table name, creating a synthetic one if needed."""
    normalized = normalize_name(name)
    try:
        return instance.table_id(normalized)
    except (KeyError, Exception):
        return relation_id(RelationKind.TABLE, identifier_name(normalized))


def _solver_column(
    instance: Instance,
    relation: RelationId,
    col_name: str,
    row_scope: Optional[str] = None,
    source_col_name: Optional[str] = None,
) -> exp.Column:
    """Create a Column annotated with SolverVar + type from the instance schema.

    Args:
        source_col_name: Real column name for type lookup when col_name is a
            synthetic alias (e.g., _g0 -> county).
    """
    col_id = physical_column(col_name, relation)
    var = SolverVar(column_id=col_id, relation_id=relation, row_scope=row_scope)
    table_display = relation.display
    col_node = exp.column(col_name, table=table_display)
    set_solver_var(col_node, var)
    # Set type from instance schema, trying source_col_name as fallback.
    table = relation.name.normalized if relation.name else ""
    schema = instance.tables.get(table)
    if schema:
        for lookup_name in (col_name, source_col_name):
            if not lookup_name:
                continue
            dtype = schema.get(normalize_name(lookup_name))
            if dtype:
                try:
                    col_node.type = DataType.build(dtype)
                except Exception:
                    pass
                break
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
        if normalize_name(col.name) != normalize_name(col_name):
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



# =============================================================================
# Propagator: top-down constraint derivation
# =============================================================================


_COMPARISON_NODES = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)


def _planner_alias_replacements(
    steps,
    *,
    include_aggregate_aliases: bool,
):
    """Build a mapping from planner alias names to their source expressions."""
    replacements: Dict[tuple, exp.Expression] = {}
    for step in steps:
        if not isinstance(step, Aggregate):
            continue
        source = normalize_name(step.source or step.name or "")
        if include_aggregate_aliases:
            for operand in getattr(step, "operands", ()) or ():
                if isinstance(operand, exp.Alias):
                    alias = normalize_name(operand.alias_or_name)
                    replacements[(source, alias)] = operand.this.copy()
                    replacements[("", alias)] = operand.this.copy()
            for agg_expr in step.aggregations:
                if isinstance(agg_expr, exp.Alias):
                    alias = normalize_name(agg_expr.alias_or_name)
                    replacements[(source, alias)] = agg_expr.this.copy()
                    replacements[("", alias)] = agg_expr.this.copy()
        for alias, group_expr in step.group.items():
            replacements[(source, normalize_name(alias))] = group_expr.copy()
            replacements[("", normalize_name(alias))] = group_expr.copy()
    return replacements


def _replace_planner_aliases(
    expression: exp.Expression,
    replacements: Dict[tuple, exp.Expression],
) -> exp.Expression:
    """Replace planner-generated alias columns with their base expressions."""
    if not replacements:
        return expression

    def _replace(node):
        if not isinstance(node, exp.Column):
            return node
        table_key = normalize_name(node.table or "")
        col_key = normalize_name(node.name)
        replacement = replacements.get((table_key, col_key)) or replacements.get(
            ("", col_key)
        )
        return replacement.copy() if replacement is not None else node

    return expression.transform(_replace)


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

    def _solver_col(
        self, table: str, col_name: str, row_scope: Optional[str] = None,
        source_col_name: Optional[str] = None,
    ) -> exp.Column:
        """Create a solver column from a table name string."""
        relation = _relation_for_table(self.instance, table)
        return _solver_column(
            self.instance, relation, col_name, row_scope=row_scope,
            source_col_name=source_col_name,
        )

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
        # Use the identity-based RelationId from annotations to ensure
        # it matches the RelationId used by filter/join columns.
        ann = self.plan.annotations.get(id(step))
        if ann and ann.projected_columns:
            relation = ann.projected_columns[0].relation
            table_name = relation.name.normalized if relation.name else ""
            if table_name in self.instance.tables:
                spec.require(relation)
        elif isinstance(step.source, exp.Table):
            name = normalize_name(step.source.name)
            relation = _relation_for_table(self.instance, name)
            table_name = relation.name.normalized if relation.name else ""
            if table_name in self.instance.tables:
                spec.require(relation)
        # For FROM-subquery scans, propagate into the inner plan.
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
        for join_name, join_data in (step.joins or {}).items():
            source_keys = join_data.get("source_key", [])
            join_keys = join_data.get("join_key", [])
            for sk, jk in zip(source_keys, join_keys):
                sk_id = column_identity(sk) if isinstance(sk, exp.Column) else None
                jk_id = column_identity(jk) if isinstance(jk, exp.Column) else None
                if sk_id is None or jk_id is None:
                    raise ValueError(f"Join key lacks identity: sk={sk}, jk={jk}")
                if sk_id and jk_id:
                    sk_rel = sk_id.relation
                    jk_rel = jk_id.relation
                    if sk_rel and jk_rel:
                        spec.require(sk_rel)
                        spec.require(jk_rel)
                        spec.equate(sk_id, jk_id)
                        # Store join equality as expression.
                        sk_table = (
                            sk_rel.name.normalized
                            if sk_rel.name
                            else ""
                        )
                        jk_table = (
                            jk_rel.name.normalized
                            if jk_rel.name
                            else ""
                        )
                        if sk_table and jk_table:
                            eq_expr = exp.EQ(
                                this=self._solver_col(
                                    sk_table,
                                    sk_id.name.normalized,
                                ),
                                expression=self._solver_col(
                                    jk_table,
                                    jk_id.name.normalized,
                                ),
                            )
                            spec.requirements[sk_rel].constraints.append(
                                eq_expr
                            )
                            spec.requirements[jk_rel].constraints.append(
                                eq_expr
                            )
                            # Mark join key as group_key_column.
                            req_jk = spec.require(jk_rel)
                            if jk_id not in req_jk.group_key_columns:
                                req_jk.group_key_columns.append(jk_id)

    def _derive_aggregate(self, step: Aggregate, spec: BranchSpec) -> None:
        """Mark group key columns and add aggregate NULL constraints."""
        # GROUP BY: mark group columns.
        if step.group:
            for group_expr in step.group.values():
                for col in group_expr.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id is None:
                        continue
                    relation = col_id.relation
                    matched = col_id.name.normalized
                    if matched and relation.name:
                        table_name = relation.name.normalized
                        if table_name in self.instance.tables:
                            req = spec.require(relation)
                            spec.equivalences.find(col_id)
                            if col_id not in req.group_key_columns:
                                req.group_key_columns.append(col_id)
        # Aggregate NULL detection.
        if not self._is_gold_mode:
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
                                col_node = self._solver_col(
                                    relation.name.normalized,
                                    matched,
                                )
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
            counted_relation = self._find_counted_table(step.condition)
            min_size = self._extract_min_group_size(step.condition)
            if (
                counted_relation
                and counted_relation in spec.requirements
            ):
                spec.requirements[counted_relation].min_rows = max(
                    spec.requirements[counted_relation].min_rows,
                    min_size,
                )
            else:
                for req in spec.requirements.values():
                    req.min_rows = max(req.min_rows, min_size)
            self._extract_having_value_constraints(
                step.condition, spec, min_size
            )
        elif step is self._negate_step and step.condition:
            # Negate the HAVING condition.
            negated = negate_predicate(step.condition.copy())
            self._store_expression(negated, spec)

    def _derive_project(self, step: Project, spec: BranchSpec) -> None:
        """Add IS NOT NULL for projected columns and handle DISTINCT."""
        projected = self._projected_columns(step)
        # Build col_name -> (source_col_name, ColumnId) mapping from column identity.
        source_names: dict[str, str] = {}
        col_ids: dict[str, ColumnId] = {}
        for proj in step.projections:
            if not isinstance(proj, exp.Expression):
                continue
            for col in proj.find_all(exp.Column):
                cid = column_identity(col)
                if cid is None:
                    continue
                col_ids[cid.name.normalized] = cid
                src = cid.source_column_id or cid
                if src.name.normalized != cid.name.normalized:
                    source_names[cid.name.normalized] = src.name.normalized
        # Route each projected column to its correct table.
        for col_name, table_alias in projected:
            for relation_id, tc in spec.requirements.items():
                norm_alias = normalize_name(table_alias)
                if tc.table != norm_alias and (
                    tc.alias is None
                    or normalize_name(tc.alias) != norm_alias
                ):
                    continue
                if not _has_is_not_null(tc.constraints, col_name):
                    # Use plan identity if available to avoid duplicate SolverVars.
                    plan_cid = col_ids.get(normalize_name(col_name))
                    if plan_cid is not None:
                        col_node = _solver_column(
                            self.instance, relation_id, col_name,
                            source_col_name=plan_cid.source_column_id.name.normalized if plan_cid.source_column_id else None,
                        )
                        sv = solver_var(col_node)
                        if sv is not None:
                            plan_sv = SolverVar(column_id=plan_cid, relation_id=sv.relation_id, row_scope=sv.row_scope)
                            set_solver_var(col_node, plan_sv)
                    else:
                        src = source_names.get(normalize_name(col_name))
                        col_node = self._solver_col(tc.table, col_name, source_col_name=src)
                    tc.constraints.append(_make_is_not_null(col_node))
                break
        # Duplicate / DISTINCT handling.
        for relation_id, tc in spec.requirements.items():
            dup_ids = []
            for col_name, table_alias in projected:
                norm_alias = normalize_name(table_alias)
                if tc.table == norm_alias or (
                    tc.alias is not None
                    and normalize_name(tc.alias) == norm_alias
                ):
                    cid = col_ids.get(normalize_name(col_name))
                    if cid is not None:
                        dup_ids.append(cid)
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
        driving_alias = getattr(step, "source", None)
        if driving_alias:
            resolved = self._resolve_table_alias(driving_alias)
            if resolved is not None and resolved in spec.requirements:
                spec.requirements[resolved].min_rows = max(
                    spec.requirements[resolved].min_rows, needed
                )

    def _resolve_table_alias(self, alias: str) -> RelationId | None:
        """Resolve a table alias to the real RelationId via plan annotations."""
        alias_norm = normalize_name(alias)
        for step in self.plan.ordered_steps:
            if not isinstance(step, Scan):
                continue
            ann = self.plan.annotations.get(id(step))
            if ann and ann.projected_columns:
                rel = ann.projected_columns[0].relation
                # Match by alias or real name
                if rel.alias and normalize_name(rel.alias.normalized) == alias_norm:
                    return rel
                if rel.name and normalize_name(rel.name.normalized) == alias_norm:
                    return rel
            # Also check step.name
            if step.name and normalize_name(step.name) == alias_norm:
                rid = getattr(step, "relation_id", None)
                if rid is not None:
                    return rid
        return None

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
        negated_exists = (
            sub.kind.value == "exists"
            and self._subplan_anchor_is_negated(
                parent_condition, sub.anchor
            )
        )
        if sub.kind.value == "exists" and sub.correlation and not negated_exists:
            for corr_col in sub.correlation:
                corr_id = column_identity(corr_col)
                if corr_id and corr_id.relation:
                    outer_relation = corr_id.relation
                    outer_matched = corr_id.name.normalized
                else:
                    outer_relation = _relation_for_table(
                         self.instance, corr_col.table or ""
                    )
                    outer_matched = corr_col.name
                if outer_matched:
                    spec.require(outer_relation)
                    inner_col_id = self._find_inner_corr_column(sub, spec)
                    if inner_col_id is not None:
                        outer_col_id = corr_id if corr_id is not None else physical_column(
                            outer_matched, outer_relation
                        )
                        spec.equate(outer_col_id, inner_col_id)
                        eq_expr = exp.EQ(
                            this=self._solver_col(
                                outer_relation.name.normalized
                                if outer_relation.name
                                else "",
                                outer_matched,
                            ),
                            expression=self._solver_col(
                                inner_col_id.relation.name.normalized
                                if inner_col_id.relation and inner_col_id.relation.name
                                else "",
                                inner_col_id.name.normalized,
                            ),
                        )
                        spec.requirements[outer_relation].constraints.append(
                            eq_expr
                        )

        elif sub.kind.value == "in":
            self._propagate_in_subplan(sub, spec)

        elif sub.kind.value == "scalar":
            self._propagate_scalar_subplan(sub, spec)

        # Scalar subqueries are repaired from deferred predicates.
        if (
            sub.inner
            and not negated_exists
            and sub.kind.value != "scalar"
        ):
            self._walk_step(sub.inner, spec)

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
                        for join_name in step.joins or {}:
                            right_un = BranchSpec(
                                branch=f"right_unmatched_{join_name}"
                            )
                            self._propagate_unmatched_right(
                                step, join_name, right_un
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
            self._annotate_column_types(spec)
        return specs

    # -----------------------------------------------------------------
    # Expression storage
    # -----------------------------------------------------------------

    def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
        """Decompose AND, resolve columns, store per-table."""
        conjuncts = self._split_conjuncts(expr)
        for conjunct in conjuncts:
            # Subquery-containing conjuncts must be deferred.
            if conjunct.find(exp.Exists) or conjunct.find(exp.Subquery):
                spec.deferred.append(conjunct.copy())
                continue
            resolved = self._resolve_columns(conjunct.copy())
            relation = self._find_table_for_expr(resolved)
            if relation:
                tc = spec.require(relation)
                tc.constraints.append(resolved)

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

    def _find_table_for_expr(
        self, expr: exp.Expression
    ) -> Optional[RelationId]:
        """Find the primary RelationId for an expression."""
        # For EQ with two columns, prefer the left side.
        if isinstance(expr, exp.EQ):
            left = expr.this
            if isinstance(left, exp.Column):
                col_id = column_identity(left)
                if col_id and col_id.relation:
                    tname = (
                        col_id.relation.name.normalized
                        if col_id.relation.name
                        else ""
                    )
                    if tname in self.instance.tables:
                        return col_id.relation
        # Default: first column's relation.
        for col in expr.find_all(exp.Column):
            col_id = column_identity(col)
            if col_id and col_id.relation:
                tname = (
                    col_id.relation.name.normalized
                    if col_id.relation.name
                    else ""
                )
                if tname in self.instance.tables:
                    return col_id.relation
        return None

    def _resolve_columns(self, expr: exp.Expression) -> exp.Expression:
        """Resolve column table qualifiers and ensure SolverVar metadata."""
        for col in expr.find_all(exp.Column):
            if solver_var(col) is None:
                col_id = column_identity(col)
                if col_id is not None and col_id.relation is not None:
                    var = SolverVar(column_id=col_id, relation_id=col_id.relation)
                    set_solver_var(col, var)
            col_id = column_identity(col)
            if col_id and col_id.relation:
                display = col_id.relation.display
                if display and col.table and display != col.table:
                    col.set("table", exp.to_identifier(display))
        return expr

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
                # Also map by source_column_id name for synthetic aliases.
                src = col_id.source_column_id
                if src and src.relation and src.relation.name and col_id.relation and col_id.relation.name:
                    key = (col_id.relation.name.normalized, src.name.normalized)
                    plan_col_ids[key] = col_id

        for relation_id, tc in list(spec.requirements.items()):
            table = tc.table
            if table not in self.instance.tables:
                continue

            # NOT NULL columns.
            for col_name in self.instance.tables[table]:
                plan_cid = plan_col_ids.get((table, normalize_name(col_name)))
                # Update existing IS NOT NULL with plan identity (regardless of nullability).
                if _has_is_not_null(tc.constraints, col_name):
                    if plan_cid is not None:
                        _update_solver_var_identity(tc.constraints, col_name, plan_cid)
                    continue
                col_id = physical_column(col_name, relation_id)
                if not self.instance.nullable(relation_id, col_id):
                    if _has_is_null(tc.constraints, col_name):
                        continue
                    # Add new IS NOT NULL with plan identity.
                    if plan_cid is not None:
                        col_node = _solver_column(
                            self.instance, relation_id, col_name,
                            source_col_name=plan_cid.source_column_id.name.normalized if plan_cid.source_column_id else None,
                        )
                        sv = solver_var(col_node)
                        if sv is not None:
                            plan_sv = SolverVar(column_id=plan_cid, relation_id=sv.relation_id, row_scope=sv.row_scope)
                            set_solver_var(col_node, plan_sv)
                    else:
                        col_node = self._solver_col(table, col_name)
                    tc.constraints.append(_make_is_not_null(col_node))

            # UNIQUE columns with existing data -> exclude existing values.
            existing_rows = self.instance.get_rows(relation_id)
            if existing_rows:
                for col_name in self.instance.tables[table]:
                    col_id = physical_column(col_name, relation_id)
                    if self.instance.is_unique(relation_id, col_id):
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
                                 table, col_name
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
            for fk in self.instance.get_foreign_key(relation_id):
                ref = fk.args.get("reference")
                if not ref:
                    continue
                ref_table_node = ref.find(exp.Table)
                if not ref_table_node:
                    continue
                ref_table = normalize_name(ref_table_node.name)
                fk_cols = [
                    identifier.name for identifier in fk.expressions
                ]
                if not fk_cols:
                    continue
                fk_col = fk_cols[0]
                ref_relation = _relation_for_table(
                     self.instance, ref_table
                )
                parent_rows = self.instance.get_rows(ref_relation)
                if parent_rows:
                    parent_vals: list = []
                    ref_col_name = self.instance.resolve_fk_ref_column(fk)
                    if ref_col_name:
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
                        col_node = self._solver_col(
                             table, fk_col
                        )
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
            rel = _relation_for_table(self.instance, table)
            targets[table] = {
                col
                for col in targets[table]
                if self.instance.nullable(
                    rel, physical_column(col, rel)
                )
            }
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
                 target_table, target_col
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
                     table, col_name
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
        if threshold is None or isinstance(threshold, str):
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

        boundary_val = None
        op_type = type(conjunct)
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

    def _annotate_column_types(self, spec: BranchSpec):
        """Set .type on Column nodes from metadata or instance schema."""
        for _relation_id, tc in spec.requirements.items():
            for constraint in tc.constraints:
                for col in constraint.find_all(exp.Column):
                    if getattr(col, "type", None) is not None:
                        continue
                    meta = column_meta(col)
                    if meta and "domain" in meta:
                        col.type = meta["domain"]
                    else:
                        col_id = column_identity(col)
                        if col_id and col_id.relation:
                            table = _table_name(col_id.relation)
                            schema = self.instance.tables.get(table)
                            if schema:
                                dtype = schema.get(normalize_name(col.name))
                                if dtype:
                                    try:
                                        col.type = DataType.build(dtype)
                                    except Exception:
                                        pass

        # Also annotate columns in deferred atoms.
        for atom in spec.deferred:
            for col in atom.find_all(exp.Column):
                if getattr(col, "type", None) is not None:
                    continue
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col.type = meta["domain"]
                else:
                    col_id = column_identity(col)
                    if col_id and col_id.relation:
                        table = _table_name(col_id.relation)
                        schema = self.instance.tables.get(table)
                        if schema:
                            dtype = schema.get(normalize_name(col.name))
                            if dtype:
                                try:
                                    col.type = DataType.build(dtype)
                                except Exception:
                                    pass

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
                col_node = self._solver_col(
                     table_name, matched
                )
                req.constraints.append(_make_is_null(col_node))
                req.min_rows = max(req.min_rows, 2)

    # -----------------------------------------------------------------
    # Join / SubPlan handling
    # -----------------------------------------------------------------

    def _propagate_unmatched_left(
        self, join_step: Join, spec: BranchSpec
    ):
        """Generate a left-table row with no matching right-table row."""
        source_name = join_step.source_name or join_step.name or ""
        source_relation = _relation_for_table(self.instance, source_name)
        source_table = (
            source_relation.name.normalized
            if source_relation.name
            else ""
        )
        if source_table not in self.instance.tables:
            return
        req = spec.require(source_relation)
        for join_name, join_data in (join_step.joins or {}).items():
            join_relation = _relation_for_table(self.instance, join_name)
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
                        col_node = self._solver_col(
                             source_table, sk_id.name.normalized
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
                        req.constraints.append(not_in)

    def _propagate_unmatched_right(
        self, join_step: Join, join_name: str, spec: BranchSpec
    ):
        """Generate a right-table row with no matching left-table row."""
        join_relation = _relation_for_table(self.instance, join_name)
        join_table = (
            join_relation.name.normalized if join_relation.name else ""
        )
        if join_table not in self.instance.tables:
            return
        req = spec.require(join_relation)
        source_name = join_step.source_name or join_step.name or ""
        source_relation = _relation_for_table(self.instance, source_name)
        join_data = (join_step.joins or {}).get(join_name, {})
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
                    col_node = self._solver_col(
                         join_table, jk_id.name.normalized
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
                    req.constraints.append(not_in)

    def _subplan_anchor_is_negated(
        self,
        predicate: Optional[exp.Expression],
        anchor: Optional[exp.Expression],
    ) -> bool:
        """Return True when a subplan anchor has odd NOT polarity."""
        if predicate is None or anchor is None:
            return False
        negations = 0
        node = anchor.parent
        while node is not None:
            if isinstance(node, exp.Not):
                negations += 1
            if node is predicate:
                return negations % 2 == 1
            node = node.parent
        return False

    def _propagate_in_subplan(self, sub: SubPlan, spec: BranchSpec):
        """Handle IN (SELECT col FROM t WHERE ...)."""
        anchor = sub.anchor
        if not isinstance(anchor, exp.In):
            return
        outer_col = anchor.this
        if not isinstance(outer_col, exp.Column):
            return
        outer_id = column_identity(outer_col)
        if outer_id and outer_id.relation:
            outer_relation = outer_id.relation
            outer_matched = outer_id.name.normalized
        else:
            outer_relation = _relation_for_table(
                 self.instance, outer_col.table or ""
            )
            outer_matched = outer_col.name
        if not outer_matched:
            return
        inner_cid = self._find_inner_select_column(sub, spec)
        if inner_cid is not None:
            spec.require(outer_relation)
            outer_cid = outer_id if outer_id is not None else physical_column(
                outer_matched, outer_relation
            )
            spec.equate(outer_cid, inner_cid)
            eq_expr = exp.EQ(
                this=self._solver_col(
                    outer_relation.name.normalized
                    if outer_relation.name
                    else "",
                    outer_matched,
                ),
                expression=self._solver_col(
                    inner_cid.relation.name.normalized
                    if inner_cid.relation and inner_cid.relation.name
                    else "",
                    inner_cid.name.normalized,
                ),
            )
            spec.requirements[outer_relation].constraints.append(eq_expr)

    def _propagate_scalar_subplan(
        self, sub: SubPlan, spec: BranchSpec
    ):
        """Ensure scalar subquery's inner table has at least one row."""
        stack = [sub.inner]
        visited: set = set()
        while stack:
            step = stack.pop()
            if id(step) in visited:
                continue
            visited.add(id(step))
            if isinstance(step, Scan) and step.source:
                if isinstance(step.source, exp.Table):
                    name = step.source.name
                    rel = _relation_for_table(self.instance, name)
                    tname = rel.name.normalized if rel.name else ""
                    if tname in self.instance.tables:
                        spec.require(rel)
            stack.extend(step.chain_dependencies)

        # Equate correlated columns.
        if sub.correlation:
            for corr_col in sub.correlation:
                corr_id = column_identity(corr_col)
                if corr_id and corr_id.relation:
                    outer_relation = corr_id.relation
                    outer_matched = corr_id.name.normalized
                else:
                    outer_relation = _relation_for_table(
                         self.instance, corr_col.table or ""
                    )
                    outer_matched = corr_col.name
                if not outer_matched:
                    continue
                inner_cid = self._find_corr_inner_column(
                    sub, corr_col.name
                )
                if inner_cid is not None:
                    spec.require(outer_relation)
                    outer_cid = corr_id if corr_id is not None else physical_column(
                        outer_matched, outer_relation
                    )
                    spec.equate(outer_cid, inner_cid)
                    eq_expr = exp.EQ(
                        this=self._solver_col(
                            outer_relation.name.normalized
                            if outer_relation.name
                            else "",
                            outer_matched,
                        ),
                        expression=self._solver_col(
                            inner_cid.relation.name.normalized
                            if inner_cid.relation and inner_cid.relation.name
                            else "",
                            inner_cid.name.normalized,
                        ),
                    )
                    if outer_relation in spec.requirements:
                        spec.requirements[
                            outer_relation
                        ].constraints.append(eq_expr)

    def _find_inner_select_column(
        self, sub: SubPlan, spec: BranchSpec
    ) -> Optional[ColumnId]:
        """Find the inner plan's source column for IN subqueries with full identity.

        Returns ColumnId or None.
        """
        proj_col_id = None
        stack = [sub.inner]
        visited: set = set()
        while stack:
            step = stack.pop()
            if id(step) in visited:
                continue
            visited.add(id(step))
            if isinstance(step, Project) and step.projections:
                proj = step.projections[0]
                if isinstance(proj, exp.Expression):
                    for col in proj.find_all(exp.Column):
                        cid = column_identity(col)
                        if cid is not None:
                            proj_col_id = cid
                            break
            stack.extend(step.chain_dependencies)

        if proj_col_id is None:
            return None

        # Ensure the Scan table is required.
        stack = [sub.inner]
        visited = set()
        while stack:
            step = stack.pop()
            if id(step) in visited:
                continue
            visited.add(id(step))
            if isinstance(step, Scan) and step.source:
                if isinstance(step.source, exp.Table):
                    name = step.source.name
                    rel = _relation_for_table(self.instance, name)
                    tname = rel.name.normalized if rel.name else ""
                    if tname in self.instance.tables:
                        spec.require(rel)
            stack.extend(step.chain_dependencies)
        return proj_col_id

    def _find_inner_corr_column(
        self, sub: SubPlan, spec: BranchSpec
    ) -> Optional[ColumnId]:
        """Find the inner plan's correlated column with full identity.

        Returns ColumnId or None.
        """
        stack = [sub.inner]
        while stack:
            step = stack.pop()
            if isinstance(step, Filter) and step.condition:
                for col in step.condition.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id is None or col_id.relation is None:
                        continue
                    tname = (
                        col_id.relation.name.normalized
                        if col_id.relation.name
                        else ""
                    )
                    if col_id.name.normalized and tname in self.instance.tables:
                        spec.require(col_id.relation)
                        return col_id
            stack.extend(step.chain_dependencies)
        return None

    def _find_corr_inner_column(
        self, sub: SubPlan, col_name: str
    ) -> Optional[ColumnId]:
        """Find the inner plan's column matching col_name with full identity.

        Returns ColumnId or None.
        """
        stack = [sub.inner]
        visited: set = set()
        while stack:
            step = stack.pop()
            if id(step) in visited:
                continue
            visited.add(id(step))
            if isinstance(step, Filter) and step.condition:
                for col in step.condition.find_all(exp.Column):
                    if col.name.lower() == col_name.lower():
                        col_id = column_identity(col)
                        if col_id is None or col_id.relation is None:
                            continue
                        tname = (
                            col_id.relation.name.normalized
                            if col_id.relation.name
                            else ""
                        )
                        if col_id.name.normalized and tname in self.instance.tables:
                            return col_id
            stack.extend(step.chain_dependencies)
        return None

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

    def _extract_agg_and_threshold(self, node: exp.Expression):
        """Return (agg_expr, threshold, op_class) from a comparison node."""
        left_has_agg = node.this.find((exp.Avg, exp.Sum, exp.Count))
        if left_has_agg:
            return node.this, concrete(node.expression), type(node)
        right_has_agg = node.expression.find(
            (exp.Avg, exp.Sum, exp.Count)
        )
        if right_has_agg:
            return node.expression, concrete(node.this), type(node)
        return None, None, None

    def _find_counted_table(
        self, condition: exp.Expression
    ) -> Optional[RelationId]:
        """Find the table containing COUNT(col) in a HAVING comparison."""
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for comp_node in agg_expr.find_all(_COMPARISON_NODES):
                        agg_side, _, _ = self._extract_agg_and_threshold(
                            comp_node
                        )
                        if agg_side is None:
                            continue
                        for count_node in agg_side.find_all(exp.Count):
                            if isinstance(count_node.this, exp.Star):
                                continue
                            if count_node.args.get("distinct"):
                                continue
                            for col in count_node.find_all(exp.Column):
                                col_id = column_identity(col)
                                if col_id and col_id.relation:
                                    tname = (
                                        col_id.relation.name.normalized
                                        if col_id.relation.name
                                        else ""
                                    )
                                    if tname in self.instance.tables:
                                        return col_id.relation
        # Fallback: check the HAVING condition directly.
        for comp_node in condition.find_all(_COMPARISON_NODES):
            agg_side, _, _ = self._extract_agg_and_threshold(comp_node)
            if agg_side is None:
                continue
            for count_node in agg_side.find_all(exp.Count):
                if isinstance(count_node.this, exp.Star):
                    continue
                for col in count_node.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id and col_id.relation:
                        tname = (
                            col_id.relation.name.normalized
                            if col_id.relation.name
                            else ""
                        )
                        if tname in self.instance.tables:
                            return col_id.relation
        return None

    def _extract_min_group_size(
        self, condition: exp.Expression
    ) -> int:
        """Extract minimum group size from HAVING."""
        result = 1
        result = max(result, self._min_group_from_expr(condition))
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    result = max(
                        result, self._min_group_from_expr(agg_expr)
                    )
        return result

    def _min_group_from_expr(self, expr: exp.Expression) -> int:
        """Extract min group size from a single expression."""
        for node in expr.find_all(_COMPARISON_NODES):
            agg_side, threshold, op_class = (
                self._extract_agg_and_threshold(node)
            )
            if agg_side is None or not isinstance(
                threshold, (int, float)
            ):
                continue
            if not self._is_direct_count_expr(agg_side):
                continue
            if op_class is exp.GT:
                return int(threshold) + 1
            if op_class is exp.GTE:
                return int(threshold)
            if op_class is exp.EQ:
                return int(threshold)
        return 1

    @staticmethod
    def _is_direct_count_expr(expr: exp.Expression) -> bool:
        """Return True for COUNT(...) or a simple cast around COUNT."""
        if isinstance(expr, exp.Count):
            return True
        if isinstance(expr, exp.Cast):
            return isinstance(expr.this, exp.Count)
        return False

    def _extract_having_value_constraints(
        self,
        condition: exp.Expression,
        spec: BranchSpec,
        min_rows: int,
    ):
        """Derive per-row value constraints from HAVING aggregate thresholds."""
        self._extract_agg_value_from_expr(condition, spec, min_rows)
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    self._extract_agg_value_from_expr(
                        agg_expr, spec, min_rows
                    )

    def _extract_agg_value_from_expr(
        self,
        expr: exp.Expression,
        spec: BranchSpec,
        min_rows: int,
    ):
        """Extract per-row value constraints from an aggregate comparison."""
        import math

        for node in expr.find_all(_COMPARISON_NODES):
            agg_side, threshold, op_class = (
                self._extract_agg_and_threshold(node)
            )
            if agg_side is None or not isinstance(
                threshold, (int, float)
            ):
                continue
            target_col = None
            per_row_value = None
            if op_class in (exp.GT, exp.GTE):
                offset = 1 if op_class is exp.GT else 0
                if agg_side.find(exp.Avg):
                    target_col = self._find_agg_column(
                        agg_side, exp.Avg
                    )
                    per_row_value = int(threshold) + offset
                elif agg_side.find(exp.Sum) and agg_side.find(exp.Count):
                    target_col = self._find_agg_column(
                        agg_side, exp.Sum
                    )
                    per_row_value = int(threshold) + offset
                elif agg_side.find(exp.Sum):
                    target_col = self._find_agg_column(
                        agg_side, exp.Sum
                    )
                    per_row_value = (
                        int(threshold / max(min_rows, 1)) + offset
                    )
            elif op_class in (exp.LT, exp.LTE):
                if agg_side.find(exp.Avg):
                    target_col = self._find_agg_column(
                        agg_side, exp.Avg
                    )
                    per_row_value = (
                        int(threshold) - 1
                        if op_class is exp.LT
                        else int(threshold)
                    )
                elif agg_side.find(exp.Sum):
                    target_col = self._find_agg_column(
                        agg_side, exp.Sum
                    )
                    per_row_value = 1
            elif op_class is exp.EQ:
                if agg_side.find(exp.Avg):
                    target_col = self._find_agg_column(
                        agg_side, exp.Avg
                    )
                    per_row_value = int(threshold)
                elif agg_side.find(exp.Sum):
                    target_col = self._find_agg_column(
                        agg_side, exp.Sum
                    )
                    per_row_value = math.ceil(
                        threshold / max(min_rows, 1)
                    )

            if target_col and per_row_value is not None:
                col_id = column_identity(target_col)
                if col_id is None or col_id.relation is None:
                    continue
                relation = col_id.relation
                matched = col_id.name.normalized
                tname = relation.name.normalized if relation.name else ""
                if matched and tname and relation in spec.requirements:
                    if not _has_equality_constraint(
                        spec.requirements[relation].constraints, matched
                    ):
                        col_node = self._solver_col(
                             tname, matched
                        )
                        spec.requirements[relation].constraints.append(
                            exp.EQ(
                                this=col_node,
                                expression=to_literal(per_row_value),
                            )
                        )

    def _find_agg_column(
        self, expr: exp.Expression, agg_type
    ) -> Optional[exp.Column]:
        """Find the column inside an aggregate function."""
        for agg in expr.find_all(agg_type):
            for col in agg.find_all(exp.Column):
                return col
        return None

    def _gold_having_scalar_constraints(
        self, condition: exp.Expression
    ) -> List[exp.Expression]:
        """Return non-aggregate HAVING predicates for gold witnesses."""
        source = condition.copy()
        if self._is_synthetic_having_alias(condition):
            source = self._find_having_alias_expression(condition)
            if source is None:
                return []
        source = self._resolve_group_aliases(source)
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
        return normalize_name(condition.name).startswith("_h")

    def _find_having_alias_expression(
        self, condition: exp.Column
    ) -> Optional[exp.Expression]:
        """Find the expression behind a planner-generated HAVING alias."""
        alias = normalize_name(condition.name)
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            for agg_expr in step.aggregations:
                if isinstance(agg_expr, exp.Alias) and normalize_name(
                    agg_expr.alias_or_name
                ) == alias:
                    return agg_expr.this.copy()
        return None

    def _resolve_group_aliases(
        self, expression: exp.Expression
    ) -> exp.Expression:
        """Replace planner group aliases with their base expressions."""
        replacements = _planner_alias_replacements(
            self.plan.ordered_steps,
            include_aggregate_aliases=False,
        )
        return _replace_planner_aliases(expression, replacements)

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
    """Build RowBinding objects for every table x row in the spec."""
    bindings: Dict[str, RowBinding] = {}
    alias_scoped_tables: Set[str] = set()
    for _relation, req in spec.requirements.items():
        if req.alias:
            alias_scoped_tables.add(req.table)

    for _relation, req in spec.requirements.items():
        physical = req.table
        if physical in alias_scoped_tables and not req.alias:
            # For self-join tables, only create bindings for alias-scoped entries.
            # If this entry has no alias and the table is alias-scoped, skip it
            # UNLESS there is no alias-scoped version with this specific alias.
            continue
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
    return [
        binding
        for binding in row_bindings.values()
        if binding.table == target_table
        and (target_alias is None or normalize_name(binding.alias or "") == normalize_name(target_alias))
    ]


def _find_binding_for_column(
    table_name: str,
    row_bindings: Dict[str, RowBinding],
) -> Optional[RowBinding]:
    """Find the first RowBinding for a physical table name."""
    normalized = normalize_name(table_name)
    for binding in row_bindings.values():
        if binding.table == normalized:
            return binding
    return None


def _requirement_for_binding(
    spec: BranchSpec,
    binding: RowBinding,
) -> Optional[TableConstraint]:
    """Find the TableConstraint for a binding."""
    for _relation, req in spec.requirements.items():
        if req.table != binding.table:
            continue
        if req.alias:
            if normalize_name(req.alias) == normalize_name(binding.alias or ""):
                return req
        elif binding.alias == binding.table or binding.alias is None:
            return req
    # Fallback: match by table name only.
    for _relation, req in spec.requirements.items():
        if req.table == binding.table:
            return req
    return None


# ---------------------------------------------------------------------------
# Constraint rewriting for row scoping
# ---------------------------------------------------------------------------


def _rewrite_constraint_for_binding(
    constraint: exp.Expression,
    binding: RowBinding,
    instance: Instance,
    scoped_vars: Optional[Dict[Tuple[str, str], SolverVar]] = None,
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
        sv = solver_var(col)
        if sv is not None:
            if sv.relation_id.name.normalized != binding.table:
                continue
            new_var = SolverVar(
                column_id=sv.column_id,
                relation_id=sv.relation_id,
                row_scope=f"r{binding.row}",
            )
            set_solver_var(col, new_var)
            ct = col_type(col)
            if ct is None:
                table = sv.relation_id.name.normalized if sv.relation_id.name else ""
                schema = instance.tables.get(table)
                if schema:
                    dtype = schema.get(normalize_name(col.name))
                    if dtype:
                        try:
                            col.type = DataType.build(dtype)
                        except Exception:
                            pass
            if scoped_vars is not None:
                scoped_vars[(binding.table, normalize_name(col.name))] = new_var
            matched = True
        else:
            col_table = normalize_name(col.table or "")
            if col_table and col_table != binding.table:
                if binding.alias and col_table != normalize_name(binding.alias):
                    continue
                elif not binding.alias:
                    continue
            # Reuse scoped variable if available.
            key = (binding.table, normalize_name(col.name))
            existing = scoped_vars.get(key) if scoped_vars is not None else None
            if existing is not None:
                set_solver_var(col, existing)
                ct = col_type(col)
                if ct is None:
                    table = existing.relation_id.name.normalized if existing.relation_id.name else ""
                    schema = instance.tables.get(table)
                    if schema:
                        dtype = schema.get(normalize_name(col.name))
                        if dtype:
                            try:
                                col.type = DataType.build(dtype)
                            except Exception:
                                pass
            else:
                new_col = _solver_column(
                    instance, binding.relation, col.name,
                    row_scope=f"r{binding.row}",
                )
                set_solver_var(col, solver_var(new_col))
                if hasattr(new_col, "type") and new_col.type is not None:
                    col.type = new_col.type
            matched = True
    return rewritten if matched else None


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
        dtype = schema.get(normalize_name(var.column_id.name.normalized))
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
            pair_key = (
                min(left_sv.display, right_sv.display),
                max(left_sv.display, right_sv.display),
            )
            if pair_key in seen:
                continue
            seen.add(pair_key)
            equalities.append((left_sv, right_sv))

    return equalities


# ---------------------------------------------------------------------------
# Row extraction from solver results
# ---------------------------------------------------------------------------


def _rows_from_solver_result(
    assignments: Dict[SolverVar, Any],
    _row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    """Extract concrete rows from solver assignments.

    Groups by (table, row_index). Maps SolverVar fields to table/row/column.
    Skips boundary rows (row_idx >= 1000).
    """
    rows_by_slot: Dict[Tuple[str, int], Dict[str, Any]] = {}

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

        slot = (table_name, row_idx)
        rows_by_slot.setdefault(slot, {})[column_name] = value

    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (table, _row_idx), values in sorted(rows_by_slot.items()):
        rows.setdefault(table, []).append(values)
    return rows


# ---------------------------------------------------------------------------
# Fallback row generation
# ---------------------------------------------------------------------------


def _gold_domain_value(
    instance: Instance,
    table: str,
    column: str,
    row_context: Optional[Dict[str, Any]] = None,
    rows: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    builder=None,
) -> Any:
    """Generate a single value for a column, respecting constraints."""
    context = dict(row_context or {})
    context.pop(column, None)
    if builder is None:
        if rows:
            from parseval.domain.builder import DatabaseBuilder

            builder = DatabaseBuilder(instance.schema_spec)
            for table_name in instance.tables:
                for existing_row in instance.get_rows(table_name):
                    builder.runtime.remember_row(
                        table_name, _row_value_dict(existing_row),
                    )
            for tbl_name, tbl_rows in rows.items():
                if tbl_name not in instance.tables:
                    continue
                for row in tbl_rows:
                    if row:
                        builder.runtime.remember_row(tbl_name, row)
        else:
            builder = instance.builder
    return builder.generate_value(table, column, row_context=context)


def _fallback_rows(
    spec: BranchSpec,
    instance: Instance,
    _row_bindings: Dict[str, RowBinding],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build rows using heuristic values when solver fails.

    Extracts fixed values from EQ constraints and generates defaults
    for remaining columns.
    """
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for _relation, req in spec.requirements.items():
        physical = req.table
        if physical not in instance.tables:
            continue
        fixed = _extract_fixed_values(req.constraints)
        for _row_index in range(max(req.min_rows, 1)):
            row: Dict[str, Any] = dict(fixed)
            for col_name in instance.tables[physical]:
                if col_name in row:
                    continue
                try:
                    row[col_name] = _gold_domain_value(
                        instance, physical, col_name,
                        row_context=row, rows=rows,
                    )
                except Exception:
                    pass
            rows.setdefault(physical, []).append(row)
    return rows


# ---------------------------------------------------------------------------
# Row completion
# ---------------------------------------------------------------------------


def _gold_fk_columns(instance: Instance, table: str) -> Set[str]:
    """Get FK column names for a table."""
    columns: Set[str] = set()
    for fk in instance.get_foreign_key(table):
        for identifier in fk.expressions:
            columns.add(identifier.name)
    return columns


def _complete_gold_rows(
    rows: Dict[str, List[Dict[str, Any]]],
    row_bindings: Dict[str, RowBinding],
    spec: BranchSpec,
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    """Fill missing columns using instance.builder.generate_value."""
    builder = type(instance.builder)(instance.schema_spec)
    for table_name in instance.tables:
        for existing_row in instance.get_rows(table_name):
            builder.runtime.remember_row(table_name, _row_value_dict(existing_row))

    pending_rows = {
        table: [dict(row) for row in table_rows]
        for table, table_rows in rows.items()
    }
    completed: Dict[str, List[Dict[str, Any]]] = {}
    group_values: Dict[Tuple[str, str], Any] = {}
    unique_values: Dict[Tuple[str, str], Set[Any]] = {}

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
        key=lambda b: (b.table, normalize_name(b.alias or ""), b.row),
    )
    for binding in ordered_bindings:
        table_rows = pending_rows.setdefault(binding.table, [])
        row = table_rows.pop(0) if table_rows else {}
        req = _requirement_for_binding(spec, binding)

        if req is not None:
            fk_columns = _gold_fk_columns(instance, binding.table)

            # Apply group_key_columns.
            for cid in req.group_key_columns:
                col_name = cid.name.normalized
                key = (binding.table, col_name)
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

    for table, table_rows in pending_rows.items():
        completed.setdefault(table, []).extend(table_rows)
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
        for fk in instance.get_foreign_key(table_name):
            ref = fk.args.get("reference")
            if ref is None:
                continue
            ref_table_node = ref.find(exp.Table)
            if ref_table_node is None:
                continue
            ref_table = normalize_name(ref_table_node.name)
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


def _find_subplan_for_subquery(
    plan: Plan,
    subquery: exp.Subquery,
    dialect: str,
) -> Optional[SubPlan]:
    """Find SubPlan for a subquery anchor."""
    target_sql = subquery.sql(dialect=dialect)
    for step in _iter_all_plan_steps(plan):
        if isinstance(step, SubPlan) and step.anchor is not None:
            if step.anchor is subquery or step.anchor.sql(dialect=dialect) == target_sql:
                return step
    return None


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
        raw_table = normalize_name(col.table or "")
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
        relation = _relation_for_table(instance, physical)
        binding = RowBinding(relation=relation, row=row_index)
        bindings[_solver_table_key(binding)] = binding
    return bindings


def _merge_scalar_solver_assignments(
    rows: Dict[str, List[Dict[str, Any]]],
    assignments: Dict[SolverVar, Any],
    _row_bindings: Dict[str, RowBinding],
) -> Set[Tuple[str, int]]:
    """Merge solver assignments into rows. Returns set of (table, row_index) touched."""
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
        table_rows[row_idx][column_name] = value
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
            table = normalize_name(col.table or "")
            schema = instance.tables.get(table)
            if schema:
                dtype = schema.get(normalize_name(col.name))
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
        table = normalize_name(col.table or "")
        binding = _find_binding_for_column(table, all_bindings)
        if binding is None:
            continue
        new_col = _solver_column(instance, binding.relation, col.name, row_scope=f"r{binding.row}")
        set_solver_var(col, solver_var(new_col))
        if hasattr(new_col, "type") and new_col.type is not None:
            col.type = new_col.type

    for col in inner_scoped.find_all(exp.Column):
        table = normalize_name(col.table or "")
        binding = _find_binding_for_column(table, all_bindings)
        if binding is None:
            continue
        new_col = _solver_column(instance, binding.relation, col.name, row_scope=f"r{binding.row}")
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


def _satisfy_gold_scalar_subqueries(
    spec: BranchSpec,
    plan: Plan,
    rows: Dict[str, List[Dict[str, Any]]],
    instance: Instance,
    dialect: str,
) -> None:
    """Handle deferred scalar subquery atoms."""
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
        subplan = _find_subplan_for_subquery(plan, subquery, dialect)
        inner_expr = _scalar_subquery_operand_expression(subplan)
        if inner_expr is None:
            continue

        inner_row_index = 1
        # Check if inner_expr has rows at index 1, otherwise use 0.
        inner_table = None
        for col in inner_expr.find_all(exp.Column):
            t = normalize_name(col.table or "")
            if t in instance.tables:
                inner_table = t
                break
        if inner_table and (inner_table not in rows or len(rows[inner_table]) <= 1):
            inner_row_index = 0

        _solve_scalar_witness_values(
            atom, outer_expr, inner_expr,
            rows, instance, dialect, inner_row_index,
        )


# ---------------------------------------------------------------------------
# Evaluation validation
# ---------------------------------------------------------------------------


def _gold_has_positive_evaluator_observations(tree: BranchTree) -> bool:
    """Check if evaluator observations support a positive witness."""
    has_filter_nodes = False
    for node in tree.nodes:
        if node.site in {"filter", "join_on", "having", "case_arm"}:
            has_filter_nodes = True
            all_true = True
            for atom_id, _atom in enumerate(node.atoms):
                if BranchType.ATOM_TRUE not in node.observed_outcomes(atom_id):
                    all_true = False
                    break
            if all_true:
                return True
        elif node.site == "group":
            has_filter_nodes = True
            if node.observed_outcomes(0):
                return True
    return not has_filter_nodes


def _gold_candidate_has_output(
    plan: Plan,
    instance: Instance,
    rows_per_table: Dict[str, List[Dict[str, Any]]],
    dialect: str = "sqlite",
) -> bool:
    """Verify rows produce evaluator-observable output."""
    checkpoint = instance.checkpoint() if rows_per_table else None
    try:
        if rows_per_table:
            _materialize_rows(instance, rows_per_table)
        tree = BranchTree()
        ctx = PlanEvaluator(plan, instance, dialect).evaluate_context(tree)
        if any(table.rows for table in ctx.tables.values()):
            return True
        if _gold_has_positive_evaluator_observations(tree):
            return True
        return False
    except Exception as exc:
        logger.debug("gold_non_empty validation failed: %s", exc)
        return False
    finally:
        if checkpoint is not None:
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

        # Solve.
        if self.solver is not None:
            result = self.solver.solve(constraint)
            if result.sat:
                rows = _rows_from_solver_result(result.assignments, row_bindings, self.instance)
            else:
                logger.debug(
                    "Solver failed for spec=%s reason=%s; using fallback",
                    spec.branch, result.reason,
                )
                rows = _fallback_rows(spec, self.instance, row_bindings)
        else:
            rows = _fallback_rows(spec, self.instance, row_bindings)

        if not rows:
            return {}

        # Complete missing columns.
        rows = _complete_gold_rows(rows, row_bindings, spec, self.instance)

        # Satisfy scalar subqueries.
        _satisfy_gold_scalar_subqueries(
            spec, self.plan, rows, self.instance, self.dialect,
        )

        # Materialize and validate.
        checkpoint = self.instance.checkpoint()
        try:
            _materialize_rows(self.instance, rows)
            if _gold_candidate_has_output(
                self.plan, self.instance, {}, dialect=self.dialect,
            ):
                return rows
            self.instance.rollback(checkpoint)
            return {}
        except Exception:
            self.instance.rollback(checkpoint)
            return {}

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
                    var.relation_id,
                    var.column_id.name.normalized,
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
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve.

    Returns one entry per branch (positive + negatives).
    Each entry is (branch_name, rows_per_table).

    Args:
        plan: The query plan to generate data for.
        instance: The database instance to materialize rows into.
        dialect: SQL dialect (default: "sqlite").
        config: SpeculateConfig controlling which branches to generate.
            If None, uses SpeculateConfig.gold_non_empty().

    Returns:
        List of (branch_name, rows_per_table) tuples.
    """
    if config is None:
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
        try:
            rows = resolver.resolve(spec)
        except Exception as exc:
            logger.debug("spec %s failed: %s", spec.branch, exc)
            rows = {}
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
