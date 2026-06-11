"""Unit tests for the Propagator class in speculate.py."""
import pytest
import sqlglot
from sqlglot import exp

from parseval.instance import Instance
from parseval.plan.planner import Plan, Scan, Filter, Join, Aggregate, Having, Project
from parseval.symbolic.speculate import Propagator, BranchSpec, SpeculateConfig


def _make_instance(tables: dict[str, dict[str, str]]) -> Instance:
    """Create a minimal Instance from a table->columns dict."""
    ddls = []
    for table_name, columns in tables.items():
        cols = []
        for col_name, col_type in columns.items():
            cols.append(f"{col_name} {col_type}")
        ddls.append(f"CREATE TABLE {table_name} ({', '.join(cols)})")
    return Instance(ddls="; ".join(ddls), name="test", dialect="sqlite")


def _make_plan(sql: str, instance: Instance) -> Plan:
    """Build a Plan from a SQL string with identity resolved."""
    expression = sqlglot.parse_one(sql, dialect="sqlite")
    plan = Plan(expression, instance=instance)
    return plan


def _propagate(sql: str, tables: dict[str, dict[str, str]],
               config: SpeculateConfig | None = None) -> list[BranchSpec]:
    """Run Propagator on a SQL string and return branch specs."""
    instance = _make_instance(tables)
    plan = _make_plan(sql, instance)
    propagator = Propagator(plan, instance, "sqlite", config=config)
    return propagator.propagate()


# ---------------------------------------------------------------------------
# Task 2: Positive branch propagation
# ---------------------------------------------------------------------------


def test_positive_simple_select():
    """Positive branch for SELECT with WHERE should produce one spec."""
    sql = "SELECT x.id, x.amount FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    assert len(specs) == 1
    assert specs[0].branch == "positive"
    assert any(tc.table == "orders" for tc in specs[0].requirements.values())
    orders_req = next(tc for tc in specs[0].requirements.values() if tc.table == "orders")
    assert len(orders_req.constraints) >= 1


def test_positive_requires_solver_var_on_columns():
    """Every Column in positive branch constraints must carry a solver var."""
    from parseval.solver.types import solver_var
    sql = "SELECT x.id FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    spec = specs[0]
    for tc in spec.requirements.values():
        for constraint in tc.constraints:
            for col in constraint.find_all(exp.Column):
                sv = solver_var(col)
                assert sv is not None, f"Column {col.sql()} lacks solver var"


# ---------------------------------------------------------------------------
# Task 3: Negative branch propagation
# ---------------------------------------------------------------------------


def test_negative_branch_negates_filter():
    """Negative branch should negate the WHERE condition."""
    sql = "SELECT x.id FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    config = SpeculateConfig(positive=0, negative=1, null=0,
                             left_unmatched=0, right_unmatched=0,
                             having_fail=0, case_else=0, boundary=0)
    specs = _propagate(sql, tables, config)
    assert len(specs) >= 1
    neg_spec = specs[0]
    assert neg_spec.branch.startswith("negative")
    # Check all requirements for the negated condition (may span
    # multiple entries for the same table with different aliases).
    has_negated = False
    for tc in neg_spec.requirements.values():
        if tc.table != "orders":
            continue
        for constraint in tc.constraints:
            if isinstance(constraint, (exp.LTE, exp.LT)):
                has_negated = True
    assert has_negated, "Expected negated condition (LTE/LT) in negative branch"


# ---------------------------------------------------------------------------
# Task 4: Join propagation
# ---------------------------------------------------------------------------


def test_join_creates_equivalence():
    """Join should link source_key and join_key via Union-Find."""
    sql = """
        SELECT o.id, c.name
        FROM orders AS o
        JOIN customers AS c ON o.customer_id = c.id
    """
    tables = {
        "orders": {"id": "INT", "customer_id": "INT", "amount": "REAL"},
        "customers": {"id": "INT", "name": "TEXT"},
    }
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    assert len(specs) == 1
    spec = specs[0]
    table_names = {tc.table for tc in spec.requirements.values()}
    assert "orders" in table_names
    assert "customers" in table_names
    groups = spec.equivalences.groups()
    assert len(groups) >= 1, "Expected at least one equivalence group from join"


# ---------------------------------------------------------------------------
# Task 5: HAVING / GROUP BY propagation
# ---------------------------------------------------------------------------


def test_having_sets_min_rows():
    """HAVING with COUNT should set min_rows on the table."""
    sql = """
        SELECT o.customer_id, COUNT(*) AS cnt
        FROM orders AS o
        GROUP BY o.customer_id
        HAVING COUNT(*) > 2
    """
    tables = {"orders": {"id": "INT", "customer_id": "INT", "amount": "REAL"}}
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    assert len(specs) == 1
    spec = specs[0]
    for tc in spec.requirements.values():
        if tc.table == "orders":
            assert tc.min_rows >= 3, f"Expected min_rows >= 3 for COUNT > 2, got {tc.min_rows}"
