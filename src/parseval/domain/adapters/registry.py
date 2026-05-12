from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from parseval.dtype import DataType

from .base import TypeAdapter
from .generic import GenericTypeAdapter
from .mysql import MySQLTypeAdapter
from .postgres import PostgresTypeAdapter
from .sqlite import SQLiteTypeAdapter


@dataclass(frozen=True)
class AdapterMatch:
    score: int
    priority: int
    adapter: TypeAdapter


class TypeAdapterRegistry:
    def __init__(self) -> None:
        self._adapters: List[TypeAdapter] = []

    @classmethod
    def with_builtin_adapters(cls) -> "TypeAdapterRegistry":
        registry = cls()
        registry.register(MySQLTypeAdapter())
        registry.register(PostgresTypeAdapter())
        registry.register(SQLiteTypeAdapter())
        registry.register(GenericTypeAdapter())
        return registry

    def register(self, adapter: TypeAdapter) -> None:
        self._adapters.append(adapter)

    def resolve(self, datatype: DataType, dialect: Optional[str]) -> TypeAdapter:
        candidates: List[AdapterMatch] = []
        for adapter in self._adapters:
            score = adapter.supports(datatype, dialect)
            if score > 0:
                candidates.append(AdapterMatch(score, adapter.priority, adapter))
        if not candidates:
            raise ValueError(f"No type adapter for {datatype} dialect={dialect}")
        candidates.sort(key=lambda item: (item.score, item.priority), reverse=True)
        return candidates[0].adapter
