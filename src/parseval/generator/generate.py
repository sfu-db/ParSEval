"""Convenience facade for coverage-directed instance generation."""

from __future__ import annotations

from dataclasses import replace

from parseval.catalog import Catalog

from .config import GenerationConfig
from .engine import AttemptCallback, Generator, ProgressCallback
from .model import CounterExample, GenerationResult, InvalidModelError, TargetResult

_UNSET = object()


def generate(
    sql: str,
    catalog: Catalog,
    *,
    config: GenerationConfig | None = None,
    timeout_ms: int | None | object = _UNSET,
    group_size: int | None = None,
    on_attempt: AttemptCallback | None = None,
    on_progress: ProgressCallback | None = None,
) -> GenerationResult:
    """Generate concrete witnesses for discovered semantic coverage targets.

    ``timeout_ms`` and ``group_size`` remain keyword shortcuts for callers that
    do not need a full :class:`GenerationConfig`.
    """

    selected = config or GenerationConfig()
    overrides: dict[str, object] = {}
    if timeout_ms is not _UNSET:
        overrides["timeout_ms"] = timeout_ms
    if group_size is not None:
        overrides["group_size"] = group_size
    if overrides:
        selected = replace(selected, **overrides)
    return Generator(catalog, config=selected).generate(
        sql,
        on_attempt=on_attempt,
        on_progress=on_progress,
    )


__all__ = [
    "CounterExample",
    "GenerationResult",
    "InvalidModelError",
    "TargetResult",
    "generate",
]
