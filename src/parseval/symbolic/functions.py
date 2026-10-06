"""Concrete callbacks for SQL builtins emitted by the frontend.

Each callback receives a tuple of concrete arguments, as described for
``Runtime.SCALAR_FUNCTIONS``. Strict callbacks return NULL for any NULL input;
COALESCE and NULLIF define their own NULL behavior.
"""

from __future__ import annotations

from datetime import date, datetime, time

from parseval.terms.sorts import TIMESTAMP, IntervalValue, parse_iso_temporal_value

_UNIX_EPOCH_JULIAN_DAY = 2440587.5
REFERENCE_DATE = date(2000, 1, 1)
"""The current date of STABLE functions, fixed so that execution is repeatable."""


def _strict(function):
    def evaluate(arguments):
        if any(argument is None for argument in arguments):
            return None
        return function(*arguments)

    return evaluate


def _timestamp(value):
    if isinstance(value, datetime):
        return value
    if value == "now":
        return datetime.combine(REFERENCE_DATE, time())
    if isinstance(value, date):
        return datetime.combine(value, time())
    return parse_iso_temporal_value(value, TIMESTAMP)


def _coalesce(arguments):
    return next((argument for argument in arguments if argument is not None), None)


def _nullif(arguments):
    left, right = arguments
    return None if left is not None and left == right else left


def _round(value, digits=0):
    result = round(value, digits)
    return float(result) if isinstance(value, float) else result


def _extract(unit, value):
    if isinstance(value, IntervalValue):
        fields = {
            "year": value.months // 12,
            "month": value.months % 12,
            "day": value.days,
            "hour": value.microseconds // 3_600_000_000,
            "minute": value.microseconds // 60_000_000 % 60,
            "second": value.microseconds // 1_000_000 % 60,
        }
        return fields[unit]
    if unit in ("dow", "dayofweek"):
        return value.isoweekday() % 7
    if unit in ("doy", "dayofyear"):
        return value.timetuple().tm_yday
    if unit == "quarter":
        return (value.month - 1) // 3 + 1
    if unit == "week":
        return value.isocalendar()[1]
    if unit == "epoch":
        return int((_timestamp(value) - datetime(1970, 1, 1)).total_seconds())
    return getattr(value, unit)


def _time_to_str(value, pattern):
    """strftime with SQL's four-digit years, independent of the C library."""
    value = _timestamp(value)
    return value.strftime(pattern.replace("%Y", f"{value.year:04d}"))


def _age(value):
    # A fixed reference date keeps AGE deterministic across runs.
    today = REFERENCE_DATE
    return today.year - value.year - ((today.month, today.day) < (value.month, value.day))


def _error(arguments):
    raise ValueError("SQL runtime error")


SQL_FUNCTIONS = {
    # A SQL runtime error as an operation, e.g. a scalar subquery with two rows.
    "sql_error": _error,
    "coalesce": _coalesce,
    "nullif": _nullif,
    "round": _strict(_round),
    "date": _strict(lambda value: _timestamp(value).date()),
    "time": _strict(lambda value: _timestamp(value).time()),
    "year": _strict(lambda value: value.year),
    "julianday": _strict(
        lambda value: (_timestamp(value) - datetime(1970, 1, 1)).total_seconds() / 86400
        + _UNIX_EPOCH_JULIAN_DAY
    ),
    "ts_or_ds_to_timestamp": _strict(_timestamp),
    "datediff": _strict(lambda left, right: (_timestamp(left) - _timestamp(right)).days),
    "instr": _strict(lambda text, needle: text.find(needle) + 1),
    "date_part": _strict(lambda unit, value: _extract(unit.casefold(), value)),
    "time_to_str": _strict(_time_to_str),
    "age": _strict(_age),
    "current_timestamp": lambda arguments: datetime.combine(REFERENCE_DATE, time()),
    "curdate": lambda arguments: REFERENCE_DATE,
    "datetime": _strict(lambda value, *modifiers: _timestamp(value)),
    **{
        f"extract_{unit}": _strict(lambda value, unit=unit: _extract(unit, value))
        for unit in (
            "year", "month", "day", "hour", "minute", "second",
            "dow", "dayofweek", "doy", "dayofyear", "quarter", "week", "epoch",
        )
    },
}

__all__ = ["SQL_FUNCTIONS"]
