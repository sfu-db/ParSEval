from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.branch_tree import build_branch_tree
from parseval.symbolic.types import BranchType, CoverageThresholds


SCHEMA = "CREATE TABLE t (id INT PRIMARY KEY, a INT, b INT);"


def _tree(sql: str, thresholds: CoverageThresholds | None = None):
    instance = Instance(SCHEMA, name="targets", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    return build_branch_tree(plan, instance, thresholds or CoverageThresholds())


def test_project_targets_are_per_output_ordinal():
    tree = _tree("SELECT a, b FROM t")

    targets = {
        (target.atom_id, target.target_outcome)
        for target in tree.uncovered_targets
        if target.node.site == "project_output"
    }

    assert targets == {
        (0, BranchType.PROJECT_NULL),
        (0, BranchType.PROJECT_NON_NULL),
        (1, BranchType.PROJECT_NULL),
        (1, BranchType.PROJECT_NON_NULL),
    }


def test_count_aggregate_has_no_null_result_target():
    tree = _tree("SELECT SUM(a) AS total, COUNT(DISTINCT b) AS count_b FROM t")

    targets = {
        (target.atom_id, target.target_outcome)
        for target in tree.uncovered_targets
        if target.node.site == "aggregate_output"
    }

    assert (0, BranchType.AGGREGATE_NULL) in targets
    assert (0, BranchType.AGGREGATE_NON_NULL) in targets
    assert (1, BranchType.AGGREGATE_NULL) not in targets
    assert (1, BranchType.AGGREGATE_NON_NULL) in targets


def test_new_thresholds_can_disable_individual_outcomes():
    thresholds = CoverageThresholds(
        project_null=0,
        aggregate_distinct_duplicate_eliminated=0,
    )
    project_tree = _tree("SELECT a FROM t", thresholds)
    aggregate_tree = _tree("SELECT COUNT(DISTINCT b) FROM t", thresholds)

    project_outcomes = {
        target.target_outcome
        for target in project_tree.uncovered_targets
        if target.node.site == "project_output"
    }
    distinct_outcomes = {
        target.target_outcome
        for target in aggregate_tree.uncovered_targets
        if target.node.site == "aggregate_distinct_input"
    }

    assert BranchType.PROJECT_NULL not in project_outcomes
    assert BranchType.PROJECT_NON_NULL in project_outcomes
    assert BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED not in distinct_outcomes
    assert BranchType.AGG_DISTINCT_NULL_IGNORED in distinct_outcomes
    assert BranchType.AGG_DISTINCT_MULTIPLE_RETAINED in distinct_outcomes


def test_case_group_aggregate_and_join_targets_are_obligations():
    case_tree = _tree("SELECT CASE WHEN a > 0 THEN b ELSE id END FROM t")
    case_targets = {
        (target.obligation.metric, target.target_outcome)
        for target in case_tree.uncovered_targets
        if target.node.site == "case_arm"
    }
    assert case_targets == {
        ("case_arm", BranchType.CASE_ARM_TAKEN),
        ("case_arm", BranchType.CASE_ARM_SKIPPED),
        ("case_positive", BranchType.CASE_ARM_TAKEN),
    }

    group_tree = _tree("SELECT a, COUNT(*) FROM t GROUP BY a")
    group_targets = {
        (target.obligation.metric, target.target_outcome)
        for target in group_tree.uncovered_targets
        if target.node.site == "group"
    }
    assert group_targets == {
        ("group_size", BranchType.GROUP_SINGLE),
        ("group_size", BranchType.GROUP_MULTI),
        ("group_count", BranchType.GROUP_SINGLE),
        ("group_count", BranchType.GROUP_MULTI),
    }

    aggregate_tree = _tree("SELECT SUM(a) FROM t")
    aggregate_targets = {
        (target.obligation.metric, target.target_outcome)
        for target in aggregate_tree.uncovered_targets
        if target.node.site == "aggregate_input"
    }
    assert aggregate_targets == {
        ("aggregate_input_null", BranchType.AGGREGATE_NULL),
        ("aggregate_input_duplicate", BranchType.DUPLICATE),
    }

    schema = """
    CREATE TABLE left_t (id INT PRIMARY KEY, k INT);
    CREATE TABLE right_t (id INT PRIMARY KEY, k INT);
    """
    instance = Instance(schema, name="targets", dialect="sqlite")
    sql = "SELECT left_t.id FROM left_t INNER JOIN right_t ON left_t.k = right_t.k"
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    join_tree = build_branch_tree(plan, instance, CoverageThresholds(atom_null=0))
    join_targets = {
        (target.obligation.metric, target.target_outcome)
        for target in join_tree.uncovered_targets
        if target.node.site == "join_on"
        and target.obligation is not None
        and target.obligation.metric.startswith("join_")
    }
    assert {
        ("join_match", BranchType.JOIN_MATCH),
        ("join_left_unmatched", BranchType.JOIN_LEFT),
        ("join_right_unmatched", BranchType.JOIN_RIGHT),
    } <= join_targets
