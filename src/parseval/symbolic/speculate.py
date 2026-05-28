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
from parseval.plan.rex import concrete
from parseval.solver.lowering import (
    ColumnPredicate,
    ColumnUnionFind,
    lower_predicates,
    match_column as _match_col,
    negate_predicate_value,
    resolve_table as _resolve_col_table,
    resolve_table_name as _resolve_tbl,
)


# =============================================================================
# Data structures
# =============================================================================


@dataclass
class TableRequirement:
    """What one table needs to contribute for a specific branch."""
    table: str  # physical table name
    alias: Optional[str] = None  # alias (for self-joins, distinguishes rows)
    min_rows: int = 1
    fixed_values: Dict[str, Any] = field(default_factory=dict)
    not_null: Set[str] = field(default_factory=set)
    must_null: Set[str] = field(default_factory=set)
    predicates: List[Tuple[str, str, Any]] = field(default_factory=list)
    duplicate_columns: List[str] = field(default_factory=list)
    group_key_columns: List[str] = field(default_factory=list)


# ColumnUnionFind imported from solver.lowering


@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""
    branch: str
    requirements: Dict[str, TableRequirement] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)

    def require(self, table: str) -> TableRequirement:
        if table not in self.requirements:
            self.requirements[table] = TableRequirement(table=table)
        return self.requirements[table]

    def equate(self, col_a: str, col_b: str) -> None:
        """Declare two columns must have the same value."""
        self.equivalences.union(col_a, col_b)


# =============================================================================
# Propagator: top-down constraint derivation
# =============================================================================


class Propagator:
    """Walk the Plan top-down, deriving table requirements for each branch."""

    def __init__(self, plan: Plan, instance: Instance, alias_map: Dict[str, str], dialect: str):
        self.plan = plan
        self.instance = instance
        self.alias_map = alias_map
        self.dialect = dialect
        self._key_counter = 0

    def _next_key(self) -> str:
        self._key_counter += 1
        return f"k{self._key_counter}"

    def _resolve_table(self, name: str) -> str:
        if not name:
            return ""
        return _resolve_tbl(name, self.instance, self.alias_map)

    def _match_column(self, table: str, col_name: str) -> Optional[str]:
        return _match_col(self.instance, table, col_name)

    def propagate(self) -> List[BranchSpec]:
        """Produce specs for positive + all negative branches."""
        specs = []
        # Positive path.
        pos = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, pos)
        specs.append(pos)
        # Negative branches per decision site.
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                neg = BranchSpec(branch="negative")
                self._propagate_step(self.plan.root, neg, negate_step=step)
                specs.append(neg)
            elif isinstance(step, Join):
                left_un = BranchSpec(branch="left_unmatched")
                self._propagate_unmatched_left(step, left_un)
                specs.append(left_un)
            elif isinstance(step, Having) and step.condition:
                fail = BranchSpec(branch="having_fail")
                self._propagate_step(self.plan.root, fail, negate_step=step)
                specs.append(fail)
        return specs

    def _propagate_step(self, step: Step, spec: BranchSpec, negate_step: Optional[Step] = None):
        """Recursively propagate requirements top-down."""
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            needed = offset + int(limit_val)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Increase min_rows for all tables.
            for req in spec.requirements.values():
                req.min_rows = max(req.min_rows, needed)

        elif isinstance(step, Project):
            # NOT NULL for projected columns + duplicate requirement.
            projected = self._projected_columns(step)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Apply to all tables in spec.
            for table, req in spec.requirements.items():
                for col in projected:
                    matched = self._match_column(table, col)
                    if matched:
                        req.not_null.add(matched)
                # Duplicate: ensure ≥2 rows with same projected values.
                dup_cols = [c for c in projected if self._match_column(table, c)]
                if dup_cols:
                    req.duplicate_columns = dup_cols
                    req.min_rows = max(req.min_rows, 2)

        elif isinstance(step, Sort):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition and step is not negate_step:
                self._extract_predicates(step.condition, spec)
                # HAVING with aggregate: derive min group size.
                min_size = self._extract_min_group_size(step.condition)
                for req in spec.requirements.values():
                    req.min_rows = max(req.min_rows, min_size)
                # Derive per-row value constraints from aggregate thresholds.
                self._extract_having_value_constraints(step.condition, spec, min_size)

        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # GROUP BY: mark group columns as needing the same value across rows.
            # If the column is already in an equivalence class (from JOIN), that's fine —
            # Union-Find handles it naturally (union with self is a no-op).
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        table = self._resolve_table(col.table or "")
                        matched = self._match_column(table, col.name)
                        if matched:
                            req = spec.require(table)
                            # Register in union-find (ensures it appears in groups())
                            spec.equivalences.find(f"{table}.{matched}")
                            # Mark as group key so Resolver coordinates values
                            if matched not in req.group_key_columns:
                                req.group_key_columns.append(matched)

        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition:
                if step is negate_step:
                    self._extract_negated_predicates(step.condition, spec)
                else:
                    self._extract_predicates(step.condition, spec)

        elif isinstance(step, Join):
            # Process chain dependencies first.
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Link join keys via equivalence (Union-Find).
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
                        # Mark join keys as group keys so all rows share the same value
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

    def _propagate_unmatched_left(self, join_step: Join, spec: BranchSpec):
        """Generate a left-table row with no matching right-table row."""
        source = self._resolve_table(join_step.source_name or join_step.name)
        if source in self.instance.tables:
            spec.require(source)  # Just needs to exist, no shared key.

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
            # col IN (SELECT col2 FROM t2 WHERE ...)
            # Link outer column to inner SELECT column.
            self._propagate_in_subplan(sub, spec)

        elif sub.kind.value == "scalar":
            # (SELECT col FROM t WHERE ...) — ensure inner table has rows.
            self._propagate_scalar_subplan(sub, spec)

        # Always propagate into inner plan for WHERE constraints.
        if sub.inner:
            self._propagate_step(sub.inner, spec)
            # Fix misqualified columns: inner filters may reference outer table
            # names due to sqlglot scope resolution. Re-resolve against inner tables.
            self._fix_inner_filter_tables(sub.inner, spec)

    def _propagate_in_subplan(self, sub: SubPlan, spec: BranchSpec):
        """Handle IN (SELECT col FROM t WHERE ...).

        Links the outer column (from the IN expression's left side) to
        the inner SELECT's projected column via Union-Find.
        """
        # Find the outer column from the anchor (exp.In node).
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

        # Find the inner SELECT's first projected column.
        inner_col_key = self._find_inner_select_column(sub, spec)
        if inner_col_key:
            spec.require(outer_table)
            spec.equate(f"{outer_table}.{outer_matched}", inner_col_key)

    def _propagate_scalar_subplan(self, sub: SubPlan, spec: BranchSpec):
        """Ensure scalar subquery's inner table has at least one row."""
        stack = [sub.inner]
        visited = set()
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

    def _fix_inner_filter_tables(self, inner_root, spec: BranchSpec):
        """Fix misqualified columns in inner subplan filters.

        sqlglot sometimes qualifies inner columns with the outer table name.
        If a fixed_value was assigned to an outer table but the column also
        exists in an inner table, move it to the inner table.
        """
        # Find the inner scan table(s)
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

        # Check each requirement for outer tables: if a fixed_value column
        # also exists in an inner table, move it there (inner takes priority).
        outer_tables = [t for t in spec.requirements if t not in inner_tables]
        for table in outer_tables:
            req = spec.requirements[table]
            cols_to_move = []
            for col, val in list(req.fixed_values.items()):
                # Check if this column exists in any inner table
                for inner_t in inner_tables:
                    matched = self._match_column(inner_t, col)
                    if matched:
                        cols_to_move.append((col, val, inner_t, matched))
                        break
            for col, val, target_table, target_col in cols_to_move:
                del req.fixed_values[col]
                spec.require(target_table).fixed_values[target_col] = val

    def _find_inner_select_column(self, sub: SubPlan, spec: BranchSpec) -> Optional[str]:
        """Find the inner plan's source column for IN subqueries.

        For `col IN (SELECT col2 FROM t2 WHERE ...)`, we need the actual
        base table column that the inner query reads from — not the
        projection alias which may reference the outer table.
        """
        # Get the projection column name (what the inner SELECT returns).
        proj_col_name = None
        stack = [sub.inner]
        visited = set()
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

        # Find the Scan table and match the column there.
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

    def _extract_predicates(self, condition: exp.Expression, spec: BranchSpec):
        """Extract value constraints from a predicate using centralized lowering."""
        tables = tuple(spec.requirements.keys()) or tuple(
            v for v in self.alias_map.values() if v in self.instance.tables
        )
        # Handle two-column equalities (col1 = col2) via Union-Find.
        self._extract_column_equalities(condition, spec)
        # Self-join aware: extract per-alias constraints directly from columns
        # that reference self-joined tables, before lowering collapses them.
        self._extract_self_join_predicates(condition, spec)
        preds, _ = lower_predicates(condition, self.instance, tables, self.alias_map)
        for pred in preds:
            # Skip if already handled by self-join extraction
            if self._is_self_join_table(pred.table):
                continue
            if pred.op == "=":
                # Merge temporal values: if column already has a date string,
                # combine year/month/day components
                existing = spec.require(pred.table).fixed_values.get(pred.column)
                if existing and isinstance(existing, str) and isinstance(pred.value, str):
                    merged = self._merge_date_values(existing, pred.value)
                    spec.require(pred.table).fixed_values[pred.column] = merged
                else:
                    spec.require(pred.table).fixed_values[pred.column] = pred.value
            elif pred.op == "is_null":
                spec.require(pred.table).must_null.add(pred.column)
            elif pred.op == "like":
                pat = str(pred.value).replace("%", "x").replace("_", "a")
                spec.require(pred.table).fixed_values[pred.column] = pat
            elif pred.op == "not_null":
                spec.require(pred.table).not_null.add(pred.column)
            else:
                spec.require(pred.table).predicates.append((pred.column, pred.op, pred.value))
        # Extract upper bound for BETWEEN (lowering returns >= for low bound)
        for atom in condition.find_all(exp.Between):
            col = atom.this
            high = atom.args.get("high")
            if isinstance(col, exp.Column) and isinstance(high, exp.Literal):
                table = self._resolve_table(col.table if hasattr(col, 'table') else "")
                matched = self._match_column(table, col.name)
                if matched:
                    high_val = concrete(high)
                    spec.require(table).predicates.append((matched, "<=", high_val))

    def _is_self_join_table(self, table: str) -> bool:
        """Check if a table is involved in a self-join."""
        if hasattr(self.alias_map, 'has_self_join'):
            return self.alias_map.has_self_join(table)
        count = sum(1 for v in self.alias_map.values() if v == table)
        return count > 1

    def _merge_date_values(self, existing: str, new: str) -> str:
        """Merge two date-like strings by combining their year/month/day components.

        E.g., '1991-06-15' + '2024-10-15' → '1991-10-15' (year from first, month from second).
        """
        import re
        date_pat = re.compile(r'^(\d{4})-(\d{2})-(\d{2})')
        m_existing = date_pat.match(existing)
        m_new = date_pat.match(new)
        if not m_existing or not m_new:
            return new  # Can't merge, use new value
        # Take non-default components from each
        ey, em, ed = m_existing.groups()
        ny, nm, nd = m_new.groups()
        # "Default" values from the lowering: year=2024, month=06, day=15
        year = ey if ey != "2024" else ny
        month = em if em != "06" else nm
        day = ed if ed != "15" else nd
        return f"{year}-{month}-{day}"

    def _extract_self_join_predicates(self, condition: exp.Expression, spec: BranchSpec):
        """For self-joins: extract per-alias constraints using alias field.

        When T2.colour = 'Blue' AND T3.colour = 'Blond' and both T2/T3 map to
        the same table, we create separate requirements with distinct aliases
        so each gets its own fixed_values.
        """
        from parseval.plan.rex import concrete as _concrete

        # Detect self-join aliases
        self_join_tables: Dict[str, List[str]] = {}
        if hasattr(self.alias_map, 'self_join_tables'):
            self_join_tables = self.alias_map.self_join_tables()
        else:
            from collections import defaultdict
            groups: Dict[str, List[str]] = defaultdict(list)
            for a, t in self.alias_map.items():
                groups[t].append(a)
            self_join_tables = {t: aliases for t, aliases in groups.items() if len(aliases) > 1}

        if not self_join_tables:
            return

        # Find EQ atoms with column references to self-joined aliases
        for eq_node in condition.find_all(exp.EQ):
            left, right = eq_node.this, eq_node.expression
            col = left if isinstance(left, exp.Column) else (right if isinstance(right, exp.Column) else None)
            lit = right if col is left else left
            if col is None or isinstance(lit, exp.Column):
                continue
            if not col.table:
                continue
            alias = normalize_name(col.table)
            table = self._resolve_table(alias)
            if table not in self_join_tables:
                continue
            col_name = self._match_column(table, col.name)
            if not col_name:
                continue
            val = _concrete(lit)
            if val is None:
                continue
            # Use physical table as key but with alias field set
            req_key = f"{table}__{alias}"
            if req_key not in spec.requirements:
                spec.requirements[req_key] = TableRequirement(table=table, alias=alias)
            spec.requirements[req_key].fixed_values[col_name] = val

        # Also handle LIKE on self-joined aliases
        for like_node in condition.find_all(exp.Like):
            col = like_node.this
            if not isinstance(col, exp.Column) or not col.table:
                continue
            alias = normalize_name(col.table)
            table = self._resolve_table(alias)
            if table not in self_join_tables:
                continue
            col_name = self._match_column(table, col.name)
            if not col_name:
                continue
            pat_node = like_node.expression
            if isinstance(pat_node, exp.Literal) and pat_node.is_string:
                pat = str(pat_node.this).replace("%", "x").replace("_", "a")
                req_key = f"{table}__{alias}"
                if req_key not in spec.requirements:
                    spec.requirements[req_key] = TableRequirement(table=table, alias=alias)
                spec.requirements[req_key].fixed_values[col_name] = pat

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
        # Handle year-difference comparisons: STRFTIME('%Y','now') - STRFTIME('%Y', col) > N
        self._extract_temporal_age_constraints(condition, spec)

    def _extract_temporal_age_constraints(self, condition: exp.Expression, spec: BranchSpec):
        """Handle year-difference patterns like STRFTIME('%Y','now') - STRFTIME('%Y', col) > N.

        Also handles JULIANDAY('now') - JULIANDAY(col) > N (days) and
        CAST((JULIANDAY('now') - JULIANDAY(col)) AS REAL) / 365 >= N.
        """
        from datetime import date as _date, timedelta
        for node in condition.find_all((exp.GT, exp.GTE)):
            threshold = concrete(node.expression)
            if not isinstance(threshold, (int, float)):
                continue
            left = node.this
            # Find a column inside the left expression that's used in a date context
            cols = list(left.find_all(exp.Column))
            if not cols:
                continue
            # Check if expression involves 'now' or date functions
            has_now = any(
                isinstance(n, exp.CurrentDate) or
                (isinstance(n, exp.Literal) and 'now' in str(n.this).lower())
                for n in left.walk()
            )
            if not has_now:
                continue
            # Find the column that represents the date/birthday
            for col in cols:
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if not matched or table not in self.instance.tables:
                    continue
                # Generate a date that's `threshold` years in the past
                col_type = str(self.instance.tables[table].get(matched, "")).upper()
                if "DATE" in col_type or "TIME" in col_type or "birthday" in matched.lower() or "date" in matched.lower():
                    years_ago = int(threshold) + 1
                    old_date = _date.today().replace(year=_date.today().year - years_ago)
                    spec.require(table).fixed_values[matched] = old_date.isoformat()
                    break

    def _extract_negated_predicates(self, condition: exp.Expression, spec: BranchSpec):
        """Extract NEGATED constraints for negative branches.

        For AND: negate the easiest conjunct, keep others positive.
        For OR: negate ALL disjuncts (¬(A OR B) = ¬A AND ¬B).
        """
        if isinstance(condition, exp.And):
            # ¬(A AND B) = ¬A OR ¬B — pick one conjunct to negate
            conjuncts = self._split_and(condition)
            # Negate the first conjunct (simplest heuristic)
            if conjuncts:
                self._negate_single(conjuncts[0], spec)
                # Keep remaining conjuncts as positive (path constraints)
                for other in conjuncts[1:]:
                    self._extract_predicates(other, spec)
            return
        if isinstance(condition, exp.Or):
            # ¬(A OR B) = ¬A AND ¬B — negate all disjuncts
            disjuncts = self._split_or(condition)
            for d in disjuncts:
                self._negate_single(d, spec)
            return
        if isinstance(condition, exp.Paren):
            self._extract_negated_predicates(condition.this, spec)
            return
        # Single atom — negate directly
        self._negate_single(condition, spec)

    def _negate_single(self, condition: exp.Expression, spec: BranchSpec):
        """Negate a single predicate atom into spec requirements."""
        tables = tuple(spec.requirements.keys()) or tuple(
            v for v in self.alias_map.values() if v in self.instance.tables
        )
        preds, _ = lower_predicates(condition, self.instance, tables, self.alias_map)
        for pred in preds:
            neg_op, neg_val = negate_predicate_value(pred.op, pred.value)
            if neg_op == "=":
                spec.require(pred.table).fixed_values[pred.column] = neg_val
            else:
                spec.require(pred.table).predicates.append((pred.column, neg_op, neg_val))

    def _split_and(self, expr: exp.Expression) -> List[exp.Expression]:
        """Split a conjunction into its top-level conjuncts."""
        parts = []
        if isinstance(expr, exp.And):
            parts.extend(self._split_and(expr.left))
            parts.extend(self._split_and(expr.right))
        elif isinstance(expr, exp.Paren):
            parts.extend(self._split_and(expr.this))
        else:
            parts.append(expr)
        return parts

    def _split_or(self, expr: exp.Expression) -> List[exp.Expression]:
        """Split a disjunction into its top-level disjuncts."""
        parts = []
        if isinstance(expr, exp.Or):
            parts.extend(self._split_or(expr.left))
            parts.extend(self._split_or(expr.right))
        elif isinstance(expr, exp.Paren):
            parts.extend(self._split_or(expr.this))
        else:
            parts.append(expr)
        return parts

    def _extract_min_group_size(self, condition: exp.Expression) -> int:
        """Extract minimum group size from HAVING (e.g., COUNT(*) > 3 → 4).

        Also checks the Aggregate step's aggregations for internal aliases.
        """
        result = 1
        # Check the condition directly
        result = max(result, self._min_group_from_expr(condition))
        # Check Aggregate aggregations (handles _h alias case)
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

    def _extract_having_value_constraints(self, condition: exp.Expression, spec: BranchSpec, min_rows: int):
        """Derive per-row value constraints from HAVING aggregate thresholds.

        For HAVING SUM(col)/COUNT(*) > N or HAVING AVG(col) > N:
        set each row's col value to N+1 so the aggregate exceeds the threshold.
        For HAVING SUM(col) > N: set each row's col to ceil(N/min_rows)+1.

        Also checks the Aggregate step's aggregations list for the actual
        expressions (since HAVING may reference internal aliases like _h).
        """
        # First try the condition directly
        self._extract_agg_value_from_expr(condition, spec, min_rows)
        # Also check the Aggregate step's aggregations (handles _h alias case)
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

    def _find_agg_column(self, expr: exp.Expression, agg_type) -> Optional[exp.Column]:
        """Find the column inside an aggregate function."""
        for agg in expr.find_all(agg_type):
            for col in agg.find_all(exp.Column):
                return col
        return None

    def _projected_columns(self, step: Project) -> List[str]:
        cols = []
        for proj in step.projections:
            if isinstance(proj, exp.Expression):
                for col in proj.find_all(exp.Column):
                    cols.append(col.name)
        return cols



class Resolver:
    """Turn TableRequirements into concrete row values."""

    def __init__(self, instance: Instance, dialect: str = "sqlite"):
        self.instance = instance
        self.dialect = dialect

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        """Produce concrete rows for each table in the spec."""
        shared_values = self._resolve_equivalences(spec)
        order = self._creation_order(spec)
        result: Dict[str, List[Dict[str, Any]]] = {}

        for table_key in order:
            if table_key not in spec.requirements:
                continue
            req = spec.requirements[table_key]
            # Resolve physical table name (use alias field or strip suffix)
            physical_table = req.table if not req.alias else req.table
            if "__" in physical_table:
                physical_table = physical_table.split("__")[0]
            rows = self._resolve_table(physical_table, req, shared_values)
            result.setdefault(physical_table, []).extend(rows)

        return result

    def _resolve_equivalences(self, spec: BranchSpec) -> Dict[str, Any]:
        """Resolve each equivalence class to a single concrete value."""
        shared: Dict[str, Any] = {}  # "table.col" → value
        groups = spec.equivalences.groups()

        for representative, members in groups.items():
            # Check if any member already has a fixed value.
            fixed_val = None
            for member in members:
                parts = member.split(".", 1)
                if len(parts) == 2:
                    table, col = parts
                    req = spec.requirements.get(table)
                    if req and col in req.fixed_values:
                        fixed_val = req.fixed_values[col]
                        break

            if fixed_val is not None:
                value = fixed_val
            else:
                # Generate a value based on the first member's type.
                value = self._generate_equiv_value(members, spec)

            for member in members:
                shared[member] = value

        return shared

    def _generate_equiv_value(self, members: List[str], spec: BranchSpec) -> Any:
        """Generate a value for an equivalence class."""
        # Use the first member's column type.
        for member in members:
            parts = member.split(".", 1)
            if len(parts) != 2:
                continue
            table, col = parts
            if table not in self.instance.tables:
                continue
            col_type = self.instance.tables[table].get(col, "TEXT")
            type_str = str(col_type).upper()

            # Avoid existing unique values.
            existing: Set[Any] = set()
            if self.instance.is_unique(table, col):
                existing = {s.concrete for s in self.instance.get_column_data(table, col) if s.concrete is not None}

            if "INT" in type_str:
                v = 1
                while v in existing:
                    v += 1
                return v
            elif "TEXT" in type_str or "CHAR" in type_str:
                v = "key_1"
                i = 1
                while v in existing:
                    i += 1
                    v = f"key_{i}"
                return v
            else:
                return 1
        return 1

    def _resolve_table(self, table: str, req: TableRequirement, shared: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = []
        base_row = self._build_row(table, req, shared)
        rows.append(base_row)

        for i in range(1, req.min_rows):
            row = self._build_row(table, req, shared)
            # For duplicate columns: copy values from base row.
            if req.duplicate_columns and i == 1:
                for col in req.duplicate_columns:
                    if col in base_row:
                        row[col] = base_row[col]
            # Group key columns must share the same value across all rows.
            for col in req.group_key_columns:
                if col in base_row:
                    row[col] = base_row[col]
            rows.append(row)

        return rows

    def _build_row(self, table: str, req: TableRequirement, shared: Dict[str, Any]) -> Dict[str, Any]:
        row: Dict[str, Any] = {}
        # Equivalence class values (JOIN/GROUP BY coordination).
        for col in self.instance.tables.get(table, {}):
            key = f"{table}.{col}"
            if key in shared:
                row[col] = shared[key]
        # Fixed values override equivalences (WHERE constraints are more specific).
        row.update(req.fixed_values)
        # Predicates: collect per-column, then satisfy all.
        col_preds: Dict[str, List[Tuple[str, Any]]] = {}
        for col, op, value in req.predicates:
            if col not in row:
                col_preds.setdefault(col, []).append((op, value))
        for col, preds in col_preds.items():
            row[col] = self._satisfy_all(preds)
        # Must NULL.
        for col in req.must_null:
            row[col] = None
        return row

    def _satisfy_all(self, preds: List[Tuple[str, Any]]) -> Any:
        """Satisfy all predicates for a single column.

        For numeric ranges, finds a value satisfying all bounds.
        For mixed predicates, satisfies the first and hopes for the best.
        """
        if not preds:
            return None
        if len(preds) == 1:
            return self._satisfy(preds[0][0], preds[0][1])

        # Separate into lower bounds, upper bounds, and other
        lower = []  # (op, value) where op is > or >=
        upper = []  # (op, value) where op is < or <=
        other = []

        for op, value in preds:
            if op in (">", ">=") and isinstance(value, (int, float)):
                lower.append((op, value))
            elif op in ("<", "<=") and isinstance(value, (int, float)):
                upper.append((op, value))
            else:
                other.append((op, value))

        # If we have both lower and upper bounds, find a value in range
        if lower and upper:
            lo_val = max(v for _, v in lower)
            lo_op = ">" if any(op == ">" for op, v in lower if v == lo_val) else ">="
            hi_val = min(v for _, v in upper)
            hi_op = "<" if any(op == "<" for op, v in upper if v == hi_val) else "<="

            # Adjust for strict inequalities
            if lo_op == ">":
                lo_val = lo_val + 1
            if hi_op == "<":
                hi_val = hi_val - 1

            if lo_val <= hi_val:
                mid = (lo_val + hi_val) // 2 if isinstance(lo_val, int) else (lo_val + hi_val) / 2
                return mid

        # Fallback: satisfy the first predicate
        return self._satisfy(preds[0][0], preds[0][1])

    def _satisfy(self, op: str, value: Any) -> Any:
        """Generate a value satisfying the predicate."""
        if op == ">" and isinstance(value, (int, float)):
            return value + 1
        if op == ">=" and isinstance(value, (int, float)):
            return value
        if op == "<" and isinstance(value, (int, float)):
            return value - 1
        if op == "<=" and isinstance(value, (int, float)):
            return value
        if op == "=":
            return value
        if op == "!=":
            if isinstance(value, (int, float)):
                return value + 1
            if isinstance(value, str):
                return value + "_neq"
            return value
        if op == "in":
            return value  # lowering already picks first value
        return value



    def _creation_order(self, spec: BranchSpec) -> List[str]:
        tables = list(spec.requirements.keys())
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


# =============================================================================
# Top-level API
# =============================================================================


def speculate(
    plan: Plan,
    instance: Instance,
    alias_map: Dict[str, str],
    dialect: str = "sqlite",
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve → list of (branch_name, rows_per_table).

    Returns one entry per branch (positive + negatives). The engine
    materializes each one.
    """
    propagator = Propagator(plan, instance, alias_map, dialect)
    resolver = Resolver(instance, dialect)
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
    resolver = Resolver(instance, dialect)
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
    "TableRequirement",
    "UNSET",
    "build_spec",
    "resolve_spec",
    "speculate",
]
