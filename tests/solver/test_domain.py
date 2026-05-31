"""Tests for the DomainSolver — CSP-lite with ValueSpace narrowing."""
from sqlglot import exp

from parseval.solver.domain import DomainSolver
from parseval.solver.unified import SolverConstraint


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = exp.DataType.build(dtype)
    return node


def _constraint(tables, expressions=None, join_equalities=None, alias_map=None):
    return SolverConstraint(
        target_tables=tables,
        constraints=expressions or [],
        join_equalities=join_equalities or [],
        alias_map=alias_map or {},
    )


def _sat_assignments(result):
    assert result.status == "sat"
    assert result.assignments is not None
    assert result.reason == ""
    return result.assignments


def test_domain_returns_sat_result_for_simple_equality():
    expr = exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25))
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "sat"
    assert result.assignments == {"t1": {"age": 25}}
    assert result.reason == ""


def test_domain_returns_unsat_for_conflicting_equalities():
    result = DomainSolver().solve(_constraint(
        ("t1",),
        [
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25)),
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(30)),
        ],
    ))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason


def test_domain_returns_unknown_for_arithmetic_predicate():
    expr = exp.GT(
        this=exp.Add(this=_col("t1", "x", "INT"), expression=_col("t1", "y", "INT")),
        expression=exp.Literal.number(10),
    )
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_arithmetic"


def test_domain_returns_unknown_for_mixed_supported_and_unsupported_and():
    supported = exp.EQ(this=_col("t1", "name", "TEXT"), expression=exp.Literal.string("Alice"))
    unsupported = exp.GT(
        this=exp.Add(this=_col("t1", "a", "INT"), expression=_col("t1", "b", "INT")),
        expression=exp.Literal.number(1000),
    )
    result = DomainSolver().solve(
        _constraint(("t1",), [exp.And(this=supported, expression=unsupported)]),
    )
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_arithmetic"


def test_domain_returns_unknown_for_not_or_expression():
    expr = exp.Not(this=exp.Or(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(2)),
    ))
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_not"


def test_domain_returns_unsat_for_or_with_two_unsat_branches():
    left = exp.And(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(2)),
    )
    right = exp.And(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(3)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(4)),
    )
    result = DomainSolver().solve(_constraint(("t1",), [exp.Or(this=left, expression=right)]))
    assert result.status == "unsat"
    assert result.assignments is None


def test_domain_top_level_unknown_does_not_mask_unsat():
    unsupported = exp.GT(
        this=exp.Add(this=_col("t1", "a", "INT"), expression=_col("t1", "b", "INT")),
        expression=exp.Literal.number(1000),
    )
    unsat = exp.And(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(2)),
    )
    result = DomainSolver().solve(_constraint(("t1",), [unsupported, unsat]))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason == "contradictory_bounds"


def test_domain_returns_unknown_for_or_followed_by_and():
    disjunction = exp.Or(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "y", "INT"), expression=exp.Literal.number(2)),
    )
    conjunction = exp.And(
        this=disjunction,
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(3)),
    )
    result = DomainSolver().solve(_constraint(("t1",), [conjunction]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_or"


def test_domain_returns_unknown_for_unsat_or_unknown():
    unsat = exp.And(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(2)),
    )
    unknown = exp.GT(
        this=exp.Add(this=_col("t1", "a", "INT"), expression=_col("t1", "b", "INT")),
        expression=exp.Literal.number(1000),
    )
    result = DomainSolver().solve(_constraint(("t1",), [exp.Or(this=unsat, expression=unknown)]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_arithmetic"


def test_domain_preserves_alias_assignment_keys():
    expr = exp.EQ(this=_col("a", "name", "TEXT"), expression=exp.Literal.string("Alice"))
    result = DomainSolver().solve(_constraint(
        ("a", "b"),
        [expr],
        alias_map={"a": "people", "b": "people"},
    ))
    assignments = _sat_assignments(result)
    assert "a" in assignments
    assert "people" not in assignments
    assert assignments["a"]["name"] == "Alice"


def test_simple_equality():
    expr = exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["age"] == 25


def test_greater_than():
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["age"] > 18


def test_less_than():
    expr = exp.LT(this=_col("t1", "score", "INT"), expression=exp.Literal.number(100))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["score"] < 100


def test_conjunction():
    expr1 = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr2 = exp.LT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(20))
    expr = exp.And(this=expr1, expression=expr2)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert 10 < assignments["t1"]["age"] < 20


def test_is_null():
    expr = exp.Is(this=_col("t1", "name", "TEXT"), expression=exp.Null())
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["name"] is None


def test_join_equality():
    expr = exp.GT(this=_col("t1", "id", "INT"), expression=exp.Literal.number(0))
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("t1", "t2"), [expr],
        join_equalities=[("t1", "id", "t2", "t1_id")],
    ))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["id"] == assignments["t2"]["t1_id"]


def test_empty_constraints():
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",)))
    assignments = _sat_assignments(result)
    assert "t1" in assignments


def test_not_equal():
    expr = exp.NEQ(this=_col("t1", "status", "TEXT"), expression=exp.Literal.string("deleted"))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["status"] != "deleted"


def test_gte():
    expr = exp.GTE(this=_col("t1", "count", "INT"), expression=exp.Literal.number(5))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["count"] >= 5


def test_lte():
    expr = exp.LTE(this=_col("t1", "count", "INT"), expression=exp.Literal.number(100))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["count"] <= 100


def test_multiple_tables():
    expr1 = exp.EQ(this=_col("t1", "id", "INT"), expression=exp.Literal.number(1))
    expr2 = exp.EQ(this=_col("t2", "name", "TEXT"), expression=exp.Literal.string("Alice"))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1", "t2"), [expr1, expr2]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["id"] == 1
    assert assignments["t2"]["name"] == "Alice"


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
    assignments = _sat_assignments(result)
    # Each alias should get its own value
    assert assignments["a"]["name"] == "Alice"
    assert assignments["b"]["name"] == "Bob"
    # Join equality should hold
    assert assignments["a"]["manager_id"] == assignments["b"]["id"]


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
    assignments = _sat_assignments(result)
    assert assignments["a"]["score"] > 80
    assert assignments["b"]["score"] < 50


def test_is_not_null():
    expr = exp.Is(
        this=_col("t1", "name", "TEXT"),
        expression=exp.Not(this=exp.Null()),
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["name"] is not None


def test_empty_boolean_domain_is_unsat():
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("t1",),
        [
            exp.NEQ(this=_col("t1", "flag", "BOOLEAN"), expression=exp.Boolean(this=True)),
            exp.NEQ(this=_col("t1", "flag", "BOOLEAN"), expression=exp.Boolean(this=False)),
        ],
    ))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason == "contradictory_bounds"


def test_boolean_or_branch_prefers_sat_branch_when_other_is_unsat():
    solver = DomainSolver()
    left = exp.Paren(this=exp.And(
        this=exp.NEQ(this=_col("t1", "flag", "BOOLEAN"), expression=exp.Boolean(this=True)),
        expression=exp.NEQ(this=_col("t1", "flag", "BOOLEAN"), expression=exp.Boolean(this=False)),
    ))
    right = exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1))
    result = solver.solve(_constraint(("t1",), [exp.Or(this=left, expression=right)]))
    assert result.status == "sat"
    assert result.assignments is not None
    assert result.assignments["t1"]["x"] == 1
    assert result.reason == ""


def test_left_associated_boolean_or_branch_prefers_sat_branch():
    solver = DomainSolver()
    left = exp.Paren(this=exp.And(
        this=exp.And(
            this=exp.Is(
                this=_col("t1", "flag", "BOOLEAN"),
                expression=exp.Not(this=exp.Null()),
            ),
            expression=exp.NEQ(
                this=_col("t1", "flag", "BOOLEAN"),
                expression=exp.Boolean(this=True),
            ),
        ),
        expression=exp.NEQ(
            this=_col("t1", "flag", "BOOLEAN"),
            expression=exp.Boolean(this=False),
        ),
    ))
    right = exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1))
    result = solver.solve(_constraint(("t1",), [exp.Or(this=left, expression=right)]))
    assert result.status == "sat"
    assert result.assignments is not None
    assert result.assignments["t1"]["x"] == 1
    assert result.reason == ""


def test_null_and_not_null_conflict_is_unsat():
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("t1",),
        [
            exp.Is(this=_col("t1", "name", "TEXT"), expression=exp.Null()),
            exp.Is(
                this=_col("t1", "name", "TEXT"),
                expression=exp.Not(this=exp.Null()),
            ),
        ],
    ))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason == "contradictory_bounds"


def test_null_sentinel_does_not_force_text_column_boolean_family():
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("t1",),
        [
            exp.Is(
                this=_col("t1", "name", "TEXT"),
                expression=exp.Not(this=exp.Null()),
            ),
            exp.NEQ(this=_col("t1", "name", "TEXT"), expression=exp.Boolean(this=True)),
            exp.NEQ(this=_col("t1", "name", "TEXT"), expression=exp.Boolean(this=False)),
        ],
    ))
    assert result.status == "sat"
    assert result.assignments is not None
    assert result.assignments["t1"]["name"] is not None
    assert result.reason == ""


def test_not_gt():
    """NOT(col > 10) should lower to col <= 10."""
    inner = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr = exp.Not(this=inner)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["age"] <= 10


def test_not_eq():
    """NOT(col = 5) should lower to col != 5."""
    inner = exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(5))
    expr = exp.Not(this=inner)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["x"] != 5


def test_in_list():
    expr = exp.In(
        this=_col("t1", "status", "TEXT"),
        expressions=[exp.Literal.string("active"), exp.Literal.string("pending")],
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["t1"]["status"] in ("active", "pending")


def test_between():
    expr = exp.Between(
        this=_col("t1", "age", "INT"),
        low=exp.Literal.number(18),
        high=exp.Literal.number(65),
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assignments = _sat_assignments(result)
    assert 18 <= assignments["t1"]["age"] <= 65


def test_bounds_propagation_across_eq():
    """a.x > 10 AND a.x = b.y → b.y should also be > 10."""
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("a", "b"),
        [exp.GT(this=_col("a", "x", "INT"), expression=exp.Literal.number(10))],
        join_equalities=[("a", "x", "b", "y")],
    ))
    assignments = _sat_assignments(result)
    assert assignments["b"]["y"] > 10


def test_column_column_equality():
    """a.x = b.y without join_equalities — should create eq constraint."""
    solver = DomainSolver()
    expr = exp.EQ(this=_col("a", "x", "INT"), expression=_col("b", "y", "INT"))
    result = solver.solve(_constraint(("a", "b"), [expr]))
    assignments = _sat_assignments(result)
    assert assignments["a"]["x"] == assignments["b"]["y"]


def test_boolean_equality_exclusions_can_make_eq_unsat():
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("a", "b"),
        [
            exp.EQ(this=_col("a", "flag", "BOOLEAN"), expression=_col("b", "flag", "BOOLEAN")),
            exp.NEQ(this=_col("a", "flag", "BOOLEAN"), expression=exp.Boolean(this=True)),
            exp.NEQ(this=_col("b", "flag", "BOOLEAN"), expression=exp.Boolean(this=False)),
        ],
    ))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason == "contradictory_bounds"


def test_returns_unknown_for_complex_expressions():
    """Domain solver can't handle arithmetic — should return unknown."""
    add = exp.Add(
        this=_col("t1", "x", "INT"),
        expression=_col("t1", "y", "INT"),
    )
    expr = exp.GT(this=add, expression=exp.Literal.number(10))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_arithmetic"
