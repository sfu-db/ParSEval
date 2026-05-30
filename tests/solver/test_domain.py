"""Tests for the DomainSolver — CSP-lite with ValueSpace narrowing."""
from sqlglot import exp

from parseval.solver.domain import DomainSolver
from parseval.solver.unified import SolverConstraint


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = exp.DataType.build(dtype)
    return node


def _constraint(tables, expressions=None, join_equalities=None):
    return SolverConstraint(
        target_tables=tables,
        constraints=expressions or [],
        join_equalities=join_equalities or [],
    )


def test_simple_equality():
    expr = exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["age"] == 25


def test_greater_than():
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["age"] > 18


def test_less_than():
    expr = exp.LT(this=_col("t1", "score", "INT"), expression=exp.Literal.number(100))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["score"] < 100


def test_conjunction():
    expr1 = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr2 = exp.LT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(20))
    expr = exp.And(this=expr1, expression=expr2)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert 10 < result["t1"]["age"] < 20


def test_is_null():
    expr = exp.Is(this=_col("t1", "name", "TEXT"), expression=exp.Null())
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["name"] is None


def test_join_equality():
    expr = exp.GT(this=_col("t1", "id", "INT"), expression=exp.Literal.number(0))
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("t1", "t2"), [expr],
        join_equalities=[("t1", "id", "t2", "t1_id")],
    ))
    assert result is not None
    assert result["t1"]["id"] == result["t2"]["t1_id"]


def test_empty_constraints():
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",)))
    assert result is not None
    assert "t1" in result


def test_not_equal():
    expr = exp.NEQ(this=_col("t1", "status", "TEXT"), expression=exp.Literal.string("deleted"))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["status"] != "deleted"


def test_gte():
    expr = exp.GTE(this=_col("t1", "count", "INT"), expression=exp.Literal.number(5))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["count"] >= 5


def test_lte():
    expr = exp.LTE(this=_col("t1", "count", "INT"), expression=exp.Literal.number(100))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["count"] <= 100


def test_multiple_tables():
    expr1 = exp.EQ(this=_col("t1", "id", "INT"), expression=exp.Literal.number(1))
    expr2 = exp.EQ(this=_col("t2", "name", "TEXT"), expression=exp.Literal.string("Alice"))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1", "t2"), [expr1, expr2]))
    assert result is not None
    assert result["t1"]["id"] == 1
    assert result["t2"]["name"] == "Alice"


def test_self_join_different_values():
    """Self-join: same physical table, different aliases, different values."""
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("a", "b"),
        [
            exp.EQ(this=_col("a", "name", "TEXT"), expression=exp.Literal.string("Alice")),
            exp.EQ(this=_col("b", "name", "TEXT"), expression=exp.Literal.string("Bob")),
        ],
        join_equalities=[("a", "manager_id", "b", "id")],
    ))
    assert result is not None
    # Each alias should get its own value
    assert result["a"]["name"] == "Alice"
    assert result["b"]["name"] == "Bob"
    # Join equality should hold
    assert result["a"]["manager_id"] == result["b"]["id"]


def test_self_join_no_collision():
    """Self-join: same column name on different aliases must not collide."""
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("a", "b"),
        [
            exp.GT(this=_col("a", "score", "INT"), expression=exp.Literal.number(80)),
            exp.LT(this=_col("b", "score", "INT"), expression=exp.Literal.number(50)),
        ],
    ))
    assert result is not None
    assert result["a"]["score"] > 80
    assert result["b"]["score"] < 50


def test_is_not_null():
    expr = exp.Is(
        this=_col("t1", "name", "TEXT"),
        expression=exp.Not(this=exp.Null()),
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["name"] is not None


def test_not_gt():
    """NOT(col > 10) should lower to col <= 10."""
    inner = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr = exp.Not(this=inner)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["age"] <= 10


def test_not_eq():
    """NOT(col = 5) should lower to col != 5."""
    inner = exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(5))
    expr = exp.Not(this=inner)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["x"] != 5


def test_in_list():
    expr = exp.In(
        this=_col("t1", "status", "TEXT"),
        expressions=[exp.Literal.string("active"), exp.Literal.string("pending")],
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["status"] in ("active", "pending")


def test_between():
    expr = exp.Between(
        this=_col("t1", "age", "INT"),
        low=exp.Literal.number(18),
        high=exp.Literal.number(65),
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert 18 <= result["t1"]["age"] <= 65


def test_bounds_propagation_across_eq():
    """a.x > 10 AND a.x = b.y → b.y should also be > 10."""
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("a", "b"),
        [exp.GT(this=_col("a", "x", "INT"), expression=exp.Literal.number(10))],
        join_equalities=[("a", "x", "b", "y")],
    ))
    assert result is not None
    assert result["b"]["y"] > 10


def test_column_column_equality():
    """a.x = b.y without join_equalities — should create eq constraint."""
    solver = DomainSolver()
    expr = exp.EQ(this=_col("a", "x", "INT"), expression=_col("b", "y", "INT"))
    result = solver.solve(_constraint(("a", "b"), [expr]))
    assert result is not None
    assert result["a"]["x"] == result["b"]["y"]


def test_returns_none_for_complex_expressions():
    """Domain solver can't handle arithmetic — should return None."""
    add = exp.Add(
        this=_col("t1", "x", "INT"),
        expression=_col("t1", "y", "INT"),
    )
    expr = exp.GT(this=add, expression=exp.Literal.number(10))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is None
