"""Concrete execution by the concolic machine agrees with SQLite."""

import sqlite3
from collections import Counter

import pytest

from parseval.catalog import Catalog
from parseval.instance import Instance, Machine, Valuation
from parseval.parser.query import lower_query
from parseval.uexpr.lowering import UExprCompiler

DDL = (
    "CREATE TABLE t(id INT PRIMARY KEY, g INT, x INT, name TEXT);"
    "CREATE TABLE s(id INT PRIMARY KEY, tid INT REFERENCES t(id), y INT)"
)
T_ROWS = [(1, 10, 5, "ann"), (2, 10, 1, "bob"), (3, None, 7, None), (4, 20, None, "ann"), (5, 20, 5, "cy")]
S_ROWS = [(1, 1, 4), (2, 1, 9), (3, 3, None), (4, None, 2)]

QUERIES = [
    "SELECT x FROM t WHERE x > 3 AND g IS NULL",
    "SELECT x, name FROM t WHERE x IS NULL OR name = 'ann'",
    "SELECT t.x, s.y FROM t JOIN s ON s.tid = t.id",
    "SELECT t.id, s.y FROM t LEFT JOIN s ON s.tid = t.id",
    "SELECT t.x FROM t WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.tid = t.id)",
    "SELECT t.x FROM t WHERE EXISTS (SELECT 1 FROM s WHERE s.tid = t.id AND s.y > 5)",
    "SELECT g, COUNT(*), SUM(x), MIN(x), MAX(name) FROM t GROUP BY g",
    "SELECT g, COUNT(x), AVG(x) FROM t GROUP BY g HAVING COUNT(*) > 1",
    "SELECT COUNT(DISTINCT x), SUM(DISTINCT x) FROM t",
    "SELECT COUNT(*), SUM(x) FROM t WHERE x > 100",
    "SELECT DISTINCT g FROM t",
    "SELECT x FROM t WHERE x IN (SELECT y FROM s)",
    "SELECT x FROM t WHERE x NOT IN (SELECT y FROM s WHERE y IS NOT NULL)",
    "SELECT id FROM t WHERE x = (SELECT MAX(x) FROM t)",
    "SELECT id, (SELECT COUNT(*) FROM s WHERE s.tid = t.id) FROM t",
    "SELECT id, CASE WHEN x > 4 THEN 'big' WHEN x IS NULL THEN 'none' ELSE 'small' END FROM t",
    "SELECT id, COALESCE(x, g, -1) FROM t",
    "SELECT x FROM t UNION ALL SELECT y FROM s",
    "SELECT x FROM t UNION SELECT y FROM s",
    "SELECT id, x % 3, -x, x * 2 + 1 FROM t WHERE x IS NOT NULL",
    "SELECT name FROM t WHERE name LIKE 'a%'",
    "SELECT id FROM t ORDER BY x DESC, id LIMIT 2",
    "SELECT id FROM t ORDER BY x LIMIT 2 OFFSET 1",
    "SELECT tid FROM s WHERE tid = (SELECT id FROM t ORDER BY x DESC, id LIMIT 1)",
]


@pytest.fixture(scope="module")
def database():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    instance = Instance(catalog)
    for table, rows in (("t", T_ROWS), ("s", S_ROWS)):
        relation = catalog.resolve_table(table).relation
        for row in rows:
            instance, _ = instance.insert(relation, row)
    connection = sqlite3.connect(":memory:")
    connection.executescript(DDL)
    connection.executemany("INSERT INTO t VALUES (?, ?, ?, ?)", T_ROWS)
    connection.executemany("INSERT INTO s VALUES (?, ?, ?)", S_ROWS)
    return catalog, instance, connection


def execute(catalog, instance, sql):
    query = lower_query(sql, catalog)
    root = UExprCompiler(query.arena, instance.arena).compile(query.root).simplified_root
    valuation = Valuation(instance)
    machine = Machine(valuation)
    relation = machine.run(root).relation
    return [tuple(valuation.concrete(cell) for cell in row.cells) for row in machine.occurrences(relation)]


def normalize(rows):
    return [tuple(float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value
                  for value in row) for row in rows]


@pytest.mark.parametrize("sql", QUERIES)
def test_machine_matches_sqlite(database, sql):
    catalog, instance, connection = database
    actual = normalize(execute(catalog, instance, sql))
    expected = normalize(connection.execute(sql).fetchall())
    if "ORDER BY" in sql:
        assert actual == expected
    else:
        assert Counter(actual) == Counter(expected)


def test_candidate_rows_are_absent_but_symbolic(database):
    catalog, instance, _ = database
    relation = catalog.resolve_table("t").relation
    instance, candidate = instance.insert(relation, (9, 10, 50, "zed"), 0)
    query = lower_query("SELECT x FROM t WHERE x > 40", catalog)
    root = UExprCompiler(query.arena, instance.arena).compile(query.root).simplified_root
    valuation = Valuation(instance, frozenset(candidate.parameters))
    execution = Machine(valuation).run(root)
    assert valuation.concrete(execution.output) == 0
    assert valuation.is_open(execution.output)
    stored = instance.assign({instance.arena[candidate.multiplicity.expression.root].payload.parameter: 1})
    assert Valuation(stored).value(execution.output) == 1


def test_multiplicities_that_cannot_repeat_fold_repetition_to_false(database):
    catalog, instance, _ = database
    instance, candidate = instance.insert(catalog.resolve_table("t").relation, (9, 10, 50, "zed"), 0)
    weight = candidate.parameters[-1]
    v = Valuation(instance, frozenset(candidate.parameters), frozenset({weight}))
    m = v.input(candidate.multiplicity)
    # A window's share of a 0/1 weight: weight - max(0, min(weight, 3 - position)).
    integer = v.arena[v.one].sort.sql_type
    position = v.add(v.literal(2, integer), m)
    inside = v.sub(m, v.maximum(v.zero, v.minimum(m, v.sub(v.literal(3, integer), position))))
    assert v.most(m) == 1 and v.most(inside) == 1
    assert v.at_least(m, 2) == v.false and v.at_least(inside, 2) == v.false
    assert v.at_least(v.add(m, m), 2) != v.false
