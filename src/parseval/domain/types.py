from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from parseval.dtype import DataType, TypeFamily

@dataclass(frozen=True)
class TypeProfile:
    """A normalized snapshot of a column's type characteristics.

    Captures the datatype, dialect, type family, and dimensional attributes
    (length, precision, scale) in a single immutable record. Used by the
    provider registry and constraint compiler to make type-aware decisions
    during value generation.

    Attributes:
        datatype: The resolved DataType instance.
        dialect: The SQL dialect (e.g. "sqlite"), or None.
        family: The broad type family (INTEGER, TEXT, etc.).
        exact_type: Canonical string representation of the type.
        length: Character/precision length, if applicable.
        precision: Numeric precision, if applicable.
        scale: Decimal scale, if applicable.
        unsigned: Whether the type is unsigned, if applicable.
        timezone: Whether the type includes timezone info, if applicable.
        metadata: Extra type metadata as a free-form dict.
    """

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
    """Registry-backed service that resolves and caches type profiles.

    Provides two entry points:
    - ``profile()`` — takes a :class:`ColumnSpec` and returns a cached
      :class:`TypeProfile`.
    - ``profile_datatype()`` — takes a raw :class:`DataType` + optional dialect.

    Both methods cache by ``(datatype_sql, dialect)`` to avoid redundant
    adapter lookups.
    """

    def __init__(self, registry=None) -> None:
        if registry is None:
            from .adapters.registry import TypeAdapterRegistry

            registry = TypeAdapterRegistry.with_builtin_adapters()
        self.registry = registry
        self._profile_cache: dict[tuple[str, Optional[str]], TypeProfile] = {}

    def profile(self, column_spec) -> TypeProfile:
        """Resolve the TypeProfile for a ColumnSpec, caching the result."""
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
        """Resolve the TypeProfile for a raw DataType, caching the result."""
        datatype = DataType.build(datatype)
        key = (datatype.sql(dialect=dialect), dialect)
        if key not in self._profile_cache:
            adapter = self.registry.resolve(datatype, dialect)
            self._profile_cache[key] = adapter.profile(datatype, dialect)
        return self._profile_cache[key]


__all__ = ["TypeFamily", "TypeProfile", "TypeService"]
