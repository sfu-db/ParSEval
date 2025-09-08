from __future__ import annotations
import math
import typing as t
from abc import ABC, abstractmethod

from sqlglot import alias, exp
import os, logging
from . import rel

def ensure_list_of_dependence(deps):
    if deps is None:
        return []
    return deps if isinstance(deps, list) else [deps]

def vindex(value):
    if isinstance(value, rel.Column):
        return int(value.name[value.name.index('$') + 1:])
    return value

class Step:
    def __init__(self) -> None:
        self.name: t.Optional[str] = self.type_name

        self.dependencies: t.List[Step] = []
        self.dependents: t.List[Step] = []

        self.projections: t.Sequence[exp.Expression] = []
        self.condition: t.Optional[exp.Expression] = None

        self.branches = None

    def add_dependency(self, dependency: t.Union[Step, t.List[Step], None] = None) -> None:
        dependency = dependency if isinstance(dependency, list) else [dependency]
        for child in dependency:
            self.dependencies.append(child)
            child.dependents.append(self)

    @abstractmethod
    def get_input(self, column: t.Union[exp.Expression, int]) -> exp.Expression:
        'Return input of current operator in column index'
        ...


    @abstractmethod
    def get_inputs(self) -> t.List[exp.Expression]:
        '''
            Return a list of Expression of child operators
        '''
        ...

    @abstractmethod
    def get_input_type(self, column: t.Union[rel.Column, int]) -> exp.DataType:
        '''
            Return data type of input at index 
        '''
    @abstractmethod
    def is_input_unique(self, column: t.Union[rel.Column, int])-> bool:
        '''
            Check if input of current operator (i.e. output of child operators) is unique
        '''
        ...
    @abstractmethod
    def is_input_notnull(self, column: t.Union[rel.Column, int]) -> bool:
        '''
            Check if input of current operator (i.e. output of child operators) is not null, 
        '''
        ...

    
    def get_output_type(self, column: t.Union[rel.Column, int]) -> exp.DataType:
        '''
            Return output type of column in current operator
        '''
        return self.get_input_type(column)
    
    def is_output_unique(self, column: t.Union[rel.Column, int]) -> bool:
        '''
            check if output column of current operator is unique
        '''
        return self.is_input_unique(column)

    def is_output_notnull(self, column: t.Union[rel.Column, int]) -> bool:
        '''
            check if output column of current operator is notnull
        '''
        return self.is_input_notnull(column)

    def get_divider(self):
        if self.dependencies:
            return len(self.dependencies[0].projections)
        return 0
    
    @abstractmethod
    def setup(self, instance):
        ...

    @abstractmethod
    def to_formular(self, row_id):
        ...

    @abstractmethod
    def transform_constraint(self, constraints:t.List):
        ...

    @abstractmethod
    def get_next(self, constraints, **kw):
        ...

    @abstractmethod
    def get_output_data(self)-> t.List:
        ...

    @abstractmethod
    def get_input_data(self):
        ...

    def update_coverage(self, row):
        ...
    def __repr__(self) -> str:
        return self._to_s('')

    def _to_s(self, _indent: str) -> str:
        return ''

    def postorder_traverse(self):
        """Traverse the tree in preorder"""
        for child in self.dependencies:
            yield from child.postorder_traverse()
        yield self

    def preorder_traverse(self):
        yield self
        for child in self.dependencies:
            yield from child.preorder_traverse()

    @property
    def type_name(self) -> str:
        return self.__class__.__name__
    