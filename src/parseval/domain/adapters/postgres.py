from __future__ import annotations

from typing import Optional

from parseval.dtype import DataType

from .generic import GenericTypeAdapter


class PostgresTypeAdapter(GenericTypeAdapter):
    priority = 10

    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        return 10 if (dialect or "").lower() in {"postgres", "postgresql"} else 0
