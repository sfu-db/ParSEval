from __future__ import annotations
from abc import abstractmethod
from functools import reduce
from sqlglot import exp as sqlglot_exp, expressions
from sqlglot import generator
from src.parseval.dtype import DataType
from typing import TYPE_CHECKING, List, Optional, Dict, Any

if TYPE_CHECKING:
    from src.parseval.dtype import DATATYPE

Expression = sqlglot_exp.Expression


class ColumnRef(sqlglot_exp.Expression):
    """
    Column Reference Expression in Logical plan.
    Args:
        this: Column name (Identifier)
        table: Table name (str)
        ref: Column index in the schema (int)
        datatype: Data type of the column (DataType)
    """

    arg_types = {
        "this": True,
        "table": False,
        "ref": False,
        "datatype": False,
        "join_mark": False,
    }

    @property
    def ref(self) -> int:
        return self.args.get("ref", 0)

    @property
    def table(self) -> str:
        return self.text("table")

    @property
    def datatype(self) -> DATATYPE:
        return DataType.build(self.args.get("datatype", "UNKNOWN"))

    @property
    def qualified_name(self) -> str:
        """Get fully qualified column name"""
        return f"{self.table}.{self.name}" if self.table else self.name

    def __str__(self):
        return self.name
        return super().sql(dialect, **opts)

    def __repr__(self):
        return self.name

    def sql(self, dialect=None, **opts):
        return f"{self.name}"


class Is_Null(sqlglot_exp.Unary, sqlglot_exp.Predicate):

    def sql(self, dialect=None, **opts):
        return f"{self.this.sql(dialect=dialect, **opts)} IS NULL"


class Is_Not_Null(sqlglot_exp.Unary, sqlglot_exp.Predicate):
    def sql(self, dialect=None, **opts):
        return f"{self.this.sql(dialect=dialect, **opts)} IS NOT NULL"


class FunctionCall(sqlglot_exp.Func):
    arg_types = {"this": True, "expressions": True}

    def params(self) -> List[sqlglot_exp.Expression]:
        return self.expressions

    def sql(self, dialect=None, **opts):
        args_sql = ", ".join(
            [expr.sql(dialect=dialect, **opts) for expr in self.expressions]
        )
        return f"{self.name}({args_sql})"


class Strftime(FunctionCall):
    arg_types = {"this": True, "expressions": True, "datatype": True}

    @property
    def fmt(self) -> sqlglot_exp.Expression:
        return self.expressions[1]

    @property
    def operand(self) -> sqlglot_exp.Expression:
        return self.this


class ABS(FunctionCall):
    arg_types = {"this": True, "datatype": True}

    @property
    def operand(self) -> Expression:
        return self.this


class Schema(Expression):
    arg_types = {"this": False, "expressions": True}

    @property
    def columns(self) -> List[sqlglot_exp.Expression]:
        return self.expressions

    def column_names(self) -> List[str]:
        """Get list of column names"""
        return [col.name for col in self.columns]


class Table(Expression):
    arg_types = {
        "this": True,
        "schema": False,
        "constraints": False,
        "primary_key": False,
        "foreign_key": False,
    }

    @property
    def schema(self) -> Schema:
        return self.args.get("schema")

    @property
    def constraints(self):
        return self.args.get("constraints", {})

    @property
    def columns(self) -> List[ColumnRef]:
        if "_columns" in self.args:
            return self.args.get("_columns")

        columns = []
        for column in self.schema.columns:
            nullable = self.nullable(column.name)
            unique = self.is_unique(column.name)
            column.set("table", self.name)
            column.set("unique", unique)
            column.set("nullable", nullable)
            columns.append(column)
        self.set("_columns", columns)
        return columns

    def nullable(self, column_name):
        if self.args.get("primary_key"):
            for column_name in self.primary_key.find_all(sqlglot_exp.Identifier):
                if column_name.this.name == column_name:
                    return False
        for constraint in self.constraints.get(column_name, []):

            if isinstance(constraint.kind, sqlglot_exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
        return True

    def is_unique(self, column_name):
        for constraint in self.constraints.get(column_name, []):
            if isinstance(
                constraint.kind,
                (
                    sqlglot_exp.UniqueColumnConstraint,
                    sqlglot_exp.PrimaryKeyColumnConstraint,
                ),
            ):
                return True
        if self.args.get("primary_key"):
            for column_name in self.primary_key.find_all(sqlglot_exp.Identifier):
                if column_name.this.name == column_name:
                    return True
        return False


class Catalog(Expression):
    arg_types = {"tables": False}

    @property
    def tables(self) -> Dict[str, Table]:
        return self.args.get("tables", {})

    def add_table(self, table_info: Table):
        """Register a table in the catalog"""
        self.tables[table_info.name] = table_info

    def get_table(self, name: str) -> Optional[Table]:
        """Get table information by name"""
        return self.tables.get(name)


class LogicalOperator(sqlglot_exp.Expression):
    """
    Represents a single step in a REX (Relational EXpression) plan.
    """

    @property
    def operator_id(self) -> str:
        return self.args.get("operator_id", "")

    @property
    def operator_type(self) -> str:
        return self.key[7:].capitalize()

    @abstractmethod
    def schema(self, catalog):
        """
        Returns the schema of the output produced by this operator.
        """
        pass

    def sql(self, dialect=None, **opts):
        indent = opts.get("indent", 0)
        pad = "  " * indent
        lines = [f"{pad}{repr(self)}"]
        for child in self.children:
            opts["indent"] = indent + 1
            lines.append(child.sql(dialect=dialect, **opts))
        return "\n".join(lines)


class LeafOperator(LogicalOperator):
    """Base class for operators with no children (leaf nodes)"""

    @property
    def children(self) -> List[LogicalOperator]:
        return []


class UnaryOperator(LogicalOperator):
    """Base class for operators with exactly one child"""

    arg_types = {"this": True}

    @property
    def children(self) -> List[LogicalOperator]:
        return [self.this]

    def schema(self, catalog):
        if "_schema" in self.args:
            return self.args.get("_schema")
        scm = self.this.schema(catalog)
        self.set("_schema", scm)
        return scm


class BinaryOperator(LogicalOperator):
    """Base class for operators with exactly two children"""

    @property
    def left(self):
        return self.this

    @property
    def right(self):
        return self.expression

    @property
    def children(self) -> List[LogicalOperator]:
        return [self.left, self.right]


class LogicalScan(LeafOperator):
    """
    Represents a logical scan operation in a REX plan.
    """

    arg_types = {
        "this": True,
        "operator_id": True,
    }

    @property
    def table_name(self) -> str:
        return self.text("this")

    def __repr__(self) -> str:
        return f"{self.operator_type}(table={self.table_name})"

    def schema(self, catalog):

        if "_schema" in self.args:
            return self.args.get("_schema")
        table = catalog.get_table(self.table_name)
        columns = []
        for index, col in enumerate(table.columns):
            unique = table.is_unique(col.name)
            nullable = table.nullable(col.name)
            datatype = col.datatype
            datatype.nullable = nullable
            columns.append(
                ColumnRef(
                    this=sqlglot_exp.to_identifier(col.name),
                    table=self.table_name,
                    datatype=datatype,
                    ref=index,
                    unique=unique,
                )
            )
        scm = Schema(expressions=columns)
        self.set("_schema", scm)
        return scm


class LogicalProject(UnaryOperator):
    """
    Represents a logical projection operation in a REX plan.
    """

    arg_types = {
        "this": True,
        "expressions": True,
        "operator_id": True,
    }

    def __repr__(self) -> str:
        exprs = ", ".join(f"{expr}" for expr in self.expressions)
        return f"{self.operator_type}({exprs}, id={self.operator_id[:8]})"

    def schema(self, catalog):
        if "_schema" in self.args:
            return self.args.get("_schema")

        input_schema = self.this.schema(catalog=catalog)
        columns = []
        for expr in self.expressions:
            new_expr = expr.transform(resolve_schema, input_schema=input_schema)
            columns.append(new_expr)
        scm = Schema(expressions=columns)
        self.set("_schema", scm)
        return self.args.get("_schema")


class LogicalFilter(UnaryOperator):
    """
    Represents a logical filter operation in a REX plan.
    """

    arg_types = {
        "this": True,
        "condition": True,
        "operator_id": True,
    }

    @property
    def condition(self) -> Expression:
        return self.args.get("condition")

    def __repr__(self) -> str:
        return f"{self.operator_type}(condition={self.condition})"


class LogicalHaving(LogicalFilter):
    pass


class LogicalSort(UnaryOperator):
    arg_types = {
        "this": True,
        "expressions": True,
        "dirs": True,
        "offset": True,
        "limit": True,
        "operator_id": True,
    }

    @property
    def sorts(self) -> List[sqlglot_exp.Expression]:
        return self.expressions

    @property
    def offset(self) -> int:
        return self.args.get("offset", 0)

    @property
    def limit(self) -> Optional[int]:
        return self.args.get("limit", None)

    @property
    def dirs(self) -> List:
        return self.args.get("dirs", [])

    def __repr__(self):
        return f"{self.operator_type}({', '.join([str(s) for s in self.sorts])}, dir={self.dirs}, offset={self.offset}, limit={self.limit})"


class LogicalAggregate(UnaryOperator):
    arg_types = {
        "this": True,
        "expressions": True,
        "aggs": True,
        "operator_id": True,
    }

    @property
    def keys(self) -> List[sqlglot_exp.Expression]:
        return self.expressions

    @property
    def aggs(self) -> List[sqlglot_exp.Expression]:
        return self.args.get("aggs", [])

    def __repr__(self):
        keys = ", ".join([str(k) for k in self.keys])
        agg_funcs = ", ".join([str(a) for a in self.aggs])
        return f"{self.operator_type}(keys=[{keys}], aggs=[{agg_funcs}]"

    def schema(self, catalog):
        if "_schema" in self.args:
            return self.args.get("_schema")

        input_schema = self.this.schema(catalog)
        columns = []
        for key in self.keys:
            columns.append(input_schema.columns[key.ref])
        for agg_expr in self.aggs:
            agg = agg_expr.transform(resolve_schema, input_schema=input_schema)
            columns.append(agg)

        scm = Schema(expressions=columns)
        self.set("_schema", scm)
        return scm


class LogicalJoin(BinaryOperator):
    arg_types = {
        "this": True,
        "expression": True,
        "join_type": True,
        "condition": True,
        "operator_id": True,
    }

    @property
    def join_type(self) -> str:
        return self.args.get("join_type")

    @property
    def condition(self) -> Optional[Expression]:
        return self.args.get("condition", None)

    def __repr__(self) -> str:
        return f"{self.operator_type}(condition= {self.condition}, type={self.join_type}, id={self.operator_id[:8]})"

    def schema(self, catalog):
        if "_schema" in self.args:
            return self.args.get("_schema")

        scm = Schema(
            expressions=[column for column in self.left.schema(catalog).columns]
            + [column for column in self.right.schema(catalog).columns]
        )
        self.set("_schema", scm)
        return self.args.get("_schema")


class LogicalUnion(BinaryOperator):
    arg_types = {
        "this": True,
        "expression": True,
        "union_all": True,
        "operator_id": True,
    }

    @property
    def union_all(self) -> bool:
        return self.args.get("union_all", False)

    def __repr__(self) -> str:
        return f"{self.operator_type}(all={self.all})"

    def schema(self, catalog):
        if "_schema" in self.args:
            return self.args.get("_schema")
        scm = self.left.schema(catalog)
        self.set("_schema", scm)
        return scm


class LogicalIntersect(LogicalUnion):
    pass


class LogicalDifference(LogicalUnion):
    pass


def resolve_schema(expr, input_schema: Schema):
    """
    Resolve a ColumnRef expression to its corresponding schema entry.

    Args:
        expr (sql_exp.Expression): The expression to check.
        input_schema: The schema of children used to retrieve schema info.

    Returns:
        The resolved schema column if applicable, otherwise None.
    """
    if isinstance(expr, ColumnRef):
        return input_schema.columns[expr.ref]
    return expr


class Planner:
    EXPRESSION_HANDLERS = {
        "INPUT_REF": lambda planner, **kwargs: ColumnRef(
            this=sqlglot_exp.to_identifier(kwargs.pop("name")),
            datatype=DataType(this=kwargs.pop("type", "UNKNOWN")),
            ref=kwargs.pop("index"),
        ),
        "CAST": lambda self, **kwargs: sqlglot_exp.Cast(
            this=self.walk(kwargs.pop("operands").pop()),
            to=DataType.build(kwargs.pop("type")),
        ),
        "SUBSTR": lambda self, **kwargs: sqlglot_exp.Substring(
            this=self.walk(kwargs["operands"].pop()),
            start=self.walk(kwargs["operands"].pop()),
            length=(
                self.walk(kwargs["operands"].pop())
                if kwargs.get("operands", None)
                else sqlglot_exp.Literal.number(-1)
            ),
        ),
        "STRFTIME": lambda self, **kwargs: Strftime(
            this=self.walk(kwargs["operands"].pop()),
            expressions=[self.walk(kwargs["operands"].pop())],
            datatype=DataType.build(kwargs.pop("type")),
        ),
        "ABS": lambda self, **kwargs: ABS(
            this=self.walk(kwargs["operands"].pop()),
            datatype=DataType.build(kwargs.pop("type")),
        ),
        "IS_NULL": lambda self, **kwargs: Is_Null(
            this=self.walk(kwargs.pop("operands").pop())
        ),
        #     "INSTR": lambda args: sqlglot_exp.InStr(
        #         this=args[0], substring=args[1], start=args[2] if len(args) == 3 else None
        #     ),
        #     "UDATE": lambda args: sqlglot_exp.Date(this=args[0]),
        #     "||": lambda args: sqlglot_exp.Concat(expressions=args[0]),
        #     "LENGTH": lambda args: sqlglot_exp.Length(this=args[0]),
        #     "ABS": lambda args: sqlglot_exp.Abs(this=args[0]),
        #     "CURRENT_TIMESTAMP": lambda args: sqlglot_exp.CurrentTimestamp,
        #     "JULIANDAY": lambda args: Julianday(this=args[0]),
        #     "STRFTIME": lambda args: _build_strftime(args),
    }
    TRANSFORM_MAPPING = {
        "LogicalTableScan": lambda self, **kwargs: LogicalScan(
            this=sqlglot_exp.to_identifier(kwargs.pop("table")),
            operator_id=kwargs.pop("id", None),
        ),
        "LogicalProject": lambda self, **kwargs: self.on_project(**kwargs),
        "LogicalFilter": lambda self, **kwargs: self.on_filter(**kwargs),
        "LogicalJoin": lambda self, **kwargs: self.on_join(**kwargs),
        "LogicalAggregate": lambda self, **kwargs: self.on_aggregate(**kwargs),
        "LogicalSort": lambda self, **kwargs: self.on_sort(**kwargs),
        # "LogicalUnion": lambda self, **kwargs: LogicalUnion(
    }

    def __init__(self, expression_registry=None):
        self.expr_registry = expression_registry
        self.dispatches = {
            "relOp": lambda self, node: self.TRANSFORM_MAPPING[node["relOp"]](
                self, **node
            ),
            "kind": lambda self, node: self.EXPRESSION_HANDLERS[node["kind"]](
                self, **node
            ),
            "operator": lambda self, node: self.EXPRESSION_HANDLERS[node["operator"]](
                self, **node
            ),
        }

    def explain2(self, schema: str, plan_path: str, dialect: str = "postgres"):
        if isinstance(schema, str):
            schema = schema.split(";")
        import json

        with open(plan_path) as f:
            plan = json.load(f)

        return self.walk(plan)

    def walk(self, node):
        for key, func in self.dispatches.items():
            # and (
            #     node.get(key).upper() in self.EXPRESSION_HANDLERS
            #     or node.get(key) in self.TRANSFORM_MAPPING
            # )
            if key in node:
                return func(self, node)
        raise ValueError(f"Cannot find relOp or kind/operator in node: {node}")

    def on_project(self, **kwargs):
        child = self.walk(kwargs.pop("inputs")[0])
        expressions = [self.walk(proj) for proj in kwargs.pop("project", [])]
        operator_id = kwargs.pop("id", None)
        return LogicalProject(
            this=child, expressions=expressions, operator_id=operator_id
        )

    def on_filter(self, **kwargs):
        child = self.walk(kwargs.pop("inputs")[0])
        condition = self.walk(kwargs.pop("condition"))
        operator_id = kwargs.pop("id", None)
        if isinstance(child, LogicalAggregate):
            return LogicalHaving(
                this=child, condition=condition, operator_id=operator_id
            )
        return LogicalFilter(this=child, condition=condition, operator_id=operator_id)

    def on_join(self, **kwargs):
        children = [self.walk(child) for child in kwargs.pop("inputs")]
        condition = self.walk(kwargs.pop("condition"))
        join_type = kwargs.pop("joinType", "INNER").upper()
        return LogicalJoin(
            this=children[0],
            expression=children[1],
            join_type=join_type,
            condition=condition,
            operator_id=kwargs.pop("id", None),
        )

    def on_aggregate(self, **kwargs):
        child = self.walk(kwargs.pop("inputs")[0])
        groupby = []
        for gid, key in enumerate(kwargs.pop("keys")):
            groupby.append(
                ColumnRef(
                    this=sqlglot_exp.to_identifier(f"${gid}"),
                    ref=key.get("column"),
                    datatype=DataType.build(key.get("type")),
                )
            )

        aggs = kwargs.pop("aggs", [])
        agg_funcs = [self.walk(func_def) for func_def in aggs]

        return LogicalAggregate(this=child, expressions=groupby, aggs=agg_funcs)

    def on_union(self, **kwargs):
        pass

    def on_sort(self, **kwargs):
        this = self.walk(kwargs["inputs"][0])
        sort = kwargs.get("sort", [])
        return LogicalSort(
            this=this,
            expressions=[
                ColumnRef(
                    this=sqlglot_exp.to_identifier(str(s["column"])),
                    ref=s["column"],
                    datatype=DataType.build(s["type"]),
                )
                for s in sort
            ],
            dirs=kwargs.pop("dir", []),
            offset=kwargs.pop("offset", 0),
            limit=kwargs.pop("limit", 1),
            operator_id=kwargs.pop("id", None),
        )


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

UNARY_OPERATORS = {
    "NOT": sqlglot_exp.Not,
    # "IS_NULL": Is_Null,
}
AGG_FUNCS = {
    "COUNT": sqlglot_exp.Count,
    "SUM": sqlglot_exp.Sum,
    "AVG": sqlglot_exp.Avg,
    "MAX": sqlglot_exp.Max,
    "MIN": sqlglot_exp.Min,
}


def parse_literal(self, **kwargs):
    value = kwargs.pop("value")
    datatype = DataType.build(
        dtype=kwargs.pop("type", "UNKNOWN"),
        nullable=kwargs.pop("nullable"),
        precision=kwargs.pop("precision", None),
    )
    literal = None
    if datatype.is_type(*sqlglot_exp.DataType.NUMERIC_TYPES):
        literal = sqlglot_exp.Literal.number(value)
    else:
        literal = sqlglot_exp.Literal.string(value)
    literal.set("datatype", datatype)
    return literal


for func_name, func_class in AGG_FUNCS.items():
    Planner.EXPRESSION_HANDLERS[func_name] = (
        lambda self, func_class=func_class, **kwargs: func_class(
            this=(
                ColumnRef(
                    this=sqlglot_exp.to_identifier(
                        f'${kwargs["operands"][0]["column"]}'
                    ),
                    type=DataType.build(kwargs["operands"][0].get("type")),
                    ref=kwargs["operands"][0]["column"],
                )
                if kwargs.get("operands")
                else sqlglot_exp.Star()
            ),
            distinct=kwargs.get("distinct", False),
            ignorenulls=kwargs.get("ignorenulls", False),
            datatype=DataType.build(kwargs.get("type")),
        )
    )


for kind, op_class in BINARY_OPERATORS.items():
    Planner.EXPRESSION_HANDLERS[kind] = (
        lambda self, op_class=op_class, **kwargs: reduce(
            lambda x, y: op_class(this=x, expression=y),
            [self.walk(operand) for operand in kwargs.pop("operands", [])],
        )
    )

for kind, op_class in UNARY_OPERATORS.items():
    Planner.EXPRESSION_HANDLERS[kind] = (
        lambda self, op_class=op_class, **kwargs: negate_predicate(
            self.walk(kwargs.pop("operands").pop())
        )
    )


def parse_case(self, **kwargs) -> Expression:
    operands = kwargs.pop("operands")
    default = self.walk(operands.pop())
    whens = []

    for index in range(0, len(operands), 2):
        when = self.walk(operands[index])
        then = self.walk(operands[index + 1])
        whens.append(sqlglot_exp.If(this=when, true=then))
    return sqlglot_exp.Case(ifs=whens, default=default)


def parse_scalary_query(self, **kwargs) -> Expression:

    query = [self.walk(q) for q in kwargs.pop("query")]

    subquery_type = kwargs.pop("operator")[1:].lower()

    return sqlglot_exp.Subquery(this=query[0], type=subquery_type, correlated=False)


Planner.EXPRESSION_HANDLERS["SCALAR_QUERY"] = parse_scalary_query
Planner.EXPRESSION_HANDLERS["LITERAL"] = parse_literal
Planner.EXPRESSION_HANDLERS["CASE"] = parse_case

for klass in [
    ColumnRef,
    Is_Null,
    Is_Not_Null,
    LogicalOperator,
    LogicalAggregate,
    LogicalJoin,
    LogicalFilter,
    LogicalProject,
    LogicalScan,
    LogicalSort,
]:
    generator.Generator.TRANSFORMS[klass] = lambda self, expression: expression.sql(
        dialect=self.dialect
    )


def negate_predicate(expr) -> "Expression":
    """Return the negation of this expression."""

    from sqlglot.optimizer.simplify import simplify

    if expr.key == "is_null":
        return Is_Not_Null(this=expr.this)
    elif expr.key == "is_not_null":
        return Is_Null(this=expr.this)

    return simplify(expr.not_())
