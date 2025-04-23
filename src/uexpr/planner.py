
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import NewType, Dict, List, Any
from sqlglot import exp
import json, copy, logging, ast
from itertools import chain
from functools import reduce
from src.uexpr.rex import *
from src.exceptions import *
from .query_parser import get_logical_plan


logger = logging.getLogger('src.parseval.planner')

UniqueId = NewType("UniqueId", str)
Pattern = NewType('Pattern', str)

CONSTRAINT = 'CONSTRAINT'
REQUIRE = 'REQUIRE'
DATA = 'DATA'
GROUP_KEY = 'GROUP_KEY'
GROUP_DATA = 'GROUP_DATA'

RELOP = 'relOp'
CONDITION = 'condition'


OTHER_FUNCTION = {
    'STRFTIME' : 'strftime',
    'UDATE': 'udate'
}

# ["OTHER_FUNCTION", "OTHER"]

BINARY_OPERATORS = {
    "EQUALS" : 'exp.EQ',
    "NOT_EQUALS": 'exp.NEQ',
    "GREATER_THAN": 'exp.GT',
    "LESS_THAN": 'exp.LT',
    "LESS_THAN_OR_EQUAL": 'exp.LTE',
    "GREATER_THAN_OR_EQUAL": 'exp.GTE',
    "LIKE": 'exp.Like',
    "AND" : 'exp.And',
    "OR" : 'exp.Or',
    "PLUS": 'exp.Add',
    "MINUS": 'exp.Sub',
    "TIMES": 'exp.Mul',
    "DIVIDE": 'exp.Div',
    "AND" : 'exp.And',
    "OR": 'exp.Or',

    "PLUS": 'exp.Add',
    "MINUS": 'exp.Sub',
    "TIMES": 'exp.Mul',
    "DIVIDE": 'exp.Div',
}

UNARY_OPERATORS = {
    "NOT": 'exp.Not',
    "IS_NULL": 'Is_Null',
}

AGG_FUNCS = {
    "COUNT": exp.Count,
    "SUM": exp.Sum,
    "AVG": exp.Avg,
    "MAX": exp.Max,
    "MIN": exp.Min,
}

def _build_strftime(args):
    if len(args) == 1:
        args.append(exp.CurrentTimestamp())
    return Strftime(this = args[1], format = args[0])

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

class Plan:    
    REL_MAPPING = {
        "LogicalTableScan": 'scan',
        'EnumerableTableScan' : 'scan',
        'LogicalProject': 'project',
        'LogicalFilter' : 'filter',
        'LogicalJoin': 'join',
        'LogicalAggregate': 'aggregate',
        'LogicalUnion': 'union', 
        'LogicalIntersect': 'intersect',
        'LogicalMinus': 'minus',
        'LogicalSort': 'sort',
        'LogicalValues': 'values',
        'SCALAR_QUERY': 'scalar'
    }
    def __init__(self, schema: Union[List[str], str], query: str, dialect = None) -> None:
        if isinstance(schema, str):
            schema = schema.split(';')
        self.dialect = dialect
        self.root = self._parse(schema, query= query)
        
    def _parse(self, ddl: List[str], query: str):
        """
        Args:
            ddl: List of query create table statement
            query: SQL qeury
        """
        raw = get_logical_plan(ddl= ddl, queries= [query], dialect= self.dialect)
        src = json.loads(raw)[0]
        
        if src['state'] == 'SUCCESS':
            return self.walk(json.loads(src.get('plan')))
        elif src['state'] == 'SYNTAX_ERROR':
            raise QuerySyntaxError(src['error'])
        elif src['state'] == 'SCHEMA_ERROR':
            raise SchemaError(src['error'])

    def walk(self, node):
        fname = None
        if RELOP in node:
            '''parse rel expression'''
            relOp = node.pop(RELOP)
            fname = self.REL_MAPPING.get(relOp)
        elif 'kind' in node or 'operator' in node:
            '''parse rex expression'''
            kind_operator = node.get('kind', node.get('operator'))
            if kind_operator in AGG_FUNCS:
                fname = 'aggfunc'
            else:
                fname = kind_operator
        fname = "on_%s" % fname.lower()
        if hasattr(self, fname):
            fn = getattr(self, fname)
            return fn(node)
        
        raise RuntimeError(f'counld not parse {node}')

    def on_scan(self, node):
        return Scan(**node)
    
    def on_project(self, node):
        this = self.walk(node.pop('inputs')[0])
        expressions =  [self.walk(project) for project in node.pop('project')]
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        return Project(this = this, expressions = expressions, **parameters)
    
    def on_filter(self, node):
        this = self.walk(node.pop('inputs')[0])
        condition = self.walk(node.pop(CONDITION))
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        return Filter(this = this, condition = condition, **parameters)
    
    def on_scalar_query(self, node):
        this = self.walk(node.pop('query')[0])
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        return Scalar(this = this, **parameters)


    def on_join(self, node):
        deps =[self.walk(dep) for dep in node.pop('inputs')]
        condition = self.walk(node.pop(CONDITION))
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        return Join(this = deps[0], expression = deps[1], condition = condition, **parameters)
    
    def on_aggregate(self, node):
        this = self.walk(node.pop('inputs')[0])
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        groupby = tuple(exp.Column(this = f'${gid}', ref = key.get('column'), 
                                   datatype = exp.DataType.build(dtype= key.get('type'), dialect= self.dialect)) 
                                   for gid, key in enumerate(parameters.pop('keys')))
        agg_funcs = tuple(self.walk(func_def) for func_def in parameters.pop('aggs'))
        return Aggregate(this = this, groupby = groupby, agg_funcs = agg_funcs, **parameters)
    
    def on_sort(self, node):
        this = self.walk(node.pop('inputs')[0])
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        return Sort(this = this, **parameters)
    
    def on_union(self, node):
        inputs = node.pop('inputs')
        left = inputs[0]
        right = inputs[1]
        parameters = {k: v for k, v in node.items() if k != 'inputs'}
        return Union(this = self.walk(left), expression = self.walk(right), **parameters)
    def on_unary(self, node):
        kind = node.get('kind', node.get('operator'))
        expressions = [self.walk(operand) for operand in node.get('operands', [])]
        return UNARY_OPERATORS[kind](this = expressions.pop())
    
    def on_aggfunc(self, node):
        '''AGG FUNCTION'''
        func_name = node.get('operator')
        distinct = node.get('distinct')
        operands = node.get('operands')
        this = exp.Star()
        if operands:
            this = self.on_input_ref({'name':  f"${operands[0]['column']}", 
                                     'type': operands[0].get('type'),
                                     'index': operands[0]['column']})
        func = AGG_FUNCS[func_name](this = this, distinct = distinct, ignorenulls = node.get('ignorenulls'), datatype = exp.DataType.build(node.get('type')))
        # exp.func()
        return func    

    def on_literal(self, node):
        value = node.get('value')
        dtype = exp.DataType.build(dtype= node.pop('type'))
        if dtype.is_type(*exp.DataType.INTEGER_TYPES, *exp.DataType.REAL_TYPES):
            literal = exp.Literal.number(value)
        else:
            literal = exp.Literal.string(value)
        literal.set('nullable', node.get('nullable'))
        literal.set('precision', node.get('precision'))
        literal.set('datatype', dtype)
        return literal
        
    def on_input_ref(self, node):
        return exp.Column(this = exp.parse_identifier(node.pop('name')), 
                          datatype = exp.DataType.build(dtype=node.pop('type')), 
                          ref = node.pop('index'))
    def on_cast(self, node):
        this = self.walk(node.get('operands')[0])
        return exp.cast(this, to= node.get('type'))
    
    def on_other_function(self, node):
        operator = node.get('operator')
        expressions = [self.walk(operand) for operand in node.get('operands', [])]
        return SCALAR_FUNCTION.get(operator.upper())(expressions)
        return getattr(self, f'on_{OTHER_FUNCTION.get(operator)}')(node)

        
    def on_strftime(self, node):
        # arg_types = {"this": True, "format": True, "culture": False}
        fmt = self.walk(node.get('operands')[0])
        this = self.walk(node.get('operands')[1])
        return Strftime(this = this, format = fmt)
    
    def on_udate(self, node):
        expressions = [self.walk(operand) for operand in node.get('operands', [])]
        return exp.Date(this = expressions.pop())
    def on_substr(self, node):
        expressions = [self.walk(operand) for operand in node.get('operands', [])]
        this = expressions[0]

        start = expressions[1]
        length = expressions[2] if len(expressions) > 1 else -1

        return exp.Substring(this = this, start = start, len = length)
    
    def on_case(self, node):

        operands = node.get('operands')
        default = self.walk(operands.pop())

        ifs = []
        for index in range(0, len(operands), 2):
            this = self.walk(operands[index])
            true = self.walk(operands[index + 1])
            ifs.append(exp.If(this = this, true = true))
        return exp.Case( ifs = ifs, default = default)

    def on_in(self, node):
        this = self.walk(node.get('operands').pop())
        query = self.walk(node.get('query').pop())
        return exp.In(this = this , query = query)







        
def make_method(method, op):
    code = "def %s(self, node):\n" % method
    code += "   kind = node.get('kind', node.get('operator'))\n"
    code += "   expressions = [self.walk(operand) for operand in node.get('operands', [])]\n"
    code += "   return reduce(lambda x, y: %s(this = x, expression = y), expressions)" % op
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(Plan, method, locals_dict[method])

def make_unary_method(method, op):
    code = "def %s(self, node):\n" % method
    code += "   expressions = [self.walk(operand) for operand in node.get('operands', [])]\n"
    code += "   return %s(this = expressions.pop())" % op
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(Plan, method, locals_dict[method])

for (name, op) in BINARY_OPERATORS.items():
    method = 'on_%s' % name.lower()
    make_method(method, op)

for (name, op) in UNARY_OPERATORS.items():
    method = 'on_%s' % name.lower()
    make_unary_method(method, op)



