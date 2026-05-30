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

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.planner import (
    Aggregate, Filter, Having, Join, Limit, Project, Scan, Sort, SubPlan,
)
from parseval.plan.rex import column_meta, concrete, negate_predicate
from parseval.solver.lowering import (
    ColumnPredicate,
    ColumnUnionFind,
    lower_predicates,
    match_column as _match_col,
    negate_predicate_value,
)


# =============================================================================
# Data structures
# =============================================================================


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


# ColumnUnionFind imported from solver.lowering


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


class Propagator:
    """Walk the Plan top-down, deriving table requirements for each branch.

    The new Propagator stores constraints as ``exp.Expression`` objects
    directly, instead of lowering to ``(col, op, value)`` tuples.
    """

    def __init__(self, plan: Plan, instance: Instance, alias_map, dialect: str):
        self.plan = plan
        self.instance = instance
        self.alias_map = alias_map
        self.dialect = dialect

    def _resolve_table(self, name: str) -> str:
        """Resolve alias or table name to physical table name."""
        if not name:
            return ""
        real = self.alias_map.resolve(name)
        return real if real in self.instance.tables else name

    def _match_column(self, table: str, col_name: str) -> Optional[str]:
        return _match_col(self.instance, table, col_name)

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
        """Produce specs for positive + all negative branches."""
        specs = []
        # Positive path.
        pos = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, pos)
        self._add_schema_constraints(pos)
        self._annotate_column_types(pos)
        specs.append(pos)
        # Negative branches per decision site.
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                neg = BranchSpec(branch="negative")
                self._propagate_step(self.plan.root, neg, negate_step=step)
                self._add_schema_constraints(neg)
                self._annotate_column_types(neg)
                specs.append(neg)
            elif isinstance(step, Join):
                left_un = BranchSpec(branch="left_unmatched")
                self._propagate_unmatched_left(step, left_un)
                self._add_schema_constraints(left_un)
                self._annotate_column_types(left_un)
                specs.append(left_un)
            elif isinstance(step, Having) and step.condition:
                fail = BranchSpec(branch="having_fail")
                self._propagate_step(self.plan.root, fail, negate_step=step)
                self._add_schema_constraints(fail)
                self._annotate_column_types(fail)
                specs.append(fail)
        return specs

    # -----------------------------------------------------------------
    # Recursive step propagation
    # -----------------------------------------------------------------

    def _propagate_step(self, step: Step, spec: BranchSpec, negate_step: Optional[Step] = None):
        """Recursively propagate requirements top-down."""
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            needed = offset + int(limit_val)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
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
                self._propagate_step(dep, spec, negate_step)
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

        elif isinstance(step, Sort):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition and step is not negate_step:
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
                self._propagate_step(dep, spec, negate_step)
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
            for agg_expr in step.aggregations:
                self._add_aggregate_null_constraints(agg_expr, spec)

        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition:
                if step is negate_step:
                    # For AND: negate first conjunct, keep rest as positive.
                    conjuncts = self._split_conjuncts(step.condition)
                    if len(conjuncts) > 1:
                        negated = negate_predicate(conjuncts[0].copy())
                        self._store_expression(negated, spec)
                        for other in conjuncts[1:]:
                            self._store_expression(other, spec)
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
                self._propagate_step(dep, spec, negate_step)
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

        elif isinstance(step, Scan):
            table = self._resolve_table(step.name)
            if table in self.instance.tables:
                spec.require(table)
            # For FROM-subquery scans, propagate into the SubPlan's inner plan.
            for sub in step.subplan_dependencies:
                if sub.inner:
                    self._propagate_step(sub.inner, spec, negate_step)

        # Handle SubPlan dependencies.
        for sub in step.subplan_dependencies:
            self._propagate_subplan(sub, spec)

    # -----------------------------------------------------------------
    # Expression storage
    # -----------------------------------------------------------------

    def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
        """Decompose AND, resolve columns, store per-table."""
        conjuncts = self._split_conjuncts(expr)
        for conjunct in conjuncts:
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

    def _resolve_columns(self, expr: exp.Expression) -> exp.Expression:
        """Resolve column table qualifiers to physical table names."""
        for col in expr.find_all(exp.Column):
            if col.table:
                resolved = self._resolve_table(col.table)
                if resolved and resolved in self.instance.tables and resolved != col.table:
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
    # Column type annotation
    # -----------------------------------------------------------------

    def _annotate_column_types(self, spec: BranchSpec):
        """Set .type on Column nodes from column_meta or instance schema."""
        from parseval.dtype import DataType

        for table_key, tc in spec.requirements.items():
            for constraint in tc.constraints:
                for col in constraint.find_all(exp.Column):
                    meta = column_meta(col)
                    if meta and "domain" in meta:
                        col.set("type", meta["domain"])
                    else:
                        col_table = self._resolve_table(col.table or table_key)
                        if col_table and col_table in self.instance.tables:
                            col_type_str = self.instance.tables[col_table].get(col.name)
                            if col_type_str:
                                try:
                                    col.set("type", DataType.build(col_type_str))
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

    # -----------------------------------------------------------------
    # Join / SubPlan handling
    # -----------------------------------------------------------------

    def _propagate_unmatched_left(self, join_step: Join, spec: BranchSpec):
        """Generate a left-table row with no matching right-table row."""
        source = self._resolve_table(join_step.source_name or join_step.name)
        if source in self.instance.tables:
            spec.require(source)

    def _propagate_subplan(self, sub: SubPlan, spec: BranchSpec):
        """Handle EXISTS/IN/SCALAR subplan correlation."""
        if sub.kind.value == "exists" and sub.correlation:
            for corr_col in sub.correlation:
                outer_table = self._resolve_table(corr_col.table or "")
                matched = self._match_column(outer_table, corr_col.name)
                if matched:
                    spec.require(outer_table)
                    outer_key = f"{outer_table}.{matched}"
                    inner_key = self._find_inner_corr_column(sub, spec)
                    if inner_key:
                        spec.equate(outer_key, inner_key)

        elif sub.kind.value == "in":
            self._propagate_in_subplan(sub, spec)

        elif sub.kind.value == "scalar":
            self._propagate_scalar_subplan(sub, spec)

        # Always propagate into inner plan for WHERE constraints.
        if sub.inner:
            self._propagate_step(sub.inner, spec)
            self._fix_inner_filter_tables(sub.inner, spec)

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

    # -----------------------------------------------------------------
    # Column equality extraction (for Union-Find)
    # -----------------------------------------------------------------

    def _extract_column_equalities(self, condition: exp.Expression, spec: BranchSpec):
        """Extract col1 = col2 patterns and link them via Union-Find."""
        for eq_node in condition.find_all(exp.EQ):
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

    def _find_counted_table(self, condition: exp.Expression) -> Optional[str]:
        """Find the table containing the column inside COUNT(col) in a HAVING comparison."""
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for comp_node in agg_expr.find_all((exp.GT, exp.GTE)):
                        count_side = comp_node.this
                        for count_node in count_side.find_all(exp.Count):
                            if isinstance(count_node.this, exp.Star):
                                continue
                            if count_node.args.get("distinct"):
                                continue
                            for col in count_node.find_all(exp.Column):
                                table = self._resolve_table(col.table or "")
                                if table and table in self.instance.tables:
                                    return table
        # Fallback: check the HAVING condition directly.
        for comp_node in condition.find_all((exp.GT, exp.GTE)):
            count_side = comp_node.this
            for count_node in count_side.find_all(exp.Count):
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
        for node in expr.find_all(exp.GT):
            if node.this.find(exp.Count):
                val = concrete(node.expression)
                if isinstance(val, (int, float)):
                    return int(val) + 1
        for node in expr.find_all(exp.GTE):
            if node.this.find(exp.Count):
                val = concrete(node.expression)
                if isinstance(val, (int, float)):
                    return int(val)
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
        else:
            if predicate.find(exp.Subquery) and isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
                yield predicate

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
            # Store the expression on the alias-specific key
            spec.requirements[req_key].constraints.append(conjunct)
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

        For HAVING SUM(col)/COUNT(*) > N or HAVING AVG(col) > N:
        set each row's col value to N+1 so the aggregate exceeds the threshold.
        For HAVING SUM(col) > N: set each row's col to ceil(N/min_rows)+1.
        """
        self._extract_agg_value_from_expr(condition, spec, min_rows)
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    self._extract_agg_value_from_expr(agg_expr, spec, min_rows)

    def _extract_agg_value_from_expr(self, expr: exp.Expression, spec: BranchSpec, min_rows: int):
        """Extract per-row value constraints from an aggregate comparison."""
        for node in expr.find_all((exp.GT, exp.GTE)):
            agg_side = node.this
            threshold_side = node.expression
            threshold = concrete(threshold_side)
            if not isinstance(threshold, (int, float)):
                continue
            target_col = None
            per_row_value = None
            if agg_side.find(exp.Avg):
                target_col = self._find_agg_column(agg_side, exp.Avg)
                per_row_value = int(threshold) + 1
            elif agg_side.find(exp.Sum) and agg_side.find(exp.Count):
                target_col = self._find_agg_column(agg_side, exp.Sum)
                per_row_value = int(threshold) + 1
            elif agg_side.find(exp.Sum):
                target_col = self._find_agg_column(agg_side, exp.Sum)
                per_row_value = int(threshold / max(min_rows, 1)) + 1

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


# =============================================================================
# Resolver: turn TableConstraints into concrete row values
# =============================================================================


class Resolver:
    """Turn TableRequirements into concrete row values.

    Delegates constraint satisfaction to the Solver (domain + SMT).
    Falls back to heuristic satisfaction when no solver is provided.
    """

    def __init__(self, instance: Instance, dialect: str = "sqlite", solver=None):
        self.instance = instance
        self.dialect = dialect
        self.solver = solver

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
                row = self._solve_row(physical, req, spec, join_equalities, result)
                if row:
                    if req.duplicate_columns and i > 0 and physical in result:
                        base = result[physical][0]
                        for col in req.duplicate_columns:
                            if col in base:
                                row[col] = base[col]
                    if req.group_key_columns and i > 0 and physical in result:
                        base = result[physical][0]
                        for col in req.group_key_columns:
                            if col in base:
                                row[col] = base[col]
                    result.setdefault(physical, []).append(row)

        if spec.deferred:
            self._resolve_deferred(spec, result)

        return result

    def _solve_row(self, table, req, spec, join_equalities, result):
        """Build a SolverConstraint and call solver.solve() to get a row."""
        if self.solver is None:
            return self._fallback_row(table, req)

        from parseval.solver.unified import SolverConstraint

        all_constraints = list(req.constraints)
        # Cross-table coordination: add EQ for join-relevant columns only
        for lt, lc, rt, rc in join_equalities:
            # Check if this join equality involves the current table
            if lt == table and rt in result and result[rt]:
                val = result[rt][0].get(rc)
                all_constraints.append(exp.EQ(
                    this=exp.Column(
                        this=exp.to_identifier(lc),
                        table=exp.to_identifier(table),
                    ),
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))
            elif rt == table and lt in result and result[lt]:
                val = result[lt][0].get(lc)
                all_constraints.append(exp.EQ(
                    this=exp.Column(
                        this=exp.to_identifier(rc),
                        table=exp.to_identifier(table),
                    ),
                    expression=_make_literal(val) if val is not None else exp.Null(),
                ))

        constraint = SolverConstraint(
            target_tables=(table,),
            constraints=all_constraints,
            join_equalities=join_equalities,
        )
        solve_result = self.solver.solve(constraint)
        if solve_result.sat:
            return solve_result.assignments.get(table, {})
        return {}

    def _fallback_row(self, table, req):
        """Simple row generation when no solver is provided (backward compat)."""
        row: Dict[str, Any] = {}
        row.update(req.fixed_values)
        for col in self.instance.tables.get(table, {}):
            if col not in row:
                col_type = str(self.instance.tables[table].get(col, "TEXT")).upper()
                if "INT" in col_type:
                    row[col] = 1
                else:
                    row[col] = "val"
        for col in req.must_null:
            row[col] = None
        return row

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


def speculate(
    plan: Plan,
    instance: Instance,
    alias_map,
    dialect: str = "sqlite",
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve → list of (branch_name, rows_per_table).

    Returns one entry per branch (positive + negatives). The engine
    materializes each one.
    """
    from parseval.solver.unified import Solver
    propagator = Propagator(plan, instance, alias_map, dialect)
    solver = Solver(dialect=dialect)
    resolver = Resolver(instance, dialect, solver=solver)
    branch_specs = propagator.propagate()

    results = []
    for spec in branch_specs:
        if spec.requirements:
            rows = resolver.resolve(spec)
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
]
