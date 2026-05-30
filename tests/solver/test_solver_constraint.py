from sqlglot import exp
from parseval.solver.unified import SolverConstraint, SolveResult


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = exp.DataType.build(dtype)
    return node


def test_solver_constraint_defaults():
    c = SolverConstraint(target_tables=("t1",))
    assert c.constraints == []
    assert c.join_equalities == []
    assert c.alias_map == {}
    assert c.atom is None


def test_solver_constraint_with_expressions():
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    c = SolverConstraint(
        target_tables=("t1",),
        constraints=[expr],
    )
    assert len(c.constraints) == 1


def test_solve_result_sat():
    r = SolveResult(sat=True, assignments={"t1": {"age": 20}})
    assert r.sat
    assert r.assignments["t1"]["age"] == 20


def test_solve_result_unsat():
    r = SolveResult(sat=False, reason="no solution")
    assert not r.sat
