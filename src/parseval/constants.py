from __future__ import annotations
from typing import Union
from enum import IntEnum, Enum


class PlausibleBit(IntEnum):
    """Bits representing different plausible branches."""

    FALSE = 0  # e.g., if condition is false
    TRUE = 1  # e.g., if condition is true
    JOIN_TRUE = 2  # e.g., threr exist tuple in both tables can join
    JOIN_LEFT = 3  # e.g., there exist tuple in left table cannot join with right table,
    JOIN_RIGHT = 4  # e.g., there exist tuple in right table cannot join with left table
    NULL = 5  # e.g., column is null
    DUPLICATE = 6  # e.g., duplicate values exist
    MAX = 7  # e.g., number of max value
    MIN = 8  # e.g., number of  min value
    GROUP_COUNT = 9  # e.g., number of groups
    GROUP_SIZE = 10  # e.g., size of groups(count of rows in each group)
    GROUP_NULL = 11
    GROUP_DUPLICATE = 12
    AGGREGATE_SIZE = 13
    HAVING_TRUE = 14  # e.g., having condition is true
    HAVING_FALSE = 15  # e.g., having condition is false
    
    PROJECT = 16  # e.g., projection of a column
    

    @classmethod
    def from_int(cls, value: Union[int, str, bool, PlausibleBit]) -> "PlausibleBit":
        if isinstance(value, PlausibleBit):
            return value
        return cls(int(value))

    def __str__(self):
        return str(self.value)


PBit = PlausibleBit


VALID_PATH_BITS = (PBit.FALSE, PBit.TRUE, PBit.JOIN_TRUE, PBit.GROUP_SIZE, PBit.PROJECT, PBit.AGGREGATE_SIZE, PBit.HAVING_TRUE, PBit.HAVING_FALSE)

def is_valid_path_bit(bit: PlausibleBit) -> bool:
    bit = PBit.from_int(bit)
    return bit in VALID_PATH_BITS

class PlausibleType(Enum):
    """Types of plausible (unexplored) branches."""

    POSITIVE = "positive"  # Branch
    COVERED = "covered"  # Branch already covered
    UNEXPLORED = "unexplored"  # Branch exists but never taken
    INFEASIBLE = "infeasible"  # Branch proven impossible (via constraint solving)
    PENDING = "pending"  # Branch queued for exploration
    TIMEOUT = "timeout"  # Branch exists but solver timed out
    ERROR = "error"  # Branch caused an error during exploration

class BranchType(IntEnum):
    """Types of branch in plausible node."""
    POSITIVE = 1
    NEGATIVE = 0
    UNDECIDED = -1
    
    def __bool__(self):
        return self == BranchType.POSITIVE
    
    
class StepType(Enum):
    ROOT = "root"
    SCAN = "scan"
    JOIN = "join"
    AGGREGATE = "aggregate"
    GROUPBY = "groupby"
    PROJECT = "project"
    FILTER = "filter"
    HAVING = "having"
    SORT = "sort"
    UNION = "union"
    EXCEPT = "except"
    INTERSECT = "intersect"
    
    def __repr__(self):
        return self.name