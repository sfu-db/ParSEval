from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.evaluator import PlanEvaluator
from parseval.symbolic.types import BranchTree, BranchType, CoverageThresholds


def _evaluate(sql: str, schema: str, rows):
    instance = Instance(schema, name="observations", dialect="sqlite")
    table = next(iter(instance.tables))
    for row in rows:
        instance.create_row(table, values=row)
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = PlanEvaluator(plan, instance, "sqlite").evaluate(
        BranchTree(thresholds=CoverageThresholds())
    )
    return tree


def test_project_observations_are_per_output_ordinal():
    tree = _evaluate(
        "SELECT a, b, a FROM t",
        "CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT);",
        [
            {"id": 1, "a": None, "b": 1},
            {"id": 2, "a": 2, "b": None},
        ],
    )

    node = next(node for node in tree.nodes if node.site == "project_output")

    assert len(node.atoms) == 3
    assert node.atoms[0].sql() == node.atoms[2].sql()
    assert set(node.observations) == {0, 1, 2}
    assert node.observed_outcomes(0) == {
        BranchType.PROJECT_NULL,
        BranchType.PROJECT_NON_NULL,
    }
    assert node.observed_outcomes(1) == {
        BranchType.PROJECT_NULL,
        BranchType.PROJECT_NON_NULL,
    }


def test_aggregate_observations_include_distinct_input_behavior():
    tree = _evaluate(
        "SELECT SUM(x) AS total, COUNT(x) AS count_x, "
        "COUNT(DISTINCT x) AS distinct_count, "
        "SUM(DISTINCT x) AS distinct_sum FROM t",
        "CREATE TABLE t (id INT PRIMARY KEY, x INT);",
        [
            {"id": 1, "x": None},
            {"id": 2, "x": 2},
            {"id": 3, "x": 2},
            {"id": 4, "x": 3},
        ],
    )

    aggregate = next(node for node in tree.nodes if node.site == "aggregate_output")
    distinct = next(
        node for node in tree.nodes if node.site == "aggregate_distinct_input"
    )

    for atom_id in range(4):
        assert BranchType.AGGREGATE_NON_NULL in aggregate.observed_outcomes(atom_id)
    for atom_id in range(2):
        outcomes = distinct.observed_outcomes(atom_id)
        assert BranchType.AGG_DISTINCT_NULL_IGNORED in outcomes
        assert BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED in outcomes
        assert BranchType.AGG_DISTINCT_MULTIPLE_RETAINED in outcomes


def test_all_null_aggregate_outputs_distinguish_count():
    tree = _evaluate(
        "SELECT SUM(x), AVG(x), MIN(x), MAX(x), COUNT(x) FROM t",
        "CREATE TABLE t (id INT PRIMARY KEY, x INT);",
        [{"id": 1, "x": None}],
    )

    node = next(node for node in tree.nodes if node.site == "aggregate_output")

    for atom_id in range(4):
        assert BranchType.AGGREGATE_NULL in node.observed_outcomes(atom_id)
    assert node.observed_outcomes(4) == {BranchType.AGGREGATE_NON_NULL}


def test_group_count_and_size_observations_are_separate():
    tree = _evaluate(
        "SELECT x, COUNT(*) FROM t GROUP BY x",
        "CREATE TABLE t (id INT PRIMARY KEY, x INT);",
        [
            {"id": 1, "x": 1},
            {"id": 2, "x": 1},
            {"id": 3, "x": 2},
        ],
    )

    node = next(node for node in tree.nodes if node.site == "group")

    assert BranchType.GROUP_MULTI in node.observed_outcomes(0)
    assert BranchType.GROUP_SINGLE in node.observed_outcomes(0)
    assert node.observed_outcomes(1) == {BranchType.GROUP_MULTI}


def test_aggregate_input_observations_include_null_and_duplicate():
    tree = _evaluate(
        "SELECT SUM(x) FROM t",
        "CREATE TABLE t (id INT PRIMARY KEY, x INT);",
        [
            {"id": 1, "x": None},
            {"id": 2, "x": 2},
            {"id": 3, "x": 2},
        ],
    )

    node = next(node for node in tree.nodes if node.site == "aggregate_input")

    assert BranchType.AGGREGATE_NULL in node.observed_outcomes(0)
    assert BranchType.DUPLICATE in node.observed_outcomes(0)


def test_inner_join_observes_left_and_right_unmatched_rows():
    instance = Instance(
        """
        CREATE TABLE left_t (id INT PRIMARY KEY, k INT);
        CREATE TABLE right_t (id INT PRIMARY KEY, k INT);
        """,
        name="join_observations",
        dialect="sqlite",
    )
    instance.create_row("left_t", values={"id": 1, "k": 10})
    instance.create_row("left_t", values={"id": 2, "k": 20})
    instance.create_row("right_t", values={"id": 1, "k": 10})
    instance.create_row("right_t", values={"id": 2, "k": 30})
    sql = "SELECT left_t.id FROM left_t INNER JOIN right_t ON left_t.k = right_t.k"
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = PlanEvaluator(plan, instance, "sqlite").evaluate(
        BranchTree(thresholds=CoverageThresholds(atom_null=0))
    )

    node = next(node for node in tree.nodes if node.site == "join_on")

    assert BranchType.JOIN_MATCH in node.observed_outcomes(-1)
    assert BranchType.JOIN_LEFT in node.observed_outcomes(-2)
    assert BranchType.JOIN_RIGHT in node.observed_outcomes(-3)
