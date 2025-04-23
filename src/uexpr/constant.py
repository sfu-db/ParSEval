from enum import auto, Enum

PARSEVAL_NO_SOLUTIONS = 'No Solutions'
PARSEVAL_GAVE_UP = 'Gave up'
PARSEVAL_SAT = 'sat'


class BranchType(Enum):
    NEGATIVE = 0
    POSITIVE = 1
    STRAIGHT = 2
    ROOT = 3
    def __bool__(self):
        return self in {BranchType.POSITIVE, BranchType.STRAIGHT}
    def __and__(self, other):
        if not isinstance(other, BranchType):
            other = BranchType.from_value(other)        
        if self == BranchType.ROOT:
            return other
        elif self and other:
            return other
        else:
            return BranchType.NEGATIVE
    
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
class Action(Enum):    
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


# def _create_branch_info(identifier, operator_key, is_positive) -> BranchType:
#     if identifier == 'ROOT':
#         return BranchType.from_value(is_positive)
#     if operator_key in {'project', 'aggregate'}:
#         return BranchType.STRAIGHT
#     return BranchType.from_value(BranchType.POSITIVE and is_positive)


# print(_create_branch_info('ROOT', 'project', True))
# print(_create_branch_info('filter', 'project', True))
# print(_create_branch_info('filter', 'filter', True))
# print(_create_branch_info('filter', 'filter', False))

# print(BranchType.from_value(1) & BranchType.from_value(0))
# print(BranchType.from_value(1) & BranchType.from_value(2))
# print(BranchType.from_value(2) & BranchType.from_value(1))
# print(BranchType.from_value(1) & BranchType.from_value(1))


