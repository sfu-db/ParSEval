from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
import hashlib
import random as _random
import re
import string as _string
from typing import Any, Dict, Optional, Set

from parseval.dtype import TypeFamily, parse_date, parse_datetime, parse_time


@dataclass
class ValueSpace:
    """A narrowed space of valid concrete values for one variable or column."""

    family: TypeFamily = TypeFamily.TEXT
    min_val: Optional[Any] = None
    max_val: Optional[Any] = None
    min_inclusive: bool = True
    max_inclusive: bool = True
    equals: Optional[Any] = None
    not_equals: Set[Any] = field(default_factory=set)
    allowed: Optional[Set[Any]] = None
    must_null: bool = False
    not_null: bool = False
    like_pattern: Optional[str] = None
    like_case_insensitive: bool = False
    max_length: Optional[int] = None
    temporal_components: Dict[str, "ValueSpace"] = field(default_factory=dict)

    def is_empty(self) -> bool:
        if self.must_null and self.not_null:
            return True
        if self.must_null:
            return False
        if self.equals is not None:
            return not self._candidate_valid(self.equals)
        if self.min_val is not None and self.max_val is not None:
            bounds = _comparable_pair(self.min_val, self.max_val)
            if bounds is None:
                return True
            min_val, max_val = bounds
            if min_val > max_val:
                return True
            if min_val == max_val and (
                not self.min_inclusive or not self.max_inclusive
            ):
                return True
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
        if any(space.is_empty() for space in self.temporal_components.values()):
            return True
        return False

    def pick(self, hint: Any = None, rng: _random.Random | None = None) -> Any:
        if self.must_null:
            return None
        if self.equals is not None:
            return self.equals if self._candidate_valid(self.equals) else None
        if self.allowed is not None:
            return self._first_valid(sorted(self.allowed, key=repr))
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric(rng=rng)
        if self.family == TypeFamily.TEXT:
            return self._pick_text(hint=hint, rng=rng)
        if self.family in (TypeFamily.DATE, TypeFamily.DATETIME, TypeFamily.TIME):
            return self._pick_temporal(rng=rng)
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
        bounded_value = self._bounded_value(value)
        if self.min_val is not None:
            comparable = _comparable_pair(bounded_value, self.min_val)
            if comparable is None:
                return False
            candidate, minimum = comparable
            if candidate < minimum:
                return False
            if candidate == minimum and not self.min_inclusive:
                return False
        if self.max_val is not None:
            comparable = _comparable_pair(bounded_value, self.max_val)
            if comparable is None:
                return False
            candidate, maximum = comparable
            if candidate > maximum:
                return False
            if candidate == maximum and not self.max_inclusive:
                return False
        if self.max_length is not None and isinstance(value, str):
            if len(value) > self.max_length:
                return False
        if self.like_pattern is not None and not _matches_like(
            str(value),
            self.like_pattern,
            case_insensitive=self.like_case_insensitive,
        ):
            return False
        if not self._temporal_components_valid(value):
            return False
        return True

    def component(self, name: str) -> "ValueSpace":
        return self.temporal_components.setdefault(
            name,
            ValueSpace(family=TypeFamily.INTEGER),
        )

    def _bounded_value(self, value: Any) -> Any:
        if self.family == TypeFamily.DATETIME:
            if isinstance(value, datetime):
                return value
            return parse_datetime(value) or value
        if self.family == TypeFamily.DATE:
            if isinstance(value, date) and not isinstance(value, datetime):
                return value
            return parse_date(value) or value
        if self.family == TypeFamily.TIME:
            if isinstance(value, dt_time):
                return value
            return parse_time(value) or value
        if self.family == TypeFamily.TEXT and self._has_temporal_bound():
            if isinstance(self.min_val, datetime) or isinstance(self.max_val, datetime):
                return parse_datetime(value) or value
            if isinstance(self.min_val, date) or isinstance(self.max_val, date):
                return parse_date(value) or value
            if isinstance(self.min_val, dt_time) or isinstance(self.max_val, dt_time):
                return parse_time(value) or value
        return value

    def _temporal_components_valid(self, value: Any) -> bool:
        if not self.temporal_components:
            return True
        parsed = self._bounded_value(value)
        if not isinstance(parsed, (date, datetime, dt_time)):
            return True
        for name, space in self.temporal_components.items():
            component = _temporal_component(parsed, name)
            if component is None or not space._candidate_valid(component):
                return False
        return True

    def _first_valid(self, candidates) -> Any:
        for candidate in candidates:
            if self._candidate_valid(candidate):
                return candidate
        return None

    def _pick_numeric(self, rng: _random.Random | None = None) -> Any:
        lo = self.min_val if self.min_val is not None else min(self.max_val - 100, 1) if self.max_val is not None else 1
        hi = self.max_val if self.max_val is not None else max(self.min_val + 100, lo + 100) if self.min_val is not None else lo + 100
        if lo > hi:
            return None
        if self.family == TypeFamily.INTEGER:
            lo = int(lo)
            hi = int(hi)
            if self.min_val is not None and not self.min_inclusive:
                lo += 1
            if self.max_val is not None and not self.max_inclusive:
                hi -= 1
            if rng is not None:
                if lo > hi:
                    return None
                for _ in range(50):
                    candidate = rng.randint(lo, hi)
                    if self._candidate_valid(candidate):
                        return candidate
                return None
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
            if rng is not None:
                lo_f = float(lo)
                hi_f = float(hi)
                if lo_f > hi_f:
                    return None
                for _ in range(50):
                    candidate = rng.uniform(lo_f, hi_f)
                    if self._candidate_valid(candidate):
                        return candidate
                return None
            candidates = []
            if self.min_val is not None:
                candidates.append(self.min_val)
            if self.max_val is not None and self.max_val != self.min_val:
                candidates.append(self.max_val)
            if _numeric_like(self.min_val) and _numeric_like(self.max_val):
                candidates.append((float(self.min_val) + float(self.max_val)) / 2)
            if not candidates:
                candidates = [lo, hi]
            for candidate in candidates:
                if self._candidate_valid(candidate):
                    return candidate
            base = float(candidates[0])
            lo_f, hi_f = float(lo), float(hi)
            for offset in range(1, int(hi_f - lo_f) + 1):
                for try_val in (base + offset, base - offset):
                    if lo_f <= try_val <= hi_f and self._candidate_valid(try_val):
                        return try_val
        return None

    def _pick_text(self, hint: Any = None, rng: _random.Random | None = None) -> Optional[str]:
        length = min(self.max_length or 10, 10)
        has_numeric_min = self.min_val is not None and isinstance(self.min_val, (int, float))
        has_numeric_max = self.max_val is not None and isinstance(self.max_val, (int, float))
        if has_numeric_min or has_numeric_max:
            lo = int(self.min_val) if has_numeric_min else int(self.max_val) - 100
            hi = int(self.max_val) if has_numeric_max else int(self.min_val) + 1000
            if rng is not None:
                for _ in range(50):
                    text = str(rng.randint(lo, hi))
                    if self._candidate_valid(text):
                        return text
                return None
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
        if rng is not None:
            for _ in range(50):
                budget = rng.randint(1, length)
                prefix = base[:budget]
                remaining = length - len(prefix)
                if remaining > 0:
                    suffix = "".join(rng.choices(_string.ascii_lowercase, k=remaining))
                    candidate = prefix + suffix
                else:
                    candidate = prefix
                if candidate not in self.not_equals and self._candidate_valid(candidate):
                    return candidate
            return None
        i = 1
        while base in self.not_equals:
            base = f"val_{i}"[:length]
            if not base:
                base = "v"
            i += 1
        return base if self._candidate_valid(base) else None

    def _pick_temporal(self, rng: _random.Random | None = None) -> Any:
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
            if rng is not None:
                for _ in range(50):
                    try:
                        val = date(1970, 1, 1) + timedelta(days=rng.randint(lo, hi))
                        if self._candidate_valid(val):
                            return val
                    except (ValueError, OverflowError):
                        continue
                return None
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
        component_candidate = self._pick_temporal_from_components()
        if component_candidate is not None:
            candidates.append(component_candidate)
        if self.min_val is not None:
            candidates.append(self.min_val)
            if not self.min_inclusive:
                shifted = _next_temporal(self.min_val)
                if shifted is not None:
                    candidates.append(shifted)
        if self.max_val is not None and self.max_val != self.min_val:
            candidates.append(self.max_val)
            if not self.max_inclusive:
                shifted = _previous_temporal(self.max_val)
                if shifted is not None:
                    candidates.append(shifted)
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
        if rng is not None:
            for _ in range(50):
                offset = rng.randint(0, 1825)
                sign = -1 if rng.choice([True, False]) else 1
                try:
                    val = date(2024, 6, 15) + timedelta(days=sign * offset)
                    if self._candidate_valid(val):
                        return val
                except (ValueError, OverflowError):
                    continue
            return None
        base = date(2024, 6, 15)
        for offset in range(0, 366):
            for candidate in (base + timedelta(days=offset), base - timedelta(days=offset)):
                if self._candidate_valid(candidate):
                    return candidate
        return None

    def _pick_temporal_from_components(self) -> Any:
        if not self.temporal_components:
            return None
        year = self._component_pick("year", 2024)
        month = self._component_pick("month", 1)
        day = self._component_pick("day", 1)
        hour = self._component_pick("hour", 0)
        minute = self._component_pick("minute", 0)
        second = self._component_pick("second", 0)
        try:
            if self.family == TypeFamily.TIME:
                return dt_time(hour, minute, second)
            if self.family == TypeFamily.DATE:
                return date(year, month, day)
            return datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None

    def _component_pick(self, name: str, default: int) -> int:
        space = self.temporal_components.get(name)
        if space is None:
            return default
        value = space.pick()
        return default if value is None else int(value)

    def _has_temporal_bound(self) -> bool:
        return isinstance(self.min_val, (date, datetime, dt_time)) or isinstance(
            self.max_val, (date, datetime, dt_time)
        )

    def narrow_min(self, val: Any, inclusive: bool = True) -> None:
        if self.min_val is None or val > self.min_val:
            self.min_val = val
            self.min_inclusive = inclusive
        elif val == self.min_val and not inclusive:
            self.min_inclusive = False

    def narrow_max(self, val: Any, inclusive: bool = True) -> None:
        if self.max_val is None or val < self.max_val:
            self.max_val = val
            self.max_inclusive = inclusive
        elif val == self.max_val and not inclusive:
            self.max_inclusive = False

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


def _matches_like(value: str, pattern: str, *, case_insensitive: bool = False) -> bool:
    regex = "^" + re.escape(pattern).replace("%", ".*").replace("_", ".") + "$"
    flags = re.IGNORECASE if case_insensitive else 0
    return re.match(regex, value, flags) is not None


def _numeric_like(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return re.fullmatch(r"-?\d+(?:\.\d+)?", value) is not None
    return False


def _comparable_pair(left: Any, right: Any) -> Optional[tuple[Any, Any]]:
    if _numeric_like(left) and _numeric_like(right):
        if isinstance(left, float) or isinstance(right, float):
            return float(left), float(right)
        return int(left), int(right)
    if isinstance(left, datetime) and isinstance(right, datetime):
        return left, right
    if (
        isinstance(left, date)
        and not isinstance(left, datetime)
        and isinstance(right, date)
        and not isinstance(right, datetime)
    ):
        return left, right
    if isinstance(left, dt_time) and isinstance(right, dt_time):
        return left, right
    if isinstance(left, str) and isinstance(right, str):
        return left, right
    return None


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


def _next_temporal(value: Any) -> Optional[Any]:
    if isinstance(value, datetime):
        return value + timedelta(microseconds=1)
    if isinstance(value, date):
        return value + timedelta(days=1)
    if isinstance(value, dt_time):
        base = datetime.combine(date(2000, 1, 1), value)
        return (base + timedelta(microseconds=1)).time()
    return None


def _previous_temporal(value: Any) -> Optional[Any]:
    if isinstance(value, datetime):
        return value - timedelta(microseconds=1)
    if isinstance(value, date):
        return value - timedelta(days=1)
    if isinstance(value, dt_time):
        base = datetime.combine(date(2000, 1, 1), value)
        return (base - timedelta(microseconds=1)).time()
    return None


def _temporal_component(value: Any, name: str) -> Optional[int]:
    if name == "year" and isinstance(value, (date, datetime)):
        return value.year
    if name == "month" and isinstance(value, (date, datetime)):
        return value.month
    if name == "day" and isinstance(value, (date, datetime)):
        return value.day
    if name == "hour" and isinstance(value, (datetime, dt_time)):
        return value.hour
    if name == "minute" and isinstance(value, (datetime, dt_time)):
        return value.minute
    if name == "second" and isinstance(value, (datetime, dt_time)):
        return value.second
    return None


def _temporal_to_text(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dt_time):
        return value.isoformat()
    return None
