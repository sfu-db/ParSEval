from sqlglot import exp

from parseval.solver.domain import DomainSolver
from parseval.solver.types import ColumnPredicate


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = exp.DataType.build(dtype)
    return node


def test_simple_equality():
    expr = exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25))
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert result["t1"]["age"] == 25


def test_greater_than():
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert result["t1"]["age"] > 18


def test_conjunction():
    expr1 = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr2 = exp.LT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(20))
    expr = exp.And(this=expr1, expression=expr2)
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert 10 < result["t1"]["age"] < 20


def test_is_null():
    expr = exp.Is(this=_col("t1", "name", "TEXT"), expression=exp.Null())
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert result["t1"]["name"] is None


def test_join_equality():
    expr = exp.GT(this=_col("t1", "id", "INT"), expression=exp.Literal.number(0))
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1", "t2"),
        expressions=[expr],
        join_equalities=[("t1", "id", "t2", "t1_id")],
    )
    assert result is not None
    assert result["t1"]["id"] == result["t2"]["t1_id"]


def test_empty_constraints():
    solver = DomainSolver()
    result = solver.solve(target_tables=("t1",), expressions=[])
    assert result is not None
