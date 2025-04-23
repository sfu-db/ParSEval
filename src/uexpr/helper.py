from sqlglot import exp
from typing import Set, Union, Optional, Dict, Generator
import re, z3


def visit_z3_expr(
        e: Union[z3.ExprRef, z3.QuantifierRef],
        seen: Optional[Dict[Union[z3.ExprRef, z3.QuantifierRef], bool]] = None) -> \
        Generator[Union[z3.ExprRef, z3.QuantifierRef], None, None]:
    if seen is None:
        seen = {}
    elif e in seen:
        return

    seen[e] = True
    yield e

    if z3.is_app(e):
        for ch in e.children():
            for e in visit_z3_expr(ch, seen):
                yield e
        return

    if z3.is_quantifier(e):
        for e in visit_z3_expr(e.body(), seen):
            yield e
        return

def is_z3_var(e: z3.ExprRef) -> bool:
    # print(z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED, e)
    return z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED

def get_all_vars(e: z3.ExprRef) -> Set[z3.ExprRef]:
    return {sub for sub in visit_z3_expr(e) if is_z3_var(sub)}






def rename_variable(expr, rename_map = None):
    if rename_map is None:
        rename_map = {}

    variables = get_all_vars(expr)
    for old_var in variables:
        if old_var not in rename_map:
            new_name = f"{old_var.decl().name()}_0"
            rename_map[old_var] = z3.Const(new_name, old_var.sort())
    return z3.substitute(expr, [(old, new) for old, new in rename_map.items()])


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

def clean_str(s):
    return re.sub(r"[^0-9a-zA-Z+\-*/%=$!() ]|!(?!=)", "", s)


def smt_complexity(*args):
    ''' We use VARIABLES in the expression as complexity.
    '''
    cnt = 0
    for expr in args:
        cnt += count_nodes_in_smt(expr)
    return cnt

def count_nodes_in_smt(expr):
    """Recursively counts the number of nodes in a Z3 expression."""
    if expr.num_args() == 0:
        return 1
    return 1 + sum(count_nodes_in_smt(child) for child in expr.children())

def extend_smt_clause(existing_expr, varis):
    '''
        Extend the existing expression with new variables.
        The new variables are added to the existing expression.
        Args:
            existing_expr: The existing Z3 SMT expression.
            varis: The new Z3 variables (src, tar) to add to the expression.
        Returns:
            The extended expression.
    '''
    existing_terms = []
    op = z3.Or
    if z3.is_or(existing_expr):
        existing_terms.extend(existing_expr.children())
    elif z3.is_and(existing_expr):
        existing_terms.extend(existing_expr.children())
        op = z3.And
    else:
        existing_terms.append(existing_expr)
    new_terms = set()
    
    for term in existing_terms:
        if get_all_vars(term) & set(varis):
            new_condition = z3.substitute(term, varis)
            new_terms.add(new_condition)
        else:
            new_terms.add(term)
    new_expr = op(*list(new_terms)) 
    return new_expr

        
