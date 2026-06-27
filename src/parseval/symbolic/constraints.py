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


@dataclass(frozen=True)
class _AggregateGroupReference:
    total: int | float
    count: int
    minimum: int | float | None
    maximum: int | float | None
    group_values: Tuple[Tuple[ColumnId, Any], ...]


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

        for predicate in path.predicates:
            constraints.extend(self._constraints_for_path_predicate(predicate))
        constraints.extend(
            self._constraints_for_obligations(
                path.obligations,
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
                return self._positive_predicate_constraints(
                    predicate.node.predicate
                    if predicate.expression is predicate.node.predicate
                    else expression,
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
        expression = predicate.expression
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

        info = self._aggregate_alias_info(alias_col.name)
        if info is None or info.source is None:
            return []
        relation = self._relation_for_visible_name(info.source.table, predicate.node.tables)
        if relation is None:
            return []
        literal_value = self._literal_number(literal)
        if literal_value is None:
            return []
        reference = self._aggregate_group_reference(
            info,
            relation,
            predicate.node,
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

    def _aggregate_alias_info(
        self, alias: str
    ) -> _AggregateAliasInfo | None:
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            annotation = self.plan.annotation_for(step)
            agg_meta = annotation.metadata.get("aggregation", {})
            for _col_id, info in agg_meta.get("aggregate_outputs", {}).items():
                if info["alias"] == alias:
                    kind = info["function"]
                    argument = info["argument"]  # ColumnId
                    if argument is not None:
                        source_col = _column_expr_from_id(argument)
                        return _AggregateAliasInfo(step, kind, source_col)
                    # COUNT(*) has no argument column
                    return _AggregateAliasInfo(step, kind, None)
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
            row_scope = None if index == 0 else f"r{index}"
            counted = self._constraint_column(source_col, row_scope=row_scope)
            counted_cols.append(counted)
            constraints.append(
                exp.Is(this=counted.copy(), expression=exp.Not(this=exp.Null()))
            )

        for left_index, left in enumerate(counted_cols):
            for right in counted_cols[left_index + 1:]:
                constraints.append(exp.NEQ(this=left.copy(), expression=right.copy()))

        group_cols = self._aggregate_group_columns(info)
        for group_col in group_cols:
            if reference is not None:
                continue
            first = self._constraint_column(
                group_col,
                row_scope=None,
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
        constraints = [conjunct.copy()]
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
            set_solver_var(
                col,
                SolverVar(
                    column_id=col_id,
                    relation_id=col_id.relation,
                    row_scope=row_scope,
                ),
            )
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
                if row_scope is None:
                    row_scope = self._row_scope_for_column(
                        identity_col,
                        row_scope_by_relation,
                    )
                if row_scope is None and row_count > 1:
                    row_scope = f"r{index}"
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
        path_predicates = list(constraints)
        not_null_vars: List[SolverVar] = []
        avoid_values: Dict[SolverVar, Set[Any]] = {}
        foreign_keys: List[Tuple[SolverVar, RelationId, ColumnId]] = []
        required_vars: Dict[Tuple[Tuple[str, str] | None, str | None], SolverVar] = {}

        for expr in path_predicates:
            for col in expr.find_all(exp.Column):
                variable = solver_var(col)
                if variable is None:
                    continue
                variable = self._with_join_scope(variable, row_scope_by_relation)
                storage_key = self._storage_column_key(variable.column_id)
                required_key = (storage_key, variable.row_scope)
                if required_key in required_vars:
                    continue
                required_vars[required_key] = variable
                col_id = variable.column_id
                storage_relation = self._storage_relation_for_column_id(col_id)
                if storage_relation is None:
                    continue
                storage_col = col_id.source_column_id or col_id
                if not self.instance.nullable(storage_relation, storage_col):
                    not_null_vars.append(variable)
                if self.instance.is_unique(storage_relation, storage_col):
                    existing = {
                        sym.concrete
                        for sym in self.instance.get_column_data(storage_relation, storage_col)
                        if sym.concrete is not None
                    }
                    if existing:
                        avoid_values[variable] = existing

        required_by_storage: Dict[Tuple[str, str], List[SolverVar]] = {}
        for (key, _row_scope), variable in required_vars.items():
            if key is not None:
                required_by_storage.setdefault(key, []).append(variable)

        active_storage_relations: Set[RelationId] = {
            storage_relation
            for variable in required_vars.values()
            if (storage_relation := self._storage_relation_for_column_id(variable.column_id))
            is not None
        }

        for rel in active_storage_relations:
            table_name = _relation_table_name(rel)
            if table_name not in self.instance.tables:
                continue
            for fk in self.instance.get_foreign_key(table_name):
                local_col = physical_column(fk.expressions[0].name, rel, dialect=self.dialect)
                local_key = self._storage_column_key(local_col)
                local_vars = (
                    required_by_storage.get(local_key, [])
                    if local_key is not None
                    else []
                )
                if not local_vars:
                    continue
                ref = fk.args.get("reference")
                ref_table_node = ref.find(exp.Table) if ref is not None else None
                ref_col = self.instance.resolve_fk_ref_column(fk)
                ref_rel = (
                    self._storage_relation_for_table_reference(ref_table_node)
                    if ref_table_node is not None
                    else None
                )
                if ref_rel is not None and ref_col is not None:
                    ref_column = physical_column(ref_col, ref_rel, dialect=self.dialect)
                    for local_var in local_vars:
                        foreign_keys.append(
                            (
                                local_var,
                                ref_rel,
                                ref_column,
                            )
                        )
            for check_expr in self.instance.get_check_constraints(table_name):
                constraints.append(check_expr)

        for variable in not_null_vars:
            constraints.append(
                exp.Is(
                    this=self._constraint_column(
                        variable.column_id,
                        row_scope=variable.row_scope,
                    ),
                    expression=exp.Not(this=exp.Null()),
                )
            )
        for variable, vals in avoid_values.items():
            constraints.append(
                exp.Not(
                    this=exp.In(
                        this=self._constraint_column(
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
        for local_var, ref_rel, ref_col in foreign_keys:
            parent_vals = []
            if _relation_table_name(ref_rel) in self.instance.tables:
                for row in self.instance.get_rows(ref_rel):
                    val = self._row_value(row, ref_col)
                    if val is not None:
                        parent_vals.append(val)
            if parent_vals:
                constraints.append(
                    exp.In(
                        this=self._constraint_column(
                            local_var.column_id,
                            row_scope=local_var.row_scope,
                        ),
                        expressions=[
                            exp.Literal.number(v)
                            if isinstance(v, (int, float))
                            else exp.Literal.string(str(v))
                            for v in parent_vals
                        ],
                    )
                )
        return constraints, []

    def _storage_column_key(self, column: ColumnId) -> Tuple[str, str] | None:
        relation = self._storage_relation_for_column_id(column)
        storage_col = column.source_column_id or column
        if relation is None or relation.name is None:
            return None
        return relation.name.normalized, storage_col.name.normalized

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
            return SolverVar(
                column_id=canonical_col,
                relation_id=canonical_relation,
                row_scope=variable.row_scope,
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
        return SolverVar(
            column_id=canonical_col,
            relation_id=relation_id,
            row_scope=row_scope,
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
                        canonical_col = self._scoped_physical_column(
                            (existing_var.column_id.source_column_id or existing_var.column_id).name.raw,
                            existing_var.column_id.relation,
                        )
                        existing_var = SolverVar(
                            column_id=canonical_col,
                            relation_id=existing_var.relation_id,
                            row_scope=existing_var.row_scope,
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
                sv = SolverVar(
                    column_id=col_id,
                    relation_id=rel_id,
                    row_scope=self._row_scope_for_column(col_id, row_scope_by_relation),
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
