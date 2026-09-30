"""Compact-IR translation and opt-in U-expression analyses."""

from .espnf import ESPNFView, ProductTermView, inspect_bag_espnf
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
    "BaseBagLineage",
    "CompiledUExpr",
    "ESPNFView",
    "OrderKeyTerm",
    "OrderNormalForm",
    "ProductTermView",
    "RowProjectionMap",
    "UExprCompiler",
    "analyze_base_lineage",
    "analyze_order_normal_form",
    "analyze_projection_map",
    "inspect_bag_espnf",
    "project_bag",
    "simplify_uexpr",
    "to_espnf",
)
