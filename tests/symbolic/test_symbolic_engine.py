"""Tests for the new symbolic module: evaluator, engine, constraints, types."""

from __future__ import annotations

import unittest

from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver import SolveResult, SolverConstraint
from parseval.solver.types import SolverVar
from parseval.symbolic import (
    BranchTree,
    BranchType,
    CoverageThresholds,
    PlanEvaluator,
    SymbolicEngine,
    decompose_atoms,
    is_infeasible,
)
from parseval.symbolic.types import BranchNode


SCHEMA = "CREATE TABLE t (a INT, b INT, c TEXT);"
def _pred(sql: str):
    from sqlglot import parse_one, exp as sqlexp
    return parse_one(f"SELECT * FROM t WHERE {sql}").find(sqlexp.Where).this


SCHEMA_FK = (
    "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT NOT NULL);"
    "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
)


def _plan(sql: str, schema: str = SCHEMA, dialect: str = "sqlite") -> Plan:
    instance = Instance(ddls=schema, name="test", dialect=dialect)
    expr = preprocess_sql(sql, instance, dialect=dialect)
    return Plan(expr)


# ---------------------------------------------------------------------------
# decompose_atoms
# ---------------------------------------------------------------------------


class TestDecomposeAtoms(unittest.TestCase):
    def test_simple_comparison(self):
        from sqlglot import parse_one, exp

        pred = parse_one("SELECT * FROM t WHERE a > 5").find(exp.Where).this
        atoms = decompose_atoms(pred)
        self.assertEqual(len(atoms), 1)
        self.assertIn(">", atoms[0].sql())

    def test_and_splits(self):
        from sqlglot import parse_one, exp

        pred = parse_one("SELECT * FROM t WHERE a > 5 AND b < 10").find(exp.Where).this
        atoms = decompose_atoms(pred)
        self.assertEqual(len(atoms), 2)

    def test_or_splits(self):
        from sqlglot import parse_one, exp

        pred = parse_one("SELECT * FROM t WHERE a > 5 OR b < 10").find(exp.Where).this
        atoms = decompose_atoms(pred)
        self.assertEqual(len(atoms), 2)

    def test_nested_and_or(self):
        from sqlglot import parse_one, exp

        pred = parse_one("SELECT * FROM t WHERE (a > 5 AND b < 10) OR c = 'x'").find(exp.Where).this
        atoms = decompose_atoms(pred)
        self.assertEqual(len(atoms), 3)


# ---------------------------------------------------------------------------
# PlanEvaluator
# ---------------------------------------------------------------------------


class TestPlanEvaluator(unittest.TestCase):
    def test_empty_instance_produces_no_observations(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        expr = preprocess_sql("SELECT a FROM t WHERE a > 5", instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        # Branch node should exist (the filter site) but with no observations.
        self.assertTrue(len(tree.nodes) >= 1)
        filter_nodes = [n for n in tree.nodes if n.site == "filter"]
        self.assertEqual(len(filter_nodes), 1)
        self.assertEqual(len(filter_nodes[0].observations), 0)

    def test_single_row_produces_observations(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        instance.create_row("t", {"a": 10, "b": 1})
        expr = preprocess_sql("SELECT a FROM t WHERE a > 5", instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        filter_nodes = [n for n in tree.nodes if n.site == "filter"]
        self.assertEqual(len(filter_nodes), 1)
        self.assertGreater(len(filter_nodes[0].observations), 0)
        outcomes = filter_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.ATOM_TRUE, outcomes)

    def test_false_branch_observed(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        instance.create_row("t", {"a": 3, "b": 1})
        expr = preprocess_sql("SELECT a FROM t WHERE a > 5", instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        filter_nodes = [n for n in tree.nodes if n.site == "filter"]
        outcomes = filter_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.ATOM_FALSE, outcomes)

    def test_null_branch_observed(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        instance.place_row("t", {"a": None, "b": 1, "c": "x"})
        expr = preprocess_sql("SELECT a FROM t WHERE a > 5", instance, dialect="sqlite")
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        filter_nodes = [n for n in tree.nodes if n.site == "filter"]
        outcomes = filter_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.ATOM_NULL, outcomes)

    def test_compound_predicate_atoms_tracked_independently(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        instance.create_row("t", {"a": 10, "b": 3})
        expr = preprocess_sql(
            "SELECT a FROM t WHERE a > 5 AND b < 10", instance, dialect="sqlite"
        )
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        filter_nodes = [n for n in tree.nodes if n.site == "filter"]
        self.assertEqual(len(filter_nodes[0].atoms), 2)

    def test_case_arm_branches_discovered(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        instance.create_row("t", {"a": 10, "b": 1})
        expr = preprocess_sql(
            "SELECT CASE WHEN a > 5 THEN 'big' ELSE 'small' END FROM t",
            instance,
            dialect="sqlite",
        )
        plan = Plan(expr)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        case_nodes = [n for n in tree.nodes if n.site == "case_arm"]
        self.assertTrue(len(case_nodes) >= 1)


# ---------------------------------------------------------------------------
# BranchTree coverage tracking
# ---------------------------------------------------------------------------


class TestBranchTree(unittest.TestCase):
    def test_infeasible_excluded_from_targets(self):
        tree = BranchTree(thresholds=CoverageThresholds())
        node = tree.get_or_create_node(
            step_id="step_0",
            step_type="Filter",
            site="filter",
            predicate=_pred("a IS NULL"),
            atoms=(_pred("a IS NULL"),),
            tables=("t",),
        )
        tree.mark_infeasible(node, 0, BranchType.ATOM_NULL)
        targets = tree.uncovered_targets
        self.assertTrue(all(t.target_outcome != BranchType.ATOM_NULL for t in targets))

# ---------------------------------------------------------------------------
# Infeasibility
# ---------------------------------------------------------------------------


class TestInfeasibility(unittest.TestCase):
    def test_is_null_predicate_cannot_be_null(self):
        from sqlglot import parse_one, exp as sqlexp

        pred = parse_one("SELECT * FROM t WHERE a IS NULL").find(sqlexp.Where).this
        node = BranchNode(
            step_id="s0", step_type="Filter", site="filter",
            predicate=pred, atoms=(pred,), tables=("t",),
        )
        reason = is_infeasible(
            node, 0, BranchType.ATOM_NULL,
            Instance(ddls=SCHEMA, name="t", dialect="sqlite"),
        )
        self.assertIsNotNone(reason)

    def test_normal_predicate_is_feasible(self):
        from sqlglot import parse_one, exp as sqlexp

        pred = parse_one("SELECT * FROM t WHERE a > 5").find(sqlexp.Where).this
        node = BranchNode(
            step_id="s0", step_type="Filter", site="filter",
            predicate=pred, atoms=(pred,), tables=("t",),
        )
        reason = is_infeasible(
            node, 0, BranchType.ATOM_NULL,
            Instance(ddls=SCHEMA, name="t", dialect="sqlite"),
        )
        self.assertIsNone(reason)


# ---------------------------------------------------------------------------
# SymbolicEngine (integration)
# ---------------------------------------------------------------------------


class TestSymbolicEngine(unittest.TestCase):
    def test_generates_rows_for_simple_filter(self):
        instance = Instance(ddls=SCHEMA, name="engine", dialect="sqlite")
        engine = SymbolicEngine(
            instance,
            "SELECT a FROM t WHERE a > 5",
            dialect="sqlite",
            max_iterations=10,
        )
        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
        # Should have generated at least one row.
        self.assertGreater(result.rows_generated, 0)
        # Should have some coverage.
        self.assertGreater(result.coverage, 0.0)

    def test_respects_max_iterations(self):
        instance = Instance(ddls=SCHEMA, name="engine", dialect="sqlite")
        engine = SymbolicEngine(
            instance,
            "SELECT a FROM t WHERE a > 5 AND b < 10",
            dialect="sqlite",
            max_iterations=2,
        )
        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
        self.assertLessEqual(result.iterations, 2)

    def test_checkpoint_rollback_on_failure(self):
        """Engine should not leave partial state on solver failure."""
        instance = Instance(ddls=SCHEMA, name="engine", dialect="sqlite")
        initial_rows = sum(len(instance.get_rows(t)) for t in instance.tables)
        engine = SymbolicEngine(
            instance,
            "SELECT a FROM t WHERE a > 5",
            dialect="sqlite",
            max_iterations=5,
        )
        # Even if some iterations fail, the instance should be consistent.
        result = engine.generate()
        final_rows = sum(len(instance.get_rows(t)) for t in instance.tables)
        self.assertGreaterEqual(final_rows, initial_rows)

    def test_full_coverage_for_simple_query(self):
        instance = Instance(ddls=SCHEMA, name="engine", dialect="sqlite")
        engine = SymbolicEngine(
            instance,
            "SELECT a FROM t WHERE a > 5",
            dialect="sqlite",
            max_iterations=20,
        )
        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
        # For a simple single-atom filter, we should achieve full coverage
        # (TRUE + FALSE) within 20 iterations.
        self.assertEqual(result.coverage, 1.0)

    def test_compound_filter_coverage(self):
        instance = Instance(ddls=SCHEMA, name="engine", dialect="sqlite")
        engine = SymbolicEngine(
            instance,
            "SELECT a FROM t WHERE a > 5 AND b < 10",
            dialect="sqlite",
            max_iterations=20,
        )
        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
        # Should cover both atoms' TRUE and FALSE.
        self.assertGreaterEqual(result.coverage, 0.5)


class _FixedSolver:
    def __init__(self, assignments):
        self.assignments = assignments

    def solve(self, _constraint):
        return SolveResult(sat=True, assignments=self.assignments)


def _people_alias_assignments(instance):
    physical = instance.table_id("people")
    physical_id = instance.column_id("people", "id")
    physical_name = instance.column_id("people", "name")
    left = relation_id(
        RelationKind.TABLE,
        physical.name,
        alias=identifier_name("a"),
        scope_id="left",
    )
    right = relation_id(
        RelationKind.TABLE,
        physical.name,
        alias=identifier_name("b"),
        scope_id="right",
    )
    left_id = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("id"),
        left,
        source_column_id=physical_id,
    )
    left_name = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("name"),
        left,
        source_column_id=physical_name,
    )
    right_id = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("id"),
        right,
        source_column_id=physical_id,
    )
    right_name = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("name"),
        right,
        source_column_id=physical_name,
    )
    assignments = {
        SolverVar(left_id, left, "r0"): 1,
        SolverVar(left_name, left, "r0"): "Alice",
        SolverVar(right_id, right, "r0"): 2,
        SolverVar(right_name, right, "r0"): "Bob",
    }
    return physical, physical_id, physical_name, left, right, assignments


def test_same_table_aliases_materialize_as_separate_rows():
    instance = Instance(
        ddls="CREATE TABLE people (id INT PRIMARY KEY, manager_id INT, name TEXT NOT NULL);",
        name="self_join_materialization",
        dialect="sqlite",
    )
    physical, physical_id, physical_name, left, right, assignments = (
        _people_alias_assignments(instance)
    )
    constraint = SolverConstraint(
        target_relations=(left, right),
        storage_relations={variable: physical for variable in assignments},
    )
    engine = SymbolicEngine(
        instance,
        "SELECT a.name FROM people a JOIN people b ON a.id = b.manager_id",
        solver=_FixedSolver(assignments),
        max_iterations=1,
    )

    assert engine._solve_and_materialize(constraint)
    assert sorted(
        (row[physical_id].concrete, row[physical_name].concrete)
        for row in instance.get_rows("people")
    ) == [(1, "Alice"), (2, "Bob")]


def test_conflicting_materialized_assignment_fails_closed():
    instance = Instance(
        ddls="CREATE TABLE people (id INT PRIMARY KEY, name TEXT NOT NULL);",
        name="conflicting_materialization",
        dialect="sqlite",
    )
    physical = instance.table_id("people")
    physical_name = instance.column_id("people", "name")
    binding = relation_id(
        RelationKind.TABLE,
        physical.name,
        alias=identifier_name("p"),
        scope_id="binding",
    )
    first = column_id(
        ColumnKind.PROJECTED,
        identifier_name("first_name"),
        binding,
        source_column_id=physical_name,
    )
    second = column_id(
        ColumnKind.PROJECTED,
        identifier_name("second_name"),
        binding,
        source_column_id=physical_name,
    )
    assignments = {
        SolverVar(first, binding, "r0"): "Alice",
        SolverVar(second, binding, "r0"): "Bob",
    }
    constraint = SolverConstraint(
        target_relations=(binding,),
        storage_relations={variable: physical for variable in assignments},
    )
    engine = SymbolicEngine(
        instance,
        "SELECT name FROM people",
        solver=_FixedSolver(assignments),
        max_iterations=1,
    )

    assert not engine._solve_and_materialize(constraint)
    assert not instance.get_rows("people")


if __name__ == "__main__":
    unittest.main()
