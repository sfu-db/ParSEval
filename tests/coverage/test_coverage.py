from parseval.catalog import Catalog
from parseval.coverage import (
    CoverageStatus,
    CoverageTracker,
    measure_coverage,
    next_paths,
    observed_paths,
    target_is_covered,
)
from parseval.coverage.model import CoverageTarget, WitnessedObligation
from parseval.generator import SolveStatus, Solver
from parseval.instance import Instance
from parseval.parser.query import lower_query
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.sorts import RowSort
from parseval.terms.terms import TermId
from parseval.terms import terms as nodes
from parseval.coverage.evaluate import UExprEvaluator
from parseval.coverage.observation import (
    NullCondition, PredicateCondition, TruthOutcome, WeightCondition,
)
from parseval.uexpr import UExprCompiler


def _query(sql: str, catalog: Catalog) -> tuple[TermArena, TermId]:
    query = lower_query(sql, catalog)
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    return arena, root


def test_filter_goals_partition_true_false_and_unknown():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT a FROM t WHERE a > 3", catalog)
    instances = tuple(
        Instance.from_rows(catalog, {relation: ((value,),)})
        for value in (4, 3, None)
    )
    targets = {target.id: target for instance in instances for target in observed_paths(arena, root, instance)}

    assert len(targets) == 3
    for instance in instances:
        assert sum(target_is_covered(arena, instance, target) for target in targets.values()) == 1
    assert not measure_coverage(arena, root, Instance.empty(catalog), tuple(targets.values())).covered


def test_nested_distinct_and_group_source_filters_are_covered():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    instances = tuple(
        Instance.from_rows(catalog, {relation: ((value,),)})
        for value in (4, 3, None)
    )
    for sql, complete in (
        ("SELECT DISTINCT a FROM t WHERE a > 3", True),
        ("SELECT a, COUNT(*) FROM t WHERE a > 3 GROUP BY a", False),
    ):
        arena, root = _query(sql, catalog)
        report = measure_coverage(arena, root, instances)
        assert report.covered
        assert {
            condition.truth
            for instance in instances
            for target in observed_paths(arena, root, instance)
            for condition in target.obligation.conditions
            if isinstance(condition, PredicateCondition)
        } == {TruthOutcome.TRUE, TruthOutcome.FALSE, TruthOutcome.UNKNOWN}
        assert report.complete is complete


def test_existing_instance_yields_complete_path_and_one_step_frontier():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT, b INT)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT a FROM t WHERE a > 3 AND b > 4", catalog)

    for row in ((5, 5), (3, 4), (None, None)):
        instance = Instance.from_rows(catalog, {relation: (row,)})
        observed = observed_paths(arena, root, instance)
        upcoming = next_paths(arena, root, instance)
        assert len(observed) == 1
        assert len(upcoming) >= 4
        assert target_is_covered(arena, instance, observed[0])
        assert all(not target_is_covered(arena, instance, target) for target in upcoming)


def test_nonnullable_threshold_derives_its_false_neighbor():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT NOT NULL)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT a FROM t WHERE a > 3", catalog)
    existing = Instance.from_rows(catalog, {relation: ((4,),)})

    neighbor = next(
        target for target in next_paths(arena, root, existing)
        if any(
            isinstance(condition, PredicateCondition)
            and condition.truth is TruthOutcome.FALSE
            for condition in target.obligation.conditions
        )
    )

    assert not target_is_covered(arena, existing, neighbor)
    assert target_is_covered(
        arena,
        Instance.from_rows(catalog, {relation: ((3,),)}),
        neighbor,
    )


def test_empty_instance_starts_with_a_productive_witness_goal():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT NOT NULL)")
    arena, root = _query("SELECT a FROM t WHERE a > 3", catalog)

    assert observed_paths(arena, root, Instance.empty(catalog)) == ()
    assert next_paths(arena, root, Instance.empty(catalog))


def test_absent_relation_outcome_uses_another_relation_as_row_witness():
    catalog = Catalog.from_ddl(
        "CREATE TABLE a(x INT NOT NULL); CREATE TABLE b(x INT NOT NULL)"
    )
    (left, left_spec), (right, _) = tuple(catalog.context.relations())
    arena = TermArena(catalog.context)
    builder = IRBuilder(arena)
    left_bag, right_bag = builder.base(left), builder.base(right)
    root = builder.finish(
        builder.bag_lam(
            left_spec.schema,
            lambda output: builder.sum(
                RowSort(left_spec.schema),
                lambda row: builder.mul(
                    builder.at(left_bag, row),
                    builder.at(right_bag, row),
                    builder.indicator(builder.row_identity_eq(output, row)),
                ),
            ),
        )
    )
    instance = Instance.from_rows(catalog, {right: ((1,),)})

    observed = observed_paths(arena, root, instance)

    assert len(observed) == 1
    assert target_is_covered(arena, instance, observed[0])
    assert any(
        isinstance(condition, WeightCondition)
        and isinstance(arena[condition.term], nodes.At)
        and arena[arena[condition.term].children[0]].payload.relation == left
        and (condition.minimum, condition.maximum) == (0, 0)
        for condition in observed[0].obligation.conditions
    )
    assert next_paths(arena, root, instance)


def test_overlapping_additive_alternatives_are_both_observed():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT); CREATE TABLE u(a INT)"
    )
    arena, root = _query("SELECT a FROM t UNION ALL SELECT a FROM u", catalog)
    left, right = (relation for relation, _ in catalog.context.relations())
    instance = Instance.from_rows(
        catalog, {left: ((1,),), right: ((1,),)}
    )

    observed = observed_paths(arena, root, instance)

    assert len(observed) == 2
    assert all(target_is_covered(arena, instance, target) for target in observed)


def test_distinct_inner_filter_has_neighboring_paths():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT DISTINCT a FROM t WHERE a > 3", catalog)
    instance = Instance.from_rows(catalog, {relation: ((3,),)})

    assert len(observed_paths(arena, root, instance)) == 1
    assert len(next_paths(arena, root, instance)) >= 2


def test_correlated_inner_scope_uses_outer_row_witness():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    arena, root = _query(
        "SELECT a FROM t WHERE EXISTS "
        "(SELECT 1 FROM t AS u WHERE u.a = t.a)",
        catalog,
    )

    relation, _ = next(iter(catalog.context.relations()))
    empty = Instance.empty(catalog)
    report = measure_coverage(arena, root, empty)
    inner = tuple(
        target for target in next_paths(arena, root, empty)
        if target.obligation.contexts
    )
    instance = Instance.from_rows(catalog, {relation: ((1,),)})

    assert report.complete
    assert inner
    assert any(target_is_covered(arena, instance, target) for target in inner)


def test_relation_binding_scope_is_observed_in_its_lexical_context():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    arena, root = _query(
        "WITH x AS (SELECT a FROM t) SELECT a FROM x", catalog
    )

    relation, _ = next(iter(catalog.context.relations()))
    empty = measure_coverage(arena, root, Instance.empty(catalog))
    instance = Instance.from_rows(catalog, {relation: ((7,),)})
    observed = observed_paths(arena, root, instance)

    assert empty.complete
    assert not empty.covered
    assert observed
    assert all(target_is_covered(arena, instance, target) for target in observed)


def test_coverage_uses_supplied_function_callbacks():
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {**UExprEvaluator.SCALAR_FUNCTIONS, "abs": lambda args: 0}

    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT a FROM t WHERE ABS(a) > 0", catalog)
    instance = Instance.from_rows(catalog, {relation: ((-1,),)})
    targets = (*observed_paths(arena, root, instance), *next_paths(arena, root, instance))
    false_target = next(
        target for target in targets
        if target_is_covered(arena, instance, target, evaluator_class=CustomEvaluator)
        and not target_is_covered(arena, instance, target)
    )

    default = measure_coverage(arena, root, instance, targets)
    custom = measure_coverage(
        arena, root, instance, targets,
        evaluator_class=CustomEvaluator,
    )

    assert false_target.id not in default.covered
    assert false_target.id in custom.covered


def test_typed_predicate_and_null_conditions_share_concrete_and_smt_semantics():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT a FROM t WHERE a > 3", catalog)
    seed = next_paths(arena, root, Instance.empty(catalog))[0]
    indicator = next(
        factor for factor in arena.post_order((root,))
        if isinstance(arena[factor], nodes.Indicator)
        and not isinstance(arena[arena[factor].children[0]], nodes.RowIdentityEq)
    )
    predicate = arena[indicator].children[0]
    field = next(
        term for term in arena.post_order((predicate,))
        if isinstance(arena[term], nodes.Field)
    )
    target = CoverageTarget(
        "unknown-and-null",
        seed.site,
        WitnessedObligation(
            seed.obligation.plan,
            (PredicateCondition(predicate, TruthOutcome.UNKNOWN), NullCondition(field, True)),
        ),
    )

    result = Solver(catalog).solve(arena, target)

    assert result.status is SolveStatus.SAT
    assert result.instance is not None
    assert result.instance.rows(relation)[0].values == (None,)
    assert target_is_covered(arena, result.instance, target)


def test_relation_multiplicity_observes_one_and_repeated_rows():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    arena, root = _query("SELECT a FROM t", catalog)
    single = Instance.from_rows(catalog, {relation: ((7,),)})
    repeated = Instance.from_rows(catalog, {relation: ((7,), (7,))})

    def relation_classes(instance):
        return {
            (condition.minimum, condition.maximum)
            for target in observed_paths(arena, root, instance)
            for condition in target.obligation.conditions
            if isinstance(condition, WeightCondition)
            and isinstance(arena[condition.term], nodes.At)
        }

    assert relation_classes(single) == {(1, 1)}
    assert relation_classes(repeated) == {(2, None)}
    assert UExprEvaluator(arena, repeated).evaluate_query(root).multiplicity((7,)) == 2


def test_tracker_keeps_concrete_coverage_over_bounded_solver_failure():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    arena, root = _query("SELECT a FROM t", catalog)
    target = next_paths(arena, root, Instance.empty(catalog))[0]
    tracker = CoverageTracker()
    tracker.discover((target,))

    tracker.record(target, CoverageStatus.COVERED)
    tracker.record(target, CoverageStatus.BOUNDED_UNSAT, "small bound")
    report = tracker.report()

    assert report.covered == {target.id}
    assert not report.bounded_unsat
    assert report.fully_covered
