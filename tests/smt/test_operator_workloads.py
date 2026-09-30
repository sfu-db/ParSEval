"""Direct U-semiring correctness and structural performance regressions."""
import importlib.util
from pathlib import Path

import pytest
import z3

from parseval.coverage import target_is_covered
from parseval.smt.encoding import UExprEncoder, _Environment
from parseval.smt.instance import SymbolicInstance
from parseval.smt.solver import Solver, SolveStatus
from parseval.terms.builder import IRBuilder
from parseval.terms.sorts import RowSort
from parseval.uexpr import validate_instance


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_smt_operators.py"
spec = importlib.util.spec_from_file_location("benchmark_smt_operators", SCRIPT)
assert spec is not None and spec.loader is not None
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@pytest.mark.parametrize("name", benchmark.CASES)
@pytest.mark.parametrize("size", [1, 2, 4])
def test_direct_operator_obligations(name, size):
    catalog, arena, target, expected = benchmark.build_case(name, size)
    result = Solver(catalog, timeout_ms=5000, max_support=4, minimize=False).solve(arena, target)
    assert result.status is expected, result.reason
    if expected is SolveStatus.SAT:
        assert result.instance is not None
        assert not validate_instance(result.instance)
        assert target_is_covered(arena, result.instance, target)
    else:
        assert result.instance is None


@pytest.mark.parametrize("left,right", [(0, 0), (0, 3), (2, 0), (2, 3)])
def test_core_operators_have_exact_natural_number_semantics(left, right):
    """Pin input weights and check against arithmetic, including empty bags."""
    catalog, arena, _, _ = benchmark.build_case("sum_union", 1)
    b = IRBuilder(arena)
    relations = tuple(catalog.context.relations())
    database = SymbolicInstance(catalog, {relation: 1 for relation, _ in relations})
    encoder = UExprEncoder(arena, database)
    counts = [b.sum(RowSort(info.schema), lambda row, relation=relation: b.at(b.base(relation), row))
              for relation, info in relations]
    x, y = counts
    expressions = [
        (b.zero(), 0), (b.one(), 1), (b.add(x, y), left + right),
        (b.mul(x, y), left * right), (b.squash(x), int(left > 0)),
        (b.unot(x), int(left == 0)),
        (b.mul(x, b.unot(y)), left * int(right == 0)),
        (b.squash(b.add(x, y)), int(left + right > 0)),
        (b.unot(b.unot(x)), int(left > 0)),
    ]
    encoded = [(encoder.multiplicity(b.finish(term), _Environment()), expected)
               for term, expected in expressions]
    solver = z3.Solver()
    solver.set(timeout=5000)
    solver.add(*encoder.schema_constraints(), *encoder.constraints)
    for (relation, _), weight in zip(relations, (left, right), strict=True):
        solver.add(database.relations[relation][0].multiplicity == weight)
    assert solver.check() == z3.sat
    # Prove no assignment of unobserved cells can change these exact weights.
    solver.add(z3.Or(*(actual != expected for actual, expected in encoded)))
    assert solver.check() == z3.unsat


def test_shared_dag_encoding_work_grows_linearly():
    for size in (4, 16, 64):
        catalog, arena, target, _ = benchmark.build_case("shared_dag", size)
        relations = tuple(catalog.context.relations())
        database = SymbolicInstance(catalog, {
            relation: int(index == 0) for index, (relation, _) in enumerate(relations)
        })
        encoder = UExprEncoder(arena, database)
        goal = encoder.target(target)
        solver = z3.Solver()
        solver.set(timeout=5000)
        solver.add(goal, *encoder.schema_constraints(), *encoder.constraints)
        assert solver.check() == z3.sat
        # A duplicated child at every level must be memoized, not expanded 2^n.
        assert encoder.budget.steps <= 20 * size + 100
        assert len(encoder.circuit.nodes) <= 10 * size + 100


def test_required_distinct_values_grow_support_not_only_weights():
    result = benchmark.run_case("support_diversity", 4, timeout_ms=5000)
    assert result["status"] == "sat", result["reason"]
    assert result["attempts"] == 3  # 1 -> 2 -> 4 classes
    assert max(count for _, count in result["support"]) == 4


def test_insufficient_support_is_bounded_unsat():
    result = benchmark.run_case("support_diversity", 4, max_support=2)
    assert result["status"] == "bounded_unsat"


def test_work_exhaustion_is_unknown_not_unsat():
    result = benchmark.run_case("exact_join", 4, max_encoding_steps=1)
    assert result["status"] == "unknown"
    assert "work limit" in result["reason"]
