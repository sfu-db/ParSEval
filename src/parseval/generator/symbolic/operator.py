from __future__ import annotations

import logging
import random
from copy import deepcopy
from dataclasses import dataclass
from itertools import product
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, TYPE_CHECKING

from sqlglot import exp

from parseval.coercion import CoercionError, coerce_literal_value
from parseval.domain.exceptions import DomainError
from parseval.generator.config import GenerationConfig
from parseval.generator.schema_constraints import (
    _not_null_constraints_for_columns,
    batch_unique_constraints_for_solver_rows,
    literal_for_value as _literal_for_value,
    schema_constraints_for_solver_row,
)
from parseval.plan.context import DerivedSchema, Row
from parseval.plan.rex import Symbol
from parseval.solver.types import Problem, Result, SolverVar
from parseval.generator.budget import GenerationBudget
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Limit,
    Plan,
    Projection,
    ScalarSubqueryRef,
    UnsupportedExpression,
    Sort,
    Step,
    TableScan,
    Union,
    SubqueryAlias,
    Values,
    EmptyRelation,
    Unnest,
    Repartition,
    RecursiveQuery,
    RawStep,
    Distinct,
    Window,
    normalize_join_type,
)
from parseval.plan.rex import Environment, Variable, concrete, concrete_supported
from parseval.generator.coverage import (
    CoverageTreeNode,
    SemanticTarget,
    _case_expressions,
    _is_not_null_filter,
    _step_semantic_targets,
    sql_order_key
)
from parseval.generator.helper import leaf_table_scans, same_identifier
from parseval.generator.symbolic.targets import (
    ordered_dependencies,
    scalar_subquery_targets,
)

if TYPE_CHECKING:
    from parseval.instance import Instance


logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class GroupDemand:
    group_index: int
    row_count: int
    group_key_values: Tuple[Tuple[exp.Expression, object], ...] = ()
    row_predicates: Tuple[exp.Expression, ...] = ()
    row_predicates_by_index: Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...] = ()


@dataclass(frozen=True)
class ExpressionDemand:
    expression: exp.Expression
    kind: str
    value: object | None = None
    rank: int | None = None
    descending: bool = False
    origin: str = ""


@dataclass(frozen=True)
class JoinKeyRef:
    origin: Tuple[exp.Expression, exp.Expression]
    pair_rank: int
    key_index: int


@dataclass(frozen=True)
class SchemaDemand:
    count: int = 1
    predicates: Tuple[exp.Expression, ...] = ()
    order_keys: Tuple[exp.Expression, ...] = ()
    distinct: bool = False
    group_demands: Tuple[GroupDemand, ...] = ()
    expression_demands: Tuple[ExpressionDemand, ...] = ()
    require_scalar_order_ties: bool = True


@dataclass(frozen=True)
class _AtomicRowRequest:
    table: exp.Table
    row_specs: Tuple[Mapping[object, object], ...]
    predicates: Tuple[Tuple[exp.Expression, ...], ...]
    expression_demands: Tuple[ExpressionDemand, ...] = ()


@dataclass(frozen=True)
class DemandContext:
    pipeline: "EncodePipeline"
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]]

    @property
    def config(self) -> object:
        return self.pipeline.config

    @property
    def dialect(self) -> str | None:
        return self.pipeline.dialect

    def lower(self, step: Step, demand: SchemaDemand) -> None:
        self.pipeline._lower_demand(step, demand, self.cache)

    def single_dependency(self, step: Step) -> Step:
        return _single_dependency(step)


def _row_value_dict(row) -> Dict[exp.Identifier, Any]:
    """Extract concrete {col_ident: value} from a Row/Variable row."""
    d: Dict[exp.Identifier, Any] = {}
    for col_ident, val in row.column_values.items():
        if isinstance(val, Variable):
            d[col_ident] = val.concrete
        else:
            d[col_ident] = val
    return d


def _step_name(step: Step) -> str:
    return step.name.name if step.name else type(step).__name__

def _row_value(row: Mapping[object, object], column: exp.Identifier) -> object:
    if hasattr(row, "column_values"):
        value = row[column]
        return value.concrete if isinstance(value, Variable) else value
    if column in row:
        value = row[column]
    else:
        value = next(
            (
                candidate
                for key, candidate in row.items()
                if (
                    key.name
                    if isinstance(key, (exp.Identifier, exp.Column))
                    else str(key)
                ).casefold()
                == column.name.casefold()
            ),
            None,
        )
    return value.concrete if isinstance(value, Variable) else value


def _solved_expression_value(
    row: Mapping[object, object],
    expression: exp.Expression,
) -> object:
    while isinstance(expression, exp.Cast):
        expression = expression.this
    if isinstance(expression, exp.Column) and isinstance(expression.this, exp.Identifier):
        return _row_value(row, expression.this)
    return _MISSING


def _solver_var_for_column(
    table: exp.Table,
    column: exp.Identifier,
    row_index: int,
    dtype: Any,
) -> SolverVar:
    return SolverVar(
        key=f"gen.{table.name}.{column.name}.{row_index}",
        dtype=dtype,
        meta={"table": table.name, "column": column.name, "row_index": row_index},
    )


def _rewrite_columns_to_solver_vars(
    expression: exp.Expression,
    sv_map: Mapping[str, SolverVar],
) -> exp.Expression:
    if isinstance(expression, exp.Column) and isinstance(expression.this, exp.Identifier):
        replacement = sv_map.get(expression.this.name)
        if replacement is not None:
            return replacement
    rewritten = deepcopy(expression)
    for col in list(rewritten.find_all(exp.Column)):
        if isinstance(col.this, exp.Identifier) and col.this.name in sv_map:
            col.replace(sv_map[col.this.name])
    return rewritten


def _typed_predicate_for_table(
    instance: Instance,
    table: exp.Table,
    predicate: exp.Expression,
) -> exp.Expression:
    """Coerce comparison literals to the declared type of their base column."""
    rewritten = deepcopy(predicate)
    comparisons = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
    column_names = {
        column.name.casefold(): column.name
        for column in instance.database_constraints(table).columns
    }
    nodes = (rewritten, *rewritten.find_all(*comparisons))
    for node in nodes:
        if not isinstance(node, comparisons):
            continue
        left, right = node.this, node.expression
        column: exp.Column | None = None
        literal: exp.Literal | None = None
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            column, literal = left, right
        elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            column, literal = right, left
        if column is None or literal is None:
            continue
        declared_name = column_names.get(column.name.casefold())
        if declared_name is None:
            continue
        value = _coerce_value_for_column(
            instance,
            table,
            declared_name,
            _literal_value(literal),
        )
        literal.replace(_literal_for_value(value))
    return rewritten


def _normalized_request_specs(
    instance: Instance,
    table: exp.Table,
    row_specs: Sequence[Mapping[object, object]],
    predicates: Sequence[Sequence[exp.Expression]],
    expression_demands: Sequence[ExpressionDemand],
    correlated_bindings: Mapping[JoinKeyRef, object],
) -> Tuple[Mapping[object, object], ...]:
    """Normalize exact row values and direct predicate equalities before anchoring."""
    table_schema = instance.database_constraints(table)
    columns_by_name = {
        column.name.casefold(): column for column in table_schema.columns
    }
    normalized: List[Mapping[object, object]] = []
    for spec, row_predicates in zip(row_specs, predicates):
        row: Dict[object, object] = {}
        for key, value in spec.items():
            name = key.name if isinstance(key, exp.Expression) else str(key)
            column = columns_by_name.get(name.casefold())
            if column is None:
                row[key] = value
                continue
            row[column] = _coerce_value_for_column(
                instance,
                table,
                column.name,
                value,
            )
        for predicate in row_predicates:
            for atom in _conjuncts(predicate):
                if not isinstance(atom, exp.EQ):
                    continue
                left, right = atom.this, atom.expression
                if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                    column, literal = left, right
                elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                    column, literal = right, left
                else:
                    continue
                declared = columns_by_name.get(column.name.casefold())
                if declared is None or declared in row:
                    continue
                row[declared] = _coerce_value_for_column(
                    instance,
                    table,
                    declared.name,
                    _literal_value(literal),
                )
        row_rank = len(normalized)
        for demand in expression_demands:
            if demand.rank != row_rank:
                continue
            expression = demand.expression
            if isinstance(expression, (exp.Alias, exp.Ordered)):
                expression = expression.this
            if not isinstance(expression, exp.Column):
                continue
            value = demand.value
            if demand.kind == "correlated" and isinstance(value, JoinKeyRef):
                if value not in correlated_bindings:
                    continue
                value = correlated_bindings[value]
            elif demand.kind != "literal" or value is None:
                continue
            declared = columns_by_name.get(expression.name.casefold())
            if declared is None or declared in row:
                continue
            row[declared] = _coerce_value_for_column(
                instance,
                table,
                declared.name,
                value,
            )
        normalized.append(row)
    return tuple(normalized)


def _schema_constraints_for_solver_rows(
    instance: Instance,
    table: exp.Table,
    sv_rows: Sequence[Mapping[str, SolverVar]],
    exact_columns_by_row: Sequence[Set[str]],
    unconstrained_fk_columns_by_row: Sequence[Set[str]] = (),
    exact_unique_values_validated: bool = False,
    include_existing_fks: bool = True,
) -> List[exp.Expression]:
    table_schema = instance.database_constraints(table)
    constraints: List[exp.Expression] = []
    required_non_null_by_row: List[Set[str]] = [set() for _ in sv_rows]
    if not unconstrained_fk_columns_by_row:
        unconstrained_fk_columns_by_row = tuple(set() for _ in sv_rows)
    for sv_map, exact_columns, unconstrained_fk_columns in zip(
        sv_rows,
        exact_columns_by_row,
        unconstrained_fk_columns_by_row,
    ):
        constraints.extend(
            schema_constraints_for_solver_row(
                instance,
                table,
                sv_map,
                exact_columns=exact_columns,
                unconstrained_fk_columns=unconstrained_fk_columns,
                include_existing_fks=include_existing_fks,
            )
        )

    for group in table_schema.uniqueness_groups():
        names = tuple(column.name for column in group)
        if any(not set(names) <= set(sv_map) for sv_map in sv_rows):
            continue
        for left_index, _left in enumerate(sv_rows):
            for right_index, _right in enumerate(sv_rows[left_index + 1 :], start=left_index + 1):
                required_non_null_by_row[left_index].update(names)
                required_non_null_by_row[right_index].update(names)
    if not exact_unique_values_validated:
        constraints.extend(batch_unique_constraints_for_solver_rows(instance, table, sv_rows))
    else:
        for group in table_schema.uniqueness_groups():
            names = {column.name for column in group}
            if all(names <= exact for exact in exact_columns_by_row):
                continue
            constraints.extend(
                batch_unique_constraints_for_solver_rows(instance, table, sv_rows)
            )
            break
    for sv_map, column_names in zip(sv_rows, required_non_null_by_row):
        constraints.extend(
            _not_null_constraints_for_columns(
                table_schema,
                sv_map,
                column_names,
            )
        )
    return constraints


def _expression_demand_batch_constraints(
    expression_demands: Sequence[ExpressionDemand],
    sv_rows: Sequence[Mapping[str, SolverVar]],
    dialect: str | None = None,
    correlated_bindings: Mapping[JoinKeyRef, object] | None = None,
) -> List[exp.Expression]:
    del dialect
    correlated_bindings = correlated_bindings or {}
    constraints: List[exp.Expression] = []
    distinct_by_origin: Dict[str, Dict[int, exp.Expression]] = {}
    equal_by_origin: Dict[Tuple[str, object | None], Dict[int, exp.Expression]] = {}
    order_by_origin: Dict[str, Dict[int, Tuple[exp.Expression, object | None]]] = {}
    for demand in expression_demands:
        if demand.kind not in {"distinct", "order", "equal", "literal", "null", "not_null", "correlated"} or demand.rank is None:
            continue
        if demand.rank < 0 or demand.rank >= len(sv_rows):
            continue
        expression = demand.expression
        if isinstance(expression, exp.Alias):
            expression = expression.this
        if isinstance(expression, exp.Ordered):
            expression = expression.this
        if demand.kind == "order" and not isinstance(expression, exp.Column):
            continue
        expression = _rewrite_columns_to_solver_vars(expression, sv_rows[demand.rank])
        origin = demand.origin or _normalize_expression_key(demand.expression.sql())
        if demand.kind == "distinct":
            distinct_by_origin.setdefault(origin, {})[demand.rank] = expression
        elif demand.kind == "equal":
            equal_by_origin.setdefault((origin, demand.value), {})[demand.rank] = expression
        elif demand.kind == "correlated":
            if isinstance(demand.value, JoinKeyRef) and demand.value in correlated_bindings:
                constraints.append(
                    exp.EQ(
                        this=expression,
                        expression=_literal_for_value(correlated_bindings[demand.value]),
                    )
                )
            else:
                constraints.append(
                    exp.Not(
                        this=exp.Is(
                            this=expression,
                            expression=exp.Null(),
                        )
                    )
                )
        elif demand.kind == "literal":
            if demand.value is not None:
                constraints.append(
                    exp.EQ(
                        this=expression,
                        expression=_literal_for_value(demand.value),
                    )
                )
        elif demand.kind == "null":
            constraints.append(exp.Is(this=expression, expression=exp.Null()))
        elif demand.kind == "not_null":
            constraints.append(
                exp.Not(this=exp.Is(this=expression, expression=exp.Null()))
            )
        else:
            order_by_origin.setdefault(origin, {})[demand.rank] = (
                expression,
                demand.value,
            )
    for rank_to_expression in distinct_by_origin.values():
        ranked = sorted(rank_to_expression.items())
        for left_index, (left_rank, left_expression) in enumerate(ranked):
            for _right_rank, right_expression in ranked[left_index + 1 :]:
                constraints.append(exp.NEQ(this=left_expression, expression=right_expression))
    for rank_to_expression in equal_by_origin.values():
        ranked = sorted(rank_to_expression.items())
        for left_index, (_left_rank, left_expression) in enumerate(ranked):
            for _right_rank, right_expression in ranked[left_index + 1 :]:
                constraints.append(exp.EQ(this=left_expression, expression=right_expression))
    for rank_to_expression in order_by_origin.values():
        ranked = sorted(rank_to_expression.items())
        for left_index, (_left_rank, (left_expression, left_value)) in enumerate(ranked):
            for _right_rank, (right_expression, right_value) in ranked[left_index + 1 :]:
                if left_value == right_value:
                    constraints.append(exp.EQ(this=left_expression, expression=right_expression))
                elif _order_value_before(left_value, right_value):
                    constraints.append(exp.GT(this=left_expression, expression=right_expression))
                else:
                    constraints.append(exp.LT(this=left_expression, expression=right_expression))
    return constraints


def _order_value_before(left: object | None, right: object | None) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    try:
        return left > right
    except TypeError:
        return str(left) > str(right)


def _solve_atomic_row_requests(
    instance: Instance,
    requests: Sequence[_AtomicRowRequest],
    *,
    dialect: str | None,
    budget: GenerationBudget,
    correlated_bindings: Mapping[JoinKeyRef, object] | None = None,
) -> tuple[
    Result,
    Tuple[Tuple[Mapping[str, object], ...], ...],
    Problem,
]:
    constraints: List[exp.Expression] = []
    variables: Set[SolverVar] = set()
    compiled: List[Tuple[_AtomicRowRequest, List[Dict[str, SolverVar]]]] = []
    rows_by_table: Dict[exp.Table, List[Dict[str, SolverVar]]] = {}
    request_counts_by_table: Dict[exp.Table, int] = {}
    specs_by_table: Dict[exp.Table, List[Mapping[object, object]]] = {}
    correlated_expressions: Dict[JoinKeyRef, List[exp.Expression]] = {}
    equal_expressions: Dict[Tuple[str, str], List[exp.Expression]] = {}
    relation_expressions: Dict[str, List[Tuple[str, str, exp.Expression]]] = {}
    correlated_ref_counts: Dict[JoinKeyRef, int] = {}
    for request in requests:
        for demand in request.expression_demands:
            if demand.kind == "correlated" and isinstance(demand.value, JoinKeyRef):
                correlated_ref_counts[demand.value] = correlated_ref_counts.get(demand.value, 0) + 1
    bindings = {
        ref: value
        for ref, value in (correlated_bindings or {}).items()
        if correlated_ref_counts.get(ref, 0) < 2
    }
    next_index = {
        table: len(instance.get_rows(table))
        for table in instance.schema.fk_safe_table_order()
    }

    for request in requests:
        table = instance.resolve_table(request.table)
        table_schema = instance.database_constraints(table)
        unsupported_check = next(
            (check for check in table_schema.checks if not check.supported),
            None,
        )
        if unsupported_check is not None:
            reason = (
                f"unsupported_check_constraint:{table.name}:"
                f"{unsupported_check.reason or 'unknown'}"
            )
            problem = Problem(constraints=constraints, variables=variables)
            return Result(status="unknown", reason=reason), (), problem
        try:
            normalized_specs = _normalized_request_specs(
                instance,
                table,
                request.row_specs,
                request.predicates,
                request.expression_demands,
                bindings,
            )
            typed_predicates = tuple(
                tuple(
                    _typed_predicate_for_table(instance, table, predicate)
                    for predicate in row_predicates
                )
                for row_predicates in request.predicates
            )
            typed_expression_demands: List[ExpressionDemand] = []
            column_names = {
                column.name.casefold(): column.name for column in table_schema.columns
            }
            for demand in request.expression_demands:
                value = demand.value
                expression = demand.expression
                unwrapped = expression.this if isinstance(expression, (exp.Alias, exp.Ordered)) else expression
                if (
                    demand.kind == "literal"
                    and value is not None
                    and isinstance(unwrapped, exp.Column)
                    and unwrapped.name.casefold() in column_names
                ):
                    declared_name = column_names[unwrapped.name.casefold()]
                    value = _coerce_value_for_column(
                        instance,
                        table,
                        declared_name,
                        value,
                    )
                typed_expression_demands.append(
                    ExpressionDemand(
                        expression=expression,
                        kind=demand.kind,
                        value=value,
                        rank=demand.rank,
                        descending=demand.descending,
                        origin=demand.origin,
                    )
                )
        except CoercionError as exc:
            problem = Problem(constraints=constraints, variables=variables)
            return (
                Result(
                    status="unknown",
                    reason=f"unsupported_literal_coercion:{table.name}:{exc}",
                ),
                (),
                problem,
            )
        normalized_specs, typed_predicates, typed_expression_demands_tuple = (
            _without_reusable_existing_specs(
                instance,
                table,
                normalized_specs,
                typed_predicates,
                typed_expression_demands,
                bindings,
            )
        )
        typed_expression_demands = list(typed_expression_demands_tuple)
        request = _AtomicRowRequest(
            table=table,
            row_specs=_unique_anchored_row_specs(instance, table, normalized_specs),
            predicates=typed_predicates,
            expression_demands=tuple(typed_expression_demands),
        )
        exact_uniques_validated = _exact_unique_groups_are_distinct(
            table_schema,
            request.row_specs,
        )
        sv_rows: List[Dict[str, SolverVar]] = []
        exact_columns_by_row: List[Set[str]] = []
        for offset, row in enumerate(request.row_specs):
            sv_map = {
                column.name: _solver_var_for_column(
                    table,
                    column,
                    next_index[table] + offset,
                    column_schema.datatype,
                )
                for column, column_schema in table_schema.columns.items()
            }
            sv_rows.append(sv_map)
            exact_columns: Set[str] = set()
            for column in table_schema.columns:
                if column not in row and column.name not in row:
                    continue
                exact_columns.add(column.name)
                value = _row_value(row, column)
                constraints.append(
                    exp.Is(this=sv_map[column.name], expression=exp.Null())
                    if value is None
                    else exp.EQ(
                        this=sv_map[column.name],
                        expression=_literal_for_value(value),
                    )
                )
            for predicate in request.predicates[offset]:
                constraints.append(_rewrite_columns_to_solver_vars(predicate, sv_map))
            exact_columns_by_row.append(exact_columns)
        next_index[table] += len(sv_rows)
        constraints.extend(
            _schema_constraints_for_solver_rows(
                instance,
                table,
                sv_rows,
                exact_columns_by_row,
                exact_unique_values_validated=exact_uniques_validated,
                include_existing_fks=False,
            )
        )
        constraints.extend(
            _expression_demand_batch_constraints(
                request.expression_demands,
                sv_rows,
                dialect,
                bindings,
            )
        )
        for demand in request.expression_demands:
            if (
                demand.kind != "correlated"
                or not isinstance(demand.value, JoinKeyRef)
                or demand.rank is None
                or demand.rank < 0
                or demand.rank >= len(sv_rows)
            ):
                continue
            expression = demand.expression
            if isinstance(expression, (exp.Alias, exp.Ordered)):
                expression = expression.this
            correlated_expressions.setdefault(demand.value, []).append(
                _rewrite_columns_to_solver_vars(expression, sv_rows[demand.rank])
            )
        for demand in request.expression_demands:
            if (
                demand.kind != "equal"
                or demand.rank is None
                or demand.rank < 0
                or demand.rank >= len(sv_rows)
            ):
                continue
            expression = demand.expression
            if isinstance(expression, (exp.Alias, exp.Ordered)):
                expression = expression.this
            key = (
                demand.origin or _normalize_expression_key(demand.expression.sql()),
                repr(demand.value),
            )
            equal_expressions.setdefault(key, []).append(
                _rewrite_columns_to_solver_vars(expression, sv_rows[demand.rank])
            )
        for demand in request.expression_demands:
            if (
                demand.kind != "relation"
                or demand.rank is None
                or demand.rank < 0
                or demand.rank >= len(sv_rows)
                or not isinstance(demand.value, str)
                or ":" not in demand.value
            ):
                continue
            operator, side = demand.value.split(":", 1)
            expression = demand.expression
            if isinstance(expression, (exp.Alias, exp.Ordered)):
                expression = expression.this
            relation_expressions.setdefault(demand.origin, []).append(
                (
                    operator,
                    side,
                    _rewrite_columns_to_solver_vars(expression, sv_rows[demand.rank]),
                )
            )
        variables.update(var for row in sv_rows for var in row.values())
        rows_by_table.setdefault(table, []).extend(sv_rows)
        request_counts_by_table[table] = request_counts_by_table.get(table, 0) + 1
        specs_by_table.setdefault(table, []).extend(request.row_specs)
        compiled.append((request, sv_rows))

    for expressions in correlated_expressions.values():
        if len(expressions) < 2:
            continue
        anchor = expressions[0]
        constraints.extend(
            exp.EQ(this=anchor, expression=other)
            for other in expressions[1:]
        )
    for expressions in equal_expressions.values():
        if len(expressions) < 2:
            continue
        anchor = expressions[0]
        constraints.extend(
            exp.EQ(this=anchor, expression=other)
            for other in expressions[1:]
        )
    relation_types = {
        "eq": exp.EQ,
        "neq": exp.NEQ,
        "gt": exp.GT,
        "gte": exp.GTE,
        "lt": exp.LT,
        "lte": exp.LTE,
    }
    for expressions in relation_expressions.values():
        left = next((item for item in expressions if item[1] == "left"), None)
        right = next((item for item in expressions if item[1] == "right"), None)
        if left is None or right is None or left[0] != right[0]:
            continue
        relation_type = relation_types.get(left[0])
        if relation_type is not None:
            constraints.append(relation_type(this=left[2], expression=right[2]))
    for table, rows in rows_by_table.items():
        if request_counts_by_table.get(table, 0) > 1:
            constraints.extend(
                batch_unique_constraints_for_solver_rows(instance, table, rows)
            )
    constraints.extend(_atomic_foreign_key_constraints(instance, rows_by_table, specs_by_table))
    problem = Problem(constraints=constraints, variables=variables)
    result = budget.solve(problem, dialect=dialect or instance.dialect)
    if result.status != "sat":
        return result, (), problem

    decoded: List[Tuple[Mapping[str, object], ...]] = []
    for request, sv_rows in compiled:
        table_schema = instance.database_constraints(request.table)
        request_rows: List[Mapping[str, object]] = []
        for spec, sv_map in zip(request.row_specs, sv_rows):
            solved: Dict[str, object] = {}
            for column in table_schema.columns:
                if column in spec or column.name in spec:
                    solved[column.name] = _row_value(spec, column)
                elif sv_map[column.name] in result.assignments:
                    solved[column.name] = result.assignments[sv_map[column.name]]
            request_rows.append(solved)
        decoded.append(tuple(request_rows))
    return result, tuple(decoded), problem


def _unique_anchored_row_specs(
    instance: Instance,
    table: exp.Table,
    row_specs: Sequence[Mapping[object, object]],
) -> Tuple[Mapping[object, object], ...]:
    table_schema = instance.database_constraints(table)
    anchored = [dict(row) for row in row_specs]
    existing = [
        {
            column: _row_value(_row_value_dict(row), column)
            for column in table_schema.columns
        }
        for row in instance.get_rows(table)
    ]
    generated: List[Mapping[object, object]] = []
    for row in anchored:
        for group in table_schema.uniqueness_groups():
            if len(group) != 1:
                continue
            column = group[0]
            if column in row or column.name in row:
                continue
            row[column] = instance._domain.next_value(
                table,
                column,
                existing_rows=existing + generated,
            )
        generated.append(row)
    return tuple(anchored)


def _without_reusable_existing_specs(
    instance: Instance,
    table: exp.Table,
    row_specs: Sequence[Mapping[object, object]],
    predicates: Sequence[Sequence[exp.Expression]],
    expression_demands: Sequence[ExpressionDemand],
    correlated_bindings: Mapping[JoinKeyRef, object],
) -> tuple[
    Tuple[Mapping[object, object], ...],
    Tuple[Tuple[exp.Expression, ...], ...],
    Tuple[ExpressionDemand, ...],
]:
    table_schema = instance.database_constraints(table)
    existing = [_row_value_dict(row) for row in instance.get_rows(table)]
    demands_by_rank: Dict[int, List[ExpressionDemand]] = {}
    for demand in expression_demands:
        if demand.rank is not None:
            demands_by_rank.setdefault(demand.rank, []).append(demand)

    kept: List[int] = []
    for rank, (spec, row_predicates) in enumerate(zip(row_specs, predicates)):
        rank_demands = demands_by_rank.get(rank, ())
        relational = any(
            demand.kind in {"equal", "distinct", "order", "null", "not_null"}
            or (
                demand.kind == "correlated"
                and isinstance(demand.value, JoinKeyRef)
                and demand.value not in correlated_bindings
            )
            for demand in rank_demands
        )
        if relational or not any(
            all(column in spec or column.name in spec for column in group)
            for group in table_schema.uniqueness_groups()
        ):
            kept.append(rank)
            continue
        reusable = any(
            all(
                _row_value(row, column) == _row_value(spec, column)
                for group in table_schema.uniqueness_groups()
                if all(column in spec or column.name in spec for column in group)
                for column in group
            )
            and all(
                concrete_supported(predicate)
                and concrete(predicate, Environment.from_row(row)) is True
                for predicate in row_predicates
            )
            for row in existing
        )
        if not reusable:
            kept.append(rank)

    rank_map = {old: new for new, old in enumerate(kept)}
    remapped_demands = tuple(
        ExpressionDemand(
            expression=demand.expression,
            kind=demand.kind,
            value=demand.value,
            rank=rank_map[demand.rank],
            descending=demand.descending,
            origin=demand.origin,
        )
        if demand.rank is not None and demand.rank in rank_map
        else demand
        for demand in expression_demands
        if demand.rank is None or demand.rank in rank_map
    )
    return (
        tuple(row_specs[index] for index in kept),
        tuple(tuple(predicates[index]) for index in kept),
        remapped_demands,
    )


def _with_required_parent_requests(
    instance: Instance,
    requests: Sequence[_AtomicRowRequest],
) -> Tuple[_AtomicRowRequest, ...]:
    expanded: List[_AtomicRowRequest] = list(requests)
    requested_tables = {instance.resolve_table(request.table) for request in expanded}
    additions: Dict[exp.Table, List[Mapping[object, object]]] = {}
    for request in requests:
        table = instance.resolve_table(request.table)
        table_schema = instance.database_constraints(table)
        for foreign_key in table_schema.foreign_keys:
            parent = instance.resolve_table(foreign_key.target_table)
            if parent in requested_tables:
                continue
            existing_parent_values = {
                tuple(_row_value_dict(row).get(column) for column in foreign_key.target_columns)
                for row in instance.get_rows(parent)
            }
            for row in request.row_specs:
                exact = (
                    tuple(_row_value(row, column) for column in foreign_key.source_columns)
                    if all(
                        column in row or column.name in row
                        for column in foreign_key.source_columns
                    )
                    else None
                )
                if exact is not None and any(value is None for value in exact):
                    continue
                if exact is not None and exact in existing_parent_values:
                    continue
                parent_row = (
                    {
                        target: value
                        for target, value in zip(foreign_key.target_columns, exact)
                    }
                    if exact is not None
                    else {}
                )
                additions.setdefault(parent, []).append(parent_row)
    if not additions:
        return tuple(expanded)
    parent_requests = [
        _AtomicRowRequest(
            table=table,
            row_specs=tuple(rows),
            predicates=tuple(() for _ in rows),
        )
        for table, rows in additions.items()
    ]
    return tuple(parent_requests + expanded)


def _exact_unique_groups_are_distinct(
    table_schema: Any,
    row_specs: Sequence[Mapping[object, object]],
) -> bool:
    for group in table_schema.uniqueness_groups():
        values = []
        for row in row_specs:
            if any(column not in row and column.name not in row for column in group):
                return False
            values.append(tuple(_row_value(row, column) for column in group))
        if len(set(values)) != len(values):
            return False
    return True


def _atomic_foreign_key_constraints(
    instance: Instance,
    rows_by_table: Mapping[exp.Table, Sequence[Mapping[str, SolverVar]]],
    specs_by_table: Mapping[exp.Table, Sequence[Mapping[object, object]]],
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for table, child_rows in rows_by_table.items():
        table_schema = instance.database_constraints(table)
        for foreign_key in table_schema.foreign_keys:
            parent_table = instance.resolve_table(foreign_key.target_table)
            parent_rows = rows_by_table.get(parent_table, ())
            child_specs = specs_by_table.get(table, ())
            parent_specs = specs_by_table.get(parent_table, ())
            for child_index, child in enumerate(child_rows):
                source_names = tuple(column.name for column in foreign_key.source_columns)
                target_names = tuple(column.name for column in foreign_key.target_columns)
                if not set(source_names) <= set(child):
                    continue
                child_spec = child_specs[child_index]
                exact_source = (
                    tuple(_row_value(child_spec, column) for column in foreign_key.source_columns)
                    if all(
                        column in child_spec or column.name in child_spec
                        for column in foreign_key.source_columns
                    )
                    else None
                )
                exact_parents = {
                    tuple(_row_value(spec, column) for column in foreign_key.target_columns)
                    for spec in parent_specs
                    if all(
                        column in spec or column.name in spec
                        for column in foreign_key.target_columns
                    )
                }
                exact_parents.update(
                    tuple(_row_value_dict(row).get(column) for column in foreign_key.target_columns)
                    for row in instance.get_rows(parent_table)
                )
                if exact_source is not None and exact_source in exact_parents:
                    continue
                choices: List[exp.Expression] = []
                for parent in parent_rows:
                    if set(target_names) <= set(parent):
                        choices.append(
                            _and_all(
                                tuple(
                                    exp.EQ(this=child[source], expression=parent[target])
                                    for source, target in zip(source_names, target_names)
                                )
                            )
                        )
                for existing in instance.get_rows(parent_table):
                    values = _row_value_dict(existing)
                    if all(values.get(column) is not None for column in foreign_key.target_columns):
                        choices.append(
                            _and_all(
                                tuple(
                                    exp.EQ(
                                        this=child[source],
                                        expression=_literal_for_value(values[target_column]),
                                    )
                                    for source, target_column in zip(
                                        source_names,
                                        foreign_key.target_columns,
                                    )
                                )
                            )
                        )
                nullable = all(
                    table_schema.columns[column].nullable
                    for column in foreign_key.source_columns
                )
                if nullable:
                    choices.append(
                        _and_all(
                            tuple(
                                exp.Is(this=child[source], expression=exp.Null())
                                for source in source_names
                            )
                        )
                    )
                if choices:
                    constraints.append(_or_all(choices))
    return constraints

# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------

class EncodeStep:
    """Base class for a single-step concrete-enrichment operator.

    Each operator corresponds to one :class:`Step` in the plan DAG and
    implements :meth:`forward` to ensure the :class:`Instance` has rows
    that cover the operator's semantics (both passing and failing).
    """

    def __init__(self, step: Step, instance: Optional[Instance] = None) -> None:
        self.step = step
        self.instance = instance
        self.dialect = getattr(instance, "dialect", None)

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        raise NotImplementedError

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        dependencies = tuple(self.step.dependencies)
        if len(dependencies) == 1:
            context.lower(dependencies[0], demand)
            return
        table = getattr(output_schema, "_table", None)
        if table is not None:
            context.pipeline._materialize_table_demand(output_schema, table, demand)

    def semantic_targets(self, path: str) -> Tuple[SemanticTarget, ...]:
        return _step_semantic_targets(self.step, self.instance, path)

# ------------------------------------------------------------------
# Scan
# ------------------------------------------------------------------

class ScanEncodeStep(EncodeStep):
    """Load concrete rows from :class:`Instance`."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        scan: TableScan = self.step
        table = scan.table
        col_keys: List[exp.Column] = [
            p for p in scan.scan_projections if isinstance(p, exp.Column)
        ]
        rows: List[Row] = []        
        for existing in self.instance.get_rows(table):
            selected = {}
            for col_key in col_keys:
                if isinstance(col_key.this, exp.Identifier):
                    val = existing[col_key.this]
                    selected[col_key] = val
            if selected:
                rows.append(Row(this=(table.name, existing.rowid), columns=selected))

        datatypes = {}
        nullables = {}
        uniqueness = {}
        for col_key in col_keys:
            datatypes[col_key] = self.instance.get_column_type(table, col_key)
            nullables[col_key] = self.instance.nullable(table, col_key)
            uniqueness[col_key] = self.instance.is_unique(table, col_key)

        ds = DerivedSchema(
            columns=tuple(col_keys),
            rows=rows,
            datatypes=datatypes,
            nullables=nullables,
            uniqueness=uniqueness,
            row_provenance={
                row.rowid: {table.name: (existing.rowid,)}
                for row, existing in zip(rows, self.instance.get_rows(table))
            },
        )
        ds._table = table
        return ds

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del child_schemas
        table = getattr(output_schema, "_table", None)
        if table is not None:
            context.pipeline._materialize_table_demand(output_schema, table, demand)


# ------------------------------------------------------------------
# Filter
# ------------------------------------------------------------------


_MISSING = object()
_UNSUPPORTED_EVALUATION = object()


def _scalar_schema_value(schema: DerivedSchema) -> object:
    if not schema.rows:
        return _MISSING
    row = schema.rows[0]
    if not row.column_values:
        return _MISSING
    value = next(iter(row.column_values.values()))
    if isinstance(value, (Symbol, Variable)):
        value = value.concrete
    return value


def _scalar_schema_ready_for_predicate(
    schema: DerivedSchema,
    parent_expr: exp.Expression,
) -> bool:
    value = _scalar_schema_value(schema)
    if value is _MISSING:
        return False
    if value is not None:
        return True
    return not isinstance(parent_expr, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE))


def _literal_for_scalar_schema(schema: DerivedSchema) -> exp.Expression:
    value = _scalar_schema_value(schema)
    return _literal_for_value(None if value is _MISSING else value)


def _scalar_ref_parent_expr(
    condition: exp.Expression,
    ref: ScalarSubqueryRef,
) -> exp.Expression:
    parent = ref.parent
    while parent is not None:
        if isinstance(parent, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return parent
        parent = parent.parent
    return condition


def _expression_with_scalar_subqueries(
    expression: exp.Expression,
    subquery_schemas: Sequence[DerivedSchema],
    *,
    require_ready: bool = False,
) -> exp.Expression:
    if not subquery_schemas:
        return expression
    rewritten = deepcopy(expression)
    for ref, schema in zip(list(rewritten.find_all(ScalarSubqueryRef)), subquery_schemas):
        parent_expr = _scalar_ref_parent_expr(rewritten, ref)
        if require_ready and not _scalar_schema_ready_for_predicate(schema, parent_expr):
            return expression
        ref.replace(_literal_for_scalar_schema(schema))
    return rewritten


class FilterEncodeStep(EncodeStep):
    """Evaluate a filter over the child DerivedSchema."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        fs: Filter = self.step
        if fs.condition is None:
            return child

        condition = _expression_with_scalar_subqueries(fs.condition, children[1:])
        kept_rows: List[Row] = []
        for row in child.rows:
            env = Environment.from_row(row)
            if concrete(condition, env) is True:
                kept_rows.append(row)

        result = child.with_rows(kept_rows)
        result._table = getattr(child, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema
        fs: Filter = self.step
        child = context.single_dependency(fs)
        child_schema = child_schemas[0]
        if fs.condition is None:
            context.lower(child, demand)
            return
        if isinstance(child, Aggregate):
            context.pipeline._materialize_having_demand(fs, child, demand, context.cache)
            return
        if not context.pipeline._ensure_scalar_expression_values(
            fs.condition,
            context.cache,
            require_order_ties=demand.require_scalar_order_ties,
        ):
            return
        conditions = context.pipeline._conditions_with_scalar_subquery_values(
            fs.condition,
            context.cache,
        )
        if not conditions:
            return
        for condition in conditions:
            if (
                not demand.order_keys
                and not demand.distinct
                and not demand.group_demands
                and not demand.expression_demands
            ):
                required = max(demand.count, 1)
                predicate = _and_all(demand.predicates + (condition,))
                matching = sum(
                    concrete(predicate, Environment.from_row(row)) is True
                    for row in child_schema.rows
                )
                if matching >= required:
                    continue
            context.lower(
                child,
                SchemaDemand(
                    count=demand.count,
                    predicates=demand.predicates + (condition,),
                    order_keys=demand.order_keys,
                    distinct=demand.distinct,
                    group_demands=demand.group_demands,
                    expression_demands=demand.expression_demands,
                    require_scalar_order_ties=demand.require_scalar_order_ties,
                ),
            )

# ------------------------------------------------------------------
# Projection
# ------------------------------------------------------------------

class ProjectEncodeStep(EncodeStep):
    """Evaluate projection expressions over child DerivedSchema rows."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        proj_step: Projection = self.step

        projection_items = _projection_items_with_scalar_subqueries(
            proj_step.projections,
            children[1:],
            self.dialect,
        )

        new_rows: List[Row] = []
        for row in child.rows:
            new_columns = {}
            for output, expression in projection_items:
                new_columns[output] = _expr_value(expression, row)
            new_rows.append(Row(this=row.rowid, columns=new_columns))

        out_cols = tuple(output for output, _expression in projection_items)
        result = child.with_rows(new_rows, columns=out_cols)
        result.datatypes = {
            output: dtype
            for output, expression in projection_items
            if (dtype := _schema_column_metadata(child.datatypes, expression, self.dialect)) is not None
        }
        result.nullables = {
            output: nullable
            for output, expression in projection_items
            if (nullable := _schema_column_metadata(child.nullables, expression, self.dialect)) is not None
        }
        result.uniqueness = {
            output: unique
            for output, expression in projection_items
            if (unique := _schema_column_metadata(child.uniqueness, expression, self.dialect)) is not None
        }
        result._table = getattr(child, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        child = context.single_dependency(self.step)
        context.lower(
            child,
            _rewrite_projection_demand(
                demand,
                self.step,
                output_schema,
                child_schemas[0],
                context.dialect,
            ),
        )


# ------------------------------------------------------------------
# Join
# ------------------------------------------------------------------

class JoinEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        join_step: Join = self.step
        left_ds = children[0]
        right_ds = children[1]
        result = self._build_join_output(left_ds, right_ds, join_step)
        result._table = getattr(left_ds, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        context.pipeline._materialize_join_demand(self.step, demand, context.cache)

    @staticmethod
    def _build_join_output(
        left_ds: DerivedSchema,
        right_ds: DerivedSchema,
        join_step: Join,
    ) -> DerivedSchema:
        jt = normalize_join_type(join_step.join_type)

        # SEMI / ANTI — emit only left rows, no right columns
        if jt in ("SEMI", "ANTI"):
            output_rows: List[Row] = []
            provenance: Dict[tuple[str, ...], Mapping[str, tuple[tuple[str, ...], ...]]] = {}
            for lrow in left_ds.rows:
                ldict = _row_value_dict(lrow) if hasattr(lrow, 'column_values') else {}
                has_match = False
                matching_right_provenance: List[Mapping[str, Sequence[tuple[str, ...]]]] = []
                for rrow in right_ds.rows:
                    if _join_pair_matches(join_step, lrow, rrow):
                        has_match = True
                        matching_right_provenance.append(
                            right_ds.row_provenance.get(rrow.rowid, {})
                        )
                if (jt == "SEMI" and has_match) or (jt == "ANTI" and not has_match):
                    output = Row(
                        this=(_step_name(join_step), lrow.rowid),
                        columns={ident: ldict.get(ident, None)
                                 for ident in left_ds.columns},
                    )
                    output_rows.append(output)
                    provenance[output.rowid] = _merge_provenance(
                        left_ds.row_provenance.get(lrow.rowid, {}),
                        *matching_right_provenance,
                    )

            result = DerivedSchema(
                columns=left_ds.columns,
                rows=output_rows,
                datatypes=left_ds.datatypes,
                nullables=left_ds.nullables,
                uniqueness=left_ds.uniqueness,
            )
            result.row_provenance = provenance
            return result

        # INNER / LEFT / RIGHT / FULL — emit combined columns
        out_cols = tuple(left_ds.columns) + tuple(right_ds.columns)

        output_rows: List[Row] = []
        provenance: Dict[tuple[str, ...], Mapping[str, tuple[tuple[str, ...], ...]]] = {}
        matched_left: Set[tuple[str, ...]] = set()
        matched_right: Set[tuple[str, ...]] = set()
        left_table = getattr(left_ds, "_table", None)
        right_table = getattr(right_ds, "_table", None)

        for lrow in left_ds.rows:
            ldict = _row_value_dict(lrow) if hasattr(lrow, 'column_values') else {}
            for rrow in right_ds.rows:
                rdict = _row_value_dict(rrow) if hasattr(rrow, 'column_values') else {}
                merged = {**ldict, **rdict}
                if not _join_pair_matches(join_step, lrow, rrow):
                    continue
                out_row = Row(
                    this=(_step_name(join_step), lrow.rowid, rrow.rowid),
                    columns={ident: merged.get(ident, None)
                             for ident in out_cols},
                )
                output_rows.append(out_row)
                matched_left.add(lrow.rowid)
                matched_right.add(rrow.rowid)
                provenance[out_row.rowid] = _merge_provenance(
                    left_ds.row_provenance.get(lrow.rowid, {}),
                    right_ds.row_provenance.get(rrow.rowid, {}),
                )

        if jt in {"LEFT", "FULL"}:
            for lrow in left_ds.rows:
                if lrow.rowid in matched_left:
                    continue
                ldict = _row_value_dict(lrow)
                out_row = Row(
                    this=(_step_name(join_step), lrow.rowid, ("null_right",)),
                    columns={
                        column: ldict.get(column) if column in left_ds.columns else None
                        for column in out_cols
                    },
                )
                output_rows.append(out_row)
                row_provenance = dict(left_ds.row_provenance.get(lrow.rowid, {}))
                if right_table is not None:
                    row_provenance.setdefault(right_table.name, ())
                provenance[out_row.rowid] = row_provenance

        if jt in {"RIGHT", "FULL"}:
            for rrow in right_ds.rows:
                if rrow.rowid in matched_right:
                    continue
                rdict = _row_value_dict(rrow)
                out_row = Row(
                    this=(_step_name(join_step), ("null_left",), rrow.rowid),
                    columns={
                        column: rdict.get(column) if column in right_ds.columns else None
                        for column in out_cols
                    },
                )
                output_rows.append(out_row)
                row_provenance = dict(right_ds.row_provenance.get(rrow.rowid, {}))
                if left_table is not None:
                    row_provenance.setdefault(left_table.name, ())
                provenance[out_row.rowid] = row_provenance

        result = DerivedSchema(
            columns=out_cols,
            rows=output_rows,
            datatypes={**left_ds.datatypes, **right_ds.datatypes},
            nullables={**left_ds.nullables, **right_ds.nullables},
            uniqueness={**left_ds.uniqueness, **right_ds.uniqueness},
        )
        result.row_provenance = provenance
        return result

# ------------------------------------------------------------------
# Stub operators (passthrough)
# ------------------------------------------------------------------

class SubqueryAliasEncodeStep(EncodeStep):
    """Remap Column keys from child's table name to the alias."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        alias_step: SubqueryAlias = self.step
        alias_name = alias_step.alias.name if alias_step.alias else ""

        if not alias_name:
            return child

        new_rows: List[Row] = []
        for row in child.rows:
            new_columns = {}
            for key, val in row.column_values.items():
                if isinstance(key, exp.Column) and key.table:
                    new_key = exp.Column(
                        this=exp.Identifier(this=key.this.name if key.this else ""),
                        table=exp.Identifier(
                            this=alias_name,
                            quoted=bool(getattr(key.table, "quoted", False)),
                        ),
                    )
                    new_columns[new_key] = val
                else:
                    col_name = key.name if isinstance(key, exp.Identifier) else str(key)
                    new_key = exp.Column(
                        this=exp.Identifier(this=col_name, quoted=getattr(key, 'quoted', False)),
                        table=exp.Identifier(this=alias_name),
                    )
                    new_columns[new_key] = val
            new_rows.append(Row(this=row.rowid, columns=new_columns))

        new_cols = tuple(
            exp.Column(
                this=exp.Identifier(this=c.name if isinstance(c, exp.Identifier) else (c.this.name if isinstance(c, exp.Column) and c.this else "")),
                table=exp.Identifier(this=alias_name),
            )
            for c in child.columns
        )

        result = child.with_rows(new_rows, columns=new_cols)
        column_pairs = tuple(zip(child.columns, new_cols))
        result.datatypes = _remap_column_metadata(child.datatypes, column_pairs)
        result.nullables = _remap_column_metadata(child.nullables, column_pairs)
        result.uniqueness = _remap_column_metadata(child.uniqueness, column_pairs)
        result._table = getattr(child, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        child = context.single_dependency(self.step)
        context.lower(
            child,
            _rewrite_demand_alias(
                demand,
                self.step.alias.name if self.step.alias else "",
                output_schema,
                child_schemas[0],
                context.dialect,
            ),
        )


class AggregateEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Aggregate = self.step
        group_exprs = tuple(step.group or ())
        group_sources = tuple(
            expression.this if isinstance(expression, exp.Alias) else expression
            for expression in group_exprs
        )
        aggregations = tuple(step.aggregations or ())

        grouped: Dict[Tuple[Any, ...], List[Row]] = {}
        for row in child.rows:
            key = tuple(_expr_value(expr, row) for expr in group_sources)
            grouped.setdefault(key, []).append(row)
        if not grouped and not group_exprs:
            grouped[()] = []

        out_cols: List[Any] = []
        out_cols.extend(group_exprs)
        aggregate_keys = _aggregate_output_keys(aggregations, self.dialect)
        duplicate_keys = _duplicate_aggregate_key_names(aggregate_keys)
        if duplicate_keys:
            raise ValueError(
                "Duplicate aggregate output key(s): " + ", ".join(duplicate_keys)
            )
        out_cols.extend(aggregate_keys)
        out_rows: List[Row] = []

        for group_index, (group_key, rows) in enumerate(grouped.items()):
            values: Dict[Any, Any] = {}
            for expr, value in zip(group_exprs, group_key):
                values[expr] = value
            for aggregate, key in zip(aggregations, aggregate_keys):
                values[key] = _aggregate_value(aggregate, rows)
            rowids = tuple(row.rowid for row in rows)
            out_rows.append(
                Row(
                    this=(_step_name(step), str(group_index), *rowids),
                    columns=values,
                )
            )

        result = child.with_rows(out_rows, columns=tuple(out_cols))
        result.row_provenance = {
            row.rowid: _merge_provenance(
                *(child.row_provenance.get(source.rowid, {}) for source in rows)
            )
            for row, rows in zip(out_rows, grouped.values())
        }
        result.uniqueness.update({group_expr: True for group_expr in group_exprs})
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema
        node: Aggregate = self.step
        child = context.single_dependency(node)
        child_schema = child_schemas[0]
        expression_demands = _rewrite_aggregate_expression_demands(
            demand.expression_demands,
            node,
            context.dialect,
        )
        group_demands = list(demand.group_demands)
        child_predicates: List[exp.Expression] = []
        aggregate_predicate_group: GroupDemand | None = None
        for original_predicate in demand.predicates:
            predicate = _rewrite_aggregate_predicate(
                original_predicate,
                node,
                context.dialect,
            )
            for atom in _conjuncts(predicate):
                if not _expression_contains_aggregate(atom):
                    child_predicates.append(atom)
                    continue
                compiled = _aggregate_predicate_group_demand(
                    atom,
                    node,
                    group_index=0,
                    default_row_count=max(
                        int(getattr(context.config, "rows_per_group", 1) or 1),
                        1,
                    ),
                    dialect=context.dialect,
                )
                if compiled is None:
                    context.pipeline._record_demand_failure(
                        f"unsupported_aggregate_predicate:{atom.sql(dialect=context.dialect)}"
                    )
                    return
                if aggregate_predicate_group is None:
                    aggregate_predicate_group = compiled
                    continue
                keys = list(aggregate_predicate_group.group_key_values)
                for key, value in compiled.group_key_values:
                    if any(
                        _expression_key(existing, context.dialect)
                        == _expression_key(key, context.dialect)
                        for existing, _existing_value in keys
                    ):
                        continue
                    keys.append((key, value))
                aggregate_predicate_group = GroupDemand(
                    group_index=0,
                    row_count=max(
                        aggregate_predicate_group.row_count,
                        compiled.row_count,
                    ),
                    group_key_values=tuple(keys),
                    row_predicates=(
                        aggregate_predicate_group.row_predicates
                        + compiled.row_predicates
                    ),
                    row_predicates_by_index=(
                        aggregate_predicate_group.row_predicates_by_index
                        + compiled.row_predicates_by_index
                    ),
                )
        if aggregate_predicate_group is not None:
            group_demands.append(aggregate_predicate_group)
        group_demands.extend(
            _aggregate_expression_group_demands(
                node,
                expression_demands,
                rows_per_group=max(
                    int(getattr(context.config, "rows_per_group", 1) or 1),
                    1,
                ),
                dialect=context.dialect,
            )
        )
        if node.group and not group_demands:
            group_demands = _aggregate_group_demands(
                group_count=max(
                    demand.count,
                    int(getattr(context.config, "groups", 1) or 1),
                ),
                rows_per_group=max(
                    int(getattr(context.config, "rows_per_group", 1) or 1),
                    1,
                ),
            )
        group_demands = list(
            _with_random_aggregate_null_rows(
                node,
                group_demands,
                child_schema,
                context.pipeline.randomizer,
                max_rows=min(
                    int(getattr(context.config, "max_rows_per_table", 1) or 1),
                    int(getattr(context.config, "max_total_rows", 1) or 1),
                ),
                dialect=context.dialect,
            )
        )
        if node.group and group_demands:
            expression_demands = expression_demands + _aggregate_group_key_expression_demands(
                node,
                group_demands,
                context.dialect,
            )
        child_group_demands = group_demands
        child_order_keys = tuple(node.group or ()) + tuple(
            key
            for key in demand.order_keys
            if _expression_uses_only_schema_columns(
                key.this if isinstance(key, exp.Ordered) else key,
                child_schema,
                context.dialect,
            )
        )
        context.lower(
            child,
            SchemaDemand(
                count=max(demand.count, sum(group.row_count for group in group_demands)),
                predicates=tuple(child_predicates),
                order_keys=child_order_keys,
                distinct=demand.distinct,
                group_demands=child_group_demands,
                expression_demands=tuple(
                    expression_demand
                    for expression_demand in expression_demands
                    if not _expression_contains_aggregate(expression_demand.expression)
                ),
                require_scalar_order_ties=demand.require_scalar_order_ties,
            ),
        )


class SortEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Sort = self.step

        rows = list(child.rows)
        for key in reversed(step.key or []):
            expr = key.this if isinstance(key, exp.Ordered) else key
            desc = isinstance(key, exp.Ordered) and bool(key.args.get("desc"))
            rows.sort(
                key=lambda row: sql_order_key(_expr_value(expr, row)),
                reverse=desc,
            )
        if step.fetch is not None:
            rows = rows[: step.fetch]
        return child.with_rows(rows)

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema
        context.pipeline._materialize_sort_demand(
            self.step,
            demand,
            child_schemas[0],
            context.cache,
        )


class LimitEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Limit = self.step
        offset = step.offset or 0
        stop = None if step.fetch is None else offset + step.fetch
        rows = list(child.rows)[offset:stop]
        return child.with_rows(rows)

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        offset = self.step.offset or 0
        fetch = self.step.fetch or demand.count
        context.lower(
            context.single_dependency(self.step),
            SchemaDemand(
                count=max(demand.count, offset + fetch),
                predicates=demand.predicates,
                order_keys=demand.order_keys,
                distinct=demand.distinct,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands,
                require_scalar_order_ties=demand.require_scalar_order_ties,
            ),
        )


class DistinctEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        seen: Set[Tuple[Any, ...]] = set()
        rows: List[Row] = []
        for row in child.rows:
            key = tuple(_cell_value(row[column]) for column in child.columns)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        return child.with_rows(rows)

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        context.lower(
            context.single_dependency(self.step),
            SchemaDemand(
                count=demand.count,
                predicates=demand.predicates,
                order_keys=demand.order_keys,
                distinct=True,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands,
                require_scalar_order_ties=demand.require_scalar_order_ties,
            ),
        )


class UnionEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: Union = self.step
        columns = _union_columns(children)
        rows: List[Row] = []
        seen: Set[Tuple[Any, ...]] = set()
        provenance: Dict[tuple[str, ...], Mapping[str, tuple[tuple[str, ...], ...]]] = {}
        for branch_index, child in enumerate(children):
            for row in child.rows:
                values = _row_values_by_position(row)
                if len(values) != len(columns):
                    raise ValueError(
                        f"Union row width does not match output columns: {len(values)} != {len(columns)}"
                    )
                key = tuple(_cell_value(value) for value in values)
                if not step.is_all and key in seen:
                    continue
                seen.add(key)
                output = Row(
                    this=(_step_name(step), str(branch_index), row.rowid),
                    columns=dict(zip(columns, values)),
                )
                rows.append(output)
                provenance[output.rowid] = dict(
                    child.row_provenance.get(row.rowid, {})
                )
        result = (children[0] if children else DerivedSchema(columns=columns)).with_rows(
            rows,
            columns=columns,
        )
        result.row_provenance = provenance
        return result


def _union_columns(children: Tuple[DerivedSchema, ...]) -> Tuple[Any, ...]:
    for child in children:
        if child.columns:
            return child.columns
    for child in children:
        for row in child.rows:
            return row.columns
    return ()


def _row_values_by_position(row: Row) -> Tuple[Any, ...]:
    return tuple(row.values())

class ValuesEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: Values = self.step
        width = max((len(row) for row in step.values), default=0)
        columns = tuple(exp.to_identifier(f"column{i + 1}") for i in range(width))
        rows = [
            Row(
                this=(_step_name(step), str(index)),
                columns={
                    columns[col_index]: concrete(value, Environment())
                    for col_index, value in enumerate(values)
                },
            )
            for index, values in enumerate(step.values)
        ]
        return DerivedSchema(columns=columns, rows=rows)


class EmptyRelationEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: EmptyRelation = self.step
        rows = [Row(this=(_step_name(step), "0"), columns={})] if step.produce_one_row else []
        return DerivedSchema(columns=(), rows=rows)


class RepartitionEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        return children[0]


class WindowEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        values_by_window: List[Dict[int, object]] = []
        output_columns: List[exp.Column] = []
        for window in self.step.window_exprs:
            output = exp.column(
                str(window.meta.get("datafusion_name") or window.sql(dialect=self.dialect)),
                quoted=True,
            )
            output_columns.append(output)
            values: Dict[int, object] = {}
            partitions: Dict[Tuple[object, ...], List[Row]] = {}
            partition_by = tuple(window.args.get("partition_by") or ())
            for row in child.rows:
                key = tuple(_expr_value(expression, row) for expression in partition_by)
                partitions.setdefault(key, []).append(row)
            order = window.args.get("order")
            order_keys = tuple(order.expressions) if isinstance(order, exp.Order) else ()
            for rows in partitions.values():
                ordered_rows = list(rows)
                for key in reversed(order_keys):
                    expression = key.this if isinstance(key, exp.Ordered) else key
                    descending = isinstance(key, exp.Ordered) and bool(key.args.get("desc"))
                    ordered_rows.sort(
                        key=lambda row: sql_order_key(_expr_value(expression, row)),
                        reverse=descending,
                    )
                previous_key: Tuple[object, ...] | None = None
                rank = 0
                dense_rank = 0
                for position, row in enumerate(ordered_rows, start=1):
                    order_value = tuple(
                        _expr_value(
                            key.this if isinstance(key, exp.Ordered) else key,
                            row,
                        )
                        for key in order_keys
                    )
                    if previous_key is None or order_value != previous_key:
                        rank = position
                        dense_rank += 1
                        previous_key = order_value
                    function = window.this
                    function_name = (
                        "row_number"
                        if isinstance(function, exp.RowNumber)
                        else str(function.this).casefold()
                        if isinstance(function, exp.Anonymous)
                        else ""
                    )
                    if function_name == "row_number":
                        value = position
                    elif function_name == "rank":
                        value = rank
                    elif function_name == "dense_rank":
                        value = dense_rank
                    else:
                        value = None
                    values[id(row)] = value
            values_by_window.append(values)

        rows = [
            Row(
                this=row.rowid,
                columns={
                    **row.column_values,
                    **{
                        output: values[id(row)]
                        for output, values in zip(output_columns, values_by_window)
                    },
                },
            )
            for row in child.rows
        ]
        result = child.with_rows(rows, columns=tuple(child.columns) + tuple(output_columns))
        for output, window in zip(output_columns, self.step.window_exprs):
            if window.type is not None:
                result.datatypes[output] = window.type
            result.nullables[output] = False
            result.uniqueness[output] = False
        result._table = getattr(child, "_table", None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        context.lower(context.single_dependency(self.step), demand)


class UnnestEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        return DerivedSchema(columns=tuple(self.step.columns), rows=[])


class UnsupportedPlanEncodeStep(EncodeStep):
    """Keep an unsupported plan node evaluable by the concrete interpreter."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        del children
        return DerivedSchema(columns=(), rows=[])


def _row_mapping(row: Row) -> Dict[Any, Any]:
    return {key: _cell_value(value) for key, value in row.column_values.items()}


def _merge_provenance(
    *provenances: Mapping[str, Sequence[tuple[str, ...]]],
) -> Dict[str, tuple[tuple[str, ...], ...]]:
    merged: Dict[str, List[tuple[str, ...]]] = {}
    for provenance in provenances:
        for table, rowids in provenance.items():
            values = merged.setdefault(table, [])
            for rowid in rowids:
                if rowid not in values:
                    values.append(rowid)
    return {table: tuple(rowids) for table, rowids in merged.items()}


def _cell_value(value: Any) -> Any:
    return value.concrete if isinstance(value, Symbol) else value


def _expr_value(expr: exp.Expression, row: Row) -> Any:
    if isinstance(expr, exp.Column) and expr in row:
        return _cell_value(row[expr])
    if not concrete_supported(expr):
        return _UNSUPPORTED_EVALUATION
    return concrete(expr, Environment.from_row(_row_mapping(row)))


def _projection_output_item(
    projection: exp.Expression,
    dialect: str | None,
) -> Tuple[exp.Expression, exp.Expression]:
    expression = projection.this if isinstance(projection, exp.Alias) else projection
    if isinstance(projection, exp.Alias):
        output = exp.Column(
            this=exp.Identifier(this=projection.alias),
        )
    elif isinstance(expression, exp.Column):
        output = expression.copy()
    else:
        output = exp.Column(
            this=exp.Identifier(
                this=_projection_expression_name(expression, dialect),
                quoted=True,
            )
        )
    return output, expression


def _projection_items_with_scalar_subqueries(
    projections: Sequence[exp.Expression],
    subquery_schemas: Sequence[DerivedSchema],
    dialect: str | None,
) -> Tuple[Tuple[exp.Expression, exp.Expression], ...]:
    items: List[Tuple[exp.Expression, exp.Expression]] = []
    offset = 0
    for projection in projections:
        output, expression = _projection_output_item(projection, dialect)
        subquery_count = len(list(expression.find_all(ScalarSubqueryRef)))
        items.append(
            (
                output,
                _projection_expression_with_scalar_subqueries(
                    expression,
                    subquery_schemas[offset : offset + subquery_count],
                ),
            )
        )
        offset += subquery_count
    return tuple(items)


def _projection_expression_name(
    expression: exp.Expression,
    dialect: str | None,
) -> str:
    if expression.alias_or_name:
        return str(expression.alias_or_name)
    return expression.sql(dialect=dialect)


def _projection_expression_with_scalar_subqueries(
    expression: exp.Expression,
    subquery_schemas: Sequence[DerivedSchema],
) -> exp.Expression:
    if not subquery_schemas:
        return expression
    rewritten = deepcopy(expression)
    for ref, schema in zip(list(rewritten.find_all(ScalarSubqueryRef)), subquery_schemas):
        ref.replace(_literal_for_scalar_schema(schema))
    return rewritten


def _aggregate_output_keys(
    aggregations: Sequence[exp.Expression],
    dialect: str | None,
) -> Tuple[exp.Column, ...]:
    compatible_keys = tuple(
        _quoted_column(_aggregate_output_name(aggregate, dialect))
        for aggregate in aggregations
    )
    counts: Dict[str, int] = {}
    for key in compatible_keys:
        name = key.name.casefold()
        counts[name] = counts.get(name, 0) + 1
    return tuple(
        _quoted_column(_aggregate_name(aggregate, dialect))
        if counts[compatible_key.name.casefold()] > 1
        else compatible_key
        for aggregate, compatible_key in zip(aggregations, compatible_keys)
    )


def _quoted_column(name: str) -> exp.Column:
    return exp.Column(
        this=exp.Identifier(
            this=name,
            quoted=True,
        )
    )


def _aggregate_output_name(
    aggregate: exp.Expression,
    dialect: str | None,
) -> str:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, exp.Count):
        source = expression.this
        if source is None or isinstance(source, exp.Star):
            return "count(Int64(1))"
        return f"count({_aggregate_arg_name(source, dialect)})"
    if isinstance(expression, exp.Avg):
        return f"avg({_aggregate_arg_name(expression.this, dialect)})"
    if isinstance(expression, exp.Sum):
        return f"sum({_aggregate_arg_name(expression.this, dialect)})"
    if isinstance(expression, exp.Min):
        return f"min({_aggregate_arg_name(expression.this, dialect)})"
    if isinstance(expression, exp.Max):
        return f"max({_aggregate_arg_name(expression.this, dialect)})"
    return expression.sql(dialect=dialect)


def _aggregate_name(
    aggregate: exp.Expression,
    dialect: str | None,
) -> str:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max)):
        source = expression.this
        function_name = expression.key.lower()
        if source is None or isinstance(source, exp.Star):
            return f"{function_name}(*)"
        if isinstance(source, exp.Distinct):
            source_sql = ", ".join(
                item.sql(dialect=dialect) for item in source.expressions
            )
            return f"{function_name}(DISTINCT {source_sql})"
        if expression.args.get("distinct"):
            return f"{function_name}(DISTINCT {source.sql(dialect=dialect)})"
        return f"{function_name}({source.sql(dialect=dialect)})"
    return expression.sql(dialect=dialect)


def _aggregate_arg_name(
    expr: exp.Expression | None,
    dialect: str | None,
) -> str:
    if expr is None:
        return ""
    while isinstance(expr, exp.Cast):
        expr = expr.this
    if isinstance(expr, exp.Distinct):
        return ", ".join(_aggregate_arg_name(item, dialect) for item in expr.expressions)
    return expr.sql(dialect=dialect)


def _duplicate_aggregate_key_names(keys: Sequence[exp.Column]) -> Tuple[str, ...]:
    seen: Set[str] = set()
    duplicates: List[str] = []
    for key in keys:
        name = key.name.casefold()
        if name in seen and key.name not in duplicates:
            duplicates.append(key.name)
        seen.add(name)
    return tuple(duplicates)


def _aggregate_value(aggregate: exp.Expression, rows: List[Row]) -> Any:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, exp.Count):
        source = expression.this
        if source is None or isinstance(source, exp.Star):
            return len(rows)
        values = _aggregate_inputs(source, rows)
        if values is None:
            return None
        if isinstance(source, exp.Distinct) or expression.args.get("distinct"):
            return len({value for value in values if value is not None})
        return sum(1 for value in values if value is not None)
    if isinstance(expression, exp.Avg):
        values = _nonnull_aggregate_inputs(expression.this, rows)
        if values is None:
            return None
        return None if not values else sum(values) / len(values)
    if isinstance(expression, exp.Sum):
        values = _nonnull_aggregate_inputs(expression.this, rows)
        if values is None:
            return None
        return None if not values else sum(values)
    if isinstance(expression, exp.Min):
        values = _nonnull_aggregate_inputs(expression.this, rows)
        if values is None:
            return None
        return None if not values else min(values)
    if isinstance(expression, exp.Max):
        values = _nonnull_aggregate_inputs(expression.this, rows)
        if values is None:
            return None
        return None if not values else max(values)
    return None


def _aggregate_inputs(expr: exp.Expression | None, rows: List[Row]) -> List[Any] | None:
    if isinstance(expr, exp.Distinct):
        if len(expr.expressions) != 1:
            return []
        expr = expr.expressions[0]
    if expr is None:
        return []
    values = [_expr_value(expr, row) for row in rows]
    return None if any(value is _UNSUPPORTED_EVALUATION for value in values) else values


def _nonnull_aggregate_inputs(
    expr: exp.Expression | None,
    rows: List[Row],
) -> List[Any] | None:
    values = _aggregate_inputs(expr, rows)
    return None if values is None else [value for value in values if value is not None]


def _aggregate_expression_map(
    aggregate: Aggregate,
    dialect: str | None = None,
) -> Dict[str, exp.Expression]:
    mapping: Dict[str, exp.Expression] = {}
    output_keys = _aggregate_output_keys(tuple(aggregate.aggregations or ()), dialect)
    for expression, key in zip(aggregate.aggregations or (), output_keys):
        unaliased = expression.this if isinstance(expression, exp.Alias) else expression
        mapping[key.name.casefold()] = unaliased
        mapping[_expression_key(key, dialect)] = unaliased
        canonical_key = _quoted_column(_aggregate_name(expression, dialect))
        mapping[canonical_key.name.casefold()] = unaliased
        mapping[_expression_key(canonical_key, dialect)] = unaliased
        mapping[_expression_key(unaliased, dialect)] = unaliased
        if expression.alias_or_name:
            mapping[str(expression.alias_or_name).casefold()] = unaliased
    return mapping


def _resolve_aggregate_expression(
    expression: exp.Expression,
    aggregates: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> exp.Expression | None:
    while isinstance(expression, exp.Cast):
        expression = expression.this
    if isinstance(expression, exp.Column):
        return (
            aggregates.get(expression.name.casefold())
            or aggregates.get(_expression_key(expression, dialect))
        )
    if isinstance(expression, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
        return expression
    return None


def _rewrite_aggregate_expression_demands(
    expression_demands: Sequence[ExpressionDemand],
    aggregate: Aggregate,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    return tuple(
        ExpressionDemand(
            expression=_rewrite_aggregate_columns(
                expression_demand.expression,
                aggregate,
                dialect,
            ),
            kind=expression_demand.kind,
            value=expression_demand.value,
            rank=expression_demand.rank,
            descending=expression_demand.descending,
            origin=expression_demand.origin,
        )
        for expression_demand in expression_demands
    )


def _rewrite_aggregate_predicate(
    predicate: exp.Expression,
    aggregate: Aggregate,
    dialect: str | None = None,
) -> exp.Expression:
    return _rewrite_aggregate_columns(predicate, aggregate, dialect)


def _rewrite_aggregate_columns(
    expression: exp.Expression,
    aggregate: Aggregate,
    dialect: str | None = None,
) -> exp.Expression:
    aggregates = _aggregate_expression_map(aggregate, dialect)

    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            replacement = _resolve_aggregate_expression(node, aggregates, dialect)
            if replacement is not None:
                return deepcopy(replacement)
        return node

    return deepcopy(expression).transform(replace)


def _group_key_predicates(
    group: GroupDemand,
) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for key, value in group.group_key_values:
        predicates.extend(
            _expression_demand_predicates(
                ExpressionDemand(
                    expression=key,
                    kind="group",
                    value=value,
                    rank=group.group_index,
                    origin="group_key",
                )
            )
        )
    return tuple(predicates)


def _columns_including_self(expression: exp.Expression) -> Tuple[exp.Column, ...]:
    if isinstance(expression, exp.Column):
        return (expression,)
    return tuple(expression.find_all(exp.Column))


def _predicate_equality_value(
    predicates: Sequence[exp.Expression],
    expression: exp.Expression,
    dialect: str | None = None,
) -> object:
    expression_key = _expression_key(expression, dialect)
    for predicate in predicates:
        for atom in _conjuncts(predicate):
            if not isinstance(atom, exp.EQ):
                continue
            if (
                isinstance(atom.expression, exp.Literal)
                and _expression_key(atom.this, dialect) == expression_key
            ):
                return _literal_value(atom.expression)
            if (
                isinstance(atom.this, exp.Literal)
                and _expression_key(atom.expression, dialect) == expression_key
            ):
                return _literal_value(atom.this)
    return _MISSING


def _aggregate_arg_expression(expression: exp.Expression) -> exp.Expression | None:
    arg = expression.this if isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count)) else None
    while isinstance(arg, exp.Cast):
        arg = arg.this
    if isinstance(arg, exp.Distinct) and len(arg.expressions) == 1:
        arg = arg.expressions[0]
    if isinstance(arg, exp.Star):
        return None
    return arg


def _count_column_argument(aggregate: Aggregate) -> exp.Expression | None:
    for item in aggregate.aggregations:
        expression = item.this if isinstance(item, exp.Alias) else item
        if not isinstance(expression, exp.Count) or isinstance(expression.this, exp.Distinct):
            continue
        argument = _aggregate_arg_expression(expression)
        if argument is not None:
            return argument
    return None

def _predicate_argument_row_state(
    predicates: Sequence[exp.Expression],
    argument: exp.Expression,
    dialect: str | None = None,
) -> Tuple[bool, object]:
    argument_key = _expression_key(argument, dialect)
    forced_value: object = _MISSING
    for predicate in predicates:
        for atom in _conjuncts(predicate):
            if (
                isinstance(atom, exp.Is)
                and _expression_key(atom.this, dialect) == argument_key
                and isinstance(atom.expression, exp.Null)
            ):
                return True, forced_value
            if not isinstance(atom, exp.EQ):
                continue
            if (
                _expression_key(atom.this, dialect) == argument_key
                and isinstance(atom.expression, exp.Literal)
            ):
                forced_value = _literal_value(atom.expression)
            if (
                _expression_key(atom.expression, dialect) == argument_key
                and isinstance(atom.this, exp.Literal)
            ):
                forced_value = _literal_value(atom.this)
    return False, forced_value


def _row_predicates_for_group_row(
    group: GroupDemand,
    row_index: int,
) -> Tuple[exp.Expression, ...]:
    predicates = list(group.row_predicates)
    for local_index, local_predicates in group.row_predicates_by_index:
        if local_index == row_index:
            predicates.extend(local_predicates)
    return tuple(predicates)


def _not_null_predicate(expression: exp.Expression) -> exp.Expression:
    return exp.Not(this=exp.Is(this=deepcopy(expression), expression=exp.Null()))


def _comparison_target(
    condition: exp.Expression,
) -> Tuple[exp.Expression, exp.Expression, str] | None:
    if isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
        operator = type(condition).__name__.lower()
        if isinstance(condition.expression, exp.Literal):
            return condition.this, condition.expression, operator
        if isinstance(condition.this, exp.Literal):
            reversed_operator = {
                "gt": "lt",
                "gte": "lte",
                "lt": "gt",
                "lte": "gte",
                "eq": "eq",
            }[operator]
            return condition.expression, condition.this, reversed_operator
    return None


def _comparison_predicate(
    expression: exp.Expression,
    literal: exp.Expression,
    operator: str,
    *,
    pass_group: bool,
) -> exp.Expression | None:
    predicate_type = {
        "gt": exp.GT,
        "gte": exp.GTE,
        "lt": exp.LT,
        "lte": exp.LTE,
        "eq": exp.EQ,
    }.get(operator)
    if predicate_type is None:
        return None
    predicate = predicate_type(
        this=deepcopy(expression),
        expression=deepcopy(literal),
    )
    return predicate if pass_group else _invert_atom(predicate)


def _row_count_for_count(
    threshold: float,
    operator: str,
    default_count: int,
    *,
    pass_group: bool,
) -> int:
    if operator in {"gt", "gte"}:
        passing = int(threshold) + (1 if operator == "gt" else 0)
        failing = max(int(threshold) - (0 if operator == "gt" else 1), 0)
    elif operator in {"lt", "lte"}:
        passing = max(int(threshold) - (1 if operator == "lt" else 0), 0)
        failing = int(threshold) + (0 if operator == "lt" else 1)
    else:
        passing = int(threshold)
        failing = int(threshold) + 1
    return max(default_count, passing if pass_group else failing, 1)


def _group_demand_for_having(
    condition: exp.Expression,
    aggregate: Aggregate,
    group_index: int,
    default_row_count: int,
    *,
    pass_group: bool,
    dialect: str | None = None,
) -> GroupDemand | None:
    conjuncts = _conjuncts(condition)
    if len(conjuncts) > 1:
        if not pass_group:
            return _group_demand_for_having(
                conjuncts[0],
                aggregate,
                group_index,
                default_row_count,
                pass_group=False,
                dialect=dialect,
            )
        demands = [
            _group_demand_for_having(
                conjunct,
                aggregate,
                group_index,
                default_row_count,
                pass_group=True,
                dialect=dialect,
            )
            for conjunct in conjuncts
        ]
        if any(demand is None for demand in demands):
            return None
        return GroupDemand(
            group_index=group_index,
            row_count=max(demand.row_count for demand in demands if demand is not None),
            row_predicates=tuple(
                predicate
                for demand in demands
                if demand is not None
                for predicate in demand.row_predicates
            ),
            row_predicates_by_index=tuple(
                item
                for demand in demands
                if demand is not None
                for item in demand.row_predicates_by_index
            ),
        )
    target = _comparison_target(condition)
    if target is None:
        return None
    expression, literal, operator = target
    threshold = _numeric_value(literal)
    if threshold is None:
        return None

    aggregates = _aggregate_expression_map(aggregate, dialect)
    row_predicates: List[exp.Expression] = []
    row_predicates_by_index: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    row_count = max(default_row_count, 1)

    if isinstance(expression, exp.Div):
        # Ratio aggregates need an exact finite-row encoding. Do not guess a
        # numerator value and present the resulting sufficient condition as an
        # exact translation.
        return None

    aggregate_expression = _resolve_aggregate_expression(expression, aggregates, dialect)
    if aggregate_expression is None:
        return None

    if isinstance(aggregate_expression, exp.Count):
        row_count = _row_count_for_count(
            threshold,
            operator,
            default_row_count,
            pass_group=pass_group,
        )
        arg = _aggregate_arg_expression(aggregate_expression)
        if arg is not None:
            required_non_null = _non_null_count_for_count(
                threshold,
                operator,
                row_count,
                pass_group=pass_group,
            )
            row_predicates_by_index.extend(
                _required_not_null_row_predicates(
                    arg,
                    required_count=required_non_null,
                    row_count=row_count,
                    existing=row_predicates_by_index,
                    dialect=dialect,
                )
            )
    elif isinstance(aggregate_expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        arg = _aggregate_arg_expression(aggregate_expression)
        if arg is None:
            return None
        if isinstance(aggregate_expression, exp.Avg):
            row_count = max(row_count, 2)
        elif isinstance(aggregate_expression, exp.Sum):
            row_count = 1
        predicate = _comparison_predicate(
            arg,
            literal,
            operator,
            pass_group=pass_group,
        )
        if predicate is None:
            return None
        row_predicates.append(predicate)
    else:
        return None

    return GroupDemand(
        group_index=group_index,
        row_count=row_count,
        row_predicates=tuple(row_predicates),
        row_predicates_by_index=tuple(row_predicates_by_index),
    )


def _required_not_null_row_predicates(
    argument: exp.Expression,
    *,
    required_count: int,
    row_count: int,
    existing: Sequence[Tuple[int, Tuple[exp.Expression, ...]]],
    dialect: str | None = None,
) -> Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...]:
    if required_count <= 0:
        return ()
    existing_by_index: Dict[int, Tuple[exp.Expression, ...]] = {
        index: predicates for index, predicates in existing
    }
    selected: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    for index in range(row_count):
        predicates = existing_by_index.get(index, ())
        forces_null, _forced_value = _predicate_argument_row_state(
            predicates,
            argument,
            dialect,
        )
        if forces_null:
            continue
        selected.append((index, (_not_null_predicate(argument),)))
        if len(selected) == required_count:
            return tuple(selected)
    return tuple(selected)


def _non_null_count_for_count(
    threshold: float,
    operator: str,
    row_count: int,
    *,
    pass_group: bool,
) -> int:
    if pass_group:
        if operator == "gt":
            return min(max(int(threshold) + 1, 0), row_count)
        if operator == "gte":
            return min(max(int(threshold), 0), row_count)
        if operator == "eq":
            return min(max(int(threshold), 0), row_count)
        if operator == "lt":
            return min(max(int(threshold) - 1, 0), row_count)
        if operator == "lte":
            return min(max(int(threshold), 0), row_count)
    if operator in {"gt", "gte"}:
        return 0
    if operator in {"lt", "lte", "eq"}:
        return min(row_count, max(int(threshold) + 1, 1))
    return 0


def _aggregate_expression_group_demands(
    aggregate: Aggregate,
    expression_demands: Sequence[ExpressionDemand],
    *,
    rows_per_group: int,
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    demands: List[GroupDemand] = []
    for expression_demand in expression_demands:
        if not _expression_contains_aggregate(expression_demand.expression):
            continue
        predicates = _expression_demand_predicates(expression_demand)
        for predicate in predicates:
            demand = _aggregate_predicate_group_demand(
                predicate,
                aggregate,
                group_index=expression_demand.rank or 0,
                default_row_count=rows_per_group,
                dialect=dialect,
            )
            if demand is not None:
                demands.append(demand)
    return tuple(demands)


def _aggregate_group_key_expression_demands(
    aggregate: Aggregate,
    group_demands: Sequence[GroupDemand],
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for group in group_demands:
        for expression in aggregate.group or ():
            if any(
                _expression_key(key, dialect) == _expression_key(expression, dialect)
                for key, _value in group.group_key_values
            ):
                continue
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(expression),
                    kind="distinct",
                    rank=group.group_index,
                    origin=f"group_key:{expression.sql(dialect=dialect)}",
                )
            )
    return tuple(demands)


def _expand_group_key_expression_demands(
    expression_demands: Sequence[ExpressionDemand],
    group_demands: Sequence[GroupDemand],
) -> Tuple[ExpressionDemand, ...]:
    """Lower logical group identities to relations over physical row ranks."""
    group_by_index = {group.group_index: group for group in group_demands}
    row_start_by_index: Dict[int, int] = {}
    next_rank = 0
    for group in group_demands:
        row_start_by_index[group.group_index] = next_rank
        next_rank += max(group.row_count, 1)

    expanded: List[ExpressionDemand] = []
    for demand in expression_demands:
        if (
            demand.kind != "distinct"
            or not demand.origin.startswith("group_key:")
            or demand.rank not in group_by_index
        ):
            expanded.append(demand)
            continue
        group = group_by_index[demand.rank]
        start = row_start_by_index[group.group_index]
        expanded.append(
            ExpressionDemand(
                expression=deepcopy(demand.expression),
                kind="distinct",
                rank=start,
                descending=demand.descending,
                origin=demand.origin,
            )
        )
        cohort_origin = f"{demand.origin}:group:{group.group_index}"
        expanded.extend(
            ExpressionDemand(
                expression=deepcopy(demand.expression),
                kind="equal",
                rank=start + offset,
                descending=demand.descending,
                origin=cohort_origin,
            )
            for offset in range(max(group.row_count, 1))
        )
    return tuple(expanded)


def _expression_contains_aggregate(expression: exp.Expression) -> bool:
    return any(
        expression.find(kind) is not None
        for kind in (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    )


def _aggregate_predicate_group_demand(
    predicate: exp.Expression,
    aggregate: Aggregate,
    *,
    group_index: int,
    default_row_count: int,
    dialect: str | None = None,
) -> GroupDemand | None:
    literal_demand = _group_demand_for_having(
        predicate,
        aggregate,
        group_index,
        default_row_count,
        pass_group=True,
        dialect=dialect,
    )
    if literal_demand is not None:
        return literal_demand
    if not isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
        return None
    aggregates = _aggregate_expression_map(aggregate, dialect)
    left = _resolve_aggregate_expression(predicate.this, aggregates, dialect)
    right = _resolve_aggregate_expression(predicate.expression, aggregates, dialect)
    if type(left) is not type(right) or not isinstance(left, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        return None
    left_arg = _aggregate_arg_expression(left)
    right_arg = _aggregate_arg_expression(right)
    if left_arg is None or right_arg is None:
        return None
    row_predicate = type(predicate)(
        this=deepcopy(left_arg),
        expression=deepcopy(right_arg),
    )
    row_count = max(default_row_count, 2 if isinstance(left, exp.Avg) else 1)
    return GroupDemand(
        group_index=group_index,
        row_count=row_count,
        row_predicates=(row_predicate,),
    )


def _having_group_demands(
    condition: exp.Expression,
    aggregate: Aggregate,
    *,
    group_count: int,
    default_row_count: int,
    pass_group: bool,
    start_index: int = 0,
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    demands: List[GroupDemand] = []
    for offset in range(max(group_count, 1)):
        group_index = start_index + offset
        demand = _group_demand_for_having(
            condition,
            aggregate,
            group_index,
            default_row_count,
            pass_group=pass_group,
            dialect=dialect,
        )
        if demand is not None:
            demands.append(demand)
    return tuple(demands)


def _aggregate_group_demands(
    *,
    group_count: int,
    rows_per_group: int,
) -> Tuple[GroupDemand, ...]:
    return tuple(
        GroupDemand(
            group_index=index,
            row_count=min(index + 1, max(rows_per_group, 1)),
        )
        for index in range(max(group_count, 1))
    )


def _with_random_aggregate_null_rows(
    aggregate: Aggregate,
    group_demands: Sequence[GroupDemand],
    child_schema: DerivedSchema,
    randomizer: random.Random,
    *,
    max_rows: int,
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    columns: List[exp.Column] = []
    seen: Set[str] = set()
    group_keys = {
        _expression_key(column, dialect)
        for expression in aggregate.group
        for column in _columns_including_self(expression)
    }
    for item in aggregate.aggregations:
        expression = item.this if isinstance(item, exp.Alias) else item
        for column in expression.find_all(exp.Column):
            scoped = _expression_in_schema_scope(column, child_schema, dialect)
            if not isinstance(scoped, exp.Column):
                continue
            key = _expression_key(scoped, dialect)
            if key in seen or key in group_keys or not _schema_column_metadata(
                child_schema.nullables,
                scoped,
                dialect,
            ):
                continue
            seen.add(key)
            columns.append(scoped)

    if not columns or not group_demands:
        return tuple(group_demands)

    remaining = max(max_rows - sum(group.row_count for group in group_demands), 0)
    expanded: List[GroupDemand] = []
    for group in group_demands:
        row_count = group.row_count
        row_predicates_by_index = list(group.row_predicates_by_index)
        constrained_keys = {
            _expression_key(column, dialect)
            for predicate in group.row_predicates
            for column in predicate.find_all(exp.Column)
        }
        eligible = [
            column
            for column in columns
            if _expression_key(column, dialect) not in constrained_keys
        ]
        for column in eligible:
            null_count = min(
                randomizer.randint(1, max(group.row_count, 1)),
                remaining,
            )
            for _ in range(null_count):
                predicates: List[exp.Expression] = []
                for candidate in eligible:
                    is_null = exp.Is(
                        this=deepcopy(candidate),
                        expression=exp.Null(),
                    )
                    predicates.append(
                        is_null if candidate == column else exp.Not(this=is_null)
                    )
                row_predicates_by_index.append((row_count, tuple(predicates)))
                row_count += 1
            remaining -= null_count
        expanded.append(
            GroupDemand(
                group_index=group.group_index,
                row_count=row_count,
                group_key_values=group.group_key_values,
                row_predicates=group.row_predicates,
                row_predicates_by_index=tuple(row_predicates_by_index),
            )
        )
    return tuple(expanded)


def _aggregate_duplicate_group_demand(
    aggregate: Aggregate,
    child: Step,
    child_schema: DerivedSchema,
) -> GroupDemand | None:
    if not aggregate.group or aggregate.aggregations:
        return None
    if _step_wraps_aggregate(child):
        return None
    group_exprs = tuple(aggregate.group or ())
    group_sources = tuple(
        expression.this if isinstance(expression, exp.Alias) else expression
        for expression in group_exprs
    )
    groups: dict[tuple[object, ...], int] = {}
    for row in child_schema.rows:
        key = tuple(_expr_value(expression, row) for expression in group_sources)
        if any(value is None for value in key):
            continue
        groups[key] = groups.get(key, 0) + 1
    if not groups:
        return None
    if any(size > 1 for size in groups.values()):
        return None
    key, size = max(groups.items(), key=lambda item: item[1])
    return GroupDemand(
        group_index=0,
        row_count=size + 1,
        group_key_values=tuple(
            (deepcopy(expression), value)
            for expression, value in zip(group_exprs, key)
        ),
    )


def _step_wraps_aggregate(step: Step) -> bool:
    node = step
    while isinstance(node, (Projection, SubqueryAlias)) and len(node.dependencies) == 1:
        node = next(iter(node.dependencies))
    return isinstance(node, Aggregate)

def _rows_by_group(
    aggregate: Aggregate,
    child_schema: DerivedSchema,
) -> Dict[Tuple[Any, ...], List[Row]]:
    group_exprs = tuple(aggregate.group or ())
    grouped: Dict[Tuple[Any, ...], List[Row]] = {}
    for row in child_schema.rows:
        key = tuple(_expr_value(expr, row) for expr in group_exprs)
        grouped.setdefault(key, []).append(row)
    if not grouped and not group_exprs:
        grouped[()] = []
    return grouped


def _has_duplicate_non_null_argument(
    argument: exp.Expression,
    rows: Sequence[Row],
) -> bool:
    values = [_expr_value(argument, row) for row in rows]
    non_null_values = [value for value in values if value is not None]
    return len(set(non_null_values)) < len(non_null_values)


def _root_result_count(root: Step, config: object) -> int:
    fetch = _root_fetch(root)
    if fetch is not None:
        return max(int(fetch or 1), 1)
    aggregate = _root_aggregate(root)
    if aggregate is not None:
        if _is_distinct_aggregate(aggregate):
            return max(int(getattr(config, "root_rows", 1) or 1), 1)
        if aggregate.group:
            return max(int(getattr(config, "groups", 1) or 1), 1)
        return 1
    return max(int(getattr(config, "root_rows", 1) or 1), 1)


def _root_fetch(root: Step) -> int | None:
    node = root
    while True:
        if isinstance(node, (Limit, Sort)) and node.fetch is not None:
            return node.fetch
        if not isinstance(node, Projection) or len(node.dependencies) != 1:
            return None
        node = next(iter(node.dependencies))


def _root_aggregate(root: Step) -> Aggregate | None:
    node = root
    while True:
        if isinstance(node, Aggregate):
            return node
        if isinstance(node, (Limit, Sort)):
            return None
        if not isinstance(node, Projection) or len(node.dependencies) != 1:
            return None
        node = next(iter(node.dependencies))


def _is_distinct_aggregate(aggregate: Aggregate) -> bool:
    return bool(aggregate.group) and not aggregate.aggregations


def _single_dependency(step: Step) -> Step:
    try:
        return next(iter(step.dependencies))
    except StopIteration as exc:
        raise ValueError(f"{step.type_name} has no dependency") from exc


def _join_inputs(join: Join) -> Tuple[Step, Step] | None:
    if join.left is not None and join.right is not None:
        return join.left, join.right
    dependencies = tuple(join.dependencies)
    if len(dependencies) == 2:
        return dependencies[0], dependencies[1]
    return None


def _schema_for(
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    step: Step,
) -> DerivedSchema:
    return cache[step][0]


def _schema_alias(schema: DerivedSchema) -> str:
    for column in schema.columns:
        if isinstance(column, exp.Column) and column.table:
            return column.table.casefold()
    table = getattr(schema, "_table", None)
    return table.name.casefold() if table is not None else ""


def _schema_table_order(instance: Instance, schema: DerivedSchema) -> int:
    table = getattr(schema, "_table", None)
    if table is None:
        return 0
    for index, ordered in enumerate(instance.schema.fk_safe_table_order()):
        if ordered.name.casefold() == table.name.casefold():
            return index
    return 0


def _identifier(value: object) -> exp.Identifier:
    if isinstance(value, exp.Identifier):
        return value
    if isinstance(value, (exp.Table, exp.Column)):
        return value.this
    return exp.to_identifier(str(value))


def _identifier_equal(left: object, right: object, dialect: str | None) -> bool:
    if dialect:
        return same_identifier(_identifier(left), _identifier(right), dialect)
    return _identifier(left).name.casefold() == _identifier(right).name.casefold()


def _filter_outcomes(schema: DerivedSchema, condition: exp.Expression) -> Set[object]:
    if not concrete_supported(condition):
        return {_UNSUPPORTED_EVALUATION}
    outcomes: Set[object] = set()
    for row in schema.rows:
        outcomes.add(concrete(condition, Environment.from_row(row)))
    return outcomes


def _false_condition(condition: exp.Expression) -> exp.Expression | None:
    conjuncts = _conjuncts(condition)
    if not conjuncts:
        return None
    atoms = [deepcopy(atom) for atom in conjuncts]
    invert_index = next(
        (index for index, atom in enumerate(atoms) if not _is_not_null_filter(atom)),
        None,
    )
    if invert_index is None:
        return None
    atoms[invert_index] = _invert_atom(atoms[invert_index])
    return _and_all(atoms)


def _without_embedded_aliases(expression: exp.Expression) -> exp.Expression:
    rewritten = deepcopy(expression)
    return rewritten.transform(
        lambda node: deepcopy(node.this) if isinstance(node, exp.Alias) else node,
    )


def _invert_atom(atom: exp.Expression) -> exp.Expression:
    if isinstance(atom, exp.GT):
        return exp.LTE(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.GTE):
        return exp.LT(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.LT):
        return exp.GTE(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.LTE):
        return exp.GT(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.EQ):
        return exp.NEQ(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    return exp.Not(this=deepcopy(atom))

def _truth_condition(
    condition: exp.Expression,
    outcome: str,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> exp.Expression | None:
    """Compile an exact SQL three-valued truth outcome for ``condition``."""

    def states(node: exp.Expression) -> tuple[exp.Expression, exp.Expression, exp.Expression]:
        if isinstance(node, exp.Paren):
            return states(node.this)
        if isinstance(node, exp.Not):
            true, false, unknown = states(node.this)
            return false, true, unknown
        if isinstance(node, exp.And):
            lt, lf, lu = states(node.this)
            rt, rf, ru = states(node.expression)
            return (
                _and_all((lt, rt)),
                _or_all((lf, rf)),
                _or_all(
                    (
                        _and_all((lu, _or_all((rt, ru)))),
                        _and_all((ru, _or_all((lt, lu)))),
                    )
                ),
            )
        if isinstance(node, exp.Or):
            lt, lf, lu = states(node.this)
            rt, rf, ru = states(node.expression)
            return (
                _or_all((lt, rt)),
                _and_all((lf, rf)),
                _or_all(
                    (
                        _and_all((lu, _or_all((rf, ru)))),
                        _and_all((ru, _or_all((lf, lu)))),
                    )
                ),
            )
        if isinstance(node, exp.Is):
            return deepcopy(node), exp.Not(this=deepcopy(node)), exp.false()

        nullable_columns: List[exp.Expression] = []
        for column in node.find_all(exp.Column):
            visible = _visible_schema_column(schema, column, dialect)
            if visible is not None and schema.nullable(visible):
                nullable_columns.append(
                    exp.Is(this=deepcopy(column), expression=exp.Null())
                )
        unknown = _or_all(nullable_columns)
        true = deepcopy(node)
        false = _and_all(
            tuple([exp.Not(this=deepcopy(unknown)), _invert_atom(node)])
        )
        return true, false, unknown

    true, false, unknown = states(condition)
    return {"true": true, "false": false, "null": unknown}.get(outcome)


def _and_all(atoms: Sequence[exp.Expression]) -> exp.Expression:
    if not atoms:
        return exp.Boolean(this=True)
    expression = atoms[0]
    for atom in atoms[1:]:
        expression = exp.And(this=expression, expression=atom)
    return expression


def _or_all(atoms: Sequence[exp.Expression]) -> exp.Expression:
    if not atoms:
        return exp.Boolean(this=False)
    expression = atoms[0]
    for atom in atoms[1:]:
        expression = exp.Or(this=expression, expression=atom)
    return expression


def _expression_in_schema_scope(
    expression: exp.Expression,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> exp.Expression:
    rewritten = deepcopy(expression)
    for column in rewritten.find_all(exp.Column):
        visible = _visible_schema_column(schema, column, dialect)
        if visible is not None and visible.table:
            column.set("table", exp.Identifier(this=visible.table))
    return rewritten


def _rewrite_demand_alias(
    demand: SchemaDemand,
    alias: str,
    output_schema: DerivedSchema,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> SchemaDemand:
    if not alias:
        return demand
    column_map = _schema_column_name_map(output_schema, child_schema, dialect)
    return SchemaDemand(
        count=demand.count,
        predicates=tuple(
            _rewrite_columns_through_schema(predicate, alias, column_map, dialect)
            for predicate in demand.predicates
        ),
        order_keys=tuple(
            _rewrite_columns_through_schema(key, alias, column_map, dialect)
            for key in demand.order_keys
        ),
        distinct=demand.distinct,
        group_demands=tuple(
            GroupDemand(
                group_index=group.group_index,
                row_count=group.row_count,
                group_key_values=tuple(
                    (
                        _rewrite_columns_through_schema(key, alias, column_map, dialect),
                        value,
                    )
                    for key, value in group.group_key_values
                ),
                row_predicates=tuple(
                    _rewrite_columns_through_schema(predicate, alias, column_map, dialect)
                    for predicate in group.row_predicates
                ),
                row_predicates_by_index=tuple(
                    (
                        index,
                        tuple(
                            _rewrite_columns_through_schema(predicate, alias, column_map, dialect)
                            for predicate in predicates
                        ),
                    )
                    for index, predicates in group.row_predicates_by_index
                ),
            )
            for group in demand.group_demands
        ),
        expression_demands=tuple(
            ExpressionDemand(
                expression=_rewrite_columns_through_schema(
                    expression_demand.expression,
                    alias,
                    column_map,
                    dialect,
                ),
                kind=expression_demand.kind,
                value=expression_demand.value,
                rank=expression_demand.rank,
                descending=expression_demand.descending,
                origin=expression_demand.origin,
            )
            for expression_demand in demand.expression_demands
        ),
        require_scalar_order_ties=demand.require_scalar_order_ties,
    )


def _schema_column_name_map(
    output_schema: DerivedSchema,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> Dict[str, exp.Column]:
    mapping: Dict[str, exp.Column] = {}
    for output in output_schema.columns:
        if not isinstance(output, exp.Column):
            continue
        child = next(
            (
                column
                for column in child_schema.columns
                if isinstance(column, exp.Column)
                and _identifier_equal(column.this, output.this, dialect)
            ),
            None,
        )
        if child is not None:
            mapping[output.name.casefold()] = child
    return mapping


def _rewrite_columns_through_schema(
    expression: exp.Expression,
    alias: str,
    column_map: Mapping[str, exp.Column],
    dialect: str | None = None,
) -> exp.Expression:
    rewritten = deepcopy(expression)
    for column in rewritten.find_all(exp.Column):
        if column.table and _identifier_equal(column.args["table"], alias, dialect):
            child = column_map.get(column.name.casefold())
            if child is not None:
                column.set("table", exp.Identifier(this=child.table) if child.table else None)
                column.set("this", child.this.copy())
    return rewritten


def _rewrite_projection_demand(
    demand: SchemaDemand,
    projection: Projection,
    output_schema: DerivedSchema,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> SchemaDemand:
    del child_schema
    expression_by_output = _projection_expression_map(projection, output_schema, dialect)
    expression_demands = tuple(
        _rewrite_projection_expression_demand(
            expression_demand,
            expression_by_output,
            dialect,
        )
        for expression_demand in demand.expression_demands
    )
    if demand.distinct:
        expression_demands += _projection_distinct_expression_demands(
            projection,
            output_schema,
            demand.count,
            dialect,
        )
    if not demand.distinct and not demand.group_demands:
        expression_demands += _projection_case_expression_demands(
            projection,
            output_schema,
            dialect,
        )
    return SchemaDemand(
        count=demand.count,
        predicates=tuple(
            _rewrite_projection_expression(predicate, expression_by_output, dialect)
            for predicate in demand.predicates
        ),
        order_keys=tuple(
            _rewrite_projection_expression(key, expression_by_output, dialect)
            for key in demand.order_keys
        ),
        distinct=False if demand.distinct else demand.distinct,
        group_demands=tuple(
            GroupDemand(
                group_index=group.group_index,
                row_count=group.row_count,
                group_key_values=tuple(
                    _rewrite_projection_group_key(key, value, expression_by_output, dialect)
                    for key, value in group.group_key_values
                ),
                row_predicates=tuple(
                    _rewrite_projection_expression(predicate, expression_by_output, dialect)
                    for predicate in group.row_predicates
                ),
                row_predicates_by_index=tuple(
                    (
                        index,
                        tuple(
                            _rewrite_projection_expression(predicate, expression_by_output, dialect)
                            for predicate in predicates
                        ),
                    )
                    for index, predicates in group.row_predicates_by_index
                ),
            )
            for group in demand.group_demands
        ),
        expression_demands=expression_demands,
        require_scalar_order_ties=demand.require_scalar_order_ties,
    )


def _rewrite_projection_group_key(
    key: exp.Expression,
    value: object,
    expression_by_output: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> Tuple[exp.Expression, object]:
    rewritten = _rewrite_projection_expression(key, expression_by_output, dialect)
    return rewritten, value


def _rewrite_projection_expression_demand(
    expression_demand: ExpressionDemand,
    expression_by_output: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> ExpressionDemand:
    expression = _rewrite_projection_expression(
        expression_demand.expression,
        expression_by_output,
        dialect,
    )
    value = expression_demand.value
    kind = expression_demand.kind
    if (
        kind == "distinct"
        and expression_demand.rank is not None
        and isinstance(expression, exp.Case)
    ):
        values = _distinct_target_values(expression, expression_demand.rank + 1)
        if values:
            value = values[expression_demand.rank % len(values)]
            kind = "predicate"
    return ExpressionDemand(
        expression=expression,
        kind=kind,
        value=value,
        rank=expression_demand.rank,
        descending=expression_demand.descending,
        origin=expression_demand.origin,
    )


def _projection_distinct_expression_demands(
    projection: Projection,
    output_schema: DerivedSchema,
    count: int,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for projected, output in zip(projection.projections, output_schema.columns):
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        for rank in range(count):
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(expression),
                    kind="distinct",
                    rank=rank,
                    origin=output.sql(dialect=dialect)
                    if isinstance(output, exp.Expression)
                    else str(output),
                )
            )
    return tuple(demands)


def _projection_case_expression_demands(
    projection: Projection,
    output_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for projected, output in zip(projection.projections, output_schema.columns):
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        case = expression if isinstance(expression, exp.Case) else expression.find(exp.Case)
        if not isinstance(case, exp.Case):
            continue
        for branch in case.args.get("ifs") or ():
            result = branch.args.get("true") if isinstance(branch, exp.If) else None
            if isinstance(result, exp.Literal):
                demands.append(
                    ExpressionDemand(
                        expression=deepcopy(case),
                        kind="predicate",
                        value=_literal_value(result),
                        origin=output.sql(dialect=dialect)
                        if isinstance(output, exp.Expression)
                        else str(output),
                    )
                )
                break
    return tuple(demands)


def _distinct_target_values(expression: exp.Expression, count: int) -> Tuple[object, ...]:
    if isinstance(expression, exp.Case):
        values: List[object] = []
        for branch in expression.args.get("ifs") or ():
            result = branch.args.get("true") if isinstance(branch, exp.If) else None
            if isinstance(result, exp.Literal):
                values.append(_literal_value(result))
        default = expression.args.get("default")
        if isinstance(default, exp.Literal):
            values.append(_literal_value(default))
        deduped = tuple(dict.fromkeys(values))
        if len(deduped) >= min(count, len(values)):
            return deduped
    return ()


def _order_expression_demands(
    order_keys: Sequence[exp.Expression],
    count: int,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for order_index, ordered in enumerate(order_keys):
        expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        desc = isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))
        order_expr = expr
        predicate_expr: exp.Expression | None = None
        if isinstance(expr, exp.Case):
            for branch in expr.args.get("ifs") or ():
                if not isinstance(branch, exp.If):
                    continue
                branch_result = branch.args.get("true")
                if branch.this is not None and branch_result is not None:
                    predicate_expr = deepcopy(branch.this)
                    order_expr = deepcopy(branch_result)
                    break
        for rank in range(count):
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(order_expr),
                    kind="order",
                    value=_order_rank_value(rank, count, desc),
                    rank=rank,
                    descending=desc,
                    origin=ordered.sql(dialect=dialect)
                    if isinstance(ordered, exp.Expression)
                    else str(ordered),
                )
            )
            if predicate_expr is not None:
                demands.append(
                    ExpressionDemand(
                        expression=deepcopy(predicate_expr),
                        kind="predicate_expression",
                        rank=rank,
                        origin=ordered.sql(dialect=dialect)
                        if isinstance(ordered, exp.Expression)
                        else str(ordered),
                    )
                )
    return tuple(demands)


def _order_expression_demands_for_sort(
    sort: Sort,
    schema: DerivedSchema,
    count: int,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    order_keys = sort.key or ()
    if not order_keys or not schema.rows:
        return _order_expression_demands(order_keys, count, dialect)
    sorted_rows = _sorted_rows_for_step(sort, schema.rows)
    fetch = sort.fetch or 1
    selected = sorted_rows[:fetch]
    if not selected:
        return _order_expression_demands(order_keys, count, dialect)
    selected_keys = {_sort_key_tuple(sort, row) for row in selected}
    selected_ids = {id(row) for row in selected}
    has_tie = any(
        id(row) not in selected_ids and _sort_key_tuple(sort, row) in selected_keys
        for row in sorted_rows
    )
    if has_tie:
        return _order_expression_demands(order_keys, count, dialect)
    reference = selected[0]
    demands: List[ExpressionDemand] = []
    for ordered in order_keys:
        expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if not _expression_uses_only_schema_columns(expr, schema, dialect):
            return _order_expression_demands(order_keys, count, dialect)
        value = _expr_value(expr, reference)
        if value is None:
            return _order_expression_demands(order_keys, count, dialect)
        for rank in range(count):
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(expr),
                    kind="literal",
                    value=value,
                    rank=rank,
                    origin=ordered.sql(dialect=dialect)
                    if isinstance(ordered, exp.Expression)
                    else str(ordered),
                )
            )
    return tuple(demands)


def _order_rank_value(rank: int, count: int, descending: bool) -> object:
    if count > 1 and rank == count - 1:
        return count if descending else 1
    return count - rank if descending else rank + 1


def _expression_demand_predicates(
    demand: ExpressionDemand,
) -> Tuple[exp.Expression, ...]:
    expression = demand.expression
    value = demand.value
    if demand.kind in {"equal", "correlated", "relation"}:
        return ()
    if demand.kind == "predicate_expression":
        return (deepcopy(expression),)
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if isinstance(expression, exp.Ordered):
        expression = expression.this
    if demand.kind == "order" and isinstance(expression, exp.Column):
        return ()
    while isinstance(expression, exp.Cast):
        expression = expression.this
    if isinstance(expression, exp.Case) and value is not None:
        predicates = _case_value_predicates(expression, value)
        if predicates:
            return predicates
    if value is None:
        return ()
    return (exp.EQ(this=deepcopy(expression), expression=_literal_for_value(value)),)


def _case_value_predicates(
    expression: exp.Case,
    value: object,
) -> Tuple[exp.Expression, ...]:
    prior_conditions: List[exp.Expression] = []
    for branch in expression.args.get("ifs") or ():
        if not isinstance(branch, exp.If):
            continue
        condition = branch.this
        result = branch.args.get("true")
        if _literal_matches(result, value):
            return tuple([deepcopy(condition)] + [_false_condition(cond) for cond in prior_conditions if _false_condition(cond) is not None])
        if result is not None and value is not None and not isinstance(result, exp.Literal):
            return tuple(
                [deepcopy(condition)]
                + [
                    false_condition
                    for prior in prior_conditions
                    if (false_condition := _false_condition(prior)) is not None
                ]
                + [exp.EQ(this=deepcopy(result), expression=_literal_for_value(value))]
            )
        if condition is not None:
            prior_conditions.append(condition)
    default = expression.args.get("default")
    if _literal_matches(default, value):
        return tuple(
            false_condition
            for condition in prior_conditions
            if (false_condition := _false_condition(condition)) is not None
        )
    if default is not None and value is not None and not isinstance(default, exp.Literal):
        return tuple(
            [
                false_condition
                for condition in prior_conditions
                if (false_condition := _false_condition(condition)) is not None
            ]
            + [exp.EQ(this=deepcopy(default), expression=_literal_for_value(value))]
        )
    return ()


def _literal_matches(expression: exp.Expression | None, value: object) -> bool:
    return isinstance(expression, exp.Literal) and _literal_value(expression) == value


def _projection_expression_map(
    projection: Projection,
    output_schema: DerivedSchema,
    dialect: str | None = None,
) -> Dict[str, exp.Expression]:
    mapping: Dict[str, exp.Expression] = {}
    for projected, output in zip(projection.projections, output_schema.columns):
        if not isinstance(output, exp.Column):
            continue
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        mapping[output.name.casefold()] = expression
        mapping[_expression_key(output, dialect)] = expression
        mapping[_expression_key(expression, dialect)] = expression
        mapping[_expression_key_without_casts(expression, dialect)] = expression
        mapping[_expression_key_without_casts(output, dialect)] = expression
    for projected in projection.projections:
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        mapping[_expression_key(expression, dialect)] = expression
        mapping[_expression_key_without_casts(expression, dialect)] = expression
        if expression.alias_or_name:
            mapping[str(expression.alias_or_name).casefold()] = expression
    return mapping


def _rewrite_projection_expression(
    expression: exp.Expression,
    expression_by_output: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> exp.Expression:
    def replace(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        replacement = expression_by_output.get(node.name.casefold())
        if replacement is None:
            replacement = expression_by_output.get(_expression_key(node, dialect))
        if replacement is None:
            replacement = expression_by_output.get(_expression_key_without_casts(node, dialect))
        if replacement is not None:
            return deepcopy(replacement)
        return node

    if isinstance(expression, exp.Column):
        return replace(expression)
    if isinstance(expression, exp.Ordered):
        rewritten = deepcopy(expression)
        rewritten.set(
            "this",
            _rewrite_projection_expression(expression.this, expression_by_output, dialect),
        )
        return rewritten
    return deepcopy(expression).transform(replace)


def _expression_key(expression: exp.Expression, dialect: str | None = None) -> str:
    if isinstance(expression, exp.Expression):
        canonical = deepcopy(expression).transform(_normalize_identifier)
        return canonical.sql(dialect=dialect, normalize=True)
    return str(expression)


def _expression_key_without_casts(
    expression: exp.Expression,
    dialect: str | None = None,
) -> str:
    rewritten = deepcopy(expression).transform(
        lambda node: node.this.copy() if isinstance(node, exp.Cast) else node
    )
    return _expression_key(rewritten, dialect)


def _normalize_identifier(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Identifier):
        node.set("this", str(node.this).casefold())
    return node


def _normalize_expression_key(text: str) -> str:
    return (
        text.casefold()
        .replace('"', "")
        .replace("`", "")
        .replace(" ", "")
    )

def _split_predicate_by_schema(
    predicate: exp.Expression,
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Expression, ...], Tuple[exp.Expression, ...]]:
    left: List[exp.Expression] = []
    right: List[exp.Expression] = []
    for atom in _conjuncts(predicate):
        columns = tuple(atom.find_all(exp.Column))
        if not columns:
            continue
        if all(_schema_has_column(left_schema, column, dialect) for column in columns):
            left.append(_expression_in_schema_scope(atom, left_schema, dialect))
        elif all(_schema_has_column(right_schema, column, dialect) for column in columns):
            right.append(_expression_in_schema_scope(atom, right_schema, dialect))
    return tuple(left), tuple(right)


def _split_group_row_predicates_by_schema(
    group: GroupDemand,
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[
    Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...],
    Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...],
]:
    left_rows: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    right_rows: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    for row_index, predicates in group.row_predicates_by_index:
        left_predicates: List[exp.Expression] = []
        right_predicates: List[exp.Expression] = []
        for predicate in predicates:
            left_part, right_part = _split_predicate_by_schema(
                predicate,
                left_schema,
                right_schema,
                dialect,
            )
            left_predicates.extend(left_part)
            right_predicates.extend(right_part)
        if left_predicates:
            left_rows.append((row_index, tuple(left_predicates)))
        if right_predicates:
            right_rows.append((row_index, tuple(right_predicates)))
    return tuple(left_rows), tuple(right_rows)


def _aggregate_case_predicates(step: Aggregate) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for aggregate in step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        for case in expression.find_all(exp.Case):
            for branch in case.args.get("ifs") or ():
                condition = branch.this if isinstance(branch, exp.If) else None
                if condition is not None:
                    predicates.append(_unwrap_aggregate_common_expr_aliases(condition))
    return tuple(predicates)


def _aggregate_case_child_predicates(step: Aggregate) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for aggregate in step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        for case in expression.find_all(exp.Case):
            for branch in case.args.get("ifs") or ():
                if isinstance(branch, exp.If):
                    predicates.append(_without_embedded_aliases(branch.this))
    return tuple(predicates)


def _unwrap_aggregate_common_expr_aliases(expression: exp.Expression) -> exp.Expression:
    rewritten = deepcopy(expression)
    for alias in list(rewritten.find_all(exp.Alias)):
        alias_name = alias.alias
        if not alias_name or "." not in alias_name:
            continue
        table_name, column_name = alias_name.split(".", 1)
        alias.replace(
            exp.Column(
                this=exp.Identifier(this=column_name),
                table=exp.Identifier(this=table_name),
            )
        )
    return rewritten


def _sort_required_child_count(
    sort: Sort,
    demand: SchemaDemand,
    child_schema: DerivedSchema,
    competitor_count: int,
    *,
    require_rank_tie_coverage: bool,
) -> int:
    fetch = sort.fetch or demand.count
    required_window = max(demand.count, fetch)
    if not sort.key:
        return max(required_window - len(child_schema.rows), 0)

    rows = _sorted_rows_for_step(sort, child_schema.rows)
    selected = rows[:fetch]
    selected_count = len(selected)
    has_competitor = len(rows) > fetch
    has_tie = _sort_has_rank_tie(sort, rows, selected)

    missing = max(required_window - selected_count, 0)
    if require_rank_tie_coverage and competitor_count and not has_competitor:
        missing += competitor_count
    if require_rank_tie_coverage and not has_tie:
        missing = max(missing, 2)
    return missing


def _sorted_rows_for_step(sort: Sort, rows: Sequence[Row]) -> List[Row]:
    ordered = list(rows)
    for key in reversed(sort.key or []):
        expr = key.this if isinstance(key, exp.Ordered) else key
        desc = isinstance(key, exp.Ordered) and bool(key.args.get("desc"))
        ordered.sort(
            key=lambda row: sql_order_key(_expr_value(expr, row)),
            reverse=desc,
        )
    return ordered


def _sort_has_rank_tie(
    sort: Sort,
    rows: Sequence[Row],
    selected: Sequence[Row],
) -> bool:
    if not selected:
        return False
    selected_ids = {id(row) for row in selected}
    selected_keys = {
        _sort_key_tuple(sort, row)
        for row in selected
    }
    return any(
        id(row) not in selected_ids and _sort_key_tuple(sort, row) in selected_keys
        for row in rows
    )


def _sort_key_tuple(sort: Sort, row: Row) -> Tuple[Any, ...]:
    return tuple(
        _expr_value(key.this if isinstance(key, exp.Ordered) else key, row)
        for key in sort.key
    )


def _sort_tie_scalar_values(
    subquery_root: Step,
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    dialect: str | None,
) -> Tuple[object, ...]:
    if not isinstance(subquery_root, Projection) or len(subquery_root.dependencies) != 1:
        return ()
    sort = next(iter(subquery_root.dependencies))
    if not isinstance(sort, Sort) or len(sort.dependencies) != 1:
        return ()
    if not subquery_root.projections:
        return ()
    sort_child = next(iter(sort.dependencies))
    child_schema = _schema_for(cache, sort_child)
    sorted_rows = _sorted_rows_for_step(sort, child_schema.rows)
    fetch = sort.fetch or 1
    selected = sorted_rows[:fetch]
    if not selected:
        return ()
    selected_keys = {_sort_key_tuple(sort, row) for row in selected}
    selected_ids = {id(row) for row in selected}
    tied_rows = [
        row
        for row in sorted_rows
        if id(row) not in selected_ids and _sort_key_tuple(sort, row) in selected_keys
    ]
    if not tied_rows:
        return ()
    projection = subquery_root.projections[0]
    expression = projection.this if isinstance(projection, exp.Alias) else projection
    if not _expression_uses_only_schema_columns(expression, child_schema, dialect):
        return ()
    values = [_expr_value(expression, row) for row in selected + tied_rows]
    return tuple(dict.fromkeys(value for value in values if value is not None))


def _split_order_keys_by_schema(
    order_keys: Tuple[exp.Expression, ...],
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Expression, ...], Tuple[exp.Expression, ...]]:
    left: List[exp.Expression] = []
    right: List[exp.Expression] = []
    for key in order_keys:
        expr = key.this if isinstance(key, exp.Ordered) else key
        columns = tuple(expr.find_all(exp.Column))
        if columns and all(_schema_has_column(left_schema, column, dialect) for column in columns):
            left.append(_expression_in_schema_scope(key, left_schema, dialect))
        elif columns and all(_schema_has_column(right_schema, column, dialect) for column in columns):
            right.append(_expression_in_schema_scope(key, right_schema, dialect))
    return tuple(left), tuple(right)


def _split_expression_demands_by_schema(
    expression_demands: Sequence[ExpressionDemand],
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[ExpressionDemand, ...], Tuple[ExpressionDemand, ...]]:
    left: List[ExpressionDemand] = []
    right: List[ExpressionDemand] = []
    for demand in expression_demands:
        expression = demand.expression
        expr = expression.this if isinstance(expression, exp.Ordered) else expression
        columns = tuple(expr.find_all(exp.Column))
        if columns and all(_schema_has_column(left_schema, column, dialect) for column in columns):
            left.append(
                ExpressionDemand(
                    expression=_expression_in_schema_scope(expression, left_schema, dialect),
                    kind=demand.kind,
                    value=demand.value,
                    rank=demand.rank,
                    descending=demand.descending,
                    origin=demand.origin,
                )
            )
        elif columns and all(_schema_has_column(right_schema, column, dialect) for column in columns):
            right.append(
                ExpressionDemand(
                    expression=_expression_in_schema_scope(expression, right_schema, dialect),
                    kind=demand.kind,
                    value=demand.value,
                    rank=demand.rank,
                    descending=demand.descending,
                    origin=demand.origin,
                )
            )
    return tuple(left), tuple(right)


def _join_relation_demands(
    predicate: exp.Expression,
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    count: int,
    dialect: str | None = None,
) -> Tuple[Tuple[ExpressionDemand, ...], Tuple[ExpressionDemand, ...]] | None:
    relation_types = {
        exp.EQ: "eq",
        exp.NEQ: "neq",
        exp.GT: "gt",
        exp.GTE: "gte",
        exp.LT: "lt",
        exp.LTE: "lte",
    }
    operator = next(
        (name for relation_type, name in relation_types.items() if isinstance(predicate, relation_type)),
        None,
    )
    if operator is None:
        return None
    left_expression = predicate.this
    right_expression = predicate.expression
    left_side = _expression_uses_only_schema_columns(left_expression, left_schema, dialect)
    right_side = _expression_uses_only_schema_columns(right_expression, right_schema, dialect)
    if not (left_side and right_side):
        reversed_left = _expression_uses_only_schema_columns(left_expression, right_schema, dialect)
        reversed_right = _expression_uses_only_schema_columns(right_expression, left_schema, dialect)
        if not (reversed_left and reversed_right):
            return None
        left_expression, right_expression = right_expression, left_expression
        operator = {
            "gt": "lt",
            "gte": "lte",
            "lt": "gt",
            "lte": "gte",
            "eq": "eq",
            "neq": "neq",
        }[operator]
    left_demands: List[ExpressionDemand] = []
    right_demands: List[ExpressionDemand] = []
    for rank in range(count):
        origin = f"join_relation:{_expression_key(predicate, dialect)}:{rank}"
        left_demands.append(
            ExpressionDemand(
                _expression_in_schema_scope(left_expression, left_schema, dialect),
                "relation",
                f"{operator}:left",
                rank,
                origin=origin,
            )
        )
        right_demands.append(
            ExpressionDemand(
                _expression_in_schema_scope(right_expression, right_schema, dialect),
                "relation",
                f"{operator}:right",
                rank,
                origin=origin,
            )
        )
    return tuple(left_demands), tuple(right_demands)


def _expression_uses_only_schema_columns(
    expression: exp.Expression,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> bool:
    columns = tuple(expression.find_all(exp.Column))
    return bool(columns) and all(
        _schema_has_column(schema, column, dialect)
        for column in columns
    )


def _schema_has_column(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> bool:
    requested_table = column.args.get("table")
    for candidate in schema.columns:
        if not isinstance(candidate, exp.Column):
            continue
        if not _identifier_equal(candidate.this, column.this, dialect):
            continue
        if (
            requested_table is not None
            and candidate.args.get("table") is not None
            and not _identifier_equal(candidate.args["table"], requested_table, dialect)
        ):
            continue
        return True
    return False


def _schema_side_for_column(
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> str | None:
    if _schema_has_column(left_schema, column, dialect):
        return "left"
    if _schema_has_column(right_schema, column, dialect):
        return "right"
    return None


def _unambiguous_schema_side_for_column(
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> str | None:
    in_left = _schema_has_column(left_schema, column, dialect)
    in_right = _schema_has_column(right_schema, column, dialect)
    if in_left == in_right:
        return None
    return "left" if in_left else "right"

def _schema_column_metadata(
    metadata: Mapping[Any, Any],
    column: exp.Expression,
    dialect: str | None = None,
) -> Any:
    while isinstance(column, exp.Cast):
        column = column.this
    if not isinstance(column, exp.Column):
        return None
    for candidate, value in metadata.items():
        if not isinstance(candidate, exp.Column):
            continue
        if not _identifier_equal(candidate.this, column.this, dialect):
            continue
        requested_table = column.args.get("table")
        candidate_table = candidate.args.get("table")
        if (
            requested_table is not None
            and candidate_table is not None
            and not _identifier_equal(candidate_table, requested_table, dialect)
        ):
            continue
        return value
    return None


def _remap_column_metadata(
    metadata: Mapping[Any, Any],
    column_pairs: Sequence[Tuple[Any, Any]],
    dialect: str | None = None,
) -> Dict[Any, Any]:
    remapped: Dict[Any, Any] = {}
    for source, output in column_pairs:
        value = _schema_column_metadata(metadata, source, dialect)
        if value is not None:
            remapped[output] = value
    return remapped


def _schema_column_values(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> Set[object]:
    values: Set[object] = set()
    visible = _visible_schema_column(schema, column, dialect)
    if visible is None:
        return values
    for row in schema.rows:
        try:
            values.add(_cell_value(row[visible]))
        except KeyError:
            continue
    return values

def _predicate_signature(
    predicates: Sequence[exp.Expression],
    dialect: str | None,
) -> tuple[str, ...]:
    return tuple(sorted(predicate.sql(dialect=dialect) for predicate in predicates))


def _group_demand_signature(
    group: GroupDemand,
    dialect: str | None,
) -> tuple[object, ...]:
    return (
        group.group_index,
        group.row_count,
        tuple(
            (key.sql(dialect=dialect), repr(value))
            for key, value in group.group_key_values
        ),
        _predicate_signature(group.row_predicates, dialect),
        tuple(
            (index, _predicate_signature(predicates, dialect))
            for index, predicates in group.row_predicates_by_index
        ),
    )


def _join_key_pairs_for_schemas(
    join: Join,
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Column, exp.Column], ...]:
    key_pairs: List[Tuple[exp.Column, exp.Column]] = []
    for left, right in join.on_keys:
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            return ()
        left_target = _unambiguous_schema_side_for_column(
            left_schema,
            right_schema,
            left,
            dialect,
        )
        right_target = _unambiguous_schema_side_for_column(
            left_schema,
            right_schema,
            right,
            dialect,
        )
        if left_target is None or right_target is None or left_target == right_target:
            return ()
        if left_target == "left":
            key_pairs.append((left, right))
        else:
            key_pairs.append((right, left))
    return tuple(key_pairs)


def _join_has_no_match(
    join: Join,
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    dialect: str | None = None,
    *,
    side: str | None = None,
) -> bool:
    inputs = _join_inputs(join)
    if inputs is None:
        return False
    left_schema = _schema_for(cache, inputs[0])
    right_schema = _schema_for(cache, inputs[1])
    left_unmatched = any(
        not any(_join_pair_matches(join, left_row, right_row) for right_row in right_schema.rows)
        for left_row in left_schema.rows
    )
    right_unmatched = any(
        not any(_join_pair_matches(join, left_row, right_row) for left_row in left_schema.rows)
        for right_row in right_schema.rows
    )
    if side == "left":
        return left_unmatched
    if side == "right":
        return right_unmatched
    return left_unmatched or right_unmatched


def _join_has_match(
    join: Join,
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
) -> bool:
    inputs = _join_inputs(join)
    if inputs is None:
        return False
    left_schema = _schema_for(cache, inputs[0])
    right_schema = _schema_for(cache, inputs[1])
    for left_row in left_schema.rows:
        for right_row in right_schema.rows:
            if _join_pair_matches(join, left_row, right_row):
                return True
    return False

def _join_pair_matches(join: Join, left_row: Row, right_row: Row) -> bool:
    expressions = [expression for pair in join.on_keys for expression in pair]
    if join.condition is not None:
        expressions.append(join.condition)
    if any(not concrete_supported(expression) for expression in expressions):
        return False
    environment = Environment(
        row={**_row_value_dict(left_row), **_row_value_dict(right_row)}
    )
    for left, right in join.on_keys:
        left_value = concrete(left, environment)
        right_value = concrete(right, environment)
        if left_value is None or right_value is None or left_value != right_value:
            return False
    return join.condition is None or concrete(join.condition, environment) is True


def _visible_schema_column(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> exp.Column | None:
    for candidate in schema.columns:
        if not isinstance(candidate, exp.Column):
            continue
        if _identifier_equal(candidate.this, column.this, dialect):
            return candidate
    return None


def _join_side_needs_parallel_rows(
    schema: DerivedSchema,
    join_key: exp.Column,
    count: int,
    dialect: str | None,
) -> bool:
    if count <= 1:
        return False
    visible = _visible_schema_column(schema, join_key, dialect) or join_key
    return schema.is_unique(visible)


def _rank_expression_predicates(
    demands: Sequence[ExpressionDemand],
    rank: int,
) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for demand in demands:
        if demand.rank is not None and demand.rank != rank:
            continue
        predicates.extend(_expression_demand_predicates(demand))
    return tuple(predicates)


def _predicate_uses_only_base_columns(
    instance: Instance,
    table: exp.Table,
    predicate: exp.Expression,
) -> bool:
    base_columns = {name.casefold() for name in instance.column_names(table)}
    for column in predicate.find_all(exp.Column):
        if not isinstance(column.this, exp.Identifier):
            return False
        if column.name.casefold() not in base_columns:
            return False
    return True


def _apply_predicate_assignments(
    instance: Instance,
    alias_rows: Dict[str, Dict[str, object]],
    aliases: Mapping[str, exp.Table],
    predicate: exp.Expression,
) -> None:
    _release_predicate_columns_for_solver(instance, alias_rows, aliases, predicate)
    for atom in _conjuncts(predicate):
        if isinstance(atom, exp.EQ):
            left = atom.this
            right = atom.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                _assign_column(instance, alias_rows, aliases, left, _literal_value(right))
            elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                _assign_column(instance, alias_rows, aliases, right, _literal_value(left))
        elif isinstance(atom, exp.Between):
            low = _numeric_value(atom.args.get("low"))
            high = _numeric_value(atom.args.get("high"))
            if isinstance(atom.this, exp.Column) and low is not None and high is not None:
                _assign_column(instance, alias_rows, aliases, atom.this, int((low + high) / 2))
        elif isinstance(atom, exp.Like):
            if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Literal):
                pattern = str(atom.expression.this)
                value = pattern[:-1] + "_witness" if pattern.endswith("%") else pattern
                _assign_column(instance, alias_rows, aliases, atom.this, value)
        elif isinstance(atom, exp.Is):
            if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Null):
                _assign_column(instance, alias_rows, aliases, atom.this, None)


def _release_predicate_columns_for_solver(
    instance: Instance,
    alias_rows: Dict[str, Dict[str, object]],
    aliases: Mapping[str, exp.Table],
    predicate: exp.Expression,
) -> None:
    for column in predicate.find_all(exp.Column):
        alias = _alias_for_column(instance, aliases, column)
        if alias is None or alias not in alias_rows:
            continue
        table = aliases[alias]
        resolved = instance.resolve_column(table, column.name)
        alias_rows[alias].pop(resolved.name, None)
        alias_rows[alias].pop(column.name, None)


def _conjuncts(expression: exp.Expression) -> Tuple[exp.Expression, ...]:
    if isinstance(expression, exp.And):
        return _conjuncts(expression.this) + _conjuncts(expression.expression)
    return (expression,)


def _alias_for_column(
    instance: Instance,
    aliases: Mapping[str, exp.Table],
    column: exp.Column,
) -> str | None:
    alias = (column.table or "").casefold()
    if alias:
        return alias if alias in aliases else None
    matches = []
    for candidate_alias, table in aliases.items():
        if column.name.casefold() in {
            name.casefold()
            for name in instance.column_names(table)
        }:
            matches.append(candidate_alias)
    return matches[0] if len(matches) == 1 else None


def _assign_column(
    instance: Instance,
    alias_rows: Dict[str, Dict[str, object]],
    aliases: Mapping[str, exp.Table],
    column: exp.Column,
    value: object,
) -> None:
    alias = _alias_for_column(instance, aliases, column)
    table = aliases.get(alias)
    if table is None or alias not in alias_rows:
        return
    alias_rows[alias][column.name] = value


def _first_assignable_columns(instance: Instance, table: exp.Table) -> Tuple[str, ...]:
    return tuple(
        column
        for column in instance.column_names(table)
        if not instance.is_unique(table, instance.resolve_column(table, column))
    )


def _coerce_value_for_column(
    instance: Instance,
    table: exp.Table,
    column_name: str,
    value: object,
) -> object:
    if value is None:
        return None
    dtype = instance.get_column_type(table, column_name)
    return coerce_literal_value(value, dtype, instance.dialect, for_equality=True)


def _literal_value(literal: exp.Literal) -> object:
    if literal.is_string:
        return literal.this
    text = str(literal.this)
    if "." not in text:
        return int(text)
    return float(text)


def _numeric_value(expression: exp.Expression | None) -> float | None:
    if isinstance(expression, exp.Literal) and not expression.is_string:
        try:
            return float(expression.this)
        except (TypeError, ValueError):
            return None
    return None


# ------------------------------------------------------------------
# Pipeline orchestrator
# ------------------------------------------------------------------

class EncodePipeline:
    """Generate rows for uncovered semantic targets in dependency order."""

    _DEFAULT_REGISTRY: Dict[type, type] = {
        TableScan: ScanEncodeStep,
        Filter: FilterEncodeStep,
        Projection: ProjectEncodeStep,
        Join: JoinEncodeStep,
        Aggregate: AggregateEncodeStep,
        Sort: SortEncodeStep,
        Limit: LimitEncodeStep,
        Union: UnionEncodeStep,
        SubqueryAlias: SubqueryAliasEncodeStep,
        Values: ValuesEncodeStep,
        EmptyRelation: EmptyRelationEncodeStep,
        Unnest: UnnestEncodeStep,
        RecursiveQuery: UnsupportedPlanEncodeStep,
        RawStep: UnsupportedPlanEncodeStep,
        Repartition: RepartitionEncodeStep,
        Distinct: DistinctEncodeStep,
        Window: WindowEncodeStep,
    }

    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        config: GenerationConfig = GenerationConfig(),
        base_row_counts: Mapping[str, int] | None = None,
        budget: GenerationBudget | None = None,
    ) -> None:
        self.plan = plan
        self.instance = instance
        self.config = config
        self.randomizer = random.Random(config.seed)
        self.budget = budget or GenerationBudget(config)
        self.dialect = plan.dialect
        self._base_row_counts = dict(base_row_counts or {})
        self.schema_failure_reason = ""
        self.demand_failure_reasons: List[str] = []
        self._correlated_bindings: Dict[JoinKeyRef, object] = {}
        self._materialized_join_group_demands: set[tuple[object, ...]] = set()
        self._pending_row_requests: List[_AtomicRowRequest] | None = None

    @property
    def solver_calls(self) -> int:
        return self.budget.solver_calls

    def _build_operator(self, step: Step) -> EncodeStep:
        for step_type, op_cls in self._DEFAULT_REGISTRY.items():
            if isinstance(step, step_type):
                return op_cls(step, instance=self.instance)
        raise ValueError(f"No operator registered for step type {type(step).__name__}")

    def _record_demand_failure(self, reason: str) -> None:
        if reason and reason not in self.demand_failure_reasons:
            self.demand_failure_reasons.append(reason)

    def _subquery_roots(self, step: Step) -> Tuple[Step, ...]:
        exprs: List[exp.Expression] = []
        if isinstance(step, Filter) and step.condition is not None:
            exprs.append(step.condition)
        if isinstance(step, Projection):
            exprs.extend(step.projections)
        if isinstance(step, Aggregate):
            exprs.extend(step.aggregations)
            exprs.extend(step.group)
        if isinstance(step, Sort):
            exprs.extend(step.key)
        if isinstance(step, Window):
            exprs.extend(step.window_exprs)
        if isinstance(step, Join):
            if step.condition is not None:
                exprs.append(step.condition)
            for left, right in step.on_keys:
                exprs.append(left)
                exprs.append(right)

        roots: list[Step] = []
        seen: set[str] = set()
        for expr in exprs:
            for ref in list(expr.find_all(ScalarSubqueryRef)):
                subquery_id = ref.subquery_id
                if subquery_id in seen:
                    continue
                inner_root = self.plan.scalar_subqueries.get(subquery_id)
                if inner_root is None:
                    raise RuntimeError(f"unsupported_scalar_subquery_ref:{subquery_id}")
                seen.add(subquery_id)
                roots.append(inner_root)
        return tuple(roots)

    def _subquery_roots_for_expression(self, expression: exp.Expression) -> Tuple[Step, ...]:
        roots: list[Step] = []
        for ref in list(expression.find_all(ScalarSubqueryRef)):
            inner_root = self.plan.scalar_subqueries.get(ref.subquery_id)
            if inner_root is None:
                raise RuntimeError(f"unsupported_scalar_subquery_ref:{ref.subquery_id}")
            roots.append(inner_root)
        return tuple(roots)

    def _ensure_scalar_expression_values(
        self,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
        *,
        require_order_ties: bool = True,
    ) -> bool:
        changed = False
        roots = self._subquery_roots_for_expression(expression)
        refs = list(expression.find_all(ScalarSubqueryRef))
        for ref, subquery_root in zip(refs, roots):
            schema = _schema_for(cache, subquery_root)
            parent_expr = _scalar_ref_parent_expr(expression, ref)
            if (
                _scalar_schema_ready_for_predicate(schema, parent_expr)
                and (
                    not require_order_ties
                    or not self._scalar_subquery_needs_order_tie(subquery_root, cache)
                )
            ):
                continue
            before = self._row_counts()
            self._lower_demand(subquery_root, self._root_demand(subquery_root), cache)
            if self._row_counts() != before:
                changed = True
        return not changed

    def _scalar_schemas_for_expression(
        self,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[DerivedSchema, ...]:
        return tuple(
            _schema_for(cache, subquery_root)
            for subquery_root in self._subquery_roots_for_expression(expression)
        )

    def _conditions_with_scalar_subquery_values(
        self,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[exp.Expression, ...]:
        refs = list(expression.find_all(ScalarSubqueryRef))
        if not refs:
            return (expression,)
        roots = self._subquery_roots_for_expression(expression)
        choices: List[Tuple[object, ...]] = []
        for ref, subquery_root in zip(refs, roots):
            schema = _schema_for(cache, subquery_root)
            parent_expr = _scalar_ref_parent_expr(expression, ref)
            if not _scalar_schema_ready_for_predicate(schema, parent_expr):
                return ()
            values = self._scalar_values_for_ref(subquery_root, schema, cache)
            if not values:
                return ()
            choices.append(values)

        conditions: List[exp.Expression] = []
        for values in product(*choices):
            rewritten = deepcopy(expression)
            for ref, value in zip(list(rewritten.find_all(ScalarSubqueryRef)), values):
                ref.replace(_literal_for_value(value))
            conditions.append(rewritten)
        return tuple(conditions)

    def _scalar_values_for_ref(
        self,
        subquery_root: Step,
        schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[object, ...]:
        values: List[object] = []
        values.extend(_sort_tie_scalar_values(subquery_root, cache, self.dialect))
        value = _scalar_schema_value(schema)
        if value is not _MISSING:
            values.append(value)
        return tuple(dict.fromkeys(value for value in values if value is not _MISSING))

    def _filter_conditions_for_step(
        self,
        step: Step,
        schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[exp.Expression, ...]:
        conditions: List[exp.Expression] = []
        seen: Set[int] = set()

        def visit(node: Step) -> None:
            if id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, Filter) and node.condition is not None:
                condition = node.condition
                if condition.find(ScalarSubqueryRef):
                    if not self._ensure_scalar_expression_values(condition, cache):
                        return
                    condition = _expression_with_scalar_subqueries(
                        condition,
                        self._scalar_schemas_for_expression(condition, cache),
                        require_ready=True,
                    )
                    if condition.find(ScalarSubqueryRef):
                        return
                conditions.append(
                    _expression_in_schema_scope(condition, schema, self.dialect)
                )
            for dependency in node.dependencies:
                visit(dependency)

        visit(step)
        return tuple(conditions)

    def _scalar_subquery_needs_order_tie(
        self,
        subquery_root: Step,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if not isinstance(subquery_root, Projection) or len(subquery_root.dependencies) != 1:
            return False
        sort = next(iter(subquery_root.dependencies))
        if not isinstance(sort, Sort) or not sort.key or sort.fetch is None:
            return False
        if not self._step_has_generated_rows(sort):
            return False
        sort_schema = _schema_for(cache, sort)
        if not sort_schema.rows:
            return False
        sorted_rows = _sorted_rows_for_step(sort, sort_schema.rows)
        selected = sorted_rows[: sort.fetch]
        return not _sort_has_rank_tie(sort, sorted_rows, selected)

    def _step_has_generated_rows(self, root: Step) -> bool:
        for scan in leaf_table_scans(root):
            table = self.instance.resolve_table(scan.table)
            if len(self.instance.get_rows(table)) > self._base_row_counts.get(table.name, 0):
                return True
        return False

    def forward(self) -> DerivedSchema:
        cache: Dict[Step, tuple[DerivedSchema, CoverageTreeNode]] = {}
        process = self._cache_processor(cache)
        schema, _tree = process(self.plan.root, "root")
        targets = self._semantic_targets(cache)
        covered = {
            target.id for target in targets if self._target_is_covered(target, cache)
        }

        for target in targets:
            if target.id in covered or self._budget_reason():
                continue

            token = self.instance.checkpoint()
            correlated_checkpoint = dict(self._correlated_bindings)
            join_group_checkpoint = set(self._materialized_join_group_demands)
            before = self._row_counts()
            translated, reason = self._attempt_target(target, cache)
            self._pending_row_requests = None
            after = self._row_counts()

            if after != before:
                cache.clear()
                schema, _tree = process(self.plan.root, "root")

            preserves_covered = all(
                self._target_is_covered(existing, cache)
                for existing in targets
                if existing.id in covered
            )
            keep = (
                translated
                and not reason
                and not self._rows_exceed_budget(after)
                and bool(schema.rows)
                and preserves_covered
                and self._target_is_covered(target, cache)
            )
            if not keep:
                self.instance.rollback(token)
                self._correlated_bindings = correlated_checkpoint
                self._materialized_join_group_demands = join_group_checkpoint
                cache.clear()
                schema, _tree = process(self.plan.root, "root")
                continue

            covered.update(
                candidate.id
                for candidate in targets
                if self._target_is_covered(candidate, cache)
            )
        return schema

    def _semantic_targets(
        self,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[SemanticTarget, ...]:
        targets: List[SemanticTarget] = []
        seen: Set[str] = set()
        scalar_roots = set(self.plan.scalar_subqueries.values())
        for step, (_schema, tree) in cache.items():
            local = tree.targets
            if step in scalar_roots:
                local += scalar_subquery_targets(step, tree.id)
            for target in local:
                if target.id not in seen:
                    seen.add(target.id)
                    targets.append(target)
        root_schema = _schema_for(cache, self.plan.root)
        for index, column in enumerate(root_schema.columns):
            expression = (
                deepcopy(column)
                if isinstance(column, exp.Expression)
                else exp.column(str(column))
            )
            targets.extend(
                (
                    SemanticTarget(
                        id=f"root.final_column{index}.duplicate",
                        step=self.plan.root,
                        step_type=self.plan.root.type_name,
                        kind="final_column_duplicate",
                        target="duplicate_non_null",
                        expression=expression,
                    ),
                    SemanticTarget(
                        id=f"root.final_column{index}.null",
                        step=self.plan.root,
                        step_type=self.plan.root.type_name,
                        kind="final_column_null",
                        target="duplicate_null",
                        expression=expression,
                    ),
                )
            )
        return tuple(targets)

    def _final_column_candidate(
        self,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> tuple[Step, exp.Expression]:
        step = self.plan.root
        candidate = deepcopy(expression)
        while True:
            reducer = (
                isinstance(step, (Limit, Distinct))
                or isinstance(step, Sort) and step.fetch is not None
                or isinstance(step, Aggregate) and _is_distinct_aggregate(step)
            )
            if not reducer or len(step.dependencies) != 1:
                return step, candidate
            child = _single_dependency(step)
            candidate = _expression_in_schema_scope(
                candidate,
                _schema_for(cache, child),
                self.dialect,
            )
            step = child

    def _lower_final_column_target(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if target.expression is None:
            return False
        step, expression = self._final_column_candidate(target.expression, cache)
        if isinstance(step, Aggregate) and not _is_distinct_aggregate(step):
            aggregate_expression = _resolve_aggregate_expression(
                expression,
                _aggregate_expression_map(step, self.dialect),
                self.dialect,
            )
            if aggregate_expression is not None:
                return self._lower_final_aggregate_column_target(
                    target,
                    step,
                    aggregate_expression,
                    cache,
                )
        origin = target.id
        if target.kind == "final_column_duplicate":
            demands = tuple(
                demand
                for rank in (0, 1)
                for demand in (
                    ExpressionDemand(
                        deepcopy(expression),
                        "equal",
                        rank=rank,
                        origin=origin,
                    ),
                    ExpressionDemand(
                        deepcopy(expression),
                        "not_null",
                        rank=rank,
                        origin=origin,
                    ),
                )
            )
        else:
            demands = tuple(
                ExpressionDemand(
                    deepcopy(expression),
                    "null",
                    rank=rank,
                    origin=origin,
                )
                for rank in (0, 1)
            )
        self._lower_demand(
            step,
            SchemaDemand(count=2, expression_demands=demands),
            cache,
        )
        root_eliminates_duplicates = isinstance(self.plan.root, Distinct) or (
            isinstance(self.plan.root, Aggregate)
            and _is_distinct_aggregate(self.plan.root)
        )
        if (
            target.kind == "final_column_null"
            and step is not self.plan.root
            and not root_eliminates_duplicates
        ):
            self._lower_demand(
                self.plan.root,
                SchemaDemand(
                    count=1,
                    predicates=(
                        exp.Is(
                            this=deepcopy(target.expression),
                            expression=exp.Null(),
                        ),
                    ),
                ),
                cache,
            )
        return True

    def _lower_final_aggregate_column_target(
        self,
        target: SemanticTarget,
        aggregate: Aggregate,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if not aggregate.group:
            return True
        child_schema = _schema_for(cache, _single_dependency(aggregate))
        group_values: List[Tuple[object, object]] = []
        for group_expression in aggregate.group:
            existing = [
                _expr_value(group_expression, row)
                for row in child_schema.rows
                if _expr_value(group_expression, row) is not None
            ]
            if existing and all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in existing
            ):
                base = max(existing)
                group_values.append((base + 1, base + 2))
            elif existing and all(isinstance(value, str) for value in existing):
                existing_set = set(existing)
                candidates: List[str] = []
                suffix = 0
                while len(candidates) < 2:
                    value = f"__parseval_group_{suffix}"
                    suffix += 1
                    if value not in existing_set:
                        candidates.append(value)
                group_values.append((candidates[0], candidates[1]))
            else:
                return True
        argument = _aggregate_arg_expression(expression)
        row_predicates: Tuple[exp.Expression, ...] = ()
        if target.kind == "final_column_null":
            if not isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
                return True
            if argument is None:
                return True
            row_predicates = (
                exp.Is(this=deepcopy(argument), expression=exp.Null()),
            )
        elif isinstance(expression, exp.Count):
            if argument is not None:
                row_predicates = (_not_null_predicate(argument),)
        elif isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
            if argument is None:
                return True
            row_predicates = (
                exp.EQ(
                    this=deepcopy(argument),
                    expression=exp.Literal.number(1),
                ),
            )
        else:
            return True
        self._lower_demand(
            aggregate,
            SchemaDemand(
                count=2,
                group_demands=tuple(
                    GroupDemand(
                        group_index=index,
                        row_count=1,
                        group_key_values=tuple(
                            (deepcopy(group_expression), values[index])
                            for group_expression, values in zip(
                                aggregate.group,
                                group_values,
                            )
                        ),
                        row_predicates=row_predicates,
                    )
                    for index in (0, 1)
                ),
            ),
            cache,
        )
        return True

    def _attempt_target(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> tuple[bool, str]:
        self._pending_row_requests = []
        self.demand_failure_reasons = []
        self.schema_failure_reason = ""

        translated, reason = self._lower_target(target, cache)
        if translated and not reason:
            reason = self._flush_pending_row_requests()
        self._pending_row_requests = None
        reason = reason or self.schema_failure_reason or next(
            iter(self.demand_failure_reasons), ""
        )
        return translated, reason

    def _cache_processor(
        self,
        cache: Dict[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ):
        def process(node: Step, path: str) -> tuple[DerivedSchema, CoverageTreeNode]:
            if node in cache:
                return cache[node]
            subquery_roots = self._subquery_roots(node)
            normal_children = tuple(
                child
                for child in ordered_dependencies(node)
                if child not in subquery_roots
            )
            child_results = tuple(
                process(child, f"{path}.dep{index}")
                for index, child in enumerate(normal_children)
            )
            subquery_results = tuple(
                process(child, f"{path}.subq{index}")
                for index, child in enumerate(subquery_roots)
            )
            subquery_schemas = []
            for schema, _tree in subquery_results:
                schema.evidence["max_rows"] = 1
                for column in schema.columns:
                    schema.uniqueness[column] = True
                subquery_schemas.append(schema)
            op = self._build_operator(node)
            step_schema = op.forward(
                *(schema for schema, _tree in child_results),
                *subquery_schemas,
            )
            tree = CoverageTreeNode(
                id=path,
                step=node,
                step_type=node.type_name,
                targets=op.semantic_targets(path),
                children=tuple(
                    tree for _schema, tree in child_results + subquery_results
                ),
            )
            step_schema.coverage_tree = tree
            cache[node] = (step_schema, tree)
            return cache[node]

        return process

    def _lower_target(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> tuple[bool, str]:
        step = target.step

        unsupported = (
            target.expression.find(UnsupportedExpression)
            if target.expression is not None
            else None
        )
        if unsupported is not None:
            return False, f"unsupported_plan_expression:{unsupported.variant}"

        if target.kind == "base_row":
            self._lower_demand(step, SchemaDemand(count=1), cache)
        elif target.kind in {"final_column_duplicate", "final_column_null"}:
            if not self._lower_final_column_target(target, cache):
                return False, f"unsupported_final_column_target:{target.kind}"
        elif target.kind == "filter" and isinstance(step, Filter):
            if target.target == "true":
                self._lower_demand(step, SchemaDemand(count=1), cache)
            else:
                child = _single_dependency(step)
                child_schema = _schema_for(cache, child)
                condition = step.condition
                if condition is None:
                    return False, "filter_without_condition"
                if condition.find(ScalarSubqueryRef):
                    conditions = self._conditions_with_scalar_subquery_values(condition, cache)
                    if not conditions:
                        return False, "unsupported_scalar_subquery_value"
                    condition = conditions[0]
                predicate = _truth_condition(
                    condition,
                    target.target,
                    child_schema,
                    self.dialect,
                )
                if predicate is None:
                    return False, f"unsupported_filter_outcome:{target.target}"
                self._lower_demand(child, SchemaDemand(count=1, predicates=(predicate,)), cache)
        elif target.kind == "case" and target.expression is not None:
            child = _single_dependency(step)
            child_schema = _schema_for(cache, child)
            predicate = self._case_target_predicate(target, child_schema)
            if predicate is None:
                return False, f"unsupported_case_outcome:{target.target}"
            self._lower_demand(child, SchemaDemand(count=1, predicates=(predicate,)), cache)
        elif target.kind == "subquery" and isinstance(step, Join):
            if not self._lower_subquery_target(target, cache):
                return False, f"unsupported_subquery_outcome:{step.subquery_kind}:{target.target}"
        elif target.kind == "scalar_subquery":
            if not self._lower_scalar_subquery_target(target, cache):
                return False, f"unsupported_scalar_subquery_outcome:{target.target}"
        elif target.kind in {"join", "semi_join", "anti_join"} and isinstance(step, Join):
            if target.target in {
                "no_match",
                "preserved_left",
                "preserved_right",
                "semi_no_match",
                "anti_no_match",
            }:
                side = (
                    "left"
                    if target.target in {"preserved_left", "semi_no_match", "anti_no_match"}
                    else "right"
                    if target.target == "preserved_right"
                    else None
                )
                if not self._materialize_join_coverage_demand(
                    step,
                    _schema_for(cache, step),
                    cache,
                    side=side,
                ):
                    return False, f"unsupported_join_outcome:{target.target}"
            else:
                self._lower_demand(step, SchemaDemand(count=1), cache)
        elif target.kind in {"projection_visible", "group_existence"}:
            self._lower_demand(step, self._root_demand(step), cache)
        elif target.kind in {
            "multi_row_aggregate_witness",
            "distinct_aggregate_witness",
            "conditional_aggregate_case",
            "null_sensitive_aggregate_witness",
        } and isinstance(step, Aggregate):
            demand = self._aggregate_target_demand(target)
            if demand is None:
                return False, f"unsupported_aggregate_target:{target.kind}"
            self._lower_demand(step, demand, cache)
        elif target.kind == "distinct" and isinstance(step, Distinct):
            child = _single_dependency(step)
            count = 2 if target.target == "duplicate_eliminated" else 1
            self._lower_demand(child, SchemaDemand(count=count), cache)
        elif target.kind == "distinct" and isinstance(step, Aggregate):
            child = _single_dependency(step)
            if target.target == "duplicate_eliminated":
                if self._queue_distinct_projection_clone(step, child, cache):
                    return True, ""
                duplicate = _aggregate_duplicate_group_demand(
                    step,
                    child,
                    _schema_for(cache, child),
                )
                if duplicate is None:
                    demands: List[ExpressionDemand] = []
                    for index, expression in enumerate(step.group or ()):
                        source = expression.this if isinstance(expression, exp.Alias) else expression
                        origin = f"distinct_projection:{target.id}:{index}"
                        demands.extend(
                            ExpressionDemand(
                                deepcopy(source), "equal", None, rank, origin=origin
                            )
                            for rank in (0, 1)
                        )
                    self._lower_demand(
                        child,
                        SchemaDemand(count=2, expression_demands=tuple(demands)),
                        cache,
                    )
                else:
                    self._lower_demand(
                        step,
                        SchemaDemand(count=1, group_demands=(duplicate,)),
                        cache,
                    )
            else:
                self._lower_demand(step, self._root_demand(step), cache)
        elif target.kind == "ordering" and isinstance(step, Sort):
            required = (step.fetch or self.config.root_rows) + self.config.order_competitors
            if target.target == "rank_tie":
                required = max(required, 2)
            self._lower_demand(
                step,
                SchemaDemand(
                    count=required,
                    order_keys=tuple(step.key or ()),
                    require_scalar_order_ties=target.target == "rank_tie",
                ),
                cache,
            )
        elif target.kind == "limit_window" and isinstance(step, Limit):
            required = (step.offset or 0) + (step.fetch or self.config.root_rows)
            self._lower_demand(step, SchemaDemand(count=required), cache)
        elif target.kind == "union" and isinstance(step, Union):
            dependencies = ordered_dependencies(step)
            if (
                target.target in {"left_only", "right_only"}
                and len(dependencies) == 2
                and set(leaf_table_scans(dependencies[0]))
                == set(leaf_table_scans(dependencies[1]))
            ):
                return False, "shared_union_input"
            if not self._lower_union_target(target, cache):
                return False, f"unsupported_union_outcome:{target.target}"
        elif target.kind == "window" and isinstance(step, Window):
            if not self._lower_window_target(target, cache):
                return False, f"unsupported_window_outcome:{target.target}"
        elif target.kind == "unsupported_plan_node":
            return False, f"unsupported_plan_node:{target.target}"
        else:
            return False, f"unsupported_semantic_target:{target.kind}:{target.target}"

        reason = self.schema_failure_reason or next(
            iter(self.demand_failure_reasons),
            "",
        )
        return True, reason

    def _queue_distinct_projection_clone(
        self,
        aggregate: Aggregate,
        child: Step,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if self._pending_row_requests is None:
            return False
        join = next(
            (
                step
                for step in _reachable_steps(child)
                if isinstance(step, Join)
                and normalize_join_type(step.join_type) in {"LEFT", "FULL"}
            ),
            None,
        )
        if join is None or join.left is None:
            return False
        scans = tuple(leaf_table_scans(join.left))
        if len(scans) != 1:
            return False
        scan = scans[0]
        aliases = {
            scan.table.name.casefold(),
            (scan.name.name if scan.name is not None else "").casefold(),
        }
        aliases.update(
            node.name.name.casefold()
            for node in _reachable_steps(join.left)
            if node.name is not None
        )
        group_columns = [
            expression
            for expression in aggregate.group or ()
            if isinstance(expression, exp.Column)
            and (expression.table or "").casefold() in aliases
        ]
        if not group_columns:
            return False
        child_schema = _schema_for(cache, child)
        anchor = next(iter(child_schema.rows), None)
        if anchor is None:
            return False
        specs = {
            self.instance.resolve_column(scan.table, column.name): _expr_value(column, anchor)
            for column in group_columns
        }
        table = self.instance.resolve_table(scan.table)
        row_specs = [dict(specs), dict(specs)]
        self._pending_row_requests.append(
            _AtomicRowRequest(
                table=table,
                row_specs=tuple(row_specs),
                predicates=((), ()),
            )
        )
        return True

    def _aggregate_target_demand(
        self,
        target: SemanticTarget,
    ) -> SchemaDemand | None:
        aggregate = target.step
        group_index = {
            "multi_row_aggregate_witness": 0,
            "distinct_aggregate_witness": 1,
            "conditional_aggregate_case": 2,
            "null_sensitive_aggregate_witness": 3,
        }.get(target.kind, 0)
        if target.kind == "multi_row_aggregate_witness":
            return SchemaDemand(
                count=1,
                group_demands=(
                    GroupDemand(
                        group_index=group_index,
                        row_count=max(self.config.rows_per_group, 2),
                    ),
                ),
            )
        if target.kind == "distinct_aggregate_witness" and target.expression is not None:
            argument = _aggregate_arg_expression(target.expression)
            if argument is None:
                return None
            origin = f"distinct_aggregate:{target.id}"
            return SchemaDemand(
                count=1,
                group_demands=(
                    GroupDemand(
                        group_index=group_index,
                        row_count=2,
                        row_predicates_by_index=(
                            (0, (_not_null_predicate(argument),)),
                            (1, (_not_null_predicate(argument),)),
                        ),
                    ),
                ),
                expression_demands=tuple(
                    ExpressionDemand(argument, "equal", origin, rank, origin=origin)
                    for rank in (0, 1)
                ),
            )
        if target.kind == "conditional_aggregate_case":
            predicates = _aggregate_case_predicates(aggregate)
            if not predicates:
                return None
            return SchemaDemand(
                count=1,
                group_demands=(
                    GroupDemand(
                        group_index=group_index,
                        row_count=len(predicates),
                        row_predicates_by_index=tuple(
                            (index, (predicate,))
                            for index, predicate in enumerate(predicates)
                        ),
                    ),
                ),
            )
        if target.kind == "null_sensitive_aggregate_witness":
            argument = _count_column_argument(aggregate)
            if argument is None:
                return None
            return SchemaDemand(
                count=1,
                group_demands=(
                    GroupDemand(
                        group_index=group_index,
                        row_count=2,
                        row_predicates_by_index=(
                            (0, (exp.Is(this=argument.copy(), expression=exp.Null()),)),
                            (1, (_not_null_predicate(argument),)),
                        ),
                    ),
                ),
            )
        return None

    def _lower_scalar_subquery_target(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        root = target.step
        if target.target == "singleton_null":
            schema = _schema_for(cache, root)
            if not schema.columns:
                return False
            self._lower_demand(
                root,
                SchemaDemand(
                    count=1,
                    predicates=(
                        exp.Is(this=deepcopy(schema.columns[0]), expression=exp.Null()),
                    ),
                ),
                cache,
            )
            return True
        sort = next(
            (step for step in _reachable_steps(root) if isinstance(step, Sort)),
            None,
        )
        if target.target == "singleton_non_null":
            schema = _schema_for(cache, root)
            predicates = (
                (_not_null_predicate(schema.columns[0]),)
                if schema.columns
                else ()
            )
            self._lower_demand(root, SchemaDemand(count=1, predicates=predicates), cache)
            return True
        if target.target in {"multi_row", "ordered_selection"}:
            if sort is None:
                self._lower_demand(root, SchemaDemand(count=2), cache)
                return target.target == "multi_row"
            self._lower_demand(
                sort,
                SchemaDemand(
                    count=max((sort.fetch or 1) + self.config.order_competitors, 2),
                    order_keys=tuple(sort.key or ()),
                    require_scalar_order_ties=False,
                ),
                cache,
            )
            return True
        return False

    def _lower_subquery_target(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        join = target.step
        inputs = _join_inputs(join)
        if inputs is None or not join.on_keys:
            return False
        left, right = inputs
        left_key, right_key = join.on_keys[0]
        if target.target == "empty":
            return self._materialize_join_coverage_demand(
                join,
                _schema_for(cache, join),
                cache,
                side="left",
            )
        if target.target == "matching":
            self._lower_demand(join, SchemaDemand(count=1), cache)
            return True
        if target.target == "non_matching":
            return self._materialize_join_coverage_demand(
                join,
                _schema_for(cache, join),
                cache,
                side="left",
            )
        if target.target == "multi_row":
            origin = f"subquery_multi:{target.id}"
            self._lower_demand(
                left,
                SchemaDemand(
                    count=1,
                    expression_demands=(
                        ExpressionDemand(left_key, "equal", origin, 0, origin=origin),
                    ),
                ),
                cache,
            )
            self._lower_demand(
                right,
                SchemaDemand(
                    count=4,
                    expression_demands=(
                        ExpressionDemand(right_key, "null", None, 0, origin=origin),
                        ExpressionDemand(right_key, "not_null", None, 1, origin=origin),
                        ExpressionDemand(right_key, "not_null", None, 2, origin=origin),
                        ExpressionDemand(right_key, "not_null", None, 3, origin=origin),
                        ExpressionDemand(right_key, "equal", origin, 1, origin=origin),
                        ExpressionDemand(right_key, "equal", origin, 2, origin=origin),
                        ExpressionDemand(right_key, "distinct", None, 1, origin=f"{origin}:u"),
                        ExpressionDemand(right_key, "distinct", None, 3, origin=f"{origin}:u"),
                    ),
                ),
                cache,
            )
            return True
        if target.target == "null_operand":
            self._lower_demand(
                left,
                SchemaDemand(
                    count=1,
                    predicates=(
                        exp.Is(this=deepcopy(left_key), expression=exp.Null()),
                    ),
                ),
                cache,
            )
            return True
        if target.target == "null_poison":
            scans = tuple(leaf_table_scans(right))
            if len(scans) != 1 or not isinstance(right_key, exp.Column):
                return False
            scan = scans[0]
            column = next(
                (
                    candidate
                    for candidate in scan.scan_projections
                    if isinstance(candidate, exp.Column)
                    and candidate.name.casefold() == right_key.name.casefold()
                ),
                None,
            )
            if column is None or not self.instance.nullable(scan.table, column):
                return False
            self._lower_demand(
                scan,
                SchemaDemand(
                    count=1,
                    predicates=(exp.Is(this=column.copy(), expression=exp.Null()),),
                ),
                cache,
            )
            self._materialize_join_coverage_demand(
                join,
                _schema_for(cache, join),
                cache,
                side="left",
            )
            return True
        return False

    def _lower_union_target(
        self,
        semantic_target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        dependencies = ordered_dependencies(semantic_target.step)
        if len(dependencies) != 2:
            return False
        left, right = dependencies
        left_schema = _schema_for(cache, left)
        right_schema = _schema_for(cache, right)
        if len(left_schema.columns) != len(right_schema.columns):
            return False
        target = semantic_target.target
        if target in {"overlap", "duplicate_preserved", "duplicate_eliminated"}:
            left_demands: List[ExpressionDemand] = []
            right_demands: List[ExpressionDemand] = []
            for index, (left_column, right_column) in enumerate(
                zip(left_schema.columns, right_schema.columns)
            ):
                origin = f"union_overlap:{semantic_target.id}:{index}"
                left_demands.append(
                    ExpressionDemand(left_column, "equal", origin, 0, origin=origin)
                )
                right_demands.append(
                    ExpressionDemand(right_column, "equal", origin, 0, origin=origin)
                )
            self._lower_demand(
                left,
                SchemaDemand(count=1, expression_demands=tuple(left_demands)),
                cache,
            )
            self._lower_demand(
                right,
                SchemaDemand(count=1, expression_demands=tuple(right_demands)),
                cache,
            )
            return True
        if target not in {"left_only", "right_only"}:
            return False
        selected = left if target == "left_only" else right
        selected_schema = left_schema if target == "left_only" else right_schema
        other_schema = right_schema if target == "left_only" else left_schema
        exclusions: List[exp.Expression] = []
        for row in other_schema.rows:
            alternatives: List[exp.Expression] = []
            for column, value in zip(
                selected_schema.columns,
                _row_values_by_position(row),
            ):
                concrete_value = _cell_value(value)
                alternatives.append(
                    exp.Not(this=exp.Is(this=deepcopy(column), expression=exp.Null()))
                    if concrete_value is None
                    else exp.NEQ(
                        this=deepcopy(column),
                        expression=_literal_for_value(concrete_value),
                    )
                )
            exclusions.append(_or_all(alternatives))
        self._lower_demand(
            selected,
            SchemaDemand(count=1, predicates=tuple(exclusions)),
            cache,
        )
        return True

    def _lower_window_target(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if not isinstance(target.step, Window) or not isinstance(target.expression, exp.Window):
            return False
        function = target.expression.this
        function_name = (
            "row_number"
            if isinstance(function, exp.RowNumber)
            else str(function.this).casefold()
            if isinstance(function, exp.Anonymous)
            else ""
        )
        if function_name not in {"row_number", "rank", "dense_rank"}:
            return False
        child = _single_dependency(target.step)
        if target.target == "row":
            self._lower_demand(child, SchemaDemand(count=1), cache)
            return True
        expressions: List[exp.Expression] = []
        if target.target == "partition_peer":
            expressions.extend(target.expression.args.get("partition_by") or ())
        elif target.target == "order_tie":
            expressions.extend(target.expression.args.get("partition_by") or ())
            order = target.expression.args.get("order")
            if isinstance(order, exp.Order):
                expressions.extend(
                    item.this if isinstance(item, exp.Ordered) else item
                    for item in order.expressions
                )
        else:
            return False
        if not expressions:
            return False
        demands: List[ExpressionDemand] = []
        for index, expression in enumerate(expressions):
            origin = f"window_peer:{target.id}:{index}"
            for rank in (0, 1):
                demands.append(
                    ExpressionDemand(expression, "equal", origin, rank, origin=origin)
                )
        self._lower_demand(
            child,
            SchemaDemand(count=2, expression_demands=tuple(demands)),
            cache,
        )
        return True

    def _flush_pending_row_requests(self) -> str:
        requests = _with_required_parent_requests(
            self.instance,
            tuple(self._pending_row_requests or ()),
        )
        if not requests:
            return ""
        batch_budget_reason = self._pending_batch_budget_reason(requests)
        if batch_budget_reason:
            return batch_budget_reason
        result, rows_by_request, _problem = _solve_atomic_row_requests(
            self.instance,
            requests,
            dialect=self.dialect,
            budget=self.budget,
            correlated_bindings=self._correlated_bindings,
        )
        if result.status != "sat":
            return f"demand_{result.status}:{result.reason or 'unknown'}"
        rows_by_table: Dict[exp.Table, List[Mapping[str, object]]] = {}
        for request, rows in zip(requests, rows_by_request):
            rows_by_table.setdefault(request.table, []).extend(rows)
        if not self._try_create_rows(
            rows_by_table,
            reason="atomic_batch_materialization_failed",
        ):
            return self.schema_failure_reason or "atomic_batch_materialization_failed"
        for request, rows in zip(requests, rows_by_request):
            self._bind_correlated_demands(rows, request.expression_demands)
        return ""

    def _pending_batch_budget_reason(
        self,
        requests: Sequence[_AtomicRowRequest],
    ) -> str:
        requested_by_table: Dict[exp.Table, List[object]] = {}
        for request in requests:
            table = self.instance.resolve_table(request.table)
            requested_by_table.setdefault(table, []).extend(request.row_specs)
        return self.budget.row_reason(self.instance, requested_by_table)

    def _case_target_predicate(
        self,
        target: SemanticTarget,
        schema: DerivedSchema,
    ) -> exp.Expression | None:
        expressions: Sequence[exp.Expression]
        if isinstance(target.step, Projection):
            expressions = target.step.projections
        elif isinstance(target.step, Aggregate):
            expressions = target.step.aggregations
        else:
            return None
        cases = _case_expressions(expressions)
        if isinstance(target.expression, exp.Case):
            case = target.expression
        else:
            expression_key = (
                _expression_key(target.expression, self.dialect)
                if target.expression is not None
                else ""
            )
            case = next(
                (
                    candidate
                    for candidate in cases
                    if any(
                        _expression_key(branch.this, self.dialect) == expression_key
                        for branch in candidate.args.get("ifs", ()) or ()
                    )
                ),
                None,
            )
        if case is None:
            return None
        base = case.this

        def condition(branch: exp.If) -> exp.Expression:
            if base is None:
                return deepcopy(branch.this)
            return exp.EQ(this=deepcopy(base), expression=deepcopy(branch.this))

        branches = tuple(case.args.get("ifs", ()) or ())
        prior_non_true: List[exp.Expression] = []
        selected_index = None
        if target.target.startswith("when_"):
            try:
                selected_index = int(target.target.split("_", 2)[1])
            except (IndexError, ValueError):
                return None
        for index, branch in enumerate(branches):
            predicate = condition(branch)
            if index == selected_index:
                return _and_all(tuple(prior_non_true + [predicate]))
            false = _truth_condition(predicate, "false", schema, self.dialect)
            unknown = _truth_condition(predicate, "null", schema, self.dialect)
            if false is None or unknown is None:
                return None
            prior_non_true.append(_or_all((false, unknown)))
        return _and_all(tuple(prior_non_true)) if target.target == "default" else None

    def _target_is_covered(
        self,
        target: SemanticTarget,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if target.kind in {"final_column_duplicate", "final_column_null"}:
            if target.expression is None:
                return False
            _step, expression = self._final_column_candidate(target.expression, cache)
            candidate_schema = _schema_for(cache, _step)
            values = [_expr_value(expression, row) for row in candidate_schema.rows]
            if target.kind == "final_column_duplicate":
                non_null = [value for value in values if value is not None]
                return len(non_null) != len(set(non_null))
            root_schema = _schema_for(cache, self.plan.root)
            root_has_null = any(
                _expr_value(target.expression, row) is None
                for row in root_schema.rows
            )
            return values.count(None) >= 2 and root_has_null
        if target.kind == "scalar_subquery":
            root_schema = _schema_for(cache, target.step)
            if target.target == "singleton_null":
                return len(root_schema.rows) == 1 and any(
                    _cell_value(next(iter(row.column_values.values()), _MISSING)) is None
                    for row in root_schema.rows
                )
            if target.target == "singleton_non_null":
                return len(root_schema.rows) == 1 and all(
                    _cell_value(next(iter(row.column_values.values()), _MISSING)) is not None
                    for row in root_schema.rows
                )
            sort = next(
                (
                    step
                    for step in _reachable_steps(target.step)
                    if isinstance(step, Sort)
                ),
                None,
            )
            candidate_schema = (
                _schema_for(cache, _single_dependency(sort))
                if sort is not None
                else root_schema
            )
            if target.target == "multi_row":
                return len(root_schema.rows) >= 2
            if target.target == "ordered_selection":
                return (
                    sort is not None
                    and bool(sort.key)
                    and bool(root_schema.rows)
                    and len(candidate_schema.rows) >= 2
                )
            return False
        if target.kind == "subquery" and isinstance(target.step, Join):
            inputs = _join_inputs(target.step)
            if inputs is None:
                return False
            left, right = inputs
            if target.target == "empty":
                return _join_has_no_match(
                    target.step,
                    cache,
                    self.dialect,
                    side="left",
                )
            if target.target == "matching":
                return _join_has_match(target.step, cache)
            if target.target == "non_matching":
                return bool(_schema_for(cache, right).rows) and _join_has_no_match(
                    target.step,
                    cache,
                    self.dialect,
                    side="left",
                )
            if target.target == "multi_row":
                if not target.step.on_keys:
                    return False
                _left_key, right_key = target.step.on_keys[0]
                values = [
                    _expr_value(right_key, row)
                    for row in _schema_for(cache, right).rows
                ]
                counts: Dict[object, int] = {}
                for value in values:
                    if value is not None:
                        counts[value] = counts.get(value, 0) + 1
                return (
                    len(values) >= 4
                    and any(value is None for value in values)
                    and any(count >= 2 for count in counts.values())
                    and any(count == 1 for count in counts.values())
                )
            if target.target == "null_operand":
                if not target.step.on_keys:
                    return False
                left_key, _right_key = target.step.on_keys[0]
                return any(
                    _expr_value(left_key, row) is None
                    for row in _schema_for(cache, left).rows
                )
            if target.target == "null_poison":
                if not target.step.on_keys:
                    return False
                _left_key, right_key = target.step.on_keys[0]
                if not isinstance(right_key, exp.Column):
                    return False
                return any(
                    _expr_value(column, row) is None
                    for scan in leaf_table_scans(right)
                    for column in scan.scan_projections
                    if isinstance(column, exp.Column)
                    and column.name.casefold() == right_key.name.casefold()
                    for row in _schema_for(cache, scan).rows
                ) and _join_has_no_match(
                    target.step,
                    cache,
                    self.dialect,
                    side="left",
                )
            return False
        if target.kind == "union" and isinstance(target.step, Union):
            dependencies = ordered_dependencies(target.step)
            if len(dependencies) != 2:
                return False
            left_schema, right_schema = (
                _schema_for(cache, dependency) for dependency in dependencies
            )
            left_keys = {
                tuple(_cell_value(value) for value in _row_values_by_position(row))
                for row in left_schema.rows
            }
            right_keys = {
                tuple(_cell_value(value) for value in _row_values_by_position(row))
                for row in right_schema.rows
            }
            if target.target == "left_only":
                return bool(left_keys - right_keys)
            if target.target == "right_only":
                return bool(right_keys - left_keys)
            if target.target == "overlap":
                return bool(left_keys & right_keys)
            output_rows = _schema_for(cache, target.step).rows
            output_keys = [
                tuple(_cell_value(value) for value in _row_values_by_position(row))
                for row in output_rows
            ]
            if target.target == "duplicate_preserved":
                return len(output_keys) != len(set(output_keys))
            if target.target == "duplicate_eliminated":
                return bool(left_keys & right_keys) and len(output_keys) == len(set(output_keys))
            return False
        if target.kind == "window" and isinstance(target.expression, exp.Window):
            child_rows = _schema_for(cache, _single_dependency(target.step)).rows
            if target.target == "row":
                return bool(child_rows)
            expressions: List[exp.Expression] = []
            if target.target == "partition_peer":
                expressions.extend(target.expression.args.get("partition_by") or ())
            elif target.target == "order_tie":
                expressions.extend(target.expression.args.get("partition_by") or ())
                order = target.expression.args.get("order")
                if isinstance(order, exp.Order):
                    expressions.extend(
                        item.this if isinstance(item, exp.Ordered) else item
                        for item in order.expressions
                    )
            keys = [
                tuple(_expr_value(expression, row) for expression in expressions)
                for row in child_rows
            ]
            return bool(expressions) and len(keys) != len(set(keys))
        if (
            target.kind == "filter"
            and target.target == "true"
            and isinstance(target.step, Filter)
            and target.step.condition is not None
            and target.step.condition.find(ScalarSubqueryRef)
        ):
            conditions = self._conditions_with_scalar_subquery_values(
                target.step.condition,
                cache,
            )
            child_rows = _schema_for(
                cache,
                _single_dependency(target.step),
            ).rows
            return bool(conditions) and all(
                any(
                    concrete(condition, Environment.from_row(row)) is True
                    for row in child_rows
                )
                for condition in conditions
            )
        if target.kind == "distinct" and isinstance(target.step, Aggregate):
            step = target.step
            child_schema = _schema_for(cache, _single_dependency(step))
            group_sources = tuple(
                expression.this if isinstance(expression, exp.Alias) else expression
                for expression in step.group
            )
            keys = [
                tuple(_expr_value(expression, row) for expression in group_sources)
                for row in child_schema.rows
            ]
            if target.target == "duplicate_eliminated":
                return len(set(keys)) < len(keys)
            return bool(keys)
        if target.kind in {"join", "semi_join", "anti_join"} and isinstance(target.step, Join):
            if target.target in {"match", "semi_match", "anti_match_excluded"}:
                return _join_has_match(target.step, cache)
            if target.target in {
                "no_match",
                "preserved_left",
                "preserved_right",
                "semi_no_match",
                "anti_no_match",
            }:
                side = (
                    "left"
                    if target.target in {"preserved_left", "semi_no_match", "anti_no_match"}
                    else "right"
                    if target.target == "preserved_right"
                    else None
                )
                return _join_has_no_match(target.step, cache, self.dialect, side=side)
            return False
        if target.kind == "ordering" and isinstance(target.step, Sort):
            step = target.step
            child_schema = _schema_for(cache, _single_dependency(step))
            rows = _sorted_rows_for_step(step, child_schema.rows)
            limit = step.fetch or self.config.root_rows
            selected = rows[:limit]
            if target.target == "selected":
                return bool(selected)
            if target.target == "excluded_competitor":
                return len(rows) > limit
            if target.target == "rank_tie":
                return _sort_has_rank_tie(step, rows, selected)
            return False
        if target.kind == "limit_window" and isinstance(target.step, Limit):
            step = target.step
            rows = _schema_for(cache, _single_dependency(step)).rows
            offset = step.offset or 0
            fetch = step.fetch or self.config.root_rows
            if target.target == "selected":
                return bool(rows[offset : offset + fetch])
            if target.target == "offset_skipped":
                return offset > 0 and len(rows[:offset]) == offset
            return False
        if target.kind in {"filter", "case"} and target.expression is not None:
            step = target.step
            if not step.dependencies:
                return False
            child_schema = _schema_for(cache, _single_dependency(step))
            if target.kind == "case" and target.target == "default":
                case = target.expression
                if not isinstance(case, exp.Case):
                    return False
                conditions = tuple(
                    _expression_in_schema_scope(branch.this, child_schema, self.dialect)
                    for branch in case.args.get("ifs", ()) or ()
                )
                return any(
                    all(concrete(condition, Environment.from_row(row)) is not True for condition in conditions)
                    for row in child_schema.rows
                )
            expression = _expression_in_schema_scope(
                _without_embedded_aliases(target.expression),
                child_schema,
                self.dialect,
            )
            if expression.find(ScalarSubqueryRef):
                subquery_schemas = self._scalar_schemas_for_expression(expression, cache)
                expression = _expression_with_scalar_subqueries(
                    expression,
                    subquery_schemas,
                    require_ready=True,
                )
                if expression.find(ScalarSubqueryRef):
                    return False
            outcomes = {
                concrete(expression, Environment.from_row(row))
                for row in child_schema.rows
            }
            desired = {
                "true": True,
                "false": False,
                "null": None,
            }.get(target.target, True)
            return desired in outcomes
        if target.kind == "base_row" and isinstance(target.step, TableScan):
            return bool(_schema_for(cache, target.step).rows)
        if target.kind == "projection_visible":
            return bool(_schema_for(cache, target.step).rows)
        if target.kind == "group_existence" and isinstance(target.step, Aggregate):
            return bool(_schema_for(cache, target.step).rows)
        if target.kind == "multi_row_aggregate_witness" and isinstance(target.step, Aggregate):
            child = _schema_for(cache, _single_dependency(target.step))
            return any(
                len(rows) >= 2
                for rows in _rows_by_group(target.step, child).values()
            )
        if target.kind == "distinct_aggregate_witness" and target.expression is not None:
            child = _schema_for(cache, _single_dependency(target.step))
            aggregate = target.expression
            source = aggregate.this if isinstance(aggregate, exp.Count) else None
            if isinstance(source, exp.Distinct) and len(source.expressions) == 1:
                return any(
                    _has_duplicate_non_null_argument(source.expressions[0], rows)
                    for rows in _rows_by_group(target.step, child).values()
                )
            return False
        if target.kind == "conditional_aggregate_case" and isinstance(target.step, Aggregate):
            child = _schema_for(cache, _single_dependency(target.step))
            predicates = _aggregate_case_child_predicates(target.step)
            return bool(predicates) and all(
                True in _filter_outcomes(
                    child,
                    _expression_in_schema_scope(predicate, child, self.dialect),
                )
                for predicate in predicates
            )
        if target.kind == "null_sensitive_aggregate_witness" and isinstance(target.step, Aggregate):
            child = _schema_for(cache, _single_dependency(target.step))
            argument = _count_column_argument(target.step)
            return argument is not None and any(
                any(_expr_value(argument, row) is None for row in rows)
                and any(_expr_value(argument, row) is not None for row in rows)
                for rows in _rows_by_group(target.step, child).values()
            )
        return False

    def _budget_reason(self) -> str:
        counts = self._row_counts()
        if self.solver_calls >= self.config.max_solver_calls:
            return "solver_call_budget_exhausted"
        if sum(counts.values()) >= self.config.max_total_rows:
            return "total_row_budget_exhausted"
        if any(count >= self.config.max_rows_per_table for count in counts.values()):
            return "table_row_budget_exhausted"
        return ""

    def _rows_exceed_budget(self, counts: Mapping[exp.Table, int]) -> bool:
        return (
            sum(counts.values()) > self.config.max_total_rows
            or any(count > self.config.max_rows_per_table for count in counts.values())
        )

    def _row_counts(self) -> Dict[exp.Table, int]:
        return {
            table: len(self.instance.get_rows(table))
            for table in self.instance.schema.fk_safe_table_order()
        }

    def _root_demand(self, root: Step) -> SchemaDemand:
        count = _root_result_count(root, self.config)
        aggregate = _root_aggregate(root)
        group_demands: Tuple[GroupDemand, ...] = ()
        if aggregate is not None:
            rows_per_group = self.config.rows_per_group
            if aggregate.group:
                group_count = max(self.config.groups, count)
                if _is_distinct_aggregate(aggregate):
                    group_demands = tuple(
                        GroupDemand(
                            group_index=index,
                            row_count=1,
                        )
                        for index in range(max(group_count, 1))
                    )
                else:
                    group_demands = _aggregate_group_demands(
                        group_count=group_count,
                        rows_per_group=rows_per_group,
                    )
            else:
                group_demands = (
                    GroupDemand(
                        group_index=0,
                        row_count=rows_per_group,
                    ),
                )
        return SchemaDemand(count=count, group_demands=group_demands)

    def _lower_demand(
        self,
        node: Step,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        required = max(
            demand.count,
            sum(group.row_count for group in demand.group_demands),
        )
        if required > self.config.max_rows_per_table or required > self.config.max_total_rows:
            self._record_demand_failure(
                "row_budget_exhausted:"
                f"required={required},per_table={self.config.max_rows_per_table},"
                f"total={self.config.max_total_rows}"
            )
            return
        if self._reuse_provenance_prefix(node, demand, cache):
            return
        op = self._build_operator(node)
        child_schemas = tuple(_schema_for(cache, child) for child in node.dependencies)
        op.lower_demand(
            demand,
            _schema_for(cache, node),
            child_schemas,
            DemandContext(self, cache),
        )

    def _reuse_provenance_prefix(
        self,
        node: Step,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if not demand.group_demands:
            return False
        schema = _schema_for(cache, node)
        for group in demand.group_demands:
            if group.row_count < 2 or not group.group_key_values:
                continue
            anchor = next(
                (
                    row
                    for row in schema.rows
                    if all(
                        _expr_value(expression, row) == value
                        for expression, value in group.group_key_values
                    )
                ),
                None,
            )
            if anchor is None:
                continue
            provenance = schema.row_provenance.get(anchor.rowid, {})
            for table_name, rowids in provenance.items():
                table = self.instance.resolve_table(table_name)
                if self.instance.database_constraints(table).uniqueness_groups():
                    continue
                sources = [
                    row
                    for rowid in rowids
                    for row in self.instance.get_rows(table)
                    if row.rowid == rowid
                ]
                if not sources:
                    continue
                missing = max(group.row_count - len(rowids), 0)
                if missing == 0:
                    return True
                source = sources[0]
                row = {
                    column.name: _cell_value(value)
                    for column, value in source.items()
                }
                self._submit_row_request(
                    table,
                    tuple(dict(row) for _ in range(missing)),
                    tuple(() for _ in range(missing)),
                )
                return True
        return False

    def _try_create_rows(
        self,
        rows_by_table: Mapping[exp.Table, Sequence[Mapping[str, object]]],
        *,
        reason: str,
    ) -> bool:
        token = self.instance.checkpoint()
        try:
            self.instance.create_rows(rows_by_table)
            return True
        except (DomainError, KeyError) as exc:
            self.instance.rollback(token)
            self.schema_failure_reason = reason
            logger.info("%s:%s", reason, exc)
            return False

    def _bind_correlated_demands(
        self,
        rows: Sequence[Mapping[str, object]],
        expression_demands: Sequence[ExpressionDemand],
    ) -> None:
        for demand in expression_demands:
            if (
                demand.kind != "correlated"
                or not isinstance(demand.value, JoinKeyRef)
                or demand.rank is None
                or demand.value in self._correlated_bindings
                or demand.rank < 0
                or demand.rank >= len(rows)
            ):
                continue
            value = _solved_expression_value(rows[demand.rank], demand.expression)
            if value is not _MISSING and value is not None:
                self._correlated_bindings[demand.value] = value

    def _materialize_having_demand(
        self,
        step: Filter,
        aggregate: Aggregate,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if step.condition is None:
            return False
        group_count = max(self.config.groups, demand.count, 1)
        default_row_count = self.config.rows_per_group
        group_demands: List[GroupDemand] = list(demand.group_demands)
        if not group_demands:
            group_demands.extend(
                _having_group_demands(
                    step.condition,
                    aggregate,
                    group_count=group_count,
                    default_row_count=default_row_count,
                    pass_group=True,
                    start_index=0,
                    dialect=self.dialect,
                )
            )
        if not group_demands:
            return False
        self._lower_demand(
            aggregate,
            SchemaDemand(
                count=max(demand.count, sum(group.row_count for group in group_demands)),
                predicates=demand.predicates,
                order_keys=demand.order_keys,
                distinct=demand.distinct,
                group_demands=tuple(group_demands),
                require_scalar_order_ties=demand.require_scalar_order_ties,
            ),
            cache,
        )
        return True

    def _materialize_join_demand(
        self,
        join: Join,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        inputs = _join_inputs(join)
        if inputs is None:
            for dependency in tuple(join.dependencies):
                self._lower_demand(dependency, demand, cache)
            return
        left_dep, right_dep = inputs
        left_schema = _schema_for(cache, left_dep)
        right_schema = _schema_for(cache, right_dep)
        if demand.group_demands:
            self._materialize_join_group_demands(
                join,
                demand,
                left_dep,
                left_schema,
                right_dep,
                right_schema,
                cache,
            )
            return
        self._lower_correlated_join_demand(
            join,
            demand,
            left_dep,
            left_schema,
            right_dep,
            right_schema,
            cache,
        )

    def _lower_correlated_join_demand(
        self,
        join: Join,
        demand: SchemaDemand,
        left_dep: Step,
        left_schema: DerivedSchema,
        right_dep: Step,
        right_schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        key_pairs = _join_key_pairs_for_schemas(
            join,
            left_schema,
            right_schema,
            self.dialect,
        )
        join_schema = _schema_for(cache, join)
        missing = max(demand.count - len(join_schema.rows), 0)
        if missing <= 0:
            return
        relation_demands = (
            _join_relation_demands(
                join.condition,
                left_schema,
                right_schema,
                missing,
                self.dialect,
            )
            if join.condition is not None
            else None
        )
        if not key_pairs and relation_demands is None:
            return

        left_predicates: List[exp.Expression] = []
        right_predicates: List[exp.Expression] = []
        if join.condition is not None:
            left_part, right_part = _split_predicate_by_schema(
                join.condition,
                left_schema,
                right_schema,
                self.dialect,
            )
            left_predicates.extend(left_part)
            right_predicates.extend(right_part)

        demand_predicates: List[exp.Expression] = []
        for predicate in demand.predicates:
            if predicate.find(ScalarSubqueryRef):
                if not self._ensure_scalar_expression_values(
                    predicate,
                    cache,
                    require_order_ties=demand.require_scalar_order_ties,
                ):
                    return
                predicate = _expression_with_scalar_subqueries(
                    predicate,
                    self._scalar_schemas_for_expression(predicate, cache),
                    require_ready=True,
                )
                if predicate.find(ScalarSubqueryRef):
                    continue
            demand_predicates.append(predicate)
        for predicate in demand_predicates:
            left_part, right_part = _split_predicate_by_schema(
                predicate,
                left_schema,
                right_schema,
                self.dialect,
            )
            left_predicates.extend(left_part)
            right_predicates.extend(right_part)
        left_predicates.extend(
            self._filter_conditions_for_step(left_dep, left_schema, cache)
        )
        right_predicates.extend(
            self._filter_conditions_for_step(right_dep, right_schema, cache)
        )

        left_expression_demands, right_expression_demands = _split_expression_demands_by_schema(
            demand.expression_demands,
            left_schema,
            right_schema,
            self.dialect,
        )
        left_order, right_order = _split_order_keys_by_schema(
            demand.order_keys,
            left_schema,
            right_schema,
            self.dialect,
        )
        left_demands: List[ExpressionDemand] = list(left_expression_demands)
        right_demands: List[ExpressionDemand] = list(right_expression_demands)
        if relation_demands is not None:
            left_demands.extend(relation_demands[0])
            right_demands.extend(relation_demands[1])
        left_count = 0
        right_count = 0

        def add_pair(
            pair_rank: int,
            values: Tuple[object, ...] | None,
            materialize_left: bool,
            materialize_right: bool,
        ) -> None:
            nonlocal left_count, right_count
            refs = tuple(
                JoinKeyRef(
                    origin=(left_key, right_key),
                    pair_rank=pair_rank,
                    key_index=key_index,
                )
                for key_index, (left_key, right_key) in enumerate(key_pairs)
            )
            if values is not None:
                for ref, value in zip(refs, values):
                    self._correlated_bindings[ref] = value
            if materialize_left:
                local_rank = left_count
                left_count += 1
                for ref, (left_key, _right_key) in zip(refs, key_pairs):
                    left_demands.append(
                        ExpressionDemand(
                            expression=_expression_in_schema_scope(
                                left_key,
                                left_schema,
                                self.dialect,
                            ),
                            kind="correlated",
                            value=ref,
                            rank=local_rank,
                            origin="join_key",
                        )
                    )
            if materialize_right:
                local_rank = right_count
                right_count += 1
                for ref, (_left_key, right_key) in zip(refs, key_pairs):
                    right_demands.append(
                        ExpressionDemand(
                            expression=_expression_in_schema_scope(
                                right_key,
                                right_schema,
                                self.dialect,
                            ),
                            kind="correlated",
                            value=ref,
                            rank=local_rank,
                            origin="join_key",
                        )
                    )

        for pair_rank in range(missing):
            add_pair(pair_rank, None, True, True)

        child_demands = []
        if left_count:
            child_demands.append(
                (
                    left_dep,
                    left_schema,
                    SchemaDemand(
                        count=left_count,
                        predicates=tuple(left_predicates),
                        order_keys=left_order,
                        distinct=demand.distinct,
                        expression_demands=tuple(left_demands),
                        require_scalar_order_ties=demand.require_scalar_order_ties,
                    ),
                )
            )
        if right_count:
            child_demands.append(
                (
                    right_dep,
                    right_schema,
                    SchemaDemand(
                        count=right_count,
                        predicates=tuple(right_predicates),
                        order_keys=right_order,
                        distinct=demand.distinct,
                        expression_demands=tuple(right_demands),
                        require_scalar_order_ties=demand.require_scalar_order_ties,
                    ),
                )
            )
        for dependency, _schema, child_demand in sorted(
            child_demands,
            key=lambda item: _schema_table_order(self.instance, item[1]),
        ):
            self._lower_demand(dependency, child_demand, cache)

    def _materialize_join_group_demands(
        self,
        join: Join,
        demand: SchemaDemand,
        left_dep: Step,
        left_schema: DerivedSchema,
        right_dep: Step,
        right_schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        for group in demand.group_demands:
            left_predicates: List[exp.Expression] = []
            right_predicates: List[exp.Expression] = []
            left_expression_demands: List[ExpressionDemand] = []
            right_expression_demands: List[ExpressionDemand] = []
            if join.condition is not None:
                left_part, right_part = _split_predicate_by_schema(
                    join.condition,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
                left_predicates.extend(left_part)
                right_predicates.extend(right_part)
            for predicate in demand.predicates + group.row_predicates:
                left_part, right_part = _split_predicate_by_schema(
                    predicate,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
                left_predicates.extend(left_part)
                right_predicates.extend(right_part)

            left_predicates.extend(
                self._filter_conditions_for_step(left_dep, left_schema, cache)
            )
            right_predicates.extend(
                self._filter_conditions_for_step(right_dep, right_schema, cache)
            )
            left_row_predicates_by_index, right_row_predicates_by_index = (
                _split_group_row_predicates_by_schema(
                    group,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
            )

            left_group_keys: List[Tuple[exp.Expression, object]] = []
            right_group_keys: List[Tuple[exp.Expression, object]] = []
            for key, value in group.group_key_values:
                columns = _columns_including_self(key)
                if columns and all(
                    _schema_has_column(left_schema, column, self.dialect)
                    for column in columns
                ):
                    scoped_key = _expression_in_schema_scope(
                        key,
                        left_schema,
                        self.dialect,
                    )
                    scoped_value = _predicate_equality_value(
                        left_predicates,
                        scoped_key,
                        self.dialect,
                    )
                    if scoped_value is _MISSING:
                        scoped_value = value
                    scoped_predicate = exp.EQ(
                        this=deepcopy(scoped_key),
                        expression=_literal_for_value(scoped_value),
                    )
                    left_group_keys.append((scoped_key, scoped_value))
                    left_predicates.append(
                        _expression_in_schema_scope(
                            scoped_predicate,
                            left_schema,
                            self.dialect,
                        )
                    )
                elif columns and all(
                    _schema_has_column(right_schema, column, self.dialect)
                    for column in columns
                ):
                    scoped_key = _expression_in_schema_scope(
                        key,
                        right_schema,
                        self.dialect,
                    )
                    scoped_value = _predicate_equality_value(
                        right_predicates,
                        scoped_key,
                        self.dialect,
                    )
                    if scoped_value is _MISSING:
                        scoped_value = value
                    scoped_predicate = exp.EQ(
                        this=deepcopy(scoped_key),
                        expression=_literal_for_value(scoped_value),
                    )
                    right_group_keys.append((scoped_key, scoped_value))
                    right_predicates.append(
                        _expression_in_schema_scope(
                            scoped_predicate,
                            right_schema,
                            self.dialect,
                        )
                    )
            left_count = 1 if left_group_keys and not right_group_keys else group.row_count
            right_count = 1 if right_group_keys and not left_group_keys else group.row_count
            if group.row_count > 1 and group.group_key_values:
                left_count = group.row_count
                right_count = group.row_count
            demand_key = (
                id(join),
                tuple(
                    sorted(
                        (table.sql(dialect=self.dialect), count)
                        for table, count in self._row_counts().items()
                    )
                ),
                _group_demand_signature(group, self.dialect),
                tuple(
                    (
                        key_left.sql(dialect=self.dialect),
                        key_right.sql(dialect=self.dialect),
                    )
                    for key_left, key_right in join.on_keys
                ),
                _predicate_signature(left_predicates, self.dialect),
                _predicate_signature(right_predicates, self.dialect),
            )
            if demand_key in self._materialized_join_group_demands:
                continue
            self._materialized_join_group_demands.add(demand_key)
            for key_index, (left, right) in enumerate(join.on_keys):
                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    continue
                left_target = _schema_side_for_column(
                    left_schema,
                    right_schema,
                    left,
                    self.dialect,
                )
                right_target = _schema_side_for_column(
                    left_schema,
                    right_schema,
                    right,
                    self.dialect,
                )
                left_side_schema = left_schema if left_target == "left" else right_schema
                right_side_schema = left_schema if right_target == "left" else right_schema
                if (
                    left_group_keys
                    and not right_group_keys
                    and _join_side_needs_parallel_rows(
                        right_side_schema,
                        right,
                        group.row_count,
                        self.dialect,
                    )
                ):
                    left_count = group.row_count
                if (
                    right_group_keys
                    and not left_group_keys
                    and _join_side_needs_parallel_rows(
                        left_side_schema,
                        left,
                        group.row_count,
                        self.dialect,
                    )
                ):
                    right_count = group.row_count
                join_value_count = group.row_count if left_count > 1 and right_count > 1 else 1
                for row_index in range(join_value_count):
                    ref = JoinKeyRef(
                        origin=(left, right),
                        pair_rank=group.group_index * max(group.row_count, 1) + row_index,
                        key_index=key_index,
                    )
                    local_rank = row_index if join_value_count > 1 else 0
                    if left_target == "left":
                        left_expression_demands.append(
                            ExpressionDemand(left, "correlated", ref, local_rank, origin="join_key")
                        )
                    elif left_target == "right":
                        right_expression_demands.append(
                            ExpressionDemand(left, "correlated", ref, local_rank, origin="join_key")
                        )
                    if right_target == "left":
                        left_expression_demands.append(
                            ExpressionDemand(right, "correlated", ref, local_rank, origin="join_key")
                        )
                    elif right_target == "right":
                        right_expression_demands.append(
                            ExpressionDemand(right, "correlated", ref, local_rank, origin="join_key")
                        )
            left_order, right_order = _split_order_keys_by_schema(
                demand.order_keys,
                left_schema,
                right_schema,
                self.dialect,
            )
            child_demands = [
                (
                    left_dep,
                    left_schema,
                    SchemaDemand(
                        count=max(left_count, 1),
                        predicates=tuple(left_predicates),
                        order_keys=left_order,
                        distinct=demand.distinct,
                        require_scalar_order_ties=demand.require_scalar_order_ties,
                        group_demands=(
                            GroupDemand(
                                group_index=group.group_index,
                                row_count=max(left_count, 1),
                                group_key_values=tuple(left_group_keys),
                                row_predicates=(),
                                row_predicates_by_index=left_row_predicates_by_index,
                            ),
                        ),
                        expression_demands=tuple(left_expression_demands),
                    ),
                ),
                (
                    right_dep,
                    right_schema,
                    SchemaDemand(
                        count=max(right_count, 1),
                        predicates=tuple(right_predicates),
                        order_keys=right_order,
                        distinct=demand.distinct,
                        require_scalar_order_ties=demand.require_scalar_order_ties,
                        group_demands=(
                            GroupDemand(
                                group_index=group.group_index,
                                row_count=max(right_count, 1),
                                group_key_values=tuple(right_group_keys),
                                row_predicates=(),
                                row_predicates_by_index=right_row_predicates_by_index,
                            ),
                        ),
                        expression_demands=tuple(right_expression_demands),
                    ),
                ),
            ]
            for dependency, _schema, child_demand in sorted(
                child_demands,
                key=lambda item: _schema_table_order(self.instance, item[1]),
            ):
                self._lower_demand(dependency, child_demand, cache)

    def _materialize_table_demand(
        self,
        schema: DerivedSchema,
        table: exp.Table,
        demand: SchemaDemand,
    ) -> None:
        row_specs: List[Dict[str, object]] = []
        row_predicates: List[Tuple[exp.Expression, ...]] = []
        alias = _schema_alias(schema)
        aliases = {alias: table}
        expression_demands = list(
            _expand_group_key_expression_demands(
                demand.expression_demands,
                demand.group_demands,
            )
        )
        if demand.distinct:
            distinct_columns = _first_assignable_columns(self.instance, table)
            if distinct_columns:
                expression = exp.column(distinct_columns[0], table=alias or None)
                origin = f"distinct:{table.name}:{distinct_columns[0]}"
                expression_demands.extend(
                    ExpressionDemand(
                        expression=expression,
                        kind="distinct",
                        rank=rank,
                        origin=origin,
                    )
                    for rank in range(demand.count)
                )
        if demand.group_demands:
            for group in demand.group_demands:
                for row_index in range(max(group.row_count, 1)):
                    alias_rows = {alias: {}}
                    group_key_predicates = _group_key_predicates(
                        group,
                    )
                    predicates = (
                        demand.predicates
                        + _row_predicates_for_group_row(group, row_index)
                        + group_key_predicates
                        + _rank_expression_predicates(
                            demand.expression_demands,
                            group.group_index,
                        )
                    )
                    predicates = tuple(
                        predicate
                        for predicate in predicates
                        if _predicate_uses_only_base_columns(
                            self.instance,
                            table,
                            predicate,
                        )
                    )
                    for predicate in (_and_all(predicates),):
                        _apply_predicate_assignments(
                            self.instance,
                            alias_rows,
                            aliases,
                            predicate,
                        )
                    row_specs.append(alias_rows[alias])
                    row_predicates.append(tuple(predicates))
            if row_specs:
                self._submit_row_request(
                    table,
                    row_specs,
                    row_predicates,
                    expression_demands=tuple(expression_demands),
                )
            return
        for rank in range(demand.count):
            alias_rows = {alias: {}}
            predicates = demand.predicates + _rank_expression_predicates(
                expression_demands,
                rank,
            )
            predicates = tuple(
                predicate
                for predicate in predicates
                if _predicate_uses_only_base_columns(
                    self.instance,
                    table,
                    predicate,
                )
            )
            for predicate in (_and_all(predicates),):
                _apply_predicate_assignments(
                    self.instance,
                    alias_rows,
                    aliases,
                    predicate,
                )
            row_specs.append(alias_rows[alias])
            row_predicates.append(tuple(predicates))
        if row_specs:
            self._submit_row_request(
                table,
                row_specs,
                row_predicates,
                expression_demands=tuple(expression_demands),
            )

    def _submit_row_request(
        self,
        table: exp.Table,
        row_specs: Sequence[Mapping[object, object]],
        predicates: Sequence[Sequence[exp.Expression]],
        *,
        expression_demands: Sequence[ExpressionDemand] = (),
    ) -> None:
        request = _AtomicRowRequest(
            table=self.instance.resolve_table(table),
            row_specs=tuple(row_specs),
            predicates=tuple(tuple(items) for items in predicates),
            expression_demands=tuple(expression_demands),
        )
        if self._pending_row_requests is not None:
            self._pending_row_requests.append(request)
            return
        raise RuntimeError("row_request_outside_target_attempt")

    def _materialize_sort_demand(
        self,
        step: Sort,
        demand: SchemaDemand,
        child_schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        competitor_count = self.config.order_competitors
        is_root_unbounded_sort = step is self.plan.root and step.fetch is None
        require_rank_tie_coverage = (
            not is_root_unbounded_sort
            and demand.require_scalar_order_ties
        )
        if not require_rank_tie_coverage:
            competitor_count = 0

        required_count = _sort_required_child_count(
            step,
            demand,
            child_schema,
            competitor_count,
            require_rank_tie_coverage=require_rank_tie_coverage,
        )
        if required_count <= 0:
            return False
        child = _single_dependency(step)
        child_count = required_count
        order_expression_demands: Tuple[ExpressionDemand, ...] = ()
        if require_rank_tie_coverage:
            order_expression_demands = _order_expression_demands_for_sort(
                step,
                child_schema,
                required_count,
                self.dialect,
            )
        before = self._row_counts()
        self._lower_demand(
            child,
            SchemaDemand(
                count=child_count,
                predicates=demand.predicates,
                order_keys=tuple(step.key or ()) + demand.order_keys,
                distinct=demand.distinct,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands + order_expression_demands,
                require_scalar_order_ties=demand.require_scalar_order_ties,
            ),
            cache,
        )
        return self._row_counts() != before

    def _materialize_join_coverage_demand(
        self,
        step: Join,
        schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
        *,
        side: str | None = None,
    ) -> bool:
        if schema.rows and _join_has_no_match(step, cache, self.dialect, side=side):
            return False
        inputs = _join_inputs(step)
        if inputs is None:
            return False
        left_dep, right_dep = inputs
        left_schema = _schema_for(cache, left_dep)
        right_schema = _schema_for(cache, right_dep)
        if not step.on_keys and step.condition is not None:
            inverse = _false_condition(step.condition)
            relation_demands = (
                _join_relation_demands(
                    inverse,
                    left_schema,
                    right_schema,
                    1,
                    self.dialect,
                )
                if inverse is not None
                else None
            )
            if relation_demands is None:
                return False
            self._lower_demand(
                left_dep,
                SchemaDemand(count=1, expression_demands=relation_demands[0]),
                cache,
            )
            self._lower_demand(
                right_dep,
                SchemaDemand(count=1, expression_demands=relation_demands[1]),
                cache,
            )
            return True
        if not step.on_keys:
            return False
        left_key, right_key = step.on_keys[0]
        if not isinstance(left_key, exp.Column) or not isinstance(right_key, exp.Column):
            return False
        candidates = [
            (left_dep, left_schema, left_key, right_schema, right_key),
            (right_dep, right_schema, right_key, left_schema, left_key),
        ]
        if side == "left":
            candidates = candidates[:1]
        elif side == "right":
            candidates = candidates[1:]
        target_dep, target_schema, target_key, other_schema, other_key = min(
            candidates,
            key=lambda item: self._join_key_is_foreign_key_source(item[1], item[2]),
        )
        forbidden = _schema_column_values(other_schema, other_key, self.dialect)
        predicates: List[exp.Expression] = [_not_null_predicate(target_key)]
        predicates.extend(
            exp.NEQ(
                this=deepcopy(target_key),
                expression=_literal_for_value(value),
            )
            for value in forbidden
            if value is not None
        )
        self._lower_demand(
            target_dep,
            SchemaDemand(count=1, predicates=tuple(predicates)),
            cache,
        )
        return True

    def _join_key_is_foreign_key_source(
        self,
        schema: DerivedSchema,
        column: exp.Column,
    ) -> bool:
        table = getattr(schema, "_table", None)
        if table is None:
            return False
        if column.name.casefold() not in {
            name.casefold() for name in self.instance.column_names(table)
        }:
            return False
        resolved = self.instance.resolve_column(table, column.name)
        return any(
            resolved in foreign_key.source_columns
            for foreign_key in self.instance.get_foreign_keys(table)
        )

def _reachable_steps(root: Step) -> tuple[Step, ...]:
    ordered: list[Step] = []
    seen: Set[Step] = set()

    def visit(node: Step) -> None:
        if node in seen:
            return
        seen.add(node)
        ordered.append(node)
        for dependency in node.dependencies:
            visit(dependency)

    visit(root)
    return tuple(ordered)
