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
from parseval.plan.planner import Filter, Having, Join, Aggregate, Scan, SubPlan
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

    def __init__(self, plan: Plan, instance: Instance, dialect: str = "sqlite"):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect

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

        return SolverConstraint(
            target_tables=tables,
            constraints=constraints,
            join_equalities=join_equalities,
            atom=atom_constraint,
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

                    # Generate a fresh value
                    if existing and all(isinstance(v, int) for v in existing):
                        fresh = max(existing) + 1
                    else:
                        fresh = 99999

                    atom = exp.EQ(this=corr_col.copy(), expression=exp.Literal.number(fresh))
                    return SolverConstraint(
                        target_tables=(outer_table,),
                        constraints=[atom],
                        atom=atom,
                    )

        # Non-correlated or EXISTS_TRUE — return minimal constraint
        return SolverConstraint(
            target_tables=target.node.tables,
            constraints=[target.atom] if target.atom else [],
            atom=target.atom,
        )

    def _generate_in_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for IN_MATCH or IN_NO_MATCH."""
        return SolverConstraint(
            target_tables=target.node.tables,
            constraints=[target.atom] if target.atom else [],
            atom=target.atom,
        )

    def _generate_distinct_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for DISTINCT_UNIQUE or DISTINCT_DUPLICATE."""
        atom = exp.Literal.string("DISTINCT")
        return SolverConstraint(
            target_tables=target.node.tables,
            constraints=[atom],
            atom=atom,
        )

    def _generate_group_constraint(self, target: CoverageTarget) -> SolverConstraint:
        """Generate constraint for GROUP_SINGLE or GROUP_MULTI."""
        atom = exp.Literal.number(1)
        return SolverConstraint(
            target_tables=target.node.tables,
            constraints=[atom],
            atom=atom,
        )

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
        if col.table:
            real = normalize_name(col.table)
            if real in self.instance.tables:
                return real
        col_name = normalize_name(col.name)
        for t in tables:
            real = normalize_name(t)
            if real in self.instance.tables and col_name in self.instance.tables[real]:
                return real
        return tables[0] if tables else ""

    def _resolve_table_name(self, name: str) -> str:
        real = normalize_name(name)
        if real in self.instance.tables:
            return real
        return name


__all__ = ["ConstraintGenerator"]
