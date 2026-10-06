"""End-to-end concolic generation."""

import sqlite3
from collections import Counter

import pytest

from parseval.catalog import Catalog
from parseval.generator import GenerationConfig, Session, generate

DDL = (
    "CREATE TABLE t(id INT PRIMARY KEY, g INT, x INT);"
    "CREATE TABLE s(id INT PRIMARY KEY, tid INT REFERENCES t(id), y INT)"
)

QUERIES = [
    "SELECT x FROM t WHERE x > 3 AND g IS NULL",
    "SELECT t.x FROM t WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.tid = t.id)",
    "SELECT t.g, COUNT(*) FROM t LEFT JOIN s ON s.tid = t.id WHERE t.x > 1 GROUP BY t.g HAVING COUNT(*) > 1",
    "SELECT x FROM t WHERE x IN (SELECT y FROM s) OR x = (SELECT MAX(y) FROM s)",
    "SELECT g, SUM(x), AVG(x) FROM t GROUP BY g HAVING AVG(x) > 10",
    "SELECT y FROM s WHERE tid = (SELECT id FROM t ORDER BY x DESC LIMIT 1)",
    "SELECT DISTINCT g FROM t ORDER BY g LIMIT 1",
    "SELECT x FROM t WHERE g = (SELECT g FROM t GROUP BY g ORDER BY COUNT(*) DESC LIMIT 1)",
    "SELECT t.x FROM t JOIN s ON s.tid = t.id ORDER BY s.y DESC LIMIT 3, 1",
]


def replay(instance, catalog, sql):
    connection = sqlite3.connect(":memory:")
    connection.executescript(DDL)
    for table in catalog.tables():
        name = table.name.parts[-1].text
        for row in instance.rows(table.relation):
            connection.execute(f"INSERT INTO {name} VALUES ({', '.join('?' for _ in row)})", row)
    connection.execute("PRAGMA foreign_key_check")
    return connection.execute(sql).fetchall()


@pytest.mark.parametrize("sql", QUERIES)
def test_generated_database_is_productive_and_valid(sql):
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000))
    assert result.nonempty
    assert replay(result.instance, catalog, sql)
    assert result.coverage.covered
    for table in catalog.tables():
        keys = [row[0] for row in result.instance.rows(table.relation)]
        assert None not in keys
        assert len(keys) == len(set(keys))


def test_accepted_steps_only_append_rows():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    session = Session(catalog, "SELECT t.x FROM t JOIN s ON s.tid = t.id WHERE s.y > t.x", GenerationConfig(timeout_ms=3000))
    current = session.execute(session.with_candidates(session.empty))
    steps = 0
    for _ in range(6):
        target = session._next(current, set())
        if target is None:
            break
        following, attempt = session.attempt(current, target)
        if following is None:
            continue
        for relation in current.instance.relations():
            after = Counter(following.instance.rows(relation))
            after.subtract(Counter(current.instance.rows(relation)))
            assert min(after.values(), default=0) >= 0
        current, steps = following, steps + 1
    assert steps


def test_uncorrelated_both_outcomes_cannot_share_one_database():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    result = generate("SELECT COUNT(*) FROM t WHERE NOT EXISTS (SELECT 1 FROM s)", catalog)
    assert result.nonempty
    assert any(label.endswith("predicate.not3:false") for label in result.coverage.failed)


@pytest.mark.parametrize(
    ("sql", "distinct"),
    [
        ("SELECT g, COUNT(*) FROM t GROUP BY g", True),
        ("SELECT COUNT(*) FROM t GROUP BY g", False),
        ("SELECT g FROM t GROUP BY g, x", False),
        ("SELECT x, g FROM t GROUP BY g, x ORDER BY g LIMIT 3", True),
        ("SELECT DISTINCT g FROM t", True),
        ("SELECT COUNT(*) FROM t", True),
        ("SELECT g FROM t", False),
    ],
)
def test_rows_distinct_by_construction_have_no_duplicate_outcome(sql, distinct):
    from parseval.generator.generate import OUTPUT, distinct_rows
    from parseval.parser.query import lower_query

    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    query = lower_query(sql, catalog, ignore_root_limit=True)
    assert distinct_rows(query.arena, query.root) is distinct
    session = Session(catalog, sql, GenerationConfig(timeout_ms=3000))
    reached = session.execute(session.with_candidates(session.empty)).coverage.reached
    assert any(target.site == OUTPUT.site and target.outcome == "duplicate" for target in reached) is not distinct


@pytest.mark.parametrize("speculate", [True, False])
def test_rows_with_different_values_are_grown_when_needed(speculate):
    # One row is never above its own average: the output needs a second,
    # different row, which repeating a candidate row cannot provide.
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    sql = "SELECT x FROM t WHERE x > (SELECT AVG(x) FROM t)"
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000, speculate=speculate))
    assert result.nonempty
    assert replay(result.instance, catalog, sql)


# Cases speculation does not solve (arithmetic equations) or solves only in
# part: the concolic layer must still produce output for each.
BEYOND_SPECULATION = [
    "SELECT x FROM t WHERE x + g = 9 AND x > 2",
    "SELECT a.x FROM t AS a, t AS b WHERE a.x < b.g AND a.id < b.id",
    "SELECT x FROM t WHERE CAST(x AS REAL) / g > 0.3",
    "SELECT s.y FROM s JOIN t ON s.tid = t.id WHERE s.y * 2 = t.x + 1",
]


@pytest.mark.parametrize("speculate", [True, False])
@pytest.mark.parametrize("sql", BEYOND_SPECULATION)
def test_concolic_layer_covers_what_speculation_misses(sql, speculate):
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000, speculate=speculate))
    assert result.nonempty
    assert replay(result.instance, catalog, sql)


@pytest.mark.parametrize("speculate", [True, False])
def test_probed_filters_reach_what_replay_reaches(speculate):
    # s.y = 7 is looked up as a key; the filters scheduled after the lookup
    # must be reached by candidate rows exactly as by stored ones.
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    # Without scheduling the probed filter right after its row, two solutions
    # of this query were satisfiable but failed to replay.
    sql = "SELECT s.y FROM t JOIN s ON s.tid = t.id WHERE t.x BETWEEN 2 AND 5 AND s.y = 7"
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000, speculate=speculate))
    assert result.nonempty
    assert not [attempt for attempt in result.attempts if attempt.status.value == "sat" and not attempt.accepted]
    assert "/bag.lambda:output" in result.coverage.covered


def test_outcomes_needing_two_new_rows_per_binding_are_covered():
    # c.id is both c's key and a foreign key: once every stored p has its c,
    # a new output row needs a new c and a new p, beyond one candidate row
    # per binding.
    ddl = "CREATE TABLE p(id INT PRIMARY KEY, v INT); CREATE TABLE c(id INT PRIMARY KEY REFERENCES p(id), w INT)"
    catalog = Catalog.from_ddl(ddl, dialect="sqlite")
    sql = "SELECT p.v FROM c JOIN p ON c.id = p.id ORDER BY c.w DESC LIMIT 5, 1"
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000))
    assert result.nonempty
    assert {"/order.map:duplicate", "-1/order.map:duplicate"} <= set(result.coverage.covered)


def test_generation_returns_its_best_database_at_the_time_limit():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    result = generate(QUERIES[2], catalog, config=GenerationConfig(time_limit_s=0))
    assert not result.attempts and not result.nonempty
