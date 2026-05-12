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

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.plan import Plan, Step
from parseval.plan.planner import Filter, Having, Join, Aggregate, Scan
from parseval.plan.rex import negate_predicate
from parseval.helper import normalize_name
from parseval.instance import Instance

from .types import BranchType, CoverageTarget


@dataclass
class SolverConstraint:
    """The full constraint set the solver must satisfy.

    Includes query predicates, database constraints, and JOIN conditions
    — everything needed to produce a valid, coordinated row (or set of
    rows across tables).
    """

    target_tables: Tuple[str, ...]
    atom: exp.Expression
    target_outcome: BranchType
    # Upstream query predicates (WHERE, JOIN ON) the row must satisfy.
    path_predicates: List[exp.Expression] = field(default_factory=list)
    # JOIN key equalities: (left_table, left_col, right_table, right_col).
    join_equalities: List[Tuple[str, str, str, str]] = field(default_factory=list)
    # Columns that must be NULL (for ATOM_NULL targets).
    null_columns: List[exp.Column] = field(default_factory=list)
    # NOT NULL columns that must have a value.
    not_null_columns: List[Tuple[str, str]] = field(default_factory=list)
    # Unique columns → existing values to avoid.
    avoid_values: Dict[str, Set[Any]] = field(default_factory=dict)
    # FK relationships: (child_table, child_col, parent_table, parent_col).
    foreign_keys: List[Tuple[str, str, str, str]] = field(default_factory=list)
    # Alias map for column resolution.
    alias_map: Dict[str, str] = field(default_factory=dict)


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

        # --- Database constraints for all target tables ---
        not_null_columns: List[Tuple[str, str]] = []
        avoid_values: Dict[str, Set[Any]] = {}
        foreign_keys: List[Tuple[str, str, str, str]] = []

        for table_name in tables:
            real_table = self._resolve_table_name(table_name)
            if real_table not in self.instance.tables:
                continue

            for col_name in self.instance.tables[real_table]:
                if not self.instance.nullable(real_table, col_name):
                    not_null_columns.append((real_table, col_name))
                if self.instance.is_unique(real_table, col_name):
                    existing = {
                        sym.concrete
                        for sym in self.instance.get_column_data(real_table, col_name)
                        if sym.concrete is not None
                    }
                    if existing:
                        avoid_values[f"{real_table}.{col_name}"] = existing

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

        return SolverConstraint(
            target_tables=tables,
            atom=atom_constraint,
            target_outcome=outcome,
            path_predicates=path_predicates,
            join_equalities=join_equalities,
            null_columns=null_columns,
            not_null_columns=not_null_columns,
            avoid_values=avoid_values,
            foreign_keys=foreign_keys,
        )

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


__all__ = ["ConstraintGenerator", "SolverConstraint"]
