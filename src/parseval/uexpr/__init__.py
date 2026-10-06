"""Compact-IR translation and opt-in U-expression analyses."""

from .espnf import ESPNFView, ProductTermView, inspect_bag_espnf
from .lowering import CompiledUExpr, UExprCompiler
from .normalize import simplify_uexpr, to_espnf
from .order import OrderKeyTerm, OrderNormalForm, analyze_order_normal_form
from .projection import (
    RowProjectionMap,
    analyze_projection_map,
)

__all__ = (
    "CompiledUExpr",
    "ESPNFView",
    "OrderKeyTerm",
    "OrderNormalForm",
    "ProductTermView",
    "RowProjectionMap",
    "UExprCompiler",
    "analyze_order_normal_form",
    "analyze_projection_map",
    "inspect_bag_espnf",
    "simplify_uexpr",
    "to_espnf",
)
