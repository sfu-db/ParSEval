from __future__ import annotations
from abc import ABC, abstractmethod
import typing as t
from sqlglot import exp
import json, copy, logging
from parseval.planner.planner import get_logical_plan
from parseval.exceptions import assert_state
from itertools import chain
from functools import reduce
from parseval.query.rex import *

logger = logging.getLogger('app')
plan_logger = logging.getLogger('app.qplan')

BINARY_OPERATOR = ['EQUALS', 'NOT_EQUALS', 'GREATER_THAN', 'LESS_THAN', 'LESS_THAN_OR_EQUAL', 'GREATER_THAN_OR_EQUAL', 'LIKE', 'AND', 'OR', 'PLUS', 'MINUS', 'TIMES', 'DIVIDE']
UNARY_OPERATOR = ["NOT", 'IS_NULL']
AGG_FUNC = ["COUNT", "SUM", "AVG", "MAX", "MIN"]

OTHER_FUNCTION = ["OTHER_FUNCTION", "OTHER"]

SCALAR_FUNCTION = {
    "SUBSTR" : lambda args: exp.Substring(this = args[0], start = args[1], length = args[2] if len(args) == 3 else exp.Literal.number(-1)),
    "INSTR": lambda args: InStr(this = args[0], substring = args[1], start = args[2] if len(args) == 3 else None),
    "UDATE": lambda args: exp.Date(this = args[0]),
    "||": lambda args: exp.Concat(expressions = args[0]),
    "LENGTH": lambda args: exp.Length(this = args[0]),
    "ABS" : lambda args: exp.Abs(this = args[0]),
    "CURRENT_TIMESTAMP": lambda args: exp.CurrentTimestamp,
    "JULIANDAY": lambda args: Julianday(this = args[0]),
    "STRFTIME": lambda args: _build_strftime(args)
}

def _build_literal(kwargs: t.Dict):
    value = kwargs.get('value')
    dtype = exp.DataType.build(dtype= kwargs.pop('type'))
    if dtype.is_type(*exp.DataType.INTEGER_TYPES, *exp.DataType.REAL_TYPES):
        literal = exp.Literal.number(value)
    else:
        literal = exp.Literal.string(value)
    literal.set('nullable', kwargs.get('nullable'))
    literal.set('precision', kwargs.get('precision'))
    literal.set('datatype', dtype)
    return literal

def _build_cast(kwargs: t.Dict):
    this = rexparser(kwargs.get('operands')[0], dialect = kwargs.get('dialect'))
    
    to = exp.DataType.build(dtype= kwargs.get('type'))
    return exp.Cast(this = this ,to = to)

def _build_strftime(args):
    if len(args) == 1:
        args.append(exp.CurrentTimestamp())
    return Strftime(this = args[1], format = args[0])


def _build_in(kwargs):
    # {'kind': 'IN', 'operator': 'IN', 'operands': [{'kind': 'INPUT_REF', 'index': 0, 'name': '$0', 'type': 'INTEGER'}], 'query': [{'relOp': 'LogicalProject', 'project': [{'kind': 'INPUT_REF', 'index': 0, 'name': '$0', 'type': 'INTEGER'}], 'id': '2', 'inputs': [{'relOp': 'LogicalFilter', 'condition': {'kind': 'AND', 'operator': 'AND', 'type': 'BOOLEAN', 'operands': [{'kind': 'EQUALS', 'operator': '=', 'type': 'BOOLEAN', 'operands': [{'kind': 'INPUT_REF', 'index': 1, 'name': '$1', 'type': 'INTEGER'}, {'kind': 'LITERAL', 'value': 468, 'type': 'INTEGER', 'nullable': False, 'precision': 10}]}, {'kind': 'EQUALS', 'operator': '=', 'type': 'BOOLEAN', 'operands': [{'kind': 'INPUT_REF', 'index': 2, 'name': '$2', 'type': 'INTEGER'}, {'kind': 'LITERAL', 'value': 0, 'type': 'INTEGER', 'nullable': False, 'precision': 10}]}]}, 'variableset': '[]', 'id': '1', 'inputs': [{'relOp': 'LogicalTableScan', 'table': 'ITL', 'id': '0', 'inputs': []}]}]}], 'dialect': None}
    this = rexparser(kwargs.get('operands')[0], dialect = kwargs.get('dialect'))
    if 'query' in kwargs:
        # print(f"kwargs.get('query')[0]: {kwargs.get('query')[0]}")
        query = relparser(kwargs.get('query')[0], dialect = kwargs.get('dialect'))
        # print(repr(query))
        # print('--' * 10)
        return exp.In(this = this, query = query)
    else:
        raise RuntimeError(f'unsupport IN')

def _build_subquery(kwargs):
    # {'kind': 'SCALAR_QUERY', 'operator': '$SCALAR_QUERY', 'operands': [], 'query': [{'relOp': 'LogicalProject', 'project': [{'kind': 'INPUT_REF', 'index': 1, 'name': '$1', 'type': 'INTEGER'}], 'id': '7', 'inputs': [{'relOp': 'LogicalFilter', 'condition': {'kind': 'GREATER_THAN', 'operator': '>', 'type': 'BOOLEAN', 'operands': [{'kind': 'INPUT_REF', 'index': 2, 'name': '$2', 'type': 'BIGINT'}, {'kind': 'LITERAL', 'value': 4, 'type': 'INTEGER', 'nullable': False, 'precision': 10}]}, 'variableset': '[]', 'id': '6', 'inputs': [{'relOp': 'LogicalAggregate', 'keys': [{'column': 0, 'type': 'INTEGER'}], 'aggs': [{'operator': 'MAX', 'distinct': False, 'ignoreNulls': False, 'operands': [{'column': 1, 'type': 'INTEGER'}], 'type': 'INTEGER', 'name': 'EXPR$0'}, {'operator': 'COUNT', 'distinct': True, 'ignoreNulls': False, 'operands': [{'column': 2, 'type': 'INTEGER'}], 'type': 'BIGINT', 'name': None}], 'id': '5', 'inputs': [{'relOp': 'LogicalProject', 'project': [{'kind': 'INPUT_REF', 'index': 2, 'name': '$2', 'type': 'INTEGER'}, {'kind': 'INPUT_REF', 'index': 3, 'name': '$3', 'type': 'INTEGER'}, {'kind': 'INPUT_REF', 'index': 0, 'name': '$0', 'type': 'INTEGER'}], 'id': '4', 'inputs': [{'relOp': 'LogicalFilter', 'condition': {'kind': 'EQUALS', 'operator': '=', 'type': 'BOOLEAN', 'operands': [{'kind': 'INPUT_REF', 'index': 1, 'name': '$1', 'type': 'INTEGER'}, {'kind': 'CAST', 'operator': 'CAST', 'type': 'INTEGER', 'operands': [{'kind': 'LITERAL', 'value': 'CS', 'type': 'CHAR', 'nullable': False, 'precision': 2}]}]}, 'variableset': '[]', 'id': '3', 'inputs': [{'relOp': 'LogicalJoin', 'joinType': 'inner', 'condition': {'kind': 'EQUALS', 'operator': '=', 'type': 'BOOLEAN', 'operands': [{'kind': 'INPUT_REF', 'index': 2, 'name': '$2', 'type': 'INTEGER'}, {'kind': 'INPUT_REF', 'index': 4, 'name': '$4', 'type': 'INTEGER'}]}, 'id': '2', 'inputs': [{'relOp': 'LogicalTableScan', 'table': 'COURSE', 'id': '0', 'inputs': []}, {'relOp': 'LogicalTableScan', 'table': 'DEPARTMENT', 'id': '1', 'inputs': []}]}]}]}]}]}]}], 'dialect': None}
    if 'query' in kwargs:
        query = relparser(kwargs.get('query')[0], dialect = kwargs.get('dialect'))
        return exp.Subquery(this = query)


    # class In(Predicate):
    # arg_types = {
    #     "this": True,
    #     "expressions": False,
    #     "query": False,
    #     "unnest": False,
    #     "field": False,
    #     "is_global": False,
    # }

    ...
SQLKIND_TO_UEXPR = {
    "LITERAL": lambda kwargs: _build_literal(kwargs),
    "INPUT_REF": lambda kwargs: exp.Column(this = exp.parse_identifier(kwargs.get('name')), datatype = exp.DataType.build(dtype=kwargs.pop('type')), ref = kwargs.pop('index')),
    
    "EQUALS" : exp.EQ,    
    "NOT_EQUALS": exp.NEQ,
    "GREATER_THAN": exp.GT,
    "LESS_THAN": exp.LT,
    "LESS_THAN_OR_EQUAL": exp.LTE,
    "GREATER_THAN_OR_EQUAL": exp.GTE,
    "LIKE": exp.Like,
    "AND" : exp.And,
    "OR" : exp.Or,
    "PLUS": exp.Add,
    "MINUS": exp.Sub,
    "TIMES": exp.Mul,
    "DIVIDE": exp.Div,
    "AND" :exp.And,
    "OR": exp.Or,

    "PLUS": exp.Add,
    "MINUS": exp.Sub,
    "TIMES": exp.Mul,
    "DIVIDE": exp.Div,

    "NOT": exp.Not,
    "IS_NULL": Is_Null,
    "CAST": _build_cast,

    "COUNT": exp.Count,
    "SUM": exp.Sum,
    "AVG": exp.Avg,
    "MAX": exp.Max,
    "MIN": exp.Min,

    "CASE" : exp.Case,

    "IN": lambda kwargs: _build_in(kwargs),
    'SCALAR_QUERY': lambda kwargs: _build_subquery(kwargs)
}

# def _build_case(rex):
#     expressions = [build(operand) for operand in rex.get('operands', [])]
#     ifs = []
#     default = expressions[-1]
    
#     for index in range(0, len(expressions) - 1 , 2):
#         ifs.append(uexpression(If, this = expressions[index], true = expressions[index + 1]))
#     return uexpression(
#         Case, ifs=ifs, default=default
#     )

def rexparser(rex, dialect = None):
    rex['dialect'] = dialect
    kind = rex.get('kind', rex.get('operator'))
    if kind in BINARY_OPERATOR:
        '''CONNECTOR OPERATOR'''
        expressions = [rexparser(operand, dialect= dialect) for operand in rex.get('operands', [])]
        return reduce(lambda x, y : SQLKIND_TO_UEXPR[kind](this = x, expression = y), expressions)
    elif kind in UNARY_OPERATOR:
        this = rexparser(rex.get('operands')[0], dialect= dialect)
        return SQLKIND_TO_UEXPR[kind](this = this)
    elif kind in AGG_FUNC:
        '''AGG FUNCTION'''
        func_name = rex.get('operator')
        distinct = rex.get('distinct')
        operands = rex.get('operands')
        this = exp.Star()
        if operands:
            this = exp.Column(this = f"${operands[0]['column']}", ref =  operands[0]['column'], datatype = exp.DataType.build(dtype = operands[0]['type']))
        func = SQLKIND_TO_UEXPR[func_name](this = this, distinct = distinct, ignorenulls = rex.get('ignorenulls'), datatype = exp.DataType.build(rex.get('type')))
        return func
    elif kind in OTHER_FUNCTION:
        kind = rex.get('operator')
        args = [rexparser(operand, dialect= dialect) for operand in rex.get('operands')]
        return SCALAR_FUNCTION[kind](args)
    return SQLKIND_TO_UEXPR[kind](rex)


######################################################################
####           Convert Logical Plan to Expression Tree            ####
######################################################################

def __build_aggregate(kwargs):
    groupby = tuple(exp.Column(this = f"${gid}", ref = key.get('column'), datatype = exp.DataType.build(dtype = key.get('type')))  for gid, key in enumerate(kwargs.pop('keys')))
    agg_funcs = tuple(rexparser(func_def) for func_def in kwargs.pop('aggs'))
    return Aggregate(this = kwargs.pop('this'), groupby = groupby, agg_funcs = agg_funcs, **kwargs)


REL_MAPPING = {
        "LogicalTableScan": lambda rel : Scan(**rel),
        'EnumerableTableScan' : lambda rel : Scan(**rel),
        'LogicalProject': lambda rel: Project(this = rel.pop('this'), expressions = [rexparser(project) for project in rel.pop('project')], **rel),
        'LogicalFilter' : lambda rel: Filter(this = rel.pop('this'), condition = rexparser(rel.pop('condition')), **rel),
        'LogicalJoin': lambda rel : Join(this = rel.pop('this'), expression = rel.pop('expression'), condition = rexparser(rel.pop('condition')), **rel),
        'LogicalAggregate': lambda rel : __build_aggregate(rel),
        'LogicalIntersect': lambda rel :   Intersect(this = rel.pop('this'), expression = rel.pop('expression'), **rel),
        'LogicalMinus': lambda rel :   Minus(this = rel.pop('this'), expression = rel.pop('expression'), **rel),
        'LogicalSort': lambda rel : Sort(this = rel.pop('this'), **rel),
        'LogicalValues': Values,
        'LogicalUnion': lambda rel: Union(this = rel.pop('this'), expression = rel.pop('expression'), **rel)
    }
   

def relparser(rel, **args):
    rel = copy.deepcopy(rel)
    relOp = rel.pop('relOp')
    dependences = [relparser(dep, **args) for dep in rel.get('inputs', [])]    
    parameters = {k: v for k, v in rel.items() if k != 'inputs'}
    this = dependences[0] if dependences else None
    expression = dependences[1] if len(dependences) > 1 else None
    parameters['this'] = this
    parameters['expression'] = expression
  
    op = REL_MAPPING[relOp](parameters)
  
    return op

def qparse_one(schema: t.List[str], query: str, dialect = 'sqlite') -> Step:
    response = get_logical_plan(ddl= schema, queries= [query], dialect=  dialect)
    raw = json.loads(response)[0]
    assert_state(raw.get('state'), raw.get('error', ''))
    plan = json.loads(raw.get('plan'))
    plan_logger.info(raw)

    
    return relparser(plan)

def qparse(schema: t.List[str], queries: t.List[str], dialect = 'sqlite') -> t.List[t.Optional[Step]]:
    response = get_logical_plan(ddl= schema, queries= queries, dialect=  dialect)
    step = []
    for raw in json.loads(response):
        assert_state(raw.get('state'), raw.get('error', ''))
        plan = json.loads(raw.get('plan'))
        plan_logger.info(raw)
        step.append(relparser(plan))
    return step
    