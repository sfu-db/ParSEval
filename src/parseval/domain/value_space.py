from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
import re
from typing import Any, Optional, Set

from parseval.dtype import TypeFamily


@dataclass
class ValueSpace:
    """A narrowed space of valid concrete values for one variable or column."""

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
            if self.like_pattern is not None and not _matches_like(
                str(self.equals), self.like_pattern
            ):
                return True
            return False
        if self.min_val is not None and self.max_val is not None:
            if self.min_val > self.max_val:
                return True
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if self.like_pattern is not None:
                valid = {
                    value for value in valid
                    if _matches_like(str(value), self.like_pattern)
                }
            if not valid:
                return True
        if self.family == TypeFamily.BOOLEAN:
            candidates = {True, False}
            if self.allowed is not None:
                candidates &= self.allowed
            if not candidates - self.not_equals:
                return True
        return False

    def pick(self) -> Any:
        if self.must_null:
            return None
        if self.equals is not None:
            if self.equals in self.not_equals:
                return None
            if self.like_pattern is not None and not _matches_like(
                str(self.equals), self.like_pattern
            ):
                return None
            return self.equals
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if self.like_pattern is not None:
                valid = {
                    value for value in valid
                    if _matches_like(str(value), self.like_pattern)
                }
            return next(iter(valid)) if valid else None
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric()
        if self.family == TypeFamily.TEXT:
            return self._pick_text()
        if self.family in (TypeFamily.DATE, TypeFamily.DATETIME, TypeFamily.TIME):
            return self._pick_temporal()
        if self.family == TypeFamily.BOOLEAN:
            candidates = {True, False}
            if self.allowed is not None:
                candidates &= self.allowed
            for value in (True, False):
                if value in candidates and value not in self.not_equals:
                    return value
            return None
        return None

    def _pick_numeric(self) -> Any:
        lo = self.min_val if self.min_val is not None else min(self.max_val - 100, 1) if self.max_val is not None else 1
        hi = self.max_val if self.max_val is not None else max(self.min_val + 100, lo + 100) if self.min_val is not None else lo + 100
        if lo > hi:
            return None
        if self.family == TypeFamily.INTEGER:
            lo = int(lo)
            hi = int(hi)
            mid = (lo + hi) // 2
            for offset in range(hi - lo + 1):
                for try_val in (mid + offset, mid - offset):
                    if lo <= try_val <= hi and try_val not in self.not_equals:
                        return try_val
        else:
            mid = (lo + hi) / 2
            for try_val in (mid, lo, hi):
                if try_val not in self.not_equals:
                    return try_val
        return None

    def _pick_text(self) -> Optional[str]:
        length = min(self.max_length or 10, 10)
        has_numeric_min = self.min_val is not None and isinstance(self.min_val, (int, float))
        has_numeric_max = self.max_val is not None and isinstance(self.max_val, (int, float))
        if has_numeric_min or has_numeric_max:
            lo = int(self.min_val) if has_numeric_min else int(self.max_val) - 100
            hi = int(self.max_val) if has_numeric_max else int(self.min_val) + 1000
            mid = (lo + hi) // 2
            candidates = []
            for offset in range(hi - lo + 1):
                for try_val in (mid + offset, mid - offset):
                    if lo <= try_val <= hi:
                        candidates.append(try_val)
            for try_val in candidates:
                text = str(try_val)
                if text in self.not_equals:
                    continue
                if self.like_pattern is not None and not _matches_like(text, self.like_pattern):
                    continue
                return text
            return None
        if self._has_temporal_bound():
            candidate = self.min_val if self.min_val is not None else self.max_val
            text = _temporal_to_text(candidate)
            if text is not None:
                return text
        if self.like_pattern:
            return self.like_pattern.replace("%", "x").replace("_", "a")
        base = "value"[:length]
        if self.min_val is not None and isinstance(self.min_val, str):
            base = self.min_val + "a"
        if self.max_val is not None and isinstance(self.max_val, str):
            if base > self.max_val:
                base = self.max_val
        base = base[:length]
        if not base:
            base = "v"
        i = 1
        while base in self.not_equals:
            base = f"val_{i}"[:length]
            if not base:
                base = "v"
            i += 1
        return base

    def _pick_temporal(self) -> Any:
        if self.like_pattern:
            prefix = self.like_pattern.replace("%", "").replace("_", "")
            if len(prefix) >= 4:
                try:
                    if len(prefix) <= 4:
                        return date(int(prefix), 1, 1)
                    if len(prefix) <= 7:
                        return date.fromisoformat(prefix + "-01")
                    return date.fromisoformat(prefix[:10])
                except (ValueError, IndexError):
                    pass
        if self.min_val is not None and isinstance(self.min_val, (int, float)):
            lo = int(self.min_val)
            hi = (
                int(self.max_val)
                if self.max_val is not None and isinstance(self.max_val, (int, float))
                else lo + 365
            )
            mid = (lo + hi) // 2
            try:
                return date(1970, 1, 1) + timedelta(days=mid)
            except (ValueError, OverflowError):
                pass
        if self.min_val is not None:
            if isinstance(self.min_val, datetime):
                return self.min_val
            if isinstance(self.min_val, date):
                return self.min_val
            if isinstance(self.min_val, dt_time):
                return self.min_val
            if isinstance(self.min_val, str):
                try:
                    return date.fromisoformat(self.min_val[:10])
                except (ValueError, IndexError):
                    pass
        if self.max_val is not None and isinstance(self.max_val, dt_time):
            return self.max_val
        if self.max_val is not None and isinstance(self.max_val, date):
            return self.max_val
        return date(2024, 6, 15)

    def _has_temporal_bound(self) -> bool:
        return isinstance(self.min_val, (date, datetime, dt_time)) or isinstance(
            self.max_val, (date, datetime, dt_time)
        )

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
            self.allowed = set(values)
        else:
            self.allowed &= set(values)


__all__ = ["ValueSpace"]


def _matches_like(value: str, pattern: str) -> bool:
    regex = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    return re.match(regex, value) is not None


def _temporal_to_text(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dt_time):
        return value.isoformat()
    return None
