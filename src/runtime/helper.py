from sqlglot import exp
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
    if isinstance(expr, exp.Column):
        return expr
    return exp.Not(this=expr)