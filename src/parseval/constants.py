from __future__ import annotations
from typing import Union
from enum import IntEnum, Enum


class PlausibleBit(IntEnum):
    """Bits representing different plausible branches."""

    FALSE = 0  # e.g., if condition is false
    TRUE = 1  # e.g., if condition is true
    ATOM_FALSE = 0
    ATOM_TRUE = 1
    FILTER_FALSE = 0
    FILTER_TRUE = 1
    JOIN_TRUE = 2  # e.g., threr exist tuple in both tables can join
    JOIN_MATCH = 2
    JOIN_LEFT = 3  # e.g., there exist tuple in left table cannot join with right table,
    JOIN_NO_MATCH = 3
    JOIN_RIGHT = 4  # e.g., there exist tuple in right table cannot join with left table
    NULL = 5  # e.g., column is null
    ATOM_NULL = 5
    FILTER_NULL = 5
    DUPLICATE = 6  # e.g., duplicate values exist
    MAX = 7  # e.g., number of max value
    MIN = 8  # e.g., number of  min value
    GROUP_COUNT = 9  # e.g., number of groups
    GROUP_SIZE = 10  # e.g., size of groups(count of rows in each group)
    GROUP_SINGLE = 9
    GROUP_MULTI = 10
    GROUP_NULL = 11
    GROUP_DUPLICATE = 12
    AGGREGATE_SIZE = 13
    HAVING_TRUE = 14  # e.g., having condition is true
    HAVING_PASS = 14
    HAVING_FALSE = 15  # e.g., having condition is false
    HAVING_FAIL = 15
    
    PROJECT = 16  # e.g., projection of a column
    JOIN_NULL = 17
    HAVING_NULL = 18
    CASE_TAKEN = 19
    CASE_ARM_TAKEN = 19
    CASE_SKIPPED = 20
    CASE_ARM_SKIPPED = 20
    EXISTS_TRUE = 21
    EXISTS_FALSE = 22
    IN_MATCH = 23
    IN_NO_MATCH = 24
    DISTINCT_UNIQUE = 25
    DISTINCT_DUPLICATE = 26
    PROJECT_NULL = 27
    PROJECT_NON_NULL = 28
    AGGREGATE_NULL = 29
    AGGREGATE_NON_NULL = 30
    AGG_DISTINCT_NULL_IGNORED = 31
    AGG_DISTINCT_DUPLICATE_ELIMINATED = 32
    AGG_DISTINCT_MULTIPLE_RETAINED = 33
    

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
