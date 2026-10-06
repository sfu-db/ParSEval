"""Speculation: lineage hints from the compact IR and coverage-greedy seeding."""

import sqlite3

import pytest

from parseval.catalog import Catalog
from parseval.generator import GenerationConfig, Session
from parseval.parser.query import lower_query
from parseval.speculate import Speculator
from parseval.speculate.speculate import _atoms

DDL = (
    "CREATE TABLE t(id INT PRIMARY KEY, g INT, x INT, note TEXT);"
    "CREATE TABLE s(id INT PRIMARY KEY, tid INT REFERENCES t(id), y INT)"
)


def speculator(sql):
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    session = Session(catalog, sql, GenerationConfig())
    query = lower_query(sql, catalog)
    return session, Speculator(session.empty, query.arena, query.root)


def column(session, spec, table, position):
    """The column of the first occurrence of ``table`` in the query."""
    relation = next(item.relation for item in session.catalog.tables() if item.name.parts[-1].text == table)
    return min(occurrence for occurrence, other in spec.relation_of.items() if other == relation), position


def test_joins_and_foreign_keys_share_a_class():
    session, spec = speculator("SELECT t.x FROM t JOIN s ON s.y = t.x")
    assert spec.classes[column(session, spec, "s", 2)] == spec.classes[column(session, spec, "t", 2)]
    # The foreign key needs some parent row: its own occurrence of t, not the query's t.
    s_occurrence = column(session, spec, "s", 0)[0]
    (parent,) = spec.parents[s_occurrence]
    assert spec.classes[s_occurrence, 1] == spec.classes[parent, 0]
    assert spec.classes[column(session, spec, "s", 1)] != spec.classes[column(session, spec, "t", 0)]
    assert spec.classes[column(session, spec, "t", 1)] != spec.classes[column(session, spec, "t", 2)]


def test_self_join_occurrences_get_their_own_rows():
    session, spec = speculator("SELECT a.x, b.x FROM t AS a, t AS b WHERE a.id < b.id AND a.g = b.g")
    occurrences = sorted({occurrence for occurrence, _ in spec.classes})
    a, b = occurrences[:2]
    assert spec.classes[a, 1] == spec.classes[b, 1]  # a.g = b.g
    assert spec.classes[a, 0] != spec.classes[b, 0]  # a.id < b.id needs two rows
    catalog = session.catalog
    instance = session.speculate()
    assert replay(instance, catalog, "SELECT a.x, b.x FROM t AS a, t AS b WHERE a.id < b.id AND a.g = b.g")


@pytest.mark.parametrize("sql", [
    "SELECT x FROM t WHERE CAST(x AS REAL) / g > 0.3",
    "SELECT x FROM t WHERE x > g AND x > 2",
    "SELECT x FROM t WHERE x + g = 9 AND x > 2",
    "SELECT s.y FROM s JOIN t ON s.tid = t.id WHERE s.y * 2 = t.x + 1",
    "SELECT a.x FROM t AS a, t AS b WHERE a.x < b.g",
])
def test_joint_tests_choose_columns_together(sql):
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    for seed in range(5):
        instance = Session(catalog, sql, GenerationConfig(seed=seed)).speculate()
        assert replay(instance, catalog, sql)


def test_multi_column_checks_hold():
    catalog = Catalog.from_ddl("CREATE TABLE r (id INT PRIMARY KEY, lo INT, hi INT, CHECK (lo < hi))", dialect="sqlite")
    instance = Session(catalog, "SELECT id FROM r WHERE lo > 5", GenerationConfig()).speculate()
    rows = instance.rows(catalog.tables()[0].relation)
    assert rows and all(lo is None or hi is None or lo < hi for _, lo, hi in rows)


def test_constants_keys_and_row_demand():
    session, spec = speculator("SELECT g, COUNT(*) FROM t WHERE x > 7 AND note LIKE 'ab%' GROUP BY g HAVING COUNT(*) > 4")
    assert {7, 8, 6} <= set(spec.pools[spec.classes[column(session, spec, "t", 2)]])
    assert "ab" in spec.pools[spec.classes[column(session, spec, "t", 3)]]
    assert spec.classes[column(session, spec, "t", 1)] in spec.keyed
    assert spec.size == 5


@pytest.mark.parametrize(
    ("sql", "groups", "size"),
    [
        ("SELECT x FROM t ORDER BY x LIMIT 1", 2, 1),
        ("SELECT x FROM t LIMIT 5", 6, 1),
        ("SELECT x FROM t ORDER BY x LIMIT 1 OFFSET 4", 6, 1),
        ("SELECT x FROM t", 1, 1),
        ("SELECT g FROM t GROUP BY g HAVING COUNT(*) > 4 ORDER BY g LIMIT 1", 2, 5),
        ("SELECT g FROM t GROUP BY g HAVING COUNT(*) >= 4", 1, 4),
        ("SELECT g FROM t GROUP BY g HAVING COUNT(*) < 4", 1, 1),
        ("SELECT x FROM t WHERE x > (SELECT AVG(x) FROM t)", 2, 1),
        ("SELECT x FROM t WHERE x - g < (SELECT MAX(x - g) FROM t)", 2, 1),
        ("SELECT x FROM t WHERE x >= (SELECT AVG(x) FROM t)", 1, 1),
        ("SELECT x FROM t WHERE x > (SELECT AVG(y) FROM s)", 1, 1),
    ],
)
def test_limits_ask_for_groups_and_counts_for_group_size(sql, groups, size):
    spec = speculator(sql)[1]
    assert (spec.groups, spec.size) == (groups, size)


QUERIES = [
    "SELECT x FROM t WHERE x > 3 AND g IS NULL",
    "SELECT t.g, COUNT(*) FROM t LEFT JOIN s ON s.tid = t.id WHERE t.x > 1 GROUP BY t.g HAVING COUNT(*) > 1",
    "SELECT y FROM s WHERE tid = (SELECT id FROM t ORDER BY x DESC LIMIT 1)",
    "SELECT t.x FROM t JOIN s ON s.tid = t.id ORDER BY s.y LIMIT 5, 1",
    "SELECT x FROM t WHERE note LIKE 'ab%'",
]


def replay(instance, catalog, sql):
    connection = sqlite3.connect(":memory:")
    connection.executescript(DDL)
    connection.execute("PRAGMA foreign_keys = ON")
    for table in catalog.tables():
        name = table.name.parts[-1].text
        for row in instance.rows(table.relation):
            connection.execute(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in row)})", row)
    return connection.execute(sql).fetchall()


@pytest.mark.parametrize("sql", QUERIES)
def test_speculation_alone_is_productive_and_valid(sql):
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    session = Session(catalog, sql, GenerationConfig())
    instance = session.speculate()
    assert replay(instance, catalog, sql)


def test_unmentioned_columns_take_various_values():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    instance = Session(catalog, "SELECT x FROM t WHERE x > 3", GenerationConfig()).speculate()
    rows = [row for table in catalog.tables() for row in instance.rows(table.relation)]
    assert rows
    assert any(len({value for value in row if value is not None}) > 1 for row in rows)


def test_rows_that_would_block_output_are_not_kept():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    sql = "SELECT COUNT(*) FROM t WHERE NOT EXISTS (SELECT 1 FROM s)"
    instance = Session(catalog, sql, GenerationConfig()).speculate()
    assert not any(instance.rows(table.relation) for table in catalog.tables() if table.name.parts[-1].text == "s")


def test_provider_supplies_fresh_values():
    calls = []

    def provider(table, column, existing, unique):
        calls.append((column.name.text, unique))
        if column.storage_type.kind.value == "string":
            return f"{column.name.text}-{len(existing)}"
        return max(existing, default=0) + 1 if unique else 7

    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    config = GenerationConfig(provider=provider)
    instance = Session(catalog, "SELECT note FROM t WHERE x > 3", config).speculate()
    assert ("id", True) in calls and ("note", False) in calls
    notes = [row[3] for table in catalog.tables() for row in instance.rows(table.relation) if len(row) == 4]
    assert any(isinstance(note, str) and note.startswith("note-") for note in notes)


def test_sequential_skips_values_in_use():
    from parseval.instance.domain import sequential

    table = Catalog.from_ddl(DDL, dialect="sqlite").tables()[0]
    x, note = table.columns[2], table.columns[3]
    assert sequential(table, x, set(), False) == 1
    assert sequential(table, x, {1, 2, 4}, True) == 5
    assert sequential(table, note, {"a"}, False) == "b"


def test_speculated_rows_keep_columns_distinct():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    instance = Session(catalog, "SELECT note FROM t", GenerationConfig()).speculate()
    first = next(row for table in catalog.tables() for row in instance.rows(table.relation))
    integers = [value for value in first if isinstance(value, int)]
    assert len(integers) == len(set(integers)) > 1


def test_dependence_spreads_constants_but_not_classes():
    session, spec = speculator("SELECT g FROM t WHERE x + g = 9 GROUP BY g HAVING SUM(x) / COUNT(*) > 400")
    x, g = spec.classes[column(session, spec, "t", 2)], spec.classes[column(session, spec, "t", 1)]
    assert x != g
    assert {400, 9} <= set(spec.pools[x])
    atoms = [atom for formula in spec.formulas for atom in _atoms(formula)]
    # x + g = 9 is a joint test over both; SUM(x) / COUNT(*) > 400 forms no atom.
    assert [set(atom.columns) for atom in atoms] == [{x, g}]


def test_sequential_values_fit_the_column():
    from parseval.instance.domain import sequential

    table = Catalog.from_ddl(
        "CREATE TABLE c (flag CHAR(1), small SMALLINT, ratio DECIMAL(3, 2), tiny DECIMAL(2, 2))", dialect="postgres"
    ).tables()[0]
    flag, small, ratio, tiny = table.columns
    used: set = set()
    for _ in range(30):
        used.add(sequential(table, flag, used, False))
    assert all(len(value) == 1 for value in used) and len(used) == 26
    assert sequential(table, small, set(), True) == 1
    assert all(abs(sequential(table, ratio, set(range(n)), False)) < 10 for n in range(0, 40, 7))
    assert 0 < sequential(table, tiny, set(), True) < 1


def test_aggregate_argument_goals_form_their_own_group():
    # A group whose x are all NULL makes SUM(x) NULL and HAVING UNKNOWN; joining
    # an existing group with non-NULL x would not.
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    session = Session(catalog, "SELECT g, SUM(x) FROM t GROUP BY g HAVING SUM(x) > 5", GenerationConfig())
    coverage = session.execute(session.with_candidates(session.speculate())).coverage
    labels = {session.label(target) for target in coverage.covered}
    assert any(label.endswith("predicate.lt3:unknown") for label in labels), sorted(labels)
