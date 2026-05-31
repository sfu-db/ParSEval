"""Tests for the SMTSolver — Z3 backend without Instance dependency."""
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.solver.smt import SMTSolver


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = DataType.build(dtype)
    return node


def test_integer_gt():
    solver = SMTSolver()
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.age"] > 18


def test_text_equality():
    solver = SMTSolver()
    expr = exp.EQ(
        this=_col("t1", "name", "TEXT"),
        expression=exp.Literal.string("Alice"),
    )
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.name"] == "Alice"


def test_conjunction():
    solver = SMTSolver()
    gt = exp.GT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(0))
    lt = exp.LT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(100))
    expr = exp.And(this=gt, expression=lt)
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert 0 < model["t1.x"] < 100


def test_unsat():
    solver = SMTSolver()
    gt = exp.GT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(100))
    lt = exp.LT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(0))
    expr = exp.And(this=gt, expression=lt)
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "unsat"


def test_declare_variable_and_solve():
    solver = SMTSolver()
    solver.declare_variable("t1.id", DataType.build("INT"))
    # Use a Column EQ expression so the solver translates it properly
    expr = exp.EQ(this=_col("t1", "id", "INT"), expression=exp.Literal.number(42))
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.id"] == 42


def test_float_comparison():
    solver = SMTSolver()
    expr = exp.GTE(
        this=_col("t1", "score", "FLOAT"),
        expression=exp.Literal.number(3.14),
    )
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.score"] >= 3.14


def test_or_expression():
    solver = SMTSolver()
    eq1 = exp.EQ(this=_col("t1", "status", "TEXT"), expression=exp.Literal.string("active"))
    eq2 = exp.EQ(this=_col("t1", "status", "TEXT"), expression=exp.Literal.string("pending"))
    expr = exp.Or(this=eq1, expression=eq2)
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.status"] in ("active", "pending")


def test_not_expression():
    solver = SMTSolver()
    eq = exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(0))
    expr = exp.Not(this=eq)
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.x"] != 0


def test_addition():
    solver = SMTSolver()
    add = exp.Add(
        this=_col("t1", "a", "INT"),
        expression=_col("t1", "b", "INT"),
    )
    expr = exp.GT(this=add, expression=exp.Literal.number(10))
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.a"] + model["t1.b"] > 10


def test_no_instance_param():
    """Verify SMTSolver does not accept instance parameter."""
    solver = SMTSolver()
    assert not hasattr(solver, "instance") or solver.instance is None


def test_unsupported_expression_translate_returns_none():
    solver = SMTSolver()
    expr = exp.GT(
        this=exp.Anonymous(
            this="MISSINGFUNC",
            expressions=[_col("t1", "age", "INT")],
        ),
        expression=exp.Literal.number(10),
    )

    assert solver.translate(expr) is None
