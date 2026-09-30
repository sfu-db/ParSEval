"""Semantic analysis for SELECT blocks and their projections."""

from __future__ import annotations

from dataclasses import dataclass

from sqlglot import exp

from parseval.parser.dialect import SQLDialect
from parseval.parser.scope import FieldSlot
from parseval.parser.syntax import aggregate_expression, strip_alias
from parseval.terms.names import Identifier


@dataclass(frozen=True, slots=True)
class Projection:
    expression: exp.Expression
    name: Identifier
    expression_id: str
    source_slot: FieldSlot | None = None


@dataclass(frozen=True, slots=True)
class QueryBlockFacts:
    group_expressions: tuple[exp.Expression, ...]
    grouping_sets: tuple[tuple[exp.Expression, ...], ...] | None
    grouping_expressions: tuple[exp.Anonymous, ...]
    aggregate_expressions: tuple[exp.Expression, ...]
    window_expressions: tuple[exp.Window, ...]
    bare_columns: tuple[exp.Column, ...]
    having: exp.Expression | None
    qualify: exp.Expression | None

    @property
    def requires_aggregation(self) -> bool:
        return self.grouping_sets is not None or bool(self.aggregate_expressions)


class QueryBlockAnalyzer:
    """Collect clause semantics for one SELECT block without planning execution."""

    __slots__ = ("dialect", "_expression_ids", "_query_blocks")

    def __init__(self, dialect: SQLDialect) -> None:
        self.dialect = dialect
        self._expression_ids: dict[int, str] = {}
        self._query_blocks: dict[int, QueryBlockFacts] = {}

    def expression_id(self, expression: exp.Expression) -> str:
        key = id(expression)
        result = self._expression_ids.get(key)
        if result is None:
            result = expression.sql(dialect=self.dialect.name)
            self._expression_ids[key] = result
        return result

    def query_block(self, select: exp.Select) -> QueryBlockFacts:
        key = id(select)
        result = self._query_blocks.get(key)
        if result is not None:
            return result

        group = select.args.get("group")
        grouping_sets = self._grouping_sets(group)
        groups = self._group_expressions(grouping_sets)
        having_node = select.args.get("having")
        having = having_node.this if isinstance(having_node, exp.Having) else None
        qualify_node = select.args.get("qualify")
        qualify = (
            qualify_node.this if isinstance(qualify_node, exp.Qualify) else None
        )
        roots = [strip_alias(item) for item in select.expressions]
        if having is not None:
            roots.append(having)
        if qualify is not None:
            roots.append(qualify)
        order = select.args.get("order")
        if isinstance(order, exp.Order):
            roots.extend(item.this for item in order.expressions)

        aggregates: list[exp.Expression] = []
        windows: list[exp.Window] = []
        window_seen: set[str] = set()
        grouping_functions: list[exp.Anonymous] = []
        grouping_seen: set[str] = set()
        seen: set[str] = set()
        for root in roots:
            for window in self._collect_windows(root):
                identity = self.expression_id(window)
                if identity not in window_seen:
                    window_seen.add(identity)
                    windows.append(window)
            for aggregate in self._collect_aggregates(root):
                identity = self.expression_id(aggregate)
                if identity not in seen:
                    seen.add(identity)
                    aggregates.append(aggregate)
            for grouping_function in self._collect_grouping_functions(root):
                identity = self.expression_id(grouping_function)
                if identity not in grouping_seen:
                    grouping_seen.add(identity)
                    grouping_functions.append(grouping_function)

        bare_columns: list[exp.Column] = []
        if groups or aggregates:
            group_ids = {self.expression_id(item) for item in groups}
            projection_aliases = {
                item.alias
                for item in select.expressions
                if item.alias
            }
            bare_seen: set[str] = set()

            def visit_bare(node: exp.Expression) -> None:
                if self.expression_id(node) in group_ids:
                    return
                if aggregate_expression(node) is not None or isinstance(
                    node, (exp.Subquery, exp.Window)
                ):
                    return
                if isinstance(node, exp.Column):
                    if not node.table and node.name in projection_aliases:
                        return
                    identity = self.expression_id(node)
                    if identity not in bare_seen:
                        bare_seen.add(identity)
                        bare_columns.append(node)
                    return
                for child in node.iter_expressions():
                    visit_bare(child)

            for root in roots:
                visit_bare(root)

        result = QueryBlockFacts(
            groups,
            grouping_sets,
            tuple(grouping_functions),
            tuple(aggregates),
            tuple(windows),
            tuple(bare_columns),
            having,
            qualify,
        )
        self._query_blocks[key] = result
        return result

    def _collect_aggregates(
        self, expression: exp.Expression
    ) -> tuple[exp.Expression, ...]:
        result: list[exp.Expression] = []

        def visit(node: exp.Expression) -> None:
            if isinstance(node, exp.Window):
                function = node.this
                for child in function.iter_expressions():
                    visit(child)
                return
            aggregate = aggregate_expression(node)
            if aggregate is not None:
                result.append(aggregate)
                return
            if isinstance(node, exp.Subquery):
                return
            for child in node.iter_expressions():
                visit(child)

        visit(expression)
        return tuple(result)

    def _collect_windows(
        self, expression: exp.Expression
    ) -> tuple[exp.Window, ...]:
        result: list[exp.Window] = []

        def visit(node: exp.Expression) -> None:
            if isinstance(node, exp.Subquery):
                return
            if isinstance(node, exp.Window):
                result.append(node)
                return
            for child in node.iter_expressions():
                visit(child)

        visit(expression)
        return tuple(result)

    def _collect_grouping_functions(
        self, expression: exp.Expression
    ) -> tuple[exp.Anonymous, ...]:
        result: list[exp.Anonymous] = []

        def visit(node: exp.Expression) -> None:
            if isinstance(node, (exp.Subquery, exp.Window)):
                return
            if isinstance(node, exp.Anonymous) and node.name.casefold() == "grouping":
                result.append(node)
                return
            for child in node.iter_expressions():
                visit(child)

        visit(expression)
        return tuple(result)

    @staticmethod
    def _grouping_sets(
        group: exp.Expression | None,
    ) -> tuple[tuple[exp.Expression, ...], ...] | None:
        if not isinstance(group, exp.Group):
            return None

        fixed = tuple(strip_alias(item) for item in group.expressions)
        rollup = tuple(strip_alias(item) for item in group.args.get("rollup") or ())
        cube = tuple(strip_alias(item) for item in group.args.get("cube") or ())
        components: list[tuple[tuple[exp.Expression, ...], ...]] = []
        if rollup:
            components.append(
                tuple(rollup[:width] for width in range(len(rollup), -1, -1))
            )
        if cube:
            components.append(tuple(
                tuple(
                    item
                    for index, item in enumerate(cube)
                    if mask & (1 << (len(cube) - index - 1))
                )
                for mask in range((1 << len(cube)) - 1, -1, -1)
            ))
        explicit = group.args.get("grouping_sets") or ()
        if explicit:
            components.append(
                tuple(
                    tuple(strip_alias(item) for item in grouping_set.expressions)
                    if isinstance(grouping_set, exp.Tuple)
                    else (strip_alias(grouping_set),)
                    for grouping_set in explicit
                )
            )

        result = (fixed,)
        for component in components:
            result = tuple(prefix + suffix for prefix in result for suffix in component)
        return result

    def _group_expressions(
        self,
        grouping_sets: tuple[tuple[exp.Expression, ...], ...] | None,
    ) -> tuple[exp.Expression, ...]:
        result: list[exp.Expression] = []
        seen: set[str] = set()
        for grouping_set in grouping_sets or ():
            for expression in grouping_set:
                identity = self.expression_id(expression)
                if identity not in seen:
                    seen.add(identity)
                    result.append(expression)
        return tuple(result)
