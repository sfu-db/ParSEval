from sqlglot import exp
from typing import List, Union
def split_sql_conditions(condition: exp.Condition):
    """
    Splits the WHERE condition into individual filters.
    Args:
        condition (exp.Expression): The WHERE condition expression.
    Returns:
        list[exp.Expression]: A list of individual filter conditions.
    """
    if isinstance(condition, exp.Connector):
        return split_sql_conditions(condition.this) + split_sql_conditions(condition.expression)
    
    return [condition]


def negate_sql_condition(expr: exp.Condition) -> exp.Condition:
    """Negate a simple comparison condition"""
    if isinstance(expr, exp.EQ):
        return exp.NEQ(this=expr.left, expression=expr.right)
    if isinstance(expr, exp.NEQ):
        return exp.EQ(this=expr.left, expression=expr.right)
    if isinstance(expr, exp.GT):
        return exp.LTE( this=expr.left, expression=expr.right)
    if isinstance(expr, exp.GTE):
        return exp.LT( this=expr.left, expression=expr.right)
    if isinstance(expr, exp.LT):
        return exp.GTE( this=expr.left, expression=expr.right)
    if isinstance(expr, exp.LTE):
        return exp.GT( this=expr.left, expression=expr.right)
    if isinstance(expr, exp.Not):
        return expr.this
    if isinstance(expr, ( exp.Column, exp.AggFunc)):
        return expr
    return exp.Not(this=expr)


def get_datatype(expr: exp.Expression) -> exp.DataType:
    if isinstance(expr, exp.Expression):
        datatype = expr.args.get('datatype')
        if datatype is None:
            datatype = list( expr.find_all(exp.DataType))[0]
            # datatype = expr.type
        return datatype
    raise ValueError(f'Cannot get data type from {expr}')

def get_ref(expr: exp.Condition) -> int:
    refs = get_refs(expr)
    return refs[0] if refs else 0

def get_refs(expr: exp.Condition) -> List[int]:
    assert isinstance(expr, exp.Expression), f"can not get ref from {expr}" 
    refs = []
    for column in expr.find_all(exp.Column):
        ref = column.args.get('ref')
        if ref is not None:
            refs.append(int(ref))
    return refs