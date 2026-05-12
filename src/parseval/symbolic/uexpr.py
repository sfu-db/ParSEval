"""UExprToConstraint — SMT-based constraint solver for query satisfiability.

Translates plan operators into Z3 constraints over Instance Variables.
Operates directly on the Instance: creates rows, encodes query + schema
constraints as Z3 assertions, solves, and writes concrete values back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

import z3
from sqlglot import exp

from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.planner import Aggregate, Filter, Having, Join, Scan, SubPlan
from parseval.plan.rex import Variable, concrete, Environment
from parseval.symbolic.types import BranchType, CoverageTarget


def _build_alias_map(plan: Plan) -> Dict[str, str]:
    from parseval.symbolic.engine import _build_alias_map as _bam
    return _bam(plan)


class UExprToConstraint:
    """Translate plan operators into Z3 constraints over Instance Variables."""

    def __init__(self, plan: Plan, instance: Instance, dialect: str = "sqlite"):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.alias_map = _build_alias_map(plan)
        self.solver = z3.Solver()
        self.solver.set("timeout", 10000)
        self._z3_vars: Dict[str, z3.ExprRef] = {}
        self._var_to_symbol: Dict[str, Variable] = {}

    # =========================================================================
    # Public API
    # =========================================================================

    def solve_uncovered(self, target: CoverageTarget) -> bool:
        """Solve a specific uncovered branch target."""
        node = target.node
        tables = node.tables
        atom = target.atom
        outcome = target.target_outcome

        # Determine tables and get/create rows
        real_tables = self._resolve_tables(tables)
        row_ctx = self._build_row_context(real_tables)

        # Target constraint
        if outcome == BranchType.ATOM_TRUE:
            z3_pred = self._translate(atom, row_ctx)
            if z3_pred is not None:
                self.solver.add(z3_pred)
        elif outcome == BranchType.ATOM_FALSE:
            z3_pred = self._translate(atom, row_ctx)
            if z3_pred is not None:
                self.solver.add(z3.Not(z3_pred))
        elif outcome == BranchType.ATOM_NULL:
            # Make one column NULL
            for col in atom.find_all(exp.Column):
                table = self._resolve_col_table(col, real_tables)
                if table and self.instance.nullable(table, col.name):
                    key = self._var_key(table, col.name)
                    if key in self._z3_vars:
                        self.solver.add(self._z3_vars[key] == self._null_val(table, col.name))
                        break
            else:
                return False

        # Path constraints
        for pred in self._collect_path_predicates(target):
            z3_p = self._translate(pred, row_ctx)
            if z3_p is not None:
                self.solver.add(z3_p)

        # JOIN constraints
        self._add_join_constraints(row_ctx)

        # Schema constraints
        for table in real_tables:
            self._add_schema_constraints(table)

        # Solve
        if self.solver.check() == z3.sat:
            self._apply_model(self.solver.model())
            return True
        return False

    def ensure_nonempty(self) -> bool:
        """Ensure the query returns non-empty results."""
        # Analyze requirements
        requirements = self._analyze_requirements()

        # Create rows as needed
        for table, count in requirements.items():
            existing = len(self.instance.get_rows(table))
            for _ in range(max(0, count - existing)):
                try:
                    self.instance.create_row(table, values={})
                except Exception:
                    pass

        # Build context with all rows
        all_tables = set(self.alias_map.values()) & set(self.instance.tables.keys())
        row_ctx = self._build_row_context(tuple(all_tables))

        # WHERE constraints
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                if step.condition.find(exp.Subquery):
                    self._handle_subquery_filter(step, row_ctx)
                else:
                    z3_p = self._translate(step.condition, row_ctx)
                    if z3_p is not None:
                        self.solver.add(z3_p)

        # JOIN constraints
        self._add_join_constraints(row_ctx)

        # HAVING constraints
        self._add_having_constraints(row_ctx)

        # Schema constraints
        for table in all_tables:
            self._add_schema_constraints(table)

        # Solve
        if self.solver.check() == z3.sat:
            self._apply_model(self.solver.model())
            return True
        return False

    # =========================================================================
    # Row Context Building
    # =========================================================================

    def _build_row_context(self, tables: Tuple[str, ...]) -> Dict[str, z3.ExprRef]:
        """Build Z3 variable/constant context from Instance rows."""
        ctx: Dict[str, z3.ExprRef] = {}
        for table in tables:
            if table not in self.instance.tables:
                continue
            rows = self.instance.get_rows(table)
            if not rows:
                continue
            # Use first row for each table (primary target)
            row = rows[0]
            for col, sym in row.items():
                key = self._var_key(table, col)
                if sym.is_bound:
                    # Bound variable — treat as constant
                    ctx[key] = self._make_const(sym.concrete, table, col)
                else:
                    # Unbound — Z3 will determine its value
                    ctx[key] = self._declare_var(key, table, col)
                    self._var_to_symbol[key] = sym
        return ctx

    # =========================================================================
    # Z3 Variable/Constant Management
    # =========================================================================

    def _declare_var(self, name: str, table: str, col: str) -> z3.ExprRef:
        if name in self._z3_vars:
            return self._z3_vars[name]
        sort = self._col_sort(table, col)
        var = z3.Const(name, sort)
        self._z3_vars[name] = var
        return var

    def _make_const(self, value: Any, table: str, col: str) -> z3.ExprRef:
        sort = self._col_sort(table, col)
        if sort == z3.IntSort():
            return z3.IntVal(int(value) if value is not None else 0)
        elif sort == z3.RealSort():
            return z3.RealVal(float(value) if value is not None else 0.0)
        elif sort == z3.StringSort():
            return z3.StringVal(str(value) if value is not None else "")
        elif sort == z3.BoolSort():
            return z3.BoolVal(bool(value))
        return z3.IntVal(0)

    def _null_val(self, table: str, col: str) -> z3.ExprRef:
        """Sentinel for NULL — use a value outside normal range."""
        sort = self._col_sort(table, col)
        if sort == z3.IntSort():
            return z3.IntVal(-2147483648)
        elif sort == z3.StringSort():
            return z3.StringVal("__NULL__")
        return z3.IntVal(-2147483648)

    def _col_sort(self, table: str, col: str) -> z3.SortRef:
        col_type = str(self.instance.tables.get(table, {}).get(col, "TEXT")).upper()
        if any(t in col_type for t in ("INT", "INTEGER", "BIGINT", "SMALLINT")):
            return z3.IntSort()
        if any(t in col_type for t in ("REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")):
            return z3.RealSort()
        if "BOOL" in col_type:
            return z3.BoolSort()
        return z3.StringSort()

    def _var_key(self, table: str, col: str) -> str:
        return f"{table}.{col}"

    # =========================================================================
    # Predicate Translation
    # =========================================================================

    def _translate(self, expr: exp.Expression, ctx: Dict[str, z3.ExprRef]) -> Optional[z3.ExprRef]:
        """Translate a sqlglot expression to Z3."""
        try:
            return self._tr(expr, ctx)
        except Exception:
            return None

    def _tr(self, node: exp.Expression, ctx: Dict[str, z3.ExprRef]) -> Optional[z3.ExprRef]:
        if node is None:
            return None

        if isinstance(node, exp.And):
            l = self._tr(node.left, ctx)
            r = self._tr(node.right, ctx)
            if l is not None and r is not None:
                return z3.And(l, r)
            return l or r

        if isinstance(node, exp.Or):
            l = self._tr(node.left, ctx)
            r = self._tr(node.right, ctx)
            if l is not None and r is not None:
                return z3.Or(l, r)
            return l or r

        if isinstance(node, exp.Not):
            inner = self._tr(node.this, ctx)
            return z3.Not(inner) if inner is not None else None

        if isinstance(node, exp.Paren):
            return self._tr(node.this, ctx)

        if isinstance(node, exp.EQ):
            return self._tr_cmp(node, ctx, lambda a, b: a == b)
        if isinstance(node, exp.NEQ):
            return self._tr_cmp(node, ctx, lambda a, b: a != b)
        if isinstance(node, exp.GT):
            return self._tr_cmp(node, ctx, lambda a, b: a > b)
        if isinstance(node, exp.GTE):
            return self._tr_cmp(node, ctx, lambda a, b: a >= b)
        if isinstance(node, exp.LT):
            return self._tr_cmp(node, ctx, lambda a, b: a < b)
        if isinstance(node, exp.LTE):
            return self._tr_cmp(node, ctx, lambda a, b: a <= b)

        if isinstance(node, exp.Between):
            val = self._tr_val(node.this, ctx)
            lo = self._tr_val(node.args.get("low"), ctx)
            hi = self._tr_val(node.args.get("high"), ctx)
            if val is not None and lo is not None and hi is not None:
                return z3.And(val >= lo, val <= hi)
            return None

        if isinstance(node, exp.Like):
            col = self._tr_val(node.this, ctx)
            pat_node = node.expression
            if col is not None and isinstance(pat_node, exp.Literal) and pat_node.is_string:
                pat = str(pat_node.this)
                if pat.startswith('%') and pat.endswith('%') and len(pat) > 2:
                    return z3.Contains(col, z3.StringVal(pat[1:-1]))
                elif pat.endswith('%'):
                    return z3.PrefixOf(z3.StringVal(pat[:-1]), col)
                elif pat.startswith('%'):
                    return z3.SuffixOf(z3.StringVal(pat[1:]), col)
                else:
                    return col == z3.StringVal(pat)
            return None

        if isinstance(node, exp.In):
            val = self._tr_val(node.this, ctx)
            exprs = node.args.get("expressions") or []
            if val is not None and exprs:
                options = [self._tr_val(e, ctx) for e in exprs]
                options = [o for o in options if o is not None]
                if options:
                    return z3.Or(*[val == o for o in options])
            return None

        if isinstance(node, exp.Is):
            left = node.this
            right = node.expression
            if isinstance(right, exp.Null):
                lv = self._tr_val(left, ctx)
                if lv is not None:
                    return lv == self._null_val_for(left, ctx)
            return None

        return None

    def _tr_cmp(self, node, ctx, op):
        l = self._tr_val(node.this, ctx)
        r = self._tr_val(node.expression, ctx)
        if l is not None and r is not None:
            try:
                return op(l, r)
            except (z3.Z3Exception, TypeError):
                return None
        return None

    def _tr_val(self, node, ctx) -> Optional[z3.ExprRef]:
        """Translate a value expression (column, literal, arithmetic)."""
        if node is None:
            return None

        if isinstance(node, exp.Column):
            table = self._resolve_col_table(node, tuple(
                t for t in self.alias_map.values() if t in self.instance.tables
            ))
            if table:
                col_name = normalize_name(node.name)
                key = self._var_key(table, col_name)
                if key in ctx:
                    return ctx[key]
                # Try to find in z3_vars
                if key in self._z3_vars:
                    return self._z3_vars[key]
            return None

        if isinstance(node, exp.Literal):
            if node.is_string:
                return z3.StringVal(str(node.this))
            try:
                text = str(node.this)
                if '.' in text:
                    return z3.RealVal(float(text))
                return z3.IntVal(int(text))
            except (ValueError, TypeError):
                return z3.StringVal(str(node.this))

        if isinstance(node, exp.Boolean):
            return z3.BoolVal(bool(node.this))

        if isinstance(node, exp.Null):
            return None

        if isinstance(node, exp.Add):
            l = self._tr_val(node.left, ctx)
            r = self._tr_val(node.right, ctx)
            if l is not None and r is not None:
                return l + r
            return None

        if isinstance(node, exp.Sub):
            l = self._tr_val(node.left, ctx)
            r = self._tr_val(node.right, ctx)
            if l is not None and r is not None:
                return l - r
            return None

        if isinstance(node, exp.Mul):
            l = self._tr_val(node.left, ctx)
            r = self._tr_val(node.right, ctx)
            if l is not None and r is not None:
                return l * r
            return None

        if isinstance(node, exp.Div):
            l = self._tr_val(node.left, ctx)
            r = self._tr_val(node.right, ctx)
            if l is not None and r is not None:
                return l / r
            return None

        if isinstance(node, exp.Neg):
            v = self._tr_val(node.this, ctx)
            return -v if v is not None else None

        if isinstance(node, exp.Cast):
            return self._tr_val(node.this, ctx)

        if isinstance(node, exp.Paren):
            return self._tr_val(node.this, ctx)

        return None

    def _null_val_for(self, node, ctx):
        if isinstance(node, exp.Column):
            table = self._resolve_col_table(node, tuple(self.alias_map.values()))
            if table:
                return self._null_val(table, normalize_name(node.name))
        return z3.IntVal(-2147483648)

    # =========================================================================
    # JOIN Constraints
    # =========================================================================

    def _add_join_constraints(self, ctx: Dict[str, z3.ExprRef]):
        for step in self.plan.ordered_steps:
            if not isinstance(step, Join):
                continue
            source_name = step.source_name or step.name
            source_table = self._resolve_alias(source_name)
            for join_name, join_data in (step.joins or {}).items():
                join_table = self._resolve_alias(join_name)
                for sk, jk in zip(join_data.get("source_key", []), join_data.get("join_key", [])):
                    sk_name = normalize_name(sk.name if hasattr(sk, "name") else str(sk))
                    jk_name = normalize_name(jk.name if hasattr(jk, "name") else str(jk))
                    sk_key = self._var_key(source_table, sk_name)
                    jk_key = self._var_key(join_table, jk_name)
                    if sk_key in ctx and jk_key in ctx:
                        try:
                            self.solver.add(ctx[sk_key] == ctx[jk_key])
                        except (z3.Z3Exception, TypeError):
                            pass

    # =========================================================================
    # HAVING Constraints
    # =========================================================================

    def _add_having_constraints(self, ctx: Dict[str, z3.ExprRef]):
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            for agg_expr in step.aggregations:
                # Find GT/GTE with COUNT or SUM
                for node in agg_expr.find_all((exp.GT, exp.GTE)):
                    agg_side = node.this
                    threshold = concrete(node.expression)
                    if not isinstance(threshold, (int, float)):
                        continue
                    # For SUM(col)/COUNT(*) > N or AVG(col) > N
                    # Assert the relevant column values are large enough
                    for agg_fn in agg_side.find_all((exp.Sum, exp.Avg)):
                        for col in agg_fn.find_all(exp.Column):
                            table = self._resolve_col_table(col, tuple(self.alias_map.values()))
                            if table:
                                key = self._var_key(table, normalize_name(col.name))
                                if key in ctx:
                                    self.solver.add(ctx[key] > z3.IntVal(int(threshold)))

    # =========================================================================
    # Subquery Handling
    # =========================================================================

    def _handle_subquery_filter(self, step: Filter, ctx: Dict[str, z3.ExprRef]):
        """Handle filter predicates containing subqueries."""
        condition = step.condition
        # Handle NOT IN
        for in_node in condition.find_all(exp.In):
            if in_node.find(exp.Subquery):
                # Check if it's NOT IN (wrapped in Not)
                parent = in_node.parent
                is_not_in = isinstance(parent, exp.Not)
                outer_col = in_node.this
                if isinstance(outer_col, exp.Column):
                    table = self._resolve_col_table(outer_col, tuple(self.alias_map.values()))
                    if table:
                        key = self._var_key(table, normalize_name(outer_col.name))
                        if key in ctx and is_not_in:
                            # Get existing values from inner query
                            inner_vals = self._get_inner_query_values(in_node)
                            for v in inner_vals:
                                self.solver.add(ctx[key] != self._make_const(v, table, normalize_name(outer_col.name)))

        # Handle scalar subquery: col = (SELECT ...)
        for eq_node in condition.find_all(exp.EQ):
            if eq_node.find(exp.Subquery):
                col_side = eq_node.this if isinstance(eq_node.this, exp.Column) else (
                    eq_node.expression if isinstance(eq_node.expression, exp.Column) else None)
                subq_side = eq_node.expression if eq_node.this is col_side else eq_node.this
                if col_side and subq_side:
                    scalar = self._evaluate_scalar_subquery(subq_side)
                    if scalar is not None:
                        table = self._resolve_col_table(col_side, tuple(self.alias_map.values()))
                        if table:
                            key = self._var_key(table, normalize_name(col_side.name))
                            if key in ctx:
                                self.solver.add(ctx[key] == self._make_const(scalar, table, normalize_name(col_side.name)))

    def _get_inner_query_values(self, in_node: exp.In) -> List[Any]:
        """Evaluate the inner query of an IN expression against current instance."""
        subq = in_node.find(exp.Subquery)
        if not subq:
            return []
        inner_select = subq.this
        if not isinstance(inner_select, exp.Select):
            return []
        # Find the inner table and column
        from_clause = inner_select.args.get("from")
        if not from_clause:
            return []
        from_table = from_clause.this
        if not isinstance(from_table, exp.Table):
            return []
        table_name = normalize_name(from_table.alias_or_name)
        table_name = self.alias_map.get(table_name, table_name)
        if table_name not in self.instance.tables:
            return []
        # Get projected column
        projections = inner_select.expressions
        if not projections:
            return []
        proj_col = None
        for col in projections[0].find_all(exp.Column):
            proj_col = normalize_name(col.name)
            break
        if not proj_col:
            return []
        # Evaluate WHERE filter
        rows = self.instance.get_rows(table_name)
        where = inner_select.args.get("where")
        values = []
        for row in rows:
            if where:
                env = Environment({c: s.concrete for c, s in row.items()})
                if concrete(where.this, env) is not True:
                    continue
            if proj_col in row.columns:
                v = row[proj_col].concrete
                if v is not None:
                    values.append(v)
        return values

    def _evaluate_scalar_subquery(self, subq_expr: exp.Expression) -> Optional[Any]:
        """Evaluate a scalar subquery against current instance."""
        subq = subq_expr.find(exp.Subquery) or subq_expr
        inner = subq.this if isinstance(subq, exp.Subquery) else subq
        if not isinstance(inner, exp.Select):
            return None
        from_clause = inner.args.get("from")
        if not from_clause:
            return None
        from_table = from_clause.this
        if not isinstance(from_table, exp.Table):
            return None
        table_name = normalize_name(from_table.alias_or_name)
        table_name = self.alias_map.get(table_name, table_name)
        if table_name not in self.instance.tables:
            return None
        rows = self.instance.get_rows(table_name)
        where = inner.args.get("where")
        for row in rows:
            if where:
                env = Environment({c: s.concrete for c, s in row.items()})
                if concrete(where.this, env) is not True:
                    continue
            # Return first matching row's projected value
            projections = inner.expressions
            if projections:
                env = Environment({c: s.concrete for c, s in row.items()})
                return concrete(projections[0], env)
        return None

    # =========================================================================
    # Schema Constraints
    # =========================================================================

    def _add_schema_constraints(self, table: str):
        if table not in self.instance.tables:
            return
        for col in self.instance.tables[table]:
            key = self._var_key(table, col)
            if key not in self._z3_vars:
                continue
            var = self._z3_vars[key]
            # NOT NULL
            if not self.instance.nullable(table, col):
                null_v = self._null_val(table, col)
                self.solver.add(var != null_v)
            # UNIQUE avoidance
            if self.instance.is_unique(table, col):
                existing = [s.concrete for s in self.instance.get_column_data(table, col)
                            if s.concrete is not None]
                for ev in existing:
                    try:
                        self.solver.add(var != self._make_const(ev, table, col))
                    except (z3.Z3Exception, TypeError):
                        pass

    # =========================================================================
    # Model Application
    # =========================================================================

    def _apply_model(self, model: z3.ModelRef):
        """Write Z3 solution back into Instance Variables."""
        for key, sym in self._var_to_symbol.items():
            if key not in self._z3_vars:
                continue
            z3_var = self._z3_vars[key]
            z3_val = model.evaluate(z3_var, model_completion=True)
            python_val = self._z3_to_python(z3_val)
            if python_val is not None:
                sym.set("concrete", python_val)
                sym.set("is_bound", True)
                sym.set("is_null", False)

    def _z3_to_python(self, val: z3.ExprRef) -> Optional[Any]:
        if val is None:
            return None
        if z3.is_int_value(val):
            v = val.as_long()
            return None if v == -2147483648 else v
        if z3.is_rational_value(val):
            return float(val.as_fraction())
        if z3.is_string_value(val):
            s = val.as_string()
            return None if s == "__NULL__" else s
        if z3.is_true(val):
            return True
        if z3.is_false(val):
            return False
        try:
            return int(str(val))
        except (ValueError, TypeError):
            return str(val)

    # =========================================================================
    # Helpers
    # =========================================================================

    def _resolve_alias(self, name: str) -> str:
        real = self.alias_map.get(normalize_name(name), name)
        return normalize_name(real)

    def _resolve_col_table(self, col: exp.Column, tables: Tuple[str, ...]) -> Optional[str]:
        if col.table:
            t = self._resolve_alias(col.table)
            if t in self.instance.tables:
                return t
        col_name = normalize_name(col.name)
        for t in tables:
            if t in self.instance.tables and col_name in self.instance.tables[t]:
                return t
        return None

    def _resolve_tables(self, tables: Tuple[str, ...]) -> Tuple[str, ...]:
        resolved = []
        for t in tables:
            real = self._resolve_alias(t)
            if real in self.instance.tables:
                resolved.append(real)
        return tuple(resolved) if resolved else tuple(
            v for v in self.alias_map.values() if v in self.instance.tables
        )

    def _collect_path_predicates(self, target: CoverageTarget) -> List[exp.Expression]:
        """Collect upstream predicates that must hold."""
        from parseval.symbolic.constraints import _collect_path_predicates_and_joins
        step = None
        for s in self.plan.ordered_steps:
            if self.plan.annotation_for(s).step_id == target.node.step_id:
                step = s
                break
        if step is None:
            return []
        preds, _ = _collect_path_predicates_and_joins(self.plan, step)
        return [p for p in preds if not p.find(exp.Subquery)]

    def _analyze_requirements(self) -> Dict[str, int]:
        """Determine how many rows each table needs."""
        reqs: Dict[str, int] = {}
        for table in self.alias_map.values():
            if table in self.instance.tables:
                reqs[table] = max(reqs.get(table, 0), 1)
        # Check HAVING COUNT
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for node in agg_expr.find_all((exp.GT, exp.GTE)):
                        if node.this.find(exp.Count):
                            threshold = concrete(node.expression)
                            if isinstance(threshold, (int, float)):
                                needed = int(threshold) + 1
                                # Find the counted table (usually the joined table)
                                for col in node.this.find_all(exp.Column):
                                    t = self._resolve_col_table(col, tuple(reqs.keys()))
                                    if t:
                                        reqs[t] = max(reqs.get(t, 0), needed)
                                        break
                                else:
                                    # COUNT(*) — apply to all joined tables
                                    for t in reqs:
                                        reqs[t] = max(reqs[t], needed)
        return reqs


__all__ = ["UExprToConstraint"]
