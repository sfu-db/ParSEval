from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver.types import solver_var
from parseval.symbolic.constraints import ConstraintGenerator
from parseval.symbolic.engine import SymbolicEngine
from parseval.symbolic.evaluator import build_branch_tree
from parseval.symbolic.types import BranchType


SCHEMA = "CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT);"


def _compile(sql: str, site: str, outcome: BranchType, atom_id: int = 0):
    instance = Instance(SCHEMA, name="generation", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == site
        and target.atom_id == atom_id
        and target.target_outcome == outcome
    )
    return ConstraintGenerator(plan, instance).generate(target)


def _assert_physical_storage(constraint):
    variables = {
        solver_var(column)
        for expression in constraint.constraints
        for column in expression.find_all(exp.Column)
    }
    variables.discard(None)
    assert variables
    for variable in variables:
        assert variable.relation_id is not None
        assert variable in constraint.storage_relations
        assert constraint.storage_relations[variable].scope_id is None


def test_project_null_target_constrains_the_output_expression():
    constraint = _compile(
        "SELECT a + 1 AS shifted FROM t",
        "project_output",
        BranchType.PROJECT_NULL,
    )

    null_constraint = next(item for item in constraint.constraints if isinstance(item, exp.Is))
    assert isinstance(null_constraint.expression, exp.Null)
    column = next(null_constraint.find_all(exp.Column))
    assert solver_var(column) is not None
    _assert_physical_storage(constraint)


def test_aggregate_null_target_constrains_the_aggregate_input():
    constraint = _compile(
        "SELECT SUM(a) AS total FROM t",
        "aggregate_output",
        BranchType.AGGREGATE_NULL,
    )

    null_constraint = next(item for item in constraint.constraints if isinstance(item, exp.Is))
    assert isinstance(null_constraint.this, exp.Column)
    assert isinstance(null_constraint.expression, exp.Null)
    assert solver_var(null_constraint.this) is not None
    _assert_physical_storage(constraint)


def test_distinct_aggregate_duplicate_target_uses_two_non_null_rows():
    constraint = _compile(
        "SELECT COUNT(DISTINCT b) FROM t",
        "aggregate_distinct_input",
        BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED,
    )

    equality = next(item for item in constraint.constraints if isinstance(item, exp.EQ))
    scopes = {solver_var(column).row_scope for column in equality.find_all(exp.Column)}
    assert scopes == {"r0", "r1"}
    non_null = [
        item
        for item in constraint.constraints
        if isinstance(item, exp.Is) and isinstance(item.expression, exp.Not)
    ]
    assert len(non_null) >= 2
    _assert_physical_storage(constraint)


def test_distinct_aggregate_multiple_target_uses_unequal_rows():
    constraint = _compile(
        "SELECT SUM(DISTINCT b) FROM t",
        "aggregate_distinct_input",
        BranchType.AGG_DISTINCT_MULTIPLE_RETAINED,
    )

    inequality = next(item for item in constraint.constraints if isinstance(item, exp.NEQ))
    scopes = {solver_var(column).row_scope for column in inequality.find_all(exp.Column)}
    assert scopes == {"r0", "r1"}
    _assert_physical_storage(constraint)


def test_engine_covers_nullable_project_outputs_with_valid_primary_keys():
    instance = Instance(
        "CREATE TABLE items (id INT PRIMARY KEY NOT NULL, nullable_text TEXT);",
        name="project_engine",
        dialect="sqlite",
    )
    result = SymbolicEngine(
        instance,
        "SELECT nullable_text, id FROM items",
        dialect="sqlite",
        max_iterations=20,
    ).generate()

    rows = instance.get_rows("items")
    values = [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in rows
    ]
    assert result.coverage == 1.0
    assert {value["nullable_text"] is None for value in values} == {True, False}
    assert all(value["id"] is not None for value in values)


def test_engine_covers_grouped_distinct_aggregate_inputs():
    instance = Instance(
        "CREATE TABLE items (id INT PRIMARY KEY NOT NULL, category TEXT, amount INT);",
        name="aggregate_engine",
        dialect="sqlite",
    )
    result = SymbolicEngine(
        instance,
        "SELECT category, SUM(DISTINCT amount), COUNT(DISTINCT amount) "
        "FROM items GROUP BY category",
        dialect="sqlite",
        max_iterations=30,
        max_rows_per_table=20,
    ).generate()

    rows = instance.get_rows("items")
    values = [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in rows
    ]
    distinct_node = next(
        node for node in result.tree.nodes if node.site == "aggregate_distinct_input"
    )
    for atom_id in range(2):
        assert distinct_node.observed_outcomes(atom_id) >= {
            BranchType.AGG_DISTINCT_NULL_IGNORED,
            BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED,
            BranchType.AGG_DISTINCT_MULTIPLE_RETAINED,
        }
    assert result.coverage == 1.0
    assert all(value["id"] is not None for value in values)
