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
    StorageLiteral,
)

_TYPE_SERVICE = TypeService()
_PERMISSIVE_DIALECTS = frozenset({"sqlite", "mysql"})


class CoercionError(ValueError):
    """Raised when a predicate literal cannot be soundly coerced."""


def is_permissive_dialect(dialect: str | None) -> bool:
    key = (dialect or "sqlite").strip().lower()
    return (
        key in _PERMISSIVE_DIALECTS
        or key.startswith("sqlite")
        or key.startswith("mysql")
    )


def coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> Any:
    """Convert a concrete value into the requested datatype or raise."""
    if isinstance(value, StorageLiteral):
        return value
    profile = _TYPE_SERVICE.profile_datatype(DataType.build(datatype), dialect=dialect)
    adapter = _TYPE_SERVICE.registry.resolve(profile.datatype, profile.dialect)
    return adapter.coerce_in(value, profile)


def storage_key(value: Any, datatype: DataType, dialect: str | None = None) -> Any:
    """Project a concrete value to the database's storage/index equivalence key."""
    if isinstance(value, StorageLiteral):
        value = str(value)
    profile = _TYPE_SERVICE.profile_datatype(DataType.build(datatype), dialect=dialect)
    adapter = _TYPE_SERVICE.registry.resolve(profile.datatype, profile.dialect)
    return adapter.storage_key(value, profile)


def coerce_literal_value(
    value: Any,
    datatype: DataType | None,
    dialect: str | None = None,
    *,
    for_equality: bool = False,
) -> Any:
    """Coerce a Python literal into a column/variable dtype for predicate solving.

    Raises :class:`CoercionError` when coercion is unsupported or unparseable.
    """
    if value is None or datatype is None:
        return value
    if isinstance(value, StorageLiteral):
        return value

    dtype = DataType.build(datatype)
    family = type_family(dtype)

    if (
        for_equality
        and family == TypeFamily.DATETIME
        and isinstance(value, str)
        and "." in value
    ):
        return StorageLiteral(value)

    if family == TypeFamily.TEXT:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not is_permissive_dialect(dialect):
                raise CoercionError("unsupported_mixed_sort_literal:numeric:text")
            return str(value)
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        return value

    if family == TypeFamily.INTEGER:
        if isinstance(value, str) and not is_permissive_dialect(dialect):
            raise CoercionError("unsupported_mixed_sort_literal:text:numeric")
        if isinstance(value, bool) or (
            not isinstance(value, (int, float, str))
        ):
            raise CoercionError("unparseable_numeric_literal")
        if isinstance(value, (int, float)):
            return int(value)
        try:
            return int(value)
        except ValueError:
            try:
                return int(float(value))
            except ValueError as exc:
                raise CoercionError("unparseable_numeric_literal") from exc

    if family == TypeFamily.DECIMAL:
        if isinstance(value, str) and not is_permissive_dialect(dialect):
            raise CoercionError("unsupported_mixed_sort_literal:text:numeric")
        if isinstance(value, bool) or (
            not isinstance(value, (int, float, str))
        ):
            raise CoercionError("unparseable_numeric_literal")
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except ValueError as exc:
            raise CoercionError("unparseable_numeric_literal") from exc

    if family == TypeFamily.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in ("1", "true", "t", "yes", "y"):
                return True
            if normalized in ("0", "false", "f", "no", "n"):
                return False
        raise CoercionError("unparseable_boolean_literal")

    if family == TypeFamily.DATE:
        parsed = parse_date(value)
        if parsed is None:
            raise CoercionError("unparseable_temporal_literal")
        return parsed
    if family == TypeFamily.DATETIME:
        parsed = parse_datetime(value)
        if parsed is None:
            raise CoercionError("unparseable_temporal_literal")
        return parsed
    if family == TypeFamily.TIME:
        parsed = parse_time(value)
        if parsed is None:
            raise CoercionError("unparseable_temporal_literal")
        return parsed

    return value


def can_coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> bool:
    try:
        coerce_value(value, datatype, dialect=dialect)
    except (TypeError, ValueError, AttributeError):
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
    "CoercionError",
    "can_coerce_value",
    "coerce_literal_value",
    "coerce_reference_value",
    "coerce_value",
    "is_permissive_dialect",
    "storage_key",
    "values_equivalent",
]
