from __future__ import annotations
from abc import ABC, abstractmethod
import typing as t
from datetime import datetime
from parseval.symbol import Term
from parseval.query import rex, uexpr

from sqlglot import exp

from parseval.exceptions import UnSupportError

def display_plan(expr, **kwargs):
    indent = kwargs.get('indent', '')
    if isinstance(expr, rex.Step):
        s  = display_step(expr, indent = indent)
        return '\n'.join(s)
    elif isinstance(expr, exp.Expression):
        return display_exp(expr, indent = indent)
    raise UnSupportError(f'UNSUPPORT query feature, cound not display {expr}')

def display_step(expr, **kwargs):
    indent = kwargs.get('indent', '')
    nested = indent + '  '

    if isinstance(expr, rex.Scan):
        return [f"{nested}{expr.type_name}({expr.text('table')})"]
    elif isinstance(expr, rex.Project):
        # print(expr.projections)
        s = [f"{indent}{expr.type_name}({', '.join([display_exp(proj) for proj in expr.projections])})", display_plan(expr.left, indent = nested)]
        return s
    elif isinstance(expr, rex.Filter):
        s = [f"{indent}{expr.type_name}(condition=[{display_plan(expr.condition)}])",  display_plan(expr.left, indent = nested)]
        return s
    elif isinstance(expr, rex.Join):
        s = [f'{indent}{expr.type_name}(condition=[{display_plan(expr.condition)}], join_kind={expr.kind})', display_plan(expr.left, indent = nested), display_plan(expr.right, indent = nested)]
        return s
    elif isinstance(expr, rex.Aggregate):
        params = [*(c.sql() for c in expr.groupby), *(f.sql() for f in expr.agg_funcs)]
        s = [f"{indent}{expr.type_name}({', '.join(params)})", display_plan(expr.left, indent= nested)]
        return s
    elif isinstance(expr, rex.Sort):
        orderby = [col['column'] for col in expr.args.get('sort')]
        s = [f"{indent}{expr.type_name}(sort = {orderby}, dir = {expr.args.get('dir')}, offset = {expr.args.get('offset')}, fetch = {expr.args.get('fetch')})", display_plan(expr.left, indent = nested)]
        return s
    elif isinstance(expr, rex.Values):
        ...
    elif isinstance(expr, rex.Union):
        s = [f"{indent}{expr.type_name}(all=[{display_plan(expr.args.get('all'))}])", display_plan(expr.left, indent = nested), display_plan(expr.right, indent = nested)]
        return s
    elif isinstance(expr, exp.Subquery):
        
        s = [f"{indent}{expr.type_name}({ display_plan(expr.this)})"]
        return s
        ...
    raise UnSupportError(f'UNSUPPORT rex node, cound not display: {expr.key}, {expr}')


def display_exp(expr, **kwargs):
    if isinstance(expr, exp.Column):
        return expr.text('this')
    elif isinstance(expr, exp.Literal):
        return expr.this if expr.is_number else expr.text('this')
    elif isinstance(expr, exp.In):
        left = display_plan(expr.this)
        right = []
        if expr.expressions:
            right = [display_plan(e) for e in expr.expressions]
        elif expr.args.get('query'):
            query = expr.args.get('query')[0]
            right = display_plan(query.this)
        return f"{left} IN {{{right}}} "
    elif isinstance(expr, exp.Unary):
        return f"{expr.key.upper()}({display_exp(expr.this, **kwargs)})"
    elif isinstance(expr, exp.Func):
        return expr.sql()
    elif isinstance(expr, exp.Binary):
        mappings = {
            'like': 'Like',
            'add' : '+',
            'sub' : '-',
            'mul' : 'x',
            'div' : '/',
            'and' : 'AND',
            'or' : 'OR',
            'gt': '>',
            "lt": "<",
            "gte": ">=",
            "lte": "<=",
            "eq": "==",
            "ne": "!=",
        }
        left = display_plan(expr.this)
        right = display_plan(expr.expression)
        # print(f'expr.key: {expr.key}')
        return f"{left} {mappings[expr.key]} {right}"
    elif isinstance(expr, exp.Subquery):
        return f"SUBQUERY({display_plan(expr.this)})"
    raise UnSupportError(f'UNSUPPORT exp node, could not display: {expr.key}, {repr(expr)}')

def display_usummation(node):
    # for eee in node.expressions:
    #     print(repr(eee))
    # print('===' * 10)
    exprs = [display_uexpr(expr) for expr in node.expressions]
    return f"∑ ({ ', '.join(exprs)})"
    
def display_tuple(node):
    return node.text('this')
# str(node.this)


def display_binary(node):
    left = display_uexpr(node.left)
    right = display_uexpr(node.right)
    mappings = {
        'like': 'LIKE',
        'add' : '+',
        'sub' : '-',
        'mul' : 'x',
        'div' : '/',
        'and' : 'and',
        'or' : 'or',
        'umul': 'x',
        'uadd': '+',
        'gt': '>',
        "lt": "<",
        "gte": ">=",
        "lte": "<=",
        "eq": "==",
        "neq": "!=",
        
    }
    return f"{left} {mappings[node.key]} {right}"

def display_upredicate(node):
    left = display_uexpr(node.this)
    # right = display_uexpr(node.expressions)
    # 'gt': '>',
    # "lt": "<",
    # "ge": ">=",
    # "le": "<=",
    # "eq": "==",
    # "ne": "!=",

    return f"| {left} |"

def display_uexpr(node) -> str:
    
    if isinstance(node, uexpr.USummation):
        return display_usummation(node)
    elif isinstance(node, uexpr.Relation):
        return f"⟦ Relation({node.text('table')}, {node.this}) ⟧"    
    elif isinstance(node, uexpr.UPredicate):
        return display_upredicate(node)
    elif isinstance(node, uexpr.UConnector):
        return display_binary(node)
    elif isinstance(node, exp.Binary):
        return display_binary(node)
    elif isinstance(node, exp.Column):
        if node.text('table'):
            return f"{node.text('table')}.{ node.text('this')}"
        return node.text('this')
    elif isinstance(node, exp.Literal):
        return node.this if node.is_number else node.text('this')
    elif isinstance(node, exp.If):
        this =  display_uexpr(node.this)
        true = display_uexpr(node.args.get('true'))
        false =  display_uexpr(node.args.get('false'))
        return f"IF({this}, true = {true}, false = {false})"
    elif isinstance(node, exp.Not):
        this =  display_uexpr(node.this)
        return f"NOT({this})"
    elif isinstance(node, rex.Is_Null):
        this = display_uexpr(node.this)
        return f"{this} IS NULL"
    elif isinstance(node, exp.Cast):
        this = display_uexpr(node.this)
        return f"CAST({this}): {node.args.get('to')}"
    # print(repr(node))