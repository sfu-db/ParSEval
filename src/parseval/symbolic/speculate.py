"""Speculative data generation via top-down constraint propagation.

The speculative component walks the Plan top-down — from "I want at least
one output row" backward through each operator — deriving what each table
needs. It produces requirements for BOTH positive and negative branches,
ensuring the generated database can distinguish equivalent from
non-equivalent queries.

Public API::

    from parseval.symbolic.speculate import speculate
    rows_per_table = speculate(plan, instance, alias_map, dialect)
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.planner import (
    Aggregate, Filter, Having, Join, Limit, Project, Scan, SetOperation, Sort, SubPlan,
)
from parseval.plan.rex import column_meta, concrete, negate_predicate
from parseval.solver import SolverConstraint

logger = logging.getLogger("parseval.speculate")


def _lookup_col_type(instance, table: str, col_name: str) -> Optional[str]:
    """Look up column type with case-insensitive fallback."""
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


def _match_column(instance, table: str, col_name: str) -> Optional[str]:
    """Find the canonical column name in the instance (case-insensitive)."""
    if table not in instance.tables:
        return None
    lower = col_name.lower()
    return next((s for s in instance.tables[table] if s.lower() == lower), None)


# =============================================================================
# Data structures
# =============================================================================


@dataclass(frozen=True)
class RowBinding:
    """Transient mapping from a solver table key to one physical witness row."""
    table: str
    alias: Optional[str]
    row: int


def _solver_table_key(binding: RowBinding) -> str:
    alias = normalize_name(binding.alias or binding.table)
    table = normalize_name(binding.table)
    return f"{table}__{alias}__r{binding.row}"


def _split_solver_variable(name: str) -> Tuple[str, str]:
    if "." not in name:
        return "", normalize_name(name)
    table_key, column = name.rsplit(".", 1)
    return normalize_name(table_key), normalize_name(column)


def _rows_from_solver_assignments(
    assignments: Dict[str, Any],
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    rows_by_slot: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
    for variable_name, value in assignments.items():
        table_key, column = _split_solver_variable(variable_name)
        binding = row_bindings.get(table_key)
        if binding is None:
            continue
        schema = instance.tables.get(binding.table)
        if schema is None or column not in schema:
            continue
        rows_by_slot.setdefault(
            (binding.table, normalize_name(binding.alias or ""), binding.row),
            {},
        )[column] = value

    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (table, _alias, _row_index), values in sorted(rows_by_slot.items()):
        rows.setdefault(table, []).append(values)
    return rows


def _physical_table_for_alias(alias_or_table: str, alias_map) -> str:
    key = normalize_name(alias_or_table)
    if hasattr(alias_map, "resolve"):
        resolved = alias_map.resolve(key)
        return normalize_name(resolved or key)
    if hasattr(alias_map, "get"):
        return normalize_name(alias_map.get(key, key))
    return key


def _binding_for_column(
    col: exp.Column,
    row_bindings: Dict[str, RowBinding],
    alias_map,
    default_row: int = 0,
) -> Optional[RowBinding]:
    raw_table = normalize_name(col.table or "")
    physical = _physical_table_for_alias(raw_table, alias_map) if raw_table else ""
    for binding in row_bindings.values():
        if binding.row != default_row:
            continue
        if raw_table and normalize_name(binding.alias or "") == raw_table:
            return binding
        if physical and normalize_name(binding.table) == physical:
            return binding
    return None


def _rewrite_expr_for_row_scope(
    expr: exp.Expression,
    row_bindings: Dict[str, RowBinding],
    alias_map,
    default_row: int = 0,
) -> exp.Expression:
    rewritten = expr.copy()
    for col in rewritten.find_all(exp.Column):
        binding = _binding_for_column(col, row_bindings, alias_map, default_row)
        if binding is None:
            continue
        old_type = getattr(col, "type", None)
        col.set("table", exp.to_identifier(_solver_table_key(binding)))
        if old_type is not None:
            col.type = old_type
    return rewritten


@dataclass
class TableConstraint:
    """Constraints on what one table needs for a specific branch."""
    table: str  # physical table name
    alias: Optional[str] = None  # alias (for self-joins, distinguishes rows)
    constraints: List[exp.Expression] = field(default_factory=list)
    min_rows: int = 1
    duplicate_columns: List[str] = field(default_factory=list)
    group_key_columns: List[str] = field(default_factory=list)
    # Backward-compat fields (kept so Resolver still works until Task 2)
    fixed_values: Dict[str, Any] = field(default_factory=dict)
    not_null: Set[str] = field(default_factory=set)
    must_null: Set[str] = field(default_factory=set)
    predicates: List[Tuple[str, str, Any]] = field(default_factory=list)
    # Boundary rows: list of {col_name: value} dicts for edge-case testing.
    boundary_rows: List[Dict[str, Any]] = field(default_factory=list)


class ColumnUnionFind:
    """Union-Find for tracking column equivalence classes (JOIN, GROUP BY)."""

    def __init__(self):
        self._parent: Dict[str, str] = {}
        self._rank: Dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> None:
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

    def same(self, x: str, y: str) -> bool:
        return self.find(x) == self.find(y)

    def groups(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for x in self._parent:
            rep = self.find(x)
            result.setdefault(rep, []).append(x)
        return result

    def members(self) -> Set[str]:
        return set(self._parent.keys())


@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""
    branch: str
    requirements: Dict[str, TableConstraint] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)
    deferred: List[exp.Expression] = field(default_factory=list)

    def require(self, table: str) -> TableConstraint:
        if table not in self.requirements:
            self.requirements[table] = TableConstraint(table=table)
        return self.requirements[table]

    def equate(self, col_a: str, col_b: str) -> None:
        """Declare two columns must have the same value."""
        self.equivalences.union(col_a, col_b)


# Backward-compat alias
TableRequirement = TableConstraint


# =============================================================================
# Propagator: top-down constraint derivation
# =============================================================================


_COMPARISON_NODES = (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)


class Propagator:
    """Walk the Plan top-down, deriving table requirements for each branch.

    The new Propagator stores constraints as ``exp.Expression`` objects
    directly, instead of lowering to ``(col, op, value)`` tuples.
    """

    def __init__(self, plan: Plan, instance: Instance, alias_map, dialect: str, objective: str = "branch_coverage"):
        self.plan = plan
        self.instance = instance
        self.alias_map = alias_map
        self.dialect = dialect
        self.objective = objective

    def _resolve_table(self, name: str) -> str:
        """Resolve alias or table name to physical table name."""
        if not name:
            return ""
        real = self.alias_map.resolve(name)
        return real if real in self.instance.tables else name

    def _match_column(self, table: str, col_name: str) -> Optional[str]:
        return _match_column(self.instance, table, col_name)

    def _is_self_join_table(self, table: str) -> bool:
        """Check if a table is involved in a self-join."""
        if hasattr(self.alias_map, 'has_self_join'):
            return self.alias_map.has_self_join(table)
        count = sum(1 for v in self.alias_map.values() if v == table)
        return count > 1

    # -----------------------------------------------------------------
    # Top-level propagation
    # -----------------------------------------------------------------

    def propagate(self) -> List[BranchSpec]:
        """Produce specs for positive + all negative + null branches."""
        # Trigger planner's column annotation so _parseval_meta is available.
        _ = self.plan.annotations
        # Build a flat alias_map dict for the solver.
        specs = []
        # Positive path.
        pos = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, pos)
        self._collect_boundary_values(pos)
        self._add_schema_constraints(pos)
        self._annotate_column_types(pos)
        specs.append(pos)
        # Negative branches per decision site.
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                conjuncts = self._split_conjuncts(step.condition)
                for idx in range(len(conjuncts)):
                    neg = BranchSpec(branch=f"negative_c{idx}")
                    self._propagate_step(self.plan.root, neg, negate_step=step, negate_conjunct=idx)
                    self._add_schema_constraints(neg)
                    self._annotate_column_types(neg)
                    specs.append(neg)
            elif isinstance(step, Join):
                left_un = BranchSpec(branch="left_unmatched")
                self._propagate_unmatched_left(step, left_un)
                self._add_schema_constraints(left_un)
                self._annotate_column_types(left_un)
                specs.append(left_un)
                # Also generate right-unmatched rows for each joined table.
                for join_name in (step.joins or {}):
                    right_un = BranchSpec(branch=f"right_unmatched_{join_name}")
                    self._propagate_unmatched_right(step, join_name, right_un)
                    self._add_schema_constraints(right_un)
                    self._annotate_column_types(right_un)
                    specs.append(right_un)
            elif isinstance(step, Having) and step.condition:
                fail = BranchSpec(branch="having_fail")
                self._propagate_step(self.plan.root, fail, negate_step=step)
                self._add_schema_constraints(fail)
                self._annotate_column_types(fail)
                specs.append(fail)
        # Null branches: one per nullable column, NULLing only that column.
        null_targets = self._collect_null_target_columns(pos)
        if null_targets:
            for table, cols in null_targets.items():
                for col_name in cols:
                    null_spec = BranchSpec(branch=f"null_{table}.{col_name}")
                    self._propagate_step(self.plan.root, null_spec)
                    self._apply_single_null_override(null_spec, table, col_name)
                    self._add_schema_constraints(null_spec)
                    self._annotate_column_types(null_spec)
                    specs.append(null_spec)
        else:
            # Fallback: single null branch if no targets found.
            null_spec = BranchSpec(branch="null_branch")
            self._propagate_step(self.plan.root, null_spec)
            self._apply_null_overrides(null_spec)
            self._add_schema_constraints(null_spec)
            self._annotate_column_types(null_spec)
            specs.append(null_spec)
        # CASE WHEN branches: one per CASE WHEN, negating all WHEN conditions
        # to exercise the ELSE arm.
        for case_idx, when_conditions in enumerate(self._collect_case_when_conditions()):
            case_spec = BranchSpec(branch=f"case_else_{case_idx}")
            self._propagate_step(self.plan.root, case_spec)
            for cond in when_conditions:
                negated = negate_predicate(cond.copy())
                self._store_expression(negated, case_spec)
            self._add_schema_constraints(case_spec)
            self._annotate_column_types(case_spec)
            specs.append(case_spec)
        return specs

    def propagate_gold_non_empty(self) -> List[BranchSpec]:
        """Produce positive witness specs only.

        This mode avoids negative, NULL, boundary, and unmatched-join coverage
        rows.
        """
        _ = self.plan.annotations
        specs: List[BranchSpec] = []

        base = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, base)
        self._add_schema_constraints(base)
        self._annotate_column_types(base)
        specs.append(base)

        for case_idx, when_conditions in enumerate(self._collect_case_when_positive_conditions()):
            prior_conditions: List[exp.Expression] = []
            for when_idx, cond in enumerate(when_conditions):
                case_spec = BranchSpec(branch=f"positive_case_{case_idx}_when_{when_idx}")
                self._propagate_step(self.plan.root, case_spec)
                for prior in prior_conditions:
                    self._store_expression(negate_predicate(prior.copy()), case_spec)
                self._store_expression(cond.copy(), case_spec)
                self._add_schema_constraints(case_spec)
                self._annotate_column_types(case_spec)
                specs.append(case_spec)
                prior_conditions.append(cond)

        return specs

    # -----------------------------------------------------------------
    # Recursive step propagation
    # -----------------------------------------------------------------

    def _propagate_step(self, step: Step, spec: BranchSpec, negate_step: Optional[Step] = None, negate_conjunct: int = 0):
        """Recursively propagate requirements top-down."""
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            needed = offset + int(limit_val)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Apply min_rows to the driving table only.
            driving_alias = getattr(step, "source", None)
            driving_table = self.alias_map.get(driving_alias, driving_alias) if driving_alias else None
            if driving_table and driving_table in spec.requirements:
                spec.requirements[driving_table].min_rows = max(
                    spec.requirements[driving_table].min_rows, needed
                )
            elif driving_table:
                spec.requirements[driving_table] = TableConstraint(
                    table=driving_table, min_rows=needed
                )

        elif isinstance(step, Project):
            projected = self._projected_columns(step)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Add IS NOT NULL for projected columns as expression constraints.
            for table, tc in spec.requirements.items():
                for col in projected:
                    matched = self._match_column(table, col)
                    if matched:
                        col_node = exp.column(matched, table)
                        is_not_null = exp.Is(this=col_node, expression=exp.Not(this=exp.Null()))
                        if not self._has_is_not_null(tc.constraints, matched):
                            tc.constraints.append(is_not_null)
                dup_cols = [c for c in projected if self._match_column(table, c)]
                if dup_cols:
                    tc.duplicate_columns = dup_cols
                    tc.min_rows = max(tc.min_rows, 2)
                    # Propagate min_rows to joined tables so join equalities
                    # can resolve to different foreign key values.
                    for rep, members in spec.equivalences.groups().items():
                        if len(members) < 2:
                            continue
                        member_tables = {m.split(".")[0] for m in members}
                        if table in member_tables:
                            for other_table in member_tables:
                                if other_table != table and other_table in spec.requirements:
                                    spec.requirements[other_table].min_rows = max(
                                        spec.requirements[other_table].min_rows, 2
                                    )

        elif isinstance(step, Sort):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.condition and step is not negate_step:
                if self.objective == "gold_non_empty":
                    for scalar_condition in self._gold_having_scalar_constraints(step.condition):
                        self._store_expression(scalar_condition, spec)
                else:
                    self._store_expression(step.condition, spec)
                # HAVING with aggregate: derive min group size for counted table only.
                counted_table = self._find_counted_table(step.condition)
                min_size = self._extract_min_group_size(step.condition)
                if counted_table and counted_table in spec.requirements:
                    spec.requirements[counted_table].min_rows = max(
                        spec.requirements[counted_table].min_rows, min_size
                    )
                else:
                    for req in spec.requirements.values():
                        req.min_rows = max(req.min_rows, min_size)
                # Derive per-row value constraints from aggregate thresholds.
                self._extract_having_value_constraints(step.condition, spec, min_size)

        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # GROUP BY: mark group columns as needing the same value across rows.
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        table = self._resolve_table(col.table or "")
                        matched = self._match_column(table, col.name)
                        if matched:
                            req = spec.require(table)
                            spec.equivalences.find(f"{table}.{matched}")
                            if matched not in req.group_key_columns:
                                req.group_key_columns.append(matched)
            # Aggregate NULL detection: COUNT/SUM/AVG columns need a NULL row.
            if self.objective != "gold_non_empty":
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
                            table = self._resolve_table(col.table or "")
                            matched = self._match_column(table, col.name)
                            if matched and table in self.instance.tables:
                                req = spec.require(table)
                                if (
                                    not self._has_is_null(req.constraints, matched)
                                    and not self._has_is_not_null(req.constraints, matched)
                                ):
                                    col_node = exp.column(matched, table)
                                    is_not_null = exp.Is(
                                        this=col_node,
                                        expression=exp.Not(this=exp.Null()),
                                    )
                                    req.constraints.append(is_not_null)

        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            if step.condition:
                if step is negate_step:
                    # For AND: negate one conjunct (indexed by negate_conjunct),
                    # keep the rest as positive.
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
                # Handle column equalities for Union-Find linking.
                self._extract_column_equalities(step.condition, spec)
                # Detect scalar subquery atoms for deferred evaluation.
                for atom in self._iter_scalar_subquery_atoms(step.condition):
                    spec.deferred.append(atom)

        elif isinstance(step, Join):
            # Process chain dependencies first.
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)
            # Link join keys via equivalence (Union-Find) and store as expressions.
            for join_name, join_data in (step.joins or {}).items():
                join_table = self._resolve_table(join_name)
                source_keys = join_data.get("source_key", [])
                join_keys = join_data.get("join_key", [])
                for sk, jk in zip(source_keys, join_keys):
                    sk_table_name = sk.table if hasattr(sk, "table") and sk.table else (step.source_name or step.name)
                    sk_table = self._resolve_table(sk_table_name)
                    sk_col = self._match_column(sk_table, sk.name if hasattr(sk, "name") else str(sk))
                    jk_col = self._match_column(join_table, jk.name if hasattr(jk, "name") else str(jk))
                    if sk_col and jk_col:
                        sk_alias = normalize_name(sk.table or sk_table_name or "")
                        jk_alias = normalize_name(jk.table or join_name or "")
                        if (
                            self.objective == "gold_non_empty"
                            and sk_table == join_table
                            and self._is_self_join_table(sk_table)
                            and sk_alias
                            and jk_alias
                        ):
                            sk_key = f"{sk_table}__{sk_alias}"
                            jk_key = f"{join_table}__{jk_alias}"
                            spec.requirements.setdefault(
                                sk_key,
                                TableConstraint(table=sk_table, alias=sk_alias),
                            )
                            spec.requirements.setdefault(
                                jk_key,
                                TableConstraint(table=join_table, alias=jk_alias),
                            )
                            spec.equate(f"{sk_key}.{sk_col}", f"{jk_key}.{jk_col}")
                            continue
                        spec.require(sk_table)
                        spec.require(join_table)
                        spec.equate(f"{sk_table}.{sk_col}", f"{join_table}.{jk_col}")
                        # Store join equality as exp.EQ expression.
                        eq_expr = exp.EQ(
                            this=exp.column(sk_col, sk_table),
                            expression=exp.column(jk_col, join_table),
                        )
                        spec.requirements[sk_table].constraints.append(eq_expr)
                        spec.requirements[join_table].constraints.append(eq_expr)
                        # Mark join keys as group_key_columns.
                        req_jk = spec.require(join_table)
                        if jk_col not in req_jk.group_key_columns:
                            req_jk.group_key_columns.append(jk_col)

        elif isinstance(step, SetOperation):
            # Propagate into each branch (left/right) of the set operation.
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step, negate_conjunct)

        elif isinstance(step, Scan):
            table = self._resolve_table(step.name)
            if table in self.instance.tables:
                spec.require(table)
            # For FROM-subquery scans, propagate into the SubPlan's inner plan.
            for sub in step.subplan_dependencies:
                if sub.inner:
                    self._propagate_step(sub.inner, spec, negate_step, negate_conjunct)

        # Handle SubPlan dependencies.
        for sub in step.subplan_dependencies:
            self._propagate_subplan(sub, spec, parent_condition=getattr(step, "condition", None))

    # -----------------------------------------------------------------
    # Expression storage
    # -----------------------------------------------------------------

    def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
        """Decompose AND, resolve columns, store per-table."""
        conjuncts = self._split_conjuncts(expr)
        for conjunct in conjuncts:
            # Subquery-containing conjuncts must be deferred, not stored
            # as solver constraints — the solver cannot handle raw Subquery nodes.
            if conjunct.find(exp.Exists) or conjunct.find(exp.Subquery):
                spec.deferred.append(conjunct.copy())
                continue
            # Check for self-join: store on alias-specific key
            if self._store_conjunct_for_self_join(conjunct, spec):
                continue
            # Resolve column table qualifiers to physical names.
            resolved = self._resolve_columns(conjunct.copy())
            table = self._find_table_for_expr(resolved)
            if table:
                tc = spec.require(table)
                tc.constraints.append(resolved)
        # Extract temporal/age constraints for backward compat.
        self._extract_temporal_age_constraints(expr, spec)

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

    def _find_table_for_expr(self, expr: exp.Expression) -> Optional[str]:
        """Find the primary table for an expression by resolving its columns."""
        # For EQ with two columns, use the left side's table (the table being constrained).
        if isinstance(expr, exp.EQ):
            left = expr.this
            if isinstance(left, exp.Column):
                table = self._resolve_table(left.table or "")
                if table and table in self.instance.tables:
                    return table
        # Default: first column's table
        for col in expr.find_all(exp.Column):
            table = self._resolve_table(col.table or "")
            if table and table in self.instance.tables:
                return table
        return None

    def _resolve_columns(self, expr: exp.Expression, spec: Optional[BranchSpec] = None) -> exp.Expression:
        """Resolve column table qualifiers to physical table names.

        For self-join tables, resolves to the alias-specific key
        (e.g. ``satscores__t1``) so the solver can find the variable.
        """
        for col in expr.find_all(exp.Column):
            if col.table:
                resolved = self._resolve_table(col.table)
                if not resolved or resolved not in self.instance.tables:
                    continue
                # For self-join tables, use the alias-specific key.
                if spec and self._is_self_join_table(resolved):
                    alias = normalize_name(col.table)
                    key = f"{resolved}__{alias}"
                    if key in spec.requirements:
                        if alias != col.table:
                            col.set("table", exp.to_identifier(alias))
                        continue
                if resolved != col.table:
                    col.set("table", exp.to_identifier(resolved))
        return expr

    # -----------------------------------------------------------------
    # Schema constraints
    # -----------------------------------------------------------------

    def _add_schema_constraints(self, spec: BranchSpec):
        """Add NOT NULL, UNIQUE, FK as expression constraints."""
        for table_key, tc in list(spec.requirements.items()):
            table = tc.table
            if "__" in table_key:
                table = table_key.split("__")[0]
            if table not in self.instance.tables:
                continue

            # NOT NULL columns.
            for col_name in self.instance.tables[table]:
                if not self.instance.nullable(table, col_name):
                    if self._has_is_null(tc.constraints, col_name):
                        continue
                    col_node = exp.column(col_name, table)
                    is_not_null = exp.Is(this=col_node, expression=exp.Not(this=exp.Null()))
                    if not self._has_is_not_null(tc.constraints, col_name):
                        tc.constraints.append(is_not_null)

            # UNIQUE columns with existing data → exclude existing values.
            existing_rows = self.instance.get_rows(table)
            if existing_rows:
                for col_name in self.instance.tables[table]:
                    if self.instance.is_unique(table, col_name):
                        existing_vals: list = []
                        for row in existing_rows:
                            sym = row.get(table, col_name)
                            if sym is not None and sym.concrete is not None:
                                existing_vals.append(sym.concrete)
                        if existing_vals:
                            col_node = exp.column(col_name, table)
                            literals = [
                                exp.Literal.number(v) if isinstance(v, (int, float))
                                else exp.Literal.string(str(v))
                                for v in existing_vals
                            ]
                            not_in = exp.Not(this=exp.In(
                                this=col_node, expressions=literals,
                            ))
                            tc.constraints.append(not_in)

            # FK constraints → parent values must be present.
            for fk in self.instance.get_foreign_key(table):
                ref = fk.args.get("reference")
                if not ref:
                    continue
                ref_table_node = ref.find(exp.Table)
                if not ref_table_node:
                    continue
                ref_table = normalize_name(ref_table_node.name)
                fk_cols = [identifier.name for identifier in fk.expressions]
                if not fk_cols:
                    continue
                fk_col = fk_cols[0]
                parent_rows = self.instance.get_rows(ref_table)
                if parent_rows:
                    parent_vals: list = []
                    ref_col_name = self.instance.resolve_fk_ref_column(fk)
                    if ref_col_name:
                        for row in parent_rows:
                            sym = row.get(ref_table, ref_col_name)
                            if sym is not None and sym.concrete is not None:
                                parent_vals.append(sym.concrete)
                    if parent_vals:
                        col_node = exp.column(fk_col, table)
                        literals = [
                            exp.Literal.number(v) if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in parent_vals
                        ]
                        in_expr = exp.In(this=col_node, expressions=literals)
                        tc.constraints.append(in_expr)

    # -----------------------------------------------------------------
    # NULL branch generation
    # -----------------------------------------------------------------

    def _collect_null_target_columns(self, spec: BranchSpec) -> Dict[str, Set[str]]:
        """Collect columns that should get NULL values in the null branch.

        Targets: columns in IS NOT NULL conditions, SELECT projections,
        and aggregate function arguments.  Schema NOT NULL columns are excluded.
        """
        targets: Dict[str, Set[str]] = {}

        # 1. Columns with IS NOT NULL in constraints.
        for table_key, tc in spec.requirements.items():
            for constraint in tc.constraints:
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(right.this, exp.Null):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            table = self._resolve_table(col.table or table_key)
                            matched = self._match_column(table, col.name)
                            if matched:
                                targets.setdefault(table, set()).add(matched)

        # 2. Columns in SELECT projections.
        for step in self.plan.ordered_steps:
            if isinstance(step, Project):
                for proj in step.projections:
                    if isinstance(proj, exp.Expression):
                        for col in proj.find_all(exp.Column):
                            table = self._resolve_table(col.table or "")
                            matched = self._match_column(table, col.name)
                            if matched and table in self.instance.tables:
                                targets.setdefault(table, set()).add(matched)

            # 3. Columns in aggregate function arguments.
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for col in agg_expr.find_all(exp.Column):
                        table = self._resolve_table(col.table or "")
                        matched = self._match_column(table, col.name)
                        if matched and table in self.instance.tables:
                            targets.setdefault(table, set()).add(matched)

        # 4. Exclude schema NOT NULL columns.
        for table in list(targets.keys()):
            if table not in self.instance.tables:
                continue
            targets[table] = {
                col for col in targets[table]
                if self.instance.nullable(table, col)
            }
            if not targets[table]:
                del targets[table]

        return targets

    def _apply_null_overrides(self, spec: BranchSpec):
        """Replace IS NOT NULL with IS NULL for target columns in the null branch."""
        targets = self._collect_null_target_columns(spec)
        if not targets:
            return

        for table_key, tc in spec.requirements.items():
            table = tc.table
            if "__" in table_key:
                table = table_key.split("__")[0]
            if table not in targets:
                continue

            # Remove IS NOT NULL constraints for target columns.
            # Handles both forms: `col IS NOT NULL` and `NOT(col IS NULL)`.
            new_constraints = []
            for constraint in tc.constraints:
                remove = False
                # Form 1: col IS NOT NULL  →  exp.Is(this=col, expression=exp.Not(exp.Null()))
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(right.this, exp.Null):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            col_table = self._resolve_table(col.table or table_key)
                            matched = self._match_column(col_table, col.name)
                            if matched and matched in targets.get(table, set()):
                                remove = True
                # Form 2: NOT(col IS NULL)  →  exp.Not(exp.Is(this=col, expression=exp.Null()))
                if not remove and isinstance(constraint, exp.Not):
                    inner = constraint.this
                    if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
                        if isinstance(inner.this, exp.Column):
                            col = inner.this
                            col_table = self._resolve_table(col.table or table_key)
                            matched = self._match_column(col_table, col.name)
                            if matched and matched in targets.get(table, set()):
                                remove = True
                if not remove:
                    new_constraints.append(constraint)
            tc.constraints = new_constraints

            # Add IS NULL constraints for target columns.
            for col_name in targets[table]:
                col_node = exp.column(col_name, table)
                is_null = exp.Is(this=col_node, expression=exp.Null())
                tc.constraints.append(is_null)

    def _apply_single_null_override(self, spec: BranchSpec, target_table: str, target_col: str):
        """Replace IS NOT NULL with IS NULL for a single target column."""
        for table_key, tc in spec.requirements.items():
            table = tc.table
            if "__" in table_key:
                table = table_key.split("__")[0]
            if table != target_table:
                continue

            new_constraints = []
            for constraint in tc.constraints:
                remove = False
                if isinstance(constraint, exp.Is):
                    right = constraint.expression
                    if isinstance(right, exp.Not) and isinstance(right.this, exp.Null):
                        if isinstance(constraint.this, exp.Column):
                            col = constraint.this
                            col_table = self._resolve_table(col.table or table_key)
                            matched = self._match_column(col_table, col.name)
                            if matched and matched == target_col:
                                remove = True
                if not remove and isinstance(constraint, exp.Not):
                    inner = constraint.this
                    if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
                        if isinstance(inner.this, exp.Column):
                            col = inner.this
                            col_table = self._resolve_table(col.table or table_key)
                            matched = self._match_column(col_table, col.name)
                            if matched and matched == target_col:
                                remove = True
                if not remove:
                    new_constraints.append(constraint)
            tc.constraints = new_constraints

            # Add IS NULL for the target column only.
            col_node = exp.column(target_col, target_table)
            is_null = exp.Is(this=col_node, expression=exp.Null())
            tc.constraints.append(is_null)

    # -----------------------------------------------------------------
    # WHERE literal extraction
    # -----------------------------------------------------------------

    def _extract_where_literals(self, spec: BranchSpec):
        """Extract literal values from WHERE conditions and inject as fixed_values.

        This ensures the generated data satisfies the WHERE conditions,
        so queries return non-empty results that can be compared.
        """
        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or not step.condition:
                continue
            conjuncts = self._split_conjuncts(step.condition)
            for conjunct in conjuncts:
                self._extract_literal_from_conjunct(conjunct, spec)

    def _extract_literal_from_conjunct(self, conjunct: exp.Expression, spec: BranchSpec):
        """Extract literal values from a single WHERE conjunct."""
        if isinstance(conjunct, exp.EQ):
            left, right = conjunct.this, conjunct.expression
            col_node, lit_node = None, None
            if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
                col_node, lit_node = left, right
            elif isinstance(right, exp.Column) and not isinstance(left, exp.Column):
                col_node, lit_node = right, left
            if col_node and lit_node:
                val = concrete(lit_node)
                if val is not None:
                    table = self._resolve_table(col_node.table or "")
                    matched = self._match_column(table, col_node.name)
                    if matched and table in self.instance.tables:
                        tc = spec.require(table)
                        tc.fixed_values[matched] = val

        elif isinstance(conjunct, exp.Like):
            # LIKE 'prefix%' → use prefix as fixed value
            left = conjunct.this
            right = conjunct.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal) and right.is_string:
                pattern = str(right.this)
                prefix = pattern.split('%')[0].split('_')[0]
                if prefix:
                    table = self._resolve_table(left.table or "")
                    matched = self._match_column(table, left.name)
                    if matched and table in self.instance.tables:
                        tc = spec.require(table)
                        tc.fixed_values[matched] = prefix

        elif isinstance(conjunct, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            # Comparison: extract boundary value
            left, right = conjunct.this, conjunct.expression
            col_node, lit_node = None, None
            if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
                col_node, lit_node = left, right
            elif isinstance(right, exp.Column) and not isinstance(left, exp.Column):
                col_node, lit_node = right, left
            if col_node and lit_node:
                threshold = concrete(lit_node)
                if threshold is not None and not isinstance(threshold, str):
                    table = self._resolve_table(col_node.table or "")
                    matched = self._match_column(table, col_node.name)
                    if matched and table in self.instance.tables:
                        tc = spec.require(table)
                        op_type = type(conjunct)
                        if op_type is exp.GT:
                            tc.fixed_values[matched] = threshold + 1
                        elif op_type is exp.GTE:
                            tc.fixed_values[matched] = threshold
                        elif op_type is exp.LT:
                            tc.fixed_values[matched] = threshold - 1
                        elif op_type is exp.LTE:
                            tc.fixed_values[matched] = threshold

        elif isinstance(conjunct, exp.Is):
            # IS NULL / IS NOT NULL — skip
            pass

        elif isinstance(conjunct, exp.In):
            # IN (val1, val2, ...) — use first value
            col_node = conjunct.this
            if isinstance(col_node, exp.Column) and conjunct.expressions:
                first_val_node = conjunct.expressions[0]
                val = concrete(first_val_node)
                if val is not None:
                    table = self._resolve_table(col_node.table or "")
                    matched = self._match_column(table, col_node.name)
                    if matched and table in self.instance.tables:
                        tc = spec.require(table)
                        tc.fixed_values[matched] = val

    # -----------------------------------------------------------------
    # Boundary value collection
    # -----------------------------------------------------------------

    def _collect_boundary_values(self, spec: BranchSpec):
        """Collect boundary values from filter comparison predicates.

        For each comparison like ``col > 5``, generates a boundary row
        where ``col = 5`` (the threshold value that changes the predicate
        outcome). This exposes differences between queries with different
        comparison thresholds.
        """
        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or not step.condition:
                continue
            conjuncts = self._split_conjuncts(step.condition)
            for conjunct in conjuncts:
                self._extract_boundary_from_conjunct(conjunct, spec)

    def _extract_boundary_from_conjunct(self, conjunct: exp.Expression, spec: BranchSpec):
        """Extract boundary values from a single comparison conjunct."""
        if not isinstance(conjunct, _COMPARISON_NODES):
            return

        left, right = conjunct.this, conjunct.expression
        col_node, lit_node = None, None
        if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
            col_node, lit_node = left, right
        elif isinstance(right, exp.Column) and not isinstance(left, exp.Column):
            col_node, lit_node = right, left
        if col_node is None or lit_node is None:
            return

        threshold = concrete(lit_node)
        if threshold is None:
            return
        if isinstance(threshold, str):
            return

        table = self._resolve_table(col_node.table or "")
        matched = self._match_column(table, col_node.name)
        if not matched or table not in self.instance.tables:
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
            tc = spec.require(table)
            tc.boundary_rows.append({matched: boundary_val})

    # -----------------------------------------------------------------
    # Column type annotation
    # -----------------------------------------------------------------

    def _annotate_column_types(self, spec: BranchSpec):
        """Set .type on Column nodes from column_meta or instance schema."""
        from parseval.dtype import DataType

        for table_key, tc in spec.requirements.items():
            for constraint in tc.constraints:
                for col in constraint.find_all(exp.Column):
                    if getattr(col, "type", None) is not None:
                        continue
                    meta = column_meta(col)
                    if meta and "domain" in meta:
                        col.type = meta["domain"]
                    else:
                        col_table = self._resolve_table(col.table or table_key)
                        col_type_str = _lookup_col_type(self.instance, col_table, col.name)
                        if col_type_str:
                            try:
                                col.type = DataType.build(col_type_str)
                            except Exception:
                                pass

        # Also annotate columns in deferred (scalar subquery) atoms.
        for atom in spec.deferred:
            for col in atom.find_all(exp.Column):
                if getattr(col, "type", None) is not None:
                    continue
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col.type = meta["domain"]
                else:
                    col_table = self._resolve_table(col.table or "")
                    col_type_str = _lookup_col_type(self.instance, col_table, col.name)
                    if col_type_str:
                        try:
                            col.type = DataType.build(col_type_str)
                        except Exception:
                            pass

    # -----------------------------------------------------------------
    # Aggregate NULL constraints
    # -----------------------------------------------------------------

    def _add_aggregate_null_constraints(self, agg_expr: exp.Expression, spec: BranchSpec):
        """Add IS NULL for COUNT/SUM/AVG columns.

        Skips COUNT(*) and COUNT(DISTINCT col) — those don't need NULL testing.
        """
        for count_node in agg_expr.find_all(exp.Count):
            if isinstance(count_node.this, exp.Star):
                continue
            if count_node.args.get("distinct"):
                continue
            for col in count_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    if not self._has_equality_constraint(req.constraints, matched):
                        col_node = exp.column(matched, table)
                        is_null = exp.Is(this=col_node, expression=exp.Null())
                        req.constraints.append(is_null)
                        req.min_rows = max(req.min_rows, 2)

        for sum_node in agg_expr.find_all(exp.Sum):
            for col in sum_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    if not self._has_equality_constraint(req.constraints, matched):
                        col_node = exp.column(matched, table)
                        is_null = exp.Is(this=col_node, expression=exp.Null())
                        req.constraints.append(is_null)
                        req.min_rows = max(req.min_rows, 2)

        for avg_node in agg_expr.find_all(exp.Avg):
            for col in avg_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    if not self._has_equality_constraint(req.constraints, matched):
                        col_node = exp.column(matched, table)
                        is_null = exp.Is(this=col_node, expression=exp.Null())
                        req.constraints.append(is_null)
                        req.min_rows = max(req.min_rows, 2)

        # MIN/MAX: add IS NULL to test NULL handling.
        for agg_type in (exp.Min, exp.Max):
            for agg_node in agg_expr.find_all(agg_type):
                for col in agg_node.find_all(exp.Column):
                    table = self._resolve_table(col.table or "")
                    matched = self._match_column(table, col.name)
                    if matched and table in self.instance.tables:
                        req = spec.require(table)
                        if not self._has_equality_constraint(req.constraints, matched):
                            col_node = exp.column(matched, table)
                            is_null = exp.Is(this=col_node, expression=exp.Null())
                            req.constraints.append(is_null)
                            req.min_rows = max(req.min_rows, 2)

    # -----------------------------------------------------------------
    # Join / SubPlan handling
    # -----------------------------------------------------------------

    def _propagate_unmatched_left(self, join_step: Join, spec: BranchSpec):
        """Generate a left-table row with no matching right-table row."""
        source = self._resolve_table(join_step.source_name or join_step.name)
        if source in self.instance.tables:
            req = spec.require(source)
            # Add NOT IN constraint on join key to ensure no match.
            for join_name, join_data in (join_step.joins or {}).items():
                join_table = self._resolve_table(join_name)
                source_keys = join_data.get("source_key", [])
                for sk in source_keys:
                    sk_col = self._match_column(source, sk.name if hasattr(sk, "name") else str(sk))
                    if sk_col and join_table in self.instance.tables:
                        # Collect existing join key values from the right table.
                        existing_vals = []
                        for row in self.instance.get_rows(join_table):
                            if sk_col in row.columns:
                                val = row[sk_col].concrete
                                if val is not None:
                                    existing_vals.append(val)
                        if existing_vals:
                            col_node = exp.column(sk_col, source)
                            literals = [
                                exp.Literal.number(v) if isinstance(v, (int, float))
                                else exp.Literal.string(str(v))
                                for v in existing_vals
                            ]
                            not_in = exp.Not(this=exp.In(
                                this=col_node, expressions=literals,
                            ))
                            req.constraints.append(not_in)

    def _propagate_unmatched_right(self, join_step: Join, join_name: str, spec: BranchSpec):
        """Generate a right-table row with no matching left-table row."""
        join_table = self._resolve_table(join_name)
        if join_table in self.instance.tables:
            req = spec.require(join_table)
            # Add NOT IN constraint on join key to ensure no match.
            source = self._resolve_table(join_step.source_name or join_step.name)
            join_data = (join_step.joins or {}).get(join_name, {})
            join_keys = join_data.get("join_key", [])
            for jk in join_keys:
                jk_col = self._match_column(join_table, jk.name if hasattr(jk, "name") else str(jk))
                if jk_col and source in self.instance.tables:
                    existing_vals = []
                    for row in self.instance.get_rows(source):
                        if jk_col in row.columns:
                            val = row[jk_col].concrete
                            if val is not None:
                                existing_vals.append(val)
                    if existing_vals:
                        col_node = exp.column(jk_col, join_table)
                        literals = [
                            exp.Literal.number(v) if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in existing_vals
                        ]
                        not_in = exp.Not(this=exp.In(
                            this=col_node, expressions=literals,
                        ))
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
            and self._subplan_anchor_is_negated(parent_condition, sub.anchor)
        )
        if sub.kind.value == "exists" and sub.correlation and not negated_exists:
            for corr_col in sub.correlation:
                outer_table = self._resolve_table(corr_col.table or "")
                matched = self._match_column(outer_table, corr_col.name)
                if matched:
                    spec.require(outer_table)
                    outer_key = f"{outer_table}.{matched}"
                    inner_key = self._find_inner_corr_column(sub, spec)
                    if inner_key:
                        spec.equate(outer_key, inner_key)
                        inner_table, inner_col = inner_key.split(".", 1)
                        eq_expr = exp.EQ(
                            this=exp.column(matched, outer_table),
                            expression=exp.column(inner_col, inner_table),
                        )
                        spec.requirements[outer_table].constraints.append(eq_expr)

        elif sub.kind.value == "in":
            self._propagate_in_subplan(sub, spec)

        elif sub.kind.value == "scalar":
            self._propagate_scalar_subplan(sub, spec)

        # Always propagate into inner plan for WHERE constraints.
        if sub.inner and not negated_exists:
            self._propagate_step(sub.inner, spec)
            self._fix_inner_filter_tables(sub.inner, spec)

    def _subplan_anchor_is_negated(
        self,
        predicate: Optional[exp.Expression],
        anchor: Optional[exp.Expression],
    ) -> bool:
        """Return True when a subplan anchor has odd NOT polarity in predicate."""
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
        outer_table = self._resolve_table(outer_col.table or "")
        outer_matched = self._match_column(outer_table, outer_col.name)
        if not outer_matched:
            return

        inner_col_key = self._find_inner_select_column(sub, spec)
        if inner_col_key:
            spec.require(outer_table)
            spec.equate(f"{outer_table}.{outer_matched}", inner_col_key)
            inner_table, inner_col = inner_col_key.split(".", 1)
            eq_expr = exp.EQ(
                this=exp.column(outer_matched, outer_table),
                expression=exp.column(inner_col, inner_table),
            )
            spec.requirements[outer_table].constraints.append(eq_expr)

    def _propagate_scalar_subplan(self, sub: SubPlan, spec: BranchSpec):
        """Ensure scalar subquery's inner table has at least one row."""
        stack = [sub.inner]
        visited: set = set()
        while stack:
            step = stack.pop()
            if id(step) in visited:
                continue
            visited.add(id(step))
            if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
                table = self._resolve_table(step.source.name)
                if table in self.instance.tables:
                    spec.require(table)
            stack.extend(step.chain_dependencies)

        # Equate correlated columns between outer and inner.
        if sub.correlation:
            for corr_col in sub.correlation:
                outer_table = self._resolve_table(corr_col.table or "")
                outer_matched = self._match_column(outer_table, corr_col.name)
                if not outer_matched:
                    continue
                inner_key = self._find_corr_inner_column(sub, corr_col.name)
                if inner_key:
                    spec.require(outer_table)
                    spec.equate(f"{outer_table}.{outer_matched}", inner_key)
                    # Add EQ constraint to the outer table.
                    inner_table, inner_col = inner_key.split(".", 1)
                    eq_expr = exp.EQ(
                        this=exp.column(outer_matched, outer_table),
                        expression=exp.column(inner_col, inner_table),
                    )
                    if outer_table in spec.requirements:
                        spec.requirements[outer_table].constraints.append(eq_expr)

    def _find_inner_select_column(self, sub: SubPlan, spec: BranchSpec) -> Optional[str]:
        """Find the inner plan's source column for IN subqueries."""
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
            if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
                inner_table = self._resolve_table(step.source.name)
                if inner_table in self.instance.tables:
                    matched = self._match_column(inner_table, proj_col_name)
                    if matched:
                        spec.require(inner_table)
                        return f"{inner_table}.{matched}"
            stack.extend(step.chain_dependencies)
        return None

    def _find_inner_corr_column(self, sub: SubPlan, spec: BranchSpec) -> Optional[str]:
        """Find the inner plan's correlated column and return its qualified name."""
        stack = [sub.inner]
        while stack:
            step = stack.pop()
            if isinstance(step, Filter) and step.condition:
                for col in step.condition.find_all(exp.Column):
                    inner_table = self._resolve_table(col.table or "")
                    if inner_table in self.instance.tables:
                        matched = self._match_column(inner_table, col.name)
                        if matched:
                            spec.require(inner_table)
                            return f"{inner_table}.{matched}"
            stack.extend(step.chain_dependencies)
        return None

    def _find_corr_inner_column(self, sub: SubPlan, col_name: str) -> Optional[str]:
        """Find the inner plan's column matching *col_name* for correlation."""
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
                        inner_table = self._resolve_table(col.table or "")
                        if inner_table in self.instance.tables:
                            matched = self._match_column(inner_table, col.name)
                            if matched:
                                return f"{inner_table}.{matched}"
            if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
                table = self._resolve_table(step.source.name)
                if table in self.instance.tables:
                    matched = self._match_column(table, col_name)
                    if matched:
                        return f"{table}.{matched}"
            stack.extend(step.chain_dependencies)
        return None

    # -----------------------------------------------------------------
    # Column equality extraction (for Union-Find)
    # -----------------------------------------------------------------

    def _extract_column_equalities(self, condition: exp.Expression, spec: BranchSpec):
        """Extract col1 = col2 patterns and link them via Union-Find."""
        for eq_node in condition.find_all(exp.EQ):
            if eq_node.find_ancestor(exp.Exists) is not None or eq_node.find_ancestor(exp.Subquery) is not None:
                continue
            left, right = eq_node.this, eq_node.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                lt = self._resolve_table(left.table or "")
                lc = self._match_column(lt, left.name)
                rt = self._resolve_table(right.table or "")
                rc = self._match_column(rt, right.name)
                if lc and rc and lt and rt:
                    spec.require(lt)
                    spec.require(rt)
                    spec.equate(f"{lt}.{lc}", f"{rt}.{rc}")

    # -----------------------------------------------------------------
    # HAVING helpers
    # -----------------------------------------------------------------

    def _extract_agg_and_threshold(self, node: exp.Expression):
        """Return (agg_expr, threshold, op_class) from a comparison node.

        Handles both orientations: ``agg(col) > N`` and ``N < agg(col)``.
        Returns (None, None, None) if no aggregate is found.
        """
        left_has_agg = node.this.find((exp.Avg, exp.Sum, exp.Count))
        if left_has_agg:
            return node.this, concrete(node.expression), type(node)
        right_has_agg = node.expression.find((exp.Avg, exp.Sum, exp.Count))
        if right_has_agg:
            return node.expression, concrete(node.this), type(node)
        return None, None, None

    def _find_counted_table(self, condition: exp.Expression) -> Optional[str]:
        """Find the table containing the column inside COUNT(col) in a HAVING comparison."""
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for comp_node in agg_expr.find_all(_COMPARISON_NODES):
                        agg_side, _, _ = self._extract_agg_and_threshold(comp_node)
                        if agg_side is None:
                            continue
                        for count_node in agg_side.find_all(exp.Count):
                            if isinstance(count_node.this, exp.Star):
                                continue
                            if count_node.args.get("distinct"):
                                continue
                            for col in count_node.find_all(exp.Column):
                                table = self._resolve_table(col.table or "")
                                if table and table in self.instance.tables:
                                    return table
        # Fallback: check the HAVING condition directly.
        for comp_node in condition.find_all(_COMPARISON_NODES):
            agg_side, _, _ = self._extract_agg_and_threshold(comp_node)
            if agg_side is None:
                continue
            for count_node in agg_side.find_all(exp.Count):
                if isinstance(count_node.this, exp.Star):
                    continue
                for col in count_node.find_all(exp.Column):
                    table = self._resolve_table(col.table or "")
                    if table and table in self.instance.tables:
                        return table
        return None

    def _extract_min_group_size(self, condition: exp.Expression) -> int:
        """Extract minimum group size from HAVING (e.g., COUNT(*) > 3 → 4)."""
        result = 1
        result = max(result, self._min_group_from_expr(condition))
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    result = max(result, self._min_group_from_expr(agg_expr))
        return result

    def _min_group_from_expr(self, expr: exp.Expression) -> int:
        """Extract min group size from a single expression."""
        for node in expr.find_all(_COMPARISON_NODES):
            agg_side, threshold, op_class = self._extract_agg_and_threshold(node)
            if agg_side is None or not isinstance(threshold, (int, float)):
                continue
            if not agg_side.find(exp.Count):
                continue
            if op_class is exp.GT:
                return int(threshold) + 1
            if op_class is exp.GTE:
                return int(threshold)
            if op_class is exp.EQ:
                return int(threshold)
            # LT/LTE: no lower bound on group size needed.
        return 1

    # -----------------------------------------------------------------
    # Scalar subquery detection
    # -----------------------------------------------------------------

    def _iter_scalar_subquery_atoms(self, predicate: exp.Expression):
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
            if predicate.find(exp.Subquery) and isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
                yield predicate

    # -----------------------------------------------------------------
    # Negation subsets
    # -----------------------------------------------------------------

    @staticmethod
    def _negation_subsets(n: int) -> List[Tuple[int, ...]]:
        """Generate all non-empty subsets of range(n) as tuples.

        For n=2: [(0,), (1,), (0,1)]
        For n=3: [(0,), (1,), (2,), (0,1), (0,2), (1,2), (0,1,2)]
        """
        from itertools import combinations
        result = []
        for size in range(1, n + 1):
            for combo in combinations(range(n), size):
                result.append(combo)
        return result

    # -----------------------------------------------------------------
    # DISTINCT detection
    # -----------------------------------------------------------------

    def _has_distinct_projection(self) -> bool:
        """Check if any Project step has DISTINCT in its projections."""
        for step in self.plan.ordered_steps:
            if isinstance(step, Project):
                # Check if the step has DISTINCT set
                if getattr(step, "distinct", False):
                    return True
                # Also check projections for DISTINCT flag
                for proj in (step.projections or []):
                    if isinstance(proj, exp.Distinct):
                        return True
        return False

    # -----------------------------------------------------------------
    # CASE WHEN arm coverage
    # -----------------------------------------------------------------

    def _collect_case_when_conditions(self) -> List[List[exp.Expression]]:
        """Collect WHEN conditions from all CASE expressions in the plan.

        Returns a list of lists, where each inner list contains the WHEN
        conditions for one CASE expression.  Negating all conditions in a
        list forces the ELSE arm to fire.
        """
        result: List[List[exp.Expression]] = []
        for step in self.plan.ordered_steps:
            # Gather expressions from conditions, projections, aggregations.
            expressions: List[exp.Expression] = []
            condition = getattr(step, "condition", None)
            if condition is not None:
                expressions.append(condition)
            for proj in (getattr(step, "projections", None) or []):
                if isinstance(proj, exp.Expression):
                    expressions.append(proj)
            for agg in (getattr(step, "aggregations", None) or []):
                if isinstance(agg, exp.Expression):
                    expressions.append(agg)
            for expr in expressions:
                for case_expr in expr.find_all(exp.Case):
                    conditions = []
                    case_operand = case_expr.this
                    for if_node in (case_expr.args.get("ifs") or []):
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

    def _collect_case_when_positive_conditions(self) -> List[List[exp.Expression]]:
        """Collect CASE WHEN conditions that can produce positive output rows."""
        return self._collect_case_when_conditions()

    # -----------------------------------------------------------------
    # Utilities
    # -----------------------------------------------------------------

    def _projected_columns(self, step: Project) -> List[str]:
        cols: List[str] = []
        for proj in step.projections:
            if isinstance(proj, exp.Expression):
                for col in proj.find_all(exp.Column):
                    cols.append(col.name)
        return cols

    def _find_agg_column(self, expr: exp.Expression, agg_type) -> Optional[exp.Column]:
        """Find the column inside an aggregate function."""
        for agg in expr.find_all(agg_type):
            for col in agg.find_all(exp.Column):
                return col
        return None

    # -----------------------------------------------------------------
    # Self-join handling
    # -----------------------------------------------------------------

    def _find_self_join_tables(self) -> Dict[str, List[str]]:
        """Get mapping of physical table -> list of aliases for self-joined tables."""
        if hasattr(self.alias_map, 'self_join_tables'):
            return self.alias_map.self_join_tables()
        from collections import defaultdict
        groups: Dict[str, List[str]] = defaultdict(list)
        for a, t in self.alias_map.items():
            groups[t].append(a)
        return {t: aliases for t, aliases in groups.items() if len(aliases) > 1}

    def _store_conjunct_for_self_join(self, conjunct: exp.Expression, spec: BranchSpec) -> bool:
        """Handle a single conjunct for self-join. Returns True if handled."""
        self_join_tables = self._find_self_join_tables()
        if not self_join_tables:
            return False

        # Find column references to self-joined aliases
        for col in conjunct.find_all(exp.Column):
            alias = normalize_name(col.table or "")
            table = self._resolve_table(alias)
            if table not in self_join_tables:
                continue
            col_name = self._match_column(table, col.name)
            if not col_name:
                continue
            req_key = f"{table}__{alias}"
            if req_key not in spec.requirements:
                spec.requirements[req_key] = TableConstraint(table=table, alias=alias)
            # Resolve column qualifiers to physical names so the solver
            # can annotate types and find variables.
            resolved = conjunct.copy()
            for c in resolved.find_all(exp.Column):
                if normalize_name(c.table or "") == alias:
                    c.set("table", exp.to_identifier(table))
            spec.requirements[req_key].constraints.append(resolved)
            # Also set fixed_values for backward compat if it's a simple EQ with literal
            if isinstance(conjunct, exp.EQ):
                left, right = conjunct.this, conjunct.expression
                lit = right if isinstance(left, exp.Column) and left is col else (left if isinstance(right, exp.Column) else None)
                if lit is not None:
                    val = concrete(lit)
                    if val is not None:
                        spec.requirements[req_key].fixed_values[col_name] = val
            # Handle LIKE for backward compat
            elif isinstance(conjunct, exp.Like):
                pat_node = conjunct.expression
                if isinstance(pat_node, exp.Literal) and pat_node.is_string:
                    pat = str(pat_node.this).replace("%", "x").replace("_", "a")
                    spec.requirements[req_key].fixed_values[col_name] = pat
            return True  # handled
        return False  # not a self-join conjunct

    # -----------------------------------------------------------------
    # HAVING value constraint derivation
    # -----------------------------------------------------------------

    def _extract_having_value_constraints(self, condition: exp.Expression, spec: BranchSpec, min_rows: int):
        """Derive per-row value constraints from HAVING aggregate thresholds.

        Supports GT, GTE, LT, LTE, and EQ comparisons against AVG, SUM,
        and SUM/COUNT aggregates.  Sets per-row values so the aggregate
        satisfies the threshold.
        """
        self._extract_agg_value_from_expr(condition, spec, min_rows)
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    self._extract_agg_value_from_expr(agg_expr, spec, min_rows)

    def _extract_agg_value_from_expr(self, expr: exp.Expression, spec: BranchSpec, min_rows: int):
        """Extract per-row value constraints from an aggregate comparison."""
        import math

        for node in expr.find_all(_COMPARISON_NODES):
            agg_side, threshold, op_class = self._extract_agg_and_threshold(node)
            if agg_side is None or not isinstance(threshold, (int, float)):
                continue
            target_col = None
            per_row_value = None
            if op_class in (exp.GT, exp.GTE):
                offset = 1 if op_class is exp.GT else 0
                if agg_side.find(exp.Avg):
                    target_col = self._find_agg_column(agg_side, exp.Avg)
                    per_row_value = int(threshold) + offset
                elif agg_side.find(exp.Sum) and agg_side.find(exp.Count):
                    target_col = self._find_agg_column(agg_side, exp.Sum)
                    per_row_value = int(threshold) + offset
                elif agg_side.find(exp.Sum):
                    target_col = self._find_agg_column(agg_side, exp.Sum)
                    per_row_value = int(threshold / max(min_rows, 1)) + offset
            elif op_class in (exp.LT, exp.LTE):
                if agg_side.find(exp.Avg):
                    target_col = self._find_agg_column(agg_side, exp.Avg)
                    per_row_value = int(threshold) - 1 if op_class is exp.LT else int(threshold)
                elif agg_side.find(exp.Sum):
                    target_col = self._find_agg_column(agg_side, exp.Sum)
                    per_row_value = 1
            elif op_class is exp.EQ:
                if agg_side.find(exp.Avg):
                    target_col = self._find_agg_column(agg_side, exp.Avg)
                    per_row_value = int(threshold)
                elif agg_side.find(exp.Sum):
                    target_col = self._find_agg_column(agg_side, exp.Sum)
                    per_row_value = math.ceil(threshold / max(min_rows, 1))

            if target_col and per_row_value is not None:
                table = self._resolve_table(target_col.table or "")
                matched = self._match_column(table, target_col.name)
                if matched and table in spec.requirements:
                    spec.require(table).fixed_values[matched] = per_row_value

    # -----------------------------------------------------------------
    # Temporal / age constraint handling
    # -----------------------------------------------------------------

    def _extract_temporal_age_constraints(self, condition: exp.Expression, spec: BranchSpec):
        """Handle year-difference patterns like STRFTIME('%Y','now') - STRFTIME('%Y', col) > N."""
        from datetime import date as _date
        for node in condition.find_all((exp.GT, exp.GTE)):
            threshold = concrete(node.expression)
            if not isinstance(threshold, (int, float)):
                continue
            left = node.this
            cols = list(left.find_all(exp.Column))
            if not cols:
                continue
            has_now = any(
                isinstance(n, exp.CurrentDate) or
                (isinstance(n, exp.Literal) and 'now' in str(n.this).lower())
                for n in left.walk()
            )
            if not has_now:
                continue
            for col in cols:
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if not matched or table not in self.instance.tables:
                    continue
                col_type = str(self.instance.tables[table].get(matched, "")).upper()
                if "DATE" in col_type or "TIME" in col_type or "birthday" in matched.lower() or "date" in matched.lower():
                    years_ago = int(threshold) + 1
                    old_date = _date.today().replace(year=_date.today().year - years_ago)
                    spec.require(table).fixed_values[matched] = old_date.isoformat()
                    break

    def _merge_date_values(self, existing: str, new: str) -> str:
        """Merge two date-like strings by combining their year/month/day components."""
        import re
        date_pat = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
        m_existing = date_pat.match(existing)
        m_new = date_pat.match(new)
        if not m_existing or not m_new:
            return new
        ey, em, ed = m_existing.groups()
        ny, nm, nd = m_new.groups()
        year = ey if ey != "2024" else ny
        month = em if em != "06" else nm
        day = ed if ed != "15" else nd
        return f"{year}-{month}-{day}"

    # -----------------------------------------------------------------
    # Inner filter table fix
    # -----------------------------------------------------------------

    def _fix_inner_filter_tables(self, inner_root, spec: BranchSpec):
        """Fix misqualified columns in inner subplan filters.

        sqlglot sometimes qualifies inner columns with the outer table name.
        If a fixed_value was assigned to an outer table but the column also
        exists in an inner table, move it to the inner table.
        """
        inner_tables = []
        visited = set()
        stack = [inner_root]
        while stack:
            step = stack.pop()
            if id(step) in visited:
                continue
            visited.add(id(step))
            if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
                t = self._resolve_table(step.source.name)
                if t in self.instance.tables:
                    inner_tables.append(t)
            stack.extend(step.chain_dependencies)

        if not inner_tables:
            return

        outer_tables = [t for t in spec.requirements if t not in inner_tables]
        for table in outer_tables:
            req = spec.requirements[table]
            cols_to_move = []
            for col, val in list(req.fixed_values.items()):
                for inner_t in inner_tables:
                    matched = self._match_column(inner_t, col)
                    if matched:
                        cols_to_move.append((col, val, inner_t, matched))
                        break
            for col, val, target_table, target_col in cols_to_move:
                del req.fixed_values[col]
                spec.require(target_table).fixed_values[target_col] = val

    # -----------------------------------------------------------------
    # Constraint deduplication helpers
    # -----------------------------------------------------------------

    def _has_equality_constraint(self, constraints: List[exp.Expression], col_name: str) -> bool:
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

    def _has_is_not_null(self, constraints: List[exp.Expression], col_name: str) -> bool:
        """Check if constraints already have IS NOT NULL for the given column."""
        for expr in constraints:
            if isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Not) and isinstance(expr.expression.this, exp.Null):
                for col in expr.find_all(exp.Column):
                    if col.name == col_name:
                        return True
        return False

    def _has_is_null(self, constraints: List[exp.Expression], col_name: str) -> bool:
        """Check if constraints already have IS NULL for the given column."""
        for expr in constraints:
            if isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Null):
                for col in expr.find_all(exp.Column):
                    if col.name == col_name:
                        return True
        return False

    def _is_synthetic_having_alias(self, condition: exp.Expression) -> bool:
        """Return True for planner-generated HAVING aggregate alias columns."""
        if not isinstance(condition, exp.Column):
            return False
        return normalize_name(condition.name).startswith("_h")

    def _gold_having_scalar_constraints(self, condition: exp.Expression) -> List[exp.Expression]:
        """Return non-aggregate HAVING predicates suitable for gold witnesses."""
        source = condition.copy()
        if self._is_synthetic_having_alias(condition):
            source = self._find_having_alias_expression(condition)
            if source is None:
                return []
        source = self._resolve_group_aliases(source)
        scalar_conditions: List[exp.Expression] = []
        for conjunct in self._split_conjuncts(source):
            if conjunct.find((exp.Avg, exp.Sum, exp.Count, exp.Min, exp.Max)):
                continue
            scalar_conditions.append(conjunct)
        return scalar_conditions

    def _find_having_alias_expression(self, condition: exp.Column) -> Optional[exp.Expression]:
        """Find the expression hidden behind a planner-generated HAVING alias."""
        alias = normalize_name(condition.name)
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            for agg_expr in step.aggregations:
                if isinstance(agg_expr, exp.Alias) and normalize_name(agg_expr.alias_or_name) == alias:
                    return agg_expr.this.copy()
        return None

    def _resolve_group_aliases(self, expression: exp.Expression) -> exp.Expression:
        """Replace planner group aliases like _g0 with their base expressions."""
        replacements: Dict[Tuple[str, str], exp.Expression] = {}
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            source = normalize_name(step.source or step.name or "")
            for alias, group_expr in step.group.items():
                replacements[(source, normalize_name(alias))] = group_expr.copy()
                replacements[("", normalize_name(alias))] = group_expr.copy()

        if not replacements:
            return expression

        def replace_group_alias(node):
            if not isinstance(node, exp.Column):
                return node
            table_key = normalize_name(node.table or "")
            col_key = normalize_name(node.name)
            replacement = replacements.get((table_key, col_key)) or replacements.get(("", col_key))
            return replacement.copy() if replacement is not None else node

        return expression.transform(replace_group_alias)


# =============================================================================
# Resolver: turn TableConstraints into concrete row values
# =============================================================================


class Resolver:
    """Turn TableRequirements into concrete row values.

    Delegates constraint satisfaction to the Solver (domain + SMT).
    Falls back to heuristic satisfaction when no solver is provided.
    """

    def __init__(
        self,
        instance: Instance,
        dialect: str = "sqlite",
        solver=None,
        fill_empty_rows: bool = False,
    ):
        self.instance = instance
        self.dialect = dialect
        self.solver = solver
        self.fill_empty_rows = fill_empty_rows

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        """Produce concrete rows for each table in the spec."""
        self._discover_fk_parents(spec)
        join_equalities = self._equivalences_to_join_equalities(spec)
        order = self._creation_order(spec)
        result: Dict[str, List[Dict[str, Any]]] = {}

        for table_key in order:
            if table_key not in spec.requirements:
                continue
            req = spec.requirements[table_key]
            physical = req.table
            if "__" in physical:
                physical = physical.split("__")[0]

            for i in range(req.min_rows):
                row = self._solve_row(physical, req, spec, join_equalities, result, row_index=i)
                if row:
                    result.setdefault(physical, []).append(row)

            # Generate boundary rows for edge-case testing.
            for boundary in req.boundary_rows:
                row = self._solve_boundary_row(physical, req, spec, join_equalities, result, boundary)
                if row:
                    result.setdefault(physical, []).append(row)

        if spec.deferred:
            self._resolve_deferred(spec, result)

        return result

    def _solve_row(self, table, req, spec, join_equalities, result, row_index=0):
        """Build a SolverConstraint and call solver.solve() to get a row."""
        if self.solver is None:
            return {}

        from parseval.solver.unified import SolverConstraint

        all_constraints = list(req.constraints)

        # Convert fixed_values to EQ constraints.
        for col_name, val in req.fixed_values.items():
            col_node = exp.Column(
                this=exp.to_identifier(col_name),
                table=exp.to_identifier(table),
            )
            self._annotate_col_type(col_node, table, col_name)
            # Check if there's already an EQ for this column.
            already_has_eq = any(
                isinstance(c, exp.EQ) and
                isinstance(c.this, exp.Column) and c.this.name == col_name
                for c in all_constraints
            )
            if not already_has_eq:
                all_constraints.append(exp.EQ(
                    this=col_node,
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))
        # Cross-table coordination: add EQ for join-relevant columns only
        pinned_join_eq_cols = set()
        for lt, lc, rt, rc in join_equalities:
            # Check if this join equality involves the current table
            if lt == table and rt in result and result[rt]:
                # Use the same row index if available, otherwise row 0.
                joined_row = min(row_index, len(result[rt]) - 1)
                val = result[rt][joined_row].get(rc)
                pinned_join_eq_cols.add(lc)
                col_node = exp.Column(
                    this=exp.to_identifier(lc),
                    table=exp.to_identifier(table),
                )
                self._annotate_col_type(col_node, table, lc)
                all_constraints.append(exp.EQ(
                    this=col_node,
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))
            elif rt == table and lt in result and result[lt]:
                joined_row = min(row_index, len(result[lt]) - 1)
                val = result[lt][joined_row].get(lc)
                pinned_join_eq_cols.add(rc)
                col_node = exp.Column(
                    this=exp.to_identifier(rc),
                    table=exp.to_identifier(table),
                )
                self._annotate_col_type(col_node, table, rc)
                all_constraints.append(exp.EQ(
                    this=col_node,
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))

        # For rows after the first, force duplicate_columns and group_key_columns
        # to match row 0's values as solver constraints (not post-solve fixups).
        if row_index > 0 and table in result and result[table]:
            base = result[table][0]
            # Force foreign key columns to differ from row 0 so joined tables
            # can have different key values (needed for DISTINCT testing).
            # Skip columns pinned by join equalities — NEQ would contradict.
            for lt, lc, rt, rc in join_equalities:
                fk_col = lc if lt == table else (rc if rt == table else None)
                if fk_col and fk_col in base and base[fk_col] is not None:
                    if fk_col in pinned_join_eq_cols:
                        continue
                    if fk_col not in (req.duplicate_columns or []):
                        col_node = exp.Column(
                            this=exp.to_identifier(fk_col),
                            table=exp.to_identifier(table),
                        )
                        self._annotate_col_type(col_node, table, fk_col)
                        all_constraints.append(exp.NEQ(
                            this=col_node,
                            expression=_make_literal(base[fk_col]),
                        ))
            base = result[table][0]
            # Force duplicate_columns to match row 0 (skip unique columns).
            for col in (req.duplicate_columns or []):
                if col in base and col in (self.instance.tables.get(table) or {}):
                    if table in self.instance.tables and self.instance.is_unique(table, col):
                        continue
                    col_node = exp.Column(
                        this=exp.to_identifier(col),
                        table=exp.to_identifier(table),
                    )
                    self._annotate_col_type(col_node, table, col)
                    val = base[col]
                    all_constraints.append(exp.EQ(
                        this=col_node,
                        expression=_make_literal(val) if val is not None else exp.Null(),
                    ))
            # Exclude all previously generated UNIQUE key values to avoid conflicts.
            # Skip columns pinned by join equalities.
            if table in self.instance.tables and table in result:
                for col_name in self.instance.tables[table]:
                    if not self.instance.is_unique(table, col_name):
                        continue
                    if col_name in pinned_join_eq_cols:
                        continue
                    for prev_row in result[table]:
                        val = prev_row.get(col_name)
                        if val is not None:
                            col_node = exp.Column(
                                this=exp.to_identifier(col_name),
                                table=exp.to_identifier(table),
                            )
                            self._annotate_col_type(col_node, table, col_name)
                            all_constraints.append(exp.NEQ(
                                this=col_node,
                                expression=_make_literal(val),
                            ))

        # Safety net: pre-evaluate any remaining subquery expressions
        # that weren't caught by the Propagator's deferred extraction.
        all_constraints = self._pre_lower_subqueries(all_constraints)

        # Filter out cross-table EQ expressions — the solver can't handle
        # them in the constraints list (target_tables only has one table).
        # They're already enforced via join_equalities.
        all_constraints = [
            c for c in all_constraints
            if not (
                isinstance(c, exp.EQ)
                and isinstance(c.this, exp.Column)
                and isinstance(c.expression, exp.Column)
                and c.this.table
                and c.expression.table
                and c.this.table != c.expression.table
            )
        ]

        constraint = SolverConstraint(
            target_tables=(table,),
            constraints=all_constraints,
            join_equalities=join_equalities,
        )
        solve_result = self.solver.solve(constraint)
        if solve_result.sat:
            # Extract columns for this table from flat "table.col" keys.
            prefix = f"{table}."
            row = {k[len(prefix):]: v for k, v in solve_result.assignments.items() if k.startswith(prefix)}
            if self.fill_empty_rows:
                defaults = self._minimal_non_null_row(table, req, result, row_index)
                defaults.update(row)
                return defaults
            return row
        logger.warning(
            "Solver failed for table=%s row_index=%d reason=%s constraints=%s",
            table, row_index, solve_result.reason,
            [c.sql() for c in all_constraints[:5]],
        )
        return {}

    def _minimal_non_null_row(self, table, req, result, row_index=0):
        """Return deterministic defaults when gold-mode solver has no scalar assignments."""
        schema = self.instance.tables.get(table)
        if not schema:
            return {}
        row = dict(req.fixed_values)
        seed = row_index + 1
        base_row = result.get(table, [{}])[0] if row_index > 0 else {}
        for col_name, col_type in schema.items():
            if col_name in row:
                continue
            if (
                col_name in req.group_key_columns
                and col_name in base_row
                and not self.instance.is_unique(table, col_name)
            ):
                row[col_name] = base_row[col_name]
                continue
            col_type_name = str(col_type).upper()
            if "INT" in col_type_name:
                row[col_name] = seed
            elif any(token in col_type_name for token in ("REAL", "FLOAT", "DOUBLE", "NUM", "DEC")):
                row[col_name] = float(seed)
            elif "BLOB" in col_type_name or "BYTE" in col_type_name:
                row[col_name] = f"val{seed}".encode()
            elif "DATE" in col_type_name or "TIME" in col_type_name:
                row[col_name] = "2000-01-01"
            else:
                row[col_name] = f"val{seed}"
        return row

    def _annotate_col_type(self, col_node: exp.Column, table: str, col_name: str) -> None:
        """Set .type on a Column node from the instance schema."""
        from parseval.dtype import DataType
        col_type_str = _lookup_col_type(self.instance, table, col_name)
        if col_type_str:
            try:
                col_node.type = DataType.build(col_type_str)
            except Exception:
                pass

    def _pre_lower_subqueries(self, constraints: List[exp.Expression]) -> List[exp.Expression]:
        """Evaluate subquery-containing constraints and substitute concrete values.

        Safety net for subqueries that weren't caught by the Propagator's
        deferred extraction.  Returns a new list with subqueries replaced
        by their evaluated results.
        """
        lowered: List[exp.Expression] = []
        for expr in constraints:
            if not expr.find(exp.Subquery):
                lowered.append(expr)
                continue
            # Try to evaluate the subquery and substitute.
            substituted = self._evaluate_and_substitute(expr)
            if substituted is not None:
                lowered.append(substituted)
            # If we can't evaluate, skip the constraint — don't pass
            # raw Subquery nodes to the solver.
        return lowered

    def _evaluate_and_substitute(self, expr: exp.Expression) -> Optional[exp.Expression]:
        """Evaluate subqueries in an expression and substitute concrete values."""
        # Handle comparison with subquery: col op (SELECT ...)
        if isinstance(expr, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            left, right = expr.this, expr.expression
            subq_side, outer_side = None, None
            if right and right.find(exp.Subquery):
                subq_side, outer_side = right, left
            elif left and left.find(exp.Subquery):
                subq_side, outer_side = left, right
            if subq_side is not None:
                result = self._evaluate_scalar_subquery(subq_side)
                if result is not None:
                    literal = _make_literal(result)
                    if subq_side is right:
                        return type(expr)(this=outer_side, expression=literal)
                    else:
                        return type(expr)(this=literal, expression=outer_side)
            return None
        # Handle IN with subquery: col IN (SELECT ...)
        if isinstance(expr, exp.In):
            subq = expr.find(exp.Subquery)
            if subq:
                values = self._evaluate_subquery_to_list(subq)
                if values is not None:
                    literals = [_make_literal(v) for v in values]
                    return exp.In(this=expr.this, expressions=literals)
            return None
        # For other expressions with subqueries, skip them.
        return None

    def _evaluate_subquery_to_list(self, subq_node: exp.Expression) -> Optional[list]:
        """Evaluate a subquery and return its results as a list of values."""
        subq = subq_node if isinstance(subq_node, exp.Subquery) else subq_node.find(exp.Subquery)
        if subq is None:
            return None
        inner_select = subq.this if isinstance(subq, exp.Subquery) else subq
        if not isinstance(inner_select, exp.Select):
            return None
        try:
            from parseval.plan.context import build_context_from_instance
            from parseval.plan.rex import Environment, concrete
            ctx = build_context_from_instance(self.instance, self.dialect)
            env = Environment(ctx)
            result_ctx = env.eval(inner_select)
            if result_ctx is None or not result_ctx.rows:
                return []
            # Return the first column of each row.
            values = []
            for row in result_ctx.rows:
                first_col = next(iter(row.columns.values()), None)
                if first_col is not None:
                    val = concrete(first_col)
                    values.append(val)
            return values
        except Exception:
            return None

    def _solve_boundary_row(self, table, req, spec, join_equalities, result, boundary):
        """Build a row with exact boundary values for edge-case testing."""
        if self.solver is None:
            return {}

        from parseval.solver.unified import SolverConstraint

        all_constraints = list(req.constraints)

        # Add EQ constraints for boundary column values.
        for col_name, val in boundary.items():
            col_node = exp.Column(
                this=exp.to_identifier(col_name),
                table=exp.to_identifier(table),
            )
            self._annotate_col_type(col_node, table, col_name)
            all_constraints.append(exp.EQ(
                this=col_node,
                expression=_make_literal(val) if val is not None else exp.Null(),
            ))

        # Cross-table coordination: add EQ for join-relevant columns.
        for lt, lc, rt, rc in join_equalities:
            if lt == table and rt in result and result[rt]:
                joined_row = result[rt][-1]
                val = joined_row.get(rc)
                col_node = exp.Column(
                    this=exp.to_identifier(lc),
                    table=exp.to_identifier(table),
                )
                self._annotate_col_type(col_node, table, lc)
                all_constraints.append(exp.EQ(
                    this=col_node,
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))
            elif rt == table and lt in result and result[lt]:
                joined_row = result[lt][-1]
                val = joined_row.get(lc)
                col_node = exp.Column(
                    this=exp.to_identifier(rc),
                    table=exp.to_identifier(table),
                )
                self._annotate_col_type(col_node, table, rc)
                all_constraints.append(exp.EQ(
                    this=col_node,
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))

        # Exclude existing UNIQUE values.
        if table in self.instance.tables and table in result:
            for col_name in self.instance.tables[table]:
                if self.instance.is_unique(table, col_name):
                    for prev_row in result[table]:
                        val = prev_row.get(col_name)
                        if val is not None:
                            col_node = exp.Column(
                                this=exp.to_identifier(col_name),
                                table=exp.to_identifier(table),
                            )
                            self._annotate_col_type(col_node, table, col_name)
                            all_constraints.append(exp.NEQ(
                                this=col_node,
                                expression=_make_literal(val),
                            ))

        all_constraints = self._pre_lower_subqueries(all_constraints)

        # Filter out cross-table EQ expressions — same as _solve_row.
        all_constraints = [
            c for c in all_constraints
            if not (
                isinstance(c, exp.EQ)
                and isinstance(c.this, exp.Column)
                and isinstance(c.expression, exp.Column)
                and c.this.table
                and c.expression.table
                and c.this.table != c.expression.table
            )
        ]

        constraint = SolverConstraint(
            target_tables=(table,),
            constraints=all_constraints,
            join_equalities=join_equalities,
        )
        solve_result = self.solver.solve(constraint)
        if solve_result.sat:
            prefix = f"{table}."
            return {k[len(prefix):]: v for k, v in solve_result.assignments.items() if k.startswith(prefix)}
        logger.debug(
            "Boundary solver failed for table=%s boundary=%s reason=%s",
            table, boundary, solve_result.reason,
        )
        return {}

    def _equivalences_to_join_equalities(self, spec):
        """Convert ColumnUnionFind equivalences to join_equalities tuples."""
        equalities = []
        for rep, members in spec.equivalences.groups().items():
            if len(members) >= 2:
                for i in range(len(members) - 1):
                    t1, c1 = members[i].split(".", 1)
                    t2, c2 = members[i + 1].split(".", 1)
                    equalities.append((t1, c1, t2, c2))
        return equalities

    def _discover_fk_parents(self, spec: BranchSpec) -> None:
        """Discover FK-referenced parent tables transitively.

        For each table in spec.requirements, walk its foreign keys and add
        parent tables as requirements (with min_rows=1).  Uses a while-loop
        so that grandparent (and deeper) tables are also discovered.
        """
        tables = list(spec.requirements.keys())
        i = 0
        while i < len(tables):
            table = tables[i]
            i += 1
            physical = table.split("__")[0] if "__" in table else table
            if physical not in self.instance.tables:
                continue
            for fk in self.instance.get_foreign_key(physical):
                ref = fk.args.get("reference")
                if ref:
                    ref_table_node = ref.find(exp.Table)
                    if ref_table_node:
                        ref_table = normalize_name(ref_table_node.name)
                        if ref_table not in spec.requirements and ref_table in self.instance.tables:
                            req = TableConstraint(table=ref_table, min_rows=1)
                            spec.requirements[ref_table] = req
                            tables.append(ref_table)

    def _creation_order(self, spec: BranchSpec) -> List[str]:
        tables = list(spec.requirements.keys())

        # Build dependency graph
        deps: Dict[str, Set[str]] = {t: set() for t in tables}
        for table in tables:
            # Get physical table name (strip alias suffix)
            physical = table.split("__")[0] if "__" in table else table
            if physical not in self.instance.tables:
                continue
            for fk in self.instance.get_foreign_key(physical):
                ref = fk.args.get("reference")
                if ref:
                    ref_table = ref.find(exp.Table)
                    if ref_table and normalize_name(ref_table.name) in deps:
                        deps[table].add(normalize_name(ref_table.name))

        # Topological sort
        ordered: List[str] = []
        ready = [t for t in tables if not deps[t]]
        while ready:
            t = ready.pop(0)
            ordered.append(t)
            for other in tables:
                if t in deps.get(other, set()):
                    deps[other].discard(t)
                    if not deps[other] and other not in ordered:
                        ready.append(other)
        for t in tables:
            if t not in ordered:
                ordered.append(t)
        return ordered

    def _resolve_deferred(self, spec: BranchSpec, result=None):
        """Evaluate deferred scalar subqueries and adjust outer rows.

        For atoms like `col > (SELECT AVG(val) FROM t)`:
        1. Evaluate the subquery against the current instance.
        2. Adjust the outer row's column value to satisfy the comparison.
        """
        for atom in spec.deferred:
            if not isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
                continue

            left, right = atom.this, atom.expression
            subq_side, outer_side = None, None
            if right and right.find(exp.Subquery):
                subq_side, outer_side = right, left
            elif left and left.find(exp.Subquery):
                subq_side, outer_side = left, right
            if subq_side is None:
                continue

            # Evaluate the subquery
            subq_result = self._evaluate_scalar_subquery(subq_side)
            if subq_result is None:
                continue

            # Find the outer column and adjust its value
            if not isinstance(outer_side, exp.Column):
                continue
            table = normalize_name(outer_side.table or "")
            col_name = outer_side.name
            if table not in self.instance.tables:
                continue

            rows = self.instance.get_rows(table)
            if not rows:
                continue

            # Compute the target value based on the comparison operator
            target = self._compute_target_value(atom, subq_result)
            if target is not None:
                # Adjust the first row
                if col_name in rows[0].columns:
                    rows[0][col_name].set("concrete", target)
                    rows[0][col_name].set("is_bound", True)

    def _evaluate_scalar_subquery(self, subq_node: exp.Expression) -> Optional[Any]:
        """Evaluate a scalar subquery against the current instance."""
        subq = subq_node if isinstance(subq_node, exp.Subquery) else subq_node.find(exp.Subquery)
        if subq is None:
            subq = subq_node
        inner_select = subq.this if isinstance(subq, exp.Subquery) else subq
        if not isinstance(inner_select, exp.Select):
            return None

        from_clause = inner_select.args.get("from")
        if not from_clause:
            return None
        from_table = from_clause.this
        if not isinstance(from_table, exp.Table):
            return None

        table_name = normalize_name(from_table.alias_or_name)
        if table_name not in self.instance.tables:
            return None

        rows = self.instance.get_rows(table_name)
        if not rows:
            return None

        projections = inner_select.expressions
        if not projections:
            return None

        proj = projections[0]
        values = []
        for row in rows:
            for col in proj.find_all(exp.Column):
                if col.name in row.columns:
                    v = row[col.name].concrete
                    if v is not None:
                        values.append(v)

        if not values:
            return None

        if proj.find(exp.Avg):
            return sum(values) / len(values)
        elif proj.find(exp.Count):
            return len(values)
        elif proj.find(exp.Sum):
            return sum(values)
        elif proj.find(exp.Max):
            return max(values)
        elif proj.find(exp.Min):
            return min(values)

        return values[0] if values else None

    def _compute_target_value(self, atom: exp.Expression, subq_result: Any) -> Optional[Any]:
        """Compute a value that satisfies the comparison atom."""
        if isinstance(atom, exp.GT):
            if isinstance(subq_result, (int, float)):
                return int(subq_result) + 1
        elif isinstance(atom, exp.GTE):
            if isinstance(subq_result, (int, float)):
                return int(subq_result)
        elif isinstance(atom, exp.LT):
            if isinstance(subq_result, (int, float)):
                return int(subq_result) - 1
        elif isinstance(atom, exp.LTE):
            if isinstance(subq_result, (int, float)):
                return int(subq_result)
        elif isinstance(atom, exp.EQ):
            return subq_result
        return None


# =============================================================================
# Top-level API
# =============================================================================


def _make_literal(val: Any) -> exp.Literal:
    """Create a sqlglot Literal from a Python value."""
    if isinstance(val, bool):
        return exp.Literal.number(1 if val else 0)
    if isinstance(val, int):
        return exp.Literal.number(val)
    if isinstance(val, float):
        return exp.Literal.number(val)
    return exp.Literal.string(str(val))


def _gold_non_empty_validation_sql(plan: Plan, dialect: str) -> str:
    """Render plan SQL with aggregate group aliases expanded for SQLite."""
    replacements: Dict[Tuple[str, str], exp.Expression] = {}
    for step in plan.ordered_steps:
        if not isinstance(step, Aggregate):
            continue
        source = normalize_name(step.source or step.name or "")
        for alias, group_expr in step.group.items():
            replacements[(source, normalize_name(alias))] = group_expr.copy()
            replacements[("", normalize_name(alias))] = group_expr.copy()

    if not replacements:
        return plan.expression.sql(dialect=dialect)

    expression = plan.expression.copy()

    def replace_group_alias(node):
        if not isinstance(node, exp.Column):
            return node
        table_key = normalize_name(node.table or "")
        col_key = normalize_name(node.name)
        replacement = replacements.get((table_key, col_key)) or replacements.get(("", col_key))
        return replacement.copy() if replacement is not None else node

    return expression.transform(replace_group_alias).sql(dialect=dialect)


def validate_gold_non_empty_rows(
    plan: Plan,
    instance: Instance,
    rows_per_table: Dict[str, List[Dict[str, Any]]],
    dialect: str = "sqlite",
) -> bool:
    """Return True when candidate rows make the plan SQL return rows in SQLite."""
    if dialect != "sqlite":
        return True

    sql = plan.expression.sql(dialect=dialect)
    conn = sqlite3.connect(":memory:")
    try:
        for ddl in instance.ddls.split(";"):
            ddl = ddl.strip()
            if ddl:
                conn.execute(ddl)

        for table_name, schema in instance.tables.items():
            cols = list(schema.keys())
            existing_rows = instance.get_rows(table_name)
            candidate_rows = rows_per_table.get(table_name, [])
            if not existing_rows and not candidate_rows:
                continue

            placeholders = ",".join(["?"] * len(cols))
            quoted_cols = ",".join(f'"{col}"' for col in cols)
            stmt = f'INSERT OR IGNORE INTO "{table_name}" ({quoted_cols}) VALUES ({placeholders})'

            for row in existing_rows:
                values = []
                for col in cols:
                    value = row[col].concrete if col in row.columns else None
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

            for row in candidate_rows:
                values = []
                for col in cols:
                    value = row.get(col)
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

        conn.commit()
        try:
            return bool(conn.execute(sql).fetchone())
        except sqlite3.OperationalError:
            validation_sql = _gold_non_empty_validation_sql(plan, dialect)
            if validation_sql == sql:
                raise
            return bool(conn.execute(validation_sql).fetchone())
    except Exception as exc:
        logger.debug("gold_non_empty validation failed: %s", exc)
        return False
    finally:
        conn.close()


def _build_gold_row_bindings(spec: BranchSpec) -> Dict[str, RowBinding]:
    bindings: Dict[str, RowBinding] = {}
    alias_scoped_tables = {
        normalize_name(req.table.split("__", 1)[0] if "__" in req.table else req.table)
        for table_key, req in spec.requirements.items()
        if req.alias or "__" in table_key
    }
    for table_key, req in spec.requirements.items():
        physical = normalize_name(
            req.table.split("__", 1)[0] if "__" in req.table else req.table
        )
        if physical in alias_scoped_tables and not req.alias and "__" not in table_key:
            continue
        if req.alias:
            alias = normalize_name(req.alias)
        elif "__" in table_key:
            alias = normalize_name(table_key.split("__", 1)[1])
        else:
            alias = physical
        for row_index in range(max(req.min_rows, 1)):
            binding = RowBinding(table=physical, alias=alias, row=row_index)
            bindings[_solver_table_key(binding)] = binding
    return bindings


def _bindings_for_requirement(
    table_key: str,
    req: TableConstraint,
    row_bindings: Dict[str, RowBinding],
) -> List[RowBinding]:
    physical = normalize_name(req.table.split("__", 1)[0] if "__" in req.table else req.table)
    if req.alias:
        alias = normalize_name(req.alias)
    elif "__" in table_key:
        alias = normalize_name(table_key.split("__", 1)[1])
    else:
        alias = physical
    return [
        binding
        for binding in row_bindings.values()
        if binding.table == physical and normalize_name(binding.alias or "") == alias
    ]


def _binding_for_member(
    member_table: str,
    member_column: str,
    row_bindings: Dict[str, RowBinding],
    alias_map,
) -> Optional[RowBinding]:
    table_key = normalize_name(member_table)
    if "__" in table_key:
        physical, alias = table_key.split("__", 1)
        for binding in row_bindings.values():
            if (
                binding.table == physical
                and normalize_name(binding.alias or "") == alias
                and binding.row == 0
            ):
                return binding
        return None
    return _binding_for_column(
        exp.column(member_column, table_key),
        row_bindings,
        alias_map,
    )


def _row_scoped_join_equalities(
    spec: BranchSpec,
    row_bindings: Dict[str, RowBinding],
    alias_map,
) -> List[Tuple[str, str, str, str]]:
    equalities: List[Tuple[str, str, str, str]] = []
    seen: Set[Tuple[str, str, str, str]] = set()
    for _rep, members in spec.equivalences.groups().items():
        if len(members) < 2:
            continue
        scoped: List[Tuple[str, str]] = []
        for member in members:
            table_name, column_name = member.split(".", 1)
            binding = _binding_for_member(table_name, column_name, row_bindings, alias_map)
            if binding is not None:
                scoped.append((_solver_table_key(binding), normalize_name(column_name)))
        for left, right in zip(scoped, scoped[1:]):
            equality = (left[0], left[1], right[0], right[1])
            if equality in seen:
                continue
            seen.add(equality)
            equalities.append(equality)
    return equalities


def _join_column_type_constraints(
    instance: Instance,
    row_bindings: Dict[str, RowBinding],
    join_equalities: List[Tuple[str, str, str, str]],
) -> List[exp.Expression]:
    from parseval.dtype import DataType

    constraints: List[exp.Expression] = []
    seen: Set[Tuple[str, str]] = set()
    for left_table, left_col, right_table, right_col in join_equalities:
        for table_key, column_name in ((left_table, left_col), (right_table, right_col)):
            key = (normalize_name(table_key), normalize_name(column_name))
            if key in seen:
                continue
            seen.add(key)
            binding = row_bindings.get(key[0])
            if binding is None:
                continue
            col_node = exp.column(key[1], key[0])
            col_type_str = _lookup_col_type(instance, binding.table, key[1])
            if col_type_str:
                try:
                    col_node.type = DataType.build(col_type_str)
                except Exception:
                    pass
            constraints.append(exp.Is(this=col_node, expression=exp.Not(this=exp.Null())))
    return constraints


def _build_gold_solver_constraint(
    spec: BranchSpec,
    instance: Instance,
    alias_map,
) -> Tuple[SolverConstraint, Dict[str, RowBinding]]:
    row_bindings = _build_gold_row_bindings(spec)
    constraints: List[exp.Expression] = []
    for table_key, req in spec.requirements.items():
        req_bindings = _bindings_for_requirement(table_key, req, row_bindings)
        if not req_bindings:
            continue
        for constraint in req.constraints:
            if constraint.find(exp.Subquery):
                continue
            if (
                isinstance(constraint, exp.EQ)
                and isinstance(constraint.this, exp.Column)
                and isinstance(constraint.expression, exp.Column)
            ):
                continue
            for binding in req_bindings:
                scoped_bindings = {_solver_table_key(binding): binding}
                rewritten = _rewrite_expr_for_row_scope(
                    constraint,
                    scoped_bindings,
                    alias_map,
                    default_row=binding.row,
                )
                constraints.append(rewritten)
    join_equalities = _row_scoped_join_equalities(spec, row_bindings, alias_map)
    constraints.extend(_join_column_type_constraints(instance, row_bindings, join_equalities))
    return SolverConstraint(
        target_tables=tuple(row_bindings.keys()),
        constraints=constraints,
        join_equalities=join_equalities,
    ), row_bindings


def _materialize_rows(
    instance: Instance,
    rows: Dict[str, List[Dict[str, Any]]],
) -> None:
    concretes = {
        table: {
            col: [row.get(col) for row in table_rows]
            for col in instance.tables.get(table, {})
        }
        for table, table_rows in rows.items()
    }
    for table_name in instance._creation_order(concretes):
        for row in rows.get(table_name, []):
            instance.create_row(table_name, values=row)


def _solve_and_materialize_gold(
    spec: BranchSpec,
    plan: Plan,
    instance: Instance,
    solver,
    alias_map,
    dialect: str,
) -> Dict[str, List[Dict[str, Any]]]:
    constraint, row_bindings = _build_gold_solver_constraint(spec, instance, alias_map)
    result = solver.solve(constraint)
    if not result.sat:
        return {}
    rows = _rows_from_solver_assignments(result.assignments, row_bindings, instance)
    checkpoint = instance.checkpoint()
    try:
        _materialize_rows(instance, rows)
        if validate_gold_non_empty_rows(plan, instance, {}, dialect=dialect):
            return rows
        instance.rollback(checkpoint)
        return {}
    except Exception:
        instance.rollback(checkpoint)
        return {}


def speculate(
    plan: Plan,
    instance: Instance,
    alias_map,
    dialect: str = "sqlite",
    objective: str = "branch_coverage",
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve → list of (branch_name, rows_per_table).

    Returns one entry per branch (positive + negatives). The engine
    materializes each one.
    """
    from parseval.solver.unified import Solver
    propagator = Propagator(plan, instance, alias_map, dialect, objective=objective)
    solver = Solver(dialect=dialect)
    resolver = Resolver(
        instance,
        dialect,
        solver=solver,
        fill_empty_rows=objective == "gold_non_empty",
    )
    if objective == "gold_non_empty":
        branch_specs = propagator.propagate_gold_non_empty()
        logger.info("Generated %d branch specs", len(branch_specs))
        results = []
        for spec in branch_specs:
            if not spec.requirements:
                continue
            rows = _solve_and_materialize_gold(
                spec,
                plan,
                instance,
                solver,
                alias_map,
                dialect,
            )
            if rows:
                results.append((spec.branch, rows))
        return results
    else:
        branch_specs = propagator.propagate()
    logger.info("Generated %d branch specs", len(branch_specs))

    results = []
    for spec in branch_specs:
        if spec.requirements:
            rows = resolver.resolve(spec)
            if objective == "gold_non_empty" and not validate_gold_non_empty_rows(
                plan,
                instance,
                rows,
                dialect=dialect,
            ):
                logger.debug("dropping gold_non_empty spec with empty result: %s", spec.branch)
                continue
            results.append((spec.branch, rows))
    return results


# Also keep backward-compatible names used by engine.
def build_spec(plan, instance, *, alias_map, target_outcome="positive", negate_atom=None):
    """Backward-compatible wrapper."""
    propagator = Propagator(plan, instance, alias_map, dialect="sqlite")
    if target_outcome == "positive":
        specs = propagator.propagate()
        return specs[0] if specs else BranchSpec(branch="positive")
    return BranchSpec(branch=target_outcome)


def resolve_spec(spec, instance, dialect="sqlite"):
    """Backward-compatible wrapper."""
    from parseval.solver.unified import Solver
    solver = Solver(dialect=dialect)
    resolver = Resolver(instance, dialect, solver=solver)
    rows = resolver.resolve(spec)
    # Flatten to {table: first_row_values}
    return {table: row_list[0] if row_list else {} for table, row_list in rows.items()}


# Keep SharedKey and UNSET for backward compat with engine imports.
@dataclass
class SharedKey:
    key_id: str

UNSET = object()

SpeculativeSpec = BranchSpec


__all__ = [
    "BranchSpec",
    "Propagator",
    "Resolver",
    "SharedKey",
    "SpeculativeSpec",
    "TableConstraint",
    "TableRequirement",
    "UNSET",
    "build_spec",
    "resolve_spec",
    "speculate",
    "validate_gold_non_empty_rows",
]
