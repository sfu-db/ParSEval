"""Tests for the unified Solver — domain + SMT orchestrator."""
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.solver.unified import Solver, SolverConstraint, SolveResult


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = DataType.build(dtype)
    return node


def test_solve_simple_equality():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["age"] == 25


def test_solve_gt():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["age"] > 18


def test_solve_empty():
    solver = Solver()
    constraint = SolverConstraint(target_tables=("t1",))
    result = solver.solve(constraint)
    assert result.sat


def test_solve_join_equality():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1", "t2"),
        constraints=[
            exp.GT(this=_col("t1", "id", "INT"), expression=exp.Literal.number(0)),
        ],
        join_equalities=[("t1", "id", "t2", "t1_id")],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["id"] == result.assignments["t2"]["t1_id"]


def test_solve_is_null():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.Is(this=_col("t1", "name", "TEXT"), expression=exp.Null()),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["name"] is None


def test_solve_conjunction():
    solver = Solver()
    gt = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    lt = exp.LT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(20))
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[exp.And(this=gt, expression=lt)],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert 10 < result.assignments["t1"]["age"] < 20


def test_solve_complex_expression():
    """Test that complex expressions fall through to SMT."""
    solver = Solver()
    add = exp.Add(
        this=_col("t1", "a", "INT"),
        expression=_col("t1", "b", "INT"),
    )
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.GT(this=add, expression=exp.Literal.number(10)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat


def test_complex_expression_uses_smt():
    """Arithmetic expressions should fall through to SMT solver."""
    solver = Solver()
    add = exp.Add(
        this=_col("t1", "a", "INT"),
        expression=_col("t1", "b", "INT"),
    )
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[exp.GT(this=add, expression=exp.Literal.number(10))],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["a"] + result.assignments["t1"]["b"] > 10


def test_solve_multiple_constraints():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "name", "TEXT"), expression=exp.Literal.string("Alice")),
            exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(20)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["name"] == "Alice"
    assert result.assignments["t1"]["age"] > 20


def test_solve_result_structure():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(42)),
        ],
    )
    result = solver.solve(constraint)
    assert isinstance(result, SolveResult)
    assert result.sat
    assert isinstance(result.assignments, dict)
    assert "t1" in result.assignments


def test_solver_no_instance():
    """Verify Solver does not accept instance parameter."""
    solver = Solver()
    assert not hasattr(solver, "instance")


def test_result_uses_physical_table_names():
    """When alias_map maps aliases to physical tables, result keys should be physical names."""
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("a", "b"),
        constraints=[
            exp.EQ(this=_col("a", "name", "TEXT"), expression=exp.Literal.string("Alice")),
        ],
        alias_map={"a": "people", "b": "people"},
    )
    result = solver.solve(constraint)
    assert result.sat
    # Keys should be physical table names, not aliases
    assert "people" in result.assignments
    assert "a" not in result.assignments


def test_self_join_preserves_alias_rows():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("a", "b"),
        constraints=[
            exp.EQ(this=_col("a", "name", "TEXT"), expression=exp.Literal.string("Alice")),
            exp.EQ(this=_col("b", "name", "TEXT"), expression=exp.Literal.string("Bob")),
        ],
        alias_map={"a": "people", "b": "people"},
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["a"]["name"] == "Alice"
    assert result.assignments["b"]["name"] == "Bob"
    assert "people" not in result.assignments


def test_smt_self_join_preserves_alias_rows():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("a", "b"),
        constraints=[
            exp.GT(
                this=exp.Add(
                    this=_col("a", "age", "INT"),
                    expression=_col("b", "age", "INT"),
                ),
                expression=exp.Literal.number(10),
            ),
        ],
        alias_map={"a": "people", "b": "people"},
    )
    result = solver.solve(constraint)
    assert result.sat
    assert "a" in result.assignments
    assert "b" in result.assignments
    assert "age" in result.assignments["a"]
    assert "age" in result.assignments["b"]
    assert "people" not in result.assignments


def test_rejects_unannotated_columns():
    """Solver should reject columns without type annotations."""
    solver = Solver()
    col = exp.column("age", table="t1")
    expr = exp.GT(this=col, expression=exp.Literal.number(18))
    constraint = SolverConstraint(target_tables=("t1",), constraints=[expr])
    result = solver.solve(constraint)
    assert not result.sat
    assert "type annotation" in result.reason


def test_accepts_annotated_columns():
    """Solver should accept columns with type annotations."""
    solver = Solver()
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    constraint = SolverConstraint(target_tables=("t1",), constraints=[expr])
    result = solver.solve(constraint)
    assert result.sat


def test_skips_smt_when_domain_returns_unsat(monkeypatch):
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25)),
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(30)),
        ],
    )

    def fail_if_called(_constraint):
        raise AssertionError("SMT fallback should not run for a domain unsat result")

    monkeypatch.setattr(solver, "_try_smt", fail_if_called)

    result = solver.solve(constraint)

    assert not result.sat
    assert result.reason == "contradictory_bounds"


def test_uses_smt_only_for_domain_unknown():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "name", "TEXT"), expression=exp.Literal.string("Alice")),
            exp.GT(
                this=exp.Add(
                    this=_col("t1", "a", "INT"),
                    expression=_col("t1", "b", "INT"),
                ),
                expression=exp.Literal.number(10),
            ),
        ],
    )

    result = solver.solve(constraint)

    assert result.sat
    assert result.assignments["t1"]["name"] == "Alice"
    assert result.assignments["t1"]["a"] + result.assignments["t1"]["b"] > 10


def test_rejects_partial_smt_translation():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "name", "TEXT"), expression=exp.Literal.string("Alice")),
            exp.GT(
                this=exp.Anonymous(
                    this="MISSINGFUNC",
                    expressions=[_col("t1", "age", "INT")],
                ),
                expression=exp.Literal.number(10),
            ),
        ],
    )

    result = solver.solve(constraint)

    assert not result.sat
    assert result.reason == "unsupported_smt_expression"


def test_smt_join_equalities_resolve_physical_tables_into_alias_space():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("a", "b"),
        constraints=[
            exp.EQ(this=_col("a", "id", "INT"), expression=exp.Literal.number(1)),
            exp.GT(this=_col("b", "manager_id", "INT"), expression=exp.Literal.number(1)),
            exp.GT(
                this=exp.Add(
                    this=_col("a", "age", "INT"),
                    expression=_col("b", "age", "INT"),
                ),
                expression=exp.Literal.number(10),
            ),
        ],
        join_equalities=[("people", "id", "people", "manager_id")],
        alias_map={"a": "people", "b": "people"},
    )

    result = solver.solve(constraint)

    assert not result.sat
