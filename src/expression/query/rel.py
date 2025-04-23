from __future__ import annotations
from typing import Dict, Type, Callable, List
from sqlglot import exp
from abc import abstractmethod
from .func import *

class StepRegistry:
    """Registry for custom node types and functions"""
    _node_types: Dict[str, Type['Step']] = {}
    _scalar_functions: Dict[str, Callable] = {}
    _aggregate_functions: Dict[str, Callable] = {}

    @classmethod
    def register_step(cls, node_type: str):
        """Decorator to register custom node types"""
        def decorator(node_class: Type['Step']):
            cls._node_types[node_type] = node_class
            return node_class
        return decorator
    
    @classmethod
    def register_scalar_function(cls, function_name: str):
        """Decorator to register custom scalar functions"""
        def decorator(func: Callable):
            cls._scalar_functions[function_name.upper()] = func
            return func
        return decorator
    
    @classmethod
    def register_aggregate_function(cls, function_name: str):
        """Decorator to register custom aggregate functions"""
        def decorator(func: Callable):
            cls._aggregate_functions[function_name.upper()] = func
            return func
        return decorator



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

# @StepRegistry.register_step('Scan')
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
    def projections(self) -> List[exp.Expression]:
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
    arg_types = {'expressions' : True, 'multiplicity': True}

    @property
    def multiplicity(self):
        return self.args.get('multiplicity')
    def __str__(self):
        s = ', '.join([str(e) for e in self.expressions])
        return f"{self.multiplicity} :Row({s})"
    
    def __getitem__(self, other):
        return self.expressions[other]

    def __mul__(self, other):
        c = [*self.expressions, *other.expressions]
        multiplicity = self.multiplicity * other.multiplicity
        return Row(expressions = c, multiplicity = multiplicity)

# class Row(exp.Expression):
#     arg_types = {'this': True, 'expressions' : True}

#     @property
#     def multiplicity(self):
#         return self.args.get('this')
    
#     def __str__(self):
#         s = ', '.join([str(e) for e in self.expressions])
#         return f"{self.multiplicity} :Row({s})"
    
#     def __getitem__(self, other):
#         return self.expressions[other]

#     def __mul__(self, other):
#         c = [*self.expressions, *other.expressions]
#         multiplicity = self.multiplicity * other.multiplicity
#         return Row(expressions = c, multiplicity = multiplicity)
