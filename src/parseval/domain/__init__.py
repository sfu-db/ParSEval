from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .builder import BuildPolicy, DatabaseBuilder
    from .exceptions import (
        ConstraintViolationError,
        DomainError,
        ForeignKeyResolutionError,
        TypeCoercionError,
        UniqueConflictError,
    )
    from .spec import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec
    from .state import ColumnState, RowContext, SchemaRuntime, TableState
    from .value_space import ValueSpace


_EXPORTS = {
    "BuildPolicy": (".builder", "BuildPolicy"),
    "DatabaseBuilder": (".builder", "DatabaseBuilder"),
    "DomainError": (".exceptions", "DomainError"),
    "TypeCoercionError": (".exceptions", "TypeCoercionError"),
    "ConstraintViolationError": (".exceptions", "ConstraintViolationError"),
    "UniqueConflictError": (".exceptions", "UniqueConflictError"),
    "ForeignKeyResolutionError": (".exceptions", "ForeignKeyResolutionError"),
    "SchemaSpec": (".spec", "SchemaSpec"),
    "TableSpec": (".spec", "TableSpec"),
    "ColumnSpec": (".spec", "ColumnSpec"),
    "ForeignKeySpec": (".spec", "ForeignKeySpec"),
    "SchemaRuntime": (".state", "SchemaRuntime"),
    "TableState": (".state", "TableState"),
    "ColumnState": (".state", "ColumnState"),
    "RowContext": (".state", "RowContext"),
    "ValueSpace": (".value_space", "ValueSpace"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
