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
from parseval.solver import Solver, SolverConstraint
from parseval.solver.types import SolverVar, set_solver_var


PEOPLE = relation_id(RelationKind.TABLE, identifier_name("people"))
A = relation_id(RelationKind.TABLE, identifier_name("people"), alias=identifier_name("a"))
B = relation_id(RelationKind.TABLE, identifier_name("people"), alias=identifier_name("b"))

PEOPLE_ID = column_id(ColumnKind.PHYSICAL, identifier_name("id"), PEOPLE)
PEOPLE_MANAGER_ID = column_id(ColumnKind.PHYSICAL, identifier_name("manager_id"), PEOPLE)
PEOPLE_NAME = column_id(ColumnKind.PHYSICAL, identifier_name("name"), PEOPLE)

A_ID = SolverVar(column_id=PEOPLE_ID, relation_id=A)
B_MANAGER_ID = SolverVar(column_id=PEOPLE_MANAGER_ID, relation_id=B)
A_NAME = SolverVar(column_id=PEOPLE_NAME, relation_id=A)
B_NAME = SolverVar(column_id=PEOPLE_NAME, relation_id=B)


def _col(var: SolverVar, dtype: str) -> exp.Column:
    node = exp.column(var.column_id.name.normalized, table=var.relation_id.display)
    node.type = DataType.build(dtype)
    set_solver_var(node, var)
    return node


def test_solver_assignments_are_keyed_by_solver_var():
    solver = Solver()
    constraint = SolverConstraint(
        target_relations=(A,),
        constraints=[
            exp.EQ(this=_col(A_NAME, "TEXT"), expression=exp.Literal.string("Alice")),
        ],
    )

    result = solver.solve(constraint)

    assert result.sat
    assert result.assignments == {A_NAME: "Alice"}


def test_self_join_keeps_same_physical_column_as_separate_solver_vars():
    solver = Solver()
    constraint = SolverConstraint(
        target_relations=(A, B),
        constraints=[
            exp.EQ(this=_col(A_NAME, "TEXT"), expression=exp.Literal.string("Alice")),
            exp.EQ(this=_col(B_NAME, "TEXT"), expression=exp.Literal.string("Bob")),
        ],
    )

    result = solver.solve(constraint)

    assert result.sat
    assert result.assignments[A_NAME] == "Alice"
    assert result.assignments[B_NAME] == "Bob"


def test_join_equalities_use_solver_vars_directly():
    solver = Solver()
    constraint = SolverConstraint(
        target_relations=(A, B),
        constraints=[
            exp.GT(this=_col(A_ID, "INT"), expression=exp.Literal.number(0)),
        ],
        join_equalities=[(A_ID, B_MANAGER_ID)],
    )

    result = solver.solve(constraint)

    assert result.sat
    assert result.assignments[A_ID] == result.assignments[B_MANAGER_ID]
