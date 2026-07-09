from types import SimpleNamespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_DIR = Path(__file__).resolve().parent
for path in (ROOT, EXPERIMENT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from parseval.disprover import Disprover
from parseval.states import Verdict
from mysql_case_fixture import load_leetcode_case
from scripts.classify_mysql_results import classify_records, sql_shape_tags
from test_mysql import (
    _prepare_mysql_query,
    build_ddl,
)


def test_build_ddl_unwraps_literal_constraint_values():
    schema = {
        "PRODUCT": {
            "PRODUCT_ID": "INT",
            "PRODUCT_NAME": "VARCHAR",
        },
    }
    constraints = [
        {
            "in": [
                {"value": "PRODUCT__PRODUCT_NAME"},
                [{"literal": "S8"}, {"literal": "IPHONE"}],
            ]
        }
    ]

    ddl = build_ddl(schema, constraints)

    assert "CHECK (PRODUCT_NAME IN ('S8', 'IPHONE'))" in ddl
    assert "{'literal':" not in ddl


def test_prepare_mysql_query_does_not_quote_enum_operator_tokens():
    schema = {
        "EXPRESSIONS": {
            "LEFT_OPERAND": "VARCHAR",
            "OPERATOR": "ENUM,<,>,=",
            "RIGHT_OPERAND": "VARCHAR",
        },
        "VARIABLES": {
            "NAME": "VARCHAR",
            "VALUE": "INT",
        },
    }
    sql = (
        "SELECT A.* FROM EXPRESSIONS AS A "
        "JOIN VARIABLES AS B ON A.LEFT_OPERAND = B.NAME "
        "JOIN VARIABLES AS C ON A.RIGHT_OPERAND = C.NAME "
        "WHERE B.VALUE < C.VALUE AND A.OPERATOR = '<'"
    )

    prepared = _prepare_mysql_query(sql, schema)

    assert "B.VALUE < C.VALUE" in prepared
    assert "A.OPERATOR = '<'" in prepared
    assert "LEFT_OPERAND = B.NAME" in prepared


def test_build_ddl_keeps_multiple_primary_constraints_as_candidate_keys():
    schema = {
        "USERS": {
            "ACCOUNT": "INT",
            "NAME": "VARCHAR",
        },
    }
    constraints = [
        {"primary": [{"value": "USERS__ACCOUNT"}]},
        {"primary": [{"value": "USERS__NAME"}]},
    ]

    ddl = build_ddl(schema, constraints)

    assert "PRIMARY KEY (ACCOUNT)" in ddl
    assert "UNIQUE (NAME)" in ddl
    assert "PRIMARY KEY (ACCOUNT, NAME)" not in ddl


def test_db_write_failure_is_runtime_error_not_syntax_error(monkeypatch):
    disprover = Disprover(
        "SELECT id FROM t",
        "SELECT id FROM t WHERE id > 0",
        "CREATE TABLE t (id INT)",
        dialect="mysql",
        connection_string="mysql+pymysql://root:rootpass@localhost:3306/test",
    )
    monkeypatch.setattr(
        "parseval.disprover.SymbolicEngine",
        lambda *args, **kwargs: SimpleNamespace(
            generate=lambda **_kwargs: SimpleNamespace(
                rows_generated=1,
                coverage=1.0,
            )
        ),
    )
    monkeypatch.setattr(
        "parseval.disprover.to_db",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("Out of range value for column 'SEAT_ID'")
        ),
    )

    result = disprover._try_generate_and_compare("SELECT id FROM t", 0.0)

    assert result.verdict == Verdict.RUNTIME_ERROR
    assert result.error_msg == "DB write failed: Out of range value for column 'SEAT_ID'"


def test_leetcode_case_fixture_loads_prepared_case_by_index():
    case = load_leetcode_case(2466)

    assert case.index == 2466
    assert case.ddl.startswith("CREATE TABLE")
    assert len(case.pair) == 2


def test_mysql_result_classifier_tags_evaluator_relevant_shapes():
    records = [
        {
            "index": 2466,
            "verdict": "unknown",
            "debug_category": "timeout",
            "sql1": "SELECT * FROM t WHERE (lat, lon) IN (SELECT lat, lon FROM u)",
            "sql2": "SELECT * FROM t",
        },
        {
            "index": 5772,
            "verdict": "unknown",
            "debug_category": "execution_error",
            "sql1": (
                "SELECT customer_id FROM customer GROUP BY customer_id "
                "HAVING COUNT(DISTINCT product_key) = "
                "(SELECT COUNT(DISTINCT product_key) FROM product)"
            ),
            "sql2": "SELECT customer_id FROM customer",
        },
    ]

    summary = classify_records(records)

    assert "tuple_in_subquery" in sql_shape_tags(records[0]["sql1"])
    assert summary["verdict_counts"] == {"unknown": 2}
    assert summary["representative_indices"]["tuple_in_subquery"] == [2466]
    assert summary["representative_indices"][
        "relational_division_count_distinct"
    ] == [5772]
