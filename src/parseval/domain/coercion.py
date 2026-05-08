from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from parseval.dtype import DataType
from .types import TypeService

_TYPE_SERVICE = TypeService()


def coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> Any:
    """Convert a concrete value into the requested datatype when feasible."""
    profile = _TYPE_SERVICE.profile_datatype(DataType.build(datatype), dialect=dialect)
    adapter = _TYPE_SERVICE.registry.resolve(profile.datatype, profile.dialect)
    return adapter.coerce_in(value, profile)


def can_coerce_value(value: Any, datatype: DataType, dialect: str | None = None) -> bool:
    try:
        coerce_value(value, datatype, dialect=dialect)
    except Exception:
        return False
    return True


def coerce_reference_value(
    value: Any, target_datatype: DataType, dialect: str | None = None
) -> Any:
    """Coerce a referenced parent value into the child column datatype."""
    return coerce_value(value, target_datatype, dialect=dialect)


def values_equivalent(
    left: Any,
    left_datatype: DataType,
    right: Any,
    right_datatype: DataType,
    left_dialect: str | None = None,
    right_dialect: str | None = None,
) -> bool:
    """Compare values by trying both datatype projections."""
    left_profile = _TYPE_SERVICE.profile_datatype(left_datatype, dialect=left_dialect)
    right_profile = _TYPE_SERVICE.profile_datatype(
        right_datatype, dialect=right_dialect
    )
    right_adapter = _TYPE_SERVICE.registry.resolve(
        right_profile.datatype, right_profile.dialect
    )
    return right_adapter.equivalent(left, left_profile, right, right_profile)
