from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from parseval.catalog import Catalog
    from parseval.generator import (
        GenerationConfig,
        GenerationResult,
        generate,
    )
    from parseval.db_manager import DBManager
    from parseval.instantiate import instantiate_db


_EXPORTS = {
    "Catalog": ("parseval.catalog", "Catalog"),
    "GenerationConfig": ("parseval.generator", "GenerationConfig"),
    "GenerationResult": ("parseval.generator", "GenerationResult"),
    "generate": ("parseval.generator", "generate"),
    "DBManager": ("parseval.db_manager", "DBManager"),
    "instantiate_db": ("parseval.instantiate", "instantiate_db"),
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
