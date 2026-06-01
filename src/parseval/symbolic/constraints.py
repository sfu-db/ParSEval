"""Translate coverage gaps into solver-ready constraints.

The constraint generator collects ALL constraints that must hold for a
row to be valid:

1. **Query predicates** — the atom itself (for the target outcome) plus
   upstream path predicates the row must satisfy to reach the decision site.
2. **Database constraints** — NOT NULL, UNIQUE avoidance, FK relationships
   (the generated value must reference an existing parent, or the parent
   must be co-created with a matching key).
3. **JOIN conditions** — when the target atom is inside a JOIN, the join
   key equality is part of the constraint set, so the solver produces
   coordinated values across tables.

The solver receives the full constraint set and finds values satisfying
everything simultaneously — no post-hoc FK fixup needed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.plan import Plan, Step
from parseval.plan.planner import Filter, Having, Join, Aggregate, Project, Scan, SubPlan
from parseval.plan.rex import negate_predicate, column_meta
from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.solver.unified import SolverConstraint

from .types import BranchType, CoverageTarget


def _collect_path_predicates_and_joins(
    plan: Plan, target_step: Step
) -> Tuple[List[exp.Expression], List[Tuple[str, str, str, str]]]:
    """Walk from the target step down to leaves, collecting:
    - Predicates (WHERE conditions) that must be TRUE.
    - JOIN key equalities that link tables together.
    """
    predicates: List[exp.Expression] = []
    join_equalities: List[Tuple[str, str, str, str]] = []
    visited: Set[int] = set()

    def walk(step: Step) -> None:
        if id(step) in visited:
            return
        visited.add(id(step))
        if step is not target_step:
            condition = getattr(step, "condition", None)
            if isinstance(condition, exp.Expression):
                predicates.append(condition)
        if isinstance(step, Join):
            source_name = step.source_name or step.name
            for join_name, join_data in (step.joins or {}).items():
                source_keys = join_data.get("source_key", [])
                join_keys = join_data.get("join_key", [])
                for sk, jk in zip(source_keys, join_keys):
                    sk_name = sk.name if hasattr(sk, "name") else str(sk)
                    jk_name = jk.name if hasattr(jk, "name") else str(jk)
                    join_equalities.append((source_name, sk_name, join_name, jk_name))
        for dep in step.chain_dependencies:
            walk(dep)

    for dep in target_step.chain_dependencies:
        walk(dep)
    return predicates, join_equalities


class ConstraintGenerator:
    """Translate a :class:`CoverageTarget` into a full :class:`SolverConstraint`.

    Collects query predicates + database constraints + JOIN conditions into
    one constraint set the solver satisfies simultaneously.
    """

    def __init__(self, plan: Plan, instance: Instance, dialect: str = "sqlite", alias_map=None):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.alias_map = alias_map or plan.alias_map

    def generate(self, target: CoverageTarget) -> SolverConstraint:
        atom = target.atom
        outcome = target.target_outcome
        node = target.node
        tables = node.tables

        step = self._find_step(node.step_id)

        # --- Handle SubPlan branches ---
        if node.site == "exists":
            return self._generate_exists_constraint(target)
        elif node.site == "in":
            return self._generate_in_constraint(target)

        # --- Handle DISTINCT branches ---
        if node.site == "distinct":
            return self._generate_distinct_constraint(target)

        # --- Handle GROUP branches ---
        if node.site == "group":
            return self._generate_group_constraint(target)

        # --- Transform atom for target outcome ---
        null_columns: List[exp.Column] = []
        if outcome == BranchType.ATOM_TRUE:
            atom_constraint = atom.copy()
        elif outcome == BranchType.ATOM_FALSE:
            atom_constraint = negate_predicate(atom.copy())
        elif outcome == BranchType.ATOM_NULL:
            atom_constraint = atom.copy()
            columns = list(atom.find_all(exp.Column))
            for col in columns:
                meta = column_meta(col)
                if meta is not None and meta["nullable"]:
                    null_columns.append(col)
                    break
                elif meta is None:
                    # Fallback: resolve via instance directly.
                    table_name = self._resolve_table(col, tables)
                    if table_name and self.instance.nullable(table_name, col.name):
                        null_columns.append(col)
                        break
            else:
                if columns:
                    null_columns.append(columns[0])
        else:
            atom_constraint = atom.copy()

        # --- Collect path predicates + JOIN equalities ---
        path_predicates: List[exp.Expression] = []
        join_equalities: List[Tuple[str, str, str, str]] = []
        if step is not None:
            path_predicates, join_equalities = _collect_path_predicates_and_joins(
                self.plan, step
            )

        # --- Database constraints from columns in the atom + path predicates ---
        not_null_columns: List[Tuple[str, str]] = []
        avoid_values: Dict[str, Set[Any]] = {}
        foreign_keys: List[Tuple[str, str, str, str]] = []

        # Read NOT NULL and UNIQUE from enriched column metadata — only for
        # columns that actually appear in the target predicate or path.
        seen_cols: Set[Tuple[str, str]] = set()
        all_exprs = [atom_constraint] + path_predicates
        for expr in all_exprs:
            for col in expr.find_all(exp.Column):
                meta = column_meta(col)
                if meta is None:
                    continue
                real_table = meta["table"]
                col_name = normalize_name(col.name)
                key = (real_table, col_name)
                if key in seen_cols:
                    continue
                seen_cols.add(key)

                if not meta["nullable"]:
                    not_null_columns.append(key)
                if meta["unique"]:
                    existing = {
                        sym.concrete
                        for sym in self.instance.get_column_data(real_table, col_name)
                        if sym.concrete is not None
                    }
                    if existing:
                        avoid_values[f"{real_table}.{col_name}"] = existing

        # FK and CHECK constraints are table-level — still require per-table iteration.
        for table_name in tables:
            real_table = self._resolve_table_name(table_name)
            if real_table not in self.instance.tables:
                continue

            for fk in self.instance.get_foreign_key(real_table):
                local_col = normalize_name(fk.expressions[0].name)
                ref = fk.args.get("reference")
                if ref is None:
                    continue
                ref_table_node = ref.find(exp.Table)
                if ref_table_node is None:
                    continue
                ref_table = normalize_name(ref_table_node.name)
                ref_col = self.instance.resolve_fk_ref_column(fk)
                if ref_col is None:
                    continue
                foreign_keys.append((real_table, local_col, ref_table, ref_col))

            # Include CHECK constraints as path predicates.
            for check_expr in self.instance.get_check_constraints(real_table):
                path_predicates.append(check_expr)

        # Build unified constraints list: atom + path + DB constraints
        constraints: List[exp.Expression] = [atom_constraint] + path_predicates
        for col in null_columns:
            constraints.append(exp.Is(this=col.copy(), expression=exp.Null()))
        for table_name, col_name in not_null_columns:
            constraints.append(exp.Is(
                this=exp.Column(
                    this=exp.to_identifier(col_name),
                    table=exp.to_identifier(table_name),
                ),
                expression=exp.Not(this=exp.Null()),
            ))
        for col_key, vals in avoid_values.items():
            tname, cname = col_key.split(".", 1)
            constraints.append(exp.Not(this=exp.In(
                this=exp.Column(
                    this=exp.to_identifier(cname),
                    table=exp.to_identifier(tname),
                ),
                expressions=[
                    exp.Literal.number(v) if isinstance(v, (int, float))
                    else exp.Literal.string(str(v))
                    for v in vals
                ],
            )))

        # FK constraints: local column must reference an existing parent row.
        for real_table, local_col, ref_table, ref_col in foreign_keys:
            parent_vals = []
            if ref_table in self.instance.tables:
                for row in self.instance.get_rows(ref_table):
                    if ref_col in row.columns:
                        val = row[ref_col].concrete
                        if val is not None:
                            parent_vals.append(val)
            if parent_vals:
                literals = [
                    exp.Literal.number(v) if isinstance(v, (int, float))
                    else exp.Literal.string(str(v))
                    for v in parent_vals
                ]
                constraints.append(exp.In(
                    this=exp.Column(
                        this=exp.to_identifier(local_col),
                        table=exp.to_identifier(real_table),
                    ),
                    expressions=literals,
                ))

        self._annotate_column_types(constraints)

        return SolverConstraint(
            target_tables=tables,
            constraints=constraints,
            join_equalities=join_equalities,
            alias_map=dict(self.alias_map),
        )

    def _generate_exists_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for EXISTS_TRUE or EXISTS_FALSE."""
        # For EXISTS_FALSE: generate an outer row where the inner query returns empty
        # Strategy: set the correlation column to a value not in the inner table

        # Find the SubPlan from the plan
        subplan = self._find_subplan_for_target(target)
        if subplan and subplan.correlation:
            # Correlated EXISTS
            corr_col = subplan.correlation[0]
            outer_table = self._resolve_table(corr_col, target.node.tables)

            if target.target_outcome == BranchType.EXISTS_FALSE:
                # Generate outer row with correlation value not in inner table
                inner_table = self._find_inner_scan_table(subplan)
                if inner_table:
                    existing = set()
                    for row in self.instance.get_rows(inner_table):
                        if corr_col.name in row.columns:
                            val = row[corr_col.name].concrete
                            if val is not None:
                                existing.add(val)

                    # Generate a fresh value using column type
                    corr_copy = corr_col.copy()
                    meta = column_meta(corr_col)
                    if meta and "domain" in meta:
                        corr_copy.type = meta["domain"]
                    fresh = self._generate_fresh_value(existing, meta)

                    # Build the constraint expression
                    if isinstance(fresh, str):
                        lit = exp.Literal.string(fresh)
                    else:
                        lit = exp.Literal.number(fresh)
                    atom = exp.EQ(this=corr_copy, expression=lit)
                    return SolverConstraint(
                        target_tables=(outer_table,),
                        constraints=[atom],
                        alias_map=dict(self.alias_map),
                    )

        # Non-correlated or EXISTS_TRUE — return minimal constraint
        return SolverConstraint(
            target_tables=target.node.tables,
            constraints=[target.atom] if target.atom else [],
            alias_map=dict(self.alias_map),
        )

    def _generate_in_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for IN_MATCH or IN_NO_MATCH.

        Evaluates the inner query to get existing values, then builds
        col IN (values) or col NOT IN (values) expressions.
        """
        atom = target.atom
        if not isinstance(atom, exp.In):
            return SolverConstraint(
                target_tables=target.node.tables,
                constraints=[atom] if atom else [],
                alias_map=dict(self.alias_map),
            )

        subplan = self._find_subplan_for_target(target)
        if subplan is None:
            return SolverConstraint(
                target_tables=target.node.tables,
                constraints=[atom] if atom else [],
                alias_map=dict(self.alias_map),
            )

        inner_values = self._eval_inner_plan_values(subplan.inner)
        outer_col = atom.this

        if not isinstance(outer_col, exp.Column):
            return SolverConstraint(
                target_tables=target.node.tables,
                constraints=[atom] if atom else [],
                alias_map=dict(self.alias_map),
            )

        outer_table = self._resolve_table(outer_col, target.node.tables)
        meta = column_meta(outer_col)
        outer_col = outer_col.copy()
        if meta:
            outer_col.type = meta.get("domain")

        if target.target_outcome == BranchType.IN_MATCH:
            if inner_values:
                literals = [
                    exp.Literal.number(v) if isinstance(v, (int, float))
                    else exp.Literal.string(str(v))
                    for v in inner_values
                ]
                constraint = exp.In(this=outer_col.copy(), expressions=literals)
            else:
                constraint = exp.false()
        else:
            if inner_values:
                literals = [
                    exp.Literal.number(v) if isinstance(v, (int, float))
                    else exp.Literal.string(str(v))
                    for v in inner_values
                ]
                constraint = exp.Not(this=exp.In(this=outer_col.copy(), expressions=literals))
            else:
                constraint = exp.Is(this=outer_col.copy(), expression=exp.Not(this=exp.Null()))

        return SolverConstraint(
            target_tables=target.node.tables,
            constraints=[constraint],
            alias_map=dict(self.alias_map),
        )

    def _generate_distinct_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for DISTINCT_UNIQUE or DISTINCT_DUPLICATE.

        Uses row-indexed synthetic table names so the solver generates two rows.
        DISTINCT_DUPLICATE: same projected values across rows.
        DISTINCT_UNIQUE: different projected values across rows.
        """
        tables = target.node.tables
        step = self._find_step(target.node.step_id)
        if step is None or not isinstance(step, Project):
            return SolverConstraint(
                target_tables=tables,
                constraints=[exp.Literal.string("DISTINCT")],
                alias_map=dict(self.alias_map),
            )

        proj_cols: List[exp.Column] = []
        for proj in step.projections:
            if isinstance(proj, exp.Alias):
                proj = proj.this
            if isinstance(proj, exp.Column):
                proj_cols.append(proj)

        if not proj_cols:
            return SolverConstraint(
                target_tables=tables,
                constraints=[exp.Literal.string("DISTINCT")],
                alias_map=dict(self.alias_map),
            )

        # Resolve the first column's physical table to use as row-index prefix.
        first_real = self._resolve_table(proj_cols[0], tables)
        base = first_real if first_real else (tables[0] if tables else "")
        row_tables = [f"{base}__0", f"{base}__1"]
        row_alias_map = {row_tables[0]: base, row_tables[1]: base}

        constraints: List[exp.Expression] = []
        for col in proj_cols:
            real_table = self._resolve_table(col, tables)
            if not real_table:
                continue

            col_r0 = exp.Column(
                this=col.this.copy(),
                table=exp.to_identifier(f"{real_table}__0"),
            )
            col_r1 = exp.Column(
                this=col.this.copy(),
                table=exp.to_identifier(f"{real_table}__1"),
            )

            meta = column_meta(col)
            if meta and "domain" in meta:
                col_r0.type = meta["domain"]
                col_r1.type = meta["domain"]

            if target.target_outcome == BranchType.DISTINCT_DUPLICATE:
                constraints.append(exp.EQ(this=col_r0, expression=col_r1))
            else:
                constraints.append(exp.NEQ(this=col_r0, expression=col_r1))

        for col in proj_cols:
            real_table = self._resolve_table(col, tables)
            if not real_table:
                continue
            for i in range(2):
                col_ri = exp.Column(
                    this=col.this.copy(),
                    table=exp.to_identifier(f"{real_table}__{i}"),
                )
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col_ri.type = meta["domain"]
                constraints.append(exp.Is(this=col_ri, expression=exp.Not(this=exp.Null())))

        return SolverConstraint(
            target_tables=tuple(row_tables),
            constraints=constraints,
            alias_map=row_alias_map,
        )

    def _generate_group_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for GROUP_SINGLE or GROUP_MULTI.

        Uses row-indexed synthetic table names (t1__0, t1__1) so the solver
        generates two rows. Equality constraints ensure same/different GROUP BY keys.
        """
        tables = target.node.tables
        step = self._find_step(target.node.step_id)
        if step is None or not isinstance(step, Aggregate) or not step.group:
            return SolverConstraint(
                target_tables=tables,
                constraints=[exp.Literal.number(1)],
                alias_map=dict(self.alias_map),
            )

        group_cols: List[exp.Column] = []
        for group_expr in step.group.values():
            for col in group_expr.find_all(exp.Column):
                group_cols.append(col)

        if not group_cols:
            return SolverConstraint(
                target_tables=tables,
                constraints=[exp.Literal.number(1)],
                alias_map=dict(self.alias_map),
            )

        # Resolve the first column's physical table to use as row-index prefix.
        first_real = self._resolve_table(group_cols[0], tables)
        base = first_real if first_real else (tables[0] if tables else "")
        row_tables = [f"{base}__0", f"{base}__1"]
        row_alias_map = {row_tables[0]: base, row_tables[1]: base}

        constraints: List[exp.Expression] = []
        for col in group_cols:
            real_table = self._resolve_table(col, tables)
            if not real_table:
                continue

            col_r0 = exp.Column(
                this=col.this.copy(),
                table=exp.to_identifier(f"{real_table}__0"),
            )
            col_r1 = exp.Column(
                this=col.this.copy(),
                table=exp.to_identifier(f"{real_table}__1"),
            )

            meta = column_meta(col)
            if meta and "domain" in meta:
                col_r0.type = meta["domain"]
                col_r1.type = meta["domain"]

            if target.target_outcome == BranchType.GROUP_MULTI:
                constraints.append(exp.EQ(this=col_r0, expression=col_r1))
            else:
                constraints.append(exp.NEQ(this=col_r0, expression=col_r1))

        for col in group_cols:
            real_table = self._resolve_table(col, tables)
            if not real_table:
                continue
            for i in range(2):
                col_ri = exp.Column(
                    this=col.this.copy(),
                    table=exp.to_identifier(f"{real_table}__{i}"),
                )
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col_ri.type = meta["domain"]
                constraints.append(exp.Is(this=col_ri, expression=exp.Not(this=exp.Null())))

        return SolverConstraint(
            target_tables=tuple(row_tables),
            constraints=constraints,
            alias_map=row_alias_map,
        )

    def _annotate_column_types(self, constraints: List[exp.Expression]) -> None:
        """Set .type on Column nodes from planner-annotated column_meta.

        For original columns, column_meta provides the domain directly.
        For synthetic row-indexed columns (e.g., t1__0.col_A), resolve the
        real table name via alias_map and look up from instance schema.
        """
        from parseval.dtype import DataType

        for expr in constraints:
            for col in expr.find_all(exp.Column):
                if getattr(col, "type", None) is not None:
                    continue
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col.type = meta["domain"]
                    continue
                table_name = col.table or ""
                resolved = self.alias_map.resolve(table_name)
                if resolved in self.instance.tables:
                    col_type_str = self.instance.tables[resolved].get(col.name)
                    if col_type_str:
                        try:
                            col.type = DataType.build(col_type_str)
                        except Exception:
                            pass

    def _eval_inner_plan_values(self, root: Step) -> set:
        """Evaluate an inner plan and return the set of projected column values."""
        from parseval.plan.rex import concrete, Environment

        scans: List[Scan] = []
        filters: List[Filter] = []
        projects: List[Project] = []

        def collect(s: Step) -> None:
            if isinstance(s, Scan):
                scans.append(s)
            if isinstance(s, Filter):
                filters.append(s)
            if isinstance(s, Project):
                projects.append(s)
            for dep in s.chain_dependencies:
                collect(dep)

        collect(root)
        if not scans:
            return set()

        scan = scans[0]
        source = scan.source
        table_name = source.name if isinstance(source, exp.Table) else scan.name
        if table_name not in self.instance.tables:
            return set()

        rows = list(self.instance.get_rows(table_name))
        for filt in filters:
            if filt.condition is None:
                continue
            passing = []
            for row in rows:
                env = Environment({c: s.concrete for c, s in row.items()})
                if concrete(filt.condition, env) is True:
                    passing.append(row)
            rows = passing

        if not rows or not projects or not projects[0].projections:
            return set()

        projection = projects[0].projections[0]
        if isinstance(projection, exp.Alias):
            projection = projection.this

        values = set()
        for row in rows:
            env = Environment({c: s.concrete for c, s in row.items()})
            val = concrete(projection, env)
            values.add(val)
        return values

    def _generate_fresh_value(self, existing: set, meta: Optional[dict]) -> Any:
        """Generate a fresh value not in the existing set, respecting column type."""
        from parseval.dtype import DataType

        if meta and "domain" in meta:
            dtype = meta["domain"]
            if dtype.is_type(*DataType.INTEGER_TYPES):
                ints = {v for v in existing if isinstance(v, int)}
                return max(ints) + 1 if ints else 1
            if dtype.is_type(DataType.Type.TEXT):
                i = 1
                while f"fresh_{i}" in existing:
                    i += 1
                return f"fresh_{i}"

        # Fallback: try int, then string
        if existing and all(isinstance(v, int) for v in existing):
            return max(existing) + 1
        i = 1
        while f"fresh_{i}" in existing:
            i += 1
        return f"fresh_{i}"

    def _find_subplan_for_target(self, target: CoverageTarget):
        """Find the SubPlan step that corresponds to the target."""
        for step in self.plan.ordered_steps:
            if isinstance(step, SubPlan):
                # Match by step_id or by anchor expression
                if hasattr(step, 'anchor') and step.anchor is not None:
                    # Check if the anchor matches the target's predicate
                    if step.anchor.sql() == target.node.predicate.sql():
                        return step
        return None

    def _find_inner_scan_table(self, subplan) -> str:
        """Find the main table referenced in a SubPlan's inner plan."""
        stack = [subplan.inner]
        while stack:
            step = stack.pop()
            if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
                return step.source.name
            stack.extend(step.chain_dependencies)
        return ""

    def _find_step(self, step_id: str) -> Optional[Step]:
        for step in self.plan.ordered_steps:
            if self.plan.annotation_for(step).step_id == step_id:
                return step
        return None

    def _resolve_table(self, col: exp.Column, tables: Tuple[str, ...]) -> str:
        """Resolve a column's table qualifier to a physical table name."""
        if col.table:
            resolved = self.alias_map.resolve(col.table)
            if resolved in self.instance.tables:
                return resolved
        col_name = normalize_name(col.name)
        for t in tables:
            resolved = self.alias_map.resolve(t)
            if resolved in self.instance.tables and col_name in self.instance.tables[resolved]:
                return resolved
        return tables[0] if tables else ""

    def _resolve_table_name(self, name: str) -> str:
        """Resolve an alias or table name to the physical table name."""
        resolved = self.alias_map.resolve(name)
        return resolved if resolved in self.instance.tables else name


__all__ = ["ConstraintGenerator"]
