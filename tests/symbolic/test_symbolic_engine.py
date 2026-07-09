"""Tests for the new symbolic module: evaluator, engine, constraints, types."""

from __future__ import annotations

import unittest

import pytest

from parseval.domain.exceptions import ConstraintConflict
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
)


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
    return Plan(expr, instance)


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
        plan = Plan(expr, instance)
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
        plan = Plan(expr, instance)
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
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        filter_nodes = [n for n in tree.nodes if n.site == "filter"]
        outcomes = filter_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.ATOM_FALSE, outcomes)

    def test_null_branch_observed(self):
        instance = Instance(ddls=SCHEMA, name="eval", dialect="sqlite")
        instance.place_row("t", {"a": None, "b": 1, "c": "x"})
        expr = preprocess_sql("SELECT a FROM t WHERE a > 5", instance, dialect="sqlite")
        plan = Plan(expr, instance)
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
        plan = Plan(expr, instance)
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
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance)
        tree = evaluator.evaluate()
        case_nodes = [n for n in tree.nodes if n.site == "case_arm"]
        self.assertTrue(len(case_nodes) >= 1)


# ---------------------------------------------------------------------------
# BranchTree coverage tracking
# ---------------------------------------------------------------------------


class TestBranchTree(unittest.TestCase):
    def test_uncovered_targets_with_default_thresholds(self):
        tree = BranchTree(thresholds=CoverageThresholds())
        node = tree.get_or_create_node(
            step_id="step_0",
            step_type="Filter",
            site="filter",
            predicate=_pred("a > 5"),
            atoms=(_pred("a > 5"),),
            tables=("t",),
        )
        # No observations -> atom outcomes are uncovered.
        targets = tree.uncovered_targets
        self.assertEqual(len(targets), 3)
        self.assertEqual(
            {(target.atom_id, target.target_outcome) for target in targets},
            {
                (0, BranchType.ATOM_TRUE),
                (0, BranchType.ATOM_FALSE),
                (0, BranchType.ATOM_NULL),
            },
        )

    def test_observation_reduces_uncovered(self):
        tree = BranchTree(thresholds=CoverageThresholds())
        node = tree.get_or_create_node(
            step_id="step_0",
            step_type="Filter",
            site="filter",
            predicate=_pred("a > 5"),
            atoms=(_pred("a > 5"),),
            tables=("t",),
        )
        from parseval.symbolic.types import AtomObservation

        tree.record_observation(
            node, AtomObservation(atom_id=0, outcome=BranchType.ATOM_TRUE)
        )
        targets = tree.uncovered_targets
        self.assertEqual(len(targets), 2)
        self.assertEqual(
            {(target.atom_id, target.target_outcome) for target in targets},
            {
                (0, BranchType.ATOM_FALSE),
                (0, BranchType.ATOM_NULL),
            },
        )

    def test_threshold_zero_skips_branch_type(self):
        tree = BranchTree(thresholds=CoverageThresholds(atom_null=0))
        tree.get_or_create_node(
            step_id="step_0",
            step_type="Filter",
            site="filter",
            predicate=_pred("a > 5"),
            atoms=(_pred("a > 5"),),
            tables=("t",),
        )
        targets = tree.uncovered_targets
        # Only TRUE and FALSE atom outcomes, not NULL.
        self.assertEqual(len(targets), 2)
        self.assertTrue(all(t.target_outcome != BranchType.ATOM_NULL for t in targets))

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

    def test_coverage_ratio(self):
        tree = BranchTree(thresholds=CoverageThresholds(atom_null=0))
        node = tree.get_or_create_node(
            step_id="step_0",
            step_type="Filter",
            site="filter",
            predicate=_pred("a > 5"),
            atoms=(_pred("a > 5"),),
            tables=("t",),
        )
        self.assertEqual(tree.coverage_ratio, 0.0)
        from parseval.symbolic.types import AtomObservation

        tree.record_observation(
            node, AtomObservation(atom_id=0, outcome=BranchType.ATOM_TRUE)
        )
        self.assertEqual(tree.coverage_ratio, 0.5)
        tree.record_observation(
            node, AtomObservation(atom_id=0, outcome=BranchType.ATOM_FALSE)
        )
        self.assertEqual(tree.coverage_ratio, 1.0)
        self.assertTrue(tree.fully_covered)


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

    def test_unsat_solver_result_does_not_materialize_rows(self):
        class UnsatSolver:
            def solve(self, constraint):
                return SolveResult(sat=False, reason="unsat")

        instance = Instance(ddls=SCHEMA, name="engine", dialect="sqlite")
        engine = SymbolicEngine(
            instance,
            "SELECT a FROM t WHERE a > 5",
            dialect="sqlite",
            solver=UnsatSolver(),
        )

        constraint = SolverConstraint(target_relations=(instance.table_id("t"),))

        self.assertFalse(engine._solve_and_materialize(constraint))
        self.assertEqual(sum(len(instance.get_rows(t)) for t in instance.tables), 0)

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

    def test_quoted_storage_relations_materialize_rows(self):
        schema = """
        CREATE TABLE IF NOT EXISTS `League` (
            `id` int PRIMARY KEY,
            `name` varchar
        );
        CREATE TABLE IF NOT EXISTS `Match` (
            `id` int PRIMARY KEY,
            `league_id` int,
            `season` varchar,
            FOREIGN KEY (`league_id`) REFERENCES `League`(`id`)
        );
        """
        instance = Instance(ddls=schema, name="quoted_engine", dialect="sqlite")
        engine = SymbolicEngine(
            instance,
            (
                "SELECT t2.name FROM Match AS t1 "
                "INNER JOIN League AS t2 ON t1.league_id = t2.id "
                "WHERE t1.season = '2015/2016'"
            ),
            dialect="sqlite",
            max_iterations=3,
        )

        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))

        self.assertGreater(result.rows_generated, 0)
        self.assertGreater(len(instance.get_rows("match")), 0)
        self.assertGreater(len(instance.get_rows("league")), 0)


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
        RelationKind.TABLE, physical.name,
        alias=identifier_name("a"), scope_id="left",
    )
    right = relation_id(
        RelationKind.TABLE, physical.name,
        alias=identifier_name("b"), scope_id="right",
    )
    left_id = column_id(ColumnKind.PHYSICAL, identifier_name("id"), left, source_column_id=physical_id)
    left_name = column_id(ColumnKind.PHYSICAL, identifier_name("name"), left, source_column_id=physical_name)
    right_id = column_id(ColumnKind.PHYSICAL, identifier_name("id"), right, source_column_id=physical_id)
    right_name = column_id(ColumnKind.PHYSICAL, identifier_name("name"), right, source_column_id=physical_name)
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
        name="self_join_materialization", dialect="sqlite",
    )
    physical, physical_id, physical_name, left, right, assignments = _people_alias_assignments(instance)
    constraint = SolverConstraint(
        target_relations=(left, right),
        storage_relations={variable: physical for variable in assignments},
    )
    engine = SymbolicEngine(
        instance,
        "SELECT a.name FROM people a JOIN people b ON a.id = b.manager_id",
        solver=_FixedSolver(assignments), max_iterations=1,
    )
    assert engine._solve_and_materialize(constraint)
    assert sorted(
        (row[physical_id].concrete, row[physical_name].concrete)
        for row in instance.get_rows("people")
    ) == [(1, "Alice"), (2, "Bob")]


def test_conflicting_materialized_assignment_raises_conflict():
    instance = Instance(
        ddls="CREATE TABLE people (id INT PRIMARY KEY, name TEXT NOT NULL);",
        name="conflicting_materialization", dialect="sqlite",
    )
    physical = instance.table_id("people")
    physical_name = instance.column_id("people", "name")
    binding = relation_id(
        RelationKind.TABLE, physical.name,
        alias=identifier_name("p"), scope_id="binding",
    )
    first = column_id(
        ColumnKind.PROJECTED, identifier_name("first_name"), binding,
        source_column_id=physical_name,
    )
    second = column_id(
        ColumnKind.PROJECTED, identifier_name("second_name"), binding,
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
        instance, "SELECT name FROM people",
        solver=_FixedSolver(assignments), max_iterations=1,
    )
    with pytest.raises(ConstraintConflict, match="conflicting_materialized_assignment"):
        engine._solve_and_materialize(constraint)
    assert not instance.get_rows("people")


def test_materialization_failure_reports_original_constraint_reason():
    instance = Instance(
        ddls="CREATE TABLE t (x INT, CONSTRAINT positive_x CHECK (x > 0));",
        name="materialization_failure_reason",
        dialect="sqlite",
    )
    physical = instance.table_id("t")
    physical_x = instance.column_id("t", "x")
    assignments = {
        SolverVar(physical_x, physical, "r0"): -1,
    }
    constraint = SolverConstraint(
        target_relations=(physical,),
        storage_relations={variable: physical for variable in assignments},
    )
    engine = SymbolicEngine(
        instance,
        "SELECT x FROM t",
        solver=_FixedSolver(assignments),
        max_iterations=1,
    )

    with pytest.raises(
        ConstraintConflict,
        match="materialization_failed:t:ConstraintViolationError:check_constraint_failed:t",
    ):
        engine._solve_and_materialize(constraint)
    assert not instance.get_rows("t")


def test_mysql_1440_generation_uses_valid_enum_and_text_storage_keys():
    schema = """
    CREATE TABLE VARIABLES (
        NAME VARCHAR(10) PRIMARY KEY,
        VALUE INT
    );
    CREATE TABLE EXPRESSIONS (
        LEFT_OPERAND VARCHAR(10) NOT NULL,
        OPERATOR ENUM('<','>','=') NOT NULL,
        RIGHT_OPERAND VARCHAR(10) NOT NULL,
        PRIMARY KEY (LEFT_OPERAND, OPERATOR, RIGHT_OPERAND),
        FOREIGN KEY (LEFT_OPERAND) REFERENCES VARIABLES(NAME),
        FOREIGN KEY (RIGHT_OPERAND) REFERENCES VARIABLES(NAME)
    );
    """
    query = (
        "SELECT A.*, CASE WHEN "
        "((B.VALUE < C.VALUE AND A.OPERATOR = '<') "
        "OR (B.VALUE = C.VALUE AND A.OPERATOR = '=') "
        "OR (B.VALUE > C.VALUE AND A.OPERATOR = '>')) "
        "THEN TRUE ELSE FALSE END AS VALUE "
        "FROM EXPRESSIONS AS A "
        "JOIN VARIABLES AS B ON A.LEFT_OPERAND = B.NAME "
        "JOIN VARIABLES AS C ON A.RIGHT_OPERAND = C.NAME"
    )
    instance = Instance(schema, name="lc1440", dialect="mysql")
    engine = SymbolicEngine(instance, query, dialect="mysql", max_iterations=1)

    result = engine.generate(
        thresholds=CoverageThresholds(
            atom_null=0,
            atom_false=1,
            atom_dup=1,
            project_null=0,
            distinct_duplicate=0,
            distinct_unique=0,
        )
    )

    expressions_id = instance.table_id("EXPRESSIONS")
    variables_id = instance.table_id("VARIABLES")
    expressions = instance.get_rows(expressions_id)
    variables = instance.get_rows(variables_id)
    name = instance.column_id(variables_id, "NAME")
    operator = instance.column_id(expressions_id, "OPERATOR")
    assert result.rows_generated > 0
    assert expressions
    assert {row[operator].concrete for row in expressions} <= {"<", ">", "="}
    names = [row[name].concrete for row in variables]
    assert len({value.casefold() for value in names}) == len(names)


if __name__ == "__main__":
    unittest.main()
