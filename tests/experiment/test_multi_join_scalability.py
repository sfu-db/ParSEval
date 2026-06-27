"""Scalability tests for multi-join query generation.

Tests the speculate framework's ability to handle queries with increasing
numbers of joins (1, 2, 3, 5, 7, 10) and various condition types.
"""

import json
import os
import time

import pytest

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.speculate import speculate, SpeculateConfig


# ---------------------------------------------------------------------------
# Schema: a chain of tables linked by foreign keys
# ---------------------------------------------------------------------------

CHAIN_SCHEMA = """
CREATE TABLE t0 (
    id INTEGER NOT NULL PRIMARY KEY,
    val TEXT,
    flag INTEGER
);
CREATE TABLE t1 (
    id INTEGER NOT NULL PRIMARY KEY,
    t0_id INTEGER NOT NULL,
    val TEXT,
    score REAL,
    FOREIGN KEY (t0_id) REFERENCES t0(id)
);
CREATE TABLE t2 (
    id INTEGER NOT NULL PRIMARY KEY,
    t1_id INTEGER NOT NULL,
    val TEXT,
    amount REAL,
    FOREIGN KEY (t1_id) REFERENCES t1(id)
);
CREATE TABLE t3 (
    id INTEGER NOT NULL PRIMARY KEY,
    t2_id INTEGER NOT NULL,
    val TEXT,
    count INTEGER,
    FOREIGN KEY (t2_id) REFERENCES t2(id)
);
CREATE TABLE t4 (
    id INTEGER NOT NULL PRIMARY KEY,
    t3_id INTEGER NOT NULL,
    val TEXT,
    flag INTEGER,
    FOREIGN KEY (t3_id) REFERENCES t3(id)
);
CREATE TABLE t5 (
    id INTEGER NOT NULL PRIMARY KEY,
    t4_id INTEGER NOT NULL,
    val TEXT,
    score REAL,
    FOREIGN KEY (t4_id) REFERENCES t4(id)
);
CREATE TABLE t6 (
    id INTEGER NOT NULL PRIMARY KEY,
    t5_id INTEGER NOT NULL,
    val TEXT,
    amount REAL,
    FOREIGN KEY (t5_id) REFERENCES t5(id)
);
CREATE TABLE t7 (
    id INTEGER NOT NULL PRIMARY KEY,
    t6_id INTEGER NOT NULL,
    val TEXT,
    count INTEGER,
    FOREIGN KEY (t6_id) REFERENCES t6(id)
);
CREATE TABLE t8 (
    id INTEGER NOT NULL PRIMARY KEY,
    t7_id INTEGER NOT NULL,
    val TEXT,
    flag INTEGER,
    FOREIGN KEY (t7_id) REFERENCES t7(id)
);
CREATE TABLE t9 (
    id INTEGER NOT NULL PRIMARY KEY,
    t8_id INTEGER NOT NULL,
    val TEXT,
    score REAL,
    FOREIGN KEY (t8_id) REFERENCES t8(id)
);
CREATE TABLE t10 (
    id INTEGER NOT NULL PRIMARY KEY,
    t9_id INTEGER NOT NULL,
    val TEXT,
    amount REAL,
    FOREIGN KEY (t9_id) REFERENCES t9(id)
);
"""


def _build_chain_query(n_joins: int, n_conditions: int = 0) -> str:
    """Build a query joining t0..t(n_joins) with optional conditions."""
    select_cols = [f"t0.val"]
    from_clause = "t0"
    joins = []

    for i in range(1, n_joins + 1):
        alias = f"t{i}"
        parent = f"t{i-1}"
        fk_col = f"{parent}_id"
        joins.append(f"JOIN {alias} ON {parent}.id = {alias}.{fk_col}")
        select_cols.append(f"{alias}.val")

    # Add conditions using val IS NOT NULL (exists on all tables)
    conditions = []
    for i in range(min(n_conditions, n_joins + 1)):
        alias = f"t{i}"
        conditions.append(f"{alias}.val IS NOT NULL")

    sql = f"SELECT {', '.join(select_cols)} FROM {from_clause}"
    if joins:
        sql += " " + " ".join(joins)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    return sql


def _build_star_query(n_joins: int) -> str:
    """Build a chain query with IS NOT NULL on each join key."""
    select_cols = ["t0.val"]
    from_clause = "t0"
    joins = []
    conditions = []

    for i in range(1, n_joins + 1):
        alias = f"t{i}"
        parent = f"t{i-1}"
        fk_col = f"{parent}_id"
        joins.append(f"JOIN {alias} ON {parent}.id = {alias}.{fk_col}")
        select_cols.append(f"{alias}.val")
        conditions.append(f"{alias}.{fk_col} IS NOT NULL")

    sql = f"SELECT {', '.join(select_cols)} FROM {from_clause}"
    if joins:
        sql += " " + " ".join(joins)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    return sql


def _build_filter_chain_query(n_joins: int) -> str:
    """Build a chain query with filters on each table."""
    tables = [f"t{i}" for i in range(n_joins + 1)]
    select_cols = [f"t0.val"]
    from_clause = "t0"
    joins = []
    conditions = []

    for i in range(1, n_joins + 1):
        alias = f"t{i}"
        parent = f"t{i-1}"
        fk_col = f"{parent}_id"
        joins.append(f"JOIN {alias} ON {parent}.id = {alias}.{fk_col}")
        select_cols.append(f"{alias}.val")
        conditions.append(f"{alias}.val IS NOT NULL")

    sql = f"SELECT {', '.join(select_cols)} FROM {from_clause}"
    if joins:
        sql += " " + " ".join(joins)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)

    return sql


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------

def _run_speculate(sql: str, ddls: str, label: str) -> dict:
    """Run speculate on a query and return metrics."""
    instance = Instance(ddls=ddls, name=f"test_{label}", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    t0 = time.time()
    results = speculate(plan, instance, dialect="sqlite")
    elapsed = time.time() - t0

    total_rows = sum(len(rows) for _, rows_per_table in results for rows in rows_per_table.values())
    tables_touched = set()
    for _, rows_per_table in results:
        tables_touched.update(rows_per_table.keys())

    return {
        "label": label,
        "n_joins": label.split("_")[1] if "_" in label else "0",
        "success": total_rows > 0,
        "branches": len(results),
        "total_rows": total_rows,
        "tables_touched": len(tables_touched),
        "elapsed_ms": round(elapsed * 1000, 1),
        "sql": sql[:200],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestChainJoins:
    """Test queries joining tables in a chain: t0 -> t1 -> t2 -> ..."""

    @pytest.mark.parametrize("n_joins", [1, 2, 3, 5, 7, 10])
    def test_chain_join(self, n_joins):
        sql = _build_chain_query(n_joins, n_conditions=0)
        result = _run_speculate(sql, CHAIN_SCHEMA, f"chain_{n_joins}_joins")
        print(f"\n  {result['label']}: {'PASS' if result['success'] else 'FAIL'} "
              f"({result['elapsed_ms']}ms, {result['total_rows']} rows, "
              f"{result['tables_touched']} tables)")
        assert result["success"], f"Failed for {n_joins} chain joins"

    @pytest.mark.parametrize("n_joins", [1, 2, 3, 5, 7, 10])
    def test_chain_join_with_conditions(self, n_joins):
        sql = _build_chain_query(n_joins, n_conditions=n_joins)
        result = _run_speculate(sql, CHAIN_SCHEMA, f"chain_{n_joins}_joins_cond")
        print(f"\n  {result['label']}: {'PASS' if result['success'] else 'FAIL'} "
              f"({result['elapsed_ms']}ms, {result['total_rows']} rows)")
        assert result["success"], f"Failed for {n_joins} chain joins with conditions"


class TestStarJoins:
    """Test star-join queries: t0 JOIN t1 ON t0.id=t1.t0_id JOIN t2 ON ..."""

    @pytest.mark.parametrize("n_joins", [1, 2, 3, 5, 7, 10])
    def test_star_join(self, n_joins):
        sql = _build_star_query(n_joins)
        result = _run_speculate(sql, CHAIN_SCHEMA, f"star_{n_joins}_joins")
        print(f"\n  {result['label']}: {'PASS' if result['success'] else 'FAIL'} "
              f"({result['elapsed_ms']}ms, {result['total_rows']} rows, "
              f"{result['tables_touched']} tables)")
        assert result["success"], f"Failed for {n_joins} star joins"


class TestFilterChainJoins:
    """Test chain joins with IS NOT NULL filters on each table."""

    @pytest.mark.parametrize("n_joins", [1, 2, 3, 5, 7, 10])
    def test_filter_chain(self, n_joins):
        sql = _build_filter_chain_query(n_joins)
        result = _run_speculate(sql, CHAIN_SCHEMA, f"filter_chain_{n_joins}")
        print(f"\n  {result['label']}: {'PASS' if result['success'] else 'FAIL'} "
              f"({result['elapsed_ms']}ms, {result['total_rows']} rows)")
        assert result["success"], f"Failed for {n_joins} filter chain joins"


class TestScalabilitySummary:
    """Run all configurations and print a summary table."""

    def test_full_summary(self):
        """Run all configurations and report scalability limits."""
        configs = []

        # Chain joins
        for n in [1, 2, 3, 5, 7, 10]:
            sql = _build_chain_query(n, 0)
            configs.append((f"chain_{n}", sql))

        # Chain joins with conditions
        for n in [1, 2, 3, 5, 7, 10]:
            sql = _build_chain_query(n, n)
            configs.append((f"chain_{n}_cond", sql))

        # Star joins
        for n in [1, 2, 3, 5, 7, 10]:
            sql = _build_star_query(n)
            configs.append((f"star_{n}", sql))

        # Filter chain
        for n in [1, 2, 3, 5, 7, 10]:
            sql = _build_filter_chain_query(n)
            configs.append((f"filter_{n}", sql))

        results = []
        for label, sql in configs:
            r = _run_speculate(sql, CHAIN_SCHEMA, label)
            results.append(r)

        # Print summary table
        print(f"\n{'='*70}")
        print(f"{'Config':<20} {'Joins':>5} {'Status':>8} {'Rows':>6} {'Tables':>7} {'Time(ms)':>9}")
        print(f"{'='*70}")
        for r in results:
            status = "PASS" if r["success"] else "FAIL"
            print(f"{r['label']:<20} {r['n_joins']:>5} {status:>8} {r['total_rows']:>6} "
                  f"{r['tables_touched']:>7} {r['elapsed_ms']:>9.1f}")
        print(f"{'='*70}")

        # Report scalability limit
        passing = [r for r in results if r["success"]]
        failing = [r for r in results if not r["success"]]
        if passing:
            max_joins = max(int(r["n_joins"]) for r in passing)
            print(f"\nMax joins that pass: {max_joins}")
        if failing:
            min_fail = min(int(r["n_joins"]) for r in failing)
            print(f"Min joins that fail: {min_fail}")

        # All should pass for reasonable join counts
        for r in results:
            assert r["success"], f"{r['label']} failed with {r['n_joins']} joins"
