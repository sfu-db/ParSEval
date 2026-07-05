from __future__ import annotations

from datetime import date, datetime, time
from functools import reduce
from typing import Callable

from sqlglot import exp

from parseval.dtype import DataType
from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var


T = relation_id(RelationKind.TABLE, identifier_name("t"))
U = relation_id(RelationKind.TABLE, identifier_name("u"))


def _var(table, name: str) -> SolverVar:
    return SolverVar(
        column_id(ColumnKind.PHYSICAL, identifier_name(name), table),
        table,
    )


INT_COL = _var(T, "int_col")
REAL_COL = _var(T, "real_col")
TEXT_COL = _var(T, "text_col")
BOOL_COL = _var(T, "bool_col")
DATE_COL = _var(T, "date_col")
DATETIME_COL = _var(T, "datetime_col")
TIME_COL = _var(T, "time_col")
LEFT_ID = _var(T, "id")
RIGHT_ID = _var(U, "id")
LEFT_TEXT = _var(T, "left_text")
MID_TEXT = _var(T, "mid_text")
RIGHT_TEXT = _var(T, "right_text")
ARITH_A = _var(T, "a")
ARITH_B = _var(T, "b")
ARITH_C = _var(U, "c")


def _col(var: SolverVar, dtype: str) -> exp.Column:
    node = exp.column(var.column_id.name.normalized, table=var.relation_id.display)
    node.type = DataType.build(dtype)
    set_solver_var(node, var)
    return node


def _and(*expressions: exp.Expression) -> exp.Expression:
    return reduce(lambda left, right: exp.And(this=left, expression=right), expressions)


def _solve(
    *expressions: exp.Expression,
    variables: dict[SolverVar, DataType] | None = None,
    join_equalities: list[tuple[SolverVar, SolverVar]] | None = None,
):
    return Solver().solve(
        SolverConstraint(
            target_relations=(T, U),
            constraints=list(expressions),
            join_equalities=join_equalities or [],
            variables=variables or {},
        )
    )


def _assert_sat_value(
    var: SolverVar,
    expression: exp.Expression,
    predicate: Callable[[object], bool],
) -> object:
    result = _solve(expression)
    assert result.sat, result.reason
    assert var in result.assignments
    value = result.assignments[var]
    assert predicate(value), value
    return value


def test_integer_comparisons_between_and_string_literal_in_are_coerced():
    result = _solve(
        exp.Between(
            this=_col(INT_COL, "INT"),
            low=exp.Literal.number(10),
            high=exp.Literal.number(20),
        ),
        exp.NEQ(this=_col(INT_COL, "INT"), expression=exp.Literal.number(15)),
        exp.In(
            this=_col(INT_COL, "INT"),
            expressions=[exp.Literal.string("12"), exp.Literal.string("18")],
        ),
    )

    assert result.sat, result.reason
    value = result.assignments[INT_COL]
    assert isinstance(value, int)
    assert value in {12, 18}


def test_decimal_range_keeps_float_assignment():
    value = _assert_sat_value(
        REAL_COL,
        _and(
            exp.GTE(this=_col(REAL_COL, "FLOAT"), expression=exp.Literal.number(3.5)),
            exp.LT(this=_col(REAL_COL, "FLOAT"), expression=exp.Literal.number(4.5)),
        ),
        lambda value: isinstance(value, float) and 3.5 <= value < 4.5,
    )

    assert value != 4.5


def test_text_equality_like_and_in_constraints():
    result = _solve(
        exp.Like(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("ab%")),
        exp.In(
            this=_col(TEXT_COL, "TEXT"),
            expressions=[exp.Literal.string("abc"), exp.Literal.string("zzz")],
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[TEXT_COL] == "abc"


def test_text_numeric_comparison_with_like_produces_numeric_string():
    result = _solve(
        exp.GT(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("50")),
        exp.Like(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("5%")),
    )

    assert result.sat, result.reason
    value = result.assignments[TEXT_COL]
    assert isinstance(value, str)
    assert value.startswith("5")
    assert int(value) > 50


def test_boolean_equality_and_nullability_constraints():
    true_result = _solve(
        exp.EQ(this=_col(BOOL_COL, "BOOLEAN"), expression=exp.Boolean(this=True)),
        exp.Is(this=_col(BOOL_COL, "BOOLEAN"), expression=exp.Not(this=exp.Null())),
    )
    null_result = _solve(
        exp.Is(this=_col(BOOL_COL, "BOOLEAN"), expression=exp.Null()),
    )

    assert true_result.sat, true_result.reason
    assert true_result.assignments[BOOL_COL] is True
    assert null_result.sat, null_result.reason
    assert null_result.assignments[BOOL_COL] is None


def test_date_datetime_and_time_string_comparisons_are_temporal_values():
    date_value = _assert_sat_value(
        DATE_COL,
        exp.GTE(this=_col(DATE_COL, "DATE"), expression=exp.Literal.string("2024-01-01")),
        lambda value: isinstance(value, date) and not isinstance(value, datetime)
        and value >= date(2024, 1, 1),
    )
    datetime_value = _assert_sat_value(
        DATETIME_COL,
        exp.LT(
            this=_col(DATETIME_COL, "DATETIME"),
            expression=exp.Literal.string("2024-06-01 12:00:00"),
        ),
        lambda value: isinstance(value, datetime)
        and value < datetime(2024, 6, 1, 12, 0, 0),
    )
    time_value = _assert_sat_value(
        TIME_COL,
        exp.GT(this=_col(TIME_COL, "TIME"), expression=exp.Literal.string("09:30:00")),
        lambda value: isinstance(value, time) and value > time(9, 30, 0),
    )

    assert date_value == date(2024, 1, 1)
    assert datetime_value < datetime(2024, 6, 1, 12, 0, 0)
    assert time_value > time(9, 30, 0)


def test_iso_datetime_with_offset_is_normalized_for_solver_comparisons():
    result = _solve(
        exp.EQ(
            this=_col(DATETIME_COL, "DATETIME"),
            expression=exp.Literal.string("2024-06-07T12:30:00+02:00"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATETIME_COL] == datetime(2024, 6, 7, 10, 30, 0)


def test_sqlite_datetime_equality_preserves_exact_fractional_literal():
    result = _solve(
        exp.EQ(
            this=_col(DATETIME_COL, "DATETIME"),
            expression=exp.Literal.string("2010-07-19 19:39:08.0"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATETIME_COL] == "2010-07-19 19:39:08.0"


def test_column_equality_and_join_equality_share_assignments():
    result = _solve(
        exp.GTE(this=_col(LEFT_ID, "INT"), expression=exp.Literal.number(100)),
        join_equalities=[(LEFT_ID, RIGHT_ID)],
        variables={RIGHT_ID: DataType.build("INT")},
    )

    assert result.sat, result.reason
    assert result.assignments[LEFT_ID] == result.assignments[RIGHT_ID]
    assert result.assignments[LEFT_ID] >= 100


def test_text_column_equality_component_uses_one_assignment():
    result = _solve(
        exp.EQ(this=_col(MID_TEXT, "TEXT"), expression=_col(LEFT_TEXT, "TEXT")),
        exp.EQ(this=_col(RIGHT_TEXT, "TEXT"), expression=_col(LEFT_TEXT, "TEXT")),
    )

    assert result.sat, result.reason
    assert result.assignments[MID_TEXT] == result.assignments[LEFT_TEXT]
    assert result.assignments[RIGHT_TEXT] == result.assignments[LEFT_TEXT]


def test_contradictory_comparisons_are_unsat():
    result = _solve(
        exp.GT(this=_col(INT_COL, "INT"), expression=exp.Literal.number(10)),
        exp.LT(this=_col(INT_COL, "INT"), expression=exp.Literal.number(0)),
    )

    assert not result.sat
    assert result.reason == "contradictory_bounds"


def test_smt_fallback_handles_arithmetic_or_and_not():
    arithmetic = _solve(
        exp.GT(
            this=exp.Add(this=_col(ARITH_A, "INT"), expression=_col(ARITH_B, "INT")),
            expression=exp.Literal.number(10),
        )
    )
    disjunction = _solve(
        exp.Or(
            this=exp.EQ(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("active")),
            expression=exp.EQ(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("pending")),
        )
    )
    negation = _solve(
        exp.Not(this=exp.EQ(this=_col(INT_COL, "INT"), expression=exp.Literal.number(0)))
    )

    assert arithmetic.sat, arithmetic.reason
    assert arithmetic.assignments[ARITH_A] + arithmetic.assignments[ARITH_B] > 10
    assert disjunction.sat, disjunction.reason
    assert disjunction.assignments[TEXT_COL] in {"active", "pending"}
    assert negation.sat, negation.reason
    assert negation.assignments[INT_COL] != 0


def test_smt_fallback_handles_column_column_inequality():
    result = _solve(
        exp.GT(this=_col(ARITH_A, "INT"), expression=_col(ARITH_B, "INT")),
    )

    assert result.sat, result.reason
    assert result.assignments[ARITH_A] > result.assignments[ARITH_B]


def test_smt_final_answer_handles_or_with_surrounding_constraints():
    result = _solve(
        _and(
            exp.Or(
                this=exp.EQ(this=_col(INT_COL, "INT"), expression=exp.Literal.number(1)),
                expression=exp.EQ(this=_col(INT_COL, "INT"), expression=exp.Literal.number(2)),
            ),
            exp.EQ(this=_col(INT_COL, "INT"), expression=exp.Literal.number(2)),
        )
    )

    assert result.sat, result.reason
    assert result.assignments[INT_COL] == 2


def test_like_conflicts_are_unsat_in_domain_solver():
    equality_conflict = _solve(
        exp.EQ(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("abc")),
        exp.Like(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("x%")),
    )
    hidden_numeric_conflict = _solve(
        exp.GT(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("100")),
        exp.Like(this=_col(TEXT_COL, "TEXT"), expression=exp.Literal.string("abc%")),
    )

    assert not equality_conflict.sat
    assert not hidden_numeric_conflict.sat


def test_iso_datetime_with_offset_is_supported_after_smt_fallback():
    result = _solve(
        exp.GTE(
            this=_col(DATETIME_COL, "DATETIME"),
            expression=exp.Literal.string("2024-06-07T12:30:00+02:00"),
        ),
        exp.GT(
            this=exp.Add(this=_col(ARITH_A, "INT"), expression=_col(ARITH_B, "INT")),
            expression=exp.Literal.number(0),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATETIME_COL] >= datetime(2024, 6, 7, 10, 30, 0)


def test_temporal_substr_year_equality_is_rewritten_to_date_bounds():
    result = _solve(
        exp.EQ(
            this=exp.Anonymous(
                this="SUBSTR",
                expressions=[
                    _col(DATE_COL, "DATE"),
                    exp.Literal.number(1),
                    exp.Literal.number(4),
                ],
            ),
            expression=exp.Literal.string("2010"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATE_COL].year == 2010


def test_temporal_time_to_str_year_supports_pre_1970_dates():
    result = _solve(
        exp.EQ(
            this=exp.TimeToStr(
                this=_col(DATE_COL, "DATE"),
                format=exp.Literal.string("%Y"),
            ),
            expression=exp.Literal.string("1920"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATE_COL].year == 1920


def test_temporal_time_to_str_strict_year_comparisons_are_rewritten_to_date_bounds():
    after_result = _solve(
        exp.GT(
            this=exp.TimeToStr(
                this=_col(DATE_COL, "DATE"),
                format=exp.Literal.string("%Y"),
            ),
            expression=exp.Literal.string("1995"),
        ),
    )
    before_result = _solve(
        exp.LT(
            this=exp.TimeToStr(
                this=_col(DATETIME_COL, "DATETIME"),
                format=exp.Literal.string("%Y"),
            ),
            expression=exp.Literal.string("1997"),
        ),
    )

    assert after_result.sat, after_result.reason
    assert after_result.assignments[DATE_COL] >= date(1996, 1, 1)
    assert before_result.sat, before_result.reason
    assert before_result.assignments[DATETIME_COL] <= datetime(1996, 12, 31, 23, 59, 59)


def test_date_wrapper_comparison_is_rewritten_to_temporal_bounds():
    result = _solve(
        exp.GT(
            this=exp.Date(this=_col(DATETIME_COL, "DATETIME")),
            expression=exp.Literal.string("2014-09-01"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATETIME_COL] >= datetime(2014, 9, 2, 0, 0, 0)


def test_date_wrapper_over_substr_between_is_rewritten_to_text_bounds():
    result = _solve(
        exp.Between(
            this=exp.Date(
                this=exp.Anonymous(
                    this="SUBSTR",
                    expressions=[
                        _col(TEXT_COL, "TEXT"),
                        exp.Literal.number(1),
                        exp.Literal.number(10),
                    ],
                ),
            ),
            low=exp.Literal.string("2019-03-15"),
            high=exp.Literal.string("2020-03-20"),
        ),
    )

    assert result.sat, result.reason
    value = result.assignments[TEXT_COL]
    assert "2019-03-15" <= value[:10] <= "2020-03-20"


def test_substr_time_seconds_less_than_threshold_generates_matching_text_time():
    seconds = exp.Add(
        this=exp.Mul(
            this=exp.Cast(
                this=exp.Anonymous(
                    this="SUBSTR",
                    expressions=[
                        _col(TEXT_COL, "TEXT"),
                        exp.Literal.number(1),
                        exp.Literal.number(2),
                    ],
                ),
                to=DataType.build("INT"),
            ),
            expression=exp.Literal.number(60),
        ),
        expression=exp.Add(
            this=exp.Cast(
                this=exp.Anonymous(
                    this="SUBSTR",
                    expressions=[
                        _col(TEXT_COL, "TEXT"),
                        exp.Literal.number(4),
                        exp.Literal.number(2),
                    ],
                ),
                to=DataType.build("INT"),
            ),
            expression=exp.Div(
                this=exp.Cast(
                    this=exp.Anonymous(
                        this="SUBSTR",
                        expressions=[
                            _col(TEXT_COL, "TEXT"),
                            exp.Literal.number(7),
                            exp.Literal.number(2),
                        ],
                    ),
                    to=DataType.build("FLOAT"),
                ),
                expression=exp.Literal.number(1000),
            ),
        ),
    )

    result = _solve(
        exp.LT(this=seconds, expression=exp.Literal.number(120)),
    )

    assert result.sat, result.reason
    value = result.assignments[TEXT_COL]
    assert value[:2].isdigit()
    assert value[2] == ":"
    assert value[3:5].isdigit()
    assert value[5] == ":"
    assert value[6:8].isdigit()
    total_seconds = int(value[:2]) * 60 + int(value[3:5]) + int(value[6:8]) / 1000
    assert total_seconds < 120


def test_temporal_substr_year_between_is_rewritten_to_date_bounds():
    result = _solve(
        exp.Between(
            this=exp.Anonymous(
                this="SUBSTR",
                expressions=[
                    _col(DATE_COL, "DATE"),
                    exp.Literal.number(1),
                    exp.Literal.number(4),
                ],
            ),
            low=exp.Literal.string("2008"),
            high=exp.Literal.string("2010"),
        ),
    )

    assert result.sat, result.reason
    assert 2008 <= result.assignments[DATE_COL].year <= 2010


def test_temporal_time_to_str_month_prefix_is_rewritten_to_date_bounds():
    result = _solve(
        exp.EQ(
            this=exp.TimeToStr(
                this=_col(DATE_COL, "DATE"),
                format=exp.Literal.string("%Y-%m"),
            ),
            expression=exp.Literal.string("2010-03"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATE_COL].year == 2010
    assert result.assignments[DATE_COL].month == 3


def test_temporal_substr_full_date_prefix_is_rewritten_to_date_bounds():
    result = _solve(
        exp.EQ(
            this=exp.Anonymous(
                this="SUBSTR",
                expressions=[
                    _col(DATE_COL, "DATE"),
                    exp.Literal.number(1),
                    exp.Literal.number(10),
                ],
            ),
            expression=exp.Literal.string("2010-03-15"),
        ),
    )

    assert result.sat, result.reason
    assert result.assignments[DATE_COL] == date(2010, 3, 15)


def test_smt_join_equality_assigns_both_sides():
    result = _solve(
        exp.GT(
            this=exp.Add(this=_col(ARITH_A, "INT"), expression=_col(ARITH_B, "INT")),
            expression=exp.Literal.number(10),
        ),
        join_equalities=[(ARITH_B, ARITH_C)],
        variables={ARITH_C: DataType.build("INT")},
    )

    assert result.sat, result.reason
    assert result.assignments[ARITH_B] == result.assignments[ARITH_C]
