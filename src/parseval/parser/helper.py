from __future__ import annotations

from sqlglot import exp


def strip_parens(expression: exp.Expression) -> exp.Expression:
    while isinstance(expression, exp.Paren):
        expression = expression.this
    return expression


def strip_alias(expression: exp.Expression) -> exp.Expression:
    while isinstance(expression, (exp.Alias, exp.Paren)):
        expression = expression.this
    return expression


def query_body(expression: exp.Expression) -> exp.Expression:
    """Remove syntactic parentheses and subquery wrappers around a query body."""

    while isinstance(expression, (exp.Paren, exp.Subquery)):
        expression = expression.this
    return expression


def integer_literal(expression: exp.Expression) -> int | None:
    expression = strip_parens(expression)
    if not isinstance(expression, exp.Literal) or expression.is_string:
        return None
    try:
        return int(str(expression.this))
    except ValueError:
        return None


def boolean_literal(expression: exp.Boolean) -> bool:
    raw = expression.this
    return raw if isinstance(raw, bool) else str(raw).casefold() == "true"


def function_name(expression: exp.Expression) -> str:
    if isinstance(expression, exp.Anonymous):
        return expression.name
    value = type(expression).sql_name()
    return str(value)


def aggregate_expression(
    expression: exp.Expression,
) -> exp.Expression | None:
    if isinstance(expression, exp.AggFunc):
        return expression
    if isinstance(expression, exp.Filter) and isinstance(expression.this, exp.AggFunc):
        return expression
    return None
