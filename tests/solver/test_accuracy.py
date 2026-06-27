from __future__ import annotations

import re
from datetime import date, datetime, time
from functools import reduce
from typing import Any

import pytest
from sqlglot import exp

from parseval.dtype import DataType, parse_date, parse_datetime, parse_time
from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var, solver_var


T = relation_id(RelationKind.TABLE, identifier_name("t"))
U = relation_id(RelationKind.TABLE, identifier_name("u"))


def _var(table, name: str) -> SolverVar:
    return SolverVar(
        column_id(ColumnKind.PHYSICAL, identifier_name(name), table),
        table,
    )


I = _var(T, "i")
J = _var(T, "j")
R = _var(T, "r")
S = _var(T, "s")
B = _var(T, "b")
D = _var(T, "d")
DT = _var(T, "dt")
TM = _var(T, "tm")
A = _var(T, "a")
C = _var(U, "c")


def _col(var: SolverVar, dtype: str) -> exp.Column:
    node = exp.column(var.column_id.name.normalized, table=var.relation_id.display)
    node.type = DataType.build(dtype)
    set_solver_var(node, var)
    return node


def _and(*expressions: exp.Expression) -> exp.Expression:
    return reduce(lambda left, right: exp.And(this=left, expression=right), expressions)


def _solve(
    *expressions: exp.Expression,
    join_equalities: list[tuple[SolverVar, SolverVar]] | None = None,
    variables: dict[SolverVar, DataType] | None = None,
):
    return Solver().solve(
        SolverConstraint(
            target_relations=(T, U),
            constraints=list(expressions),
            join_equalities=join_equalities or [],
            variables=variables or {},
        )
    )


def _literal(node: exp.Expression) -> Any:
    if isinstance(node, exp.Literal):
        if node.is_int:
            return int(node.this)
        if node.is_number:
            return float(node.this)
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Neg):
        value = _literal(node.this)
        return -value if isinstance(value, (int, float)) else None
    raise AssertionError(f"Unsupported literal node: {node!r}")


def _value(node: exp.Expression, assignments: dict[SolverVar, Any]) -> Any:
    if isinstance(node, exp.Column):
        variable = solver_var(node)
        assert variable is not None
        return assignments[variable]
    if isinstance(node, (exp.Literal, exp.Boolean, exp.Null, exp.Neg)):
        return _literal(node)
    if isinstance(node, exp.Add):
        return _value(node.this, assignments) + _value(node.expression, assignments)
    raise AssertionError(f"Unsupported value node: {node!r}")


def _compare(left: Any, right: Any, op: type[exp.Expression]) -> bool:
    if left is None or right is None:
        return False
    if isinstance(right, str):
        if isinstance(left, datetime):
            parsed = parse_datetime(right)
            right = parsed if parsed is not None else right
        elif isinstance(left, date):
            parsed = parse_date(right)
            right = parsed if parsed is not None else right
        elif isinstance(left, time):
            parsed = parse_time(right)
            right = parsed if parsed is not None else right
    if isinstance(left, str):
        if isinstance(right, datetime):
            parsed = parse_datetime(left)
            left = parsed if parsed is not None else left
        elif isinstance(right, date):
            parsed = parse_date(left)
            left = parsed if parsed is not None else left
        elif isinstance(right, time):
            parsed = parse_time(left)
            left = parsed if parsed is not None else left
    if isinstance(left, str) and isinstance(right, (int, float)):
        left = type(right)(left)
    if isinstance(right, str) and isinstance(left, (int, float)):
        right = type(left)(right)
    if op is exp.EQ:
        return left == right
    if op is exp.NEQ:
        return left != right
    if op is exp.GT:
        return left > right
    if op is exp.GTE:
        return left >= right
    if op is exp.LT:
        return left < right
    if op is exp.LTE:
        return left <= right
    raise AssertionError(f"Unsupported comparison: {op}")


def _matches_like(value: str, pattern: str) -> bool:
    regex = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    return re.match(regex, value) is not None


def _satisfies(expr: exp.Expression, assignments: dict[SolverVar, Any]) -> bool:
    if isinstance(expr, exp.And):
        return _satisfies(expr.this, assignments) and _satisfies(expr.expression, assignments)
    if isinstance(expr, exp.Or):
        return _satisfies(expr.this, assignments) or _satisfies(expr.expression, assignments)
    if isinstance(expr, exp.Not):
        return not _satisfies(expr.this, assignments)
    if isinstance(expr, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return _compare(_value(expr.this, assignments), _value(expr.expression, assignments), type(expr))
    if isinstance(expr, exp.Like):
        return _matches_like(str(_value(expr.this, assignments)), str(_value(expr.expression, assignments)))
    if isinstance(expr, exp.In):
        needle = _value(expr.this, assignments)
        return any(_compare(needle, _value(candidate, assignments), exp.EQ) for candidate in expr.expressions)
    if isinstance(expr, exp.Between):
        value = _value(expr.this, assignments)
        low = _value(expr.args["low"], assignments)
        high = _value(expr.args["high"], assignments)
        return _compare(value, low, exp.GTE) and _compare(value, high, exp.LTE)
    if isinstance(expr, exp.Is):
        value = _value(expr.this, assignments)
        if isinstance(expr.expression, exp.Null):
            return value is None
        if isinstance(expr.expression, exp.Not) and isinstance(expr.expression.this, exp.Null):
            return value is not None
    raise AssertionError(f"Unsupported predicate node: {expr!r}")


def _assert_sat_satisfies(
    expressions: list[exp.Expression],
    *,
    join_equalities: list[tuple[SolverVar, SolverVar]] | None = None,
    variables: dict[SolverVar, DataType] | None = None,
):
    result = _solve(*expressions, join_equalities=join_equalities, variables=variables)
    assert result.sat, result.reason
    for expression in expressions:
        assert _satisfies(expression, result.assignments), expression.sql()
    for left, right in join_equalities or []:
        assert result.assignments[left] == result.assignments[right]
    return result


@pytest.mark.parametrize(
    "expressions",
    [
        [
            exp.Between(this=_col(I, "INT"), low=exp.Literal.number(10), high=exp.Literal.number(20)),
            exp.NEQ(this=_col(I, "INT"), expression=exp.Literal.number(15)),
            exp.In(this=_col(I, "INT"), expressions=[exp.Literal.string("12"), exp.Literal.string("18")]),
        ],
        [
            exp.GTE(this=_col(R, "FLOAT"), expression=exp.Literal.number(1.25)),
            exp.LT(this=_col(R, "FLOAT"), expression=exp.Literal.number(2.25)),
        ],
        [
            exp.Like(this=_col(S, "TEXT"), expression=exp.Literal.string("ab%")),
            exp.In(this=_col(S, "TEXT"), expressions=[exp.Literal.string("abc"), exp.Literal.string("abd")]),
        ],
        [
            exp.EQ(this=_col(B, "BOOLEAN"), expression=exp.Boolean(this=True)),
            exp.Is(this=_col(B, "BOOLEAN"), expression=exp.Not(this=exp.Null())),
        ],
        [exp.GTE(this=_col(D, "DATE"), expression=exp.Literal.string("2024-01-01"))],
        [exp.GT(this=_col(TM, "TIME"), expression=exp.Literal.string("09:30:00"))],
    ],
)
def test_domain_assignments_satisfy_original_predicates(expressions):
    _assert_sat_satisfies(expressions)


@pytest.mark.parametrize(
    "expressions",
    [
        [exp.GT(this=_col(I, "INT"), expression=_col(J, "INT"))],
        [
            _and(
                exp.Or(
                    this=exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(1)),
                    expression=exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(2)),
                ),
                exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(2)),
            )
        ],
        [exp.Not(this=exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(0)))],
        [
            _and(
                exp.GTE(
                    this=_col(DT, "DATETIME"),
                    expression=exp.Literal.string("2024-06-07T12:30:00+02:00"),
                ),
                exp.GT(
                    this=exp.Add(this=_col(A, "INT"), expression=_col(J, "INT")),
                    expression=exp.Literal.number(0),
                ),
            )
        ],
    ],
)
def test_smt_assignments_satisfy_original_predicates(expressions):
    _assert_sat_satisfies(expressions)


def test_mixed_domain_and_smt_components_merge_satisfying_assignments():
    _assert_sat_satisfies([
        exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(7)),
        exp.Like(this=_col(S, "TEXT"), expression=exp.Literal.string("ok%")),
        exp.GT(
            this=exp.Add(this=_col(A, "INT"), expression=_col(J, "INT")),
            expression=exp.Literal.number(10),
        ),
    ])


def test_join_equalities_are_part_of_accuracy_check():
    _assert_sat_satisfies(
        [
            exp.GT(
                this=exp.Add(this=_col(A, "INT"), expression=_col(J, "INT")),
                expression=exp.Literal.number(10),
            ),
        ],
        join_equalities=[(J, C)],
        variables={C: DataType.build("INT")},
    )


@pytest.mark.parametrize(
    "expressions",
    [
        [
            exp.GT(this=_col(I, "INT"), expression=_col(J, "INT")),
            exp.LTE(this=_col(I, "INT"), expression=_col(J, "INT")),
        ],
        [
            _and(
                exp.Or(
                    this=exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(1)),
                    expression=exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(2)),
                ),
                exp.EQ(this=_col(I, "INT"), expression=exp.Literal.number(3)),
            )
        ],
        [
            exp.EQ(this=_col(S, "TEXT"), expression=exp.Literal.string("abc")),
            exp.Like(this=_col(S, "TEXT"), expression=exp.Literal.string("x%")),
        ],
    ],
)
def test_solver_reports_unsat_for_known_contradictions(expressions):
    result = _solve(*expressions)

    assert not result.sat
