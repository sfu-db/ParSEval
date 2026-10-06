import pytest

from parseval.catalog import Catalog
from parseval.errors import CatalogError
from parseval.instance import Instance, Machine, Sequence, Valuation
from parseval.parser.query import lower_query
from parseval.terms import TermArena
from parseval.terms.sorts import BagSort, ScalarSort
from parseval.terms.sorts import (
    BOOLEAN,
    DECIMAL,
    FLOAT,
    INTEGER,
    INTERVAL,
    STRING,
    TIMESTAMP,
    ScalarType,
    TypeKind,
)
from parseval.uexpr import UExprCompiler


@pytest.mark.parametrize("sql,limited,unlimited", [
    ("SELECT a FROM t ORDER BY a LIMIT 1", [1], [1, 2, 3]),
    ("SELECT a FROM t ORDER BY a DESC LIMIT 1 OFFSET 1", [2], [2, 1]),
    ("(SELECT a FROM t ORDER BY a LIMIT 1)", [1], [1, 2, 3]),
    ("SELECT a FROM (SELECT a FROM t ORDER BY a LIMIT 2) AS x ORDER BY a LIMIT 1",
     [1], [1, 2]),
    ("WITH x AS (SELECT a FROM t ORDER BY a LIMIT 2) SELECT a FROM x ORDER BY a LIMIT 1",
     [1], [1, 2]),
    ("SELECT a, (SELECT a FROM t ORDER BY a LIMIT 1) FROM t ORDER BY a LIMIT 1",
     [1], [1, 2, 3]),
])
@pytest.mark.parametrize("ignore_root_limit", [False, True])
def test_outer_limit_is_optional_without_changing_nested_limits_or_order(
    sql, limited, unlimited, ignore_root_limit,
):
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    table, = catalog.tables()
    instance = Instance(catalog)
    for value in (3, 1, 2):
        instance, _ = instance.insert(table.relation, (value,))
    options = {"ignore_root_limit": True} if ignore_root_limit else {}
    query = lower_query(sql, catalog, **options)
    root = UExprCompiler(query.arena, instance.arena).compile(query.root).simplified_root
    valuation = Valuation(instance)
    machine = Machine(valuation)
    relation = machine.run(root).relation
    rows = [tuple(valuation.concrete(cell) for cell in row.cells) for row in machine.occurrences(relation)]
    values = [row[0] for row in rows]
    expected = unlimited if ignore_root_limit else limited
    # A result whose root order is forgotten is a bag.
    assert values == expected if isinstance(relation, Sequence) else sorted(values) == sorted(expected)
    if len(query.columns) == 2:
        assert all(row[1] == 1 for row in rows)


def test_base_table_source_uses_catalog_column_specs():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT NOT NULL, b TEXT)")

    result = lower_query("SELECT a, b FROM t", catalog)

    assert isinstance(result.arena[result.root].sort, BagSort)
    assert [column.name.text for column in result.columns] == ["a", "b"]
    assert [column.sort.nullable for column in result.columns] == [False, True]


def test_with_clause_uses_sqlglot_query_structure():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")

    result = lower_query("WITH x AS (SELECT a FROM t) SELECT a FROM x", catalog)

    assert [column.name.text for column in result.columns] == ["a"]


def test_normalized_builtin_scalar_functions_have_catalog_signatures():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT, s TEXT)")

    result = lower_query(
        "SELECT ABS(n), LOWER(s), UPPER(s), LENGTH(s), SUBSTRING(s, 1, 2) FROM t",
        catalog,
    )

    assert [column.sort.sql_type.kind.value for column in result.columns] == [
        "integer",
        "string",
        "string",
        "integer",
        "string",
    ]
    assert all(column.sort.nullable for column in result.columns)


def test_sqlite_functions_and_affinity_coercions_lower_to_typed_terms():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(n INT, x REAL, s TEXT, d DATE)", dialect="sqlite"
    )

    result = lower_query(
        """
        SELECT
            n + x,
            STRFTIME('%Y', d),
            SUBSTR(d, 1, 4),
            INSTR(s, 'x'),
            IIF(n > 0, s, 'none'),
            s || n
        FROM t
        WHERE d >= '2000-01-01'
        """,
        catalog,
    )

    assert [column.sort.sql_type.kind.value for column in result.columns] == [
        "float",
        "string",
        "string",
        "integer",
        "string",
        "string",
    ]


def test_exists_and_mixed_type_union_have_checked_lowerings():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(n INT); CREATE TABLE s(v TEXT)", dialect="sqlite"
    )

    exists = lower_query(
        "SELECT CASE WHEN EXISTS (SELECT 1 FROM t) THEN 'yes' ELSE 'no' END",
        catalog,
    )
    union = lower_query("SELECT n FROM t UNION ALL SELECT v FROM s", catalog)

    assert exists.columns[0].sort.sql_type.kind.value == "string"
    assert union.columns[0].sort.sql_type.kind.value == "string"


def test_scalar_subquery_type_is_recovered_from_chained_ctes():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT)", dialect="sqlite")

    result = lower_query(
        """
        WITH first AS (SELECT n + 1 AS value FROM t),
             second AS (SELECT value FROM first)
        SELECT (SELECT value FROM second) + 1
        """,
        catalog,
    )

    assert result.columns[0].sort.sql_type.kind.value == "integer"


def test_select_without_from_and_scalar_subqueries_lower_to_uexpr():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT)", dialect="sqlite")
    compact = lower_query(
        "SELECT (SELECT MAX(n) FROM t) - (SELECT MIN(n) FROM t)",
        catalog,
    )

    lowered = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert lowered.sort == compact.arena[compact.root].sort


@pytest.mark.parametrize("join_type", ["LEFT", "RIGHT", "FULL"])
def test_outer_joins_compile_to_uexpr(join_type):
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT)",
        dialect="postgres",
    )
    compact = lower_query(
        f"SELECT t.a, u.b FROM t {join_type} JOIN u ON t.a = u.b",
        catalog,
    )

    compiled = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert compiled.sort == compact.arena[compact.root].sort


def test_predicate_aggregate_arguments_use_scalar_expression_types():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT)", dialect="postgres")

    result = lower_query("SELECT COUNT(n > 0) FROM t", catalog)

    spec = next(
        spec
        for _, spec in catalog.context.aggregates()
        if spec.operator == "count" and spec.input == ScalarSort(BOOLEAN, True)
    )
    assert spec.input == ScalarSort(BOOLEAN, True)
    assert spec.output == ScalarSort(INTEGER, False)
    assert result.columns[0].sort == spec.output


def test_sqlite_aggregate_signatures_capture_dynamic_numeric_semantics():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT, s TEXT)", dialect="sqlite")

    result = lower_query(
        "SELECT SUM(n > 0), COUNT(n > 0), AVG(TRUE), SUM(s) FROM t",
        catalog,
    )

    signatures = [
        ("sum", ScalarSort(BOOLEAN, True)),
        ("count", ScalarSort(BOOLEAN, True)),
        ("avg", ScalarSort(BOOLEAN, False)),
        ("sum", ScalarSort(STRING, True)),
    ]
    specs = [
        next(
            spec
            for _, spec in catalog.context.aggregates()
            if spec.operator == name and spec.input == argument
        )
        for name, argument in signatures
    ]
    assert [spec.output for spec in specs] == [
        ScalarSort(INTEGER, True),
        ScalarSort(INTEGER, False),
        ScalarSort(FLOAT, True),
        ScalarSort(FLOAT, True),
    ]
    assert [column.sort for column in result.columns] == [
        spec.output for spec in specs
    ]


def test_strict_numeric_aggregates_reject_predicates():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT)", dialect="postgres")

    with pytest.raises(CatalogError):
        lower_query("SELECT SUM(n > 0) FROM t", catalog)


def test_mysql_numeric_conditions_use_shared_dialect_truthiness():
    catalog = Catalog.from_ddl("CREATE TABLE t(n INT)", dialect="mysql")

    result = lower_query("SELECT n FROM t WHERE n", catalog)

    assert result.columns[0].sort == ScalarSort(INTEGER, True)


def test_postgres_decimal_aggregates_and_arithmetic_are_typed_as_decimal():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(amount DECIMAL(8, 2), quantity INT)",
        dialect="postgres",
    )

    aggregates = lower_query(
        """
        WITH totals AS (
            SELECT
                SUM(amount) AS total,
                AVG(CAST(quantity AS DECIMAL(12, 2))) AS average
            FROM t
        )
        SELECT SUM(total), MAX(average) FROM totals
        """,
        catalog,
    )
    arithmetic = lower_query(
        """
        SELECT amount + quantity, amount * 2, amount / quantity
        FROM t
        WHERE amount BETWEEN 1 AND 100.00
          AND amount > 1.2 * (SELECT AVG(amount) FROM t)
        """,
        catalog,
    )

    amount_decimal = ScalarSort(ScalarType(TypeKind.DECIMAL, 8, 2), True)
    # PostgreSQL averages numerics without rounding to the argument's scale.
    assert [column.sort for column in aggregates.columns] == [
        amount_decimal,
        ScalarSort(DECIMAL, True),
    ]
    assert [column.sort for column in arithmetic.columns] == [
        amount_decimal,
        amount_decimal,
        amount_decimal,
    ]


def test_intervals_and_temporal_arithmetic_are_typed_during_elaboration():
    catalog = Catalog.from_ddl("CREATE TABLE t(d DATE)", dialect="postgres")

    result = lower_query(
        "SELECT d + INTERVAL '1 month', INTERVAL '30 day' + INTERVAL '2 hours' FROM t",
        catalog,
    )

    assert result.columns[0].sort.sql_type == TIMESTAMP
    assert result.columns[1].sort == ScalarSort(INTERVAL, False)
    operators = {spec.operator for _, spec in catalog.context.functions()}
    assert "add" in operators


def test_canonical_builtins_are_elaborated_without_catalog_overloads():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(s TEXT, n INT, x DECIMAL(8, 2), d DATE)",
        dialect="postgres",
    )

    result = lower_query(
        """
        SELECT SUBSTR(s, 1, 2), COALESCE(n, 0), NULLIF(n, 0),
               ROUND(x, 2), EXTRACT(YEAR FROM d), STDDEV_SAMP(x)
        FROM t
        """,
        catalog,
    )

    assert [column.sort.sql_type.kind for column in result.columns] == [
        TypeKind.STRING,
        TypeKind.INTEGER,
        TypeKind.INTEGER,
        TypeKind.DECIMAL,
        TypeKind.INTEGER,
        TypeKind.FLOAT,
    ]
    scalar_operators = {spec.operator for _, spec in catalog.context.functions()}
    aggregate_operators = {spec.operator for _, spec in catalog.context.aggregates()}
    assert {
        "substring",
        "coalesce",
        "nullif",
        "round",
        "extract_year",
    } <= scalar_operators
    assert "stddev_samp" in aggregate_operators


def test_contextual_null_and_temporal_coercions_are_elaborated_directly():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(n INT, d DATE, ts TIMESTAMP)", dialect="postgres"
    )

    result = lower_query(
        """
        SELECT COALESCE(n, NULL), NULLIF(n, NULL), d, ts
        FROM t
        WHERE d >= '2000-01-01' AND ts >= d
        """,
        catalog,
    )

    assert result.columns[0].sort == ScalarSort(INTEGER, True)
    assert result.columns[1].sort == ScalarSort(INTEGER, True)


def test_new_scalar_applications_flow_unchanged_into_uexpr():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(d DATE, n INT, s TEXT)", dialect="postgres"
    )
    compact = lower_query(
        "SELECT d + INTERVAL '1 month', COALESCE(n, 0), SUBSTR(s, 1, 2) FROM t",
        catalog,
    )

    lowered = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert lowered.sort == compact.arena[compact.root].sort


def test_named_windows_and_qualify_lower_to_uexpr():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT, category TEXT)", dialect="postgres"
    )
    compact = lower_query(
        """
        SELECT a, ROW_NUMBER() OVER ranked AS position
        FROM t
        WINDOW ranked AS (PARTITION BY category ORDER BY a)
        QUALIFY ROW_NUMBER() OVER ranked = 1
        """,
        catalog,
    )

    lowered = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert [column.name.text for column in compact.columns] == ["a", "position"]
    assert lowered.sort == compact.arena[compact.root].sort


def test_scalar_set_operations_simple_case_and_array_any_lower_to_uexpr():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)", dialect="postgres")
    compact = lower_query(
        """
        SELECT CASE a WHEN 0 THEN 1 ELSE 2 END
        FROM t
        WHERE a = ANY(ARRAY[1, 2])
          AND (SELECT CAST(NULL AS INT) UNION ALL SELECT CAST(NULL AS INT)) IS NULL
        """,
        catalog,
    )

    lowered = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert compact.columns[0].sort.sql_type == INTEGER
    assert lowered.sort == compact.arena[compact.root].sort


def test_parenthesized_join_preserves_constituent_alias_scopes():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT); CREATE TABLE v(c INT)",
        dialect="postgres",
    )

    result = lower_query(
        """
        SELECT left_source.a, nested_right.c
        FROM t AS left_source
        JOIN ((SELECT b FROM u) AS nested_left
              JOIN (SELECT c FROM v) AS nested_right
                ON nested_left.b = nested_right.c)
          ON left_source.a = nested_right.c
        """,
        catalog,
    )

    assert [column.name.text for column in result.columns] == ["a", "c"]


def test_interval_casts_share_the_canonical_interval_value_parser():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(created_at TIMESTAMP)", dialect="postgres"
    )

    result = lower_query(
        "SELECT created_at + '1 year 2 months'::interval FROM t",
        catalog,
    )

    assert result.columns[0].sort.sql_type == TIMESTAMP


def test_translation_memo_is_normalized_to_its_environment_scope():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT); CREATE TABLE s(k INT, v INT)",
        dialect="postgres",
    )
    compact = lower_query(
        """
        SELECT t.a, current_totals.total
        FROM t,
             (SELECT k, AVG(total) AS average_total
              FROM (SELECT k, SUM(v) AS total FROM s GROUP BY k) AS totals
              GROUP BY k) AS averages,
             (SELECT k, SUM(v) AS total FROM s GROUP BY k) AS current_totals
        WHERE averages.k = current_totals.k
          AND t.a = current_totals.k
        """,
        catalog,
    )

    lowered = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert lowered.sort == compact.arena[compact.root].sort


def test_nested_star_projection_preserves_duplicate_column_ordinals():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(a INT, b INT); CREATE TABLE u(a INT, c INT)",
        dialect="postgres",
    )
    compact = lower_query(
        "SELECT * FROM (SELECT * FROM t, u) AS combined",
        catalog,
    )

    lowered = UExprCompiler(
        compact.arena,
        TermArena(catalog.context),
    ).compile(compact.root)

    assert [column.name.text for column in compact.columns] == [
        "a",
        "b",
        "a",
        "c",
    ]
    assert lowered.sort == compact.arena[compact.root].sort
