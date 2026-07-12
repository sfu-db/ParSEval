"""ParSEval plan module — single-source-of-truth for query structure.

The public surface of the plan module is:

* :class:`Plan` — the DAG of plan steps for a query, including every
  subquery and CTE as a :class:`SubPlan`.
* Step classes: :class:`Step`, :class:`Scan`, :class:`Join`,
  :class:`Filter`, :class:`Aggregate`, :class:`Having`, :class:`Sort`,
  :class:`Project`, :class:`Limit`, :class:`SetOperation`,
  :class:`SubPlan` (+ :class:`SubPlanKind`).
* :class:`StepAnnotations` — derived, cached facts about a step (step_id,
  step_type, source_relations, referenced_columns, projected_columns, etc.)
  accessed via :meth:`Plan.annotation_for`.
* :func:`correlation_columns` and :func:`scope_columns` — sqlglot-level
  helpers used by the planner at build time and by the encoder for
  column resolution.
* :mod:`parseval.plan.context` — ``Context`` / ``DerivedSchema`` /
  ``AggregateGroup`` / ``WindowFrame`` / ``build_context_from_instance``.
"""

from .context import (
    AggregateGroup,
    Context,
    DerivedSchema,
    WindowFrame,
    build_context_from_instance,
)
# from .planner import (
#     Aggregate,
#     Filter,
#     Having,
#     Join,
#     Limit,
#     Plan,
#     Project,
#     Scan,
#     SetOperation,
#     Sort,
#     Step,
#     StepAnnotations,
#     SubPlan,
#     SubPlanKind,
#     correlation_columns,
#     scope_columns,
# )


# def _symbolic_scope_encoder():
#     """Lazy resolver for :class:`parseval.symbolic.encoder.SymbolicScopeEncoder`.

#     The encoder class lives under :mod:`parseval.symbolic.encoder` but
#     downstream code has historically imported it as
#     ``parseval.plan.SymbolicScopeEncoder``. We defer the import to
#     attribute-access time so the plan module stays free of symbolic-layer
#     dependencies and no import cycle is introduced.
#     """
#     from parseval.symbolic.encoder import SymbolicScopeEncoder

#     return SymbolicScopeEncoder


# def __getattr__(name):
#     if name in ("SymbolicScopeEncoder", "Planner"):
#         return _symbolic_scope_encoder()
#     raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# __all__ = [
#     "Aggregate",
#     "AggregateGroup",
#     "Context",
#     "DerivedSchema",
#     "Filter",
#     "Having",
#     "Join",
#     "Limit",
#     "Plan",
#     "Project",
#     "Scan",
#     "SetOperation",
#     "Sort",
#     "Step",
#     "StepAnnotations",
#     "SubPlan",
#     "SubPlanKind",
#     "WindowFrame",
#     "build_context_from_instance",
#     "correlation_columns",
#     "scope_columns",
# ]
