import json
import sqlite3
from pathlib import Path

import pytest

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.speculate import SpeculateConfig, speculate


BIRD_SCHEMA_FP = Path("data/sqlite/schema.json")
BIRD_SQLITE_DEV_FP = Path("data/sqlite/dev.json")
BIRD_DAIL_FP = Path("data/sqlite/dail.txt")


def _bird_case(case_index: int):
    if (
        not BIRD_SCHEMA_FP.exists()
        or not BIRD_SQLITE_DEV_FP.exists()
        or not BIRD_DAIL_FP.exists()
    ):
        pytest.skip("BIRD SQLite fixtures are not available")
    dev = json.loads(BIRD_SQLITE_DEV_FP.read_text())
    schemas = json.loads(BIRD_SCHEMA_FP.read_text())
    pred_sql = BIRD_DAIL_FP.read_text().splitlines()[case_index]
    row = dev[case_index]
    raw_ddls = schemas[row["db_id"]]
    ddls = raw_ddls if isinstance(raw_ddls, str) else ";".join(raw_ddls)
    instance = Instance(ddls=ddls, name=f"bird_semantic_{case_index}", dialect="sqlite")
    expr = preprocess_sql(row["SQL"], instance, dialect="sqlite")
    plan = Plan(expr, instance)
    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig.gold_non_empty(),
    )
    return row, pred_sql, ddls, dict(results)


def _execute_on_branch(tmp_path, ddls: str, rows_per_table: dict, sql: str, label: str):
    db_path = tmp_path / f"{label}.sqlite"
    with sqlite3.connect(db_path) as connection:
        for ddl in ddls.split(";"):
            ddl = ddl.strip()
            if ddl:
                connection.execute(ddl)
        for table_name, rows in rows_per_table.items():
            if not rows:
                continue
            columns = list(rows[0])
            quoted_columns = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join(["?"] * len(columns))
            statement = (
                f'INSERT INTO "{table_name}" ({quoted_columns}) '
                f"VALUES ({placeholders})"
            )
            for row in rows:
                values = [
                    value.isoformat() if hasattr(value, "isoformat") else value
                    for value in (row.get(column) for column in columns)
                ]
                connection.execute(statement, values)
        connection.commit()
        return connection.execute(sql).fetchall()


def _assert_branch_distinguishes(
    tmp_path,
    ddls: str,
    rows_per_table: dict,
    gold_sql: str,
    pred_sql: str,
) -> None:
    gold_rows = _execute_on_branch(tmp_path, ddls, rows_per_table, gold_sql, "gold")
    pred_rows = _execute_on_branch(tmp_path, ddls, rows_per_table, pred_sql, "pred")
    assert gold_rows != pred_rows


def test_bird_case_29_join_order_limit_emits_antimatch_and_rank_witnesses(tmp_path):
    row, pred_sql, ddls, branches = _bird_case(29)

    assert "semantic_join_antimatch_0" in branches
    assert "semantic_rank_contrast_0" in branches
    assert (
        branches["semantic_join_antimatch_0"]["frpm"][0]["cdscode"]
        != branches["semantic_join_antimatch_0"]["schools"][0]["cdscode"]
    )
    frpm_rows = branches["semantic_rank_contrast_0"]["frpm"]
    assert len(frpm_rows) >= 2
    assert frpm_rows[0]["enrollment (k-12)"] > frpm_rows[1]["enrollment (k-12)"]
    _assert_branch_distinguishes(
        tmp_path,
        ddls,
        branches["semantic_join_antimatch_0"],
        row["SQL"],
        pred_sql,
    )


def test_bird_case_79_grouped_count_ranking_emits_two_group_counts(tmp_path):
    row, pred_sql, ddls, branches = _bird_case(79)

    rows = branches["semantic_aggregate_contrast_0"]["schools"]
    counts = {}
    for generated_row in rows:
        counts[generated_row["county"]] = counts.get(generated_row["county"], 0) + 1

    assert sorted(counts.values(), reverse=True)[:2] == [2, 1]


def test_bird_case_115_grouped_percentage_shape_emits_case_and_group_witnesses(tmp_path):
    row, pred_sql, ddls, branches = _bird_case(115)

    assert branches["semantic_case_contrast_0"]["client"][0]["gender"] == "M"
    district_rows = branches["semantic_aggregate_contrast_0"]["district"]
    assert len({row["a4"] for row in district_rows}) >= 2


def test_bird_case_195_derived_grouped_count_top1_emits_aggregate_contrast(tmp_path):
    row, pred_sql, ddls, branches = _bird_case(195)

    rows = branches["semantic_aggregate_contrast_0"]["bond"]
    counts = {}
    for generated_row in rows:
        counts[generated_row["bond_type"]] = counts.get(generated_row["bond_type"], 0) + 1

    assert sorted(counts.values(), reverse=True)[:2] == [2, 1]


def test_bird_case_701_virtual_subquery_alias_does_not_materialize_as_user_column():
    _row, _pred_sql, ddls, branches = _bird_case(701)
    instance = Instance(ddls=ddls, name="bird_semantic_701_schema", dialect="sqlite")

    assert branches
    for rows_per_table in branches.values():
        for table_name, rows in rows_per_table.items():
            for generated_row in rows:
                assert set(generated_row) <= set(instance.tables[table_name])


def test_bird_case_1474_grouped_sum_ranking_emits_sum_contrast(tmp_path):
    row, pred_sql, ddls, branches = _bird_case(1474)

    rows = branches["semantic_aggregate_contrast_0"]["yearmonth"]
    sums = {}
    for generated_row in rows:
        sums[generated_row["customerid"]] = (
            sums.get(generated_row["customerid"], 0) + generated_row["consumption"]
        )

    assert len(sums) >= 2
    assert len(set(sums.values())) >= 2
    _assert_branch_distinguishes(
        tmp_path,
        ddls,
        branches["semantic_aggregate_contrast_0"],
        row["SQL"],
        pred_sql,
    )
