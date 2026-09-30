"""Compact-IR translation and opt-in U-expression analyses."""

from .espnf import ESPNFView, ProductTermView, inspect_bag_espnf
from .evaluate import (
    AggregateFunction,
    BagEntry,
    BagValue,
    EvaluationError,
    LazyScalarFunction,
    ScalarArgument,
    ScalarFunction,
    SequenceValue,
    TruthValue,
    UExprEvaluator,
    strict,
    validate_instance,
)
from .lowering import CompiledUExpr, UExprCompiler
from .normalize import simplify_uexpr, to_espnf
from .order import OrderKeyTerm, OrderNormalForm, analyze_order_normal_form
from .projection import (
    BaseBagLineage,
    RowProjectionMap,
    analyze_base_lineage,
    analyze_projection_map,
    project_bag,
)

__all__ = (
    "AggregateFunction",
    "BaseBagLineage",
    "BagEntry",
    "BagValue",
    "CompiledUExpr",
    "ESPNFView",
    "EvaluationError",
    "LazyScalarFunction",
    "OrderKeyTerm",
    "OrderNormalForm",
    "ProductTermView",
    "RowProjectionMap",
    "SequenceValue",
    "ScalarArgument",
    "ScalarFunction",
    "TruthValue",
    "UExprCompiler",
    "UExprEvaluator",
    "validate_instance",
    "analyze_base_lineage",
    "analyze_order_normal_form",
    "analyze_projection_map",
    "inspect_bag_espnf",
    "project_bag",
    "simplify_uexpr",
    "strict",
    "to_espnf",
)
