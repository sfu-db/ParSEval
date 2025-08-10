from __future__ import annotations

from typing import NewType, Union
from abc import ABC
import sys

PARSEVAL_NO_SOLUTIONS = 'No Solutions'
PARSEVAL_GAVE_UP = 'Gave up'
PARSEVAL_SAT = 'sat'

OperatorKey = NewType('OperatorKey', str)
OperatorId = NewType('OperatorId', str)
ConstraintId = NewType('ConstraintId', str)



class _Branch(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.key = clsname.lower()[:-6].capitalize()
        klass.__doc__ = klass.__doc__ or ""
        return klass

class Branch(metaclass=_Branch):
    key = "Branch"
    arg_types = {"parent": True, "tree": False}

    def __init__(self, parent, tree):
        self.parent = parent
        self.tree = tree
    
    @staticmethod
    def from_value(value: Union[str, _Branch], parent, tree):
        if isinstance(value, _Branch):
            return value
        current_module = sys.modules[__name__]
        clas = getattr(current_module, f"{value.lower().capitalize()}Branch", None)
        if clas is None:
            raise ValueError(f"Unknown leaf behavior class: {value}")
        return clas(parent, tree)

    def __repr__(self):
        return self.key
    def __str__(self):
        return self.__class__.__name__
    

class RootBranch(Branch):
    ...

class PositiveBranch(Branch):
    ...

class NegativeBranch(Branch):
    ...

class PlausibleBranch(Branch):
    ...

class NullableBranch(Branch):
    ...

class BinaryBranch(PlausibleBranch):
    ...

class MultiplicityBranch(Branch):
    ...

class GroupingBranch(MultiplicityBranch):
    ...

