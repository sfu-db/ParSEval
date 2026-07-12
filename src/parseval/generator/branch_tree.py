from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from sqlglot import exp
import sqlglot

from parseval.dtype import DataType
from parseval.instance.schema import InstanceSchema
from parseval.plan.explain import (
    Aggregate,
    EmptyRelation,
    Filter,
    Join,
    Limit,
    Projection,
    Sort,
    Step,
    SubqueryAlias,
    TableScan,
    explain,
)
from parseval.solver import Solver
from parseval.solver.types import Problem, SolverVar

from .bindings import Branch, RelationBinding, RowBinding, Scope
from .bounds import BmcBounds
from .constraints import UnsupportedQueryFeature


@dataclass
class GenerateResult:
    status: str
    create_rows: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, object]]] = field(
        default_factory=dict
    )
    reason: str = ""
    bounds: BmcBounds = field(default_factory=BmcBounds)
    problem: Optional[Problem] = None
    assignments: Mapping[SolverVar, object] = field(default_factory=dict)

    @property
    def sat(self) -> bool:
        return self.status == "sat"


@dataclass(frozen=True)
class _AggregateProfile:
    row_count: int = 1
    null_mixed_columns: Tuple[str, ...] = ()


@dataclass(frozen=True)
class _AggregateOutput:
    variable: SolverVar
    constraints: List[exp.Expression]


class _AggregateSemantics:
    def __init__(self, generator: "BranchTreeGenerator", step: Aggregate) -> None:
        self._generator = generator
        self._step = step
        self.profile = self._profile_for_step(step)

    @property
    def required_rows(self) -> int:
        return self.profile.row_count

    def input_constraints(self, branch: Branch) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for column_sql in self.profile.null_mixed_columns:
            inputs = self._inputs_for_sql(column_sql, branch, minimum=2)
            if len(inputs) < 2:
                raise UnsupportedQueryFeature("aggregate_null_mixed_requires_two_rows")
            constraints.append(_is_not_null(inputs[0].copy()))
            constraints.append(_is_null(inputs[1].copy()))
        return constraints

    def output_expression(
        self, aggregate: exp.Expression, scope: Scope, branch: Branch
    ) -> _AggregateOutput:
        name = _aggregate_column_name(aggregate)
        dtype = (
            DataType.build("REAL")
            if isinstance(aggregate, exp.Avg)
            else DataType.build("BIGINT")
        )
        variable = SolverVar(
            key=f"q{scope.query_id}.{scope.scope_id}.__agg.r0.{name}",
            dtype=dtype,
            meta={
                "table": "__aggregate__",
                "alias": "",
                "column": name,
                "row_index": 0,
                "scope_id": scope.scope_id,
                "source_step": "Aggregate",
            },
        )
        if isinstance(aggregate, exp.Count):
            return _AggregateOutput(
                variable,
                [
                    exp.EQ(
                        this=variable,
                        expression=exp.Literal.number(self._count_value(aggregate)),
                    )
                ],
            )
        if isinstance(aggregate, exp.Sum):
            source = _aggregate_argument(aggregate)
            if source is None:
                raise UnsupportedQueryFeature("unsupported_aggregate_sum")
            inputs = self._inputs_for_expression(source, branch, minimum=1)
            return _AggregateOutput(
                variable,
                [exp.EQ(this=variable, expression=_sum_expression(inputs))],
            )
        if isinstance(aggregate, exp.Avg):
            source = _aggregate_argument(aggregate)
            if source is None:
                raise UnsupportedQueryFeature("unsupported_aggregate_avg")
            inputs = self._inputs_for_expression(source, branch, minimum=2)
            if len(inputs) < 2:
                raise UnsupportedQueryFeature("avg_requires_two_input_rows")
            total = _sum_expression(inputs)
            average = exp.Div(
                this=total,
                expression=exp.Literal.number(len(inputs)),
            )
            return _AggregateOutput(
                variable,
                [
                    exp.EQ(this=variable, expression=average),
                    *(_is_not_null(input_expr.copy()) for input_expr in inputs),
                    exp.NEQ(this=inputs[0].copy(), expression=inputs[1].copy()),
                ],
            )
        if isinstance(aggregate, (exp.Min, exp.Max)):
            source = _aggregate_argument(aggregate)
            if source is None:
                raise UnsupportedQueryFeature(f"unsupported_aggregate_{aggregate.key}")
            inputs = self._inputs_for_expression(source, branch, minimum=1)
            constraints: List[exp.Expression] = [
                exp.EQ(this=variable, expression=inputs[0].copy())
            ]
            dominance = exp.LTE if isinstance(aggregate, exp.Min) else exp.GTE
            for competitor in inputs[1:]:
                constraints.append(
                    dominance(this=variable.copy(), expression=competitor.copy())
                )
            return _AggregateOutput(variable, constraints)
        raise UnsupportedQueryFeature(f"unsupported_aggregate_{aggregate.key}")

    def _count_value(self, aggregate: exp.Count) -> int:
        source = _aggregate_argument(aggregate)
        if source is None or _count_argument_is_star(source):
            return self.profile.row_count
        if isinstance(source, exp.Distinct):
            return max(1, self.profile.row_count)
        if self._expression_sql(source) in self.profile.null_mixed_columns:
            return max(self.profile.row_count - 1, 0)
        return self.profile.row_count

    def _inputs_for_sql(
        self, expression_sql: str, branch: Branch, *, minimum: int
    ) -> List[exp.Expression]:
        for aggregate in self._step.aggregations:
            source = _aggregate_argument(aggregate)
            if source is None or isinstance(source, exp.Distinct):
                continue
            if self._expression_sql(source) == expression_sql:
                return self._inputs_for_expression(source, branch, minimum=minimum)
        raise UnsupportedQueryFeature("unknown_aggregate_input")

    def _inputs_for_expression(
        self, expression: exp.Expression, branch: Branch, *, minimum: int
    ) -> List[exp.Expression]:
        inputs: List[exp.Expression] = []
        for row_index in _candidate_row_indexes(branch):
            try:
                inputs.append(
                    _unwrap_cast(
                        _rewrite_expr_for_row(
                            expression,
                            branch,
                            row_index,
                            dialect=self._generator._dialect,
                        )
                    )
                )
            except UnsupportedQueryFeature:
                continue
            if len(inputs) >= max(minimum, self.profile.row_count):
                break
        if len(inputs) < minimum:
            raise UnsupportedQueryFeature("aggregate_requires_input_rows")
        return inputs

    @classmethod
    def _profile_for_step(cls, step: Aggregate) -> _AggregateProfile:
        required = 1
        null_mixed: List[str] = []
        has_count_star = False
        count_columns: List[str] = []
        for aggregate in step.aggregations:
            source = _aggregate_argument(aggregate)
            if isinstance(aggregate, exp.Avg):
                required = max(required, 2)
            if isinstance(aggregate, exp.Count):
                if source is None or _count_argument_is_star(source):
                    has_count_star = True
                elif isinstance(source, exp.Distinct):
                    columns = tuple(source.expressions)
                    if len(columns) != 1:
                        raise UnsupportedQueryFeature("unsupported_count_distinct")
                else:
                    count_columns.append(cls._expression_sql(source))
        if has_count_star and count_columns:
            required = max(required, 2)
            null_mixed.extend(count_columns)
        return _AggregateProfile(
            row_count=required,
            null_mixed_columns=tuple(dict.fromkeys(null_mixed)),
        )

    @staticmethod
    def _expression_sql(expression: exp.Expression) -> str:
        return expression.sql(dialect="sqlite")


class BranchTreeGenerator:
    def __init__(self, solver: Optional[Solver] = None) -> None:
        self.solver = solver
        self._dialect = "sqlite"
        self._schema: Optional[InstanceSchema] = None
        self._bounds = BmcBounds()
        self._force_all_filter_rows = False

    def generate(
        self,
        ddl: str,
        query: str,
        dialect: str = "sqlite",
        bounds: Optional[BmcBounds] = None,
    ) -> GenerateResult:
        current = bounds or BmcBounds()
        last_reason = "unsat"
        while True:
            result = self._generate_once(ddl, query, dialect, current)
            if result.status in {"sat", "bounded_unknown", "unknown"}:
                return result
            last_reason = result.reason
            current = result.bounds
            if current.exhausted:
                return GenerateResult(
                    status="bounded_unknown",
                    reason=last_reason or "bounds_exhausted",
                    bounds=current,
                    problem=result.problem,
                )
            current = current.expand_next()

    def _generate_once(
        self, ddl: str, query: str, dialect: str, bounds: BmcBounds
    ) -> GenerateResult:
        schema = InstanceSchema.from_ddl(ddl, dialect)
        plan = explain(ddl, query, dialect)
        self._dialect = dialect
        self._schema = schema
        self._force_all_filter_rows = False
        bounds, cardinality_reason = _cardinality_bounds_for_step(plan.root, bounds)
        self._bounds = bounds
        if cardinality_reason:
            return GenerateResult(
                status="bounded_unknown",
                reason=cardinality_reason,
                bounds=bounds,
            )
        scope = Scope(query_id=0, scope_id="s0")
        try:
            branch = self._execute(plan.root, schema, scope, bounds)
        except UnsupportedQueryFeature as exc:
            return GenerateResult(status="unknown", reason=str(exc), bounds=bounds)

        problem = Problem(
            constraints=branch.constraints,
            equalities=branch.equalities,
            variables=set(branch.variables),
        )
        solved = (self.solver or Solver(dialect=dialect)).solve(problem)
        if solved.status != "sat":
            return GenerateResult(
                status=solved.status,
                reason=solved.reason,
                bounds=bounds,
                problem=problem,
            )

        create_rows = _assignments_to_create_rows(
            branch.relation.rows, solved.assignments
        )
        return GenerateResult(
            status="sat",
            create_rows=create_rows,
            reason="",
            bounds=bounds,
            problem=problem,
            assignments=solved.assignments,
        )

    def _execute(
        self,
        step: Step,
        schema: InstanceSchema,
        scope: Scope,
        bounds: BmcBounds,
    ) -> Branch:
        if isinstance(step, TableScan):
            return self._table_scan(step, schema, scope, bounds)
        if isinstance(step, Filter):
            branch = self._execute(_single_dependency(step), schema, scope, bounds)
            if step.condition is not None:
                if self._force_all_filter_rows:
                    branch.constraints.extend(
                        self._rewrite_expr_for_candidate_rows(step.condition, scope, branch)
                    )
                else:
                    branch.constraints.append(self._rewrite_expr(step.condition, scope, branch))
            return branch
        if isinstance(step, Projection):
            branch = self._execute(_single_dependency(step), schema, scope, bounds)
            for projection in step.projections:
                rewritten = self._rewrite_expr(projection, scope, branch)
                _bind_output_expression(branch, projection, rewritten)
            return branch
        if isinstance(step, Join):
            return self._join(step, schema, scope, bounds)
        if isinstance(step, Sort):
            branch = self._execute(_single_dependency(step), schema, scope, bounds)
            if step.fetch == 1 and step.key:
                self._add_limit_one_dominance(step, branch, scope)
            return branch
        if isinstance(step, Limit):
            child = _single_dependency(step)
            branch = self._execute(child, schema, scope, bounds)
            if step.fetch is None:
                raise UnsupportedQueryFeature("limit_fetch_unknown")
            offset = step.offset or 0
            if offset > 0:
                sort = _find_upstream_sort(child)
                if sort is not None and sort.key:
                    self._add_offset_window_ranking(
                        sort, branch, scope, offset=offset, fetch=step.fetch
                    )
            return branch
        if isinstance(step, Aggregate):
            return self._aggregate(step, schema, scope, bounds)
        if isinstance(step, SubqueryAlias):
            branch = self._execute(_single_dependency(step), schema, scope, bounds)
            self._add_alias_view(step, branch, scope)
            return branch
        if isinstance(step, EmptyRelation):
            return Branch(relation=RelationBinding())
        raise UnsupportedQueryFeature(f"unsupported_step_{step.type_name}")

    def _table_scan(
        self,
        step: TableScan,
        schema: InstanceSchema,
        scope: Scope,
        bounds: BmcBounds,
    ) -> Branch:
        table = schema.resolve_table(step.table)
        table_schema = schema.get_table(table)
        alias = step.name if step.name is not None else table.this
        columns = {
            column.identifier: DataType.build(str(column.datatype))
            for column in table_schema.columns.values()
        }
        width = bounds.table_rows + bounds.order_competitors
        rows = [
            RowBinding.for_table(
                table=table,
                alias=alias,
                row_index=index,
                columns=columns,
                scope=scope,
                source_step=step,
            )
            for index in range(width)
        ]
        if rows:
            scope.add_row(rows[0])
        return Branch(relation=RelationBinding(rows=rows))

    def _join(
        self,
        step: Join,
        schema: InstanceSchema,
        scope: Scope,
        bounds: BmcBounds,
    ) -> Branch:
        left_step, right_step = _join_inputs(step)
        left = self._execute(left_step, schema, scope, bounds)
        right = self._execute(right_step, schema, scope, bounds)
        relation = RelationBinding(
            rows=list(left.relation.rows) + list(right.relation.rows),
            expressions={**left.relation.expressions, **right.relation.expressions},
        )
        branch = Branch(
            relation=relation,
            constraints=[*left.constraints, *right.constraints],
            equalities=[*left.equalities, *right.equalities],
        )
        for left_key, right_key in step.on_keys:
            branch.equalities.extend(_join_row_equalities(left_key, right_key, left, right))
        if step.condition is not None:
            branch.constraints.append(self._rewrite_expr(step.condition, scope, branch))
        return branch

    def _aggregate(
        self,
        step: Aggregate,
        schema: InstanceSchema,
        scope: Scope,
        bounds: BmcBounds,
    ) -> Branch:
        semantics = _AggregateSemantics(self, step)
        previous_force = self._force_all_filter_rows
        if semantics.required_rows > 1:
            self._force_all_filter_rows = True
        try:
            branch = self._execute(_single_dependency(step), schema, scope, bounds)
        finally:
            self._force_all_filter_rows = previous_force
        branch.constraints.extend(semantics.input_constraints(branch))
        for group_expr in step.group:
            rewritten = self._rewrite_expr(group_expr, scope, branch)
            _bind_output_expression(branch, group_expr, rewritten)
            if semantics.required_rows > 1:
                branch.constraints.extend(
                    _group_local_constraints(group_expr, branch, dialect=self._dialect)
                )
            group_name = _group_column_name(group_expr)
            if group_name is not None:
                scope.add_row(
                    RowBinding(
                        table=exp.table_("__aggregate__"),
                        alias=None,
                        row_index=0,
                        columns={exp.to_identifier(group_name): rewritten},
                        scope_id=scope.scope_id,
                        source_step_type=step.type_name,
                    )
                )
        for aggregate in step.aggregations:
            aggregate_output = semantics.output_expression(aggregate, scope, branch)
            _bind_output_expression(branch, aggregate, aggregate_output.variable)
            branch.constraints.extend(aggregate_output.constraints)
            scope.add_row(
                RowBinding(
                    table=exp.table_("__aggregate__"),
                    alias=None,
                    row_index=0,
                    columns={
                        exp.to_identifier(
                            _aggregate_column_name(aggregate)
                        ): aggregate_output.variable
                    },
                    scope_id=scope.scope_id,
                    source_step_type=step.type_name,
                )
            )
        return branch

    def _add_limit_one_dominance(
        self, step: Sort, branch: Branch, scope: Scope
    ) -> None:
        first_key = step.key[0]
        expression = first_key.this if isinstance(first_key, exp.Ordered) else first_key
        direction_desc = isinstance(first_key, exp.Ordered) and bool(first_key.args.get("desc"))
        rewritten = self._rewrite_expr(expression, scope, branch)
        if not isinstance(rewritten, SolverVar):
            return
        target_column = _column_for_var(branch.relation.rows, rewritten)
        if target_column is None:
            return
        for row in branch.relation.rows:
            competitor = row.resolve(target_column)
            if competitor is None or competitor == rewritten:
                continue
            op = exp.GTE if direction_desc else exp.LTE
            branch.constraints.append(op(this=rewritten, expression=competitor))

    def _add_offset_window_ranking(
        self,
        step: Sort,
        branch: Branch,
        scope: Scope,
        *,
        offset: int,
        fetch: int,
    ) -> None:
        first_key = step.key[0]
        expression = first_key.this if isinstance(first_key, exp.Ordered) else first_key
        direction_desc = isinstance(first_key, exp.Ordered) and bool(
            first_key.args.get("desc")
        )
        rewritten = self._rewrite_expr(expression, scope, branch)
        if not isinstance(rewritten, SolverVar):
            return
        target_column = _column_for_var(branch.relation.rows, rewritten)
        if target_column is None:
            return
        ranked = sorted(
            (
                row
                for row in branch.relation.rows
                if row.resolve(target_column) is not None
            ),
            key=lambda row: row.row_index,
        )
        needed = offset + fetch
        if len(ranked) < needed:
            raise UnsupportedQueryFeature("limit_offset_insufficient_rows")
        predecessors = ranked[:offset]
        window = ranked[offset:needed]
        better = exp.GT if direction_desc else exp.LT
        for pred in predecessors:
            pred_var = pred.resolve(target_column)
            assert pred_var is not None
            for win in window:
                win_var = win.resolve(target_column)
                assert win_var is not None
                branch.constraints.append(better(this=pred_var, expression=win_var))
        for band in (predecessors, window):
            for left, right in zip(band, band[1:]):
                left_var = left.resolve(target_column)
                right_var = right.resolve(target_column)
                assert left_var is not None and right_var is not None
                branch.constraints.append(better(this=left_var, expression=right_var))

    def _rewrite_expr(
        self, expr: exp.Expression, scope: Scope, branch: Branch
    ) -> exp.Expression:
        outer_branch = Branch(
            relation=RelationBinding(
                rows=list(branch.relation.rows),
                expressions=dict(branch.relation.expressions),
            )
        )
        def transform(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Subquery):
                return self._scalar_subquery(node, scope, branch)
            if isinstance(node, exp.Column):
                projected = _resolve_projected_expression(
                    node, branch, dialect=self._dialect
                )
                if projected is not None:
                    return projected.copy()
            return node

        expression = expr.copy().transform(transform)
        row_indexes = _candidate_row_indexes(outer_branch)
        if not row_indexes:
            return expression
        rewritten = _rewrite_expr_for_row(
            expression,
            outer_branch,
            row_indexes[0],
            dialect=self._dialect,
        )
        return _rewrite_ratio_threshold(rewritten)

    def _rewrite_expr_for_candidate_rows(
        self,
        expr: exp.Expression,
        scope: Scope,
        branch: Branch,
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        outer_branch = Branch(
            relation=RelationBinding(
                rows=list(branch.relation.rows),
                expressions=dict(branch.relation.expressions),
            )
        )
        def transform(node: exp.Expression) -> exp.Expression:
            if isinstance(node, exp.Subquery):
                return self._scalar_subquery(node, scope, branch)
            return node

        expression = expr.copy().transform(transform)
        for row_index in _candidate_row_indexes(branch):
            constraints.append(
                _rewrite_expr_for_row(
                    expression,
                    outer_branch,
                    row_index,
                    dialect=self._dialect,
                )
            )
        return constraints

    def _scalar_subquery(
        self, subquery: exp.Subquery, scope: Scope, branch: Branch
    ) -> exp.Expression:
        root = subquery.this
        if isinstance(root, Projection) and len(root.projections) == 1:
            dependency = _single_dependency(root)
            if isinstance(dependency, EmptyRelation):
                return root.projections[0].copy()
            if not _scalar_subquery_is_single_row(root):
                raise UnsupportedQueryFeature("unsupported_scalar_subquery")
            if self._schema is None:
                raise UnsupportedQueryFeature("unsupported_scalar_subquery")
            child_scope = scope.child(f"subq{len(branch.relation.rows)}")
            child = self._execute(
                root, schema=self._schema, scope=child_scope, bounds=self._bounds
            )
            result = self._rewrite_expr(root.projections[0], child_scope, child)
            branch.relation.rows.extend(child.relation.rows)
            branch.relation.expressions.update(child.relation.expressions)
            branch.constraints.extend(child.constraints)
            branch.equalities.extend(child.equalities)
            return result
        if isinstance(root, Aggregate) and not root.group and len(root.aggregations) == 1:
            if self._schema is None:
                raise UnsupportedQueryFeature("unsupported_scalar_subquery")
            child_scope = scope.child(f"subq{len(branch.relation.rows)}")
            child = self._execute(
                root, schema=self._schema, scope=child_scope, bounds=self._bounds
            )
            result = _resolve_bound_expression(
                root.aggregations[0],
                child,
                dialect=self._dialect,
            )
            if result is None:
                raise UnsupportedQueryFeature("unsupported_scalar_subquery")
            branch.relation.rows.extend(child.relation.rows)
            branch.relation.expressions.update(child.relation.expressions)
            branch.constraints.extend(child.constraints)
            branch.equalities.extend(child.equalities)
            return result
        raise UnsupportedQueryFeature("unsupported_scalar_subquery")

    def _add_alias_view(
        self, step: SubqueryAlias, branch: Branch, scope: Scope
    ) -> None:
        alias = step.alias.name
        if not alias:
            return
        alias_identifier = exp.to_identifier(alias)
        alias_rows: List[RowBinding] = []
        for row in branch.relation.rows:
            alias_row = RowBinding(
                table=row.table,
                alias=alias_identifier,
                row_index=row.row_index,
                columns=row.columns,
                scope_id=row.scope_id,
                source_step_type=step.type_name,
            )
            alias_rows.append(alias_row)
            if row.row_index == 0:
                scope.add_row(alias_row)
        branch.relation.rows = alias_rows


def _single_dependency(step: Step) -> Step:
    if len(step.dependencies) != 1:
        raise UnsupportedQueryFeature(f"{step.type_name}_requires_one_dependency")
    return next(iter(step.dependencies))


def _find_upstream_sort(step: Step) -> Optional[Sort]:
    current: Optional[Step] = step
    while current is not None:
        if isinstance(current, Sort):
            return current
        if isinstance(current, (Projection, SubqueryAlias, Filter)):
            try:
                current = _single_dependency(current)
            except UnsupportedQueryFeature:
                return None
            continue
        return None
    return None


def _join_inputs(step: Join) -> Tuple[Step, Step]:
    if step.left is not None and step.right is not None:
        return step.left, step.right
    deps = list(step.dependencies)
    if len(deps) != 2:
        raise UnsupportedQueryFeature("join_requires_two_inputs")
    return deps[0], deps[1]


def _cardinality_bounds_for_step(step: Step, bounds: BmcBounds) -> Tuple[BmcBounds, str]:
    return bounds.raise_table_rows(_required_rows_for_step(step))


def _required_rows_for_step(step: Step) -> int:
    required = 1
    if isinstance(step, Aggregate):
        required = max(required, _AggregateSemantics._profile_for_step(step).row_count)
    if isinstance(step, Sort) and step.fetch is not None:
        required = max(required, step.fetch)
    if isinstance(step, Limit):
        required = max(required, (step.offset or 0) + (step.fetch or 1))
    for expression in _step_expressions(step):
        required = max(required, _required_rows_for_expression(expression))
    for dependency in step.dependencies:
        required = max(required, _required_rows_for_step(dependency))
    return required


def _step_expressions(step: Step) -> Tuple[exp.Expression, ...]:
    if isinstance(step, Filter) and step.condition is not None:
        return (step.condition,)
    if isinstance(step, Projection):
        return tuple(step.projections)
    if isinstance(step, Aggregate):
        return tuple(step.aggregations) + tuple(step.group)
    if isinstance(step, Join) and step.condition is not None:
        return (step.condition,)
    if isinstance(step, Sort):
        return tuple(step.key)
    return ()


def _required_rows_for_expression(expression: exp.Expression) -> int:
    required = 1
    for subquery in expression.find_all(exp.Subquery):
        root = subquery.this
        if isinstance(root, Step):
            required = max(required, _required_rows_for_step(root))
    return required


def _bind_output_expression(
    branch: Branch,
    output: exp.Expression,
    rewritten: exp.Expression,
) -> None:
    branch.relation.expressions[output.copy()] = rewritten
    output_name = output.meta.get("datafusion_name")
    if output_name:
        branch.relation.expressions[exp.column(str(output_name))] = rewritten
    alias = output.alias_or_name
    if alias:
        branch.relation.expressions[exp.column(alias)] = rewritten
    if isinstance(output, exp.Alias):
        branch.relation.expressions[output.this.copy()] = rewritten


def _resolve_projected_expression(
    column: exp.Column,
    branch: Branch,
    *,
    dialect: str,
) -> Optional[exp.Expression]:
    for candidate in _projection_column_candidates(column, dialect=dialect):
        for expression, rewritten in branch.relation.expressions.items():
            if expression == candidate:
                return rewritten
    return None


def _resolve_bound_expression(
    target: exp.Expression,
    branch: Branch,
    *,
    dialect: str,
) -> Optional[exp.Expression]:
    target_sql = target.sql(dialect=dialect)
    for expression, rewritten in branch.relation.expressions.items():
        if expression == target or expression.sql(dialect=dialect) == target_sql:
            return rewritten
    return None


def _aggregate_argument(aggregate: exp.Expression) -> Optional[exp.Expression]:
    return aggregate.this if isinstance(aggregate.this, exp.Expression) else None


def _count_argument_is_star(argument: exp.Expression) -> bool:
    if isinstance(argument, exp.Star):
        return True
    if isinstance(argument, exp.Literal):
        return True
    return False


def _sum_expression(expressions: Sequence[exp.Expression]) -> exp.Expression:
    if not expressions:
        raise UnsupportedQueryFeature("aggregate_requires_input_rows")
    total = expressions[0].copy()
    for expression in expressions[1:]:
        total = exp.Add(this=total, expression=expression.copy())
    return total


def _is_null(expression: exp.Expression) -> exp.Expression:
    return exp.Is(this=expression, expression=exp.Null())


def _is_not_null(expression: exp.Expression) -> exp.Expression:
    return exp.Is(this=expression, expression=exp.Not(this=exp.Null()))


def _group_local_constraints(
    group_expr: exp.Expression,
    branch: Branch,
    *,
    dialect: str,
) -> List[exp.Expression]:
    row_indexes = _candidate_row_indexes(branch)
    if len(row_indexes) < 2:
        return []
    try:
        first = _rewrite_expr_for_row(
            group_expr,
            branch,
            row_indexes[0],
            dialect=dialect,
        )
    except UnsupportedQueryFeature:
        return []
    constraints: List[exp.Expression] = []
    for row_index in row_indexes[1:]:
        try:
            current = _rewrite_expr_for_row(
                group_expr,
                branch,
                row_index,
                dialect=dialect,
            )
        except UnsupportedQueryFeature:
            continue
        constraints.append(exp.EQ(this=first.copy(), expression=current))
    return constraints


def _candidate_row_indexes(branch: Branch) -> Tuple[int, ...]:
    return tuple(sorted({row.row_index for row in branch.relation.rows}))


def _rewrite_expr_for_row(
    expression: exp.Expression,
    branch: Branch,
    row_index: int,
    *,
    dialect: str,
) -> exp.Expression:
    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            projected = _resolve_projected_expression(
                node,
                branch,
                dialect=dialect,
            )
            if projected is not None and not isinstance(projected, SolverVar):
                return _rewrite_expr_for_row(
                    projected,
                    branch,
                    row_index,
                    dialect=dialect,
                )
            resolved = _resolve_row_column(node, branch.relation.rows, row_index)
            if resolved is not None:
                return resolved
            if projected is not None:
                return projected.copy()
            raise UnsupportedQueryFeature(f"unknown column {node.sql()}")
        if isinstance(node, (exp.Subquery, exp.Exists)):
            raise UnsupportedQueryFeature("nested_subquery_requires_branch_execution")
        return node

    return _strip_solver_var_casts(expression.copy().transform(transform))


def _resolve_row_column(
    column: exp.Column,
    rows: Sequence[RowBinding],
    row_index: int,
) -> Optional[SolverVar]:
    table = column.args.get("table")
    matches: Dict[str, SolverVar] = {}
    for row in rows:
        if row.row_index != row_index:
            continue
        if table is not None and table not in (row.alias, row.table.this):
            continue
        resolved = row.resolve(column)
        if resolved is not None:
            matches[resolved.var_key] = resolved
    if len(matches) > 1:
        raise UnsupportedQueryFeature(f"ambiguous column {column.sql()}")
    return next(iter(matches.values())) if matches else None


def _strip_solver_var_casts(expression: exp.Expression) -> exp.Expression:
    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Cast):
            inner = _unwrap_cast(node)
            if isinstance(inner, SolverVar):
                return inner.copy()
        return node

    return expression.transform(transform)


def _projection_column_candidates(
    column: exp.Column,
    *,
    dialect: str,
) -> Tuple[exp.Expression, ...]:
    candidates: List[exp.Expression] = [column.copy()]
    if column.table:
        candidates.append(exp.column(column.name))
    parsed = _parse_display_expression(column.name, dialect=dialect)
    if parsed is not None:
        candidates.append(parsed)
    return tuple(candidates)


def _parse_display_expression(
    text: str,
    *,
    dialect: str,
) -> Optional[exp.Expression]:
    try:
        parsed = sqlglot.parse_one(f"SELECT {text}", read=dialect)
    except Exception:
        return None
    if isinstance(parsed, exp.Select) and len(parsed.expressions) == 1:
        return parsed.expressions[0]
    return None


def _group_column_name(group_expr: exp.Expression) -> Optional[str]:
    if isinstance(group_expr, exp.Alias):
        return group_expr.alias_or_name
    output_name = group_expr.meta.get("datafusion_name")
    if output_name:
        return str(output_name)
    if isinstance(group_expr, exp.Column):
        return group_expr.name
    return None


def _scalar_subquery_is_single_row(root: Projection) -> bool:
    step: Step = _single_dependency(root)
    if isinstance(step, Sort) and step.fetch == 1:
        return True
    if isinstance(step, Limit) and step.fetch == 1 and (step.offset or 0) == 0:
        return True
    return False


def _rewrite_ratio_threshold(expr: exp.Expression) -> exp.Expression:
    if isinstance(expr, exp.And):
        return exp.and_(
            _rewrite_ratio_threshold(expr.this),
            _rewrite_ratio_threshold(expr.expression),
        )
    if not isinstance(expr, exp.GT):
        return expr
    threshold = _numeric_literal(expr.expression)
    if threshold is None or threshold < 0:
        return expr
    ratio = _unwrap_cast(expr.this)
    if not isinstance(ratio, exp.Div):
        return expr
    numerator = _unwrap_cast(ratio.this)
    denominator = _unwrap_cast(ratio.expression)
    if not isinstance(numerator, SolverVar) or not isinstance(denominator, SolverVar):
        return expr
    minimum = math.floor(threshold) + 1
    return exp.and_(
        exp.EQ(this=denominator.copy(), expression=exp.Literal.number(1)),
        exp.GT(this=numerator.copy(), expression=exp.Literal.number(minimum)),
    )


def _unwrap_cast(expr: exp.Expression) -> exp.Expression:
    while isinstance(expr, exp.Cast):
        expr = expr.this
    return expr


def _numeric_literal(expr: exp.Expression) -> Optional[float]:
    if not isinstance(expr, exp.Literal) or not expr.is_number:
        return None
    try:
        return float(expr.this)
    except (TypeError, ValueError):
        return None


def _join_row_equalities(
    left_key: exp.Expression,
    right_key: exp.Expression,
    left: Branch,
    right: Branch,
) -> List[Tuple[SolverVar, SolverVar]]:
    if not isinstance(left_key, exp.Column) or not isinstance(right_key, exp.Column):
        raise UnsupportedQueryFeature("join_key_not_solvervar")
    left_rows = _rows_matching_column(left.relation.rows, left_key)
    right_rows = _rows_matching_column(right.relation.rows, right_key)
    if not left_rows or not right_rows:
        raise UnsupportedQueryFeature("join_key_not_solvervar")
    equalities: List[Tuple[SolverVar, SolverVar]] = []
    for left_row, right_row in zip(left_rows, right_rows):
        left_var = left_row.resolve(left_key)
        right_var = right_row.resolve(right_key)
        if left_var is None or right_var is None:
            raise UnsupportedQueryFeature("join_key_not_solvervar")
        equalities.append((left_var, right_var))
    return equalities


def _rows_matching_column(rows: Sequence[RowBinding], column: exp.Column) -> List[RowBinding]:
    table = column.args.get("table")
    return [
        row
        for row in rows
        if (
            (table is None or table in (row.alias, row.table.this))
            and row.resolve(column) is not None
        )
    ]


def _column_for_var(
    rows: Sequence[RowBinding], variable: SolverVar
) -> Optional[exp.Identifier]:
    for row in rows:
        for column, bound in row.columns.items():
            if bound == variable:
                return column
    return None


def _assignments_to_create_rows(
    rows: Sequence[RowBinding],
    assignments: Mapping[SolverVar, object],
) -> Dict[exp.Table, List[Dict[exp.Identifier, object]]]:
    payload: Dict[exp.Table, List[Dict[exp.Identifier, object]]] = {}
    seen: set[Tuple[exp.Table, str, str, int]] = set()
    for row in rows:
        owner = (
            row.table,
            row.scope_id,
            row.alias.name if row.alias is not None else "",
            row.row_index,
        )
        if owner in seen:
            continue
        seen.add(owner)
        values = {
            column: assignments[var]
            for column, var in row.columns.items()
            if var in assignments
        }
        payload.setdefault(row.table, []).append(values)
    return payload


def _aggregate_column_name(aggregate: exp.Expression) -> str:
    if isinstance(aggregate, exp.Sum):
        inner = aggregate.this
        if isinstance(inner, exp.Cast):
            inner = inner.this
        if isinstance(inner, exp.Column):
            return f"sum({inner.sql()})"
    if isinstance(aggregate, exp.Count):
        inner = aggregate.this
        if inner is None or isinstance(inner, exp.Star):
            return "count(*)"
        return f"count({inner.sql()})"
    return aggregate.sql()
