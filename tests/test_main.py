"""Equivalence checking with generated databases."""

import pytest

from parseval import GenerationConfig, Verdict, disprove
from parseval.main import differ

DDL = (
    "CREATE TABLE t (id INT PRIMARY KEY, g INT, x INT);"
    "CREATE TABLE s (id INT PRIMARY KEY, tid INT REFERENCES t(id), y INT)"
)
URL = "sqlite:///:memory:"


@pytest.mark.parametrize(("q1", "q2"), [
    ("SELECT x FROM t WHERE x > 3", "SELECT x FROM t WHERE x >= 3"),
    ("SELECT x FROM t ORDER BY x", "SELECT x FROM t ORDER BY x DESC"),
    ("SELECT t.x FROM t JOIN s ON s.tid = t.id", "SELECT DISTINCT t.x FROM t JOIN s ON s.tid = t.id"),
    ("SELECT COUNT(*) FROM t", "SELECT COUNT(x) FROM t"),
    ("SELECT COUNT(x) FROM t", "SELECT SUM(CASE WHEN x IS NOT NULL THEN 1 ELSE 0 END) FROM t"),
])
def test_a_generated_database_tells_different_queries_apart(q1, q2):
    result = disprove(q1, q2, DDL, URL, "sqlite")
    assert result.verdict is Verdict.NEQ
    assert result.counterexample is not None
    assert result.results[0] != result.results[1]


@pytest.mark.parametrize(("q1", "q2"), [
    ("SELECT x FROM t WHERE x > 3", "SELECT x FROM t WHERE NOT x <= 3"),
    ("SELECT g, COUNT(*) FROM t GROUP BY g", "SELECT g, COUNT(id) FROM t GROUP BY g"),
])
def test_equivalent_queries_agree_on_every_database(q1, q2):
    assert disprove(q1, q2, DDL, URL, "sqlite").verdict is Verdict.EQ


@pytest.mark.parametrize("q2", ["SELEC x FROM t", "SELECT z FROM t", "SELECT x FROM t; SELECT g FROM t"])
def test_queries_that_do_not_parse_or_resolve_are_syntax_errors(q2):
    assert disprove("SELECT x FROM t", q2, DDL, URL, "sqlite").verdict is Verdict.SYNTAX_ERROR


def test_queries_without_rows_on_any_database_are_unknown():
    sql = "SELECT x FROM t WHERE 1 = 0"
    assert disprove(sql, sql, DDL, URL, "sqlite").verdict is Verdict.UNKNOWN


def test_generation_out_of_time_is_a_timeout():
    config = GenerationConfig(time_limit_s=0)
    result = disprove("SELECT x FROM t WHERE x > 3", "SELECT x FROM t WHERE x > 3", DDL, URL, "sqlite", config=config)
    assert result.verdict is Verdict.TIMEOUT


@pytest.mark.parametrize(("first", "second", "ordered", "set_semantics", "different"), [
    ([(1,), (1,), (2,)], [(2,), (1,)], False, False, True),
    ([(1,), (1,), (2,)], [(2,), (1,)], False, True, False),
    ([(1,), (2,)], [(2,), (1,)], True, False, True),
    ([(1,), (1,), (2,)], [(2,), (1,)], True, True, False),
    ("no such column", [], False, False, True),
    ("no such column", "no such table", False, False, False),
    (None, [(1,)], False, False, False),
])
def test_results_differ_as_bags_sets_or_lists(first, second, ordered, set_semantics, different):
    assert differ(first, second, ordered=ordered, set_semantics=set_semantics) is different


def test_duplicate_rows_do_not_tell_queries_apart_under_set_semantics():
    q1 = "SELECT t.x FROM t JOIN s ON s.tid = t.id"
    q2 = "SELECT DISTINCT t.x FROM t JOIN s ON s.tid = t.id"
    assert disprove(q1, q2, DDL, URL, "sqlite", set_semantics=True).verdict is Verdict.EQ
