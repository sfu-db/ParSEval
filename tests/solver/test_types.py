"""Tests for solver shared types."""
from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.dtype import DataType, TypeFamily
from parseval.domain.value_space import ValueSpace
from parseval.solver.types import (
    Problem,
    Result,
    SolverVar,
    collect_problem_variables,
    node_dtype,
)


INT = DataType.build("INT")
AGE = SolverVar(key="t1.age", dtype=INT, meta={"name": "age"})
ID = SolverVar(key="t1.id", dtype=INT, meta={"name": "id"})


class TestSolverVar(unittest.TestCase):
    def test_identity_is_key_only(self):
        a = SolverVar(key="t.age", dtype=INT)
        b = SolverVar(key="t.age", dtype=DataType.build("TEXT"))
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))

    def test_different_keys_unequal(self):
        self.assertNotEqual(
            SolverVar(key="t.age", dtype=INT),
            SolverVar(key="t.id", dtype=INT),
        )

    def test_meta_is_caller_owned(self):
        var = SolverVar(
            key="t1.age#r0",
            dtype=INT,
            meta={"name": "age", "row_scope": "r0"},
        )
        self.assertEqual(var.meta["name"], "age")
        self.assertEqual(var.meta["row_scope"], "r0")
        twin = SolverVar(key="t1.age#r0", dtype=INT, meta={"other": 1})
        self.assertEqual(var, twin)

    def test_copy_preserves_key_identity(self):
        original = SolverVar(key="t.age", dtype=INT)
        copied = original.copy()
        self.assertIsNot(original, copied)
        self.assertEqual(original, copied)
        self.assertEqual(hash(original), hash(copied))
        self.assertEqual(original.var_key, copied.var_key)

    def test_is_expression_leaf(self):
        self.assertIsInstance(AGE, exp.Expression)
        expr = exp.GT(this=AGE, expression=exp.Literal.number(5))
        self.assertEqual(list(expr.find_all(SolverVar)), [AGE])


class TestValueSpace(unittest.TestCase):
    def test_narrow_eq_pick(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_eq(42)
        self.assertEqual(vs.pick(), 42)

    def test_inverted_range_empty(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_min(20)
        vs.narrow_max(10)
        self.assertTrue(vs.is_empty())


class TestAnnotations(unittest.TestCase):
    def test_node_dtype_from_solver_var(self):
        self.assertEqual(node_dtype(AGE), INT)


class TestResult(unittest.TestCase):
    def test_sat_property(self):
        self.assertTrue(Result(status="sat").sat)
        self.assertFalse(Result(status="unsat", reason="x").sat)


class TestProblem(unittest.TestCase):
    def test_empty_defaults(self):
        problem = Problem()
        self.assertEqual(problem.constraints, [])
        self.assertEqual(problem.equalities, [])


class TestCollectProblemVariables(unittest.TestCase):
    def test_includes_explicit_constraint_and_equality_variables_sorted_by_key(self):
        explicit = SolverVar(key="c.explicit", dtype=INT)
        constrained = SolverVar(key="a.constrained", dtype=INT)
        left = SolverVar(key="b.left", dtype=INT)
        right = SolverVar(key="d.right", dtype=INT)
        problem = Problem(
            constraints=[
                exp.GT(this=constrained, expression=exp.Literal.number(5)),
            ],
            equalities=[(left, right)],
            variables={explicit},
        )

        variables = collect_problem_variables(problem)

        self.assertEqual(
            [variable.var_key for variable in variables],
            ["a.constrained", "b.left", "c.explicit", "d.right"],
        )


class TestPartition(unittest.TestCase):
    def test_and_does_not_merge_independent_atoms(self):
        from parseval.solver.partition import partition

        a = SolverVar(key="t1.age", dtype=INT)
        b = SolverVar(key="t1.id", dtype=INT)
        problem = Problem(
            constraints=[
                exp.And(
                    this=exp.GT(this=a, expression=exp.Literal.number(5)),
                    expression=exp.GT(this=b, expression=exp.Literal.number(1)),
                )
            ]
        )
        components = partition(problem)
        self.assertEqual(len(components), 2)
        var_sets = {frozenset(c.variables) for c in components}
        self.assertEqual(var_sets, {frozenset({a}), frozenset({b})})

    def test_atom_with_two_vars_stays_one_component(self):
        from parseval.solver.partition import partition

        a = SolverVar(key="t1.age", dtype=INT)
        b = SolverVar(key="t1.id", dtype=INT)
        problem = Problem(
            constraints=[exp.EQ(this=a, expression=b)]
        )
        components = partition(problem)
        self.assertEqual(len(components), 1)
        self.assertEqual(components[0].variables, {a, b})


if __name__ == "__main__":
    unittest.main()
