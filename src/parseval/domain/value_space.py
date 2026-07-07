from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
import hashlib
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
            return not self._candidate_valid(self.equals)
        if self.min_val is not None and self.max_val is not None:
            try:
                if self.min_val > self.max_val:
                    return True
            except TypeError:
                pass
        if self.allowed is not None:
            valid = {value for value in self.allowed if self._candidate_valid(value)}
            if not valid:
                return True
        if self.family == TypeFamily.BOOLEAN:
            candidates = {True, False}
            if self.allowed is not None:
                candidates &= self.allowed
            if not candidates - self.not_equals:
                return True
        return False

    def pick(self, hint: Any = None) -> Any:
        if self.must_null:
            return None
        if self.equals is not None:
            return self.equals if self._candidate_valid(self.equals) else None
        if self.allowed is not None:
            return self._first_valid(sorted(self.allowed, key=repr))
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric()
        if self.family == TypeFamily.TEXT:
            return self._pick_text(hint=hint)
        if self.family in (TypeFamily.DATE, TypeFamily.DATETIME, TypeFamily.TIME):
            return self._pick_temporal()
        if self.family == TypeFamily.BOOLEAN:
            candidates = {True, False}
            if self.allowed is not None:
                candidates &= self.allowed
            for value in (True, False):
                if value in candidates and self._candidate_valid(value):
                    return value
            return None
        return None

    def _candidate_valid(self, value: Any) -> bool:
        if value is None:
            return not self.not_null
        if value in self.not_equals:
            return False
        if self.equals is not None and value != self.equals:
            return False
        if self.allowed is not None and value not in self.allowed:
            return False
        try:
            if self.min_val is not None and value < self.min_val:
                return False
            if self.max_val is not None and value > self.max_val:
                return False
        except TypeError:
            pass
        if self.max_length is not None and isinstance(value, str):
            if len(value) > self.max_length:
                return False
        if self.like_pattern is not None and not _matches_like(str(value), self.like_pattern):
            return False
        return True

    def _first_valid(self, candidates) -> Any:
        for candidate in candidates:
            if self._candidate_valid(candidate):
                return candidate
        return None

    def _pick_numeric(self) -> Any:
        lo = self.min_val if self.min_val is not None else min(self.max_val - 100, 1) if self.max_val is not None else 1
        hi = self.max_val if self.max_val is not None else max(self.min_val + 100, lo + 100) if self.min_val is not None else lo + 100
        if lo > hi:
            return None
        if self.family == TypeFamily.INTEGER:
            lo = int(lo)
            hi = int(hi)
            candidates = []
            if self.min_val is not None:
                candidates.append(lo)
            if self.max_val is not None and hi != lo:
                candidates.append(hi)
            if not candidates:
                candidates.append(lo)
            for candidate in candidates:
                if lo <= candidate <= hi and self._candidate_valid(candidate):
                    return candidate
            base = candidates[0]
            for offset in range(1, hi - lo + 1):
                for try_val in (base + offset, base - offset):
                    if lo <= try_val <= hi and self._candidate_valid(try_val):
                        return try_val
        else:
            candidates = []
            if self.min_val is not None:
                candidates.append(self.min_val)
            if self.max_val is not None and self.max_val != self.min_val:
                candidates.append(self.max_val)
            if not candidates:
                candidates = [lo, hi]
            for candidate in candidates:
                if self._candidate_valid(candidate):
                    return candidate
        return None

    def _pick_text(self, hint: Any = None) -> Optional[str]:
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
                if self._candidate_valid(text):
                    return text
            return None
        if self._has_temporal_bound():
            candidate = self.min_val if self.min_val is not None else self.max_val
            text = _temporal_to_text(candidate)
            if text is not None and self._candidate_valid(text):
                return text
        if self.like_pattern:
            return self._first_valid(_like_candidates(self.like_pattern))
        base = _text_hint(hint, length) if hint is not None else "value"[:length]
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
        return base if self._candidate_valid(base) else None

    def _pick_temporal(self) -> Any:
        if self.like_pattern:
            prefix = self.like_pattern.replace("%", "").replace("_", "")
            if len(prefix) >= 4:
                try:
                    if len(prefix) <= 4:
                        candidate = date(int(prefix), 1, 1)
                    elif len(prefix) <= 7:
                        candidate = date.fromisoformat(prefix + "-01")
                    else:
                        candidate = date.fromisoformat(prefix[:10])
                    if self._candidate_valid(candidate):
                        return candidate
                except (ValueError, IndexError):
                    pass
        if self.min_val is not None and isinstance(self.min_val, (int, float)):
            lo = int(self.min_val)
            hi = (
                int(self.max_val)
                if self.max_val is not None and isinstance(self.max_val, (int, float))
                else lo + 365
            )
            candidates = [lo]
            if hi != lo:
                candidates.append(hi)
            for candidate in candidates:
                try:
                    val = date(1970, 1, 1) + timedelta(days=candidate)
                    if self._candidate_valid(val):
                        return val
                except (ValueError, OverflowError):
                    continue
            for offset in range(1, hi - lo + 1):
                for try_days in (candidates[0] + offset, candidates[0] - offset):
                    try:
                        val = date(1970, 1, 1) + timedelta(days=try_days)
                        if self._candidate_valid(val):
                            return val
                    except (ValueError, OverflowError):
                        continue
        candidates = []
        if self.min_val is not None:
            candidates.append(self.min_val)
        if self.max_val is not None and self.max_val != self.min_val:
            candidates.append(self.max_val)
        for candidate in candidates:
            if isinstance(candidate, datetime):
                if self._candidate_valid(candidate):
                    return candidate
            elif isinstance(candidate, date):
                if self._candidate_valid(candidate):
                    return candidate
            elif isinstance(candidate, dt_time):
                if self._candidate_valid(candidate):
                    return candidate
            elif isinstance(candidate, str):
                try:
                    val = date.fromisoformat(candidate[:10])
                    if self._candidate_valid(val):
                        return val
                except (ValueError, IndexError):
                    pass
        base = date(2024, 6, 15)
        for offset in range(0, 366):
            for candidate in (base + timedelta(days=offset), base - timedelta(days=offset)):
                if self._candidate_valid(candidate):
                    return candidate
        return None

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


def _text_hint(hint: Any, length: int) -> str:
    raw = getattr(hint, "display", None) or str(hint)
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    normalized = normalized or "value"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:6]
    if length <= 0:
        return ""
    if length <= len(digest):
        return digest[:length]
    budget = max(1, length - len(digest) - 1)
    return f"{normalized[:budget]}_{digest}"[:length]


def _like_candidates(pattern: str):
    has_percent = "%" in pattern
    has_underscore = "_" in pattern
    if not has_percent and not has_underscore:
        yield pattern
        return

    max_attempts = 32
    for i in range(max_attempts):
        percent_fill = "x" * i if has_percent else ""
        underscore_fill = chr(ord("a") + (i % 26))
        yield pattern.replace("%", percent_fill).replace("_", underscore_fill)


def _temporal_to_text(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dt_time):
        return value.isoformat()
    return None
