from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .exceptions import (
        ConstraintConflict,
        ConstraintViolationError,
        DomainError,
        ForeignKeyResolutionError,
        TypeCoercionError,
        UniqueConflictError,
    )
    from .generator import DomainGenerator
    from .plan import (
        CheckDescriptor,
        ColumnDomainPlan,
        ForeignKeyDescriptor,
        TableConstraintDescriptors,
        compile_column,
        compile_table,
        space_for_column,
    )
    from .value_space import ValueSpace


_EXPORTS = {
    "DomainGenerator": (".generator", "DomainGenerator"),
    "ColumnDomainPlan": (".plan", "ColumnDomainPlan"),
    "CheckDescriptor": (".plan", "CheckDescriptor"),
    "ForeignKeyDescriptor": (".plan", "ForeignKeyDescriptor"),
    "TableConstraintDescriptors": (".plan", "TableConstraintDescriptors"),
    "compile_column": (".plan", "compile_column"),
    "compile_table": (".plan", "compile_table"),
    "space_for_column": (".plan", "space_for_column"),
    "DomainError": (".exceptions", "DomainError"),
    "TypeCoercionError": (".exceptions", "TypeCoercionError"),
    "ConstraintViolationError": (".exceptions", "ConstraintViolationError"),
    "UniqueConflictError": (".exceptions", "UniqueConflictError"),
    "ForeignKeyResolutionError": (".exceptions", "ForeignKeyResolutionError"),
    "ConstraintConflict": (".exceptions", "ConstraintConflict"),
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
