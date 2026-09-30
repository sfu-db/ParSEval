from __future__ import annotations

from dataclasses import replace
from typing import Optional

from parseval.dtype import DataType

from .generic import GenericTypeAdapter


class PostgresTypeAdapter(GenericTypeAdapter):
    priority = 10

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 10 if (dialect or "").lower() in {"postgres", "postgresql"} else 0

    def profile(self, datatype: DataType, dialect: Optional[str]):
        profile = super().profile(datatype, dialect)
        if datatype.is_type(DataType.Type.CHAR) and profile.length is None:
            profile = replace(profile, maximum_length=1)
        return profile
