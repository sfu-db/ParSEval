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

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.constants import PlausibleBit, PlausibleType, StepType
from parseval.identity import (
    ColumnId,
    ColumnKind,
    RelationId,
    column_id,
    column_identity,
    identifier_name,
    physical_column,
    table_relation,
)
from parseval.plan import Plan, Step
from parseval.plan.planner import Filter, Join, Aggregate, Project, Scan, SubPlan
from parseval.plan.rex import negate_predicate, column_meta
from parseval.dtype import DataType
from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.solver import SolverConstraint
from parseval.solver.types import SolverVar, set_solver_var, solver_var

from .types import BranchNode, BranchPath, BranchType, CoverageTarget, PathPredicate

_POSITIVE_BITS = {
    PlausibleBit.TRUE,
    PlausibleBit.JOIN_TRUE,
    PlausibleBit.HAVING_TRUE,
    PlausibleBit.CASE_TAKEN,
    PlausibleBit.EXISTS_TRUE,
    PlausibleBit.IN_MATCH,
}
_NEGATIVE_BITS = {
    PlausibleBit.FALSE,
    PlausibleBit.HAVING_FALSE,
    PlausibleBit.CASE_SKIPPED,
    PlausibleBit.EXISTS_FALSE,
    PlausibleBit.IN_NO_MATCH,
}
_NULL_BITS = {
    PlausibleBit.NULL,
    PlausibleBit.JOIN_NULL,
    PlausibleBit.HAVING_NULL,
    PlausibleBit.GROUP_NULL,
}


def _step_type_for_node(node: BranchNode) -> StepType:
    raw = (node.step_type or node.site or "").lower()
    mapping = {
        "aggregate": StepType.AGGREGATE,
        "case_arm": StepType.PROJECT,
        "distinct": StepType.PROJECT,
        "filter": StepType.FILTER,
        "group": StepType.GROUPBY,
        "having": StepType.HAVING,
        "join": StepType.JOIN,
        "join_on": StepType.JOIN,
        "project": StepType.PROJECT,
        "scalar_subquery": StepType.FILTER,
        "subplan": StepType.FILTER,
    }
    return mapping.get(raw, StepType.ROOT)


@dataclass(frozen=True)
class PlausibleBranch:
    """One unexplored plausible branch selected for constraint generation."""

    step_type: StepType
    bit: PlausibleBit
    status: PlausibleType
    node: BranchNode
    atom_id: int = 0

    @classmethod
    def from_coverage_target(cls, target: CoverageTarget) -> "PlausibleBranch":
        return cls(
            step_type=_step_type_for_node(target.node),
            bit=PlausibleBit.from_int(target.target_outcome),
            status=PlausibleType.UNEXPLORED,
            node=target.node,
            atom_id=target.atom_id,
        )

    @property
    def atom(self) -> exp.Expression:
        if self.atom_id < 0:
            return self.node.predicate
        return self.node.atoms[self.atom_id]

    @property
    def target_outcome(self) -> BranchType:
        return BranchType.from_int(self.bit)

    @property
    def relation_ids(self) -> Tuple[RelationId, ...]:
        return self.node.tables


@dataclass
class PlausiblePath:
    """Plan path and branch constraints for one plausible branch."""

    branch: PlausibleBranch
    path_constraints: List[exp.Expression]
    branch_constraints: List[exp.Expression]
    join_equalities: List[Tuple[ColumnId, ColumnId]]


def _row_env_bindings(row) -> Dict[ColumnId, Any]:
    bindings: Dict[ColumnId, Any] = {}
    for column, symbol in row.items():
        if isinstance(column, ColumnId):
            bindings[column] = symbol.concrete
    return bindings


def _relation_table_name(rel: RelationId) -> str:
    """Extract the physical table name from a RelationId."""
    return rel.name.normalized if rel.name is not None else rel.display


def _relation_matches_name(rel: RelationId, name: str) -> bool:
    candidates = []
    if rel.name is not None:
        candidates.append(rel.name.normalized)
    if rel.alias is not None:
        candidates.append(rel.alias.normalized)
    return normalize_name(name) in candidates


def _column_expr_from_id(column: ColumnId) -> exp.Column:
    relation = column.relation
    table_name = ""
    if relation is not None:
        visible = relation.alias or relation.name
        if visible is not None:
            table_name = visible.raw
    return exp.Column(
        this=exp.to_identifier(column.name.raw, quoted=column.name.quoted),
        table=exp.to_identifier(table_name) if table_name else None,
    )


def _is_trivial_true(expression: exp.Expression) -> bool:
    simplified = expression.sql().upper()
    return simplified in {"TRUE", "1", "1 = 1", "TRUE AND TRUE"}


def _path_from_node_chain(target: CoverageTarget) -> BranchPath:
    nodes: List[BranchNode] = []
    current: Optional[BranchNode] = target.node
    while current is not None:
        nodes.append(current)
        current = current.parent
    nodes.reverse()

    predicates: List[PathPredicate] = []
    join_facts = []
    relations: List[RelationId] = []
    seen_relations: Set[RelationId] = set()
    for node in nodes:
        for relation in node.tables:
            if relation not in seen_relations:
                seen_relations.add(relation)
                relations.append(relation)
        join_facts.extend(node.join_facts)
        if node is target.node:
            predicates.append(
                PathPredicate(
                    node=node,
                    expression=target.atom,
                    outcome=target.target_outcome,
                )
            )
        elif node.site in {"filter", "having", "join_on"}:
            predicates.append(
                PathPredicate(
                    node=node,
                    expression=node.predicate,
                    outcome=BranchType.ATOM_TRUE,
                )
            )

    return BranchPath(
        target=target,
        predicates=tuple(predicates),
        join_facts=tuple(join_facts),
        relations=tuple(relations),
    )


class PlausibleConstraintCompiler:
    """Compile a :class:`PlausibleBranch` into a full :class:`SolverConstraint`.

    Collects query predicates + database constraints + JOIN conditions into
    one constraint set the solver satisfies simultaneously.
    """

    def __init__(self, plan: Plan, instance: Instance, dialect: str = "sqlite"):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect

    def generate(self, target: CoverageTarget) -> SolverConstraint:
        return self.compile(PlausibleBranch.from_coverage_target(target))

    def compile_path(self, path: BranchPath) -> SolverConstraint:
        if path.target.node.site == "scalar_subquery":
            return self._compile_scalar_subquery_path(path)

        constraints: List[exp.Expression] = []
        join_equalities: List[Tuple[ColumnId, ColumnId]] = []
        tables = path.relations or path.target.node.tables

        for predicate in path.predicates:
            constraints.extend(self._constraints_for_path_predicate(predicate))
        for fact in path.join_facts:
            join_equalities.extend(fact.equalities)
            if fact.predicate is not None and not _is_trivial_true(fact.predicate):
                constraints.append(fact.predicate.copy())

        constraints, extra_join_equalities = self._apply_database_constraints(
            constraints=constraints,
            tables=tables,
        )
        join_equalities.extend(extra_join_equalities)
        self._annotate_solver_vars(constraints, tables)
        lowered_joins = self._lower_join_equalities(join_equalities, tables)

        # Populate variables dict with type info for join equality columns
        # so the solver doesn't default them to TEXT.
        variables: Dict[SolverVar, DataType] = {}
        for left_sv, right_sv in lowered_joins:
            for sv in (left_sv, right_sv):
                if sv not in variables:
                    dtype = self._datatype_for_column_id(sv.column_id)
                    if dtype is not None:
                        variables[sv] = dtype

        return SolverConstraint(
            target_relations=tables,
            constraints=constraints,
            join_equalities=lowered_joins,
            variables=variables,
        )

    def compile(self, branch: PlausibleBranch) -> SolverConstraint:
        node = branch.node

        # --- Handle SubPlan branches ---
        if node.site == "exists":
            return self._generate_exists_constraint(branch)
        elif node.site == "in":
            return self._generate_in_constraint(branch)

        # --- Handle DISTINCT branches ---
        if node.site == "distinct":
            return self._generate_distinct_constraint(branch)

        # --- Handle GROUP branches ---
        if node.site == "group":
            return self._generate_group_constraint(branch)

        target = CoverageTarget(
            node=node,
            atom_id=branch.atom_id,
            target_outcome=branch.target_outcome,
        )
        return self.compile_path(_path_from_node_chain(target))

    def _constraints_for_path_predicate(self, predicate: PathPredicate) -> List[exp.Expression]:
        expression = predicate.expression.copy()
        if predicate.outcome in _POSITIVE_BITS:
            if predicate.node.site == "filter":
                return [predicate.node.predicate.copy()]
            return [] if _is_trivial_true(expression) else [expression]
        if predicate.outcome in _NEGATIVE_BITS:
            return [negate_predicate(expression)]
        if predicate.outcome in _NULL_BITS:
            columns = list(expression.find_all(exp.Column))
            if not columns:
                return [expression]
            return [
                expression,
                exp.Is(this=columns[0].copy(), expression=exp.Null()),
            ]
        if predicate.outcome in {PlausibleBit.JOIN_MATCH, PlausibleBit.JOIN_TRUE}:
            return [] if _is_trivial_true(expression) else [expression]
        if predicate.outcome == PlausibleBit.JOIN_NO_MATCH:
            return [negate_predicate(expression)] if not _is_trivial_true(expression) else []
        return [expression]

    def _compile_scalar_subquery_path(self, path: BranchPath) -> SolverConstraint:
        atom = path.target.atom
        if not isinstance(atom, exp.EQ):
            return SolverConstraint(target_relations=path.relations, constraints=[])

        left = atom.left
        right = atom.right
        outer_col = left if isinstance(left, exp.Column) else right if isinstance(right, exp.Column) else None
        subquery = right if isinstance(right, exp.Subquery) else left if isinstance(left, exp.Subquery) else None
        if outer_col is None or subquery is None:
            return SolverConstraint(target_relations=path.relations, constraints=[])

        outer_col_id = column_identity(outer_col)
        if outer_col_id is None or outer_col_id.relation is None:
            outer_rel = self._resolve_relation(outer_col, path.relations)
            if outer_rel is None:
                return SolverConstraint(target_relations=path.relations, constraints=[])
            outer_col_id = physical_column(outer_col.name, outer_rel, dialect=self.dialect)

        inner_projection = self._scalar_subquery_projection(subquery)
        if inner_projection is None:
            return SolverConstraint(target_relations=path.relations, constraints=[])
        inner_col_id = column_identity(inner_projection)
        if inner_col_id is None or inner_col_id.relation is None:
            inner_tables = tuple(
                table_relation(table.name, dialect=self.dialect)
                for table in subquery.find_all(exp.Table)
                if table.name in self.instance.tables
            )
            inner_rel = self._resolve_relation(inner_projection, inner_tables)
            if inner_rel is None:
                return SolverConstraint(target_relations=path.relations, constraints=[])
            inner_col_id = physical_column(inner_projection.name, inner_rel, dialect=self.dialect)

        constraints: List[exp.Expression] = [
            exp.EQ(
                this=self._constraint_column(outer_col_id),
                expression=self._constraint_column(inner_col_id),
            )
        ]
        join_equalities: List[Tuple[ColumnId, ColumnId]] = []
        for predicate in path.predicates:
            if predicate.node.site == "scalar_subquery":
                continue
            constraints.extend(self._constraints_for_path_predicate(predicate))
        for fact in path.join_facts:
            join_equalities.extend(fact.equalities)
            if fact.predicate is not None and not _is_trivial_true(fact.predicate):
                constraints.append(fact.predicate.copy())

        relations = tuple(
            dict.fromkeys(
                rel
                for rel in tuple(path.relations) + (outer_col_id.relation, inner_col_id.relation)
                if rel is not None
            )
        )
        constraints, extra_joins = self._apply_database_constraints(constraints, relations)
        join_equalities.extend(extra_joins)
        self._annotate_solver_vars(constraints, relations)
        return SolverConstraint(
            target_relations=relations,
            constraints=constraints,
            join_equalities=self._lower_join_equalities(join_equalities, relations),
        )

    def _scalar_subquery_projection(self, subquery: exp.Subquery) -> exp.Column | None:
        inner = subquery.this
        if isinstance(inner, exp.Select):
            for projection in inner.expressions:
                expr = projection.this if isinstance(projection, exp.Alias) else projection
                if isinstance(expr, exp.Column):
                    return expr
                found = next(expr.find_all(exp.Column), None)
                if found is not None:
                    return found
        return next(subquery.find_all(exp.Column), None)

    def _apply_database_constraints(
        self,
        constraints: List[exp.Expression],
        tables: Tuple[RelationId, ...],
    ) -> Tuple[List[exp.Expression], List[Tuple[ColumnId, ColumnId]]]:
        path_predicates = list(constraints)
        not_null_columns: List[ColumnId] = []
        avoid_values: Dict[ColumnId, Set[Any]] = {}
        foreign_keys: List[Tuple[ColumnId, RelationId, ColumnId]] = []
        seen_cols: Set[ColumnId] = set()

        for expr in path_predicates:
            for col in expr.find_all(exp.Column):
                col_id = column_identity(col)
                if col_id is None:
                    rel = self._resolve_relation(col, tables)
                    if rel is None:
                        continue
                    col_id = physical_column(col.name, rel, dialect=self.dialect)
                if col_id in seen_cols:
                    continue
                seen_cols.add(col_id)
                meta = column_meta(col)
                if meta is not None and not meta["nullable"]:
                    not_null_columns.append(col_id)
                if meta is not None and meta["unique"] and col_id.relation is not None:
                    existing = {
                        sym.concrete
                        for sym in self.instance.get_column_data(col_id.relation, col_id)
                        if sym.concrete is not None
                    }
                    if existing:
                        avoid_values[col_id] = existing

        for rel in tables:
            table_name = _relation_table_name(rel)
            if table_name not in self.instance.tables:
                continue
            for col_name in self.instance.tables[table_name]:
                col_id = physical_column(col_name, rel, dialect=self.dialect)
                if col_id in seen_cols:
                    continue
                seen_cols.add(col_id)
                if not self.instance.nullable(rel, col_id):
                    not_null_columns.append(col_id)
                if self.instance.is_unique(rel, col_id):
                    existing = {
                        sym.concrete
                        for sym in self.instance.get_column_data(rel, col_id)
                        if sym.concrete is not None
                    }
                    if existing:
                        avoid_values[col_id] = existing

            for fk in self.instance.get_foreign_key(rel):
                local_col = physical_column(fk.expressions[0].name, rel, dialect=self.dialect)
                ref = fk.args.get("reference")
                ref_table_node = ref.find(exp.Table) if ref is not None else None
                ref_col = self.instance.resolve_fk_ref_column(fk)
                ref_rel = (
                    self._resolve_table_relation(ref_table_node, tables)
                    if ref_table_node is not None
                    else None
                )
                if ref_rel is not None and ref_col is not None:
                    foreign_keys.append(
                        (
                            local_col,
                            ref_rel,
                            physical_column(ref_col, ref_rel, dialect=self.dialect),
                        )
                    )

            for check_expr in self.instance.get_check_constraints(rel):
                constraints.append(check_expr)

        for col_id in not_null_columns:
            constraints.append(
                exp.Is(this=self._constraint_column(col_id), expression=exp.Not(this=exp.Null()))
            )
        for col_id, vals in avoid_values.items():
            constraints.append(
                exp.Not(
                    this=exp.In(
                        this=self._constraint_column(col_id),
                        expressions=[
                            exp.Literal.number(v)
                            if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in vals
                        ],
                    )
                )
            )
        for local_col, ref_rel, ref_col in foreign_keys:
            parent_vals = []
            if _relation_table_name(ref_rel) in self.instance.tables:
                for row in self.instance.get_rows(ref_rel):
                    try:
                        val = row[ref_col].concrete
                    except KeyError:
                        continue
                    if val is not None:
                        parent_vals.append(val)
            if parent_vals:
                constraints.append(
                    exp.In(
                        this=self._constraint_column(local_col),
                        expressions=[
                            exp.Literal.number(v)
                            if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in parent_vals
                        ],
                    )
                )
        return constraints, []

    def _lower_join_equalities(
        self,
        join_equalities: List[Tuple[ColumnId, ColumnId] | Tuple[SolverVar, SolverVar]],
        tables: Tuple[RelationId, ...],
    ) -> List[Tuple[SolverVar, SolverVar]]:
        del tables
        lowered: List[Tuple[SolverVar, SolverVar]] = []
        for item in join_equalities:
            if len(item) == 2 and all(isinstance(part, SolverVar) for part in item):
                lowered.append(item)
                continue
            if len(item) != 2 or not all(isinstance(part, ColumnId) for part in item):
                continue
            left_id, right_id = item
            if left_id.relation is None or right_id.relation is None:
                continue
            lowered.append((
                SolverVar(column_id=left_id, relation_id=left_id.relation),
                SolverVar(column_id=right_id, relation_id=right_id.relation),
            ))
        return lowered

    def _generate_exists_constraint(self, target: PlausibleBranch) -> SolverConstraint:
        subplan = self._find_subplan_for_target(target)
        if subplan and subplan.correlation:
            corr_col = subplan.correlation[0]
            outer_rel = self._resolve_relation(corr_col, target.node.tables)

            if outer_rel is not None and target.bit == PlausibleBit.EXISTS_FALSE:
                inner_table = self._find_inner_scan_table(subplan)
                if inner_table:
                    from parseval.identity import table_relation as _tr3
                    inner_rel = _tr3(inner_table, dialect=self.dialect)
                    existing = set()
                    for row in self.instance.get_rows(inner_rel):
                        if corr_col.name in row.columns:
                            val = row[corr_col.name].concrete
                            if val is not None:
                                existing.add(val)

                    corr_copy = corr_col.copy()
                    meta = column_meta(corr_col)
                    if meta and "domain" in meta:
                        corr_copy.type = meta["domain"]
                    fresh = self._generate_fresh_value(existing, meta)

                    if isinstance(fresh, str):
                        lit = exp.Literal.string(fresh)
                    else:
                        lit = exp.Literal.number(fresh)
                    atom = exp.EQ(this=corr_copy, expression=lit)
                    self._annotate_solver_vars([atom], (outer_rel,))
                    return SolverConstraint(
                        target_relations=(outer_rel,),
                        constraints=[atom],
                    )

        return SolverConstraint(
            target_relations=target.node.tables,
            constraints=[target.atom] if target.atom else [],
        )

    def _generate_in_constraint(self, target: PlausibleBranch) -> SolverConstraint:
        atom = target.atom
        if not isinstance(atom, exp.In):
            return SolverConstraint(
                target_relations=target.node.tables,
                constraints=[atom] if atom else [],
            )

        subplan = self._find_subplan_for_target(target)
        if subplan is None:
            return SolverConstraint(
                target_relations=target.node.tables,
                constraints=[atom] if atom else [],
            )

        inner_values = self._eval_inner_plan_values(subplan.inner)
        outer_col = atom.this

        if not isinstance(outer_col, exp.Column):
            return SolverConstraint(
                target_relations=target.node.tables,
                constraints=[atom] if atom else [],
            )

        meta = column_meta(outer_col)
        outer_col = outer_col.copy()
        if meta:
            outer_col.type = meta.get("domain")

        if target.bit == PlausibleBit.IN_MATCH:
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
            target_relations=target.node.tables,
            constraints=[constraint],
        )

    def _generate_distinct_constraint(self, target: PlausibleBranch) -> SolverConstraint:
        tables = target.node.tables
        step = target.node.step
        if step is None or not isinstance(step, Project):
            return SolverConstraint(
                target_relations=tables,
                constraints=[exp.Literal.string("DISTINCT")],
            )

        proj_cols: List[exp.Column] = []
        for proj in step.projections:
            if isinstance(proj, exp.Alias):
                proj = proj.this
            if isinstance(proj, exp.Column):
                proj_cols.append(proj)

        if not proj_cols:
            return SolverConstraint(
                target_relations=tables,
                constraints=[exp.Literal.string("DISTINCT")],
            )

        constraints: List[exp.Expression] = []
        for col in proj_cols:
            col_id = self._column_id_for_expr(col, tables)
            if col_id is None or col_id.relation is None:
                continue

            col_r0 = self._constraint_column(col_id, row_scope="r0")
            col_r1 = self._constraint_column(col_id, row_scope="r1")

            meta = column_meta(col)
            if meta and "domain" in meta:
                col_r0.type = meta["domain"]
                col_r1.type = meta["domain"]

            if target.bit in {PlausibleBit.DISTINCT_DUPLICATE, PlausibleBit.DUPLICATE}:
                constraints.append(exp.EQ(this=col_r0, expression=col_r1))
            else:
                constraints.append(exp.NEQ(this=col_r0, expression=col_r1))

        for col in proj_cols:
            col_id = self._column_id_for_expr(col, tables)
            if col_id is None or col_id.relation is None:
                continue
            for i in range(2):
                col_ri = self._constraint_column(col_id, row_scope=f"r{i}")
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col_ri.type = meta["domain"]
                constraints.append(exp.Is(this=col_ri, expression=exp.Not(this=exp.Null())))

        return SolverConstraint(
            target_relations=tables,
            constraints=constraints,
        )

    def _generate_group_constraint(self, target: PlausibleBranch) -> SolverConstraint:
        tables = target.node.tables
        step = target.node.step
        if step is None or not isinstance(step, Aggregate) or not step.group:
            return SolverConstraint(
                target_relations=tables,
                constraints=[exp.Literal.number(1)],
            )

        group_cols: List[exp.Column] = []
        for group_expr in step.group.values():
            for col in group_expr.find_all(exp.Column):
                group_cols.append(col)

        if not group_cols:
            return SolverConstraint(
                target_relations=tables,
                constraints=[exp.Literal.number(1)],
            )

        constraints: List[exp.Expression] = []
        for col in group_cols:
            col_id = self._column_id_for_expr(col, tables)
            if col_id is None or col_id.relation is None:
                continue

            col_r0 = self._constraint_column(col_id, row_scope="r0")
            col_r1 = self._constraint_column(col_id, row_scope="r1")

            meta = column_meta(col)
            if meta and "domain" in meta:
                col_r0.type = meta["domain"]
                col_r1.type = meta["domain"]

            if target.bit in {PlausibleBit.GROUP_MULTI, PlausibleBit.GROUP_SIZE}:
                constraints.append(exp.EQ(this=col_r0, expression=col_r1))
            else:
                constraints.append(exp.NEQ(this=col_r0, expression=col_r1))

        for col in group_cols:
            col_id = self._column_id_for_expr(col, tables)
            if col_id is None or col_id.relation is None:
                continue
            for i in range(2):
                col_ri = self._constraint_column(col_id, row_scope=f"r{i}")
                meta = column_meta(col)
                if meta and "domain" in meta:
                    col_ri.type = meta["domain"]
                constraints.append(exp.Is(this=col_ri, expression=exp.Not(this=exp.Null())))

        return SolverConstraint(
            target_relations=tables,
            constraints=constraints,
        )

    def _constraint_column(
        self,
        column: ColumnId,
        row_scope: str | None = None,
    ) -> exp.Column:
        col = _column_expr_from_id(column)
        dtype = self._datatype_for_column_id(column)
        if dtype is not None:
            col.type = dtype
        if column.relation is not None:
            set_solver_var(
                col,
                SolverVar(
                    column_id=column,
                    relation_id=column.relation,
                    row_scope=row_scope,
                ),
            )
        return col

    def _datatype_for_column_id(self, column: ColumnId):
        lookup = column.source_column_id or column
        if lookup.relation is None:
            return None
        try:
            catalog_column = self.instance.catalog_column(
                lookup.relation,
                exp.to_identifier(lookup.name.raw, quoted=lookup.name.quoted),
            )
        except Exception:
            table_name = _relation_table_name(lookup.relation)
            type_sql = self.instance.tables.get(table_name, {}).get(lookup.name.normalized)
            if type_sql is None:
                return None
            from parseval.dtype import DataType

            try:
                return DataType.build(type_sql)
            except Exception:
                return None
        return catalog_column.datatype

    def _annotate_solver_vars(
        self,
        constraints: List[exp.Expression],
        tables: Tuple[RelationId, ...],
    ) -> None:
        """Set SolverVar metadata on Column nodes so the solver can assign values.

        Also bridges the ``column_meta["domain"]`` → ``col.type`` gap: the
        planner stores semantic types in ``column_meta`` but the solver reads
        from ``col.type``.  Without this, path-predicate columns copied from
        step expressions have ``col.type = None`` and the solver defaults to
        TEXT, generating string values for INT columns.
        """
        for expr in constraints:
            for col in expr.find_all(exp.Column):
                if solver_var(col) is not None:
                    # Even if SolverVar exists, ensure col.type is set.
                    if col.type is None:
                        self._set_type_from_metadata_or_catalog(col, tables)
                    continue
                col_id = self._column_id_for_expr(col, tables)
                if col_id is None:
                    continue
                # Set col.type from column_meta or catalog lookup.
                if col.type is None:
                    self._set_type_from_col_id(col, col_id)
                rel_id = col_id.relation
                if rel_id is None and tables:
                    rel_id = tables[0]
                if rel_id is None:
                    continue
                sv = SolverVar(column_id=col_id, relation_id=rel_id)
                set_solver_var(col, sv)

    def _set_type_from_metadata_or_catalog(
        self,
        col: exp.Column,
        tables: Tuple[RelationId, ...],
    ) -> None:
        """Set ``col.type`` from ``column_meta["domain"]`` or catalog lookup."""
        meta = column_meta(col)
        if meta is not None and "domain" in meta:
            col.type = meta["domain"]
            return
        col_id = self._column_id_for_expr(col, tables)
        if col_id is not None:
            self._set_type_from_col_id(col, col_id)

    def _set_type_from_col_id(self, col: exp.Column, col_id: ColumnId) -> None:
        """Set ``col.type`` from the catalog using a resolved ColumnId."""
        dtype = self._datatype_for_column_id(col_id)
        if dtype is not None:
            col.type = dtype

    def _column_id_for_expr(
        self,
        col: exp.Column,
        tables: Tuple[RelationId, ...],
    ) -> ColumnId | None:
        col_id = column_identity(col)
        if col_id is not None:
            return col_id
        rel = self._resolve_relation(col, tables)
        if rel is None:
            return None
        return column_id(
            ColumnKind.SYNTHETIC,
            identifier_name(col.name, dialect=self.dialect),
            rel,
        )

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

        from parseval.identity import table_relation as _tr4
        rows = list(self.instance.get_rows(_tr4(table_name, dialect=self.dialect)))
        for filt in filters:
            if filt.condition is None:
                continue
            passing = []
            for row in rows:
                env = Environment(_row_env_bindings(row))
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
            env = Environment(_row_env_bindings(row))
            val = concrete(projection, env)
            values.add(val)
        return values

    def _generate_fresh_value(self, existing: set, meta: Optional[dict]) -> Any:
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

        if existing and all(isinstance(v, int) for v in existing):
            return max(existing) + 1
        i = 1
        while f"fresh_{i}" in existing:
            i += 1
        return f"fresh_{i}"

    def _find_subplan_for_target(self, target: CoverageTarget):
        for step in self.plan.ordered_steps:
            if isinstance(step, SubPlan):
                if hasattr(step, 'anchor') and step.anchor is not None:
                    if step.anchor.sql() == target.node.predicate.sql():
                        return step
        return None

    def _find_inner_scan_table(self, subplan) -> str:
        stack = [subplan.inner]
        while stack:
            step = stack.pop()
            if isinstance(step, Scan) and step.source and isinstance(step.source, exp.Table):
                return step.source.name
            stack.extend(step.chain_dependencies)
        return ""

    def _resolve_relation(self, col: exp.Column, tables: Tuple[RelationId, ...]) -> RelationId | None:
        """Resolve a column's table qualifier to a RelationId."""
        if col.table:
            table_name = normalize_name(col.table)
            for rel in tables:
                if _relation_matches_name(rel, table_name):
                    return rel
            if table_name in self.instance.tables:
                return table_relation(table_name, dialect=self.dialect)
        col_name = normalize_name(col.name)
        for rel in tables:
            name = _relation_table_name(rel)
            if name in self.instance.tables and col_name in self.instance.tables[name]:
                return rel
        return tables[0] if tables else None

    def _resolve_table_relation(
        self,
        table: exp.Table,
        tables: Tuple[RelationId, ...],
    ) -> RelationId | None:
        table_name = normalize_name(table.name)
        for rel in tables:
            if _relation_matches_name(rel, table_name):
                return rel
        if table_name in self.instance.tables:
            return table_relation(table_name, dialect=self.dialect)
        return None


ConstraintGenerator = PlausibleConstraintCompiler


__all__ = [
    "PlausibleBranch",
    "PlausiblePath",
    "PlausibleConstraintCompiler",
    "ConstraintGenerator",
]
