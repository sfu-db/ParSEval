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

from parseval.constants import PlausibleBit
from parseval.identity import (
    ColumnId,
    ColumnKind,
    RelationId,
    column_id,
    column_identity,
    identifier_name,
    physical_column,
)
from parseval.plan import Plan, Step
from parseval.plan.planner import Filter, Having, Join, Aggregate, Project, Scan, SubPlan
from parseval.plan.rex import negate_predicate, column_meta
from parseval.dtype import DataType
from parseval.instance import Instance
from parseval.solver import SolverConstraint
from parseval.solver.types import SolverVar, set_solver_var, solver_var

from .types import (
    BranchNode,
    BranchPath,
    BranchType,
    CoverageTarget,
    JoinFact,
    OperatorObligation,
    PathPredicate,
    RowSetObligation,
    SubqueryPath,
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


class UnresolvedScopedColumnError(ValueError):
    """A qualified constraint column has no planner-provided identity."""


@dataclass(frozen=True)
class _AggregateAliasInfo:
    step: Aggregate
    kind: str
    source: exp.Column | None
    distinct: bool = False


@dataclass(frozen=True)
class _AggregateGroupReference:
    total: int | float
    count: int
    minimum: int | float | None
    maximum: int | float | None
    group_values: Tuple[Tuple[ColumnId, Any], ...]


@dataclass(frozen=True)
class _DatabaseVariable:
    variable: SolverVar
    storage_relation: RelationId
    storage_key: Tuple[str, str]
    binding_key: Tuple[str, str | None, str | None, str | None]


@dataclass
class _DatabaseConstraintContext:
    generator: "ConstraintGenerator"
    constraints: List[exp.Expression]
    required_vars: Dict[
        Tuple[Tuple[str, str | None, str | None, str | None], Tuple[str, str]],
        _DatabaseVariable,
    ]
    required_by_storage: Dict[Tuple[str, str], List[_DatabaseVariable]]
    active_storage_relations: Set[RelationId]
    not_null_vars: List[SolverVar]
    avoid_values: Dict[SolverVar, Set[Any]]

    @classmethod
    def build(
        cls,
        generator: "ConstraintGenerator",
        constraints: List[exp.Expression],
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> "_DatabaseConstraintContext":
        context = cls(
            generator=generator,
            constraints=constraints,
            required_vars={},
            required_by_storage={},
            active_storage_relations=set(),
            not_null_vars=[],
            avoid_values={},
        )
        for expr in list(constraints):
            context.collect_expression(expr, row_scope_by_relation)
        context.index_bindings()
        return context

    def collect_expression(
        self,
        expr: exp.Expression,
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> None:
        for col in expr.find_all(exp.Column):
            variable = solver_var(col)
            if variable is None:
                continue
            variable = self.generator._with_join_scope(variable, row_scope_by_relation)
            db_var = self.generator._database_variable_for_solver_var(variable)
            if db_var is None:
                continue
            required_key = (db_var.binding_key, db_var.storage_key)
            if required_key in self.required_vars:
                continue
            self.required_vars[required_key] = db_var
            self.collect_column_requirements(db_var)

    def collect_column_requirements(self, db_var: _DatabaseVariable) -> None:
        col_id = db_var.variable.column_id
        storage_relation = db_var.storage_relation
        storage_col = col_id.source_column_id or col_id
        if not self.generator.instance.nullable(storage_relation, storage_col):
            self.not_null_vars.append(db_var.variable)
        if self.generator.instance.is_unique(storage_relation, storage_col):
            existing = {
                sym.concrete
                for sym in self.generator.instance.get_column_data(
                    storage_relation,
                    storage_col,
                )
                if sym.concrete is not None
            }
            if existing:
                self.avoid_values[db_var.variable] = existing

    def index_bindings(self) -> None:
        for db_var in self.required_vars.values():
            self.required_by_storage.setdefault(db_var.storage_key, []).append(db_var)
            self.active_storage_relations.add(db_var.storage_relation)

    def lower_all(self) -> None:
        self.lower_check_constraints()
        self.lower_not_null()
        self.lower_existing_unique_values()
        self.lower_generated_key_uniqueness()
        self.lower_foreign_keys()

    def lower_check_constraints(self) -> None:
        for relation in self._active_catalog_relations():
            db_constraints = self.generator.instance.database_constraints(relation)
            binding_keys = sorted(
                {
                    db_var.binding_key
                    for db_var in self.required_vars.values()
                    if db_var.storage_relation == relation
                },
                key=lambda value: tuple("" if part is None else part for part in value),
            )
            by_binding_and_storage = {
                (db_var.binding_key, db_var.storage_key): db_var
                for db_var in self.required_vars.values()
            }

            for check in db_constraints.checks:
                if not check.supported:
                    raise ValueError(
                        f"unsupported_database_check:{check.reason or 'unknown'}"
                    )
                column_by_name = {
                    column.name.normalized: column
                    for column in check.referenced_columns
                }
                for binding_key in binding_keys:
                    db_vars = []
                    for column in check.referenced_columns:
                        storage_key = self.generator._storage_column_key(column)
                        if storage_key is None:
                            db_vars = []
                            break
                        db_var = by_binding_and_storage.get((binding_key, storage_key))
                        if db_var is None:
                            db_vars = []
                            break
                        db_vars.append(db_var)
                    if not db_vars:
                        continue
                    variable_by_column = {
                        db_var.storage_key[1]: db_var.variable
                        for db_var in db_vars
                    }
                    rewritten = check.expression.copy()
                    matched = False
                    for col in rewritten.find_all(exp.Column):
                        column = column_by_name.get(
                            identifier_name(
                                col.name,
                                dialect=self.generator.dialect,
                            ).normalized
                        )
                        if column is None:
                            continue
                        if col.type is None:
                            self.generator._set_type_from_catalog(col, column)
                        storage_key = self.generator._storage_column_key(column)
                        assert storage_key is not None
                        set_solver_var(col, variable_by_column[storage_key[1]])
                        matched = True
                    if matched:
                        self.constraints.append(rewritten)

    def lower_not_null(self) -> None:
        for variable in self.not_null_vars:
            self.constraints.append(
                exp.Is(
                    this=self.generator._constraint_column(
                        variable.column_id,
                        row_scope=variable.row_scope,
                    ),
                    expression=exp.Not(this=exp.Null()),
                )
            )

    def lower_existing_unique_values(self) -> None:
        for variable, vals in self.avoid_values.items():
            self.constraints.append(
                exp.Not(
                    this=exp.In(
                        this=self.generator._constraint_column(
                            variable.column_id,
                            row_scope=variable.row_scope,
                        ),
                        expressions=[
                            exp.Literal.number(v)
                            if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in vals
                        ],
                    )
                )
            )

    def lower_generated_key_uniqueness(self) -> None:
        self.constraints.extend(
            self.generator._generated_key_uniqueness_constraints(
                self.active_storage_relations,
                tuple(self.required_vars.values()),
            )
        )

    def lower_foreign_keys(self) -> None:
        for local_var, ref_rel, ref_col in self._foreign_key_requirements():
            ref_key = self.generator._storage_column_key(ref_col)
            generated_parent_vars = [
                db_var.variable
                for db_var in self.required_by_storage.get(ref_key, [])
                if db_var.variable != local_var
            ] if ref_key is not None else []
            parent_vals = []
            if _relation_table_name(ref_rel) in self.generator.instance.tables:
                for row in self.generator.instance.get_rows(ref_rel):
                    val = self.generator._row_value(row, ref_col)
                    if val is not None:
                        parent_vals.append(val)
            local_expr = self.generator._constraint_column(
                local_var.column_id,
                row_scope=local_var.row_scope,
            )
            choices: List[exp.Expression] = [
                exp.EQ(
                    this=local_expr.copy(),
                    expression=self.generator._constraint_column(
                        parent_var.column_id,
                        row_scope=parent_var.row_scope,
                    ),
                )
                for parent_var in generated_parent_vars
            ]
            if parent_vals:
                choices.append(
                    exp.In(
                        this=local_expr.copy(),
                        expressions=[
                            exp.Literal.number(v)
                            if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in parent_vals
                        ],
                    )
                )
            if not choices:
                continue
            constraint = choices[0]
            for choice in choices[1:]:
                constraint = exp.Or(this=constraint, expression=choice)
            self.constraints.append(constraint)

    def _foreign_key_requirements(
        self,
    ) -> List[Tuple[SolverVar, RelationId, ColumnId]]:
        foreign_keys: List[Tuple[SolverVar, RelationId, ColumnId]] = []
        for relation in self._active_catalog_relations():
            table_name = _relation_table_name(relation)
            for fk in self.generator.instance.get_foreign_key(table_name):
                local_col = physical_column(
                    fk.expressions[0].name,
                    relation,
                    dialect=self.generator.dialect,
                )
                local_key = self.generator._storage_column_key(local_col)
                local_vars = (
                    self.required_by_storage.get(local_key, [])
                    if local_key is not None
                    else []
                )
                if not local_vars:
                    continue
                ref = fk.args.get("reference")
                ref_table_node = ref.find(exp.Table) if ref is not None else None
                ref_col = self.generator.instance.resolve_fk_ref_column(fk)
                ref_rel = (
                    self.generator._storage_relation_for_table_reference(ref_table_node)
                    if ref_table_node is not None
                    else None
                )
                if ref_rel is None or ref_col is None:
                    continue
                ref_column = physical_column(
                    ref_col,
                    ref_rel,
                    dialect=self.generator.dialect,
                )
                for local_db_var in local_vars:
                    foreign_keys.append(
                        (
                            local_db_var.variable,
                            ref_rel,
                            ref_column,
                        )
                    )
        return foreign_keys

    def _active_catalog_relations(self) -> Tuple[RelationId, ...]:
        return tuple(
            relation
            for relation in self.active_storage_relations
            if _relation_table_name(relation) in self.generator.instance.tables
        )


def _row_env_bindings(row) -> Dict[ColumnId, Any]:
    bindings: Dict[ColumnId, Any] = {}
    for column, symbol in row.items():
        if isinstance(column, ColumnId):
            bindings[column] = symbol.concrete
    return bindings


def _relation_table_name(rel: RelationId) -> str:
    """Extract the physical table name from a RelationId."""
    return rel.name.normalized if rel.name is not None else rel.display


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


class ConstraintGenerator:
    """Compile coverage targets and branch paths into SolverConstraint objects.

    Collects query predicates + database constraints + JOIN conditions into
    one constraint set the solver satisfies simultaneously.
    """

    def __init__(self, plan: Plan, instance: Instance, dialect: str):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self._step_by_id: Dict[str, Step] | None = None

    def _get_step_index(self) -> Dict[str, Step]:
        if self._step_by_id is None:
            self._step_by_id = {}
            for step in self.plan.ordered_steps:
                annotation = self.plan.annotation_for(step)
                self._step_by_id[annotation.step_id] = step
        return self._step_by_id

    def _step_for_node(self, node: BranchNode) -> Step | None:
        """Look up the plan Step for a BranchNode via its step_id."""
        return self._get_step_index().get(node.step_id)

    def _step_annotation(self, node: BranchNode):
        """Get StepAnnotations for a BranchNode's step."""
        step = self._step_for_node(node)
        if step is not None:
            return self.plan.annotation_for(step)
        return None

    def compile_target(
        self,
        target: CoverageTarget,
    ) -> SolverConstraint:
        if target.node.site == "exists":
            return self._generate_exists_constraint(target)
        if target.node.site == "in":
            return self._generate_in_constraint(target)
        if target.node.site == "distinct":
            return self._generate_distinct_constraint(target)

        from .branch_tree import BranchPathBuilder

        return self.compile_path(BranchPathBuilder().path_for_target(target))

    def compile_path(self, path: BranchPath) -> SolverConstraint:
        constraints: List[exp.Expression] = []
        join_equalities: List[Tuple[ColumnId, ColumnId]] = []
        tables = path.relations or path.target.node.tables

        row_scope_by_relation = self._row_scopes_for_path(path, tables)

        row_sets = tuple(
            obligation.row_set
            for obligation in path.obligations
            if obligation.kind == "row_set" and obligation.row_set is not None
        )
        for predicate in path.predicates:
            if (
                row_sets
                and predicate.node is not path.target.node
                and predicate.expression.find(exp.In)
            ):
                continue
            if self._row_sets_cover_having_predicate(predicate, row_sets):
                continue
            constraints.extend(self._constraints_for_path_predicate(predicate))
        constraints.extend(self._constraints_for_row_set_obligations(path.obligations))
        obligations = path.obligations
        if any(obligation.kind == "row_set" for obligation in obligations):
            obligations = tuple(
                obligation
                for obligation in obligations
                if obligation.kind != "scan_exists"
            )
        constraints.extend(
            self._constraints_for_obligations(
                obligations,
                row_scope_by_relation,
            )
        )
        for fact in path.join_facts:
            join_equalities.extend(fact.equalities)
            if fact.predicate is not None and not _is_trivial_true(fact.predicate):
                constraints.append(fact.predicate.copy())

        self._annotate_solver_vars(
            constraints,
            tables,
            row_scope_by_relation=row_scope_by_relation,
        )
        constraints, extra_join_equalities = self._apply_database_constraints(
            constraints=constraints,
            tables=tables,
            row_scope_by_relation=row_scope_by_relation,
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

    def _constraints_for_path_predicate(self, predicate: PathPredicate) -> List[exp.Expression]:
        if predicate.obligation is not None:
            if predicate.obligation.metric in {"join_left_unmatched", "join_right_unmatched"}:
                return self._join_unmatched_constraints(predicate)
            if predicate.node.site == "group":
                return self._group_constraints(predicate)
        if predicate.node.site == "project_output":
            return self._project_output_constraints(predicate)
        if predicate.node.site == "aggregate_output":
            return self._aggregate_output_constraints(predicate)
        if predicate.node.site == "aggregate_input":
            return self._aggregate_input_constraints(predicate)
        if predicate.node.site == "aggregate_distinct_input":
            return self._aggregate_distinct_input_constraints(predicate)

        expression = predicate.expression.copy()
        if predicate.outcome in _POSITIVE_BITS:
            if predicate.node.site == "having":
                constraints = self._having_source_value_constraints(predicate)
                if constraints:
                    return constraints
                return self._positive_predicate_constraints(
                    expression,
                    predicate.node.subqueries,
                )
            if predicate.node.site == "filter":
                filter_expression = (
                    predicate.node.predicate
                    if predicate.expression is predicate.node.predicate
                    else expression
                )
                derived_constraints = self._aggregate_alias_comparison_constraints(
                    filter_expression,
                    predicate.node,
                )
                if derived_constraints:
                    return derived_constraints
                return self._positive_predicate_constraints(
                    filter_expression,
                    predicate.node.subqueries,
                )
            return (
                []
                if _is_trivial_true(expression)
                else self._positive_predicate_constraints(
                    expression,
                    predicate.node.subqueries,
                )
            )
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

    def _project_output_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        expression = predicate.expression
        if isinstance(expression, exp.Alias):
            expression = expression.this
        scoped = self._scoped_expression(expression, predicate.node.tables, "r0")
        if predicate.outcome == PlausibleBit.PROJECT_NULL:
            return [exp.Is(this=scoped, expression=exp.Null())]
        return [exp.Is(this=scoped, expression=exp.Not(this=exp.Null()))]

    def _aggregate_output_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        function = next(predicate.expression.find_all(exp.AggFunc), None)
        if function is None:
            return []
        if isinstance(function, exp.Count):
            return []

        argument = function.this
        if isinstance(argument, exp.Distinct):
            if not argument.expressions:
                return []
            argument = argument.expressions[0]
        if argument is None or isinstance(argument, exp.Star):
            return []

        scoped = self._scoped_expression(argument, predicate.node.tables, "r0")
        group_cols = self._group_columns_for_node(predicate.node)
        group_constraints = self._group_key_not_null_constraints(group_cols, ("r0",))
        if predicate.outcome == PlausibleBit.AGGREGATE_NULL:
            existing_keys = self._existing_group_keys(group_cols)
            return [
                exp.Is(this=scoped, expression=exp.Null()),
                *group_constraints,
                *self._avoid_existing_group_keys(group_cols, existing_keys, "r0"),
            ]
        return [exp.Is(this=scoped, expression=exp.Not(this=exp.Null())), *group_constraints]

    def _aggregate_distinct_input_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        left = self._scoped_expression(
            predicate.expression,
            predicate.node.tables,
            "r0",
        )
        group_constraints = self._same_group_constraints(predicate.node, ("r0", "r1"))
        if predicate.outcome == PlausibleBit.AGG_DISTINCT_NULL_IGNORED:
            return [
                exp.Is(this=left, expression=exp.Null()),
                *self._group_key_not_null_constraints(
                    self._group_columns_for_node(predicate.node),
                    ("r0",),
                ),
            ]

        right = self._scoped_expression(
            predicate.expression,
            predicate.node.tables,
            "r1",
        )
        comparison_type = (
            exp.EQ
            if predicate.outcome == PlausibleBit.AGG_DISTINCT_DUPLICATE_ELIMINATED
            else exp.NEQ
        )
        constraints: List[exp.Expression] = [
            comparison_type(this=left.copy(), expression=right.copy()),
            exp.Is(this=left, expression=exp.Not(this=exp.Null())),
            exp.Is(this=right, expression=exp.Not(this=exp.Null())),
        ]

        return constraints + group_constraints

    def _aggregate_input_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        left = self._scoped_expression(
            predicate.expression,
            predicate.node.tables,
            "r0",
        )
        if predicate.outcome == PlausibleBit.AGGREGATE_NULL:
            return [exp.Is(this=left, expression=exp.Null())]
        if predicate.outcome == PlausibleBit.DUPLICATE:
            right = self._scoped_expression(
                predicate.expression,
                predicate.node.tables,
                "r1",
            )
            return [
                exp.EQ(this=left.copy(), expression=right.copy()),
                exp.Is(this=left, expression=exp.Not(this=exp.Null())),
                exp.Is(this=right, expression=exp.Not(this=exp.Null())),
            ]
        return []

    def _join_unmatched_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        if not predicate.node.join_facts:
            # No join facts available (e.g., malformed join condition or
            # natural join) — allow any row combination (cross join).
            return []
        constraints: List[exp.Expression] = []
        for fact in predicate.node.join_facts:
            for left_id, right_id in fact.equalities:
                left = self._constraint_column(left_id, row_scope="r0")
                right = self._constraint_column(right_id, row_scope="r1")
                constraints.extend(
                    [
                        exp.NEQ(this=left.copy(), expression=right.copy()),
                        exp.Is(this=left, expression=exp.Not(this=exp.Null())),
                        exp.Is(this=right, expression=exp.Not(this=exp.Null())),
                    ]
                )
        return constraints

    def _group_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        group_cols = self._group_columns_for_node(predicate.node)
        if not group_cols:
            # Implicit aggregation (no GROUP BY) — entire table is one group.
            # GROUP_MULTI is impossible; GROUP_SINGLE is trivially satisfied.
            metric = predicate.obligation.metric if predicate.obligation is not None else "group_size"
            if predicate.outcome == PlausibleBit.GROUP_MULTI:
                return [exp.false()]
            return []

        metric = predicate.obligation.metric if predicate.obligation is not None else "group_size"
        existing_keys = self._existing_group_keys(group_cols)
        constraints: List[exp.Expression] = []

        if metric == "group_size":
            if predicate.outcome == PlausibleBit.GROUP_MULTI:
                if existing_keys:
                    constraints.extend(
                        self._group_key_equalities(group_cols, existing_keys[0], "r0")
                    )
                else:
                    constraints.extend(self._group_key_pair_constraints(group_cols, exp.EQ))
                    constraints.extend(self._group_key_not_null_constraints(group_cols, ("r0", "r1")))
            else:
                constraints.extend(self._group_key_not_null_constraints(group_cols, ("r0",)))
                constraints.extend(self._avoid_existing_group_keys(group_cols, existing_keys, "r0"))
            return constraints

        if metric == "group_count":
            if predicate.outcome == PlausibleBit.GROUP_MULTI:
                if existing_keys:
                    constraints.extend(self._avoid_existing_group_keys(group_cols, existing_keys, "r0"))
                    constraints.extend(self._group_key_not_null_constraints(group_cols, ("r0",)))
                else:
                    constraints.extend(self._group_key_pair_constraints(group_cols, exp.NEQ))
                    constraints.extend(self._group_key_not_null_constraints(group_cols, ("r0", "r1")))
            else:
                if len(existing_keys) > 1:
                    return [exp.false()]
                constraints.extend(self._group_key_not_null_constraints(group_cols, ("r0",)))
                constraints.extend(self._avoid_existing_group_keys(group_cols, existing_keys, "r0"))
            return constraints

        raise ValueError(f"unsupported_group_metric:{metric}")

    def _group_columns_for_node(self, node: BranchNode) -> Tuple[ColumnId, ...]:
        meta = node.annotation_metadata.get("aggregation", {})
        group_sources = meta.get("group_sources", {})
        columns: List[ColumnId] = []
        seen: Set[ColumnId] = set()
        for source_ids in group_sources.values():
            for col_id in source_ids:
                phys = col_id.source_column_id or col_id
                if phys.relation is None or phys in seen:
                    continue
                seen.add(phys)
                columns.append(phys)
        return tuple(columns)

    def _existing_group_keys(
        self,
        group_cols: Tuple[ColumnId, ...],
    ) -> List[Tuple[Any, ...]]:
        if not group_cols:
            return []
        relation = self._storage_relation_for_column_id(group_cols[0])
        if relation is None:
            return []
        keys: List[Tuple[Any, ...]] = []
        seen: Set[Tuple[Any, ...]] = set()
        for row in self.instance.get_rows(relation):
            key = tuple(self._row_value(row, col) for col in group_cols)
            if any(value is None for value in key) or key in seen:
                continue
            seen.add(key)
            keys.append(key)
        return keys

    def _group_key_equalities(
        self,
        group_cols: Tuple[ColumnId, ...],
        key: Tuple[Any, ...],
        row_scope: str,
    ) -> List[exp.Expression]:
        return [
            exp.EQ(
                this=self._constraint_column(col, row_scope=row_scope),
                expression=self._literal_for_value(value),
            )
            for col, value in zip(group_cols, key)
        ]

    def _group_key_pair_constraints(
        self,
        group_cols: Tuple[ColumnId, ...],
        comparison_type,
    ) -> List[exp.Expression]:
        return [
            comparison_type(
                this=self._constraint_column(col, row_scope="r0"),
                expression=self._constraint_column(col, row_scope="r1"),
            )
            for col in group_cols
        ]

    def _same_group_constraints(
        self,
        node: BranchNode,
        row_scopes: Tuple[str, str],
    ) -> List[exp.Expression]:
        group_cols = self._group_columns_for_node(node)
        if not group_cols:
            return []
        left_scope, right_scope = row_scopes
        return [
            exp.EQ(
                this=self._constraint_column(col, row_scope=left_scope),
                expression=self._constraint_column(col, row_scope=right_scope),
            )
            for col in group_cols
        ]

    def _group_key_not_null_constraints(
        self,
        group_cols: Tuple[ColumnId, ...],
        row_scopes: Tuple[str, ...],
    ) -> List[exp.Expression]:
        return [
            exp.Is(
                this=self._constraint_column(col, row_scope=row_scope),
                expression=exp.Not(this=exp.Null()),
            )
            for row_scope in row_scopes
            for col in group_cols
        ]

    def _avoid_existing_group_keys(
        self,
        group_cols: Tuple[ColumnId, ...],
        existing_keys: List[Tuple[Any, ...]],
        row_scope: str,
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for key in existing_keys:
            disjuncts = [
                exp.NEQ(
                    this=self._constraint_column(col, row_scope=row_scope),
                    expression=self._literal_for_value(value),
                )
                for col, value in zip(group_cols, key)
            ]
            if not disjuncts:
                continue
            condition = disjuncts[0]
            for disjunct in disjuncts[1:]:
                condition = exp.Or(this=condition, expression=disjunct)
            constraints.append(condition)
        return constraints

    def _literal_for_value(self, value: Any) -> exp.Expression:
        if isinstance(value, (int, float)):
            return exp.Literal.number(value)
        return exp.Literal.string(str(value))

    def _having_source_value_constraints(
        self,
        predicate: PathPredicate,
    ) -> List[exp.Expression]:
        constraints = self._aggregate_alias_comparison_constraints(
            predicate.expression,
            predicate.node,
        )
        if constraints:
            return constraints
        return self._aggregate_function_comparison_constraints(
            predicate.expression,
            predicate.node,
        )

    def _aggregate_function_comparison_constraints(
        self,
        expression: exp.Expression,
        node: BranchNode,
    ) -> List[exp.Expression]:
        if not isinstance(expression, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            return []

        left = expression.this
        right = expression.expression
        if isinstance(left, exp.AggFunc) and isinstance(right, exp.Literal):
            function = left
            literal = right
            comparison_type = type(expression)
        elif isinstance(right, exp.AggFunc) and isinstance(left, exp.Literal):
            function = right
            literal = left
            comparison_type = {
                exp.GT: exp.LT,
                exp.GTE: exp.LTE,
                exp.LT: exp.GT,
                exp.LTE: exp.GTE,
                exp.EQ: exp.EQ,
            }[type(expression)]
        else:
            return []

        if not isinstance(function, exp.Count):
            return []
        argument = function.this
        distinct = False
        if isinstance(argument, exp.Distinct):
            distinct = True
            if not argument.expressions:
                return []
            argument = argument.expressions[0]
        if argument is None or isinstance(argument, exp.Star):
            return []
        if not isinstance(argument, exp.Column):
            return []

        source_col = self._column_id_for_expr(argument, node.tables)
        if source_col is None or source_col.relation is None:
            return []
        step = self._aggregate_step_for_node(node)
        if step is None:
            return []
        literal_value = self._literal_number(literal)
        if literal_value is None:
            return []

        info = _AggregateAliasInfo(
            step=step,
            kind="count",
            source=_column_expr_from_id(source_col),
            distinct=distinct,
        )
        reference = self._aggregate_group_reference(
            info,
            source_col.relation,
            node,
        )
        return self._count_source_value_constraints(
            source_col,
            info,
            comparison_type,
            literal_value,
            reference,
        )

    def _aggregate_alias_comparison_constraints(
        self,
        expression: exp.Expression,
        node: BranchNode,
    ) -> List[exp.Expression]:
        if not isinstance(expression, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            return []

        left = expression.this
        right = expression.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            alias_col = left
            literal = right
            comparison_type = type(expression)
        elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            alias_col = right
            literal = left
            comparison_type = {
                exp.GT: exp.LT,
                exp.GTE: exp.LTE,
                exp.LT: exp.GT,
                exp.LTE: exp.GTE,
                exp.EQ: exp.EQ,
            }[type(expression)]
        else:
            return []

        resolved = self._aggregate_alias_info_for_column(alias_col, node)
        if resolved is None:
            return []
        info, relation = resolved
        if info.source is None:
            return []
        if relation is None:
            return []
        literal_value = self._literal_number(literal)
        if literal_value is None:
            return []
        reference = self._aggregate_group_reference(
            info,
            relation,
            node,
        )
        source_col = self._column_id_for_source(info.source, relation)
        group_constraints: List[exp.Expression] = []
        if reference is not None:
            for group_col, group_value in reference.group_values:
                group_constraints.append(
                    exp.EQ(
                        this=self._constraint_column(group_col),
                        expression=(
                            exp.Literal.number(group_value)
                            if isinstance(group_value, (int, float))
                            else exp.Literal.string(str(group_value))
                        ),
                    )
                )
        if info.kind == "count":
            return group_constraints + self._count_source_value_constraints(
                source_col,
                info,
                comparison_type,
                literal_value,
                reference,
            )
        source_literal = self._aggregate_source_literal(
            info.kind,
            comparison_type,
            literal_value,
            reference,
        )
        if source_literal is None:
            return []
        col = self._constraint_column(source_col)
        return group_constraints + [
            comparison_type(
                this=col,
                expression=exp.Literal.number(source_literal),
            )
        ]

    def _aggregate_alias_info_for_column(
        self,
        alias_col: exp.Column,
        node: BranchNode,
    ) -> Tuple[_AggregateAliasInfo, RelationId] | None:
        if alias_col.table:
            relation = self._relation_for_visible_name(alias_col.table, node.tables)
            if relation is None:
                return None
            resolved = self._aggregate_alias_info_for_relation(alias_col.name, relation)
            if resolved is not None:
                return resolved
            if node.site == "having":
                info = self._aggregate_alias_info(alias_col.name)
                if info is None:
                    return None
                relation = self._relation_for_aggregate_info(info, node)
                if relation is None:
                    return None
                return info, relation
            return None

        matches: List[Tuple[_AggregateAliasInfo, RelationId]] = []
        for relation in node.tables:
            resolved = self._aggregate_alias_info_for_relation(alias_col.name, relation)
            if resolved is not None:
                matches.append(resolved)
        if len(matches) == 1:
            return matches[0]

        info = self._aggregate_alias_info(alias_col.name)
        if info is None:
            return None
        relation = self._relation_for_aggregate_info(info, node)
        if relation is None:
            return None
        return info, relation

    def _aggregate_alias_info_for_relation(
        self,
        alias: str,
        relation: RelationId,
    ) -> Tuple[_AggregateAliasInfo, RelationId] | None:
        for step in self.plan.ordered_steps:
            if not isinstance(step, Scan):
                continue
            annotation = self.plan.annotation_for(step)
            if relation not in tuple(annotation.source_relations):
                continue
            for subplan in step.subplan_dependencies:
                source = self._subplan_output_source(alias, subplan)
                if source is None:
                    continue
                for inner in self._iter_steps_with_subplans(subplan.inner):
                    if not isinstance(inner, Aggregate):
                        continue
                    info = self._aggregate_alias_info_from_step(
                        inner,
                        alias,
                        expected_source=source,
                    )
                    if info is None:
                        continue
                    source_relation = source.relation
                    if source_relation is None:
                        source_relations = tuple(
                            self.plan.annotation_for(inner).source_relations
                        )
                        if len(source_relations) == 1:
                            source_relation = source_relations[0]
                    if source_relation is not None:
                        return info, source_relation
        return None

    def _subplan_output_source(
        self,
        alias: str,
        subplan: SubPlan,
    ) -> ColumnId | None:
        normalized = identifier_name(alias, dialect=self.dialect).normalized
        metadata = self.plan.annotation_for(subplan).metadata.get("subquery", {})
        for output_col in metadata.get("output_columns", ()):
            if not isinstance(output_col, ColumnId):
                continue
            if output_col.name.normalized != normalized:
                continue
            return output_col.source_column_id or output_col
        return None

    def _aggregate_alias_info_from_step(
        self,
        step: Aggregate,
        alias: str,
        expected_source: ColumnId | None = None,
    ) -> _AggregateAliasInfo | None:
        annotation = self.plan.annotation_for(step)
        agg_meta = annotation.metadata.get("aggregation", {})
        for _col_id, info in agg_meta.get("aggregate_outputs", {}).items():
            if info["alias"] != alias:
                continue
            argument = info["argument"]  # ColumnId
            if expected_source is not None:
                if argument is None:
                    continue
                source = argument.source_column_id or argument
                expected = expected_source.source_column_id or expected_source
                if not self._same_column_identity(source, expected):
                    continue
            kind = info["function"]
            distinct = bool(info.get("distinct"))
            if argument is not None:
                source_col = _column_expr_from_id(argument)
                return _AggregateAliasInfo(step, kind, source_col, distinct)
            # COUNT(*) has no argument column.
            return _AggregateAliasInfo(step, kind, None, distinct)
        return None

    def _relation_for_aggregate_info(
        self,
        info: _AggregateAliasInfo,
        node: BranchNode,
    ) -> RelationId | None:
        if info.source is None:
            return None
        source_identity = column_identity(info.source)
        relation = (
            source_identity.relation
            if source_identity is not None and source_identity.relation is not None
            else self._relation_for_visible_name(info.source.table, node.tables)
        )
        if relation is None:
            source_relations = tuple(self.plan.annotation_for(info.step).source_relations)
            if len(source_relations) == 1:
                relation = source_relations[0]
        return relation

    def _aggregate_alias_info(
        self, alias: str
    ) -> _AggregateAliasInfo | None:
        seen: Set[int] = set()
        for root in self.plan.ordered_steps:
            for step in self._iter_steps_with_subplans(root):
                if id(step) in seen:
                    continue
                seen.add(id(step))
                if not isinstance(step, Aggregate):
                    continue
                info = self._aggregate_alias_info_from_step(step, alias)
                if info is not None:
                    return info
        return None

    def _aggregate_step_for_node(self, node: BranchNode) -> Aggregate | None:
        step = self._step_for_node(node)
        if step is not None:
            for candidate in self._iter_steps_with_subplans(step):
                if isinstance(candidate, Aggregate):
                    return candidate
        for root in self.plan.ordered_steps:
            for candidate in self._iter_steps_with_subplans(root):
                if not isinstance(candidate, Aggregate):
                    continue
                annotation = self.plan.annotation_for(candidate)
                if set(annotation.source_relations).issubset(set(node.tables)):
                    return candidate
        return None

    def _literal_number(self, literal: exp.Literal) -> int | float | None:
        try:
            value = literal.to_py()
        except Exception:
            value = literal.this
        if isinstance(value, (int, float)):
            return value
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return int(number) if number.is_integer() else number

    def _count_source_value_constraints(
        self,
        source_col: ColumnId,
        info: _AggregateAliasInfo,
        comparison_type,
        threshold: int | float,
        reference: _AggregateGroupReference | None,
    ) -> List[exp.Expression]:
        required = self._required_count(comparison_type, threshold)
        if required is None:
            return []
        existing_count = reference.count if reference is not None else 0
        needed = max(required - existing_count, 1)

        constraints: List[exp.Expression] = []
        counted_cols: List[exp.Column] = []
        for index in range(needed):
            row_scope = f"r{index}"
            counted = self._constraint_column(source_col, row_scope=row_scope)
            counted_cols.append(counted)
            constraints.append(
                exp.Is(this=counted.copy(), expression=exp.Not(this=exp.Null()))
            )

        if info.distinct:
            for left_index, left in enumerate(counted_cols):
                for right in counted_cols[left_index + 1:]:
                    constraints.append(exp.NEQ(this=left.copy(), expression=right.copy()))

        group_cols = self._aggregate_group_columns(info)
        for group_col in group_cols:
            if reference is not None:
                continue
            first = self._constraint_column(
                group_col,
                row_scope="r0",
            )
            constraints.append(
                exp.Is(this=first.copy(), expression=exp.Not(this=exp.Null()))
            )
            for index in range(1, needed):
                constraints.append(
                    exp.EQ(
                        this=self._constraint_column(group_col, row_scope=f"r{index}"),
                        expression=first.copy(),
                    )
                )
        return constraints

    def _required_count(
        self,
        comparison_type,
        threshold: int | float,
    ) -> int | None:
        if comparison_type is exp.GT:
            return int(threshold) + 1
        if comparison_type is exp.GTE:
            return int(threshold) if float(threshold).is_integer() else int(threshold) + 1
        if comparison_type is exp.EQ:
            return int(threshold) if float(threshold).is_integer() and threshold >= 1 else None
        if comparison_type is exp.LT:
            return 1 if threshold > 1 else None
        if comparison_type is exp.LTE:
            return 1 if threshold >= 1 else None
        return None

    def _aggregate_group_columns(self, info: _AggregateAliasInfo) -> Tuple[ColumnId, ...]:
        annotation = self.plan.annotation_for(info.step)
        agg_meta = annotation.metadata.get("aggregation", {})
        return agg_meta.get("group_keys", ())

    def _aggregate_source_literal(
        self,
        kind: str,
        comparison_type,
        threshold: int | float,
        reference: _AggregateGroupReference | None,
    ) -> int | float | None:
        if kind == "sum":
            return threshold - (reference.total if reference is not None else 0)
        if kind == "avg":
            if reference is None:
                return threshold
            return threshold * (reference.count + 1) - reference.total
        if kind == "min":
            if comparison_type in {exp.GT, exp.GTE}:
                if reference is not None and not self._compare_number(
                    reference.minimum,
                    comparison_type,
                    threshold,
                ):
                    return None
                return threshold
            if comparison_type in {exp.LT, exp.LTE}:
                return threshold
            if reference is not None and not self._compare_number(
                reference.minimum,
                exp.GTE,
                threshold,
            ):
                return None
            return threshold
        if kind == "max":
            if comparison_type in {exp.LT, exp.LTE}:
                if reference is not None and not self._compare_number(
                    reference.maximum,
                    comparison_type,
                    threshold,
                ):
                    return None
                return threshold
            if comparison_type in {exp.GT, exp.GTE}:
                return threshold
            if reference is not None and not self._compare_number(
                reference.maximum,
                exp.LTE,
                threshold,
            ):
                return None
            return threshold
        if kind == "count":
            current = reference.count if reference is not None else 0
            return 1 if self._compare_number(current + 1, comparison_type, threshold) else None
        return None

    def _compare_number(
        self,
        left: int | float | None,
        comparison_type,
        right: int | float,
    ) -> bool:
        if left is None:
            return False
        if comparison_type is exp.GT:
            return left > right
        if comparison_type is exp.GTE:
            return left >= right
        if comparison_type is exp.LT:
            return left < right
        if comparison_type is exp.LTE:
            return left <= right
        if comparison_type is exp.EQ:
            return left == right
        return False

    def _aggregate_group_reference(
        self,
        info: _AggregateAliasInfo,
        source_relation: RelationId,
        node: BranchNode,
    ) -> _AggregateGroupReference | None:
        if info.source is None:
            return None
        source_col = self._column_id_for_source(info.source, source_relation)
        source_storage = self._storage_relation_for_column_id(source_col)
        if source_storage is None:
            return None

        group_cols: List[ColumnId] = []
        for group_expr in info.step.group.values():
            for col in group_expr.find_all(exp.Column):
                col_id = column_identity(col)
                if col_id is None or col_id.relation is None:
                    relation = self._relation_for_visible_name(
                        col.table, node.tables
                    )
                    if relation is None:
                        continue
                    col_id = self._column_id_for_source(col, relation)
                group_cols.append(col_id)
        if not group_cols:
            return None

        join_facts = self._join_facts_for_node_path(node)
        groups: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
        group_columns: Dict[Tuple[Any, ...], Tuple[Tuple[ColumnId, Any], ...]] = {}
        for row in self.instance.get_rows(source_storage):
            value = self._row_value(row, source_col)
            if info.kind == "count":
                if value is None:
                    continue
            elif not isinstance(value, (int, float)):
                continue
            group_values = []
            for group_col in group_cols:
                group_value = self._group_value_for_source_row(
                    row,
                    source_col,
                    group_col,
                    join_facts,
                )
                if group_value is None:
                    break
                group_values.append((group_col, group_value))
            else:
                key = tuple(value for _col, value in group_values)
                if not self._group_values_materializable(
                    tuple(group_values),
                    join_facts,
                ):
                    continue
                group_columns[key] = tuple(group_values)
                stats = groups.setdefault(
                    key,
                    {"total": 0, "count": 0, "minimum": None, "maximum": None},
                )
                stats["count"] += 1
                if isinstance(value, (int, float)):
                    stats["total"] += value
                    stats["minimum"] = (
                        value
                        if stats["minimum"] is None
                        else min(stats["minimum"], value)
                    )
                    stats["maximum"] = (
                        value
                        if stats["maximum"] is None
                        else max(stats["maximum"], value)
                    )
        if not groups:
            return None
        key = max(
            groups,
            key=lambda item: self._aggregate_reference_score(info.kind, groups[item]),
        )
        stats = groups[key]
        return _AggregateGroupReference(
            total=stats["total"],
            count=stats["count"],
            minimum=stats["minimum"],
            maximum=stats["maximum"],
            group_values=group_columns[key],
        )

    def _aggregate_reference_score(self, kind: str, stats: Dict[str, Any]) -> int | float:
        if kind == "min":
            value = stats["minimum"]
            return value if isinstance(value, (int, float)) else float("-inf")
        if kind == "max":
            value = stats["maximum"]
            return value if isinstance(value, (int, float)) else float("-inf")
        if kind == "count":
            return stats["count"]
        return stats["total"]

    def _group_values_materializable(
        self,
        group_values: Tuple[Tuple[ColumnId, Any], ...],
        join_facts,
    ) -> bool:
        for group_col, group_value in group_values:
            if self._existing_unique_value_conflicts(group_col, group_value):
                return False
            for joined_col in self._joined_columns(group_col, join_facts):
                if self._existing_unique_value_conflicts(joined_col, group_value):
                    return False
        return True

    def _joined_columns(self, col: ColumnId, join_facts) -> Tuple[ColumnId, ...]:
        columns: List[ColumnId] = []
        for fact in join_facts:
            for left, right in fact.equalities:
                if self._same_column_identity(left, col):
                    columns.append(right)
                elif self._same_column_identity(right, col):
                    columns.append(left)
        return tuple(columns)

    def _same_column_identity(self, left: ColumnId, right: ColumnId) -> bool:
        return (
            left.relation == right.relation
            and left.name.normalized == right.name.normalized
        )

    def _existing_unique_value_conflicts(
        self,
        col: ColumnId,
        value: Any,
    ) -> bool:
        relation = self._storage_relation_for_column_id(col)
        if relation is None:
            return False
        storage_col = col.source_column_id or col
        if not self.instance.is_unique(relation, storage_col):
            return False
        return any(
            symbol.concrete == value
            for symbol in self.instance.get_column_data(relation, storage_col)
        )

    def _column_id_for_source(
        self,
        col: exp.Column,
        relation: RelationId,
    ) -> ColumnId:
        col_id = column_identity(col)
        if col_id is not None and col_id.relation == relation:
            return col_id
        return self._scoped_physical_column(col.name, relation)

    def _join_facts_for_node_path(self, node: BranchNode):
        facts = []
        current: BranchNode | None = node
        while current is not None:
            facts.extend(current.join_facts)
            current = current.parent
        return tuple(facts)

    def _row_value(self, row, col_id: ColumnId) -> Any:
        lookup = col_id.source_column_id or col_id
        for row_col, symbol in row.items():
            if not isinstance(row_col, ColumnId):
                continue
            if row_col.name.normalized != lookup.name.normalized:
                continue
            value = symbol.concrete
            return value
        return None

    def _group_value_for_source_row(
        self,
        source_row,
        source_col: ColumnId,
        group_col: ColumnId,
        join_facts,
    ) -> Any:
        source_relation = source_col.relation
        if source_relation == group_col.relation:
            return self._row_value(source_row, group_col)
        for fact in join_facts:
            for left, right in fact.equalities:
                if left.relation == group_col.relation and right.relation == source_relation:
                    source_value = self._row_value(source_row, right)
                    return self._lookup_joined_group_value(
                        left, source_value, group_col
                    )
                if right.relation == group_col.relation and left.relation == source_relation:
                    source_value = self._row_value(source_row, left)
                    return self._lookup_joined_group_value(
                        right, source_value, group_col
                    )
        return None

    def _lookup_joined_group_value(
        self,
        join_col: ColumnId,
        source_value: Any,
        group_col: ColumnId,
    ) -> Any:
        if source_value is None:
            return None
        relation = self._storage_relation_for_column_id(join_col)
        if relation is None:
            return None
        for row in self.instance.get_rows(relation):
            if self._row_value(row, join_col) == source_value:
                return self._row_value(row, group_col)
        return None

    def _scoped_expression(
        self,
        expression: exp.Expression,
        tables: Tuple[RelationId, ...],
        row_scope: str,
    ) -> exp.Expression:
        def scope(node: exp.Expression) -> exp.Expression:
            if not isinstance(node, exp.Column):
                return node
            col_id = column_identity(node)
            if col_id is None:
                raise UnresolvedScopedColumnError(
                    f'unresolved_scoped_column:{node.sql()}'
                )
            return self._constraint_column(col_id, row_scope=row_scope)

        return expression.copy().transform(scope)

    def _positive_predicate_constraints(
        self,
        expression: exp.Expression,
        subqueries: Tuple[SubqueryPath, ...] = (),
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for conjunct in self._split_conjuncts(expression):
            unwrapped = self._unwrap_scalar_subquery_comparison(conjunct, subqueries)
            if unwrapped is not None:
                constraints.extend(unwrapped)
            else:
                constraints.extend(self._positive_conjunct_constraints(conjunct))
        return constraints

    def _positive_conjunct_constraints(
        self,
        conjunct: exp.Expression,
    ) -> List[exp.Expression]:
        in_constraint = self._existing_values_in_constraint(conjunct)
        if in_constraint is not None:
            return [in_constraint]
        normalized = self._normalize_supported_function_predicate(conjunct)
        constraints = [normalized if normalized is not None else conjunct.copy()]
        if not isinstance(
            conjunct,
            (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ, exp.In),
        ):
            return constraints
        constraints.extend(
            exp.Is(this=column.copy(), expression=exp.Not(this=exp.Null()))
            for column in conjunct.find_all(exp.Column)
        )
        return constraints

    def _normalize_supported_function_predicate(
        self,
        expression: exp.Expression,
    ) -> exp.Expression | None:
        if not isinstance(expression, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return None

        left = expression.this
        right = expression.expression
        if isinstance(left, exp.Date) and isinstance(left.this, exp.Column):
            return type(expression)(this=left.this.copy(), expression=right.copy())
        if isinstance(right, exp.Date) and isinstance(right.this, exp.Column):
            flipped = {
                exp.GT: exp.LT,
                exp.GTE: exp.LTE,
                exp.LT: exp.GT,
                exp.LTE: exp.GTE,
                exp.EQ: exp.EQ,
                exp.NEQ: exp.NEQ,
            }[type(expression)]
            return flipped(this=right.this.copy(), expression=left.copy())
        return None

    def _split_conjuncts(self, expression: exp.Expression) -> List[exp.Expression]:
        if isinstance(expression, exp.And):
            return self._split_conjuncts(expression.left) + self._split_conjuncts(expression.right)
        if isinstance(expression, exp.Paren):
            return self._split_conjuncts(expression.this)
        return [expression]

    def _unwrap_scalar_subquery_comparison(
        self,
        expression: exp.Expression,
        subqueries: Tuple[SubqueryPath, ...] = (),
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
        subquery_path = self._subquery_path_for_subquery(subquery, subqueries)
        if subquery_path is None:
            return None

        inner_expr = self._scalar_subquery_value_expression_from_path(subquery_path)
        if inner_expr is None:
            return None
        inner_scope = "scalar_subquery"
        inner_relations = self._plan_relations(subquery_path.inner_root)
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
                subquery_path,
                row_scope=inner_scope,
                relations=inner_relations,
            )
        )
        return constraints

    def _subquery_path_for_subquery(
        self,
        subquery: exp.Subquery,
        subqueries: Tuple[SubqueryPath, ...],
    ) -> SubqueryPath | None:
        target_sql = subquery.sql(dialect=self.dialect)
        for candidate in subqueries:
            predicate = candidate.predicate
            if predicate is None:
                continue
            for nested in predicate.find_all(exp.Subquery):
                if nested is subquery or nested.sql(dialect=self.dialect) == target_sql:
                    return candidate
        return None

    def _scalar_subquery_value_expression_from_path(
        self,
        subquery_path: SubqueryPath,
    ) -> exp.Expression | None:
        saw_aggregate = False
        for step in self._iter_steps_with_subplans(subquery_path.inner_root):
            if not isinstance(step, Aggregate):
                continue
            saw_aggregate = True
            for operand in getattr(step, "operands", ()) or ():
                expression = operand.this if isinstance(operand, exp.Alias) else operand
                return expression.copy()
            for aggregation in getattr(step, "aggregations", ()) or ():
                expression = aggregation.this if isinstance(aggregation, exp.Alias) else aggregation
                witness = self._aggregate_scalar_witness_expression(expression)
                if witness is not None:
                    return witness
        if saw_aggregate:
            return None
        for step in self._iter_steps_with_subplans(subquery_path.inner_root):
            if not isinstance(step, Project):
                continue
            for projection in getattr(step, "projections", ()) or ():
                expression = projection.this if isinstance(projection, exp.Alias) else projection
                return expression.copy()
        return None

    def _aggregate_scalar_witness_expression(
        self,
        expression: exp.Expression,
    ) -> exp.Expression | None:
        if isinstance(expression, exp.Alias):
            return self._aggregate_scalar_witness_expression(expression.this)
        if isinstance(expression, (exp.Avg, exp.Min, exp.Max, exp.Sum)):
            return expression.this.copy() if expression.this is not None else None
        if isinstance(expression, exp.Cast):
            return self._aggregate_scalar_witness_expression(expression.this)
        if isinstance(expression, exp.Div):
            left = expression.this
            right = expression.expression
            numerator = self._aggregate_scalar_witness_expression(left)
            if numerator is None:
                return None
            return numerator if self._is_count_expression(right) else None
        return None

    def _is_count_expression(self, expression: exp.Expression | None) -> bool:
        if expression is None:
            return False
        if isinstance(expression, exp.Count):
            return True
        if isinstance(expression, exp.Cast):
            return self._is_count_expression(expression.this)
        return False

    def _plan_relations(self, root: Step) -> Tuple[RelationId, ...]:
        relations: List[RelationId] = []
        seen: Set[RelationId] = set()
        for step in self._iter_steps_with_subplans(root):
            if not isinstance(step, Scan) or getattr(step, "relation_id", None) is None:
                continue
            relation = step.relation_id
            if relation in seen:
                continue
            seen.add(relation)
            relations.append(relation)
        return tuple(relations)

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
                                self._scoped_physical_column(
                                    key_name, outer_relation
                                )
                            ),
                            expression=self._constraint_column(
                                self._scoped_physical_column(
                                    key_name, inner_relation
                                ),
                                row_scope=inner_scope,
                            ),
                        )
                    )
        return constraints

    def _scoped_physical_column(
        self,
        column_name: str,
        relation: RelationId,
    ) -> ColumnId:
        storage_relation = relation
        catalog_column_name = column_name
        try:
            table_key = self.instance._table_key_for_storage(relation)
            storage_relation = self.instance.table_id(table_key)
        except Exception:
            pass

        ordinal = None
        table_name = _relation_table_name(storage_relation)
        normalized_name = identifier_name(
            column_name,
            dialect=self.dialect,
        ).normalized
        for index, candidate in enumerate(self.instance.tables.get(table_name, {})):
            if identifier_name(candidate, dialect=self.dialect).normalized == normalized_name:
                ordinal = index
                catalog_column_name = candidate
                break

        source_column = physical_column(
            catalog_column_name, storage_relation, dialect=self.dialect
        )
        return column_id(
            ColumnKind.PHYSICAL,
            identifier_name(catalog_column_name),
            relation,
            scope_id=relation.scope_id,
            ordinal=ordinal,
            source_column_id=source_column,
        )

    def _identity_column_names(self, table_name: str) -> Tuple[str, ...]:
        names: List[str] = []

        def add(value: Any) -> None:
            raw = getattr(value, "name", value)
            normalized = raw
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
                rel = self._catalog_relation_for_unqualified_column(col, relations)
                if rel is not None:
                    col_id = physical_column(col.name, rel, dialect=self.dialect)
            if col_id is None or col_id.relation is None:
                continue
            col_id = self._scoped_physical_column(
                (col_id.source_column_id or col_id).name.raw,
                col_id.relation,
            )
            set_solver_var(col, self._solver_var_for_column(col_id, row_scope))
            if col.type is None:
                self._set_type_from_catalog(col, col_id)



    def _scalar_subquery_inner_constraints(
        self,
        subquery_path: SubqueryPath,
        row_scope: str | None = None,
        relations: Tuple[RelationId, ...] = (),
    ) -> List[exp.Expression]:
        inner_expressions: List[exp.Expression] = []
        for step in self._iter_steps_with_subplans(subquery_path.inner_root):
            if isinstance(step, Join):
                for join_data in step.joins.values():
                    source_keys = tuple(join_data.get("source_key", ()) or ())
                    join_keys = tuple(join_data.get("join_key", ()) or ())
                    for source_key, join_key in zip(source_keys, join_keys):
                        if isinstance(source_key, exp.Column) and isinstance(join_key, exp.Column):
                            inner_expressions.append(
                                exp.EQ(
                                    this=source_key.copy(),
                                    expression=join_key.copy(),
                                )
                            )
                    condition = join_data.get("condition")
                    if isinstance(condition, exp.Expression):
                        inner_expressions.append(condition)
            elif isinstance(step, Filter) and isinstance(step.condition, exp.Expression):
                inner_expressions.append(step.condition)

        constraints: List[exp.Expression] = []
        for expression in inner_expressions:
            for conjunct in self._split_conjuncts(expression):
                if conjunct.find(exp.Subquery):
                    continue
                copied = conjunct.copy()
                if row_scope is not None:
                    self._scope_expression_columns(copied, row_scope, relations)
                constraints.append(copied)
        return constraints

    def _constraints_for_row_set_obligations(
        self,
        obligations: Tuple[OperatorObligation, ...],
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for obligation in obligations:
            if obligation.kind != "row_set" or obligation.row_set is None:
                continue
            constraints.extend(self._constraints_for_row_set(obligation.row_set))
        return constraints

    def _row_sets_cover_having_predicate(
        self,
        predicate: PathPredicate,
        row_sets: Tuple[RowSetObligation, ...],
    ) -> bool:
        if predicate.node.site != "having" or predicate.outcome not in _POSITIVE_BITS:
            return False
        if not any(row_set.counted_expression is not None for row_set in row_sets):
            return False
        return bool(self._having_source_value_constraints(predicate))

    def _row_set_fixed_relations(
        self,
        row_set: RowSetObligation,
    ) -> Set[RelationId]:
        constant_columns: List[ColumnId] = [
            self._row_set_column_for_scope(row_set, group_key)
            for group_key in row_set.group_keys
        ]
        changed = True
        while changed:
            changed = False
            for fact in row_set.join_facts:
                for left, right in fact.equalities:
                    left_col = self._row_set_column_for_scope(row_set, left)
                    right_col = self._row_set_column_for_scope(row_set, right)
                    left_known = any(
                        self._same_column_identity(left_col, column)
                        for column in constant_columns
                    )
                    right_known = any(
                        self._same_column_identity(right_col, column)
                        for column in constant_columns
                    )
                    if left_known and not right_known:
                        constant_columns.append(right_col)
                        changed = True
                    elif right_known and not left_known:
                        constant_columns.append(left_col)
                        changed = True

        fixed: Set[RelationId] = set()
        for column in constant_columns:
            if column.relation is None:
                continue
            storage_relation = self._storage_relation_for_column_id(column)
            if storage_relation is None:
                continue
            storage_col = column.source_column_id or column
            if self.instance.is_unique(storage_relation, storage_col):
                fixed.add(column.relation)
        return fixed

    def _constraints_for_row_set(self, row_set: RowSetObligation) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        if not row_set.row_scopes:
            return constraints

        fixed_rels = self._row_set_fixed_relations(row_set)
        first_scope = row_set.row_scopes[0]

        for row_scope in row_set.row_scopes:
            for relation in row_set.relations:
                eff_scope = first_scope if relation in fixed_rels else row_scope
                if row_scope == first_scope or relation not in fixed_rels:
                    constraints.extend(
                        self._row_set_scan_relation_constraints(
                            row_set,
                            relation,
                            eff_scope,
                        )
                    )

            constraints.extend(
                self._row_set_join_constraints(
                    row_set,
                    row_scope,
                    fixed_rels,
                    first_scope,
                )
            )
            constraints.extend(
                self._row_set_predicate_constraints(
                    row_set,
                    row_scope,
                    fixed_rels,
                    first_scope,
                )
            )

            if row_set.counted_expression is not None:
                counted = self._scoped_expression_for_row_set(
                    row_set.counted_expression,
                    row_set.relations,
                    row_scope,
                    fixed_rels,
                    first_scope,
                )
                constraints.append(
                    exp.Is(this=counted, expression=exp.Not(this=exp.Null()))
                )

        constraints.extend(
            self._row_set_group_constraints(row_set, fixed_rels, first_scope)
        )
        constraints.extend(
            self._row_set_distinct_constraints(row_set, fixed_rels, first_scope)
        )
        constraints.extend(
            self._row_set_ordering_constraints(row_set, fixed_rels, first_scope)
        )
        return constraints

    def _row_set_relation_columns(self, relation: RelationId) -> Tuple[ColumnId, ...]:
        table_name = _relation_table_name(
            self._storage_relation_for_relation(relation) or relation
        )
        columns: List[ColumnId] = []
        for column_name in self.instance.tables.get(table_name, {}):
            columns.append(self._scoped_physical_column(column_name, relation))
        return tuple(columns)

    def _storage_relation_for_relation(self, relation: RelationId) -> RelationId | None:
        if relation.name is None:
            return None
        try:
            table_key = self.instance._table_key_for_storage(relation)
        except Exception:
            return None
        try:
            return self.instance.table_id(table_key)
        except Exception:
            return None

    def _existing_values_for_column(self, column: ColumnId) -> Set[Any]:
        relation = self._storage_relation_for_column_id(column)
        if relation is None:
            return set()
        obligation = OperatorObligation(
            kind="scan_exists",
            step_id="row_set",
            site="scan",
            relation=column.relation,
            storage_relation=relation,
            columns=(column,),
        )
        return self._existing_values_for_obligation(obligation, column)

    def _row_set_scan_relation_constraints(
        self,
        row_set: RowSetObligation,
        relation: RelationId,
        row_scope: str,
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        columns = self._row_set_relation_columns(relation)
        if not columns:
            return constraints
        identity_col = columns[0]
        identity = self._constraint_column(identity_col, row_scope=row_scope)
        constraints.append(
            exp.Is(this=identity.copy(), expression=exp.Not(this=exp.Null()))
        )
        existing_values = self._existing_values_for_column(identity_col)
        storage_relation = self._storage_relation_for_column_id(identity_col)
        storage_col = identity_col.source_column_id or identity_col
        is_identity = self._is_unique_storage_column(storage_relation, storage_col)
        if existing_values and is_identity:
            constraints.append(
                exp.Not(
                    this=exp.In(
                        this=identity.copy(),
                        expressions=[
                            exp.Literal.number(value)
                            if isinstance(value, (int, float))
                            else exp.Literal.string(str(value))
                            for value in existing_values
                        ],
                    )
                )
            )
        return constraints

    def _is_unique_storage_column(
        self,
        storage_relation: RelationId | None,
        storage_col: ColumnId,
    ) -> bool:
        if storage_relation is None:
            return False
        if self.instance.is_unique(storage_relation, storage_col):
            return True
        table_name = _relation_table_name(storage_relation)
        column_name = storage_col.name.normalized
        identity_names = {
            identifier_name(name, dialect=self.dialect).normalized
            for name in self._identity_column_names(table_name)
        }
        if column_name in identity_names:
            return True
        for key_columns in self.instance._constraint_groups(storage_relation):
            if len(key_columns) != 1:
                continue
            key_name = identifier_name(
                key_columns[0].name,
                dialect=self.dialect,
            ).normalized
            if key_name == column_name:
                return True
        return False

    def _row_set_join_constraints(
        self,
        row_set: RowSetObligation,
        row_scope: str,
        fixed_rels: Set[RelationId] | None = None,
        first_scope: str = "",
    ) -> List[exp.Expression]:
        fixed_rels = fixed_rels or set()
        constraints: List[exp.Expression] = []
        for fact in row_set.join_facts:
            eff_source = first_scope if fact.source_relation in fixed_rels else row_scope
            eff_target = first_scope if fact.target_relation in fixed_rels else row_scope
            for left, right in fact.equalities:
                left_scope = (
                    eff_source
                    if (
                        left.relation == fact.source_relation
                        or self._storage_relation_for_column_id(left)
                        == self._storage_relation_for_relation(fact.source_relation)
                    )
                    else eff_target
                )
                right_scope = (
                    eff_target
                    if (
                        right.relation == fact.target_relation
                        or self._storage_relation_for_column_id(right)
                        == self._storage_relation_for_relation(fact.target_relation)
                    )
                    else eff_source
                )
                constraints.append(
                    exp.EQ(
                        this=self._constraint_column(
                            self._visible_storage_column(left),
                            row_scope=left_scope,
                        ),
                        expression=self._constraint_column(
                            self._visible_storage_column(right),
                            row_scope=right_scope,
                        ),
                    )
                )
        return constraints

    def _visible_storage_column(self, col_id: ColumnId) -> ColumnId:
        physical_source = self._physical_lineage_for_visible_column(col_id)
        if physical_source is None or col_id.relation is None:
            return col_id
        return column_id(
            ColumnKind.SYNTHETIC,
            col_id.name,
            col_id.relation,
            scope_id=col_id.scope_id,
            ordinal=col_id.ordinal,
            source_column_id=physical_source,
        )

    def _scoped_expression_for_row_set(
        self,
        expression: exp.Expression,
        tables: Tuple[RelationId, ...],
        row_scope: str,
        fixed_rels: Set[RelationId],
        first_scope: str,
    ) -> exp.Expression:
        def scope(node: exp.Expression) -> exp.Expression:
            if not isinstance(node, exp.Column):
                return node
            col_id = column_identity(node)
            if col_id is None:
                raise UnresolvedScopedColumnError(
                    f'unresolved_scoped_column:{node.sql()}'
                )
            eff_scope = row_scope
            if col_id.relation in fixed_rels:
                eff_scope = first_scope
            else:
                storage = self._storage_relation_for_column_id(col_id)
                if storage is not None:
                    for rel in tables:
                        if (
                            rel in fixed_rels
                            and self._storage_relation_for_relation(rel) == storage
                        ):
                            eff_scope = first_scope
                            break
            col_id = self._visible_storage_column(col_id)
            return self._constraint_column(col_id, row_scope=eff_scope)

        return expression.copy().transform(scope)

    def _row_set_predicate_constraints(
        self,
        row_set: RowSetObligation,
        row_scope: str,
        fixed_rels: Set[RelationId] | None = None,
        first_scope: str = "",
    ) -> List[exp.Expression]:
        fixed_rels = fixed_rels or set()
        constraints: List[exp.Expression] = []
        for predicate in row_set.path_predicates:
            for conjunct in self._split_conjuncts(predicate):
                if conjunct.find(exp.Subquery):
                    constraints.extend(
                        self._row_set_subquery_predicate_constraints(
                            conjunct,
                            row_set,
                            row_scope,
                            fixed_rels,
                            first_scope,
                        )
                    )
                    continue
                scoped = self._scoped_expression_for_row_set(
                    conjunct,
                    row_set.relations,
                    row_scope,
                    fixed_rels,
                    first_scope,
                )
                if not _is_trivial_true(scoped):
                    constraints.append(scoped)
        return constraints

    def _row_set_subquery_predicate_constraints(
        self,
        predicate: exp.Expression,
        row_set: RowSetObligation,
        row_scope: str,
        fixed_rels: Set[RelationId],
        first_scope: str,
    ) -> List[exp.Expression]:
        if not isinstance(predicate, exp.In):
            return []
        if not isinstance(predicate.this, exp.Column):
            return []
        subplan = self._find_subplan_for_anchor(predicate)
        if subplan is None or subplan.inner is None:
            return []
        scoped_outer = self._scoped_expression_for_row_set(
            predicate.this,
            row_set.relations,
            row_scope,
            fixed_rels,
            first_scope,
        )
        inner_relations = self._plan_relations(subplan.inner)
        inner_value = self._in_inner_value_expression(subplan)
        if inner_value is None or not inner_relations:
            return []

        inner_scope = f"{row_scope}_inner"
        self._scope_expression_columns(inner_value, inner_scope, inner_relations)
        constraints: List[exp.Expression] = []
        for relation in inner_relations:
            constraints.extend(
                self._row_set_scan_relation_constraints(
                    row_set,
                    relation,
                    inner_scope,
                )
            )
        constraints.append(exp.EQ(this=scoped_outer, expression=inner_value))
        inner_path = SubqueryPath(
            node=None,
            inner_root=subplan.inner,
            predicate=predicate,
        )
        constraints.extend(
            self._scalar_subquery_inner_constraints(
                inner_path,
                row_scope=inner_scope,
                relations=inner_relations,
            )
        )
        return constraints

    def _existing_values_in_constraint(
        self,
        predicate: exp.Expression,
        outer_expression: exp.Expression | None = None,
    ) -> exp.Expression | None:
        if not isinstance(predicate, exp.In):
            return None
        if not isinstance(predicate.this, exp.Column):
            return None
        if not predicate.find(exp.Subquery):
            return None
        subplan = self._find_subplan_for_anchor(predicate)
        if subplan is None:
            return None
        inner_values = self._eval_inner_plan_values(subplan.inner)
        if not inner_values:
            return None
        return exp.In(
            this=outer_expression or predicate.this.copy(),
            expressions=[
                exp.Literal.number(value)
                if isinstance(value, (int, float))
                else exp.Literal.string(str(value))
                for value in inner_values
            ],
        )

    def _find_subplan_for_anchor(self, anchor: exp.Expression) -> SubPlan | None:
        target_sql = anchor.sql(dialect=self.dialect)
        for step in self.plan.ordered_steps:
            if not isinstance(step, SubPlan):
                continue
            step_anchor = getattr(step, "anchor", None)
            if step_anchor is anchor:
                return step
            if (
                isinstance(step_anchor, exp.Expression)
                and step_anchor.sql(dialect=self.dialect) == target_sql
            ):
                return step
        return None

    def _row_set_column_for_scope(
        self,
        row_set: RowSetObligation,
        column: ColumnId,
    ) -> ColumnId:
        source = column.source_column_id or column
        source_relation = self._storage_relation_for_column_id(column) or source.relation
        if source_relation is None:
            return column
        source_table = _relation_table_name(source_relation)
        for relation in row_set.relations:
            storage_relation = self._storage_relation_for_relation(relation) or relation
            if _relation_table_name(storage_relation) == source_table:
                return self._scoped_physical_column(source.name.raw, relation)
        return column

    def _row_set_group_constraints(
        self,
        row_set: RowSetObligation,
        fixed_rels: Set[RelationId] | None = None,
        first_scope: str = "",
    ) -> List[exp.Expression]:
        fixed_rels = fixed_rels or set()
        if not row_set.group_keys or len(row_set.row_scopes) < 2:
            return []
        constraints: List[exp.Expression] = []
        for row_scope in row_set.row_scopes[1:]:
            for group_key in row_set.group_keys:
                scoped_group_key = self._row_set_column_for_scope(row_set, group_key)
                # Only enforce group equality if the key's relation is not already fixed
                eff_scope = first_scope if scoped_group_key.relation in fixed_rels else row_scope
                if eff_scope != first_scope:
                    constraints.append(
                        exp.EQ(
                            this=self._constraint_column(scoped_group_key, row_scope=row_scope),
                            expression=self._constraint_column(
                                scoped_group_key,
                                row_scope=first_scope,
                            ),
                        )
                    )
        return constraints

    def _row_set_distinct_constraints(
        self,
        row_set: RowSetObligation,
        fixed_rels: Set[RelationId] | None = None,
        first_scope: str = "",
    ) -> List[exp.Expression]:
        fixed_rels = fixed_rels or set()
        if row_set.distinct_expression is None or len(row_set.row_scopes) < 2:
            return []
        constraints: List[exp.Expression] = []
        scoped_values = [
            self._scoped_expression_for_row_set(
                row_set.distinct_expression,
                row_set.relations,
                row_scope,
                fixed_rels,
                first_scope,
            )
            for row_scope in row_set.row_scopes
        ]
        for left_index, left in enumerate(scoped_values):
            for right in scoped_values[left_index + 1:]:
                constraints.append(exp.NEQ(this=left.copy(), expression=right.copy()))
        return constraints

    def _row_set_ordering_constraints(
        self,
        row_set: RowSetObligation,
        fixed_rels: Set[RelationId] | None = None,
        first_scope: str = "",
    ) -> List[exp.Expression]:
        fixed_rels = fixed_rels or set()
        if not row_set.ordering or len(row_set.row_scopes) < 2:
            return []
        if row_set.generation_rows < row_set.required_rows:
            return []
        selected_scope = row_set.row_scopes[0]
        constraints: List[exp.Expression] = []
        for ordered in row_set.ordering:
            expression = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            if not isinstance(expression, exp.Column):
                continue
            col_id = column_identity(expression)
            if (
                col_id is None
                or col_id.kind is ColumnKind.AGGREGATE
                or (
                    self._storage_relation_for_column_id(col_id) is None
                    and self._physical_lineage_for_visible_column(col_id) is None
                )
            ):
                continue
            descending = (
                bool(ordered.args.get("desc"))
                if isinstance(ordered, exp.Ordered)
                else False
            )
            selected = self._scoped_expression_for_row_set(
                expression,
                row_set.relations,
                selected_scope,
                fixed_rels,
                first_scope,
            )
            constraints.append(
                exp.Is(this=selected.copy(), expression=exp.Not(this=exp.Null()))
            )
            for row_scope in row_set.row_scopes[1:]:
                competitor = self._scoped_expression_for_row_set(
                    expression,
                    row_set.relations,
                    row_scope,
                    fixed_rels,
                    first_scope,
                )
                constraints.append(
                    exp.Is(
                        this=competitor.copy(),
                        expression=exp.Not(this=exp.Null()),
                    )
                )
                comparison = exp.GTE if descending else exp.LTE
                constraints.append(
                    comparison(this=selected.copy(), expression=competitor)
                )
        return constraints

    def _constraints_for_obligations(
        self,
        obligations: Tuple[OperatorObligation, ...],
        row_scope_by_relation: Dict[RelationId, str] | None,
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
                row_scope = obligation.row_scope
                if row_scope is None and row_count > 1:
                    row_scope = f"r{index}"
                if row_scope is None:
                    row_scope = self._row_scope_for_column(
                        identity_col,
                        row_scope_by_relation,
                    )
                col = self._constraint_column(identity_col, row_scope=row_scope)
                scoped_columns.append(col)
                constraints.append(
                    exp.Is(this=col.copy(), expression=exp.Not(this=exp.Null()))
                )
                if existing_values:
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
        column_name = identifier_name(
            exp.to_identifier(
                storage_column.name.raw,
                quoted=storage_column.name.quoted,
            ),
            dialect=self.dialect,
        ).normalized
        values: Set[Any] = set()
        for row in self.instance.get_rows(relation):
            for row_column, symbol in row.items():
                if not isinstance(row_column, ColumnId):
                    continue
                row_column_name = identifier_name(
                    exp.to_identifier(
                        row_column.name.raw,
                        quoted=row_column.name.quoted,
                    ),
                    dialect=self.dialect,
                ).normalized
                if row_column_name != column_name:
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
        if (
            column.source_column_id is None
            and column.kind in {ColumnKind.SYNTHETIC, ColumnKind.DERIVED}
        ):
            return None
        source = column.source_column_id or column
        relation = source.relation or column.relation
        if relation is None or relation.name is None:
            return None
        # Non-physical relations (derived tables, CTEs, subqueries) have no
        # physical storage in the instance — skip DB constraints for them.
        from parseval.identity import RelationKind
        if relation.kind != RelationKind.TABLE:
            return None
        table_key = self.instance._table_key_for_storage(relation)
        return self.instance.table_id(table_key)


    def _apply_database_constraints(
        self,
        constraints: List[exp.Expression],
        tables: Tuple[RelationId, ...],
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> Tuple[List[exp.Expression], List[Tuple[ColumnId, ColumnId]]]:
        del tables
        context = _DatabaseConstraintContext.build(
            self,
            constraints,
            row_scope_by_relation,
        )
        context.lower_all()
        return constraints, []

    def _generated_key_uniqueness_constraints(
        self,
        active_storage_relations: Set[RelationId],
        required_vars: Tuple[_DatabaseVariable, ...],
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []

        by_binding_and_storage = {
            (db_var.binding_key, db_var.storage_key): db_var.variable
            for db_var in required_vars
        }

        def variable_for(
            column: ColumnId,
            binding_key: Tuple[str, str | None, str | None, str | None],
        ) -> SolverVar | None:
            key = self._storage_column_key(column)
            if key is None:
                return None
            return by_binding_and_storage.get((binding_key, key))

        for relation in active_storage_relations:
            for column_name in self.instance._table_columns_for_storage(relation):
                column = self.instance._stored_column_id(relation, column_name)
                if not self.instance.is_unique(relation, column):
                    continue
                key = self._storage_column_key(column)
                if key is None:
                    continue
                scoped = [
                    db_var.variable
                    for db_var in required_vars
                    if db_var.storage_key == key
                    and db_var.storage_relation == relation
                ]
                for left_index, left_var in enumerate(scoped):
                    for right_var in scoped[left_index + 1:]:
                        constraints.append(
                            exp.NEQ(
                                this=self._constraint_column(
                                    left_var.column_id,
                                    row_scope=left_var.row_scope,
                                ),
                                expression=self._constraint_column(
                                    right_var.column_id,
                                    row_scope=right_var.row_scope,
                                ),
                        )
                        )
            for key_columns in self.instance._constraint_groups(relation):
                binding_keys = sorted(
                    {
                        db_var.binding_key
                        for db_var in required_vars
                        if db_var.storage_relation == relation
                        and variable_for(key_columns[0], db_var.binding_key) is not None
                    },
                    key=lambda value: tuple("" if part is None else part for part in value),
                )
                scoped_keys: List[Tuple[SolverVar, ...]] = []
                for binding_key in binding_keys:
                    key_vars = tuple(
                        variable
                        for column in key_columns
                        if (variable := variable_for(column, binding_key)) is not None
                    )
                    if len(key_vars) == len(key_columns):
                        scoped_keys.append(key_vars)

                for left_index, left_key in enumerate(scoped_keys):
                    for right_key in scoped_keys[left_index + 1:]:
                        disjunct: exp.Expression | None = None
                        for left_var, right_var in zip(left_key, right_key):
                            left = self._constraint_column(
                                left_var.column_id,
                                row_scope=left_var.row_scope,
                            )
                            right = self._constraint_column(
                                right_var.column_id,
                                row_scope=right_var.row_scope,
                            )
                            comparison = exp.NEQ(this=left, expression=right)
                            disjunct = (
                                comparison
                                if disjunct is None
                                else exp.Or(this=disjunct, expression=comparison)
                            )
                        if disjunct is not None:
                            constraints.append(disjunct)
        return constraints

    def _storage_column_key(self, column: ColumnId) -> Tuple[str, str] | None:
        relation = self._storage_relation_for_column_id(column)
        storage_col = column.source_column_id or column
        if relation is None or relation.name is None:
            return None
        return relation.name.normalized, storage_col.name.normalized

    def _database_variable_for_solver_var(
        self,
        variable: SolverVar,
    ) -> _DatabaseVariable | None:
        storage_relation = self._storage_relation_for_column_id(variable.column_id)
        storage_key = self._storage_column_key(variable.column_id)
        if storage_relation is None or storage_key is None:
            return None
        relation = variable.relation_id
        binding_key = (
            storage_relation.name.normalized if storage_relation.name else "",
            relation.alias.normalized if relation.alias is not None else None,
            relation.scope_id,
            variable.row_scope,
        )
        return _DatabaseVariable(
            variable=variable,
            storage_relation=storage_relation,
            storage_key=storage_key,
            binding_key=binding_key,
        )

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
            left_scope = (
                row_scope_by_relation.get(left_id.relation)
                if row_scope_by_relation is not None
                else None
            )
            right_scope = (
                row_scope_by_relation.get(right_id.relation)
                if row_scope_by_relation is not None
                else None
            )
            lowered.append((
                self._storage_solver_var_for_visible_column(left_id, left_scope)
                or self._solver_var_for_column(left_id, left_scope),
                self._storage_solver_var_for_visible_column(right_id, right_scope)
                or self._solver_var_for_column(right_id, right_scope),
            ))
        return lowered

    def _storage_solver_var_for_visible_column(
        self,
        col_id: ColumnId,
        row_scope: str | None,
    ) -> SolverVar | None:
        if col_id.relation is None:
            return None
        physical_source = self._physical_lineage_for_visible_column(col_id)
        if physical_source is None:
            return None
        logical_col = column_id(
            ColumnKind.SYNTHETIC,
            col_id.name,
            col_id.relation,
            scope_id=col_id.scope_id,
            ordinal=col_id.ordinal,
            source_column_id=physical_source,
        )
        return SolverVar(
            column_id=logical_col,
            relation_id=col_id.relation,
            row_scope=row_scope,
        )

    def _physical_lineage_for_visible_column(
        self,
        col_id: ColumnId,
    ) -> ColumnId | None:
        from parseval.identity import RelationKind

        def key(column: ColumnId) -> tuple:
            relation = column.relation
            return (
                column.kind,
                column.name.normalized,
                relation.kind if relation is not None else None,
                relation.name.normalized if relation is not None and relation.name else None,
                relation.alias.normalized if relation is not None and relation.alias else None,
                relation.scope_id if relation is not None else None,
                column.scope_id,
                column.ordinal,
            )

        def compatible(source: ColumnId, candidate: ColumnId) -> bool:
            if source.name.normalized != candidate.name.normalized:
                return False
            if source.relation is not None and candidate.relation == source.relation:
                return True
            if source.scope_id is not None and candidate.scope_id == source.scope_id:
                return True
            if (
                source.relation is not None
                and source.relation.kind is RelationKind.SYNTHETIC
                and source.relation.scope_id is not None
                and candidate.scope_id == source.relation.scope_id
            ):
                return True
            return False

        def resolve(column: ColumnId, seen: Set[tuple]) -> ColumnId | None:
            column_key = key(column)
            if column_key in seen:
                return None
            seen.add(column_key)

            direct = self._deepest_physical_source(column)
            if direct is not None:
                return direct

            if column.source_column_id is not None:
                source_physical = resolve(column.source_column_id, seen)
                if source_physical is not None:
                    return source_physical

            if column.relation is not None:
                for step in self.plan.ordered_steps:
                    if not isinstance(step, Scan):
                        continue
                    annotation = self.plan.annotation_for(step)
                    if column.relation not in tuple(annotation.source_relations):
                        continue
                    for subplan in step.subplan_dependencies:
                        for inner_step in self._iter_steps_with_subplans(subplan):
                            for output_col in tuple(
                                getattr(inner_step, "output_column_ids", ()) or ()
                            ):
                                if not isinstance(output_col, ColumnId):
                                    continue
                                if output_col.name.normalized != column.name.normalized:
                                    continue
                                source = output_col.source_column_id or output_col
                                physical = resolve(source, seen)
                                if physical is not None:
                                    return physical
                            if not isinstance(inner_step, SubPlan):
                                continue
                            metadata = self.plan.annotation_for(inner_step).metadata.get(
                                "subquery", {}
                            )
                            for output_col in metadata.get("output_columns", ()):
                                if not isinstance(output_col, ColumnId):
                                    continue
                                if output_col.name.normalized != column.name.normalized:
                                    continue
                                source = output_col.source_column_id or output_col
                                physical = resolve(source, seen)
                                if physical is not None:
                                    return physical

            for step in self._iter_steps_with_subplans(self.plan.root):
                for output_col in tuple(getattr(step, "output_column_ids", ()) or ()):
                    if not isinstance(output_col, ColumnId):
                        continue
                    if output_col == column:
                        continue
                    if not compatible(column, output_col):
                        continue
                    source = output_col.source_column_id or output_col
                    physical = resolve(source, seen)
                    if physical is not None:
                        return physical
                if not isinstance(step, SubPlan):
                    continue
                metadata = self.plan.annotation_for(step).metadata.get("subquery", {})
                for output_col in metadata.get("output_columns", ()):
                    if not isinstance(output_col, ColumnId):
                        continue
                    if output_col == column:
                        continue
                    if not compatible(column, output_col):
                        continue
                    source = output_col.source_column_id or output_col
                    physical = resolve(source, seen)
                    if physical is not None:
                        return physical
            return None

        return resolve(col_id, set())

    def _deepest_physical_source(self, col_id: ColumnId) -> ColumnId | None:
        current = col_id
        seen: Set[int] = set()
        physical: ColumnId | None = None
        while id(current) not in seen:
            seen.add(id(current))
            if current.kind is ColumnKind.PHYSICAL:
                physical = current
            if current.source_column_id is None:
                break
            current = current.source_column_id
        return physical

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

    def _row_scopes_for_path(
        self,
        path: BranchPath,
        tables: Tuple[RelationId, ...],
    ) -> Dict[RelationId, str]:
        join_scopes = self._join_row_scopes(path.join_facts)
        if join_scopes:
            return join_scopes
        row_scoped_sites = {
            "project_output",
            "aggregate_output",
            "aggregate_distinct_input",
        }
        if any(predicate.node.site in row_scoped_sites for predicate in path.predicates):
            return {relation: "r0" for relation in tables}
        return {}

    def _with_join_scope(
        self,
        variable: SolverVar,
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> SolverVar:
        if row_scope_by_relation is None:
            return variable
        if variable.row_scope is not None:
            canonical_relation = self._canonical_path_relation_for_column(
                variable.column_id,
                row_scope_by_relation,
            )
            if canonical_relation is None or canonical_relation == variable.relation_id:
                return variable
            canonical_col = self._scoped_physical_column(
                (variable.column_id.source_column_id or variable.column_id).name.raw,
                canonical_relation,
            )
            return self._solver_var_for_column(
                canonical_col,
                variable.row_scope,
            )
        row_scope = self._row_scope_for_column(
            variable.column_id,
            row_scope_by_relation,
        )
        if row_scope is None:
            row_scope = row_scope_by_relation.get(variable.relation_id)
        if row_scope is None or variable.row_scope == row_scope:
            return variable
        canonical_relation = self._canonical_path_relation_for_column(
            variable.column_id,
            row_scope_by_relation,
        )
        canonical_col = variable.column_id
        relation_id = variable.relation_id
        if canonical_relation is not None:
            relation_id = canonical_relation
            canonical_col = self._scoped_physical_column(
                (variable.column_id.source_column_id or variable.column_id).name.raw,
                canonical_relation,
            )
        scoped = self._solver_var_for_column(canonical_col, row_scope)
        if scoped.relation_id == relation_id:
            return scoped
        return SolverVar(
            column_id=scoped.column_id,
            relation_id=relation_id,
            row_scope=scoped.row_scope,
        )

    def _row_scope_for_column(
        self,
        col_id: ColumnId,
        row_scope_by_relation: Dict[RelationId, str] | None,
    ) -> str | None:
        if row_scope_by_relation is None or col_id.relation is None:
            return None
        direct = row_scope_by_relation.get(col_id.relation)
        if direct is not None:
            return direct
        storage_relation = self._storage_relation_for_column_id(col_id)
        if storage_relation is None:
            return None
        matches: List[str] = []
        for relation, row_scope in row_scope_by_relation.items():
            if relation == storage_relation:
                matches.append(row_scope)
                continue
            try:
                relation_storage = self._storage_relation_for_column_id(
                    column_id(
                        ColumnKind.PHYSICAL,
                        col_id.name,
                        relation,
                        source_column_id=col_id.source_column_id,
                    )
                )
            except Exception:
                relation_storage = None
            if relation_storage == storage_relation:
                matches.append(row_scope)
        return matches[0] if len(set(matches)) == 1 else None

    def _canonical_path_relation_for_column(
        self,
        col_id: ColumnId,
        row_scope_by_relation: Dict[RelationId, str],
    ) -> RelationId | None:
        if col_id.relation in row_scope_by_relation:
            return col_id.relation
        storage_relation = self._storage_relation_for_column_id(col_id)
        if storage_relation is None:
            return None
        matches: List[RelationId] = []
        for relation in row_scope_by_relation:
            if relation == storage_relation:
                matches.append(relation)
                continue
            try:
                relation_storage = self._storage_relation_for_column_id(
                    column_id(
                        ColumnKind.PHYSICAL,
                        col_id.name,
                        relation,
                        source_column_id=col_id.source_column_id,
                    )
                )
            except Exception:
                relation_storage = None
            if relation_storage == storage_relation:
                matches.append(relation)
        return matches[0] if len(matches) == 1 else None

    def _generate_exists_constraint(self, target: CoverageTarget) -> SolverConstraint:
        subplan = self._find_subplan_for_target(target)
        if subplan and subplan.correlation:
            corr_col = subplan.correlation[0]
            outer_rel = self._planner_relation_for_column(corr_col, target.node.tables)

            if outer_rel is not None and target.target_outcome == PlausibleBit.EXISTS_FALSE:
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

    def _generate_in_constraint(self, target: CoverageTarget) -> SolverConstraint:
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

        coordinated = self._generate_coordinated_in_constraint(target, atom, subplan)
        if coordinated is not None:
            return coordinated

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

        if target.target_outcome == PlausibleBit.IN_MATCH:
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

    def _generate_coordinated_in_constraint(
        self,
        target: CoverageTarget,
        atom: exp.In,
        subplan: SubPlan,
    ) -> SolverConstraint | None:
        if target.target_outcome != PlausibleBit.IN_MATCH:
            return None
        if not isinstance(atom.this, exp.Column) or subplan.inner is None:
            return None

        outer_relations = self._in_outer_relations(subplan)
        inner_relations = self._plan_relations(subplan.inner)
        if not outer_relations or not inner_relations:
            return None

        outer_scope = "in_outer"
        inner_scope = "in_inner"
        row_scope_by_relation = {
            **{relation: outer_scope for relation in outer_relations},
            **{relation: inner_scope for relation in inner_relations},
        }

        constraints: List[exp.Expression] = []
        join_equalities: List[Tuple[ColumnId, ColumnId]] = []

        outer_value = atom.this.copy()
        self._scope_expression_columns(outer_value, outer_scope, outer_relations)
        inner_value = self._in_inner_value_expression(subplan)
        if inner_value is None:
            return None
        self._scope_expression_columns(inner_value, inner_scope, inner_relations)
        constraints.append(exp.EQ(this=outer_value, expression=inner_value))

        constraints.extend(
            self._in_outer_predicate_constraints(
                subplan,
                atom,
                outer_scope,
                outer_relations,
            )
        )
        inner_path = SubqueryPath(
            node=target.node,
            inner_root=subplan.inner,
            predicate=atom,
        )
        constraints.extend(
            self._scalar_subquery_inner_constraints(
                inner_path,
                row_scope=inner_scope,
                relations=inner_relations,
            )
        )
        join_equalities.extend(self._join_equalities_for_root(subplan.consumer))
        join_equalities.extend(self._join_equalities_for_root(subplan.inner))
        constraints.extend(
            self._distinct_storage_constraints_between_relation_sets(
                outer_relations,
                inner_relations,
                outer_scope,
                inner_scope,
                join_equalities,
            )
        )

        target_relations = outer_relations + inner_relations
        self._annotate_solver_vars(
            constraints,
            target_relations,
            row_scope_by_relation=row_scope_by_relation,
        )
        constraints, extra_join_equalities = self._apply_database_constraints(
            constraints=constraints,
            tables=target_relations,
            row_scope_by_relation=row_scope_by_relation,
        )
        join_equalities.extend(extra_join_equalities)
        self._annotate_solver_vars(
            constraints,
            target_relations,
            row_scope_by_relation=row_scope_by_relation,
        )
        lowered_joins = self._lower_join_equalities(
            join_equalities,
            target_relations,
            row_scope_by_relation=row_scope_by_relation,
        )

        variables: Dict[SolverVar, DataType] = {}
        for left_var, right_var in lowered_joins:
            for variable in (left_var, right_var):
                dtype = self._datatype_for_column_id(variable.column_id)
                if dtype is not None:
                    variables[variable] = dtype

        return SolverConstraint(
            target_relations=target_relations,
            constraints=constraints,
            join_equalities=lowered_joins,
            variables=variables,
            storage_relations=self._storage_relations_for_constraint(
                constraints,
                lowered_joins,
                variables,
                (),
            ),
        )

    def _in_outer_relations(self, subplan: SubPlan) -> Tuple[RelationId, ...]:
        consumer = subplan.consumer
        if consumer is None:
            return ()
        annotation = self.plan.annotation_for(consumer)
        return tuple(annotation.source_relations)

    def _in_inner_value_expression(self, subplan: SubPlan) -> exp.Expression | None:
        for step in self._iter_steps_with_subplans(subplan.inner):
            if not isinstance(step, Project):
                continue
            projections = tuple(getattr(step, "projections", ()) or ())
            if not projections:
                return None
            projection = projections[0]
            return projection.this.copy() if isinstance(projection, exp.Alias) else projection.copy()
        return None

    def _in_outer_predicate_constraints(
        self,
        subplan: SubPlan,
        atom: exp.In,
        row_scope: str,
        relations: Tuple[RelationId, ...],
    ) -> List[exp.Expression]:
        consumer = subplan.consumer
        condition = getattr(consumer, "condition", None)
        if not isinstance(condition, exp.Expression):
            return []

        constraints: List[exp.Expression] = []
        target_sql = atom.sql(dialect=self.dialect)
        for conjunct in self._split_conjuncts(condition):
            if conjunct is atom or conjunct.sql(dialect=self.dialect) == target_sql:
                continue
            if conjunct.find(exp.Subquery):
                continue
            copied = conjunct.copy()
            self._scope_expression_columns(copied, row_scope, relations)
            constraints.extend(self._positive_conjunct_constraints(copied))
        return constraints

    def _join_equalities_for_root(
        self,
        root: Step | None,
    ) -> List[Tuple[ColumnId, ColumnId]]:
        if root is None:
            return []
        equalities: List[Tuple[ColumnId, ColumnId]] = []
        for step in self._iter_steps_with_subplans(root):
            if not isinstance(step, Join):
                continue
            for join_data in (step.joins or {}).values():
                source_keys = tuple(join_data.get("source_key", ()) or ())
                join_keys = tuple(join_data.get("join_key", ()) or ())
                for source_key, join_key in zip(source_keys, join_keys):
                    if not isinstance(source_key, exp.Column) or not isinstance(join_key, exp.Column):
                        continue
                    source_id = column_identity(source_key)
                    join_id = column_identity(join_key)
                    if source_id is not None and join_id is not None:
                        equalities.append((source_id, join_id))
        return equalities

    def _distinct_storage_constraints_between_relation_sets(
        self,
        left_relations: Tuple[RelationId, ...],
        right_relations: Tuple[RelationId, ...],
        left_scope: str,
        right_scope: str,
        equalities: List[Tuple[ColumnId, ColumnId]],
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for left_relation in left_relations:
            left_table = self._storage_table_name(left_relation)
            if left_table is None:
                continue
            key_names = self._identity_column_names(left_table)
            if not key_names:
                continue
            for right_relation in right_relations:
                if left_relation == right_relation:
                    continue
                if self._storage_table_name(right_relation) != left_table:
                    continue
                for key_name in key_names:
                    left_col = self._scoped_physical_column(key_name, left_relation)
                    right_col = self._scoped_physical_column(key_name, right_relation)
                    if self._columns_equated(left_col, right_col, equalities):
                        continue
                    constraints.append(
                        exp.NEQ(
                            this=self._constraint_column(left_col, row_scope=left_scope),
                            expression=self._constraint_column(
                                right_col,
                                row_scope=right_scope,
                            ),
                        )
                    )
        return constraints

    def _storage_table_name(self, relation: RelationId) -> str | None:
        try:
            return self.instance._table_key_for_storage(relation)
        except Exception:
            return None

    def _columns_equated(
        self,
        left: ColumnId,
        right: ColumnId,
        equalities: List[Tuple[ColumnId, ColumnId]],
    ) -> bool:
        for eq_left, eq_right in equalities:
            if (
                self._same_column_identity(left, eq_left)
                and self._same_column_identity(right, eq_right)
            ) or (
                self._same_column_identity(left, eq_right)
                and self._same_column_identity(right, eq_left)
            ):
                return True
        return False

    def _generate_distinct_constraint(self, target: CoverageTarget) -> SolverConstraint:
        tables = target.node.tables
        step = self._step_for_node(target.node)
        if step is None or not isinstance(step, Project):
            return SolverConstraint(
                target_relations=tables,
                constraints=[exp.Literal.string("DISTINCT")],
            )

        annotation = self._step_annotation(target.node)
        projected_col_ids = annotation.projected_columns if annotation else ()

        phys_col_ids: List[ColumnId] = []
        for col_id in projected_col_ids:
            phys = col_id.source_column_id or col_id
            if phys.relation is not None:
                phys_col_ids.append(phys)

        if not phys_col_ids:
            return SolverConstraint(
                target_relations=tables,
                constraints=[exp.Literal.string("DISTINCT")],
            )

        constraints: List[exp.Expression] = []
        for col_id in phys_col_ids:
            col_r0 = self._constraint_column(col_id, row_scope="r0")
            col_r1 = self._constraint_column(col_id, row_scope="r1")

            if target.target_outcome in {PlausibleBit.DISTINCT_DUPLICATE, PlausibleBit.DUPLICATE}:
                constraints.append(exp.EQ(this=col_r0, expression=col_r1))
            else:
                constraints.append(exp.NEQ(this=col_r0, expression=col_r1))

        for col_id in phys_col_ids:
            for i in range(2):
                col_ri = self._constraint_column(col_id, row_scope=f"r{i}")
                constraints.append(exp.Is(this=col_ri, expression=exp.Not(this=exp.Null())))

        return SolverConstraint(
            target_relations=tables,
            constraints=constraints,
            storage_relations=self._storage_relations_for_constraint(
                constraints,
                [],
                {},
                (),
            ),
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
            set_solver_var(col, self._solver_var_for_column(column, row_scope))
        return col

    def _solver_var_for_column(
        self,
        column: ColumnId,
        row_scope: str | None = None,
    ) -> SolverVar:
        return SolverVar(
            column_id=self._solver_column_id(column),
            relation_id=column.relation,
            row_scope=row_scope,
        )

    def _solver_column_id(self, column: ColumnId) -> ColumnId:
        source = column.source_column_id or column
        if column.relation is None:
            return column
        if source.kind is not ColumnKind.PHYSICAL:
            return column
        return column_id(
            ColumnKind.SYNTHETIC,
            column.name,
            column.relation,
            scope_id=column.scope_id,
            ordinal=column.ordinal,
            source_column_id=source,
        )

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

        The planner sets ``col.type`` during enrichment, so most columns
        already have their type.  For newly constructed columns (IS NOT NULL,
        unique avoidance) that weren't in step expressions, fall back to
        catalog lookup.
        """
        for expr in constraints:
            for col in expr.find_all(exp.Column):
                existing_var = solver_var(col)
                if existing_var is not None:
                    if existing_var.column_id.relation is not None:
                        source = existing_var.column_id.source_column_id
                        if source is None or source.relation == existing_var.column_id.relation:
                            canonical_col = self._scoped_physical_column(
                                (source or existing_var.column_id).name.raw,
                                existing_var.column_id.relation,
                            )
                            existing_var = self._solver_var_for_column(
                                canonical_col,
                                existing_var.row_scope,
                            )
                            set_solver_var(col, existing_var)
                    scoped_var = self._with_join_scope(existing_var, row_scope_by_relation)
                    if scoped_var is not existing_var:
                        set_solver_var(col, scoped_var)
                    if col.type is None:
                        self._set_type_from_catalog(col, existing_var.column_id)
                    continue
                col_id = self._column_id_for_expr(col, tables)
                if col_id is None:
                    continue
                if col.type is None:
                    self._set_type_from_catalog(col, col_id)
                rel_id = col_id.relation
                if rel_id is None and tables:
                    rel_id = tables[0]
                if rel_id is None:
                    continue
                sv = self._solver_var_for_column(
                    col_id,
                    self._row_scope_for_column(col_id, row_scope_by_relation),
                )
                if sv.relation_id != rel_id:
                    sv = SolverVar(
                        column_id=sv.column_id,
                        relation_id=rel_id,
                        row_scope=sv.row_scope,
                    )
                set_solver_var(col, sv)

    def _set_type_from_catalog(self, col: exp.Column, col_id: ColumnId) -> None:
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
        if col.table:
            raise UnresolvedScopedColumnError(
                f"unresolved_scoped_column:{col.sql(dialect=self.dialect)}"
            )
        rel = self._catalog_relation_for_unqualified_column(col, tables)
        if rel is None:
            return None
        return column_id(
            ColumnKind.SYNTHETIC,
            identifier_name(col.name, dialect=self.dialect),
            rel,
        )

    def _eval_inner_plan_values(self, root: Step) -> set:
        """Evaluate an inner plan and return the set of projected column values."""
        from .evaluator import PlanEvaluator

        ctx = PlanEvaluator(self.plan, self.instance, self.dialect)._evaluate_subtree(root)
        values = set()
        for table in ctx.tables.values():
            columns = tuple(table.columns)
            for row in table.rows:
                if columns:
                    symbol = row[columns[0]]
                else:
                    try:
                        symbol = next(iter(row.columns.values()))
                    except StopIteration:
                        continue
                value = symbol.concrete
                if value is not None:
                    values.add(value)
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
                if getattr(step, "anchor", None) is target.node.predicate:
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

    def _planner_relation_for_column(
        self,
        col: exp.Column,
        tables: Tuple[RelationId, ...],
    ) -> RelationId | None:
        """Resolve a query column only from planner or solver identity."""
        del tables
        col_id = column_identity(col)
        if col_id is not None and col_id.relation is not None:
            return col_id.relation
        variable = solver_var(col)
        if variable is not None:
            return variable.relation_id
        if col.table:
            raise UnresolvedScopedColumnError(
                f"unresolved_scoped_column:{col.sql(dialect=self.dialect)}"
            )
        return None

    def _catalog_relation_for_unqualified_column(
        self,
        col: exp.Column,
        tables: Tuple[RelationId, ...],
    ) -> RelationId | None:
        """Resolve generated unqualified catalog expressions by schema shape."""
        if column_identity(col) is not None or solver_var(col) is not None:
            return self._planner_relation_for_column(col, tables)
        if col.table:
            raise UnresolvedScopedColumnError(
                f"unresolved_scoped_column:{col.sql(dialect=self.dialect)}"
            )
        col_name = identifier_name(
            col.args.get("this") if isinstance(col.args.get("this"), exp.Identifier) else col.name,
            dialect=self.dialect,
        ).normalized
        candidates: List[RelationId] = []
        for rel in tables:
            name = _relation_table_name(rel)
            if name not in self.instance.tables:
                continue
            column_names = {
                identifier_name(candidate, dialect=self.dialect).normalized
                for candidate in self.instance.tables[name]
            }
            if col_name in column_names:
                candidates.append(rel)
        if len(candidates) == 1:
            return candidates[0]
        return None

    def _relation_for_visible_name(
        self,
        table_name: str,
        tables: Tuple[RelationId, ...],
    ) -> RelationId | None:
        normalized = identifier_name(table_name, dialect=self.dialect).normalized
        for relation in tables:
            names = {
                relation.alias.normalized if relation.alias else None,
                relation.name.normalized if relation.name else None,
            }
            if normalized in names:
                return relation
        return None

    def _storage_relation_for_table_reference(
        self,
        table: exp.Table,
    ) -> RelationId | None:
        table_name = table.name
        try:
            return self.instance.table_id(table_name)
        except Exception:
            return None


__all__ = [
    "ConstraintGenerator",
]
