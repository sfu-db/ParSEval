"""Exact count circuits, lexical cache isolation, and solver-layer contracts."""
from collections import Counter

import pytest
import z3

from parseval.catalog import Catalog
from parseval.instance import Instance
from parseval.smt.budget import Budget
from parseval.smt.encoding import UExprEncoder, _Environment
from parseval.smt.instance import SymbolicInstance
from parseval.smt.prepared import PreparedTerms
from parseval.smt.solver import Solver, SolveStatus
from parseval.smt.values import _literal
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.sorts import BagSort, RowSort
from parseval.uexpr import UExprEvaluator

from test_operator_workloads import benchmark
from test_weighted_encoding import compile_query


@pytest.mark.parametrize('sql', [
    'SELECT a.g FROM t a JOIN t b ON a.x = b.g JOIN t c ON b.x = c.g',
    'SELECT a.g FROM t a JOIN t b ON a.x = b.g JOIN t c ON b.x = c.g AND c.x = a.g',
    'SELECT a.g FROM t a JOIN t b ON a.g < b.x',
    'SELECT a.g, b.x FROM t a CROSS JOIN t b',
    'SELECT a.g FROM t a LEFT JOIN t b ON a.x = b.g',
    'SELECT a.g FROM t a WHERE NOT EXISTS (SELECT 1 FROM t b WHERE b.g = a.x)',
    'SELECT a.g FROM t a WHERE EXISTS (SELECT 1 FROM t b WHERE b.g = a.x)',
    'SELECT g FROM t UNION ALL SELECT g FROM t',
    'SELECT g FROM t UNION SELECT g FROM t',
    'SELECT DISTINCT g FROM t',
    'WITH a AS (SELECT g FROM t) SELECT x.g FROM a x JOIN a y ON x.g = y.g',
])
@pytest.mark.parametrize('rows', [(), ((1, 1), (1, 1), (1, 2), (2, 1), (None, 1))])
def test_counts_equal_concrete_bag_cardinality(sql, rows):
    catalog, arena, root = compile_query('CREATE TABLE t(g INT, x INT)', sql)
    relation, _ = next(iter(catalog.context.relations()))
    counts = Counter(rows)
    # Include an inactive slot aliasing an active row. Provenance must only be
    # used under scan guards; unguarded membership must still see active aliases.
    database = SymbolicInstance(catalog, {relation: len(counts) + 1})
    encoder = UExprEncoder(arena, database)
    actual = encoder.count(root, _Environment())
    solver = z3.Solver()
    solver.set(timeout=5000)
    solver.add(*encoder.schema_constraints(), *encoder.constraints)
    pinned = [*counts.items(), ((1, 1), 0)]
    for entry, (row, weight) in zip(database.relations[relation], pinned, strict=True):
        solver.add(entry.multiplicity == weight)
        for value, concrete in zip(entry.row.values, row, strict=True):
            solver.add(value.is_null == (concrete is None))
            if concrete is not None:
                solver.add(value.value == _literal(concrete, value.sql_type))
    concrete = UExprEvaluator(arena, Instance.from_rows(catalog, {relation: rows})).evaluate_query(root)
    expected = sum(entry.multiplicity for entry in concrete.entries)
    assert solver.check() == z3.sat
    solver.add(actual != expected)
    assert solver.check() == z3.unsat


def test_chain_count_work_scales_linearly_in_length():
    work = []
    for joins in (2, 4, 8):
        catalog, arena, target, _ = benchmark.build_case('exact_join', joins)
        database = SymbolicInstance(catalog, {relation: 4 for relation, _ in catalog.context.relations()})
        encoder = UExprEncoder(arena, database, budget=Budget(max_steps=2000))
        encoder.target(target)
        work.append(encoder.budget.steps)
    assert work[1] < 2 * work[0]
    assert work[2] < 2 * work[1]


def test_deep_shared_dag_solve_does_not_call_concrete_evaluation(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('Concrete evaluation belongs outside Solver.solve')
    monkeypatch.setattr(UExprEvaluator, 'witnessed_conditions', unexpected)
    monkeypatch.setattr(UExprEvaluator, 'apply', unexpected)
    catalog, arena, target, _ = benchmark.build_case('shared_dag', 64)
    result = Solver(catalog, timeout_ms=5000, minimize=False).solve(arena, target)
    assert result.status is SolveStatus.SAT, result.reason
    assert result.instance is not None
    assert sum(len(result.instance.rows(r)) for r, _ in catalog.context.relations()) == 2


def test_prepared_dependencies_account_for_both_binder_namespaces():
    catalog = Catalog.from_ddl('CREATE TABLE t(x INT)')
    relation, info = next(iter(catalog.context.relations()))
    arena = TermArena(catalog.context)
    b = IRBuilder(arena)
    row = arena.row_var(1, RowSort(info.schema))
    rel = arena.rel_var(1, BagSort(info.schema))
    at = arena.intern_checked(nodes.At, (rel, row))
    let = arena.intern_checked(nodes.LetRel, (b.finish(b.base(relation)), at))
    function = arena.intern_checked(nodes.RowLambda, (let,), nodes.RowLambdaPayload(info.schema))
    prepared = PreparedTerms(arena)
    assert prepared.dependencies(function) == ((0,), (0,))
    assert prepared.dependencies(at) == ((1,), (1,))


def test_cache_ignores_unused_rows_but_distinguishes_relation_bindings():
    catalog = Catalog.from_ddl('CREATE TABLE t(x INT)')
    relation, info = next(iter(catalog.context.relations()))
    arena = TermArena(catalog.context)
    b = IRBuilder(arena)
    database = SymbolicInstance(catalog, {relation: 2})
    encoder = UExprEncoder(arena, database)
    rows = tuple(entry.row for entry in database.relations[relation])
    variable = arena.row_var(0, RowSort(info.schema))
    field = b.resolve(b.field(variable, 0))
    first = encoder.value(field, _Environment((rows[0], rows[0])))
    cache_size = len(encoder.cache)
    assert encoder.value(field, _Environment((rows[0], rows[1]))) is first
    assert len(encoder.cache) == cache_size
    assert encoder.value(field, _Environment((rows[1], rows[0]))) is not first
    rel = arena.rel_var(0, BagSort(info.schema))
    left, right = database.relations[relation][:1], database.relations[relation][1:]
    assert encoder.bag(rel, _Environment(relations=(left,))) is left
    assert encoder.bag(rel, _Environment(relations=(right,))) is right


def test_sql_global_count_uses_factorized_join_circuit():
    catalog, arena, root = compile_query(
        ';'.join(f'CREATE TABLE r{i}(x INT)' for i in range(9)),
        'SELECT COUNT(*) FROM r0 ' + ' '.join(
            f'JOIN r{i} ON r{i-1}.x = r{i}.x' for i in range(1, 9)),
    )
    database = SymbolicInstance(catalog, {relation: 4 for relation, _ in catalog.context.relations()})
    encoder = UExprEncoder(arena, database, budget=Budget(max_steps=2000))
    entries = encoder.bag(root, _Environment())
    assert len(entries) == 1
    solver = z3.Solver()
    solver.add(*encoder.schema_constraints(), *encoder.constraints)
    for support in database.relations.values():
        for index, entry in enumerate(support):
            # Two occurrences of each of four keys in every input relation.
            solver.add(entry.multiplicity == 2, entry.row.values[0].value == index,
                       z3.Not(entry.row.values[0].is_null))
    assert solver.check() == z3.sat
    solver.add(entries[0].row.values[0].value != 4 * 2 ** 9)
    assert solver.check() == z3.unsat
