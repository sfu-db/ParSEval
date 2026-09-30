"""Translation of compact bag operators into U-semiring multiplicities."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from parseval.errors import UExprTranslationError
from parseval.terms import terms as nodes
from parseval.terms.builder import TermRef
from parseval.terms.names import SchemaId
from parseval.terms.schema import Schema
from parseval.terms.terms import (
    BaseRelationPayload,
    TermId,
    VariablePayload,
)
from parseval.terms.sorts import BagSort, RowFunctionSort, RowSort, ScalarSort

from .projection import analyze_projection_map

if TYPE_CHECKING:
    from .lowering import LoweringEnvironment, UExprCompiler

MultiplicityConsumer = Callable[[TermRef], TermRef]


class BagWeightTranslator:
    """Translate compact bags through their row-multiplicity semantics."""

    def __init__(
        self,
        compiler: UExprCompiler,
    ) -> None:
        self.compiler = compiler
        self.source = compiler.source
        self.builder = compiler.builder
        self.scalar = compiler.scalar

    def translate_bag(self, bag: TermId, environment: LoweringEnvironment) -> TermRef:
        node = self.source[bag]
        if isinstance(node, nodes.Map):
            source, mapper = node.children
            source_sort = self._bag_sort(self.source[source].sort)
            projection = analyze_projection_map(self.source, mapper)
            if (
                not isinstance(self.source[source], nodes.GroupFold)
                and source_sort == self._bag_sort(node.sort)
                and projection is not None
                and projection.direct_input_positions
                == tuple(
                    range(len(self.source.context.schema(source_sort.schema).fields))
                )
            ):
                return self.compiler.translate(source, environment)
        if isinstance(node, nodes.Distinct):
            (source,) = node.children
            schema = self._bag_sort(node.sort).schema
            translated = self.translate_bag(source, environment)
            return self.builder.bag_lam(
                schema,
                lambda output: self.builder.squash(
                    self.builder.at(translated, output)
                ),
            )
        schema = self._bag_sort(self.source[bag].sort).schema
        return self.builder.bag_lam(
            schema,
            lambda output: self.emit_multiplicity(
                bag,
                lambda row: self.builder.indicator(
                    self.builder.row_identity_eq(row, output)
                ),
                environment,
            ),
        )

    def translate_projected_bag(
        self,
        bag: TermId,
        output_schema: SchemaId,
        mapper: Callable[[TermRef], TermRef],
        environment: LoweringEnvironment,
    ) -> TermRef:
        """Translate only the row values required by a consuming operator."""

        input_schema = self._bag_sort(self.source[bag].sort).schema
        translated = self.translate_bag(bag, environment)
        return self.builder.bag_lam(
            output_schema,
            lambda output: self.builder.sum(
                RowSort(input_schema),
                lambda row: self.builder.mul(
                    self.builder.at(translated, row),
                    self.builder.indicator(
                        self.builder.row_identity_eq(mapper(row), output)
                    ),
                ),
            ),
        )

    def emit_multiplicity(
        self,
        bag: TermId,
        consume: MultiplicityConsumer,
        environment: LoweringEnvironment,
    ) -> TermRef:
        node = self.source[bag]
        node_type = type(node)
        if node_type is nodes.Empty:
            return self.builder.zero()
        if node_type is nodes.Base:
            schema = self._bag_sort(node.sort).schema
            payload = cast(BaseRelationPayload, node.payload)
            base = self.builder.resolve(self.builder.base(payload.relation))
            return self.builder.sum(
                RowSort(schema),
                lambda row: self.builder.mul(self.builder.at(base, row), consume(row)),
            )
        if node_type is nodes.Filter:
            source, predicate = node.children
            predicate_node = self.source[predicate]
            if isinstance(predicate_node, nodes.RowLambda) and isinstance(
                self.source[predicate_node.children[0]], nodes.True3
            ):
                return self.emit_multiplicity(source, consume, environment)
            return self.emit_multiplicity(
                source,
                lambda row: self.builder.mul(
                    self.builder.indicator(
                        self.scalar.apply_lambda(predicate, row, environment)
                    ),
                    consume(row),
                ),
                environment,
            )
        if node_type is nodes.Map:
            source, mapper = node.children
            return self.emit_multiplicity(
                source,
                lambda row: consume(self.scalar.apply_lambda(mapper, row, environment)),
                environment,
            )
        if node_type in {nodes.Product, nodes.Join}:
            left = node.children[0]
            right = node.children[1]
            join_predicates = node.children[2:]
            left_schema = self._bag_sort(self.source[left].sort).schema
            right_schema = self._bag_sort(self.source[right].sort).schema
            output_schema = self.source.context.concat_schema(left_schema, right_schema)

            def consume_left(left_row: TermRef) -> TermRef:
                def consume_right(right_row: TermRef) -> TermRef:
                    combined = self.scalar.concat_rows(
                        left_row,
                        left_schema,
                        right_row,
                        right_schema,
                        output_schema,
                    )
                    result = consume(combined)
                    if join_predicates:
                        result = self.builder.mul(
                            self.builder.indicator(
                                self.scalar.apply_lambda(
                                    join_predicates[0], combined, environment
                                )
                            ),
                            result,
                        )
                    return result

                return self.emit_multiplicity(right, consume_right, environment)

            return self.emit_multiplicity(left, consume_left, environment)
        if node_type in {nodes.LeftJoin, nodes.RightJoin, nodes.FullJoin}:
            return self._emit_outer_join(bag, consume, environment)
        if node_type is nodes.UnionAll:
            left, right = node.children
            return self.builder.add(
                self.emit_multiplicity(left, consume, environment),
                self.emit_multiplicity(right, consume, environment),
            )
        if node_type is nodes.Distinct:
            (source,) = node.children
            schema = self._bag_sort(node.sort).schema
            translated = self.translate_bag(source, environment)
            return self.builder.sum(
                RowSort(schema),
                lambda output: self.builder.mul(
                    self.builder.squash(self.builder.at(translated, output)),
                    consume(output),
                ),
            )
        if node_type in {nodes.DependentJoin, nodes.DependentLeftJoin}:
            outer, function = node.children
            outer_schema = self._bag_sort(self.source[outer].sort).schema
            function_sort = cast(RowFunctionSort, self.source[function].sort)
            inner_schema = cast(BagSort, function_sort.result).schema

            def consume_outer(outer_row: TermRef) -> TermRef:
                function_node = self.source[function]
                inner = function_node.children[0]
                inner_environment = environment.bind_row(outer_row)
                matched = self.emit_multiplicity(
                    inner,
                    lambda inner_row: consume(
                        self.scalar.concat_rows(
                            outer_row,
                            outer_schema,
                            inner_row,
                            inner_schema,
                            self.source.context.concat_schema(
                                outer_schema, inner_schema
                            ),
                        )
                    ),
                    inner_environment,
                )

                if node_type is nodes.DependentJoin:
                    return matched

                cardinality = self.emit_multiplicity(
                    inner,
                    lambda _inner_row: self.builder.one(),
                    inner_environment,
                )
                nullable_output_schema = self.source.context.outer_join_schema(
                    outer_schema,
                    inner_schema,
                    nullable_left=False,
                    nullable_right=True,
                )
                nullable_inner_schema = self.source.context.intern_schema(
                    Schema(
                        tuple(
                            ScalarSort(field.sql_type, True)
                            for field in self.source.context.schema(inner_schema).fields
                        )
                    )
                )
                null_inner = self.scalar.make_row(
                    nullable_inner_schema,
                    (
                        self.builder.null(field.sql_type)
                        for field in self.source.context.schema(inner_schema).fields
                    ),
                )
                unmatched_row = self.scalar.concat_rows(
                    outer_row,
                    outer_schema,
                    null_inner,
                    nullable_inner_schema,
                    nullable_output_schema,
                )
                unmatched = self.builder.mul(
                    self.builder.unot(self.builder.squash(cardinality)),
                    consume(unmatched_row),
                )
                return self.builder.add(matched, unmatched)

            return self.emit_multiplicity(outer, consume_outer, environment)
        if node_type in {nodes.SemiJoin, nodes.AntiJoin}:
            outer, function = node.children
            def consume_outer(outer_row: TermRef) -> TermRef:
                inner_environment = environment.bind_row(outer_row)
                inner = self.source[function].children[0]
                cardinality = self.emit_multiplicity(
                    inner,
                    lambda _row: self.builder.one(),
                    inner_environment,
                )
                existence = (
                    self.builder.unot(cardinality)
                    if node_type is nodes.AntiJoin
                    else self.builder.squash(cardinality)
                )
                return self.builder.mul(existence, consume(outer_row))

            return self.emit_multiplicity(outer, consume_outer, environment)
        if node_type is nodes.RelVar:
            relation = environment.relation(cast(VariablePayload, node.payload))
            schema = self._bag_sort(node.sort).schema
            return self.builder.sum(
                RowSort(schema),
                lambda row: self.builder.mul(
                    self.builder.at(relation, row), consume(row)
                ),
            )

        # These are the admitted semantic bag boundaries. Their internals are
        # lowered normally, then observed extensionally.
        if node_type in {
            nodes.BagLambda,
            nodes.Fold,
            nodes.GlobalFold,
            nodes.GroupFold,
            nodes.ForgetOrder,
            nodes.Window,
        }:
            schema = self._bag_sort(node.sort).schema
            boundary = self.compiler.translate(bag, environment)
            return self.builder.sum(
                RowSort(schema),
                lambda row: self.builder.mul(
                    self.builder.at(boundary, row), consume(row)
                ),
            )
        raise UExprTranslationError(
            f"Missing bag-multiplicity translation for {node_type.key}"
        )

    def _emit_outer_join(
        self,
        bag: TermId,
        consume: MultiplicityConsumer,
        environment: LoweringEnvironment,
    ) -> TermRef:
        node = self.source[bag]
        left, right, predicate = node.children
        left_schema = self._bag_sort(self.source[left].sort).schema
        right_schema = self._bag_sort(self.source[right].sort).schema
        original_schema = self.source.context.concat_schema(left_schema, right_schema)
        output_schema = self._bag_sort(node.sort).schema
        left_width = len(self.source.context.schema(left_schema).fields)
        right_width = len(self.source.context.schema(right_schema).fields)

        def match(left_row: TermRef, right_row: TermRef) -> TermRef:
            combined = self.scalar.concat_rows(
                left_row,
                left_schema,
                right_row,
                right_schema,
                original_schema,
            )
            return self.builder.indicator(
                self.scalar.apply_lambda(predicate, combined, environment)
            )

        def joined_row(left_row: TermRef, right_row: TermRef) -> TermRef:
            return self.scalar.concat_rows(
                left_row,
                left_schema,
                right_row,
                right_schema,
                output_schema,
            )

        def left_null_row(left_row: TermRef) -> TermRef:
            return self.builder.row(
                output_schema,
                (
                    *(
                        self.scalar.field(left_row, index)
                        for index in range(left_width)
                    ),
                    *(
                        self.builder.null(field.sql_type)
                        for field in self.source.context.schema(right_schema).fields
                    ),
                ),
            )

        def right_null_row(right_row: TermRef) -> TermRef:
            return self.builder.row(
                output_schema,
                (
                    *(
                        self.builder.null(field.sql_type)
                        for field in self.source.context.schema(left_schema).fields
                    ),
                    *(
                        self.scalar.field(right_row, index)
                        for index in range(right_width)
                    ),
                ),
            )

        def preserved_branch(
            preserved: TermId,
            optional: TermId,
            matches: Callable[[TermRef, TermRef], TermRef],
            combine: Callable[[TermRef, TermRef], TermRef],
            null_extend: Callable[[TermRef], TermRef],
        ) -> TermRef:
            def consume_preserved(preserved_row: TermRef) -> TermRef:
                matched = self._emit_boundary(
                    optional,
                    lambda optional_row: self.builder.mul(
                        matches(preserved_row, optional_row),
                        consume(combine(preserved_row, optional_row)),
                    ),
                    environment,
                )
                matching = self._emit_boundary(
                    optional,
                    lambda optional_row: matches(preserved_row, optional_row),
                    environment,
                )
                unmatched = self.builder.mul(
                    self.builder.unot(matching),
                    consume(null_extend(preserved_row)),
                )
                return self.builder.add(matched, unmatched)

            return self._emit_boundary(preserved, consume_preserved, environment)

        def left_branch() -> TermRef:
            return preserved_branch(
                left,
                right,
                match,
                joined_row,
                left_null_row,
            )

        if isinstance(node, nodes.LeftJoin):
            return left_branch()

        if isinstance(node, nodes.RightJoin):
            return preserved_branch(
                right,
                left,
                lambda right_row, left_row: match(left_row, right_row),
                lambda right_row, left_row: joined_row(left_row, right_row),
                right_null_row,
            )

        def consume_right_unmatched(right_row: TermRef) -> TermRef:
            matching = self._emit_boundary(
                left,
                lambda left_row: match(left_row, right_row),
                environment,
            )
            return self.builder.mul(
                self.builder.unot(matching),
                consume(right_null_row(right_row)),
            )

        return self.builder.add(
            left_branch(),
            self._emit_boundary(right, consume_right_unmatched, environment),
        )

    def _emit_boundary(
        self,
        bag: TermId,
        consume: MultiplicityConsumer,
        environment: LoweringEnvironment,
    ) -> TermRef:
        """Consume a nested outer join through an extensional bag boundary."""

        if type(self.source[bag]) not in {
            nodes.LeftJoin,
            nodes.RightJoin,
            nodes.FullJoin,
        }:
            return self.emit_multiplicity(bag, consume, environment)
        schema = self._bag_sort(self.source[bag].sort).schema
        boundary = self.compiler.translate(bag, environment)
        return self.builder.sum(
            RowSort(schema),
            lambda row: self.builder.mul(
                self.builder.at(boundary, row),
                consume(row),
            ),
        )

    @staticmethod
    def _bag_sort(sort: object) -> BagSort:
        return cast(BagSort, sort)
