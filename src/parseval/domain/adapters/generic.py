from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
import uuid

from parseval.dtype import (
    DataType,
    TypeFamily,
    TypeProfile,
    StorageLiteral,
    enum_values,
    type_family,
)

from .base import TypeAdapter


class GenericTypeAdapter(TypeAdapter):
    priority = 1

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 1

    def profile(self, datatype: DataType, dialect: Optional[str]) -> TypeProfile:
        def parameter(index: int) -> Optional[int]:
            # SQLGlot represents numeric type arguments as DataTypeParam(Literal).
            if index >= len(datatype.expressions):
                return None
            return int(datatype.expressions[index].this.this)

        family = type_family(datatype)
        length = (
            parameter(0)
            if datatype.is_type(
                *DataType.TEXT_TYPES, DataType.Type.BINARY, DataType.Type.VARBINARY
            )
            else None
        )
        timezone = datatype.is_type(
            DataType.Type.TIMESTAMPTZ,
            DataType.Type.TIMESTAMPLTZ,
            DataType.Type.TIMETZ,
        )
        return TypeProfile(
            datatype=datatype,
            dialect=dialect,
            family=family,
            exact_type=datatype.this.value,
            length=length,
            maximum_length=length if family == TypeFamily.TEXT else None,
            precision=parameter(0) if family == TypeFamily.DECIMAL else None,
            scale=parameter(1) if family == TypeFamily.DECIMAL else None,
            temporal_precision=(
                parameter(0)
                if family in (TypeFamily.TIME, TypeFamily.DATETIME)
                else None
            ),
            display_width=parameter(0) if family == TypeFamily.INTEGER else None,
            allowed_values=enum_values(datatype),
            timezone=timezone,
        )

    def coerce_in(self, value: Any, profile: TypeProfile) -> Any:
        datatype = profile.datatype
        if value is None:
            return None
        if isinstance(value, StorageLiteral):
            return str(value)
        if profile.family == TypeFamily.UUID:
            if isinstance(value, uuid.UUID):
                return value
            return uuid.UUID(str(value))
        if profile.family == TypeFamily.INTEGER:
            if isinstance(value, bool):
                return int(value)
            return int(value)
        if profile.family == TypeFamily.DECIMAL:
            decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
            if profile.scale is not None:
                quant = Decimal("1").scaleb(-profile.scale)
                decimal_value = decimal_value.quantize(quant, rounding=ROUND_HALF_UP)
            return decimal_value
        if profile.family == TypeFamily.BOOLEAN:
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in {"true", "1", "t", "yes"}:
                    return True
                if lowered in {"false", "0", "f", "no"}:
                    return False
                raise ValueError(f"Cannot coerce string to boolean: {value!r}")
            return bool(value)
        if profile.family == TypeFamily.TEXT:
            return str(value)
        if profile.family == TypeFamily.DATE:
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            cleaned = str(value).rstrip('0').rstrip('.') if '.' in str(value) else str(value)
            return datetime.fromisoformat(cleaned.replace(" ", "T")).date()
        if profile.family == TypeFamily.DATETIME:
            if isinstance(value, datetime):
                return value
            if isinstance(value, date):
                return datetime(value.year, value.month, value.day)
            cleaned = str(value).rstrip('0').rstrip('.') if '.' in str(value) else str(value)
            return datetime.fromisoformat(cleaned.replace(" ", "T"))
        if profile.family == TypeFamily.TIME:
            if isinstance(value, datetime):
                return value.time().replace(microsecond=0)
            if isinstance(value, time):
                return value.replace(microsecond=0)
            return time.fromisoformat(str(value))
        return value

    def equivalent(
        self,
        left: Any,
        left_profile: TypeProfile,
        right: Any,
        right_profile: TypeProfile,
    ) -> bool:
        if left == right:
            return True
        try:
            if self.coerce_in(left, right_profile) == right:
                return True
        except Exception:
            pass
        try:
            if self.coerce_in(right, left_profile) == left:
                return True
        except Exception:
            pass
        return False

    def coerce_out(self, value: Any, profile: TypeProfile) -> Any:
        if value is None:
            return None
        if profile.family == TypeFamily.DECIMAL:
            if (profile.dialect or "").lower() == "sqlite":
                return float(value)
            return value
        return super().coerce_out(value, profile)
