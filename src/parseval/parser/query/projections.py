"""Lowering of SELECT lists, ORDER BY, and LIMIT/OFFSET."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from sqlglot import exp

from parseval.errors import ErrorCode, fail
from parseval.identifiers import Identifier, NameKey
from parseval.parser.expression import ScalarPlan
from parseval.parser.helper import integer_literal, strip_alias
from parseval.parser.scope import ColumnBinding, EmitEnvironment, FieldSlot, OuterScope, Relation
from parseval.terms.builder import Direction, NullPlacement, OrderKey, TermRef
from parseval.terms.names import CollationId
from parseval.terms.sorts import INTEGER, BagSort, ScalarSort

from .analysis import Projection


class ProjectionLowering:
    """Lower projections, ordering, and row bounds. Mixed into ``QueryCompiler``."""

    __slots__ = ()

    def _projections(
        self,
        expressions: tuple[exp.Expression, ...],
        source: Relation,
        *,
        expanded_star: bool = False,
    ) -> tuple[Projection, ...]:
        if not expressions:
            self.context.unsupported(
                "SELECT must contain at least one expression",
                expressions,
            )
        positional = (
            self._star_projections(expressions, source)
            if expanded_star
            else None
        )
        if positional is not None:
            return positional

        result: list[Projection] = []
        for index, expression in enumerate(expressions):
            core = strip_alias(expression)
            star_qualifier: NameKey | None = None
            star: exp.Star | None = None
            if isinstance(core, exp.Star):
                star = core
            elif isinstance(core, exp.Column) and isinstance(core.this, exp.Star):
                star = core.this
                _, star_qualifier = self.context.binder.column_name(
                    exp.Column(
                        this=exp.Identifier(this="_star"),
                        table=core.args.get("table"),
                        db=core.args.get("db"),
                        catalog=core.args.get("catalog"),
                    )
                )
            if star is not None:
                if star.args.get("except") or star.args.get("replace"):
                    self.context.unsupported(
                        "Star EXCEPT/REPLACE requires projection rewrite semantics",
                        expression,
                        code=ErrorCode.UNSUPPORTED_EXPRESSION,
                    )
                matched = False
                for source_index, column in enumerate(source.columns):
                    if (
                        star_qualifier is not None
                        and star_qualifier not in column.qualifiers
                    ):
                        continue
                    matched = True
                    bound_expression = exp.Column(
                        this=exp.Identifier(
                            this=column.name.text,
                            quoted=column.name.quoted,
                        )
                    )
                    result.append(
                        Projection(
                            bound_expression,
                            column.name,
                            f"$source_field_{source_index}",
                            FieldSlot(source_index, column.sort),
                        )
                    )
                if not matched:
                    self.context.unsupported(
                        "Qualified star does not identify a source relation",
                        expression,
                        code=ErrorCode.UNKNOWN_COLUMN,
                    )
                continue
            source_index = self.context.binder.local_index(
                core, source, allow_missing=True
            )
            inherited = (
                source.columns[source_index].name if source_index is not None else None
            )
            result.append(
                Projection(
                    core,
                    self.context.output_name(
                        expression,
                        fallback=f"_col_{index}",
                        inherited=inherited,
                    ),
                    self.analyzer.key(core),
                )
            )
        return tuple(result)

    def _star_projections(
        self,
        expressions: tuple[exp.Expression, ...],
        source: Relation,
    ) -> tuple[Projection, ...] | None:
        """Recover source ordinals from an expanded star projection.

        SQLGlot expands ``SELECT *`` before query lowering.  Names are not a
        sufficient identity for that expansion because a derived relation may
        legally expose duplicate names.  An expansion that consumes every
        source column exactly once is therefore bound by ordinal.
        """

        if len(expressions) != len(source.columns):
            return None

        projections: list[Projection] = []
        available = set(range(len(source.columns)))
        for index, expression in enumerate(expressions):
            core = strip_alias(expression)
            if not isinstance(core, exp.Column) or isinstance(core.this, exp.Star):
                return None
            candidates = tuple(
                candidate
                for candidate in self.context.binder.matching_indices(core, source)
                if candidate in available
            )
            if not candidates:
                return None
            source_index = candidates[0]
            column = source.columns[source_index]
            output_name = self.context.output_name(
                expression,
                fallback=f"_col_{index}",
                inherited=column.name,
            )
            if output_name.text != column.name.text:
                return None
            available.remove(source_index)
            projections.append(
                Projection(
                    core,
                    column.name,
                    self.analyzer.key(core),
                    FieldSlot(source_index, column.sort),
                )
            )
        return tuple(projections) if not available else None

    def _projection_plan(
        self,
        projection: Projection,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        slot = projection.source_slot
        if slot is None:
            return self.scalar.plan(projection.expression, environment)
        return ScalarPlan(
            slot.sort,
            lambda current: self.context.builder.field(current.row, slot.index),
        )

    def _project(
        self,
        source: Relation,
        environment: EmitEnvironment,
        projections: tuple[Projection, ...],
    ) -> Relation:
        projection_sorts: tuple[ScalarSort, ...]
        output_schema = None

        def mapper(row: TermRef) -> TermRef:
            nonlocal projection_sorts, output_schema
            row_environment = replace(environment, relation=source, row=row)
            values = tuple(
                self._projection_plan(projection, row_environment).emit(
                    row_environment
                )
                for projection in projections
            )
            projection_sorts = tuple(
                self.context.term_scalar_sort(value) for value in values
            )
            output_schema = self.context.schema_for_sorts(projection_sorts)
            return self.context.builder.row(output_schema, values)

        source_sort = self.context.arena[self.context.builder.resolve(source.term)].sort
        if isinstance(source_sort, BagSort):
            term = self.context.builder.map(source.term, mapper)
        else:
            term = self.context.builder.sequence_map(source.term, mapper)
        return Relation(
            term,
            output_schema,
            tuple(
                ColumnBinding(
                    projection.name,
                    sort,
                    frozenset(),
                    (
                        source.columns[projection.source_slot.index].collation
                        if projection.source_slot is not None
                        else self._collation(
                            projection.expression,
                            source,
                            environment.outer_scopes,
                            environment.expressions,
                        )
                    ),
                )
                for projection, sort in zip(projections, projection_sorts, strict=True)
            ),
        )

    def _projection_environment(
        self,
        relation: Relation,
        projections: tuple[Projection, ...],
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> EmitEnvironment:
        expressions = {
            projection.key: FieldSlot(index, relation.columns[index].sort)
            for index, projection in enumerate(projections)
        }
        return self._environment(
            relation,
            expressions,
            outer_scopes=outer_scopes,
        )

    def _hidden_order_projections(
        self,
        order: exp.Expression | None,
        projections: tuple[Projection, ...],
    ) -> tuple[Projection, ...]:
        """ORDER BY expressions the SELECT list lacks, as extra projections."""
        if not isinstance(order, exp.Order):
            return ()
        keys = {projection.key for projection in projections}
        hidden: list[Projection] = []
        for index, ordered in enumerate(order.expressions):
            expression = self._order_expression(ordered.this, projections)
            key = self.analyzer.key(expression)
            if key not in keys:
                keys.add(key)
                hidden.append(Projection(expression, Identifier(f"_order_{index}"), key))
        return tuple(hidden)

    def _order_and_limit(
        self,
        query: exp.Select | exp.Union,
        relation: Relation,
        environment: EmitEnvironment,
        projections: tuple[Projection, ...],
        order: exp.Expression | None,
    ) -> Relation:
        limit = query.args.get("limit")
        offset = query.args.get("offset")
        if order is None:
            if isinstance(limit, exp.Limit):
                count = self._limit_value(limit.expression)
                offset_term = (
                    self._limit_value(offset.expression)
                    if isinstance(offset, exp.Offset)
                    else self.context.builder.literal(0, INTEGER)
                )
                return replace(
                    relation,
                    term=self.context.builder.topk(
                        relation.term,
                        (),
                        offset_term,
                        count,
                    ),
                )
            if isinstance(offset, exp.Offset):
                arbitrary = self.context.builder.order_by(relation.term, ())
                return replace(
                    relation,
                    term=self.context.builder.drop(
                        self._limit_value(offset.expression),
                        arbitrary,
                    ),
                )
            return relation
        keys: list[OrderKey] = []
        for ordered in order.expressions:
            expression = self._order_expression(ordered.this, projections)
            descending = bool(ordered.args.get("desc"))
            keys.append(
                self.context.builder.order_key(
                    lambda row, expression=expression: self.scalar.emit(
                        expression,
                        replace(
                            environment,
                            relation=relation,
                            row=row,
                        ),
                    ),
                    direction=(Direction.DESC if descending else Direction.ASC),
                    nulls=(
                        NullPlacement.FIRST
                        if self.context.nulls_first(ordered, descending=descending)
                        else NullPlacement.LAST
                    ),
                    collation=self._collation(
                        expression,
                        relation,
                        environment.outer_scopes,
                        environment.expressions,
                    ),
                )
            )

        if isinstance(limit, exp.Limit):
            count = self._limit_value(limit.expression)
            offset_term = (
                self._limit_value(offset.expression)
                if isinstance(offset, exp.Offset)
                else self.context.builder.literal(0, INTEGER)
            )
            term = self.context.builder.topk(
                relation.term,
                keys,
                offset_term,
                count,
            )
        else:
            term = self.context.builder.order_by(relation.term, keys)
            if isinstance(offset, exp.Offset):
                term = self.context.builder.drop(self._limit_value(offset.expression), term)
        return replace(relation, term=term)

    def _order_expression(
        self,
        expression: exp.Expression,
        projections: tuple[Projection, ...],
    ) -> exp.Expression:
        if isinstance(expression, exp.Literal) and not expression.is_string:
            ordinal = integer_literal(expression)
            if ordinal is not None and 1 <= ordinal <= len(projections):
                return projections[ordinal - 1].expression
        if isinstance(expression, exp.Column) and not expression.table:
            name = self.context.dialect.identifier(expression.this)
            matches = [
                projection.expression
                for projection in projections
                if projection.name.text == name.text
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                fail(
                    ErrorCode.AMBIGUOUS_COLUMN,
                    f"Ambiguous ORDER BY alias {name.text!r}",
                    node=expression,
                )
        return strip_alias(expression)

    def _collation(
        self,
        expression: exp.Expression,
        relation: Relation,
        outer_scopes: tuple[OuterScope, ...],
        expressions: Mapping[str, FieldSlot],
    ) -> CollationId | None:
        expression = strip_alias(expression)
        materialized = expressions.get(self.analyzer.key(expression))
        if materialized is not None:
            return relation.columns[materialized.index].collation
        if not isinstance(expression, exp.Column):
            return None
        resolved = self.context.binder.resolve(
            expression,
            relation,
            outer_scopes=outer_scopes,
        )
        return None if resolved is None else resolved[2].collation

    def _limit_value(self, expression: exp.Expression) -> TermRef:
        value = integer_literal(expression)
        if value is None or value < 0:
            fail(
                ErrorCode.INVALID_LIMIT,
                "LIMIT/OFFSET must be a nonnegative integer literal",
                node=expression,
            )
        return self.context.builder.literal(value, INTEGER)
