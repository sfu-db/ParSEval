from __future__ import annotations

from typing import Optional

from parseval.dtype import DataType

from .generic import GenericTypeAdapter
from ..types import TypeFamily


class MySQLTypeAdapter(GenericTypeAdapter):
    priority = 10

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 10 if (dialect or "").lower() == "mysql" else 0

    def profile(self, datatype: DataType, dialect: Optional[str]):
        profile = super().profile(datatype, dialect)
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
