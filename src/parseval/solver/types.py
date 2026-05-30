"""Shared types for the solver module: ValueSpace, CSP structures, ColumnPredicate."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Set


class TypeFamily(Enum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"


@dataclass
class ValueSpace:
    """The narrowed space of valid values for a variable."""
    family: TypeFamily = TypeFamily.TEXT
    min_val: Optional[Any] = None
    max_val: Optional[Any] = None
    equals: Optional[Any] = None
    not_equals: Set[Any] = field(default_factory=set)
    allowed: Optional[Set[Any]] = None
    must_null: bool = False
    not_null: bool = False
    like_pattern: Optional[str] = None
    max_length: Optional[int] = None

    def is_empty(self) -> bool:
        if self.must_null and self.not_null:
            return True
        if self.must_null:
            return False
        if self.equals is not None:
            if self.equals in self.not_equals:
                return True
            if self.min_val is not None and self.equals < self.min_val:
                return True
            if self.max_val is not None and self.equals > self.max_val:
                return True
            if self.allowed is not None and self.equals not in self.allowed:
                return True
            return False
        if self.min_val is not None and self.max_val is not None:
            if self.min_val > self.max_val:
                return True
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if not valid:
                return True
        return False

    def pick(self) -> Any:
        if self.must_null:
            return None
        if self.equals is not None:
            return self.equals
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            return min(valid) if valid else None
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric()
        elif self.family == TypeFamily.TEXT:
            return self._pick_text()
        elif self.family in (TypeFamily.DATE, TypeFamily.DATETIME):
            return self._pick_temporal()
        elif self.family == TypeFamily.BOOLEAN:
            return True if True not in self.not_equals else False
        return "value"

    def _pick_numeric(self) -> Any:
        lo = self.min_val if self.min_val is not None else 1
        hi = self.max_val if self.max_val is not None else lo + 100
        if lo > hi:
            return None
        mid = (lo + hi) // 2 if isinstance(lo, int) else (lo + hi) / 2
        if isinstance(lo, int):
            for offset in range(hi - lo + 1):
                for try_val in (mid + offset, mid - offset):
                    if lo <= try_val <= hi and try_val not in self.not_equals:
                        return try_val
        else:
            for try_val in (mid, lo, hi):
                if try_val not in self.not_equals:
                    return try_val
        return None

    def _pick_text(self) -> str:
        if self.like_pattern:
            return self.like_pattern.replace("%", "x").replace("_", "a")
        length = min(self.max_length or 10, 10)
        base = "value"[:length]
        i = 1
        while base in self.not_equals:
            base = f"val_{i}"[:length]
            i += 1
        return base

    def _pick_temporal(self) -> Any:
        if self.min_val and isinstance(self.min_val, (date, datetime)):
            return self.min_val
        return date(2024, 6, 15)

    def narrow_min(self, val: Any) -> None:
        if self.min_val is None or val > self.min_val:
            self.min_val = val

    def narrow_max(self, val: Any) -> None:
        if self.max_val is None or val < self.max_val:
            self.max_val = val

    def narrow_eq(self, val: Any) -> None:
        self.equals = val

    def narrow_neq(self, val: Any) -> None:
        self.not_equals.add(val)

    def narrow_in(self, values: Set[Any]) -> None:
        if self.allowed is None:
            self.allowed = values
        else:
            self.allowed &= values


@dataclass
class CSPVariable:
    """A column variable in the CSP solver."""
    name: str
    table: str
    column: str
    space: ValueSpace
    assigned: Optional[Any] = None


@dataclass
class CSPConstraint:
    """A relationship between two CSP variables."""
    kind: str
    left: str
    right: str


@dataclass
class ColumnPredicate:
    """A lowered constraint on a single column."""
    table: str
    column: str
    op: str
    value: Any
