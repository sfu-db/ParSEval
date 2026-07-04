from __future__ import annotations

from typing import Any

from parseval.dtype import (
    DataType,
    TypeFamily,
    TypeService,
    parse_date,
    parse_datetime,
    parse_time,
    type_family,
    StorageLiteral
)

_TYPE_SERVICE = TypeService()


def coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> Any:
    """Convert a concrete value into the requested datatype or raise."""
    if isinstance(value, StorageLiteral):
        return value
    profile = _TYPE_SERVICE.profile_datatype(DataType.build(datatype), dialect=dialect)
    adapter = _TYPE_SERVICE.registry.resolve(profile.datatype, profile.dialect)
    return adapter.coerce_in(value, profile)


def coerce_literal_value(
    value: Any,
    datatype: DataType | None,
    dialect: str | None = None,
) -> Any:
    """Best-effort literal coercion for solver predicate lowering.

    Unlike :func:`coerce_value`, this function never raises for uncoercible
    values. If the value cannot be converted, the original value is returned
    so the caller can decide whether the expression is still supportable.
    """
    del dialect
    if isinstance(value, StorageLiteral):
        return value
    if datatype is None or value is None:
        return value
    dtype = DataType.build(datatype)
    family = type_family(dtype)
    if family == TypeFamily.INTEGER:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return int(float(value))
                except ValueError:
                    return value
    if family == TypeFamily.DECIMAL:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
    if family == TypeFamily.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in ("1", "true", "t", "yes", "y"):
                return True
            if normalized in ("0", "false", "f", "no", "n"):
                return False
    if family == TypeFamily.DATE:
        parsed = parse_date(value)
        return parsed if parsed is not None else value
    if family == TypeFamily.DATETIME:
        parsed = parse_datetime(value)
        return parsed if parsed is not None else value
    if family == TypeFamily.TIME:
        parsed = parse_time(value)
        return parsed if parsed is not None else value
    return value


def can_coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> bool:
    try:
        coerce_value(value, datatype, dialect=dialect)
    except Exception:
        return False
    return True


def coerce_reference_value(
    value: Any, target_datatype: DataType, dialect: str | None = None
) -> Any:
    """Coerce a referenced parent value into the child column's datatype."""
    return coerce_value(value, target_datatype, dialect=dialect)


def values_equivalent(
    left: Any,
    left_datatype: DataType,
    right: Any,
    right_datatype: DataType,
    left_dialect: str | None = None,
    right_dialect: str | None = None,
) -> bool:
    """Compare two values after dialect/type projection."""
    left_profile = _TYPE_SERVICE.profile_datatype(left_datatype, dialect=left_dialect)
    right_profile = _TYPE_SERVICE.profile_datatype(
        right_datatype, dialect=right_dialect
    )
    right_adapter = _TYPE_SERVICE.registry.resolve(
        right_profile.datatype, right_profile.dialect
    )
    return right_adapter.equivalent(left, left_profile, right, right_profile)


__all__ = [
    "can_coerce_value",
    "coerce_literal_value",
    "coerce_reference_value",
    "coerce_value",
    "values_equivalent",
]
