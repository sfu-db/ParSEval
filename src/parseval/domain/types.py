from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from parseval.dtype import DataType, TypeFamily

@dataclass(frozen=True)
class TypeProfile:
    datatype: DataType
    dialect: Optional[str]
    family: TypeFamily
    exact_type: str
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    unsigned: Optional[bool] = None
    timezone: Optional[bool] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TypeService:
    def __init__(self, registry=None) -> None:
        if registry is None:
            from .adapters.registry import TypeAdapterRegistry

            registry = TypeAdapterRegistry.with_builtin_adapters()
        self.registry = registry
        self._profile_cache: dict[tuple[str, Optional[str]], TypeProfile] = {}

    def profile(self, column_spec) -> TypeProfile:
        datatype = DataType.build(column_spec.datatype)
        dialect = getattr(column_spec, "dialect", None)
        key = (datatype.sql(dialect=dialect), dialect)
        if key not in self._profile_cache:
            adapter = self.registry.resolve(datatype, dialect)
            self._profile_cache[key] = adapter.profile(datatype, dialect)
        return self._profile_cache[key]

    def profile_datatype(
        self, datatype: DataType, dialect: Optional[str] = None
    ) -> TypeProfile:
        datatype = DataType.build(datatype)
        key = (datatype.sql(dialect=dialect), dialect)
        if key not in self._profile_cache:
            adapter = self.registry.resolve(datatype, dialect)
            self._profile_cache[key] = adapter.profile(datatype, dialect)
        return self._profile_cache[key]


__all__ = ["TypeFamily", "TypeProfile", "TypeService"]
