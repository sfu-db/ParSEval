from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional

from parseval.dtype import DataType, TypeFamily

from .generic import GenericTypeAdapter


class MySQLTypeAdapter(GenericTypeAdapter):
    priority = 10

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 10 if (dialect or "").lower() == "mysql" else 0

    def profile(self, datatype: DataType, dialect: Optional[str]):
        profile = super().profile(datatype, dialect)
        if datatype.is_type(DataType.Type.CHAR, DataType.Type.NCHAR) and profile.length is None:
            profile = replace(profile, maximum_length=1)
        if datatype.is_type(DataType.Type.TINYINT) and profile.display_width == 1:
            profile = replace(profile, family=TypeFamily.BOOLEAN)
        return profile

    def coerce_in(self, value: Any, profile) -> Any:
        coerced = super().coerce_in(value, profile)
        allowed_values = profile.allowed_values
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

def _is_binary_text(profile) -> bool:
    exact_type = (profile.exact_type or "").upper()
    if "BINARY" in exact_type:
        return True
    collation = str(profile.metadata.get("collation", "")).lower()
    return collation.endswith("_bin") or collation == "binary"
