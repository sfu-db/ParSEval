from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional
import uuid

from parseval.dtype import DataType

from .base import TypeAdapter
from ..types import TypeFamily, TypeProfile


class GenericTypeAdapter(TypeAdapter):
    priority = 1

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 1

    def profile(self, datatype: DataType, dialect: Optional[str]) -> TypeProfile:
        expressions = datatype.args.get("expressions") or []
        numbers = []
        for expr in expressions:
            try:
                if getattr(expr, "this", None) is not None and hasattr(expr.this, "this"):
                    numbers.append(int(expr.this.this))
            except (TypeError, ValueError):
                continue
        length = numbers[0] if len(numbers) == 1 else None
        precision = numbers[0] if len(numbers) >= 1 and datatype.is_type(*DataType.REAL_TYPES, DataType.Type.DECIMAL) else None
        scale = numbers[1] if len(numbers) >= 2 and datatype.is_type(*DataType.REAL_TYPES, DataType.Type.DECIMAL) else None
        exact_type = datatype.this.value if hasattr(datatype.this, "value") else str(datatype.this)
        family = self._family_for(datatype)
        timezone = datatype.is_type(
            DataType.Type.TIMESTAMPTZ,
            DataType.Type.TIMESTAMPLTZ,
            DataType.Type.TIMETZ,
        )
        return TypeProfile(
            datatype=datatype,
            dialect=dialect,
            family=family,
            exact_type=exact_type,
            length=length,
            precision=precision,
            scale=scale,
            timezone=timezone,
        )

    def coerce_in(self, value: Any, profile: TypeProfile) -> Any:
        datatype = profile.datatype
        if value is None:
            return None
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
            return datetime.fromisoformat(str(value).replace(" ", "T")).date()
        if profile.family == TypeFamily.DATETIME:
            if isinstance(value, datetime):
                return value
            if isinstance(value, date):
                return datetime(value.year, value.month, value.day)
            return datetime.fromisoformat(str(value).replace(" ", "T"))
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

    def _family_for(self, datatype: DataType) -> TypeFamily:
        if datatype.is_type(DataType.Type.UUID):
            return TypeFamily.UUID
        if datatype.is_type(DataType.Type.BOOLEAN):
            return TypeFamily.BOOLEAN
        if datatype.is_type(*DataType.INTEGER_TYPES):
            return TypeFamily.INTEGER
        if datatype.is_type(*DataType.REAL_TYPES):
            return TypeFamily.DECIMAL
        if datatype.is_type(DataType.Type.DATE, DataType.Type.DATE32):
            return TypeFamily.DATE
        if datatype.is_type(
            DataType.Type.DATETIME,
            DataType.Type.DATETIME64,
            DataType.Type.TIMESTAMP,
            DataType.Type.TIMESTAMP_S,
            DataType.Type.TIMESTAMP_MS,
            DataType.Type.TIMESTAMP_NS,
            DataType.Type.TIMESTAMPTZ,
            DataType.Type.TIMESTAMPLTZ,
        ):
            return TypeFamily.DATETIME
        if datatype.is_type(DataType.Type.TIME, DataType.Type.TIMETZ):
            return TypeFamily.TIME
        if datatype.is_type(*DataType.TEXT_TYPES):
            return TypeFamily.TEXT
        return TypeFamily.UNKNOWN
