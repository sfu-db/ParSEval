"""Direct SMT tests for PostgreSQL-style multi-join U-expressions."""

from __future__ import annotations

from parseval.catalog import Catalog
from parseval.coverage import next_paths, target_is_covered
from parseval.instance import Instance
from parseval.parser.query import lower_query
from parseval.smt.solver import SolveStatus, Solver
from parseval.terms.arena import TermArena
from parseval.uexpr import UExprCompiler


def _join_chain(join_count: int):
    """Compile the narrow form of the long join chains in postgres.csv."""

    ddl = "; ".join(
        f"CREATE TABLE t{index}(id INT, next_id INT)"
        for index in range(join_count + 1)
    )
    joins = " ".join(
        f"JOIN t{index} ON t{index - 1}.next_id = t{index}.id"
        for index in range(1, join_count + 1)
    )
    sql = f"SELECT t0.id FROM t0 {joins}"
    catalog = Catalog.from_ddl(ddl, dialect="postgres")
    query = lower_query(sql, catalog)
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    target = next_paths(arena, root, Instance.empty(catalog))[0]
    return catalog, arena, target


def test_solver_directly_solves_two_join_uexpression():
    catalog, arena, target = _join_chain(2)

    result = Solver(catalog, timeout_ms=1_000).solve(arena, target)

    assert result.status is SolveStatus.SAT
    assert result.instance is not None
    assert target_is_covered(arena, result.instance, target)


def test_eight_join_target_uses_one_weighted_class_per_relation():
    catalog, arena, target = _join_chain(8)

    result = Solver(catalog, timeout_ms=2_000).solve(arena, target)

    assert result.status is SolveStatus.SAT, result.reason
    assert result.instance is not None
    assert target_is_covered(arena, result.instance, target)
    assert result.statistics.attempts == 1
    assert sum(count for _, count in result.statistics.support) == 9
    assert result.statistics.encoding_steps < 2_000


def test_eight_join_witness_selects_from_multiple_classes_without_cartesian_expansion():
    catalog, arena, target = _join_chain(8)
    seed = Instance.from_rows(catalog, {
        relation: ((1, 1), (2, 2)) for relation, _ in catalog.context.relations()
    })

    result = Solver(catalog, timeout_ms=3_000).solve(arena, target, seed=seed)

    assert result.status is SolveStatus.SAT, result.reason
    assert target_is_covered(arena, result.instance, target)
    assert result.statistics.attempts == 1
    assert sum(count for _, count in result.statistics.support) == 18
    assert result.statistics.circuit_nodes > 0
    assert result.statistics.encoding_steps < 2_000
