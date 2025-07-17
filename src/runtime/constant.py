from enum import auto, Enum
from typing import NewType

PARSEVAL_NO_SOLUTIONS = 'No Solutions'
PARSEVAL_GAVE_UP = 'Gave up'
PARSEVAL_SAT = 'sat'

OperatorKey = NewType('OperatorKey', str)
OperatorId = NewType('OperatorId', str)
ConstraintId = NewType('ConstraintId', str)

class BranchType(Enum):
    """Represents different types of branches in the AST.
    Attributes:
        NEGATIVE: Branch that evaluates to false
        POSITIVE: Branch that evaluates to true
        STRAIGHT: Branch that doesn't change evaluation
        ROOT: Root node of the AST
        PLAUSIBLE: Branch that is plausible
    """

    NEGATIVE = 0
    POSITIVE = 1    
    PLAUSIBLE = 2
    RPLAUSIBLE = 3
    ROOT = 4
    UNREACHABLE = 5
    NULLABLE = 6
    SIZE = 7
    def __bool__(self):
        return self in {BranchType.POSITIVE}
    
    def __and__(self, other):
        if not isinstance(other, BranchType):
            other = BranchType.from_value(other)
        if self == BranchType.ROOT:
            return other
        elif self.value  and other.value:
            return other
        else:
            return BranchType.NEGATIVE
    
    def __xor__(self, other):
        if not isinstance(other, BranchType):
            other = BranchType.from_value(other)
        if self == BranchType.ROOT:
            return other
       
        return BranchType.from_value(max(self.value, other.value))
    
    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):
            return value
        if isinstance(value, bool):
            return cls.POSITIVE if value else cls.NEGATIVE
        if isinstance(value, int):  # Convert int to corresponding enum
            for member in cls:
                if member.value == value:
                    return member
        return cls.ROOT
    def __str__(self):
        return self.name
    def __repr__(self):
        return self.name
    

class Action(Enum):    
    """Represents different actions that can be taken during AST processing.
       
       Attributes:
           UPDATE: Update an existing element
           APPEND: Add a new element
           DONE: Complete the current operation
    """
    UPDATE = auto()
    APPEND = auto()
    DONE = auto()

class CoverageConstraintType(Enum):
    DATA_FORMAT = auto()
    PRIMARY_KEY = auto()
    FOREIGN_KEY = auto()
    CHECK = auto()
    
    GROUPING = auto()
    POSITIVE = auto()
    NEGATIVE = auto()

    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):  # If already a ConstraintType, return it
            return value
        if isinstance(value, str):  # Convert string name to enum
            try:
                return cls[value.upper()]  # Ensure case insensitivity
            except KeyError:
                pass  # Handle error later
        if isinstance(value, int):  # Convert int to corresponding enum
            for member in cls:
                if member.value == value:
                    return member
        raise ValueError(f"Cannot convert {value} to ConstraintType")

class PathConstraintType(Enum):
    SIZE = "size"      # Constraints about table size or row existence
    PATH = "path"      # Constraints about relationships between tables
    VALUE = "value"    # Constraints about specific values
    UNKNOWN = "unknown"


class SmtType(Enum):
    DATA_FORMAT = auto()
    PRIMARY_KEY = auto()
    FOREIGN_KEY = auto()
    CHECK = auto()
    
    GROUPING = auto()
    POSITIVE = auto()
    NEGATIVE = auto()
    @classmethod
    def from_value(cls, value):
        if isinstance(value, cls):  # If already a ConstraintType, return it
            return value
        if isinstance(value, str):  # Convert string name to enum
            try:
                return cls[value.upper()]  # Ensure case insensitivity
            except KeyError:
                pass  # Handle error later
        if isinstance(value, int):  # Convert int to corresponding enum
            for member in cls:
                if member.value == value:
                    return member
        raise ValueError(f"Cannot convert {value} to ConstraintType")
