from __future__ import annotations
from sqlglot.expressions import Identifier, to_identifier, Expression
from sqlglot import exp
from typing import Dict
import math, numbers, datetime
from parseval.dtype import DataType
from parseval.states import SyntaxException
from .rex import ColumnRef


def to_columnref(
    name: str | Identifier,
    datatype: str | DataType | Dict,
    index=None,
    **kwargs,
):
    """
    Converts a name and datatype into a ColumnRef object.

    Args:
        name (str | Identifier): The name of the column, either as a string or an Identifier object.
        datatype (str | DataType | Dict): The datatype of the column, which can be a string,
            a DataType object, or a dictionary defining the datatype.
        index (optional): The index or reference for the column. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the ColumnRef constructor.

    Returns:
        ColumnRef: The constructed ColumnRef object.

    Raises:
        SyntaxException: If the datatype is invalid.
    """
    if isinstance(name, ColumnRef):
        return name
    name = to_identifier(name)
    datatype = to_type(datatype)
    return ColumnRef(this=name, ref=index, datatype=datatype, **kwargs)


def to_type(type_def: str | DataType | dict) -> DataType:
    """
    Converts a type definition into a DataType object.

    Args:
        type_def (str | DataType | dict): The type definition, which can be a string,
            a DataType object, or a dictionary defining the datatype.

    Returns:
        DataType: The constructed DataType object.

    Raises:
        SyntaxException: If the type definition is invalid.
    """
    if isinstance(type_def, (DataType, str)):
        return DataType.build(type_def)
    elif isinstance(type_def, dict):
        if "name" in type_def:
            type_def["dtype"] = type_def.pop("name")
    else:
        raise SyntaxException(f"Invalid type definition: {type_def}")
    return DataType.build(**type_def)


def to_literal(
    value, datatype=None, copy=False
) -> (
    exp.Literal
    | exp.Null
    | exp.Boolean
    | exp.TimeStrToTime
    | exp.DateStrToDate
    | exp.Expression
):
    """
    Converts a value into a SQL expression literal, optionally with a specified datatype.

    Args:
        value: The value to convert, which can be of various types (e.g., str, bool, None, numbers, datetime).
        datatype (optional): The datatype to associate with the literal. Defaults to None.
    """
    concrete = None
    literal = None
    srctype = None
    if isinstance(value, exp.Expression):
        literal = exp.maybe_copy(value, copy)
        srctype = literal.type or literal.args.get("datatype")
        concrete = literal.this
    elif isinstance(value, str):
        literal = exp.Literal.string(value)
        concrete = str(value)
        srctype = "TEXT"
    elif isinstance(value, bool):
        literal = exp.Boolean(this=value)
        concrete = bool(value)
        srctype = "BOOLEAN"
    elif value is None or (isinstance(value, float) and math.isnan(value)):
        literal = exp.Null()
        concrete = None
    elif isinstance(value, numbers.Number):
        literal = exp.Literal.number(value)
        concrete = float(value) if isinstance(value, float) else int(value)
        srctype = "NUMERIC"
    elif isinstance(value, datetime.datetime):
        datetime_literal = exp.Literal.string(
            (
                value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
            ).isoformat(sep=" ")
        )
        srctype = "DATETIME"
        concrete = value
        literal = exp.TimeStrToTime(this=datetime_literal)
    elif isinstance(value, datetime.date):
        date_literal = exp.Literal.string(value.strftime("%Y-%m-%d"))
        literal = exp.DateStrToDate(this=date_literal)
        srctype = "DATE"
        concrete = value
    else:
        raise ValueError(f"Unsupported literal type: {type(value)}")
    literal.set("concrete", concrete)
    if datatype:
        literal.type = datatype
        literal.set("datatype", datatype)
    else:
        literal.type = DataType.build(srctype)
        literal.set("datatype", DataType.build(srctype))
    return literal


def to_const(
    literal: (
        exp.Literal | exp.Null | exp.Boolean | exp.TimeStrToTime | exp.DateStrToDate
    ),
    **kwargs,
) -> exp.Expression:
    DATETIME_FMT = kwargs.get("datetime_fmt", ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d"])

    value = literal.this
    datatype = literal.type
    try:
        if datatype.is_type(*exp.DataType.INTEGER_TYPES):
            value = int(value)
        elif datatype.is_type(*exp.DataType.REAL_TYPES):
            value = float(value)
        elif datatype.is_type(exp.DataType.Type.BOOLEAN):
            value = bool(value)
        elif datatype.is_type(*exp.DataType.TEMPORAL_TYPES):
            from datetime import datetime

            for fmt in DATETIME_FMT:
                try:
                    value = datetime.strptime(value, fmt)
                except ValueError:
                    continue
        elif datatype.is_type(*exp.DataType.TEXT_TYPES):
            value = str(value)
        else:
            raise ValueError(f"Unsupported datatype: {datatype}")
    except Exception as e:
        value = None
    return value


from typing import Any, List, Optional
from sqlglot.planner import Aggregate


def decode_aggregate(node: Aggregate):
    operand_map: dict[str, exp.Expression] = {
        operand.alias: operand.this for operand in node.operands
    }
    group_map: dict[str, exp.Expression] = dict(node.group)

    def restore(expr: exp.Expression) -> exp.Expression:
        expr = expr.copy()
        for col_ref in expr.find_all(exp.Column):
            name = col_ref.name
            if name in operand_map:
                col_ref.replace(operand_map[name].copy())
            elif name in group_map:
                col_ref.replace(group_map[name].copy())
        return expr

    group_by_columns: list[exp.Expression] = list(node.group.values())
    h_aliases = {a.alias for a in node.aggregations if isinstance(a, exp.Alias)}
    having_operand_name: str | None = None
    if (
        node.condition is not None
        and isinstance(node.condition, exp.Column)
        and node.condition.name in h_aliases
    ):
        having_operand_name = node.condition.name

    having_condition: exp.Expression | None = None
    having_agg_sqls: set[str] = set()
    if node.condition is not None:
        if having_operand_name:
            h_entry = next(
                a
                for a in node.aggregations
                if isinstance(a, exp.Alias) and a.alias == having_operand_name
            )
            having_condition = restore(h_entry.this)
            for agg_func in having_condition.find_all(exp.AggFunc):
                having_agg_sqls.add(agg_func.sql())
        else:
            having_condition = restore(node.condition)

    aggregations: list[exp.Expression] = []
    aggregation_alias: dict[str, exp.Expression] = {}
    covered_having_aggs: set[str] = set()

    for agg_expr in node.aggregations:
        if isinstance(agg_expr, exp.Alias) and agg_expr.alias == having_operand_name:
            continue

        restored = restore(agg_expr)
        inner = restored.this if isinstance(restored, exp.Alias) else restored

        if inner.sql() in having_agg_sqls:
            covered_having_aggs.add(inner.sql())

        aggregations.append(restored)
        aggregation_alias[restored.alias_or_name] = inner

    if having_condition is not None:
        for having_agg_sql in having_agg_sqls - covered_having_aggs:
            having_agg_node = next(
                f
                for f in having_condition.find_all(exp.AggFunc)
                if f.sql() == having_agg_sql
            )
            internal_alias = f"_having_agg_{len(aggregation_alias)}"
            aggregations.append(having_agg_node.copy())
            aggregation_alias[internal_alias] = having_agg_node.copy()

    return group_by_columns, aggregations, having_condition
