"""Tests for the DataFusion Step-IR planner in ``parseval.plan.explain``."""

from __future__ import annotations

import pytest
from sqlglot import exp

from parseval.plan.session import DataFusionSessionManager
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Limit,
    PlanError,
    Projection,
    ScalarSubqueryRef,
    Sort,
    Step,
    SubqueryAlias,
    TableScan,
    Union,
    Values,
    Window,
    assert_no_bare_string_identifiers,
    explain,
    repr_expr,
    repr_step,
    _annotate_type,
    _from_logical,
)


def _prepare(sql: str, dialect: str = "sqlite") -> str:
    return DataFusionSessionManager(dialect).prepare_query(sql)


DDL_T = "CREATE TABLE t(a INT, b TEXT)"
DDL_TU = "CREATE TABLE t(a INT); CREATE TABLE u(a INT)"
DDL_TOXICOLOGY = """
CREATE TABLE `atom` (
  `atom_id` TEXT NOT NULL,
  `molecule_id` TEXT DEFAULT NULL,
  `element` TEXT DEFAULT NULL,
  PRIMARY KEY (`atom_id`)
);
CREATE TABLE `bond` (
  `bond_id` TEXT NOT NULL,
  `molecule_id` TEXT DEFAULT NULL,
  `bond_type` TEXT DEFAULT NULL,
  PRIMARY KEY (`bond_id`)
);
CREATE TABLE `connected` (
  `atom_id` TEXT NOT NULL,
  `atom_id2` TEXT NOT NULL,
  `bond_id` TEXT DEFAULT NULL,
  PRIMARY KEY (`atom_id`,`atom_id2`)
);
"""


def _types(plan) -> set[str]:
    return {type(step).__name__ for step in plan.dag}


def _step_expressions(step: Step) -> list[exp.Expression]:
    exprs: list[exp.Expression] = []
    if isinstance(step, Filter) and step.condition is not None:
        exprs.append(step.condition)
    if isinstance(step, Projection):
        exprs.extend(step.projections)
    if isinstance(step, Aggregate):
        exprs.extend(step.group)
        exprs.extend(step.aggregations)
    if isinstance(step, Join):
        if step.condition is not None:
            exprs.append(step.condition)
        for left, right in step.on_keys:
            exprs.extend((left, right))
    if isinstance(step, Sort):
        exprs.extend(step.key)
    if isinstance(step, Window):
        exprs.extend(step.window_exprs)
    return exprs


def test_sqlglot_identifier_helpers() -> None:
    """Call sites use sqlglot's builders, not local wrappers."""
    assert isinstance(exp.to_identifier("foo"), exp.Identifier)
    assert isinstance(exp.column("a", table="t"), exp.Column)
    assert isinstance(exp.table_("t"), exp.Table)
    assert exp.to_identifier(exp.column("a").this).name == "a"


def test_scalar_subquery_ref_renders_inside_supported_dialects() -> None:
    expression = exp.Cast(
        this=ScalarSubqueryRef(this=exp.to_identifier("sq0")),
        to=exp.DataType.build("FLOAT"),
    )

    assert expression.sql(dialect="sqlite") == "CAST(sq0 AS REAL)"
    assert expression.sql(dialect="mysql") == "CAST(sq0 AS FLOAT)"
    assert expression.sql(dialect="postgres") == "CAST(sq0 AS REAL)"


def test_strftime_kept_as_sqlite_builtin_stub() -> None:
    """SQLite emit keeps STRFTIME; DF plans it via the predefined stub."""
    sql = _prepare(
        "SELECT strftime('%Y', d), strftime('%m', d) FROM t",
        "sqlite",
    )
    assert "STRFTIME" in sql.upper()
    assert "TO_CHAR" not in sql.upper()
    assert "%Y" in sql and "%m" in sql

    session = DataFusionSessionManager("sqlite")
    session.execute_ddl("CREATE TABLE t(d TEXT)")
    session.context.sql(sql).optimized_logical_plan()


def test_to_datafusion_sql_mysql_interval_emit() -> None:
    sql = _prepare(
        "SELECT d + INTERVAL 1 DAY FROM t",
        "mysql",
    )
    # DF mysql parser wants INTERVAL '1' DAY, not postgres INTERVAL '1 DAY'.
    assert "INTERVAL '1' DAY" in sql
    assert "INTERVAL '1 DAY'" not in sql


def test_session_context_uses_parser_dialect() -> None:
    assert DataFusionSessionManager.parser_dialect("mysql") == "mysql"
    assert DataFusionSessionManager.parser_dialect("sqlite") == "sqlite"
    session = DataFusionSessionManager("mysql")
    # Backticks are accepted under the mysql parser dialect.
    plan = session.context.sql(
        "SELECT `x` FROM (VALUES (1)) AS t(`x`)"
    ).optimized_logical_plan()
    assert plan is not None


def test_mysql_ddl_uses_backticks_not_double_quotes() -> None:
    session = DataFusionSessionManager("mysql")
    # execute_ddl sanitizes DATETIME→TIMESTAMP and keeps mysql backtick quoting.
    session.execute_ddl("CREATE TABLE t(`Name` INT, d DATETIME)")
    # Round-trip: information_schema / describe via a simple select plan.
    plan = session.context.sql("SELECT `Name` FROM t").optimized_logical_plan()
    assert plan is not None


def test_rewrite_sum_boolean_predicate() -> None:
    sql = _prepare(
        "SELECT SUM(event_date = min_date + 1) FROM activity",
        "mysql",
    )
    upper = sql.upper()
    # Predicate must become a numeric 0/1 CASE (not IF/IIF — DF has no IIF).
    assert "SUM(" in upper
    assert "CASE" in upper
    assert "IIF(" not in upper


def test_rewrite_sqlite_literal_truthiness_in_boolean_context() -> None:
    false_zero = _prepare("SELECT a FROM t WHERE a = 1 OR '0'", "sqlite").upper()
    false_text = _prepare("SELECT a FROM t WHERE a = 1 OR '+-'", "sqlite").upper()
    true_one = _prepare("SELECT a FROM t WHERE a = 1 OR '1'", "sqlite").upper()

    assert " OR FALSE" in false_zero
    assert " OR FALSE" in false_text
    assert " OR TRUE" in true_one


def test_rewrite_sqlite_boolean_arithmetic_to_numeric_case() -> None:
    sql = _prepare("SELECT a * (b = 1) FROM t", "sqlite")
    upper = sql.upper()

    assert "CASE" in upper
    assert "THEN 1" in upper
    assert "ELSE 0" in upper
    assert "* (B = 1)" not in upper


def test_explain_like_utf8view_lowers() -> None:
    """CAST/LIKE plans may use Arrow Utf8View; lowerer must not raise."""
    plan = explain(
        "CREATE TABLE t(a TEXT)",
        "SELECT a FROM t WHERE a LIKE 'x%'",
        "sqlite",
    )
    assert "Filter" in _types(plan)


def test_explain_sqlite_iif_plans() -> None:
    plan = explain(
        "CREATE TABLE t(a INT, b INT)",
        "SELECT IIF(AVG(a) > AVG(b), 'y', 'n') FROM t",
        "sqlite",
    )
    assert "Aggregate" in _types(plan) or "Projection" in _types(plan)


def test_explain_sum_boolean_predicate_plans() -> None:
    plan = explain(
        "CREATE TABLE t(a TEXT)",
        "SELECT SUM(a = 'x') FROM t",
        "sqlite",
    )
    assert "Aggregate" in _types(plan)


def test_explain_scalar_subquery_with_join_alias_filter_plans() -> None:
    plan = explain(
        DDL_TOXICOLOGY,
        "SELECT CAST((SELECT COUNT(T1.atom_id) "
        "FROM connected AS T1 "
        "INNER JOIN bond AS T2 ON T1.bond_id = T2.bond_id "
        "GROUP BY T2.bond_type "
        "ORDER BY COUNT(T2.bond_id) DESC LIMIT 1) AS REAL) "
        "* 100 / (SELECT COUNT(atom_id) FROM connected)",
        "sqlite",
    )

    assert "Projection" in _types(plan)


def test_explain_nested_in_with_scalar_subquery_plans() -> None:
    plan = explain(
        "CREATE TABLE t(a INT, b INT); CREATE TABLE u(a INT); CREATE TABLE v(x INT);",
        "SELECT a FROM t "
        "WHERE a IN (SELECT u.a FROM u WHERE u.a IN (SELECT MAX(x) FROM v)) "
        "AND b = (SELECT COUNT(*) FROM v)",
        "sqlite",
    )

    assert "Filter" in _types(plan)
    assert plan.scalar_subqueries


def test_explain_sqlite_boolean_arithmetic_plans() -> None:
    plan = explain(
        "CREATE TABLE t(a INT, b INT)",
        "SELECT a * (b = 1) FROM t",
        "sqlite",
    )

    assert "Projection" in _types(plan)


def test_explain_sqlite_string_literal_boolean_context_plans() -> None:
    for literal in ("'0'", "'+-'", "'1'"):
        plan = explain(
            "CREATE TABLE t(a INT)",
            f"SELECT a FROM t WHERE a = 1 OR {literal}",
            "sqlite",
        )

        assert "TableScan" in _types(plan)


def test_explain_julianday_subtraction_plans() -> None:
    plan = explain(
        "CREATE TABLE t(d TEXT)",
        "SELECT CAST((JULIANDAY('now') - JULIANDAY(d)) AS REAL) / 365 FROM t",
        "sqlite",
    )
    assert "Projection" in _types(plan) or "TableScan" in _types(plan)


def test_explain_source_iif_in_sum_plans() -> None:
    plan = explain(
        "CREATE TABLE t(a TEXT, b INT)",
        "SELECT SUM(IIF(a = 'x', 1, 0)) FROM t",
        "sqlite",
    )
    assert "Aggregate" in _types(plan)


def test_explain_strftime_year_diff_plans() -> None:
    plan = explain(
        "CREATE TABLE t(d TEXT)",
        "SELECT STRFTIME('%Y', CURRENT_TIMESTAMP) - STRFTIME('%Y', d) FROM t",
        "sqlite",
    )
    assert "Projection" in _types(plan) or "TableScan" in _types(plan)


def test_explain_strftime_lowers_to_time_to_str() -> None:
    plan = explain(
        "CREATE TABLE t(d DATE)",
        "SELECT strftime('%Y', d) FROM t WHERE strftime('%Y', d) > '1991'",
        "sqlite",
    )
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    assert any(isinstance(p, exp.TimeToStr) or p.find(exp.TimeToStr) for p in proj.projections)
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert filt.condition is not None
    tts = filt.condition.find(exp.TimeToStr)
    assert tts is not None
    assert not isinstance(tts.this, exp.Cast), tts
    assert isinstance(tts.this, exp.Column)
    assert not any(
        isinstance(node, exp.Anonymous) and str(node.name).upper() == "STRFTIME"
        for node in filt.condition.find_all(exp.Anonymous)
    )
    assert_no_bare_string_identifiers(plan)


def test_rewrite_iif_emits_case_not_iif() -> None:
    sql = _prepare("SELECT IIF(a > 1, 'y', 'n') FROM t", "sqlite")
    assert "CASE" in sql.upper()
    assert "IIF(" not in sql.upper()


def test_rewrite_tuple_in_to_exists() -> None:
    sql = _prepare(
        "SELECT * FROM activity AS a1 "
        "WHERE (a1.player_id, a1.event_date) IN ("
        "  SELECT player_id, MIN(event_date) FROM activity GROUP BY player_id"
        ")",
        "mysql",
    )
    upper = sql.upper()
    assert "EXISTS" in upper
    assert "TOO MANY COLUMNS" not in upper
    # Should not keep multi-column IN (..., ...) IN (SELECT ..., ...)
    assert "IN (" not in upper or upper.count(" IN ") == 0 or "EXISTS" in upper


def test_rewrite_group_by_adds_select_columns() -> None:
    sql = _prepare(
        "SELECT a, b, COUNT(*) FROM t GROUP BY a",
        "mysql",
    )
    upper = sql.upper().replace(" ", "")
    assert "GROUPBYA,B" in upper or "GROUPBYA,B," in upper or (
        "GROUP BY" in sql.upper() and "B" in sql.upper().split("GROUP BY", 1)[1]
    )


def test_rewrite_passes_plan_under_datafusion() -> None:
    ddl = """
    CREATE TABLE activity(player_id INT, event_date DATE, min_date DATE, b INT);
    CREATE TABLE t(a INT, b INT);
    """
    session = DataFusionSessionManager("mysql")
    session.execute_ddl(ddl)

    queries = [
        "SELECT SUM(event_date = min_date) FROM activity",
        "SELECT * FROM activity AS a1 WHERE (a1.player_id, a1.event_date) IN "
        "(SELECT player_id, MIN(event_date) FROM activity GROUP BY player_id)",
        "SELECT * FROM t WHERE (a = 1) AND b",
    ]
    for q in queries:
        df_sql = session.prepare_query(q)
        session.context.sql(df_sql).optimized_logical_plan()


def test_register_scalar_udf_plans() -> None:
    import pyarrow as pa

    session = DataFusionSessionManager("sqlite")
    session.execute_ddl("CREATE TABLE t(a INT)")

    def dbl(arr: pa.Array) -> pa.Array:
        return pa.array(
            [None if not v.is_valid else v.as_py() * 2 for v in arr],
            type=pa.int64(),
        )

    session.register_scalar_udf("dbl", dbl, [pa.int64()], pa.int64())
    df_sql = session.prepare_query("SELECT dbl(a) FROM t")
    session.context.sql(df_sql).optimized_logical_plan()


def test_explain_uses_provided_session_with_udf() -> None:
    import pyarrow as pa

    session = DataFusionSessionManager("sqlite")

    def dbl(arr: pa.Array) -> pa.Array:
        return pa.array(
            [None if not v.is_valid else v.as_py() * 2 for v in arr],
            type=pa.int64(),
        )

    session.register_scalar_udf("dbl", dbl, [pa.int64()], pa.int64())
    plan = explain(
        "CREATE TABLE t(a INT)",
        "SELECT dbl(a) FROM t",
        session=session,
    )
    assert plan.dialect == "sqlite"
    assert "TableScan" in _types(plan)


def test_predefined_planning_udfs_registered_by_default() -> None:
    """Bird/LeetCode dialect gaps are stubbed at session construction."""
    from parseval.plan.udf import PREDEFINED_PLANNING_UDFS

    names = {(name, arity) for name, arity, _ret in PREDEFINED_PLANNING_UDFS}
    assert ("julianday", 1) in names
    assert ("datediff", 2) in names
    assert ("locate", 2) in names
    assert ("iif", 3) in names
    assert ("iif", 3) in names

    sqlite = DataFusionSessionManager("sqlite")
    sqlite.execute_ddl("CREATE TABLE t(d TEXT, a REAL)")
    for q in (
        "SELECT julianday(d) FROM t",
        "SELECT datetime(d) FROM t",
        "SELECT total(a) FROM t",
    ):
        df_sql = sqlite.prepare_query(q)
        sqlite.context.sql(df_sql).optimized_logical_plan()

    mysql = DataFusionSessionManager("mysql")
    mysql.execute_ddl("CREATE TABLE t(d DATE, b TEXT, a DOUBLE)")
    for q in (
        "SELECT DATEDIFF(d, d), YEAR(d), ADDDATE(d, 1) FROM t",
        "SELECT INSTR(b, 'x') FROM t",  # emits LOCATE
        "SELECT FORMAT(a, 2), TRUNCATE(a, 1) FROM t",
    ):
        df_sql = mysql.prepare_query(q)
        mysql.context.sql(df_sql).optimized_logical_plan()


def test_filter_scan_tree() -> None:
    plan = explain(DDL_T, "SELECT a FROM t WHERE a > 1 AND b = 'x'", "sqlite")
    assert "Filter" in _types(plan)
    assert "TableScan" in _types(plan)
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert isinstance(filt.condition, exp.And)
    assert isinstance(filt.condition.this, exp.GT)
    assert isinstance(filt.condition.expression, exp.EQ)
    # Structural literals — not DF display wrappers like Int32(1) / Utf8("x")
    lit = filt.condition.this.expression
    assert isinstance(lit, exp.Literal)
    assert not lit.is_string
    assert str(lit.this) == "1"
    str_lit = filt.condition.expression.expression
    assert isinstance(str_lit, exp.Literal) and str_lit.is_string
    assert str_lit.this == "x"
    scan = next(s for s in plan.dag if isinstance(s, TableScan))
    assert isinstance(scan.table, exp.Table)
    assert scan.table.name == "t"
    assert isinstance(scan.name, exp.Identifier)
    assert_no_bare_string_identifiers(plan)


def test_aggregate_group_by() -> None:
    plan = explain(
        DDL_T,
        "SELECT a, COUNT(*) FROM t WHERE a > 1 GROUP BY a",
        "sqlite",
    )
    assert "Aggregate" in _types(plan)
    agg = next(s for s in plan.dag if isinstance(s, Aggregate))
    assert agg.group
    assert isinstance(agg.group, list)
    assert all(isinstance(g, exp.Expression) for g in agg.group)
    assert any(isinstance(a, exp.Count) for a in agg.aggregations)
    assert_no_bare_string_identifiers(plan)


def test_join_on_condition() -> None:
    plan = explain(DDL_TU, "SELECT t.a FROM t JOIN u ON t.a = u.a", "sqlite")
    assert "Join" in _types(plan)
    join = next(s for s in plan.dag if isinstance(s, Join))
    assert join.left is not None
    assert join.right is not None
    assert join.join_type.upper() == "INNER"
    assert join.on_keys
    assert all(
        isinstance(left, exp.Expression) and isinstance(right, exp.Expression)
        for left, right in join.on_keys
    )
    assert_no_bare_string_identifiers(plan)


def test_distinct_as_aggregate() -> None:
    """DataFusion lowers SELECT DISTINCT to Aggregate(groupBy=...)."""
    plan = explain(DDL_T, "SELECT DISTINCT a FROM t", "sqlite")
    assert "Aggregate" in _types(plan) or "Distinct" in _types(plan)
    assert_no_bare_string_identifiers(plan)


def test_window_step() -> None:
    plan = explain(
        DDL_T,
        "SELECT a, ROW_NUMBER() OVER (PARTITION BY b ORDER BY a) AS rn FROM t",
        "sqlite",
    )
    assert "Window" in _types(plan)
    win = next(s for s in plan.dag if isinstance(s, Window))
    assert win.window_exprs
    window = win.window_exprs[0]
    assert isinstance(window, exp.Window)
    assert isinstance(window.this, exp.RowNumber)
    assert window.args.get("partition_by")
    assert_no_bare_string_identifiers(plan)


@pytest.mark.parametrize(
    ("function_sql", "function_name"),
    (("RANK()", "rank"), ("DENSE_RANK()", "dense_rank")),
)
def test_rank_window_functions_use_anonymous_expressions(
    function_sql: str,
    function_name: str,
) -> None:
    plan = explain(
        DDL_T,
        f"SELECT a, {function_sql} OVER (PARTITION BY b ORDER BY a) AS position FROM t",
        "sqlite",
    )

    win = next(s for s in plan.dag if isinstance(s, Window))
    window = win.window_exprs[0]
    assert isinstance(window, exp.Window)
    assert isinstance(window.this, exp.Anonymous)
    assert str(window.this.this).lower() == function_name
    assert window.args.get("partition_by")
    assert_no_bare_string_identifiers(plan)


def test_structural_projection_cast_and_scalar() -> None:
    plan = explain(DDL_T, "SELECT CAST(a AS DOUBLE), abs(a) FROM t", "sqlite")
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    assert len(proj.projections) == 2
    assert isinstance(proj.projections[0], exp.Cast)
    assert isinstance(proj.projections[1], exp.Anonymous)
    assert str(proj.projections[1].this).lower() == "abs"
    assert not any(isinstance(e, exp.Var) for e in proj.projections)
    assert proj.projections[0].type == exp.DataType.build("FLOAT")
    assert proj.projections[1].type == exp.DataType.build("INT")
    assert_no_bare_string_identifiers(plan)


def test_schema_types_on_scan_filter_and_literals() -> None:
    plan = explain(DDL_T, "SELECT a FROM t WHERE a > 1 AND b = 'x'", "sqlite")
    scan = next(s for s in plan.dag if isinstance(s, TableScan))
    assert scan.scan_projections[0].type == exp.DataType.build("INT")
    assert scan.scan_projections[0].meta.get("nullable") is True
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert filt.condition is not None
    assert filt.condition.type == exp.DataType.build("BOOLEAN")
    lit = filt.condition.this.expression
    assert lit.type == exp.DataType.build("INT")
    assert_no_bare_string_identifiers(plan)


def test_date32_literal() -> None:
    plan = explain(
        "CREATE TABLE t(d DATE)",
        "SELECT * FROM t WHERE d = '2000-01-01'",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    lit = filt.condition.expression
    assert isinstance(lit, exp.Literal) and lit.is_string
    assert lit.this == "2000-01-01"
    assert lit.type == exp.DataType.build("DATE")


def test_cast_literal_annotation_propagates_to_inner_literal() -> None:
    literal = exp.Literal.string("2000-01-01")
    cast = exp.Cast(this=literal, to=exp.DataType.build("DATE"))

    _annotate_type(cast, "Date32")

    assert cast.type == exp.DataType.build("DATE")
    assert literal.type == exp.DataType.build("DATE")


def test_utf8view_null_literal_in_case() -> None:
    """DF encodes ELSE NULL on a string CASE as Utf8View(NULL)."""
    plan = explain(
        "CREATE TABLE t(a TEXT, b TEXT)",
        "SELECT COUNT(DISTINCT CASE WHEN a = 'c' THEN b ELSE NULL END) FROM t",
        "sqlite",
    )
    agg = next(s for s in plan.dag if isinstance(s, Aggregate))
    case = next(agg.aggregations[0].find_all(exp.Case))
    assert case.args.get("default") is not None
    assert isinstance(case.args["default"], exp.Null)
    assert case.args["default"].type == exp.DataType.build("TEXT")
    assert_no_bare_string_identifiers(plan)


def test_in_list_predicate() -> None:
    plan = explain(
        "CREATE TABLE t(a TEXT)",
        "SELECT a FROM t WHERE a NOT IN ('a','b','c','d','e','f','g','h','i','j')",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert isinstance(filt.condition, exp.Not)
    assert isinstance(filt.condition.this, exp.In)
    assert len(filt.condition.this.expressions) == 10


def test_scalar_subquery_in_filter() -> None:
    plan = explain(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT);",
        "SELECT a FROM t WHERE a = (SELECT MAX(b) FROM u)",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert isinstance(filt.condition, exp.EQ)
    ref = filt.condition.expression
    assert isinstance(ref, ScalarSubqueryRef)
    assert ref.subquery_id == "sq0"
    assert isinstance(plan.scalar_subqueries[ref.subquery_id], Aggregate)
    assert_no_bare_string_identifiers(plan)


def test_scalar_subquery_in_projection() -> None:
    plan = explain(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT);",
        "SELECT (SELECT MAX(b) FROM u) AS x FROM t",
        "sqlite",
    )
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    assert any(e.find(ScalarSubqueryRef) for e in proj.projections)
    assert "sq0" in plan.scalar_subqueries
    assert_no_bare_string_identifiers(plan)


def test_two_scalar_subqueries() -> None:
    plan = explain(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT);",
        "SELECT (SELECT MAX(b) FROM u) + (SELECT MIN(b) FROM u) FROM t",
        "sqlite",
    )
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    refs = [n for e in proj.projections for n in e.find_all(ScalarSubqueryRef)]
    assert [ref.subquery_id for ref in refs] == ["sq0", "sq1"]
    assert set(plan.scalar_subqueries) == {"sq0", "sq1"}
    assert_no_bare_string_identifiers(plan)


def test_scalar_subquery_in_projection_and_filter() -> None:
    plan = explain(
        "CREATE TABLE t(id INT, district_id INT);"
        "CREATE TABLE u(district_id INT, score INT);"
        "CREATE TABLE v(district_id INT);",
        "SELECT (SELECT MAX(score) FROM u) FROM t "
        "JOIN v ON t.district_id = v.district_id "
        "WHERE v.district_id = (SELECT district_id FROM v LIMIT 1)",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert filt.condition.find(ScalarSubqueryRef) is not None
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    assert any(e.find(ScalarSubqueryRef) for e in proj.projections)
    refs = [ref.subquery_id for e in proj.projections for ref in e.find_all(ScalarSubqueryRef)]
    refs.extend(ref.subquery_id for ref in filt.condition.find_all(ScalarSubqueryRef))
    assert set(refs) <= set(plan.scalar_subqueries)
    assert len(refs) == 2
    assert_no_bare_string_identifiers(plan)


def test_scalar_subquery_in_complex_predicate() -> None:
    plan = explain(
        "CREATE TABLE t(id INT, sex TEXT); CREATE TABLE u(id INT, ua REAL, dt TEXT);",
        "SELECT AVG(u.ua) FROM t JOIN u ON t.id = u.id "
        "WHERE (u.ua > 6.5 AND t.sex = 'F') OR (u.ua > 8.0 AND t.sex = 'M') "
        "AND u.dt = (SELECT MAX(dt) FROM u)",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert filt.condition.find(ScalarSubqueryRef) is not None
    assert_no_bare_string_identifiers(plan)


def test_scalar_subquery_refs_are_valid_sqlglot_leaves() -> None:
    plan = explain(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT);",
        "SELECT a FROM t WHERE a = (SELECT MAX(b) FROM u)",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    assert filt.condition is not None

    assert filt.condition.sql()
    copied = filt.condition.copy()
    assert copied.find(ScalarSubqueryRef) is not None
    transformed = filt.condition.transform(lambda node: node)
    assert transformed.find(ScalarSubqueryRef) is not None
    assert list(filt.condition.find_all(ScalarSubqueryRef))
    assert not list(filt.condition.find_all(exp.Subquery))
    assert all(
        not isinstance(subquery.this, (Step, str))
        for step in plan.dag
        for expr in _step_expressions(step)
        for subquery in expr.find_all(exp.Subquery)
    )


def test_timestamp_schema_field_params() -> None:
    plan = explain(
        "CREATE TABLE users(id INT, CreationDate TIMESTAMP, DisplayName TEXT)",
        "SELECT DisplayName FROM users WHERE STRFTIME('%Y', CreationDate) = '2014'",
        "sqlite",
    )
    assert "TableScan" in _types(plan)
    assert_no_bare_string_identifiers(plan)


def test_cast_type_from_expr_types() -> None:
    plan = explain(DDL_T, "SELECT CAST(a AS DOUBLE) AS c FROM t", "sqlite")
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    cast = next(e for e in proj.projections if isinstance(e, exp.Cast) or e.find(exp.Cast))
    node = cast if isinstance(cast, exp.Cast) else cast.find(exp.Cast)
    assert node is not None
    assert node.type == exp.DataType.build("FLOAT")

def test_window_expr_type() -> None:
    plan = explain(
        DDL_T,
        "SELECT a, ROW_NUMBER() OVER (PARTITION BY b ORDER BY a) AS rn FROM t",
        "sqlite",
    )
    win = next(s for s in plan.dag if isinstance(s, Window))
    assert win.window_exprs[0].type == exp.DataType(this=exp.DataType.Type.UBIGINT)
    assert_no_bare_string_identifiers(plan)


def test_values_and_subquery_alias() -> None:
    plan = explain("", "SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS v(x, y)", "sqlite")
    assert "Values" in _types(plan)
    assert "SubqueryAlias" in _types(plan)
    values = next(s for s in plan.dag if isinstance(s, Values))
    assert values.values
    assert all(isinstance(cell, exp.Literal) for row in values.values for cell in row)
    assert values.values[0][1].is_string and values.values[0][1].this == "a"
    alias = next(s for s in plan.dag if isinstance(s, SubqueryAlias))
    assert isinstance(alias.alias, exp.Identifier)
    assert alias.alias.name == "v"
    assert_no_bare_string_identifiers(plan)


def test_subquery_alias_from_derived_table() -> None:
    plan = explain(DDL_T, "SELECT * FROM (SELECT a FROM t) AS s", "sqlite")
    assert "SubqueryAlias" in _types(plan)
    alias = next(s for s in plan.dag if isinstance(s, SubqueryAlias))
    assert isinstance(alias.alias, exp.Identifier)
    assert alias.alias.name == "s"
    assert_no_bare_string_identifiers(plan)


def test_union_all() -> None:
    plan = explain(DDL_T, "SELECT a FROM t UNION ALL SELECT a FROM t", "sqlite")
    assert "Union" in _types(plan)
    union = next(s for s in plan.dag if isinstance(s, Union))
    assert union.is_all is True
    assert_no_bare_string_identifiers(plan)


def test_sort_and_limit() -> None:
    plan = explain(DDL_T, "SELECT a FROM t ORDER BY a LIMIT 5", "sqlite")
    assert "Sort" in _types(plan)
    sort = next(s for s in plan.dag if isinstance(s, Sort))
    assert sort.key
    assert all(isinstance(k, exp.Expression) for k in sort.key)
    assert sort.fetch == 5

    plan2 = explain(DDL_T, "SELECT a FROM t LIMIT 10 OFFSET 2", "sqlite")
    assert "Limit" in _types(plan2)
    limit = next(s for s in plan2.dag if isinstance(s, Limit))
    assert limit.fetch == 10
    assert limit.offset == 2
    assert_no_bare_string_identifiers(plan)
    assert_no_bare_string_identifiers(plan2)


def test_multi_create_ddl() -> None:
    plan = explain(DDL_TU, "SELECT t.a, u.a FROM t, u WHERE t.a = u.a", "sqlite")
    scans = [s for s in plan.dag if isinstance(s, TableScan)]
    names = {s.table.name for s in scans}
    assert names >= {"t", "u"}
    assert_no_bare_string_identifiers(plan)


def test_unmapped_variant_raises() -> None:
    class _FakeVariant:
        pass

    class _FakePlan:
        def to_variant(self):
            return _FakeVariant()

        def display(self):
            return "FakeOp: hello"

        def display_indent_schema(self):
            return "FakeOp: hello []"

        def inputs(self):
            return []

    with pytest.raises(PlanError, match="unsupported DataFusion logical plan variant"):
        from datafusion import SessionContext

        _from_logical(_FakePlan(), ctx=SessionContext(), dialect="sqlite")


def test_identifier_invariant_rejects_bare_str() -> None:
    plan = explain(DDL_T, "SELECT a FROM t", "sqlite")
    scan = next(s for s in plan.dag if isinstance(s, TableScan))
    scan.name = "oops"  # type: ignore[assignment]
    with pytest.raises(AssertionError, match="bare str"):
        assert_no_bare_string_identifiers(plan)


def test_repr_step_and_expr() -> None:
    plan = explain(
        "CREATE TABLE t(a INT); CREATE TABLE u(b INT);",
        "SELECT (SELECT MAX(b) FROM u) AS hi, a FROM t WHERE a = (SELECT MIN(b) FROM u)",
        "sqlite",
    )
    filt = next(s for s in plan.dag if isinstance(s, Filter))
    proj = next(s for s in plan.dag if isinstance(s, Projection))

    plan_repr = repr(plan)
    assert plan_repr.startswith("Plan(")
    assert "Projection(" in plan_repr
    assert "Filter(" in plan_repr
    assert "TableScan(" in plan_repr

    ref = filt.condition.expression
    assert isinstance(ref, ScalarSubqueryRef)
    subq_repr = repr_expr(ref)
    assert subq_repr.startswith("ScalarSubqueryRef(")

    inner_repr = repr_step(plan.scalar_subqueries[ref.subquery_id])
    assert inner_repr.startswith("Aggregate(")
    assert "TableScan(" in inner_repr

    subq_proj = next(e.find(ScalarSubqueryRef) for e in proj.projections if e.find(ScalarSubqueryRef))
    subq_proj_repr = repr_expr(subq_proj)
    assert subq_proj_repr.startswith("ScalarSubqueryRef(")
    assert "Max(" in repr_step(plan.scalar_subqueries[subq_proj.subquery_id])


def test_interval_literal_day() -> None:
    plan = explain(DDL_T, "SELECT INTERVAL '1' DAY AS d", "mysql")
    assert list(plan.dag)
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    interval = proj.projections[0]
    sql = interval.sql(dialect="mysql")
    assert "INTERVAL" in sql.upper()
    assert "1" in sql


def test_interval_literal_month() -> None:
    plan = explain(DDL_T, "SELECT INTERVAL '1' MONTH AS d", "mysql")
    assert list(plan.dag)
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    interval = proj.projections[0]
    sql = interval.sql(dialect="mysql")
    assert "INTERVAL" in sql.upper()
    assert "1" in sql


def test_interval_literal_hour() -> None:
    plan = explain(DDL_T, "SELECT INTERVAL '2' HOUR AS d", "mysql")
    assert list(plan.dag)
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    interval = proj.projections[0]
    sql = interval.sql(dialect="mysql")
    assert "INTERVAL" in sql.upper()
    assert "2" in sql


def test_interval_literal_year() -> None:
    plan = explain(DDL_T, "SELECT INTERVAL '1' YEAR AS d", "mysql")
    assert list(plan.dag)
    proj = next(s for s in plan.dag if isinstance(s, Projection))
    interval = proj.projections[0]
    sql = interval.sql(dialect="mysql")
    assert "INTERVAL" in sql.upper()
    assert "12" in sql


def test_interval_literal_with_nonzero_seconds() -> None:
    plan = explain(DDL_T, "SELECT INTERVAL '1.5' SECOND AS d", "mysql")
    assert list(plan.dag)
