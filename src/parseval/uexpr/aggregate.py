from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from parseval.terms import terms as nodes
from parseval.terms.builder import AggregateCall, TermRef
from parseval.terms.context import AggregateKind
from parseval.terms.names import SchemaId
from parseval.terms.terms import (
    AggregateCallLayout,
    AggregateMode,
    FoldPayload,
    TermId,
)
from parseval.terms.schema import Schema
from parseval.terms.sorts import RowFunctionSort, RowSort, ScalarSort
from parseval.terms.types import INTEGER

if TYPE_CHECKING:
    from .lowering import LoweringEnvironment, UExprCompiler


class AggregateTranslator:
    """Translate aggregates over their consumer-visible input values."""

    def __init__(
        self,
        compiler: UExprCompiler,
    ) -> None:
        self.compiler = compiler
        self.source = compiler.source
        self.builder = compiler.builder
        self.scalar = compiler.scalar
        self.bags = compiler.bags

    def translate(
        self,
        term: TermId,
        environment: LoweringEnvironment,
    ) -> TermRef:
        node = self.source[term]
        payload = cast(FoldPayload, node.payload)
        source = node.children[0]
        # MAP and DISTINCT already establish the row contract consumed by the
        # aggregate. Keeping that boundary intact also preserves the complete
        # row identity required by key and foreign-key proofs.
        if type(self.source[source]) in {nodes.Map, nodes.Distinct}:
            return self.builder.checked(
                type(node),
                (
                    self.bags.translate_bag(source, environment),
                    *(
                        self.scalar.translate(child, environment)
                        for child in node.children[1:]
                    ),
                ),
                payload,
            )
        layouts = tuple(
            self._canonical_count(source, node.children, layout)
            for layout in payload.calls
        )
        key_lambda = node.children[1] if isinstance(node, nodes.GroupFold) else None

        projected_fields: list[ScalarSort] = []
        key_schema: SchemaId | None = None
        if key_lambda is not None:
            key_sort = self._lambda_sort(key_lambda)
            key_schema = cast(RowSort, key_sort.result).schema
            projected_fields.extend(self.source.context.schema(key_schema).fields)

        for call in layouts:
            if call.argument_child is not None:
                argument_sort = self._lambda_sort(
                    node.children[call.argument_child]
                ).result
                argument_sort = cast(ScalarSort, argument_sort)
                projected_fields.append(
                    ScalarSort(argument_sort.sql_type, argument_sort.nullable)
                )
            if call.filter_child is not None:
                projected_fields.append(ScalarSort(INTEGER, nullable=False))

        projected_schema = self.source.context.intern_schema(
            Schema(tuple(projected_fields))
        )

        def project(row: TermRef) -> TermRef:
            values: list[TermRef] = []
            if key_lambda is not None:
                key_row = self.scalar.apply_lambda(
                    key_lambda, row, environment
                )
                active_key_schema = cast(SchemaId, key_schema)
                values.extend(
                    self.scalar.field(key_row, index)
                    for index in range(
                        len(self.source.context.schema(active_key_schema).fields)
                    )
                )
            for call in layouts:
                if call.argument_child is not None:
                    values.append(
                        self.scalar.apply_lambda(
                            node.children[call.argument_child], row, environment
                        )
                    )
                if call.filter_child is not None:
                    predicate = self.scalar.apply_lambda(
                        node.children[call.filter_child], row, environment
                    )
                    values.append(
                        self.builder.case(
                            predicate,
                            self.builder.literal(1, INTEGER),
                            self.builder.literal(0, INTEGER),
                        )
                    )
            return self.scalar.make_row(projected_schema, values)

        projected_source = self.bags.translate_projected_bag(
            source, projected_schema, project, environment
        )
        index = (
            len(self.source.context.schema(key_schema).fields)
            if key_schema is not None
            else 0
        )
        calls: list[AggregateCall] = []
        for layout in layouts:
            argument = None
            filter_ = None
            if layout.argument_child is not None:
                argument = self._field(index)
                index += 1
            if layout.filter_child is not None:
                filter_ = self._admitted(index)
                index += 1
            calls.append(
                self.builder.aggregate_call(
                    layout.aggregate,
                    argument=argument,
                    filter=filter_,
                    distinct=layout.mode is AggregateMode.DISTINCT,
                )
            )

        if key_schema is None:
            return self.builder.global_fold(
                projected_source, calls, payload.output_schema
            )
        return self.builder.group_fold(
            projected_source,
            lambda row: self.scalar.make_row(
                key_schema,
                (
                    self.scalar.field(row, field_index)
                    for field_index in range(
                        len(self.source.context.schema(key_schema).fields)
                    )
                ),
            ),
            calls,
            payload.output_schema,
        )

    def _lambda_sort(self, term: TermId) -> RowFunctionSort:
        return cast(RowFunctionSort, self.source[term].sort)

    def _canonical_count(
        self,
        source: TermId,
        children: tuple[TermId, ...],
        layout: AggregateCallLayout,
    ) -> AggregateCallLayout:
        if (
            layout.mode is not AggregateMode.ALL
            or layout.argument_child is None
            or not self._argument_is_non_null(
                source,
                children[layout.argument_child],
                (
                    None
                    if layout.filter_child is None
                    else children[layout.filter_child]
                ),
            )
        ):
            return layout
        spec = self.source.context.aggregate(layout.aggregate)
        if spec.kind is not AggregateKind.COUNT or spec.input is None:
            return layout
        star = next(
            (
                aggregate
                for aggregate, candidate in self.source.context.aggregates()
                if candidate.kind is AggregateKind.COUNT
                and candidate.input is None
                and candidate.output == spec.output
                and candidate.order_sensitive == spec.order_sensitive
            ),
            None,
        )
        if star is None:
            return layout
        return AggregateCallLayout(
            star,
            AggregateMode.ALL,
            argument_child=None,
            filter_child=layout.filter_child,
        )

    def _argument_is_non_null(
        self,
        source: TermId,
        argument: TermId,
        filter_: TermId | None,
    ) -> bool:
        argument_sort = self._lambda_sort(argument).result
        value = self.source[argument].children[0]
        if not isinstance(self.source[value], nodes.Field):
            return False
        if isinstance(argument_sort, ScalarSort) and not argument_sort.nullable:
            return True
        predicates = list(self._source_admission_predicates(source))
        if filter_ is not None:
            predicates.append(self.source[filter_].children[0])
        return any(
            self._predicate_implies_non_null(predicate, value)
            for predicate in predicates
        )

    def _source_admission_predicates(self, source: TermId):
        current = source
        while isinstance(self.source[current], nodes.Filter):
            inner, predicate = self.source[current].children
            yield self.source[predicate].children[0]
            current = inner

    def _predicate_implies_non_null(self, predicate: TermId, value: TermId) -> bool:
        node = self.source[predicate]
        if isinstance(node, nodes.IsNotNull):
            return node.children[0] == value
        if type(node) in {nodes.Eq3, nodes.Lt3, nodes.Like3, nodes.ILike3}:
            return value in node.children
        if isinstance(node, nodes.And3):
            return any(
                self._predicate_implies_non_null(child, value)
                for child in node.children
            )
        if isinstance(node, nodes.Or3):
            return all(
                self._predicate_implies_non_null(child, value)
                for child in node.children
            )
        return False

    def _field(self, index: int) -> Callable[[TermRef], TermRef]:
        return lambda row: self.scalar.field(row, index)

    def _admitted(self, index: int) -> Callable[[TermRef], TermRef]:
        return lambda row: self.builder.eq3(
            self.scalar.field(row, index), self.builder.literal(1, INTEGER)
        )
