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
    HAVING_TRUE = 11  # e.g., having condition is true
    HAVING_FALSE = 12  # e.g., having condition is false

    @classmethod
    def from_int(cls, value: Union[int, str, bool, PlausibleBit]) -> "PlausibleBit":
        if isinstance(value, PlausibleBit):
            return value
        return cls(int(value))

    def __str__(self):
        return str(self.value)


PBit = PlausibleBit


class PlausibleType(Enum):
    """Types of plausible (unexplored) branches."""

    POSITIVE = "positive"  # Branch
    COVERED = "covered"  # Branch already covered
    UNEXPLORED = "unexplored"  # Branch exists but never taken
    INFEASIBLE = "infeasible"  # Branch proven impossible (via constraint solving)
    PENDING = "pending"  # Branch queued for exploration
    TIMEOUT = "timeout"  # Branch exists but solver timed out
    ERROR = "error"  # Branch caused an error during exploration
