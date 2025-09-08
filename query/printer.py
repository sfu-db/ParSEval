from __future__ import annotations
from abc import ABC, abstractmethod
import typing as t
from datetime import datetime
from sqlglot import exp
from parseval.symbol import Term
from . import uexpr

def print_usummation(node):
    exprs = [print_uexpr(expr) for expr in node.expressions]
    return f"∑ ({ ', '.join(exprs)})"
    
def print_tuple(node):
    return str(node.this)

def print_binary(node):
    left = print_uexpr(node.left)
    right = print_uexpr(node.right)
    mappings = {
        'add' : '+',
        'sub' : '-',
        'mul' : 'x',
        'div' : '/',
        'and' : 'x',
        'or' : '+',
        'umul': 'x',
        'uadd': '+',
        'gt': '>',
        "lt": "<",
        "ge": ">=",
        "le": "<=",
        "eq": "==",
        "ne": "!=",
    }
    return f"{left} {mappings[node.key]} {right}"

def print_predicate(node):
    # left = print_uexpr(node.left)
    # right = print_uexpr(node.right)
    # mappings = {
    #     'gt': '>',
    #     "lt": "<",
    #     "ge": ">=",
    #     "le": "<=",
    #     "eq": "==",
    #     "ne": "!="
    # }
    return f"|{node}|"

def print_uexpr(node) -> str:
    if isinstance(node, uexpr.USummation):
        return print_usummation(node)
    elif isinstance(node, uexpr.Relation):
        return f"⟦ {node.this}({print_tuple(node.args.get('t'))}) ⟧"    
    elif isinstance(node, uexpr.UPredicate):
        return print_predicate(node)
    elif isinstance(node, uexpr.UConnector):
        return print_binary(node)
    
    
    # elif isinstance(node, exp.Column):
    #     return node.args.get('t') + '.' + str(node)
    # elif isinstance(node, exp.Literal):
    #     return f"'{str(node)}'" if node.is_string else node.this
        
    # elif isinstance(node, uexpr.Predicate):
    #     return print_predicate(node)
    # elif isinstance(node, uexpr.Binary):
    #     return print_binary(node)
