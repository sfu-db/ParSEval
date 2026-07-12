from __future__ import annotations

from sqlglot import exp

from .bindings import Scope, ScopeResolutionError


class UnsupportedQueryFeature(ValueError):
    pass


def rewrite_expr(expr: exp.Expression, scope: Scope) -> exp.Expression:
    def transform(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            try:
                return scope.resolve_column(node)
            except ScopeResolutionError as exc:
                raise UnsupportedQueryFeature(str(exc)) from exc
        if isinstance(node, (exp.Subquery, exp.Exists)):
            raise UnsupportedQueryFeature("nested_subquery_requires_branch_execution")
        return node

    return expr.copy().transform(transform)
