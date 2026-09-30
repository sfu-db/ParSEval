from parseval.catalog import Catalog
from parseval.parser.query import lower_query
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.context import Context
from parseval.uexpr import (
    UExprCompiler,
    analyze_order_normal_form,
    inspect_bag_espnf,
    simplify_uexpr,
    to_espnf,
)


def test_espnf_keeps_products_of_choices_factorized() -> None:
    arena = TermArena(Context())
    builder = IRBuilder(arena)
    first = builder.squash(builder.one())
    second = builder.unot(builder.one())
    third = builder.squash(second)
    fourth = builder.unot(first)
    root = builder.finish(
        builder.mul(
            builder.add(first, second),
            builder.add(third, fourth),
        )
    )

    simplified = simplify_uexpr(arena, root)

    assert isinstance(arena[simplified], nodes.Mul)
    assert sum(
        isinstance(arena[child], nodes.Add)
        for child in arena[simplified].children
    ) == 2

    normalized = to_espnf(arena, simplified)

    assert normalized == simplified
    assert isinstance(arena[normalized], nodes.Mul)
    assert sum(
        isinstance(arena[child], nodes.Add)
        for child in arena[normalized].children
    ) == 2


def test_espnf_consolidates_squash_and_not_factors() -> None:
    arena = TermArena(Context())
    builder = IRBuilder(arena)
    first = builder.squash(builder.one())
    second = builder.squash(builder.unot(builder.one()))
    third = builder.unot(builder.one())
    fourth = builder.unot(builder.squash(builder.one()))
    root = builder.finish(builder.mul(first, second, third, fourth))

    normalized = to_espnf(arena, root)
    factors = arena[normalized].children

    assert sum(isinstance(arena[factor], nodes.Squash) for factor in factors) == 1
    assert sum(isinstance(arena[factor], nodes.UNot) for factor in factors) == 1


def test_factorized_espnf_retains_products_of_union_choices() -> None:
    catalog = Catalog.from_ddl(
        ";".join(f"CREATE TABLE {name}(a INT)" for name in "tuvw"),
        dialect="postgres",
    )
    query = lower_query(
        "SELECT l.a "
        "FROM (SELECT a FROM t UNION ALL SELECT a FROM u) AS l "
        "JOIN (SELECT a FROM v UNION ALL SELECT a FROM w) AS r "
        "ON l.a = r.a",
        catalog,
    )
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    espnf = inspect_bag_espnf(arena, to_espnf(arena, root))

    assert len(espnf.alternatives) == 2
    assert not espnf.complete
    assert espnf.residuals


def test_order_analysis_preserves_cte_bindings_without_inlining() -> None:
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)", dialect="postgres")
    query = lower_query(
        "WITH x AS (SELECT a FROM t WHERE a > 0) "
        "SELECT a FROM x ORDER BY a LIMIT 2",
        catalog,
    )
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    order = analyze_order_normal_form(arena, root)

    assert order is not None
    assert len(order.bindings) == 1
    assert isinstance(arena[order.source], nodes.LetRel)
