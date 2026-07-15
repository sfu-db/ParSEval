from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlglot import exp

from parseval.instance import Instance
from parseval.instance.schema import normalize_identifier
from parseval.plan.explain import Join, Projection, Sort, Step, SubqueryAlias, TableScan
from parseval.plan.rex import Environment, concrete


def same_identifier(
    left: exp.Identifier,
    right: exp.Identifier,
    dialect: str,
) -> bool:
    return normalize_identifier(left, dialect) == normalize_identifier(right, dialect)


def leaf_table_scans(step: Step | None) -> tuple[TableScan, ...]:
    if step is None:
        return ()
    if isinstance(step, TableScan):
        return (step,)
    scans: list[TableScan] = []
    for dependency in step.dependencies:
        scans.extend(leaf_table_scans(dependency))
    return tuple(scans)


def single_leaf_scan(step: Step) -> TableScan | None:
    scans = leaf_table_scans(step)
    return scans[0] if len(scans) == 1 else None


def upstream_steps(step: Step) -> tuple[Step, ...]:
    steps: list[Step] = []
    seen: set[int] = set()

    def visit(candidate: Step) -> None:
        if id(candidate) in seen:
            return
        seen.add(id(candidate))
        steps.append(candidate)
        for dependency in candidate.dependencies:
            visit(dependency)

    visit(step)
    return tuple(steps)


def single_dependency_join(step: Step) -> Join | None:
    joins = [candidate for candidate in upstream_steps(step) if isinstance(candidate, Join)]
    return joins[0] if len(joins) == 1 else None


def join_alias_tables(join: Join) -> tuple[tuple[exp.Identifier, exp.Table], ...]:
    pairs: list[tuple[exp.Identifier, exp.Table]] = []

    def visit(step: Step, active_alias: exp.Identifier | None = None) -> None:
        if isinstance(step, SubqueryAlias):
            active_alias = step.alias
        if isinstance(step, TableScan) and active_alias is not None:
            pairs.append((active_alias, step.table))
            return
        for dependency in step.dependencies:
            visit(dependency, active_alias)

    for dependency in join.dependencies:
        visit(dependency)
    return tuple(pairs)


def storage_table_for_join_column(
    join: Join,
    column: exp.Column,
    instance: Instance,
) -> exp.Table | None:
    qualifier = column.args.get("table")
    if qualifier is None:
        return None
    for alias, table in join_alias_tables(join):
        if same_identifier(alias, qualifier, instance.dialect):
            return table
    try:
        return instance.resolve_table(exp.Table(this=qualifier.copy()))
    except KeyError:
        return None


def table_for_column(
    instance: Instance,
    alias_tables: Mapping[exp.Identifier, exp.Table],
    column: exp.Column,
) -> exp.Table | None:
    qualifier = column.args.get("table")
    if qualifier is not None:
        for alias, table in alias_tables.items():
            if same_identifier(alias, qualifier, instance.dialect):
                return table
        try:
            return instance.resolve_table(exp.Table(this=qualifier.copy()))
        except KeyError:
            return None
    matches = []
    for table in alias_tables.values():
        try:
            instance.resolve_column(table, column.name)
        except KeyError:
            continue
        matches.append(table)
    return matches[0] if len(matches) == 1 else None


def resolved_order_expressions(
    instance: Instance,
    table: exp.Table,
    sort: Sort,
) -> tuple[exp.Expression, ...] | None:
    expressions: list[exp.Expression] = []
    projections = [step for step in upstream_steps(sort) if isinstance(step, Projection)]
    for ordered in sort.key:
        expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if _physical_expression_supported(instance, table, expr):
            expressions.append(expr)
            continue
        if isinstance(expr, exp.Column) and len(projections) == 1:
            projection = projections[0]
            if len(projection.projections) == 1:
                projected = projection.projections[0]
                projected_expr = projected.this if isinstance(projected, exp.Alias) else projected
                if _physical_expression_supported(instance, table, projected_expr):
                    expressions.append(projected_expr)
                    continue
        return None
    return tuple(expressions)


def _physical_expression_supported(
    instance: Instance,
    table: exp.Table,
    expression: exp.Expression,
) -> bool:
    if expression.find(exp.AggFunc) or expression.find(exp.Window) or expression.find(exp.Subquery):
        return False
    columns = tuple(expression.find_all(exp.Column))
    if not columns:
        return False
    for column in columns:
        try:
            instance.resolve_column(table, column.name)
        except KeyError:
            return False
    return True


def join_order_keys_supported(
    instance: Instance,
    alias_tables: Mapping[exp.Identifier, exp.Table],
    keys: Sequence[exp.Expression],
) -> bool:
    for ordered in keys:
        expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        if expr.find(exp.AggFunc) or expr.find(exp.Window) or expr.find(exp.Subquery):
            return False
        columns = tuple(expr.find_all(exp.Column))
        if not columns:
            return False
        for column in columns:
            table = table_for_column(instance, alias_tables, column)
            if table is None:
                return False
            try:
                instance.resolve_column(table, column.name)
            except KeyError:
                return False
    return True

def order_expression_value(
    instance: Instance,
    alias_tables: Mapping[exp.Identifier, exp.Table],
    row: Mapping[exp.Identifier, Any],
    expression: exp.Expression,
) -> Any:
    if isinstance(expression, exp.Column):
        table = table_for_column(instance, alias_tables, expression)
        if table is None:
            return None
        return row.get(instance.resolve_column(table, expression.name))
    if isinstance(expression, exp.Div):
        left = order_expression_value(instance, alias_tables, row, expression.this)
        right = order_expression_value(instance, alias_tables, row, expression.expression)
        if left is None or right in {None, 0}:
            return None
        return left / right
    try:
        return concrete(expression, Environment.from_row(row))
    except Exception:
        return None
