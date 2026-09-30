"""Normalize parser-specific SQL shapes before name and type analysis."""

from __future__ import annotations

from sqlglot import exp


_COMPARISONS = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)


def normalize_query_syntax(
    expression: exp.Expression,
    *,
    dialect: str,
) -> exp.Expression:
    """Return a query tree whose nodes follow the lowering contract.

    SQLGlot 23 parses PostgreSQL ``a = b IS TRUE`` as
    ``a = (b IS TRUE)`` even though PostgreSQL gives the comparison higher
    precedence.  Repair that syntax-level discrepancy here, before either
    qualification or semantic analysis observes the tree.
    """

    expression = expression.copy()
    _expand_named_windows(expression)
    if dialect != "postgres":
        return expression

    def rewrite(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, _COMPARISONS):
            return node
        right = node.expression
        if not isinstance(right, exp.Is) or not isinstance(
            right.expression, exp.Boolean
        ):
            return node

        comparison = node.copy()
        comparison.set("expression", right.this.copy())
        truth_test = right.copy()
        truth_test.set("this", comparison)
        return truth_test

    return expression.transform(rewrite, copy=False)


def _expand_named_windows(expression: exp.Expression) -> None:
    """Inline WINDOW declarations within each SELECT scope."""

    selects = (
        (expression, *tuple(expression.find_all(exp.Select)))
        if isinstance(expression, exp.Select)
        else tuple(expression.find_all(exp.Select))
    )
    seen: set[int] = set()
    for select in selects:
        if id(select) in seen:
            continue
        seen.add(id(select))
        declarations = tuple(select.args.get("windows") or ())
        if not declarations:
            continue
        by_name = {
            declaration.this.name: declaration
            for declaration in declarations
            if isinstance(declaration, exp.Window)
            and isinstance(declaration.this, exp.Identifier)
        }
        declaration_ids = {id(declaration) for declaration in declarations}
        for window in select.find_all(exp.Window):
            if id(window) in declaration_ids:
                continue
            owner = window.parent
            while owner is not None and not isinstance(owner, exp.Select):
                owner = owner.parent
            if owner is not select:
                continue
            reference = window.args.get("alias")
            if not isinstance(reference, exp.Identifier):
                continue
            declaration = by_name.get(reference.name)
            if declaration is None:
                continue
            if not window.args.get("partition_by"):
                window.set(
                    "partition_by",
                    [
                        item.copy()
                        for item in declaration.args.get("partition_by") or ()
                    ],
                )
            if window.args.get("order") is None:
                order = declaration.args.get("order")
                window.set("order", order.copy() if order is not None else None)
            if window.args.get("spec") is None:
                spec = declaration.args.get("spec")
                window.set("spec", spec.copy() if spec is not None else None)
            window.set("alias", None)
        select.set("windows", None)
