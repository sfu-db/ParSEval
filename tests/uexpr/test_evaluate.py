import pytest

from parseval.catalog import Catalog
from parseval.instance import Instance, Row
from parseval.parser.query import lower_query
from parseval.terms.arena import TermArena
from parseval.uexpr import (
    BagEntry,
    BagValue,
    SequenceValue,
    UExprCompiler,
    UExprEvaluator,
    validate_instance,
)


def _evaluate(ddl: str, sql: str, rows, *, evaluator_class=UExprEvaluator):
    catalog = Catalog.from_ddl(ddl)
    relation, _ = next(iter(catalog.context.relations()))
    query = lower_query(sql, catalog)
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    instance = Instance.from_rows(catalog, {relation: rows})
    return evaluator_class(arena, instance).evaluate_query(root)


def test_evaluator_rejects_an_instance_from_another_context():
    left = Catalog.from_ddl("CREATE TABLE t(a INT)")
    right = Catalog.from_ddl("CREATE TABLE t(a INT)")

    with pytest.raises(ValueError, match="share one Context"):
        UExprEvaluator(TermArena(left.context), Instance.empty(right))


def test_evaluation_containers_reject_rows_from_another_schema():
    catalog = Catalog.from_ddl("CREATE TABLE a(x INT); CREATE TABLE b(y TEXT)")
    (_, specification_a), (_, specification_b) = catalog.context.relations()
    row = Row(specification_a.schema, (1,))

    with pytest.raises(ValueError, match="Bag rows do not match"):
        BagValue(specification_b.schema, (BagEntry(row, 1),))
    with pytest.raises(ValueError, match="Sequence rows do not match"):
        SequenceValue(specification_b.schema, (row,))


def test_evaluator_preserves_duplicate_multiplicity():
    result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT a FROM t WHERE a > 0",
        ((1,), (1,), (0,), (None,)),
    )

    assert tuple(row.values for row in result.expanded_rows()) == ((1,), (1,))


def test_evaluator_constructs_distinct_projection_support():
    result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT DISTINCT a + 1 FROM t",
        ((1,), (1,), (2,)),
    )

    assert {row.values for row in result.expanded_rows()} == {(2,), (3,)}


def test_evaluator_handles_global_and_grouped_aggregates():
    global_result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT COUNT(*), SUM(a) FROM t",
        ((1,), (2,), (None,)),
    )
    grouped_result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT a, COUNT(*) FROM t GROUP BY a",
        ((1,), (1,), (2,)),
    )

    assert tuple(row.values for row in global_result.expanded_rows()) == (
        (3, 3),
    )
    assert set(row.values for row in grouped_result.expanded_rows()) == {
        (1, 2),
        (2, 1),
    }


@pytest.mark.parametrize(
    "order, expected",
    [
        ("ASC NULLS FIRST", (None, 1, 2)),
        ("ASC NULLS LAST", (1, 2, None)),
        ("DESC NULLS FIRST", (None, 2, 1)),
        ("DESC NULLS LAST", (2, 1, None)),
    ],
)
def test_order_direction_preserves_explicit_null_placement(order, expected):
    result = _evaluate(
        "CREATE TABLE t(a INT)",
        f"SELECT a FROM t ORDER BY a {order}",
        ((1,), (None,), (2,)),
    )
    assert isinstance(result, SequenceValue)
    assert tuple(row.values[0] for row in result.rows) == expected


def test_coalesce_skips_unused_arguments():
    result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT COALESCE(a, 1 / 0) FROM t",
        ((1,), (2,)),
    )
    assert tuple(row.values for row in result.expanded_rows()) == ((1,), (2,))


def test_scalar_override_is_used_in_projection():
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {
            **UExprEvaluator.SCALAR_FUNCTIONS,
            "abs": lambda args: 99 if args[0] is None else 10,
        }

    result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT ABS(a) FROM t",
        ((-1,), (None,)),
        evaluator_class=CustomEvaluator,
    )
    assert tuple(row.values for row in result.expanded_rows()) == ((10,), (99,))


def test_aggregate_callback_receives_filtered_distinct_values_and_empty_inputs():
    seen = []

    def aggregate(spec, values):
        seen.append(values)
        return len(values)

    class CustomEvaluator(UExprEvaluator):
        AGGREGATE_FUNCTIONS = {**UExprEvaluator.AGGREGATE_FUNCTIONS, "sum": aggregate}

    result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT SUM(DISTINCT a) FILTER (WHERE a > 0) FROM t",
        ((1,), (1,), (2,), (-1,), (None,)),
        evaluator_class=CustomEvaluator,
    )
    assert tuple(row.values for row in result.expanded_rows()) == ((2,),)
    assert seen and all(values == (1, 2) for values in seen)

    seen.clear()
    result = _evaluate(
        "CREATE TABLE t(a INT)", "SELECT SUM(a) FROM t", (), evaluator_class=CustomEvaluator
    )
    assert tuple(row.values for row in result.expanded_rows()) == ((0,),)
    assert seen and all(values == () for values in seen)


def test_aggregate_callback_is_used_for_groups():
    class CustomEvaluator(UExprEvaluator):
        AGGREGATE_FUNCTIONS = {
            **UExprEvaluator.AGGREGATE_FUNCTIONS,
            "sum": lambda spec, values: len(values),
        }

    result = _evaluate(
        "CREATE TABLE t(a INT)",
        "SELECT a, SUM(a) FROM t GROUP BY a",
        ((1,), (1,), (2,), (None,)),
        evaluator_class=CustomEvaluator,
    )
    assert {row.values for row in result.expanded_rows()} == {(1, 2), (2, 1), (None, 1)}


def test_instance_validation_uses_supplied_callbacks():
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {**UExprEvaluator.SCALAR_FUNCTIONS, "abs": lambda args: 0}

    catalog = Catalog.from_ddl("CREATE TABLE t(a INT CHECK (ABS(a) > 0))")
    relation, _ = next(iter(catalog.context.relations()))
    instance = Instance.from_rows(catalog, {relation: ((-1,),)})

    assert validate_instance(instance) == ()
    assert validate_instance(
        instance, evaluator_class=CustomEvaluator
    )
