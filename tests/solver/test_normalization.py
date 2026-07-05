from __future__ import annotations

from sqlglot import exp

from parseval.dtype import DataType
from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.solver import SolverConstraint, SolverVar, set_solver_var, solver_var
from parseval.solver.normalization import normalize_constraint


T = relation_id(RelationKind.TABLE, identifier_name("t"))


def _var(name: str) -> SolverVar:
    return SolverVar(column_id(ColumnKind.PHYSICAL, identifier_name(name), T), T)


def _col(var: SolverVar, dtype: str) -> exp.Column:
    node = exp.column(var.column_id.name.normalized, table=var.relation_id.display)
    node.type = DataType.build(dtype)
    set_solver_var(node, var)
    return node


DATE_COL = _var("date_col")
TEXT_COL = _var("text_col")


def test_normalize_constraint_returns_copy_without_mutating_input():
    original_expr = exp.GT(
        this=exp.TimeToStr(
            this=_col(DATE_COL, "DATE"),
            format=exp.Literal.string("%Y"),
        ),
        expression=exp.Literal.string("1995"),
    )
    original_sql = original_expr.sql()
    constraint = SolverConstraint(
        target_relations=(T,),
        constraints=[original_expr],
        variables={DATE_COL: DataType.build("DATE")},
    )

    normalized = normalize_constraint(constraint)

    assert constraint is not normalized
    assert constraint.constraints[0] is original_expr
    assert constraint.constraints[0].sql() == original_sql
    assert normalized.constraints[0].sql() != original_sql
    assert isinstance(normalized.constraints[0], exp.GTE)


def test_expression_witness_lowering_is_separate_from_temporal_projection_bounds():
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
        expression=exp.Cast(
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
    )
    constraint = SolverConstraint(
        target_relations=(T,),
        constraints=[exp.LT(this=seconds, expression=exp.Literal.number(120))],
        variables={TEXT_COL: DataType.build("TEXT")},
    )

    normalized = normalize_constraint(constraint)

    assert isinstance(normalized.constraints[0], exp.EQ)
    assert solver_var(normalized.constraints[0].this) == TEXT_COL
    assert normalized.constraints[0].expression.this == "01:59:00"
