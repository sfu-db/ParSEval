"""Output sorts of scalar subqueries, inferred from their syntax before lowering."""

from __future__ import annotations

from sqlglot import exp

from parseval.errors import CatalogError, DDLImportError, ErrorCode
from parseval.parser.expression import scalar_sort_from_expression
from parseval.parser.helper import aggregate_expression, query_body, strip_alias
from parseval.terms.sorts import ScalarSort


class SortInference:
    """Infer the sole output sort of scalar queries from their syntax. Mixed into ``QueryCompiler``."""

    __slots__ = ()

    def infer_scalar_query_sort(self, query: exp.Expression) -> ScalarSort:
        """Infer the sole output sort of a scalar query expression."""

        query = query_body(query)
        if isinstance(query, (exp.Union, exp.Intersect, exp.Except)):
            left = self.infer_scalar_query_sort(query.this)
            right = self.infer_scalar_query_sort(query.expression)
            result = self.context.dialect.common_scalar_sort(left, right)
            if result is not None:
                return result
            self.context.unsupported(
                "Scalar set-operation branches have incompatible types",
                query,
                code=ErrorCode.TYPE_ERROR,
            )
        if not isinstance(query, exp.Select) or len(query.expressions) != 1:
            self.context.unsupported(
                "Scalar subqueries must project exactly one column",
                query,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        expression = strip_alias(query.expressions[0])
        inferred = self._infer_sort(expression)
        if inferred is not None:
            return inferred
        self.context.unsupported(
            "Could not infer scalar subquery output type",
            expression,
            code=ErrorCode.TYPE_ERROR,
        )

    def _infer_sort(
        self, expression: exp.Expression
    ) -> ScalarSort | None:
        expression = strip_alias(expression)
        aggregate = aggregate_expression(expression)
        if aggregate is not None:
            if isinstance(aggregate, exp.Filter):
                aggregate = aggregate.this
            if not isinstance(aggregate, exp.AggFunc):
                return None
            argument = aggregate.this
            if isinstance(argument, exp.Distinct):
                values = tuple(argument.expressions)
                if len(values) != 1:
                    return None
                argument = values[0]
            if isinstance(argument, exp.Star) or argument is None:
                argument_sort = None
            else:
                try:
                    argument_sort = scalar_sort_from_expression(
                        argument, self.context.dialect
                    )
                except DDLImportError:
                    argument_sort = self._infer_sort(argument)
                if argument_sort is None:
                    return None
            return self._aggregate_spec(aggregate, argument_sort).output
        if isinstance(expression, exp.Column):
            column_name = self.context.dialect.identifier(expression.this).text
            table_name = (
                self.context.dialect.identifier(
                    expression.args["table"]
                ).text
                if expression.args.get("table") is not None
                else None
            )
            relations = (
                (self.active_ctes.get(table_name),)
                if table_name is not None
                else tuple(self.active_ctes.values())
            )
            matches = [
                column.sort
                for relation in relations
                if relation is not None
                for column in relation.columns
                if column.name.text == column_name
            ]
            if len(matches) == 1:
                return matches[0]
            return self._aliased_column_sort(expression)
        if isinstance(expression, exp.Subquery) and isinstance(
            expression.this, exp.Select
        ):
            return self._infer_sort(
                strip_alias(expression.this.expressions[0])
            )
        if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
            left = self._infer_sort(expression.this)
            right = self._infer_sort(expression.expression)
            if left is not None and right is not None:
                return self.context.dialect.common_scalar_sort(
                    left,
                    right,
                    prefer_float=isinstance(expression, exp.Div),
                    arithmetic=True,
                )
            return None
        try:
            return scalar_sort_from_expression(expression, self.context.dialect)
        except DDLImportError:
            return None

    def _aliased_column_sort(
        self,
        expression: exp.Column,
    ) -> ScalarSort | None:
        """Resolve a qualified field through a base table's positional alias.

        SQLGlot does not propagate declared types through PostgreSQL table
        aliases of the form ``table alias(col1, ...)``.  This is binding
        information, so recover it from the containing query scope and the
        catalog rather than guessing from the renamed identifier.
        """

        select = expression.parent
        while select is not None and not isinstance(select, exp.Select):
            select = select.parent
        if not isinstance(select, exp.Select):
            return None

        wanted_name = self.context.dialect.identifier(expression.this).text
        wanted_table = (
            self.context.dialect.identifier(
                expression.args["table"]
            ).text
            if expression.args.get("table") is not None
            else None
        )
        matches: list[ScalarSort] = []
        for table in select.find_all(exp.Table):
            owner = table.parent
            while owner is not None and not isinstance(owner, exp.Select):
                owner = owner.parent
            if owner is not select:
                continue
            alias_node = table.args.get("alias")
            alias_name = (
                self.context.dialect.identifier(
                    alias_node.this
                ).text
                if isinstance(alias_node, exp.TableAlias)
                else self.context.dialect.identifier(
                    table.this
                ).text
            )
            if wanted_table is not None and alias_name != wanted_table:
                continue
            try:
                declaration = self.context.resolve_table(
                    self.context.dialect.qualified_name(table)
                )
            except CatalogError:
                continue
            aliases = (
                tuple(alias_node.args.get("columns") or ())
                if isinstance(alias_node, exp.TableAlias)
                else ()
            )
            for index, column in enumerate(declaration.columns):
                bound_name = (
                    self.context.dialect.identifier(aliases[index]).text
                    if index < len(aliases)
                    else column.name.text
                )
                if bound_name == wanted_name:
                    matches.append(declaration.column_spec(column.id).sort)
        for subquery in select.find_all(exp.Subquery):
            owner = subquery.parent
            while owner is not None and not isinstance(owner, exp.Select):
                owner = owner.parent
            if owner is not select:
                continue
            alias_node = subquery.args.get("alias")
            if not isinstance(alias_node, exp.TableAlias):
                continue
            alias_name = self.context.dialect.identifier(
                alias_node.this
            ).text
            if wanted_table is not None and alias_name != wanted_table:
                continue
            body = query_body(subquery)
            if not isinstance(body, exp.Select):
                continue
            aliases = tuple(alias_node.args.get("columns") or ())
            for index, projection in enumerate(body.expressions):
                bound_name = (
                    self.context.dialect.identifier(aliases[index]).text
                    if index < len(aliases)
                    else self.context.dialect.identifier(
                        projection.alias_or_name
                    ).text
                )
                if bound_name != wanted_name:
                    continue
                inferred = self._infer_sort(strip_alias(projection))
                if inferred is not None:
                    matches.append(inferred)
        return matches[0] if len(matches) == 1 else None
