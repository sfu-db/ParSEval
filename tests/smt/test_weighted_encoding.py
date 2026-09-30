"""Semantic and scaling regressions for weighted, demand-driven SMT encoding."""

from collections import Counter
from dataclasses import replace

import pytest
import z3

from parseval.catalog import Catalog
from parseval.coverage import next_paths, target_is_covered
from parseval.generator import generate
from parseval.instance import Instance
from parseval.parser.query import lower_query
from parseval.smt.encoding import UExprEncoder, _Environment
from parseval.smt.instance import SymbolicInstance
from parseval.smt.solver import Solver, SolveStatus
from parseval.smt.values import _decode, _literal
from parseval.terms.arena import TermArena
from parseval.coverage.evaluate import UExprEvaluator, validate_instance
from parseval.uexpr import UExprCompiler


def compile_query(ddl, sql):
    catalog = Catalog.from_ddl(ddl, dialect="postgres")
    query = lower_query(sql, catalog)
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    return catalog, arena, root


def group_target(arena, root, catalog, label, threshold=3):
    return next(target for target in next_paths(arena, root, Instance.empty(catalog), group_size=threshold)
                if target.label == f"input.group.{label}")


def test_large_group_with_fresh_primary_keys_uses_one_class():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(id INT PRIMARY KEY, g INT, amount INT)",
        "SELECT g, SUM(amount) FROM t GROUP BY g",
    )
    target = group_target(arena, root, catalog, "above.32", 32)
    result = Solver(catalog, timeout_ms=3_000).solve(arena, target)

    assert result.status is SolveStatus.SAT, result.reason
    assert result.statistics.attempts == 1
    assert [count for _, count in result.statistics.support] == [1]
    relation, _ = next(iter(catalog.context.relations()))
    rows = result.instance.rows(relation)
    assert len(rows) == 33
    assert len({row.values[0] for row in rows}) == 33
    assert not validate_instance(result.instance)
    assert target_is_covered(arena, result.instance, target)

    repeated = Solver(catalog, timeout_ms=3_000).solve(arena, target, seed=result.instance)
    assert repeated.status is SolveStatus.SAT, repeated.reason
    assert [count for _, count in repeated.statistics.support] == [1]


def test_observed_unique_key_requires_more_classes():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(id INT PRIMARY KEY, g INT)",
        "SELECT g, COUNT(DISTINCT id) FROM t GROUP BY g",
    )
    target = group_target(arena, root, catalog, "pair")
    result = Solver(catalog).solve(arena, target)

    assert result.status is SolveStatus.SAT, result.reason
    assert result.statistics.attempts == 2
    assert [count for _, count in result.statistics.support] == [2]
    bag = UExprEvaluator(arena, result.instance).evaluate_query(root)
    assert any(entry.row.values[-1] == 2 for entry in bag.entries)


def test_group_size_does_not_require_encoding_the_aggregate_result():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(id INT PRIMARY KEY, x INT)",
        "SELECT STDDEV_SAMP(x) FROM t",
    )
    target = group_target(arena, root, catalog, "pair")
    result = Solver(catalog).solve(arena, target)

    assert result.status is SolveStatus.SAT, result.reason
    assert [count for _, count in result.statistics.support] == [1]
    assert target_is_covered(arena, result.instance, target)


def test_grouping_on_unique_key_cannot_make_a_pair():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(id INT PRIMARY KEY)",
        "SELECT id, COUNT(*) FROM t GROUP BY id",
    )
    target = group_target(arena, root, catalog, "pair")
    result = Solver(catalog, max_support=3).solve(arena, target)

    assert result.status is SolveStatus.BOUNDED_UNSAT
    assert result.instance is None


def test_check_dependent_key_stays_explicit():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(id INT PRIMARY KEY CHECK(id < 0), g INT)",
        "SELECT g, COUNT(*) FROM t GROUP BY g",
    )
    target = group_target(arena, root, catalog, "pair")
    result = Solver(catalog).solve(arena, target)

    assert result.status is SolveStatus.SAT, result.reason
    relation, _ = next(iter(catalog.context.relations()))
    assert all(row.values[0] < 0 for row in result.instance.rows(relation))
    assert not validate_instance(result.instance)
    assert result.statistics.attempts == 2


def test_foreign_keys_are_preserved_when_group_support_grows():
    catalog, arena, root = compile_query(
        "CREATE TABLE p(id INT PRIMARY KEY); "
        "CREATE TABLE c(id INT PRIMARY KEY, parent INT REFERENCES p(id), g INT)",
        "SELECT g, COUNT(*) FROM c WHERE parent IS NOT NULL GROUP BY g",
    )
    target = group_target(arena, root, catalog, "pair")
    result = Solver(catalog).solve(arena, target)

    assert result.status is SolveStatus.SAT, result.reason
    assert not validate_instance(result.instance)
    assert target_is_covered(arena, result.instance, target)


def test_generation_without_having_covers_different_group_sizes():
    catalog = Catalog.from_ddl("CREATE TABLE t(g INT)")
    result = generate("SELECT g, COUNT(*) FROM t GROUP BY g", catalog, group_size=5)
    relation, _ = next(iter(catalog.context.relations()))
    sizes = {size for case in result.counterexamples
             for size in Counter(row.values[0] for row in case.instance.rows(relation)).values()}

    assert {1, 2, 6} <= sizes
    assert any(identity.endswith("group.above.5") for identity in result.coverage.covered)


def test_group_size_counts_each_group_not_the_whole_input():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(g INT)", "SELECT g, COUNT(*) FROM t GROUP BY g",
    )
    target = group_target(arena, root, catalog, "pair")
    relation, _ = next(iter(catalog.context.relations()))
    singletons = Instance.from_rows(catalog, {relation: ((1,), (2,))})
    null_group = Instance.from_rows(catalog, {relation: ((None,), (None,))})

    assert not target_is_covered(arena, singletons, target)
    assert target_is_covered(arena, null_group, target)


@pytest.mark.parametrize("sql, expected", [
    ("SELECT COUNT(*) FROM t", SolveStatus.SAT),
    ("SELECT g, COUNT(*) FROM t GROUP BY g", SolveStatus.BOUNDED_UNSAT),
])
def test_empty_global_group_is_different_from_no_group(sql, expected):
    catalog, arena, root = compile_query("CREATE TABLE t(g INT)", sql)
    target = group_target(arena, root, catalog, "single")
    condition = replace(target.obligation.conditions[0], minimum=0, maximum=0)
    target = replace(target, obligation=replace(target.obligation, conditions=(condition,)))
    result = Solver(catalog, max_support=2).solve(arena, target)

    assert result.status is expected, result.reason


def test_support_bound_failure_is_not_unrestricted_unsat():
    catalog, arena, root = compile_query(
        "CREATE TABLE t(id INT PRIMARY KEY)",
        "SELECT COUNT(DISTINCT id) FROM t",
    )
    target = group_target(arena, root, catalog, "pair")
    assert Solver(catalog, max_support=1).solve(arena, target).status is SolveStatus.BOUNDED_UNSAT
    assert Solver(catalog, max_support=2).solve(arena, target).status is SolveStatus.SAT


def test_encoding_budget_is_checked_before_z3():
    catalog, arena, root = compile_query("CREATE TABLE t(x INT)", "SELECT x FROM t")
    target = next_paths(arena, root, Instance.empty(catalog))[0]
    result = Solver(catalog, max_encoding_steps=1).solve(arena, target)

    assert result.status is SolveStatus.UNKNOWN
    assert "encoding work limit" in result.reason


@pytest.mark.parametrize("sql, rows", [
    ("SELECT g, COUNT(*), SUM(x), AVG(x), MIN(x), MAX(x) FROM t GROUP BY g",
     ((None, 2), (None, 2), (None, 5), (1, None))),
    ("SELECT g, COUNT(DISTINCT x), SUM(DISTINCT x), COUNT(x) FROM t GROUP BY g",
     ((1, 2), (1, 2), (1, 5), (1, None))),
    ("SELECT g, COUNT(*) FILTER (WHERE x > 2), SUM(x) FILTER (WHERE x > 2) FROM t GROUP BY g",
     ((1, 2), (1, 2), (1, 5), (1, None))),
    ("SELECT COUNT(*), SUM(x), AVG(x) FROM t", ()),
    ("SELECT g, COUNT(*) FROM t WHERE x > 2 GROUP BY g",
     ((1, 2), (1, 5), (1, 5), (2, None))),
    ("SELECT a.g, COUNT(*) FROM t a JOIN t b ON a.g = b.g GROUP BY a.g",
     ((1, 2), (1, 2), (2, 3))),
    ("SELECT a.g FROM t a WHERE NOT EXISTS (SELECT 1 FROM t b WHERE b.x = a.g)",
     ((1, 2), (2, 3), (2, 3))),
    ("SELECT DISTINCT g FROM t", ((1, 2), (1, 3), (1, 3), (None, 3))),
])
def test_exact_weighted_circuit_agrees_with_concrete_evaluation(sql, rows):
    catalog, arena, root = compile_query("CREATE TABLE t(g INT, x INT)", sql)
    relation, _ = next(iter(catalog.context.relations()))
    counts = Counter(rows)
    database = SymbolicInstance(catalog, {relation: len(counts)})
    encoder = UExprEncoder(arena, database)
    entries = encoder.bag(root, _Environment())
    count = encoder.count(root, _Environment())
    solver = z3.Solver()
    solver.add(*encoder.schema_constraints(), *encoder.constraints)
    for entry, (row, weight) in zip(database.relations[relation], counts.items(), strict=True):
        solver.add(entry.multiplicity == weight)
        for value, concrete in zip(entry.row.values, row, strict=True):
            solver.add(value.is_null == (concrete is None))
            if concrete is not None:
                solver.add(value.value == _literal(concrete, value.sql_type))
    assert solver.check() == z3.sat
    model = solver.model()
    symbolic = Counter()
    for entry in entries:
        weight = model.eval(entry.multiplicity, model_completion=True).as_long()
        if weight:
            symbolic[tuple(_decode(model, value) for value in entry.row.values)] += weight
    concrete = UExprEvaluator(arena, Instance.from_rows(catalog, {relation: rows})).evaluate_query(root)
    assert symbolic == Counter({entry.row.values: entry.multiplicity for entry in concrete.entries})
    solver.add(count != sum(entry.multiplicity for entry in concrete.entries))
    assert solver.check() == z3.unsat

