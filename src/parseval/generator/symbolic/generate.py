from __future__ import annotations

from typing import Mapping

from sqlglot.errors import OptimizeError, SchemaError, UnsupportedError

from parseval.generator.config import GenerationConfig
from parseval.generator.budget import GenerationBudget
from parseval.instance import Instance
from parseval.plan.explain import explain, Plan

from .operator import EncodePipeline
from parseval.generator.speculate import speculate


def generate(
    ddls: str,
    query: str,
    dialect: str = "sqlite",
    *,
    config: GenerationConfig = GenerationConfig(),
) -> Instance:
    """Generate witness rows for *query* under *ddls*.

    Speculative seeding supplies unproven bootstrap rows. EncodePipeline then
    evaluates and completes uncovered semantic targets in the same instance.
    """
    budget = GenerationBudget(config)
    try:
        plan = explain(ddls, query, dialect=dialect)
    except (ValueError, SchemaError, OptimizeError, UnsupportedError):
        plan = None
    except Exception as exc:
        # DataFusion currently raises the built-in Exception type for some
        # planning failures instead of a library-specific exception.
        if type(exc) is not Exception:
            raise
        plan = None
    if plan is None:
        return speculate(
            ddls,
            query,
            dialect=dialect,
            config=config,
            _budget=budget,
        )
    instance = speculate(
        ddls,
        query,
        dialect=dialect,
        config=config,
        _budget=budget,
    )
    return _generate_from_plan(
        plan,
        instance,
        config=config,
        before_counts={},
        budget=budget,
    )


def _generate_from_plan(
    plan: Plan,
    instance: Instance,
    *,
    config: GenerationConfig = GenerationConfig(),
    before_counts: Mapping[str, int] | None = None,
    budget: GenerationBudget | None = None,
) -> Instance:
    """Generate semantic witnesses for *plan* through EncodePipeline.

    ``Instance`` is the committed row source. Solver output is appended to
    ``instance`` and the same object is returned.
    """
    if before_counts is None:
        before_counts = _row_counts(instance)
    pipeline = EncodePipeline(
        plan,
        instance,
        config=config,
        base_row_counts=before_counts,
        budget=budget,
    )
    pipeline.forward()
    return instance


def _row_counts(instance: Instance) -> dict[str, int]:
    return {
        instance.resolve_table(table_name).name: len(instance.get_rows(table_name))
        for table_name in instance.tables
    }


__all__ = ["generate"]
