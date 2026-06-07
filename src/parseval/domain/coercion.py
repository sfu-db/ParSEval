from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from parseval.dtype import DataType, TypeService

_TYPE_SERVICE = TypeService()


def coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> Any:
    """Convert a concrete value into the requested datatype when feasible.

    Delegates to the appropriate :class:`TypeAdapter` based on the datatype
    and dialect.  For example, converts a Python ``int`` to an SQL
    ``DECIMAL`` or a string to an ``INT`` where possible.

    Args:
        value: The concrete value to coerce.
        datatype: Target datatype.
        dialect: Optional SQL dialect for dialect-specific coercion rules.

    Returns:
        The coerced value in the target type.

    Raises:
        Exception: If the value cannot be converted to the target type.
    """
    profile = _TYPE_SERVICE.profile_datatype(DataType.build(datatype), dialect=dialect)
    adapter = _TYPE_SERVICE.registry.resolve(profile.datatype, profile.dialect)
    return adapter.coerce_in(value, profile)


def can_coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> bool:
    """Return True if ``value`` can be coerced to ``datatype`` without error.

    A thin wrapper around ``coerce_value`` that catches exceptions.
    """
    try:
        coerce_value(value, datatype, dialect=dialect)
    except Exception:
        return False
    return True


def coerce_reference_value(
    value: Any, target_datatype: DataType, dialect: str | None = None
) -> Any:
    """Coerce a referenced parent value into the child column's datatype.

    Used when resolving foreign key values: the parent column's value
    is converted to the child column's type so that cross-dialect FK
    references work correctly.

    Currently a thin wrapper around ``coerce_value``; may gain additional
    cross-dialect logic in the future.

    Args:
        value: The parent column value.
        target_datatype: The child column's datatype.
        dialect: Optional dialect of the child column.

    Returns:
        The coerced value.
    """
    return coerce_value(value, target_datatype, dialect=dialect)


def values_equivalent(
    left: Any,
    left_datatype: DataType,
    right: Any,
    right_datatype: DataType,
    left_dialect: str | None = None,
    right_dialect: str | None = None,
) -> bool:
    """Compare two values that may have different datatypes/dialects.

    Projects both values through their respective type adapters and
    compares the normalized results.  This handles cross-dialect
    comparisons (e.g., SQLite TEXT-datetime vs. PostgreSQL TIMESTAMP).

    Args:
        left: The left value.
        left_datatype: Datatype of the left value.
        right: The right value.
        right_datatype: Datatype of the right value.
        left_dialect: Optional dialect of the left value.
        right_dialect: Optional dialect of the right value.

    Returns:
        True if the values are equivalent after type projection.
    """
    left_profile = _TYPE_SERVICE.profile_datatype(left_datatype, dialect=left_dialect)
    right_profile = _TYPE_SERVICE.profile_datatype(
        right_datatype, dialect=right_dialect
    )
    right_adapter = _TYPE_SERVICE.registry.resolve(
        right_profile.datatype, right_profile.dialect
    )
    return right_adapter.equivalent(left, left_profile, right, right_profile)
