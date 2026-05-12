from __future__ import annotations

from typing import Optional

from parseval.dtype import DataType

from .generic import GenericTypeAdapter


class SQLiteTypeAdapter(GenericTypeAdapter):
    priority = 10

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 10 if (dialect or "").lower() == "sqlite" else 0
