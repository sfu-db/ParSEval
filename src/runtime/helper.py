from sqlglot import exp
def split_conditions(condition: exp.Condition):
    """
    Splits the WHERE condition into individual filters.
    Args:
        condition (exp.Expression): The WHERE condition expression.
    Returns:
        list[exp.Expression]: A list of individual filter conditions.
    """
    if isinstance(condition, exp.Connector):
        return split_conditions(condition.this) + split_conditions(condition.expression)
    return [condition]