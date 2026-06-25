from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from parseval.main import disprove, instantiate_db
    from parseval.states import (
        DisproveResult,
        InstantiateResult,
        Semantics,
        Verdict,
    )


_EXPORTS = {
    "instantiate_db": ("parseval.main", "instantiate_db"),
    "disprove": ("parseval.main", "disprove"),
    "DisproveResult": ("parseval.states", "DisproveResult"),
    "InstantiateResult": ("parseval.states", "InstantiateResult"),
    "Semantics": ("parseval.states", "Semantics"),
    "Verdict": ("parseval.states", "Verdict"),
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
