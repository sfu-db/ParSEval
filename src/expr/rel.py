from __future__ import annotations
import typing as t
from sqlglot import exp
from .func import *

#######################################################################
##                   Definition of Query Plan                       ##
#######################################################################

class Step(exp.Expression):
    def sql(self, dialect: exp.DialectType = None, **opts) -> str:
        dialect = self.args.get('dialect')
        return super().sql(dialect = dialect, **opts)
    @property
    def left(self) -> Step:
        return self.this
    @property
    def type_name(self) -> str:
        return self.__class__.__name__
    def i(self):
        return self.text('id')

class Scan(Step):
    arg_types = {
        "this" : False,
        "table" : True,
    }
    @property
    def table(self) -> str:
        return self.text('table')
class Project(Step):
    arg_types = {
        "this" : True,
        "expressions": True
    }
    @property
    def projections(self) -> t.List[exp.Expression]:
        return self.expressions
        
    
class Filter(Step):
    arg_types = {
        "this" : True,
        "condition" : True
    }
    @property
    def condition(self):
        return self.args.get('condition')
    

class Scalar(Step):
    arg_types = {
        "this" : True
    }
    def __repr__(self):
        return self._to_s('')
    
    def sql(self, dialect = None, **opts):
        return self.key
    

class Aggregate(Step):
    arg_types = {
        "this" : True,
        "groupby" : True,
        "agg_funcs": False
    }
    @property
    def groupby(self):
        return self.args.get('groupby')
    @property
    def agg_funcs(self):
        return self.args.get('agg_funcs')
    

class Values(Step):
    arg_types = {
        "this": False,
        "values": False
    }
class Sort(Step):
    arg_types = {
        "this" : True,
        "dir" : True,
        "offset": True,
        "limit": False
    }
    @property
    def offset(self):
        return self.args.get('offset')
    
    @property
    def limit(self):
        return self.args.get('limit') or 'INF'


class Join(Step):
    arg_types = {
        "this" : True,
        "expression": True,
        "kind": True,
        "conditon" : True
    }
    @property
    def right(self) -> Step:
        return self.expression
    @property
    def condition(self) -> exp.Expression:
        return self.args.get('condition')
    @property
    def kind(self):
        return self.args.get('joinType')

    
class Union(Step):
    arg_types = {
        "this" : True,
        "expression": True,
        "all": True
    }
    @property
    def right(self) -> Step:
        return self.expression


class Intersect(Union):
    ...

class Minus(Union):
    ...
class Correlate(Step):
    ...



class Row(exp.Expression):
    arg_types = {'this': True, 'expressions' : True}

    @property
    def multiplicity(self):
        return self.args.get('this')
    
    def __str__(self):
        s = ', '.join([str(e) for e in self.expressions])
        return f"{self.multiplicity} :Row({s})"
    
    def __getitem__(self, other):
        return self.expressions[other]

    def __mul__(self, other):
        c = [*self.expressions, *other.expressions]
        multiplicity = self.multiplicity * other.multiplicity
        return Row(expressions = c, multiplicity = multiplicity)
