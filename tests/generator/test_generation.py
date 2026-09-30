from parseval.catalog import Catalog
from parseval.coverage import next_paths
from parseval.coverage.observation import PredicateCondition, TruthOutcome
from parseval.generator import GenerationConfig, SolveStatus, generate
from parseval.instance import Instance
from parseval.parser.query import lower_query
from parseval.terms.arena import TermArena
from parseval.uexpr import UExprCompiler
from parseval.coverage.evaluate import validate_instance


def test_generation_covers_nullable_filter_outcomes():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")

    result = generate("SELECT a FROM t WHERE a > 0", catalog)

    assert result.coverage.covered == result.coverage.targets
    assert len(result.counterexamples) >= 3
    assert all(not validate_instance(case.instance) for case in result.counterexamples)


def test_generation_handles_grouped_aggregates_through_witness_plan():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")

    result = generate("SELECT a, COUNT(*) FROM t GROUP BY a", catalog)

    assert result.counterexamples
    assert any(item.solve.status is SolveStatus.SAT for item in result.results)
    assert all(item.solve.status in {SolveStatus.SAT, SolveStatus.BOUNDED_UNSAT}
               for item in result.results)


def test_generation_respects_schema_constraints():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT NOT NULL, UNIQUE(a), CHECK(a > 0))"
    )

    result = generate(
        "SELECT a FROM t WHERE a > 1",
        catalog,
    )

    assert result.counterexamples
    assert all(not validate_instance(case.instance) for case in result.counterexamples)


def test_generation_finds_rejected_rows_when_productive_branch_is_impossible():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT NOT NULL, CHECK(a <= 0))"
    )

    result = generate("SELECT a FROM t WHERE a > 0", catalog)

    assert any(item.solve.status is SolveStatus.BOUNDED_UNSAT for item in result.results)
    assert result.counterexamples
    assert any(
        case.covered and any(row.values[0] <= 0 for relation, _ in catalog.context.relations()
                             for row in case.instance.rows(relation))
        for case in result.counterexamples
    )


def test_generation_encodes_correlated_exists_without_legacy_solver():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")

    result = generate(
        "SELECT a FROM t WHERE EXISTS "
        "(SELECT 1 FROM t AS u WHERE u.a = t.a)",
        catalog,
    )

    assert result.coverage.complete
    assert any(item.solve.status is SolveStatus.SAT for item in result.results)
    assert all(item.solve.status in {SolveStatus.SAT, SolveStatus.BOUNDED_UNSAT}
               for item in result.results)


def test_generation_encodes_literal_like_patterns_exactly():
    catalog = Catalog.from_ddl("CREATE TABLE t(value TEXT)")

    for pattern in ("x", "x_%"):
        result = generate(
            f"SELECT value FROM t WHERE value LIKE '{pattern}'",
            catalog,
        )

        assert result.coverage.ratio == 1.0
        assert all(item.solve.status is SolveStatus.SAT for item in result.results)


def test_generation_covers_both_outer_join_contributions():
    catalog = Catalog.from_ddl(
        "CREATE TABLE a(x INT); CREATE TABLE b(x INT)"
    )
    _, (right, _) = tuple(catalog.context.relations())

    result = generate(
        "SELECT a.x, b.x FROM a LEFT JOIN b ON a.x = b.x", catalog
    )

    assert result.coverage.covered == result.coverage.targets
    assert any(not case.instance.rows(right) for case in result.counterexamples)
    assert any(case.instance.rows(right) for case in result.counterexamples)


def test_generation_solves_cte_uses_in_their_relation_environment():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")

    result = generate(
        "WITH x AS (SELECT a FROM t WHERE a > 0) SELECT a FROM x", catalog
    )

    assert result.coverage.complete
    assert result.coverage.covered == result.coverage.targets
    assert all(item.solve.status is SolveStatus.SAT for item in result.results)


def test_global_aggregate_has_empty_and_nonempty_input_witnesses():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))

    result = generate("SELECT COUNT(*) FROM t", catalog)

    assert any(not case.instance.rows(relation) for case in result.counterexamples)
    assert any(case.instance.rows(relation) for case in result.counterexamples)
    assert any(identity.endswith(".nonempty") for identity in result.coverage.covered)


def test_neighbor_solver_preserves_unrelated_seed_cells():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT NOT NULL, b INT NOT NULL)")
    relation, _ = next(iter(catalog.context.relations()))
    query = lower_query("SELECT a FROM t WHERE a > 0", catalog)
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    seed = Instance.from_rows(catalog, {relation: ((5, 99),)})
    target = next(
        candidate for candidate in next_paths(arena, root, seed)
        if any(
            isinstance(condition, PredicateCondition)
            and condition.truth is TruthOutcome.FALSE
            for condition in candidate.obligation.conditions
        )
    )

    from parseval.generator import Solver

    result = Solver(catalog).solve(arena, target, seed=seed)

    assert result.status is SolveStatus.SAT
    assert result.instance is not None
    assert result.instance.rows(relation)[0].values[1] == 99


def test_generation_constructs_duplicate_rows_for_relation_multiplicity():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))

    result = generate("SELECT a FROM t", catalog)

    assert any(
        len(case.instance.rows(relation)) == 2
        and case.instance.rows(relation)[0] == case.instance.rows(relation)[1]
        for case in result.counterexamples
    )
    assert result.coverage.covered == result.coverage.targets


def test_unique_key_rules_out_duplicate_relation_multiplicity():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT NOT NULL UNIQUE)")

    result = generate("SELECT a FROM t", catalog)

    assert any(item.solve.status is SolveStatus.BOUNDED_UNSAT for item in result.results)
    assert result.coverage.bounded_unsat
    assert all(not validate_instance(case.instance) for case in result.counterexamples)


def test_attempt_budget_leaves_explicit_not_attempted_targets():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT, b INT)")

    result = generate(
        "SELECT a FROM t WHERE a > 0 AND b > 0",
        catalog,
        config=GenerationConfig(max_attempts=1),
    )

    assert len(result.results) == 1
    assert result.coverage.not_attempted
    assert result.coverage.unresolved


def test_generation_reports_solve_validation_and_rediscovery_phases():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    phases = []

    generate(
        "SELECT a FROM t WHERE a > 0",
        catalog,
        config=GenerationConfig(max_attempts=1),
        on_progress=lambda phase, _target, _attempt: phases.append(phase),
    )

    assert phases[:3] == ["solve", "validate", "rediscover"]


def test_generation_config_validates_all_budgets():
    import pytest

    with pytest.raises(ValueError, match="group_size"):
        GenerationConfig(group_size=0)
    with pytest.raises(ValueError, match="max_attempts"):
        GenerationConfig(max_attempts=0)
