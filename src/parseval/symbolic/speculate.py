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
    annotation (via ``_solver_column`` or ``_ensure_solver_var``).
    """

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

    # -----------------------------------------------------------------
    # Top-level propagation
    # -----------------------------------------------------------------

    def propagate(self) -> List[BranchSpec]:
        """Produce specs for branches based on config thresholds."""
        _ = self.plan.annotations
        specs: List[BranchSpec] = []

        # Positive path.
        if self.config.positive > 0:
            try:
                pos = BranchSpec(branch="positive")
                self._propagate_step(self.plan.root, pos)
                if self.config.boundary > 0:
                    self._collect_boundary_values(pos)
                self._add_schema_constraints(pos)
                self._annotate_column_types(pos)
                specs.append(pos)
            except Exception as exc:
                logger.debug("positive spec propagation failed: %s", exc)
                pos = BranchSpec(branch="positive")
        else:
            pos = BranchSpec(branch="positive")

        # Negative branches per decision site.
        if self.config.negative > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Filter) and step.condition:
                        conjuncts = self._split_conjuncts(step.condition)
                        for idx in range(len(conjuncts)):
                            neg = BranchSpec(branch=f"negative_c{idx}")
                            self._propagate_step(
                                self.plan.root,
                                neg,
                                negate_step=step,
                                negate_conjunct=idx,
                            )
                            self._add_schema_constraints(neg)
                            self._annotate_column_types(neg)
                            specs.append(neg)
                except Exception as exc:
                    logger.debug(
                        "negative spec propagation failed for step %s: %s",
                        type(step).__name__,
                        exc,
                    )

        # Unmatched join branches.
        if self.config.left_unmatched > 0 or self.config.right_unmatched > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Join):
                        if self.config.left_unmatched > 0:
                            left_un = BranchSpec(branch="left_unmatched")
                            self._propagate_unmatched_left(step, left_un)
                            self._add_schema_constraints(left_un)
                            self._annotate_column_types(left_un)
                            specs.append(left_un)
                        if self.config.right_unmatched > 0:
                            for join_name in step.joins or {}:
                                right_un = BranchSpec(
                                    branch=f"right_unmatched_{join_name}"
                                )
                                self._propagate_unmatched_right(
                                    step, join_name, right_un
                                )
                                self._add_schema_constraints(right_un)
                                self._annotate_column_types(right_un)
                                specs.append(right_un)
                except Exception as exc:
                    logger.debug(
                        "unmatched join propagation failed: %s", exc
                    )

        # Having fail branches.
        if self.config.having_fail > 0:
            for step in self.plan.ordered_steps:
                try:
                    if isinstance(step, Having) and step.condition:
                        fail = BranchSpec(branch="having_fail")
                        self._propagate_step(
                            self.plan.root, fail, negate_step=step
                        )
                        self._add_schema_constraints(fail)
                        self._annotate_column_types(fail)
                        specs.append(fail)
                except Exception as exc:
                    logger.debug("having_fail propagation failed: %s", exc)

        # Null branches.
        if self.config.null > 0:
            try:
                null_targets = self._collect_null_target_columns(pos)
                if null_targets:
                    for table, cols in null_targets.items():
                        for col_name in cols:
                            null_spec = BranchSpec(
                                branch=f"null_{table}.{col_name}"
                            )
                            self._propagate_step(self.plan.root, null_spec)
                            self._apply_single_null_override(
                                null_spec, table, col_name
                            )
                            self._add_schema_constraints(null_spec)
                            self._annotate_column_types(null_spec)
                            specs.append(null_spec)
                else:
                    null_spec = BranchSpec(branch="null_branch")
                    self._propagate_step(self.plan.root, null_spec)
                    self._apply_null_overrides(null_spec)
                    self._add_schema_constraints(null_spec)
                    self._annotate_column_types(null_spec)
                    specs.append(null_spec)
            except Exception as exc:
                logger.debug("null branch propagation failed: %s", exc)

        # CASE WHEN branches.
        if self.config.case_else > 0:
            try:
                for case_idx, when_conditions in enumerate(
                    self._collect_case_when_conditions()
                ):
                    case_spec = BranchSpec(branch=f"case_else_{case_idx}")
                    self._propagate_step(self.plan.root, case_spec)
                    for cond in when_conditions:
                        negated = negate_predicate(cond.copy())
                        self._store_expression(negated, case_spec)
                    self._add_schema_constraints(case_spec)
                    self._annotate_column_types(case_spec)
                    specs.append(case_spec)
            except Exception as exc:
                logger.debug("CASE WHEN propagation failed: %s", exc)

        return specs

    # -----------------------------------------------------------------
    # Recursive step propagation
    # -----------------------------------------------------------------

    def _propagate_step(
        self,
        step: Step,
        spec: BranchSpec,
        negate_step: Optional[Step] = None,
        negate_conjunct: int = 0,
    ):
        """Recursively propagate requirements top-down."""
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            if self._is_gold_mode:
                needed = offset + 1 if int(limit_val) > 0 else 0
            else:
                needed = offset + int(limit_val)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Apply min_rows to the driving table.
            driving_alias = getattr(step, "source", None)
            if driving_alias:
                driving_relation = _relation_for_table(
                    self.instance, driving_alias
                )
                if driving_relation in spec.requirements:
                    spec.requirements[driving_relation].min_rows = max(
                        spec.requirements[driving_relation].min_rows, needed
                    )
                else:
                    spec.requirements[driving_relation] = TableConstraint(
                        relation=driving_relation, min_rows=needed
                    )

        elif isinstance(step, Project):
            projected = self._projected_columns(step)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Add IS NOT NULL for projected columns.
            for relation_id, tc in spec.requirements.items():
                for col_name in projected:
                    matched = _match_column(
                        self.instance, relation_id, col_name
                    )
                    if matched and not _has_is_not_null(
                        tc.constraints, matched
                    ):
                        col_node = _solver_column(
                            self.instance, tc.table, matched
                        )
                        tc.constraints.append(_make_is_not_null(col_node))
                dup_ids = []
                for col_name in projected:
                    matched = _match_column(
                        self.instance, relation_id, col_name
                    )
                    if matched:
                        dup_ids.append(
                            physical_column(matched, relation_id)
                        )
                if step.distinct and dup_ids:
                    tc.duplicate_columns = dup_ids
                    tc.min_rows = max(tc.min_rows, 2)
                    # Propagate min_rows to joined tables.
                    for rep, members in spec.equivalences.groups().items():
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

        elif isinstance(step, Sort):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.condition and step is not negate_step:
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
            elif step is negate_step and step.condition:
                # Negate the HAVING condition.
                negated = negate_predicate(step.condition.copy())
                self._store_expression(negated, spec)

        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # GROUP BY: mark group columns.
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        col_id = column_identity(col)
                        if col_id and col_id.relation:
                            relation = col_id.relation
                            matched = col_id.name.normalized
                        else:
                            relation = _relation_for_table(
                                self.instance, col.table or ""
                            )
                            matched = _match_column(
                                self.instance, relation, col.name
                            )
                        if matched and relation.name:
                            table_name = relation.name.normalized
                            if table_name in self.instance.tables:
                                req = spec.require(relation)
                                gid = physical_column(matched, relation)
                                spec.equivalences.find(gid)
                                if gid not in req.group_key_columns:
                                    req.group_key_columns.append(gid)
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
                            if col_id and col_id.relation:
                                relation = col_id.relation
                                matched = col_id.name.normalized
                            else:
                                relation = _relation_for_table(
                                    self.instance, col.table or ""
                                )
                                matched = _match_column(
                                    self.instance, relation, col.name
                                )
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
                                    col_node = _solver_column(
                                        self.instance,
                                        relation.name.normalized,
                                        matched,
                                    )
                                    req.constraints.append(
                                        _make_is_not_null(col_node)
                                    )

        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.condition:
                if step is negate_step:
                    conjuncts = self._split_conjuncts(step.condition)
                    if len(conjuncts) > 1:
                        for idx, conjunct in enumerate(conjuncts):
                            if idx == negate_conjunct:
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
                for atom in self._iter_scalar_subquery_atoms(
                    step.condition
                ):
                    spec.deferred.append(atom)

        elif isinstance(step, Join):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Link join keys via equivalences.
            for join_name, join_data in (step.joins or {}).items():
                source_keys = join_data.get("source_key", [])
                join_keys = join_data.get("join_key", [])
                for sk, jk in zip(source_keys, join_keys):
                    sk_id = column_identity(sk) if isinstance(sk, exp.Column) else None
                    jk_id = column_identity(jk) if isinstance(jk, exp.Column) else None
                    # Fallback for columns without identity metadata.
                    if sk_id is None:
                        sk_table = getattr(sk, "table", None) or (
                            step.source_name or step.name or ""
                        )
                        sk_relation = _relation_for_table(
                            self.instance, sk_table
                        )
                        sk_name = getattr(sk, "name", str(sk))
                        sk_matched = _match_column(
                            self.instance, sk_relation, sk_name
                        )
                        if sk_matched:
                            sk_id = physical_column(sk_matched, sk_relation)
                    if jk_id is None:
                        jk_relation = _relation_for_table(
                            self.instance, join_name
                        )
                        jk_name = getattr(jk, "name", str(jk))
                        jk_matched = _match_column(
                            self.instance, jk_relation, jk_name
                        )
                        if jk_matched:
                            jk_id = physical_column(jk_matched, jk_relation)
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
                                    this=_solver_column(
                                        self.instance,
                                        sk_table,
                                        sk_id.name.normalized,
                                    ),
                                    expression=_solver_column(
                                        self.instance,
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

        elif isinstance(step, SetOperation):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)

        elif isinstance(step, Scan):
            name = step.name or ""
            relation = _relation_for_table(self.instance, name)
            table_name = relation.name.normalized if relation.name else ""
            if table_name in self.instance.tables:
                spec.require(relation)
            # For FROM-subquery scans, propagate into the inner plan.
            for sub in step.subplan_dependencies:
                if sub.inner:
                    self._propagate_step(
                        sub.inner, spec, negate_step, negate_conjunct
                    )

        # Handle SubPlan dependencies.
        for sub in step.subplan_dependencies:
            self._propagate_subplan(
                sub,
                spec,
                parent_condition=getattr(step, "condition", None),
            )

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
                if left.table:
                    rel = _relation_for_table(self.instance, left.table)
                    tname = rel.name.normalized if rel.name else ""
                    if tname in self.instance.tables:
                        return rel
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
            if col.table:
                rel = _relation_for_table(self.instance, col.table)
                tname = rel.name.normalized if rel.name else ""
                if tname in self.instance.tables:
                    return rel
        return None

    def _resolve_columns(self, expr: exp.Expression) -> exp.Expression:
        """Resolve column table qualifiers and ensure SolverVar metadata."""
        for col in expr.find_all(exp.Column):
            _ensure_solver_var(col, self.instance)
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
        for relation_id, tc in list(spec.requirements.items()):
            table = tc.table
            if table not in self.instance.tables:
                continue

            # NOT NULL columns.
            for col_name in self.instance.tables[table]:
                col_id = physical_column(col_name, relation_id)
                if not self.instance.nullable(relation_id, col_id):
                    if _has_is_null(tc.constraints, col_name):
                        continue
                    if not _has_is_not_null(tc.constraints, col_name):
                        col_node = _solver_column(
                            self.instance, table, col_name
                        )
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
                            col_node = _solver_column(
                                self.instance, table, col_name
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
                        col_node = _solver_column(
                            self.instance, table, fk_col
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
        for relation_id, tc in spec.requirements.items():
            for constraint in tc.constraints:
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(
                        right.this, exp.Null
                    ):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            col_id = column_identity(col)
                            if col_id and col_id.relation:
                                tname = (
                                    col_id.relation.name.normalized
                                    if col_id.relation.name
                                    else ""
                                )
                                matched = col_id.name.normalized
                            else:
                                tname = tc.table
                                matched = _match_column(
                                    self.instance,
                                    relation_id,
                                    col.name,
                                )
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
                            if col_id and col_id.relation:
                                tname = (
                                    col_id.relation.name.normalized
                                    if col_id.relation.name
                                    else ""
                                )
                                matched = col_id.name.normalized
                            else:
                                tname = col.table or ""
                                rel = _relation_for_table(
                                    self.instance, tname
                                )
                                tname = (
                                    rel.name.normalized
                                    if rel.name
                                    else ""
                                )
                                matched = _match_column(
                                    self.instance,
                                    _relation_for_table(
                                        self.instance, tname
                                    ),
                                    col.name,
                                )
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
                        if col_id and col_id.relation:
                            tname = (
                                col_id.relation.name.normalized
                                if col_id.relation.name
                                else ""
                            )
                            matched = col_id.name.normalized
                        else:
                            tname = col.table or ""
                            rel = _relation_for_table(
                                self.instance, tname
                            )
                            tname = (
                                rel.name.normalized if rel.name else ""
                            )
                            matched = _match_column(
                                self.instance,
                                _relation_for_table(self.instance, tname),
                                col.name,
                            )
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
        for relation_id, tc in spec.requirements.items():
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
                                matched = _match_column(
                                    self.instance, relation_id, col.name
                                )
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
                                matched = _match_column(
                                    self.instance, relation_id, col.name
                                )
                            if matched == target_col:
                                remove = True
                if not remove:
                    new_constraints.append(constraint)
            tc.constraints = new_constraints

            # Add IS NULL for the target column.
            col_node = _solver_column(
                self.instance, target_table, target_col
            )
            tc.constraints.append(_make_is_null(col_node))

    def _apply_null_overrides(self, spec: BranchSpec):
        """Replace IS NOT NULL with IS NULL for all target columns."""
        targets = self._collect_null_target_columns(spec)
        if not targets:
            return

        for relation_id, tc in spec.requirements.items():
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
                                matched = _match_column(
                                    self.instance, relation_id, col.name
                                )
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
                                matched = _match_column(
                                    self.instance, relation_id, col.name
                                )
                            if matched and matched in targets.get(
                                table, set()
                            ):
                                remove = True
                if not remove:
                    new_constraints.append(constraint)
            tc.constraints = new_constraints

            for col_name in targets[table]:
                col_node = _solver_column(
                    self.instance, table, col_name
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
        if col_id and col_id.relation:
            relation = col_id.relation
            matched = col_id.name.normalized
        else:
            relation = _relation_for_table(
                self.instance, col_node.table or ""
            )
            matched = _match_column(
                self.instance, relation, col_node.name
            )
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
            col_id_b = physical_column(matched, relation)
            tc.boundary_rows.append({col_id_b: boundary_val})

    # -----------------------------------------------------------------
    # Column type annotation
    # -----------------------------------------------------------------

    def _annotate_column_types(self, spec: BranchSpec):
        """Set .type on Column nodes from metadata or instance schema."""
        for relation_id, tc in spec.requirements.items():
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
                            col_relation = col_id.relation
                        else:
                            col_relation = _relation_for_table(
                                self.instance, col.table or ""
                            )
                        _annotate_col_type(
                            col, self.instance, col_relation, col.name
                        )

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
                        col_relation = col_id.relation
                    else:
                        col_relation = _relation_for_table(
                            self.instance, col.table or ""
                        )
                    _annotate_col_type(
                        col, self.instance, col_relation, col.name
                    )

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
        if col_id and col_id.relation:
            relation = col_id.relation
            matched = col_id.name.normalized
        else:
            relation = _relation_for_table(
                self.instance, col.table or ""
            )
            matched = _match_column(self.instance, relation, col.name)
        table_name = relation.name.normalized if relation.name else ""
        if matched and table_name in self.instance.tables:
            req = spec.require(relation)
            if not _has_equality_constraint(req.constraints, matched):
                col_node = _solver_column(
                    self.instance, table_name, matched
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
                    sk_name = getattr(sk, "name", str(sk))
                    sk_matched = _match_column(
                        self.instance, source_relation, sk_name
                    )
                    if sk_matched:
                        sk_id = physical_column(sk_matched, source_relation)
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
                        col_node = _solver_column(
                            self.instance, source_table, sk_id.name.normalized
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
                jk_name = getattr(jk, "name", str(jk))
                jk_matched = _match_column(
                    self.instance, join_relation, jk_name
                )
                if jk_matched:
                    jk_id = physical_column(jk_matched, join_relation)
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
                    col_node = _solver_column(
                        self.instance, join_table, jk_id.name.normalized
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

    def _propagate_subplan(
        self,
        sub: SubPlan,
        spec: BranchSpec,
        parent_condition: Optional[exp.Expression] = None,
    ):
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
                    outer_matched = _match_column(
                        self.instance, outer_relation, corr_col.name
                    )
                if outer_matched:
                    spec.require(outer_relation)
                    inner_key = self._find_inner_corr_column(sub, spec)
                    if inner_key:
                        inner_relation, inner_matched = inner_key
                        outer_col_id = physical_column(
                            outer_matched, outer_relation
                        )
                        inner_col_id = physical_column(
                            inner_matched, inner_relation
                        )
                        spec.equate(outer_col_id, inner_col_id)
                        eq_expr = exp.EQ(
                            this=_solver_column(
                                self.instance,
                                outer_relation.name.normalized
                                if outer_relation.name
                                else "",
                                outer_matched,
                            ),
                            expression=_solver_column(
                                self.instance,
                                inner_relation.name.normalized
                                if inner_relation.name
                                else "",
                                inner_matched,
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
            self._propagate_step(sub.inner, spec)

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
            outer_matched = _match_column(
                self.instance, outer_relation, outer_col.name
            )
        if not outer_matched:
            return
        inner_key = self._find_inner_select_column(sub, spec)
        if inner_key:
            inner_relation, inner_matched = inner_key
            spec.require(outer_relation)
            outer_cid = physical_column(outer_matched, outer_relation)
            inner_cid = physical_column(inner_matched, inner_relation)
            spec.equate(outer_cid, inner_cid)
            eq_expr = exp.EQ(
                this=_solver_column(
                    self.instance,
                    outer_relation.name.normalized
                    if outer_relation.name
                    else "",
                    outer_matched,
                ),
                expression=_solver_column(
                    self.instance,
                    inner_relation.name.normalized
                    if inner_relation.name
                    else "",
                    inner_matched,
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
                    outer_matched = _match_column(
                        self.instance, outer_relation, corr_col.name
                    )
                if not outer_matched:
                    continue
                inner_key = self._find_corr_inner_column(
                    sub, corr_col.name
                )
                if inner_key:
                    inner_relation, inner_matched = inner_key
                    spec.require(outer_relation)
                    outer_cid = physical_column(
                        outer_matched, outer_relation
                    )
                    inner_cid = physical_column(
                        inner_matched, inner_relation
                    )
                    spec.equate(outer_cid, inner_cid)
                    eq_expr = exp.EQ(
                        this=_solver_column(
                            self.instance,
                            outer_relation.name.normalized
                            if outer_relation.name
                            else "",
                            outer_matched,
                        ),
                        expression=_solver_column(
                            self.instance,
                            inner_relation.name.normalized
                            if inner_relation.name
                            else "",
                            inner_matched,
                        ),
                    )
                    if outer_relation in spec.requirements:
                        spec.requirements[
                            outer_relation
                        ].constraints.append(eq_expr)

    def _find_inner_select_column(
        self, sub: SubPlan, spec: BranchSpec
    ) -> Optional[tuple]:
        """Find the inner plan's source column for IN subqueries.

        Returns (RelationId, matched_col_name) or None.
        """
        proj_col_name = None
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
                        proj_col_name = col.name
                        break
            stack.extend(step.chain_dependencies)

        if not proj_col_name:
            return None

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
                        matched = _match_column(
                            self.instance, rel, proj_col_name
                        )
                        if matched:
                            spec.require(rel)
                            return (rel, matched)
            stack.extend(step.chain_dependencies)
        return None

    def _find_inner_corr_column(
        self, sub: SubPlan, spec: BranchSpec
    ) -> Optional[tuple]:
        """Find the inner plan's correlated column.

        Returns (RelationId, matched_col_name) or None.
        """
        stack = [sub.inner]
        while stack:
            step = stack.pop()
            if isinstance(step, Filter) and step.condition:
                for col in step.condition.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id and col_id.relation:
                        inner_relation = col_id.relation
                        matched = col_id.name.normalized
                    else:
                        inner_relation = _relation_for_table(
                            self.instance, col.table or ""
                        )
                        matched = _match_column(
                            self.instance, inner_relation, col.name
                        )
                    tname = (
                        inner_relation.name.normalized
                        if inner_relation.name
                        else ""
                    )
                    if matched and tname in self.instance.tables:
                        spec.require(inner_relation)
                        return (inner_relation, matched)
            stack.extend(step.chain_dependencies)
        return None

    def _find_corr_inner_column(
        self, sub: SubPlan, col_name: str
    ) -> Optional[tuple]:
        """Find the inner plan's column matching col_name.

        Returns (RelationId, matched_col_name) or None.
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
                        if col_id and col_id.relation:
                            inner_relation = col_id.relation
                            matched = col_id.name.normalized
                        else:
                            inner_relation = _relation_for_table(
                                self.instance, col.table or ""
                            )
                            matched = _match_column(
                                self.instance, inner_relation, col.name
                            )
                        tname = (
                            inner_relation.name.normalized
                            if inner_relation.name
                            else ""
                        )
                        if matched and tname in self.instance.tables:
                            return (inner_relation, matched)
            if isinstance(step, Scan) and step.source:
                if isinstance(step.source, exp.Table):
                    name = step.source.name
                    rel = _relation_for_table(self.instance, name)
                    tname = rel.name.normalized if rel.name else ""
                    if tname in self.instance.tables:
                        matched = _match_column(
                            self.instance, rel, col_name
                        )
                        if matched:
                            return (rel, matched)
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
                if l_id is None:
                    lt = _relation_for_table(
                        self.instance, left.table or ""
                    )
                    lc = _match_column(self.instance, lt, left.name)
                    if lc:
                        l_id = physical_column(lc, lt)
                if r_id is None:
                    rt = _relation_for_table(
                        self.instance, right.table or ""
                    )
                    rc = _match_column(self.instance, rt, right.name)
                    if rc:
                        r_id = physical_column(rc, rt)
                if l_id and r_id and l_id.relation and r_id.relation:
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
                                if col.table:
                                    rel = _relation_for_table(
                                        self.instance, col.table
                                    )
                                    tname = (
                                        rel.name.normalized
                                        if rel.name
                                        else ""
                                    )
                                    if tname in self.instance.tables:
                                        return rel
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
                    if col.table:
                        rel = _relation_for_table(
                            self.instance, col.table
                        )
                        tname = rel.name.normalized if rel.name else ""
                        if tname in self.instance.tables:
                            return rel
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
                if col_id and col_id.relation:
                    relation = col_id.relation
                    matched = col_id.name.normalized
                else:
                    relation = _relation_for_table(
                        self.instance, target_col.table or ""
                    )
                    matched = _match_column(
                        self.instance, relation, target_col.name
                    )
                tname = relation.name.normalized if relation.name else ""
                if matched and tname and relation in spec.requirements:
                    if not _has_equality_constraint(
                        spec.requirements[relation].constraints, matched
                    ):
                        col_node = _solver_column(
                            self.instance, tname, matched
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

    def _projected_columns(self, step: Project) -> List[str]:
        """Get projected column names."""
        cols: List[str] = []
        for proj in step.projections:
            if isinstance(proj, exp.Expression):
                for col in proj.find_all(exp.Column):
                    cols.append(col.name)
        return cols


# =============================================================================
# Resolver: solver integration and row materialization
# =============================================================================

# (Task 3: Resolver class will be added here)


# =============================================================================
# Public API
# =============================================================================

# (Task 3: speculate() function will be added here)
