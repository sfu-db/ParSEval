from __future__ import annotations

from typing import Any, Optional

from sqlglot import exp

from parseval.dtype import DataType, TypeFamily

from .generic import GenericTypeAdapter


class MySQLTypeAdapter(GenericTypeAdapter):
    priority = 10

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 10 if (dialect or "").lower() == "mysql" else 0

    def profile(self, datatype: DataType, dialect: Optional[str]):
        profile = super().profile(datatype, dialect)
        metadata = dict(profile.metadata)
        if datatype.is_type(DataType.Type.ENUM):
            metadata["allowed_values"] = _enum_allowed_values(datatype)
            profile = type(profile)(
                datatype=profile.datatype,
                dialect=profile.dialect,
                family=profile.family,
                exact_type=profile.exact_type,
                length=profile.length,
                precision=profile.precision,
                scale=profile.scale,
                unsigned=profile.unsigned,
                timezone=profile.timezone,
                metadata=metadata,
            )
        if datatype.is_type(DataType.Type.TINYINT) and profile.length == 1:
            return type(profile)(
                datatype=profile.datatype,
                dialect=profile.dialect,
                family=TypeFamily.BOOLEAN,
                exact_type=profile.exact_type,
                length=profile.length,
                precision=profile.precision,
                scale=profile.scale,
                unsigned=profile.unsigned,
                timezone=profile.timezone,
                metadata=profile.metadata,
            )
        return profile

    def coerce_in(self, value: Any, profile) -> Any:
        coerced = super().coerce_in(value, profile)
        allowed_values = profile.metadata.get("allowed_values")
        if allowed_values is not None and coerced is not None and coerced not in allowed_values:
            raise ValueError(
                f"Value {coerced!r} is not allowed for {profile.datatype.sql(dialect=profile.dialect)}"
            )
        return coerced

    def storage_key(self, value: Any, profile) -> Any:
        coerced = self.coerce_in(value, profile)
        if coerced is None:
            return None
        if profile.family == TypeFamily.TEXT and not _is_binary_text(profile):
            return str(coerced).casefold()
        return coerced


def _enum_allowed_values(datatype: DataType) -> tuple[Any, ...]:
    values = []
    for expression in datatype.args.get("expressions") or ():
        value = expression.this if isinstance(expression, exp.Literal) else expression
        literal = getattr(value, "this", value)
        if literal not in values:
            values.append(literal)
    return tuple(values)


def _is_binary_text(profile) -> bool:
    exact_type = (profile.exact_type or "").upper()
    if "BINARY" in exact_type:
        return True
    collation = str(profile.metadata.get("collation", "")).lower()
    return collation.endswith("_bin") or collation == "binary"
