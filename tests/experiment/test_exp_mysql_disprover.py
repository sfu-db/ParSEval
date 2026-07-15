import json
import sys
from pathlib import Path

from sqlglot import exp, parse_one

from parseval.generator import BmcBounds, generate

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.exp_mysql_disprover import build_ddl, _prepare_mysql_query


def _first_leetcode_entry():
    with open("data/mysql/leetcode.jsonlines") as f:
        return json.loads(next(f))


def test_mysql_experiment_canonicalizes_unquoted_identifiers():
    entry = _first_leetcode_entry()

    ddl = build_ddl(entry["schema"], entry.get("constraint") or [])
    sql = _prepare_mysql_query(entry["pair"][0], entry["schema"])

    assert "CREATE TABLE person" in ddl
    assert "personid INT" in ddl
    assert "CREATE TABLE PERSON" not in ddl
    assert "PERSONID INT" not in ddl

    parsed = parse_one(sql, dialect="mysql")
    assert {
        column.name
        for column in parsed.find_all(exp.Column)
        if column.name
    } == {"firstname", "lastname", "city", "state", "personid"}


def test_mysql_experiment_first_case_generates_with_canonical_identifiers():
    entry = _first_leetcode_entry()
    ddl = build_ddl(entry["schema"], entry.get("constraint") or [])
    sql = _prepare_mysql_query(entry["pair"][0], entry["schema"])

    instance = generate(
        ddl,
        sql,
        dialect="mysql",
        bounds=BmcBounds(max_iterations=1),
        generate_negatives=True,
    )

    assert instance.get_rows("person")
