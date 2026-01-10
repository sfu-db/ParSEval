from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, TYPE_CHECKING
from src.parseval.plan.rex import *
from src.parseval.plan.helper import to_identifier, to_columnref, to_type
from src.parseval.states import SchemaException, SyntaxException
import json
from src.parseval.calcite import get_logical_plan


def parse_literal(planner, **node):
    value = node.pop("value")
    datatype = to_type(node.pop("type"))
    if datatype.is_type(*DataType.NUMERIC_TYPES):
        literal = sqlglot_exp.Literal.number(value)
    else:
        literal = sqlglot_exp.Literal.string(value)
    literal.set("datatype", datatype)
    return literal


def parse_aggfunc(planner, klass, **node):
    operands = node.get("operands", [])
    if operands:
        first_op = operands[0]
        col_ref = to_columnref(
            f"${first_op['column']}", first_op.get("type"), index=first_op["column"]
        )
    else:
        col_ref = sqlglot_exp.Star()

    return klass(
        this=col_ref,
        distinct=node.get("distinct", False),
        ignorenulls=node.get("ignorenulls", False),
        datatype=to_type(node.get("type")),
    )


def parse_case(planner, **node):
    operands = node.pop("operands")
    default = planner.walk(operands.pop())
    whens = []

    for index in range(0, len(operands), 2):
        when = planner.walk(operands[index])
        then = planner.walk(operands[index + 1])
        whens.append(sqlglot_exp.If(this=when, true=then))
    return sqlglot_exp.Case(ifs=whens, default=default)


def parse_scalary_query(planner, **kwargs) -> Expression:

    query = [planner.walk(q) for q in kwargs.pop("query")]

    subquery_type = kwargs.pop("operator")[1:].lower()

    return ScalarQuery(
        this=query[0],
        type=subquery_type,
        correlated=False,
        datatype=to_type(kwargs.pop("type")),
    )


def parse_exists(planner, **kwargs) -> Expression:
    subquery_predicate = [planner.walk(q) for q in kwargs.pop("query")]

    datatype = to_type(kwargs.pop("type"))
    exists = sqlglot_exp.Exists(this=subquery_predicate[0], datatype=datatype)

    return exists


def parse_field_access(planner, **kwargs) -> Expression:

    datatype = to_type(kwargs.pop("type"))
    field_name = to_identifier(kwargs.pop("name"))

    return FieldAccess(
        this=field_name,
        column=kwargs.pop("column"),
        datatype=datatype,
    )


def parse_in(planner, **kwargs) -> Expression:
    operands = [planner.walk(op) for op in kwargs.pop("operands", [])]
    query = [planner.walk(q) for q in kwargs.pop("query", [])]
    datatype = to_type(kwargs.pop("type"))
    if query:
        return sqlglot_exp.In(this=operands[0], query=query[0], datatype=datatype)
    # sqlglot_exp.In(
    #     this=operands[0], expressions=operands[1:], subquery=query[0], datatype=datatype
    # )


class Planner:

    EXPRESSION_TRANSFORM = {
        "INPUT_REF": lambda planner, **kwargs: to_columnref(
            kwargs.pop("name"), kwargs.pop("type"), index=kwargs.pop("index"), **kwargs
        ),
        "IS_NULL": lambda planner, **kwargs: Is_Null(
            this=planner.walk(kwargs.pop("operands").pop())
        ),
        "LITERAL": lambda planner, **kwargs: parse_literal(planner, **kwargs),
        "CASE": lambda planner, **kwargs: parse_case(planner, **kwargs),
        "SCALAR_QUERY": lambda planner, **kwargs: parse_scalary_query(
            planner, **kwargs
        ),
        "NOT": lambda planner, **kwargs: negate_predicate(
            planner.walk(kwargs.pop("operands").pop())
        ),
        "CAST": lambda planner, **kwargs: sqlglot_exp.Cast(
            this=planner.walk(kwargs.pop("operands").pop()),
            to=to_type(kwargs.pop("type")),
        ),
        "FIELD_ACCESS": lambda planner, **kwargs: parse_field_access(planner, **kwargs),
        "EXISTS": lambda planner, **kwargs: parse_exists(planner, **kwargs),
        "IN": lambda planner, **kwargs: parse_in(planner, **kwargs),
        "OTHER_FUNCTION": lambda planner, **kwargs: FunctionCall(
            this=kwargs.pop("operator"),
            expressions=[planner.walk(op) for op in kwargs.pop("operands", [])],
        ),
        "COUNT": lambda planner, **kwargs: parse_aggfunc(
            planner, sqlglot_exp.Count, **kwargs
        ),
        "SUM": lambda planner, **kwargs: parse_aggfunc(
            planner, sqlglot_exp.Sum, **kwargs
        ),
        "AVG": lambda planner, **kwargs: parse_aggfunc(
            planner, sqlglot_exp.Avg, **kwargs
        ),
        "MAX": lambda planner, **kwargs: parse_aggfunc(
            planner, sqlglot_exp.Max, **kwargs
        ),
        "MIN": lambda planner, **kwargs: parse_aggfunc(
            planner, sqlglot_exp.Min, **kwargs
        ),
        "LOGICALTABLESCAN": lambda planner, **kwargs: planner.parse_scan(**kwargs),
        "LOGICALPROJECT": lambda planner, **kwargs: planner.parse_project(**kwargs),
        "LOGICALFILTER": lambda planner, **kwargs: planner.parse_filter(**kwargs),
        "LOGICALJOIN": lambda planner, **kwargs: planner.parse_join(**kwargs),
        "LOGICALAGGREGATE": lambda planner, **kwargs: planner.parse_aggregate(**kwargs),
        "LOGICALSORT": lambda planner, **kwargs: planner.parse_sort(**kwargs),
    }

    def explain(self, schema: str, sql: str, dialect: str = "sqlite"):
        res = get_logical_plan(ddls=schema, queries=[sql], dialect=dialect)
        src = json.loads(res)[0]
        if src["state"] != "SUCCESS":
            raise SyntaxException(f"Plan Error: {src.get('error')}")

        plan_json = json.loads(src["plan"])
        return self.walk(plan_json)

    def walk(self, node):
        """
        The Visitor Dispatcher.
        Determines if the node is an Operator (relOp) or Expression (kind/operator).
        """
        handler = None
        # 1. Check if it is a Relational Operator
        if "relOp" in node:
            key = node["relOp"].upper()
            handler = self.EXPRESSION_TRANSFORM[key]

        # 2. Check if it is an Expression (kind or operator)
        elif "kind" in node or "operator" in node:
            # Calcite sometimes puts the type in 'kind', sometimes in 'operator'
            key = (node.get("kind") or node.get("operator")).upper()
            handler = self.EXPRESSION_TRANSFORM[key]

        if handler:
            return handler(self, **node)
        raise SyntaxError(f"Unknown node type: {node}")

    def parse_scan(self, **node):
        table_name = to_identifier(node.get("table"))
        operator_id = node.get("id")
        columns = [
            self.walk(
                {
                    "kind": "INPUT_REF",
                    "index": index,
                    **col,
                    "table": table_name.name,
                    "alias": f"T{operator_id}",
                }
            )
            for index, col in enumerate(node.get("columns", []))
        ]
        return LogicalScan(
            this=table_name, operator_id=operator_id, expressions=columns
        )

    def parse_project(self, **node):
        child = self.walk(node.get("inputs")[0])
        expressions = [self.walk(project) for project in node.pop("project", [])]
        operator_id = node.pop("id")
        return LogicalProject(
            this=child, expressions=expressions, operator_id=operator_id
        )

    def parse_filter(self, **node):
        child = self.walk(node.get("inputs")[0])
        condition = self.walk(node.get("condition"))
        operator_id = node.get("id")
        variableset = node.get("variableset")
        klass = (
            LogicalFilter if not isinstance(child, LogicalAggregate) else LogicalHaving
        )
        return klass(
            this=child,
            condition=condition,
            operator_id=operator_id,
            variableset=variableset,
        )

    def parse_join(self, **node):
        inputs = node.get("inputs", [])
        children = [self.walk(child) for child in inputs]
        condition = self.walk(node.get("condition"))
        join_type = node.get("joinType", "INNER").upper()
        return LogicalJoin(
            this=children[0],
            expression=children[1],
            join_type=join_type,
            condition=condition,
            operator_id=node.get("id"),
        )

    def parse_aggregate(self, **node):
        child = self.walk(node.pop("inputs")[0])
        groupby = []
        for gid, key in enumerate(node.pop("keys")):
            groupby.append(
                to_columnref(
                    name=f"${gid}", datatype=key.get("type"), index=key.get("column")
                )
            )
        agg_funcs = [self.walk(func_def) for func_def in node.get("aggs", [])]
        return LogicalAggregate(
            this=child, expressions=groupby, aggs=agg_funcs, operator_id=node.get("id")
        )

    def parse_sort(self, **node):
        child = self.walk(node.get("inputs")[0])
        sort = node.get("sort", [])
        return LogicalSort(
            this=child,
            expressions=[
                to_columnref(
                    name=str(s["column"]), datatype=s["type"], index=s["column"]
                )
                for s in sort
            ],
            dirs=node.get("dir", []),
            offset=node.get("offset", 0),
            limit=node.get("limit", 1),
            operator_id=node.get("id"),
        )

    def parse_union(self, **node):
        children = [self.walk(child) for child in node.get("inputs", [])]
        return klass(
            this=children[0], expression=children[1], operator_id=node.pop("id")
        )

    def parse_intersect(self, **node): ...
    def parse_except(self, **node): ...


BINARY_OPERATORS = {
    "EQUALS": sqlglot_exp.EQ,
    "NOT_EQUALS": sqlglot_exp.NEQ,
    "GREATER_THAN": sqlglot_exp.GT,
    "LESS_THAN": sqlglot_exp.LT,
    "LESS_THAN_OR_EQUAL": sqlglot_exp.LTE,
    "GREATER_THAN_OR_EQUAL": sqlglot_exp.GTE,
    "LIKE": sqlglot_exp.Like,
    "AND": sqlglot_exp.And,
    "OR": sqlglot_exp.Or,
    "PLUS": sqlglot_exp.Add,
    "MINUS": sqlglot_exp.Sub,
    "TIMES": sqlglot_exp.Mul,
    "DIVIDE": sqlglot_exp.Div,
}

for kind, op_class in BINARY_OPERATORS.items():
    Planner.EXPRESSION_TRANSFORM[kind] = (
        lambda planner, op_class=op_class, **kwargs: reduce(
            lambda x, y: op_class(this=x, expression=y),
            [planner.walk(operand) for operand in kwargs.pop("operands", [])],
        )
    )
