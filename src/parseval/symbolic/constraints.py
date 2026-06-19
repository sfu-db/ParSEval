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

from .types import (
    BranchNode,
    BranchPath,
    BranchType,
    CoverageTarget,
    OperatorObligation,
    PathPredicate,
)

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
    obligations: List[OperatorObligation] = []
    relations: List[RelationId] = []
    seen_relations: Set[RelationId] = set()
    for node in nodes:
        for relation in node.tables:
            if relation not in seen_relations:
                seen_relations.add(relation)
                relations.append(relation)
        join_facts.extend(node.join_facts)
        obligations.extend(node.obligations)
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
        obligations=tuple(obligations),
        relations=tuple(relations),
    )


class ConstraintGenerator:
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
        constraints.extend(self._constraints_for_obligations(path.obligations))
        for fact in path.join_facts:
            join_equalities.extend(fact.equalities)
            if fact.predicate is not None and not _is_trivial_true(fact.predicate):
                constraints.append(fact.predicate.copy())

        row_scope_by_relation = self._join_row_scopes(path.join_facts)
        constraints, extra_join_equalities = self._apply_database_constraints(
            constraints=constraints,
            tables=tables,
        )
        join_equalities.extend(extra_join_equalities)
        self._annotate_solver_vars(
            constraints,
            tables,
            row_scope_by_relation=row_scope_by_relation,
        )
        lowered_joins = self._lower_join_equalities(
            join_equalities,
            tables,
            row_scope_by_relation=row_scope_by_relation,
        )

        # Populate variables dict with type info for join equality columns
        # so the solver doesn't default them to TEXT.
        variables: Dict[SolverVar, DataType] = {}
        for left_sv, right_sv in lowered_joins:
            for sv in (left_sv, right_sv):
                if sv not in variables:
                    dtype = self._datatype_for_column_id(sv.column_id)
                    if dtype is not None:
                        variables[sv] = dtype

        storage_relations = self._storage_relations_for_constraint(
            constraints,
            lowered_joins,
            variables,
            path.obligations,
        )

        return SolverConstraint(
            target_relations=tables,
            constraints=constraints,
            join_equalities=lowered_joins,
            variables=variables,
            storage_relations=storage_relations,
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
                return self._positive_predicate_constraints(predicate.node.predicate)
            return [] if _is_trivial_true(expression) else self._positive_predicate_constraints(expression)
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

    def _positive_predicate_constraints(self, expression: exp.Expression) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for conjunct in self._split_conjuncts(expression):
            unwrapped = self._unwrap_scalar_subquery_comparison(conjunct)
            if unwrapped is not None:
                constraints.extend(unwrapped)
            else:
                constraints.append(conjunct.copy())
        return constraints

    def _split_conjuncts(self, expression: exp.Expression) -> List[exp.Expression]:
        if isinstance(expression, exp.And):
            return self._split_conjuncts(expression.left) + self._split_conjuncts(expression.right)
        if isinstance(expression, exp.Paren):
            return self._split_conjuncts(expression.this)
        return [expression]

    def _unwrap_scalar_subquery_comparison(
        self,
        expression: exp.Expression,
    ) -> List[exp.Expression] | None:
        if not isinstance(expression, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ)):
            return None

        left = expression.this
        right = expression.expression
        if left is None or right is None:
            return None

        if right.find(exp.Subquery):
            outer_expr = left
            subquery_expr = right
            subquery_on_left = False
        elif left.find(exp.Subquery):
            outer_expr = right
            subquery_expr = left
            subquery_on_left = True
        else:
            return None

        subquery = (
            subquery_expr
            if isinstance(subquery_expr, exp.Subquery)
            else subquery_expr.find(exp.Subquery)
        )
        if subquery is None or not isinstance(subquery.this, exp.Select):
            return None

        inner_expr = self._scalar_subquery_value_expression(subquery)
        if inner_expr is None:
            return None
        inner_scope = "scalar_subquery"
        inner_relations = self._scalar_subquery_relations(subquery)
        self._scope_expression_columns(inner_expr, inner_scope, inner_relations)

        constraints: List[exp.Expression] = []
        if subquery_on_left:
            constraints.append(type(expression)(this=inner_expr, expression=outer_expr.copy()))
        else:
            constraints.append(type(expression)(this=outer_expr.copy(), expression=inner_expr))

        constraints.extend(
            self._scalar_subquery_distinct_storage_constraints(
                outer_expr,
                inner_relations,
                inner_scope,
            )
        )
        constraints.extend(
            self._scalar_subquery_inner_constraints(
                subquery,
                row_scope=inner_scope,
                relations=inner_relations,
            )
        )
        return constraints

    def _scalar_subquery_distinct_storage_constraints(
        self,
        outer_expr: exp.Expression,
        inner_relations: Tuple[RelationId, ...],
        inner_scope: str,
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        outer_relations: List[RelationId] = []
        seen_outer: Set[RelationId] = set()
        for col in outer_expr.find_all(exp.Column):
            col_id = column_identity(col)
            relation = col_id.relation if col_id is not None else None
            if relation is None or relation in seen_outer:
                continue
            seen_outer.add(relation)
            outer_relations.append(relation)

        for outer_relation in outer_relations:
            try:
                outer_table = self.instance._table_key_for_storage(outer_relation)
            except Exception:
                continue
            key_names = self._identity_column_names(outer_table)
            if not key_names:
                continue
            for inner_relation in inner_relations:
                try:
                    inner_table = self.instance._table_key_for_storage(inner_relation)
                except Exception:
                    continue
                if inner_table != outer_table:
                    continue
                for key_name in key_names:
                    constraints.append(
                        exp.NEQ(
                            this=self._constraint_column(
                                physical_column(key_name, outer_relation, dialect=self.dialect)
                            ),
                            expression=self._constraint_column(
                                physical_column(key_name, inner_relation, dialect=self.dialect),
                                row_scope=inner_scope,
                            ),
                        )
                    )
        return constraints

    def _identity_column_names(self, table_name: str) -> Tuple[str, ...]:
        names: List[str] = []

        def add(value: Any) -> None:
            raw = getattr(value, "name", value)
            normalized = normalize_name(raw)
            if normalized not in names:
                names.append(normalized)

        for key in self.instance.primary_keys.get(table_name, ()) or ():
            add(key)
        for unique_columns in self.instance.unique_constraints.get(table_name, ()) or ():
            for key in unique_columns:
                add(key)
        return tuple(names)

    def _scope_expression_columns(
        self,
        expression: exp.Expression,
        row_scope: str,
        relations: Tuple[RelationId, ...] = (),
    ) -> None:
        for col in expression.find_all(exp.Column):
            col_id = column_identity(col)
            if col_id is None and relations:
                rel = self._resolve_relation(col, relations)
                if rel is not None:
                    col_id = physical_column(col.name, rel, dialect=self.dialect)
            if col_id is None or col_id.relation is None:
                continue
            set_solver_var(
                col,
                SolverVar(
                    column_id=col_id,
                    relation_id=col_id.relation,
                    row_scope=row_scope,
                ),
            )
            if col.type is None:
                self._set_type_from_col_id(col, col_id)

    def _scalar_subquery_value_expression(
        self,
        subquery: exp.Subquery,
    ) -> exp.Expression | None:
        subplan = self._find_subplan_for_subquery(subquery)
        if subplan is not None and subplan.inner is not None:
            for step in self._iter_steps_with_subplans(subplan.inner):
                if not isinstance(step, Aggregate):
                    continue
                for operand in getattr(step, "operands", ()) or ():
                    expression = operand.this if isinstance(operand, exp.Alias) else operand
                    return expression.copy()

        inner = subquery.this
        if not isinstance(inner, exp.Select) or not inner.expressions:
            return None
        projection = inner.expressions[0]
        expression = projection.this if isinstance(projection, exp.Alias) else projection
        if isinstance(expression, (exp.Avg, exp.Min, exp.Max, exp.Sum)):
            return expression.this.copy()
        return expression.copy()

    def _scalar_subquery_inner_constraints(
        self,
        subquery: exp.Subquery,
        row_scope: str | None = None,
        relations: Tuple[RelationId, ...] = (),
    ) -> List[exp.Expression]:
        inner = subquery.this
        if not isinstance(inner, exp.Select):
            return []
        constraints: List[exp.Expression] = []
        for join in inner.find_all(exp.Join):
            on_expr = join.args.get("on")
            if isinstance(on_expr, exp.Expression):
                for conjunct in self._split_conjuncts(on_expr):
                    if conjunct.find(exp.Subquery):
                        continue
                    copied = conjunct.copy()
                    if row_scope is not None:
                        self._scope_expression_columns(copied, row_scope, relations)
                    constraints.append(copied)
        where = inner.args.get("where")
        if where is not None and isinstance(where.this, exp.Expression):
            for conjunct in self._split_conjuncts(where.this):
                if conjunct.find(exp.Subquery):
                    continue
                copied = conjunct.copy()
                if row_scope is not None:
                    self._scope_expression_columns(copied, row_scope, relations)
                constraints.append(copied)
        return constraints

    def _scalar_subquery_relations(
        self,
        subquery: exp.Subquery,
    ) -> Tuple[RelationId, ...]:
        subplan = self._find_subplan_for_subquery(subquery)
        if subplan is None or subplan.inner is None:
            return ()
        relations: List[RelationId] = []
        seen: Set[RelationId] = set()
        for step in self._iter_steps_with_subplans(subplan.inner):
            if not isinstance(step, Scan) or getattr(step, "relation_id", None) is None:
                continue
            relation = step.relation_id
            if relation in seen:
                continue
            seen.add(relation)
            relations.append(relation)
        return tuple(relations)

    def _constraints_for_obligations(
        self,
        obligations: Tuple[OperatorObligation, ...],
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        scan_obligations: Dict[RelationId, OperatorObligation] = {}
        for obligation in obligations:
            if obligation.kind != "scan_exists" or obligation.relation is None:
                continue
            existing = scan_obligations.get(obligation.relation)
            if existing is None or obligation.row_count > existing.row_count:
                scan_obligations[obligation.relation] = obligation

        for obligation in scan_obligations.values():
            if not obligation.columns:
                continue
            identity_col = obligation.columns[0]
            row_count = max(int(obligation.row_count or 1), 1)
            existing_values = self._existing_values_for_obligation(obligation, identity_col)
            scoped_columns: List[exp.Column] = []
            for index in range(row_count):
                row_scope = obligation.row_scope or (f"r{index}" if row_count > 1 else None)
                col = self._constraint_column(identity_col, row_scope=row_scope)
                scoped_columns.append(col)
                constraints.append(
                    exp.Is(this=col.copy(), expression=exp.Not(this=exp.Null()))
                )
                if 0 < len(existing_values) <= 8:
                    constraints.append(
                        exp.Not(
                            this=exp.In(
                                this=col.copy(),
                                expressions=[
                                    exp.Literal.number(value)
                                    if isinstance(value, (int, float))
                                    else exp.Literal.string(str(value))
                                    for value in existing_values
                                ],
                            )
                        )
                    )
            for left_index, left in enumerate(scoped_columns):
                for right in scoped_columns[left_index + 1:]:
                    constraints.append(exp.NEQ(this=left.copy(), expression=right.copy()))
        return constraints

    def _existing_values_for_obligation(
        self,
        obligation: OperatorObligation,
        column: ColumnId,
    ) -> Set[Any]:
        relation = obligation.storage_relation or self._storage_relation_for_column_id(column)
        if relation is None or relation.name is None:
            return set()
        try:
            self.instance._table_key_for_storage(relation)
        except Exception:
            return set()
        storage_column = column.source_column_id or column
        column_name = storage_column.name.normalized.lower()
        values: Set[Any] = set()
        for row in self.instance.get_rows(relation):
            for row_column, symbol in row.items():
                if not isinstance(row_column, ColumnId):
                    continue
                if row_column.name.normalized.lower() != column_name:
                    continue
                value = symbol.concrete
                if value is not None:
                    values.add(value)
        return values

    def _storage_relations_for_constraint(
        self,
        constraints: List[exp.Expression],
        join_equalities: List[Tuple[SolverVar, SolverVar]],
        variables: Dict[SolverVar, DataType],
        obligations: Tuple[OperatorObligation, ...],
    ) -> Dict[SolverVar, RelationId]:
        storage: Dict[SolverVar, RelationId] = {}

        obligation_storage: Dict[RelationId, RelationId] = {
            obligation.relation: obligation.storage_relation
            for obligation in obligations
            if obligation.relation is not None and obligation.storage_relation is not None
        }

        def add(variable: SolverVar) -> None:
            relation = obligation_storage.get(variable.relation_id)
            if relation is None:
                relation = self._storage_relation_for_column_id(variable.column_id)
            if relation is not None:
                storage[variable] = relation

        for expr in constraints:
            for col in expr.find_all(exp.Column):
                variable = solver_var(col)
                if variable is not None:
                    add(variable)
        for left_var, right_var in join_equalities:
            add(left_var)
            add(right_var)
        for variable in variables:
            add(variable)
        return storage

    def _storage_relation_for_column_id(self, column: ColumnId) -> RelationId | None:
        source = column.source_column_id or column
        relation = source.relation or column.relation
        if relation is None or relation.name is None:
            return None
        try:
            table_key = self.instance._table_key_for_storage(relation)
            return self.instance.table_id(table_key)
        except Exception:
            pass
        fallback = column.relation
        if fallback is not None and fallback.name is not None:
            try:
                table_key = self.instance._table_key_for_storage(fallback)
                return self.instance.table_id(table_key)
            except Exception:
                pass
        return None

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
        constraints.extend(self._constraints_for_obligations(path.obligations))
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
        row_scope_by_relation = self._join_row_scopes(path.join_facts)
        self._annotate_solver_vars(
            constraints,
            relations,
            row_scope_by_relation=row_scope_by_relation,
        )
        lowered_joins = self._lower_join_equalities(
            join_equalities,
            relations,
            row_scope_by_relation=row_scope_by_relation,
        )
        variables: Dict[SolverVar, DataType] = {}
        for left_sv, right_sv in lowered_joins:
            for sv in (left_sv, right_sv):
                if sv not in variables:
                    dtype = self._datatype_for_column_id(sv.column_id)
                    if dtype is not None:
                        variables[sv] = dtype
        return SolverConstraint(
            target_relations=relations,
            constraints=constraints,
            join_equalities=lowered_joins,
            variables=variables,
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
                    lookup_col = col_id.source_column_id or col_id
                    lookup_rel = lookup_col.relation or col_id.relation
                    existing = {
                        sym.concrete
                        for sym in self.instance.get_column_data(lookup_rel, lookup_col)
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
        row_scope_by_relation: Dict[RelationId, str] | None = None,
    ) -> List[Tuple[SolverVar, SolverVar]]:
        del tables
        lowered: List[Tuple[SolverVar, SolverVar]] = []
        for item in join_equalities:
            if len(item) == 2 and all(isinstance(part, SolverVar) for part in item):
                left_var, right_var = item
                lowered.append((
                    self._with_join_scope(left_var, row_scope_by_relation),
                    self._with_join_scope(right_var, row_scope_by_relation),
                ))
                continue
            if len(item) != 2 or not all(isinstance(part, ColumnId) for part in item):
                continue
            left_id, right_id = item
            if left_id.relation is None or right_id.relation is None:
                continue
            lowered.append((
                SolverVar(
                    column_id=left_id,
                    relation_id=left_id.relation,
                    row_scope=(
                        row_scope_by_relation.get(left_id.relation)
                        if row_scope_by_relation is not None
                        else None
                    ),
                ),
                SolverVar(
                    column_id=right_id,
                    relation_id=right_id.relation,
                    row_scope=(
                        row_scope_by_relation.get(right_id.relation)
                        if row_scope_by_relation is not None
                        else None
                    ),
                ),
            ))
        return lowered

    def _join_row_scopes(self, join_facts) -> Dict[RelationId, str]:
        scoped_relations: List[RelationId] = []
        seen: Set[RelationId] = set()
        for fact in join_facts:
            for relation in (fact.source_relation, fact.target_relation):
                if relation in seen:
                    continue
                seen.add(relation)
                scoped_relations.append(relation)
        return {
            relation: f"r{index}"
            for index, relation in enumerate(scoped_relations)
        }

    def _with_join_scope(
        self,
        variable: SolverVar,
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> SolverVar:
        if row_scope_by_relation is None:
            return variable
        if variable.row_scope is not None:
            return variable
        row_scope = row_scope_by_relation.get(variable.relation_id)
        if row_scope is None or variable.row_scope == row_scope:
            return variable
        return SolverVar(
            column_id=variable.column_id,
            relation_id=variable.relation_id,
            row_scope=row_scope,
        )

    def _row_scope_for_column(
        self,
        col_id: ColumnId,
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> str | None:
        if row_scope_by_relation is None or col_id.relation is None:
            return None
        return row_scope_by_relation.get(col_id.relation)

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
        row_scope_by_relation: Dict[RelationId, str] | None = None,
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
                existing_var = solver_var(col)
                if existing_var is not None:
                    scoped_var = self._with_join_scope(existing_var, row_scope_by_relation)
                    if scoped_var is not existing_var:
                        set_solver_var(col, scoped_var)
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
                sv = SolverVar(
                    column_id=col_id,
                    relation_id=rel_id,
                    row_scope=self._row_scope_for_column(col_id, row_scope_by_relation),
                )
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

    def _find_subplan_for_subquery(self, subquery: exp.Subquery):
        target_sql = subquery.sql(dialect=self.dialect)
        for step in self.plan.ordered_steps:
            if not isinstance(step, SubPlan) or step.anchor is None:
                continue
            if step.anchor is subquery or step.anchor.sql(dialect=self.dialect) == target_sql:
                return step
        return None

    def _iter_steps_with_subplans(self, step: Step):
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





__all__ = [
    "PlausibleBranch",
    "PlausiblePath",
    "ConstraintGenerator",
]
