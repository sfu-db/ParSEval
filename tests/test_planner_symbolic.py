from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope
from parseval.constants import PBit, StepType
from parseval.data_generator import DataGenerator
from parseval.db_manager import DBManager
from parseval.instance import Instance
from parseval.main import instantiate_db
from parseval.plan import SymbolicScopeEncoder, build_context_from_instance
from parseval.query import preprocess_sql
from parseval.uexpr.uexprs import UExprToConstraint
import tempfile


SCHEMA = "CREATE TABLE users (id INT PRIMARY KEY, age INT, name TEXT);"


def _encode(sql: str, seed_rows):
    instance = Instance(ddls=SCHEMA, name="planner_symbolic", dialect="sqlite")
    for row in seed_rows:
        instance.create_row("users", row)
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    tracer = UExprToConstraint()
    context = build_context_from_instance(instance)
    for scope_id, scope in enumerate(traverse_scope(expr)):
        encoder = SymbolicScopeEncoder(
            ctx=context,
            scope=scope,
            scope_id=scope_id,
            tracer=tracer,
            dialect="sqlite",
        )
        encoder.encode()
    return tracer


def test_case_projection_is_encoded_as_branch_predicates():
    tracer = _encode(
        "SELECT CASE WHEN age > 18 THEN 1 ELSE 0 END AS flag FROM users",
        [{"id": 1, "age": 20, "name": "amy"}],
    )

    assert len(tracer.leaves) == 2
    assert tracer.get_positive_patterns() == [(PBit.TRUE, PBit.TRUE)]

    positive_leaf = tracer.leaves[(PBit.TRUE, PBit.TRUE)]
    negative_leaf = tracer.leaves[(PBit.TRUE, PBit.FALSE)]

    assert positive_leaf.parent.step_type == StepType.PROJECT
    assert positive_leaf.parent.sql_condition.sql() == '"users"."age" > 18'
    assert positive_leaf.parent.coverage[PBit.TRUE]
    assert negative_leaf.parent.sql_condition.sql() == '"users"."age" > 18'


def test_having_branch_is_encoded_after_groupby():
    tracer = _encode(
        "SELECT name FROM users GROUP BY name HAVING COUNT(id) > 1",
        [
            {"id": 1, "age": 20, "name": "amy"},
            {"id": 2, "age": 30, "name": "amy"},
        ],
    )

    having_nodes = [
        leaf.parent
        for leaf in tracer.leaves.values()
        if leaf.parent.step_type == StepType.HAVING
    ]

    assert having_nodes
    assert any(node.sql_condition.sql() == 'COUNT("users"."id") > 1' for node in having_nodes)


def test_intersect_set_operation_generates_rows_under_smt_only():
    schema = "CREATE TABLE a (id INT, v INT); CREATE TABLE b (id INT, v INT);"
    query = "SELECT v FROM a INTERSECT SELECT v FROM b"

    with tempfile.TemporaryDirectory() as tmpdir:
        instantiate_db(
            query=query,
            schema=schema,
            host_or_path=tmpdir,
            db_id="planner_intersect",
            dialect="sqlite",
            global_timeout=8,
            query_timeout=5,
            allow_speculative_fallback=False,
        )
        with DBManager().get_connection(
            host_or_path=tmpdir,
            database="planner_intersect.sqlite",
            dialect="sqlite",
        ) as conn:
            rows = conn.execute(query, fetch="all", timeout=5)

    assert rows


def test_aggregate_replacement_supports_distinct_expression_operands():
    instance = Instance(ddls=SCHEMA, name="aggregate_expr", dialect="sqlite")
    instance.create_row("users", {"id": 1, "age": 5, "name": "amy"})
    instance.create_row("users", {"id": 2, "age": 7, "name": "amy"})
    expr = preprocess_sql(
        "SELECT name FROM users GROUP BY name "
        "HAVING COUNT(DISTINCT CASE WHEN age > 0 THEN 1 ELSE 0 END) = 1 "
        "AND SUM(age + 1) = 14",
        instance,
        dialect="sqlite",
    )
    generator = DataGenerator(expr=expr, instance=instance, verbose=False)
    condition = expr.args["having"].this
    aggregates = list(condition.find_all(exp.AggFunc))

    replacements = [
        generator.operator_rules._aggregate_replacement(
            agg_func=aggregate, group_rows=instance.get_rows("users")
        )
        for aggregate in aggregates
    ]

    assert replacements[0].args["concrete"] == 1
    assert replacements[1].args["concrete"] == 14
