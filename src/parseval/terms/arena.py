from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from itertools import count
from math import isfinite
from typing import Iterable, Iterator, TypeVar, cast

from parseval.errors import IRValidationError, expect

from . import terms
from .context import Context, Volatility
from .sorts import (
    BOOLEAN,
    MULTIPLICITY,
    PREDICATE,
    BagSort,
    IntervalValue,
    RelationSort,
    RowFunctionSort,
    RowSort,
    ScalarSort,
    SeqSort,
    Sort,
    TypeKind,
    is_relation_sort,
)
from .terms import (
    AggregateCallLayout,
    AggregatePayload,
    BaseRelationPayload,
    ExternalParameterPayload,
    FieldPayload,
    FoldPayload,
    LiteralPayload,
    NullPayload,
    OrderPayload,
    RowLambdaPayload,
    ScalarCallPayload,
    SchemaPayload,
    TermId,
    TermNode,
    TermPayload,
    VariablePayload,
    WindowFunctionKind,
    WindowPayload,
)

T = TypeVar("T")

_ARENA_IDS = count()


class TermArena:
    """Append-only storage with checked, canonical construction and hash-consing."""

    __slots__ = ("context", "_id", "_terms", "_intern")

    def __init__(self, context: Context) -> None:
        self.context = context
        self._id = next(_ARENA_IDS)
        self._terms: list[TermNode] = []
        self._intern: dict[TermNode, TermId] = {}

    def __len__(self) -> int:
        return len(self._terms)

    def __getitem__(self, term_id: TermId) -> TermNode:
        if not isinstance(term_id, TermId):
            raise IRValidationError(f"Expected TermId, got {term_id!r}")
        if term_id.arena != self._id:
            raise IRValidationError(f"Term {term_id!r} belongs to another arena")
        try:
            return self._terms[term_id.index]
        except IndexError as error:
            raise KeyError(f"Unknown term ID: {term_id!r}") from error

    def terms(self) -> Iterator[tuple[TermId, TermNode]]:
        for index, term in enumerate(self._terms):
            yield TermId(self._id, index), term

    def view(self, root: TermId) -> TermView:
        self[root]
        return TermView(self, root)

    def post_order(self, roots: Iterable[TermId]) -> Iterator[TermId]:
        """Deterministic DAG post-order; each node is yielded once."""

        seen: set[TermId] = set()
        for root in roots:
            self[root]
            stack: list[tuple[TermId, bool]] = [(root, False)]
            while stack:
                term_id, expanded = stack.pop()
                if term_id in seen:
                    continue
                if expanded:
                    seen.add(term_id)
                    yield term_id
                    continue
                stack.append((term_id, True))
                for child in reversed(self[term_id].children):
                    if child not in seen:
                        stack.append((child, False))

    def intern_checked(
        self,
        node_type: type[terms.TermNode],
        children: Iterable[TermId] = (),
        payload: TermPayload = None,
    ) -> TermId:
        """Validate operands before applying local canonicalization and interning."""

        expect(
            isinstance(node_type, type) and node_type in terms.NODE_TYPES,
            f"Expected a registered TermNode subclass, got {node_type!r}",
        )
        child_ids = tuple(children)
        child_nodes = tuple(self[child] for child in child_ids)
        _validate_payload_for_node(node_type, payload)
        sort = self._infer(node_type, child_nodes, payload)
        if node_type in (terms.Add, terms.Mul):
            return self._intern_ac(node_type, child_ids, child_nodes)
        if node_type in (terms.And3, terms.Or3):
            return self._intern_commutative_predicate(node_type, child_ids, child_nodes)
        if node_type is terms.Indicator:
            predicate = child_nodes[0]
            if isinstance(predicate, terms.True3):
                return self.intern_checked(terms.One)
            if isinstance(predicate, (terms.False3, terms.Unknown3)):
                return self.intern_checked(terms.Zero)
            if type(predicate) in (terms.And3, terms.Or3):
                indicators = tuple(
                    self.intern_checked(terms.Indicator, (child,))
                    for child in predicate.children
                )
                if isinstance(predicate, terms.And3):
                    return self.intern_checked(terms.Mul, indicators)
                return self.intern_checked(
                    terms.Squash, (self.intern_checked(terms.Add, indicators),)
                )
        return self._intern_node(node_type(sort, child_ids, payload=payload))

    def _intern_ac(
        self,
        node_type: type[terms.TermNode],
        children: tuple[TermId, ...],
        child_nodes: tuple[TermNode, ...],
    ) -> TermId:
        """Construct the unique flat, ordered representation of an AC term."""

        flattened: list[TermId] = []
        identity = terms.Zero if node_type is terms.Add else terms.One
        for child, node in zip(children, child_nodes, strict=True):
            if node_type is terms.Mul and isinstance(node, terms.Zero):
                return self.intern_checked(terms.Zero)
            if type(node) is identity:
                continue
            if type(node) is node_type:
                flattened.extend(node.children)
            else:
                flattened.append(child)

        if not flattened:
            return self.intern_checked(identity)
        flattened.sort(key=lambda term: term.index)
        if len(flattened) == 1:
            return flattened[0]
        child_ids = tuple(flattened)
        return self._intern_node(node_type(MULTIPLICITY, child_ids))

    def _intern_commutative_predicate(
        self,
        node_type: type[terms.TermNode],
        children: tuple[TermId, ...],
        child_nodes: tuple[TermNode, ...],
    ) -> TermId:
        identity = terms.True3 if node_type is terms.And3 else terms.False3
        absorbing = terms.False3 if node_type is terms.And3 else terms.True3
        for node in child_nodes:
            if type(node) is absorbing:
                return self.intern_checked(absorbing)
        remaining = tuple(
            child
            for child, node in zip(children, child_nodes, strict=True)
            if type(node) is not identity
        )
        if not remaining:
            return self.intern_checked(identity)
        if len(remaining) == 1:
            return remaining[0]
        ordered = tuple(sorted(remaining, key=lambda term: term.index))
        return self._intern_node(node_type(PREDICATE, ordered))

    def row_var(self, depth: int, sort: RowSort) -> TermId:
        expect(isinstance(sort, RowSort), "Row variables require RowSort")
        self.context.schema(sort.schema)
        return self._intern_node(terms.RowVar(sort, payload=VariablePayload(depth)))

    def rel_var(self, depth: int, sort: RelationSort) -> TermId:
        expect(is_relation_sort(sort), "Relational variables require a relation sort")
        self.context.schema(sort.schema)
        return self._intern_node(terms.RelVar(sort, payload=VariablePayload(depth)))

    def rebuild(self, original: TermId, children: Iterable[TermId]) -> TermId:
        node = self[original]
        child_ids = tuple(children)
        if child_ids == node.children:
            return original
        expect(
            type(node) not in (terms.RowVar, terms.RelVar),
            "Variable nodes do not have children",
        )
        rebuilt = self.intern_checked(type(node), child_ids, node.payload)
        expect(self[rebuilt].sort == node.sort, "Rebuilding changed the term sort")
        return rebuilt

    def _intern_node(self, node: TermNode) -> TermId:
        existing = self._intern.get(node)
        if existing is not None:
            return existing
        term_id = TermId(self._id, len(self._terms))
        self._terms.append(node)
        self._intern[node] = term_id
        return term_id

    def _infer(
        self,
        node_type: type[terms.TermNode],
        children: tuple[TermNode, ...],
        payload: TermPayload,
    ) -> Sort:
        child_sorts = tuple(child.sort for child in children)

        if node_type is terms.Literal:
            _arity(node_type, children, 0)
            literal = cast(LiteralPayload, payload)
            _validate_literal(literal.value, literal.sql_type.kind)
            return ScalarSort(literal.sql_type, False)

        if node_type is terms.Null:
            _arity(node_type, children, 0)
            null = cast(NullPayload, payload)
            return ScalarSort(null.sql_type, True)

        if node_type in (terms.RowVar, terms.RelVar):
            raise IRValidationError(
                f"{node_type.key} must be created through "
                "the typed variable constructor"
            )

        if node_type is terms.Field:
            _arity(node_type, children, 1)
            field = cast(FieldPayload, payload)
            row = _expect_type(node_type, child_sorts[0], RowSort)
            schema = self.context.schema(row.schema)
            try:
                field_type = schema.fields[field.index]
            except IndexError as error:
                raise IRValidationError(
                    "Field index is outside the row schema"
                ) from error
            return field_type

        if node_type is terms.ExternalParameter:
            _arity(node_type, children, 0)
            parameter = cast(ExternalParameterPayload, payload)
            return self.context.parameter(parameter.parameter).sort

        if node_type is terms.ScalarCall:
            call = cast(ScalarCallPayload, payload)
            function = self.context.function(call.function)
            _arity(node_type, children, len(function.parameters))
            for actual, expected in zip(child_sorts, function.parameters, strict=True):
                _expect_assignable_scalar(node_type, actual, expected)
            if function.volatility is Volatility.VOLATILE:
                if call.call_site is None:
                    raise IRValidationError("Volatile calls require CallSiteId")
            elif call.call_site is not None:
                raise IRValidationError("Only volatile calls may carry CallSiteId")
            return function.result

        if node_type is terms.Case:
            _arity(node_type, children, 3)
            _expect_sort(node_type, child_sorts[0], PREDICATE)
            then_sort = _expect_type(node_type, child_sorts[1], ScalarSort)
            else_sort = _expect_type(node_type, child_sorts[2], ScalarSort)
            if then_sort.sql_type != else_sort.sql_type:
                raise IRValidationError("CASE branches have different SQL types")
            return ScalarSort(
                then_sort.sql_type,
                then_sort.nullable or else_sort.nullable,
            )

        if node_type is terms.Scalarize:
            _arity(node_type, children, 1)
            bag = _expect_type(node_type, child_sorts[0], BagSort)
            schema = self.context.schema(bag.schema)
            if len(schema.fields) != 1:
                raise IRValidationError("Scalarize requires a single-column row schema")
            scalar_field = schema.fields[0]
            return ScalarSort(scalar_field.sql_type, True)

        if node_type in (terms.True3, terms.False3, terms.Unknown3):
            _arity(node_type, children, 0)
            return PREDICATE

        if node_type is terms.ToPredicate:
            _arity(node_type, children, 1)
            scalar = _expect_type(node_type, child_sorts[0], ScalarSort)
            expect(scalar.sql_type == BOOLEAN, "Predicate input must be SQL Boolean")
            return PREDICATE

        if node_type is terms.ToBoolean:
            _arity(node_type, children, 1)
            _expect_sort(node_type, child_sorts[0], PREDICATE)
            return ScalarSort(BOOLEAN, nullable=True)

        if node_type in (terms.Eq3, terms.Lt3, terms.IsNotDistinct):
            _arity(node_type, children, 2)
            left = _expect_type(node_type, child_sorts[0], ScalarSort)
            right = _expect_type(node_type, child_sorts[1], ScalarSort)
            if left.sql_type != right.sql_type:
                raise IRValidationError(
                    f"{node_type.key} operands have different SQL types"
                )
            return PREDICATE

        if node_type is terms.InSubquery:
            _arity(node_type, children, 2)
            needle = _expect_type(node_type, child_sorts[0], ScalarSort)
            candidates = _expect_type(node_type, child_sorts[1], BagSort)
            schema = self.context.schema(candidates.schema)
            if len(schema.fields) != 1:
                raise IRValidationError(
                    "IN_SUBQUERY requires a single-column candidate bag"
                )
            if needle.sql_type != schema.fields[0].sql_type:
                raise IRValidationError(
                    "IN_SUBQUERY needle and candidate have different SQL types"
                )
            return PREDICATE

        if node_type in (terms.Like3, terms.ILike3):
            _arity(node_type, children, 2)
            value = _expect_type(node_type, child_sorts[0], ScalarSort)
            pattern = _expect_type(node_type, child_sorts[1], ScalarSort)
            if (
                value.sql_type.kind is not TypeKind.STRING
                or pattern.sql_type.kind is not TypeKind.STRING
            ):
                raise IRValidationError(
                    f"{node_type.key} operands must be strings"
                )
            return PREDICATE

        if node_type in (terms.IsNull, terms.IsNotNull):
            _arity(node_type, children, 1)
            _expect_type(node_type, child_sorts[0], ScalarSort)
            return PREDICATE

        if node_type in (terms.And3, terms.Or3):
            _arity(node_type, children, 2)
            _expect_sort(node_type, child_sorts[0], PREDICATE)
            _expect_sort(node_type, child_sorts[1], PREDICATE)
            return PREDICATE

        if node_type is terms.Not3:
            _arity(node_type, children, 1)
            _expect_sort(node_type, child_sorts[0], PREDICATE)
            return PREDICATE

        if node_type is terms.Row:
            row_payload = cast(SchemaPayload, payload)
            schema = self.context.schema(row_payload.schema)
            _arity(node_type, children, len(schema.fields))
            for actual, schema_field in zip(child_sorts, schema.fields, strict=True):
                _expect_assignable_scalar(
                    node_type,
                    actual,
                    schema_field,
                )
            return RowSort(row_payload.schema)

        if node_type is terms.RowIdentityEq:
            _arity(node_type, children, 2)
            left_row = _expect_type(node_type, child_sorts[0], RowSort)
            right_row = _expect_type(node_type, child_sorts[1], RowSort)
            if left_row != right_row:
                raise IRValidationError("Row identity equality requires equal schemas")
            return PREDICATE

        if node_type is terms.RowLambda:
            _arity(node_type, children, 1)
            lambda_payload = cast(RowLambdaPayload, payload)
            self.context.schema(lambda_payload.input_schema)
            return RowFunctionSort(RowSort(lambda_payload.input_schema), child_sorts[0])

        if node_type in (terms.Zero, terms.One):
            _arity(node_type, children, 0)
            return MULTIPLICITY

        if node_type in (terms.Add, terms.Mul):
            for child_sort in child_sorts:
                _expect_sort(node_type, child_sort, MULTIPLICITY)
            return MULTIPLICITY

        if node_type in (terms.Squash, terms.UNot):
            _arity(node_type, children, 1)
            _expect_sort(node_type, child_sorts[0], MULTIPLICITY)
            return MULTIPLICITY

        if node_type is terms.Indicator:
            _arity(node_type, children, 1)
            _expect_sort(node_type, child_sorts[0], PREDICATE)
            return MULTIPLICITY

        if node_type is terms.Sum:
            _arity(node_type, children, 1)
            sum_function = _expect_type(node_type, child_sorts[0], RowFunctionSort)
            _expect_sort(node_type, sum_function.result, MULTIPLICITY)
            return MULTIPLICITY

        if node_type is terms.At:
            _arity(node_type, children, 2)
            bag = _expect_type(node_type, child_sorts[0], BagSort)
            row = _expect_type(node_type, child_sorts[1], RowSort)
            if bag.schema != row.schema:
                raise IRValidationError("AT row schema does not match bag schema")
            return MULTIPLICITY

        if node_type is terms.BagLambda:
            _arity(node_type, children, 1)
            bag_function = _expect_type(node_type, child_sorts[0], RowFunctionSort)
            _expect_sort(node_type, bag_function.result, MULTIPLICITY)
            return BagSort(bag_function.input.schema)

        if node_type is terms.Base:
            _arity(node_type, children, 0)
            base = cast(BaseRelationPayload, payload)
            return BagSort(self.context.relation(base.relation).schema)

        if node_type is terms.Empty:
            _arity(node_type, children, 0)
            empty = cast(SchemaPayload, payload)
            self.context.schema(empty.schema)
            return BagSort(empty.schema)

        if node_type is terms.Filter:
            _arity(node_type, children, 2)
            bag = _expect_type(node_type, child_sorts[0], BagSort)
            predicate = _expect_type(node_type, child_sorts[1], RowFunctionSort)
            _expect_row_function(node_type, predicate, bag.schema, PREDICATE)
            return bag

        if node_type is terms.Map:
            _arity(node_type, children, 2)
            bag = _expect_type(node_type, child_sorts[0], BagSort)
            mapper = _expect_type(node_type, child_sorts[1], RowFunctionSort)
            if mapper.input != RowSort(bag.schema):
                raise IRValidationError("MAP lambda has the wrong input row sort")
            output = _expect_type(node_type, mapper.result, RowSort)
            return BagSort(output.schema)

        if node_type is terms.SeqMap:
            _arity(node_type, children, 2)
            sequence = _expect_type(node_type, child_sorts[0], SeqSort)
            mapper = _expect_type(node_type, child_sorts[1], RowFunctionSort)
            if mapper.input != RowSort(sequence.schema):
                raise IRValidationError("SEQ_MAP lambda has the wrong input row sort")
            output = _expect_type(node_type, mapper.result, RowSort)
            return SeqSort(output.schema)

        if node_type is terms.Product:
            _arity(node_type, children, 2)
            left_bag = _expect_type(node_type, child_sorts[0], BagSort)
            right_bag = _expect_type(node_type, child_sorts[1], BagSort)
            return BagSort(
                self.context.concat_schema(left_bag.schema, right_bag.schema)
            )

        if node_type is terms.Join:
            _arity(node_type, children, 3)
            join_left = _expect_type(node_type, child_sorts[0], BagSort)
            join_right = _expect_type(node_type, child_sorts[1], BagSort)
            output_schema = self.context.concat_schema(
                join_left.schema, join_right.schema
            )
            predicate = _expect_type(node_type, child_sorts[2], RowFunctionSort)
            _expect_row_function(node_type, predicate, output_schema, PREDICATE)
            return BagSort(output_schema)

        if node_type in (terms.LeftJoin, terms.RightJoin, terms.FullJoin):
            _arity(node_type, children, 3)
            join_left = _expect_type(node_type, child_sorts[0], BagSort)
            join_right = _expect_type(node_type, child_sorts[1], BagSort)
            output_schema = self.context.outer_join_schema(
                join_left.schema,
                join_right.schema,
                nullable_left=node_type in (terms.RightJoin, terms.FullJoin),
                nullable_right=node_type in (terms.LeftJoin, terms.FullJoin),
            )
            predicate_schema = self.context.concat_schema(
                join_left.schema, join_right.schema
            )
            predicate = _expect_type(node_type, child_sorts[2], RowFunctionSort)
            _expect_row_function(node_type, predicate, predicate_schema, PREDICATE)
            return BagSort(output_schema)

        if node_type is terms.UnionAll:
            _arity(node_type, children, 2)
            union_left = _expect_type(node_type, child_sorts[0], BagSort)
            union_right = _expect_type(node_type, child_sorts[1], BagSort)
            expect(union_left == union_right, "UNION ALL requires equal bag schemas")
            return union_left

        if node_type is terms.Distinct:
            _arity(node_type, children, 1)
            return _expect_type(node_type, child_sorts[0], BagSort)

        if node_type is terms.Fold:
            _arity(node_type, children, 1)
            aggregate = cast(AggregatePayload, payload)
            spec = self.context.aggregate(aggregate.aggregate)
            relation = child_sorts[0]
            if isinstance(relation, BagSort):
                if spec.order_sensitive:
                    raise IRValidationError(
                        "Order-sensitive aggregates require sequence input"
                    )
                schema_id = relation.schema
            elif isinstance(relation, SeqSort):
                schema_id = relation.schema
            else:
                raise IRValidationError("FOLD requires a bag or sequence")
            _validate_aggregate_input(self.context, node_type, schema_id, spec.input)
            return spec.output

        if node_type in (terms.GroupFold, terms.GlobalFold):
            fold = cast(FoldPayload, payload)
            self.context.schema(fold.output_schema)
            minimum = 2 if node_type is terms.GroupFold else 1
            if len(children) < minimum:
                raise IRValidationError(f"{node_type.key} has too few children")
            input_bag = _expect_type(node_type, child_sorts[0], BagSort)
            key_schema = None
            start = 1
            if node_type is terms.GroupFold:
                key_function = _expect_type(node_type, child_sorts[1], RowFunctionSort)
                if key_function.input != RowSort(input_bag.schema):
                    raise IRValidationError("Group key lambda has wrong input schema")
                key_schema = _expect_type(
                    node_type, key_function.result, RowSort
                ).schema
                start = 2
            self._validate_fold_calls(
                node_type, input_bag, children, child_sorts, fold.calls, start
            )
            _validate_fold_output(
                self.context,
                node_type,
                fold.output_schema,
                key_schema,
                fold.calls,
            )
            return BagSort(fold.output_schema)

        if node_type is terms.OrderBy:
            order = cast(OrderPayload, payload)
            expect(
                len(children) == 1 + len(order.keys),
                "ORDER_BY key count does not match payload",
            )
            bag = _expect_type(node_type, child_sorts[0], BagSort)
            self._validate_order_keys(node_type, bag, child_sorts[1:], order)
            return SeqSort(bag.schema)

        if node_type in (terms.Take, terms.Drop):
            _arity(node_type, children, 2)
            _expect_bound(node_type, children[0])
            return _expect_type(node_type, child_sorts[1], SeqSort)

        if node_type is terms.Slice:
            _arity(node_type, children, 3)
            _expect_bound(node_type, children[0])
            _expect_bound(node_type, children[1])
            return _expect_type(node_type, child_sorts[2], SeqSort)

        if node_type is terms.ForgetOrder:
            _arity(node_type, children, 1)
            sequence = _expect_type(node_type, child_sorts[0], SeqSort)
            return BagSort(sequence.schema)

        if node_type is terms.TopK:
            order = cast(OrderPayload, payload)
            expect(
                len(children) == 3 + len(order.keys),
                "TOPK key count does not match payload",
            )
            bag = _expect_type(node_type, child_sorts[0], BagSort)
            _expect_bound(node_type, children[1])
            _expect_bound(node_type, children[2])
            self._validate_order_keys(node_type, bag, child_sorts[3:], order)
            return SeqSort(bag.schema)

        if node_type is terms.Window:
            window = cast(WindowPayload, payload)
            source = _expect_type(node_type, child_sorts[0], BagSort)
            source_fields = self.context.schema(source.schema).fields
            output_fields = self.context.schema(window.output_schema).fields
            expected_fields = source_fields + tuple(call.result for call in window.calls)
            expect(
                output_fields == expected_fields,
                "Window output schema must append its call results to the input",
            )

            def scalar_lambda(index: int) -> ScalarSort:
                if index <= 0 or index >= len(child_sorts):
                    raise IRValidationError("Window lambda index is out of range")
                function = _expect_type(
                    node_type, child_sorts[index], RowFunctionSort
                )
                expect(
                    function.input == RowSort(source.schema),
                    "Window lambda has the wrong input schema",
                )
                return _expect_type(node_type, function.result, ScalarSort)

            for call in window.calls:
                arguments = tuple(
                    scalar_lambda(index) for index in call.argument_children
                )
                for index in call.partition_children:
                    scalar_lambda(index)
                for index in call.order_children:
                    scalar_lambda(index)
                if call.filter_child is not None:
                    filter_function = _expect_type(
                        node_type,
                        child_sorts[call.filter_child],
                        RowFunctionSort,
                    )
                    expect(
                        filter_function.input == RowSort(source.schema)
                        and filter_function.result == PREDICATE,
                        "Window filter must be a predicate over the input row",
                    )

                if call.kind is WindowFunctionKind.AGGREGATE:
                    spec = self.context.aggregate(call.aggregate)
                    expected_arity = 0 if spec.input is None else 1
                    expect(
                        len(arguments) == expected_arity,
                        "Aggregate window has the wrong argument count",
                    )
                    if spec.input is not None:
                        _expect_assignable_scalar(node_type, arguments[0], spec.input)
                    expect(
                        call.result == spec.output,
                        "Aggregate window result does not match its declaration",
                    )
                elif call.kind in {
                    WindowFunctionKind.ROW_NUMBER,
                    WindowFunctionKind.RANK,
                    WindowFunctionKind.DENSE_RANK,
                }:
                    expect(not arguments, "Ranking windows do not accept arguments")
                    expect(
                        call.result.sql_type.kind is TypeKind.INTEGER
                        and not call.result.nullable,
                        "Ranking windows return a non-null integer",
                    )
                else:
                    expect(
                        1 <= len(arguments) <= 3,
                        "LAG and LEAD accept one to three arguments",
                    )
                    expect(
                        arguments[0].sql_type == call.result.sql_type,
                        "LAG/LEAD result type must match their value argument",
                    )
                    if len(arguments) >= 2:
                        expect(
                            arguments[1].sql_type.kind is TypeKind.INTEGER,
                            "LAG/LEAD offset must be an integer",
                        )
                    if len(arguments) == 3:
                        expect(
                            arguments[2].sql_type == arguments[0].sql_type,
                            "LAG/LEAD default must match their value argument",
                        )
            return BagSort(window.output_schema)

        if node_type is terms.LetRel:
            _arity(node_type, children, 2)
            _expect_relation(node_type, child_sorts[0])
            return child_sorts[1]
        if node_type in (
            terms.DependentJoin,
            terms.DependentLeftJoin,
            terms.SemiJoin,
            terms.AntiJoin,
        ):
            _arity(node_type, children, 2)

            outer = _expect_type(
                node_type,
                child_sorts[0],
                BagSort,
            )

            relation_function = _expect_type(
                node_type,
                child_sorts[1],
                RowFunctionSort,
            )

            expect(
                relation_function.input == RowSort(outer.schema),
                f"{node_type.key} relation function has the wrong outer-row input sort",
            )

            inner = _expect_type(
                node_type,
                relation_function.result,
                BagSort,
            )

            if node_type is terms.DependentJoin:
                return BagSort(
                    self.context.concat_schema(
                        outer.schema,
                        inner.schema,
                    )
                )

            if node_type is terms.DependentLeftJoin:
                return BagSort(
                    self.context.outer_join_schema(
                        outer.schema,
                        inner.schema,
                        nullable_left=False,
                        nullable_right=True,
                    )
                )

            return outer
        raise IRValidationError(f"No typing rule for operator {node_type.key}")

    def _validate_order_keys(
        self,
        node_type: type[terms.TermNode],
        bag: BagSort,
        key_sorts: tuple[Sort, ...],
        order: OrderPayload,
    ) -> None:
        for key_sort, key_spec in zip(key_sorts, order.keys, strict=True):
            function = _expect_type(node_type, key_sort, RowFunctionSort)
            expect(
                function.input == RowSort(bag.schema),
                "Order key has the wrong input schema",
            )
            _expect_type(node_type, function.result, ScalarSort)
            if key_spec.collation is not None:
                self.context.require_collation(key_spec.collation)

    def _validate_fold_calls(
        self,
        node_type: type[terms.TermNode],
        input_bag: BagSort,
        children: tuple[TermNode, ...],
        child_sorts: tuple[Sort, ...],
        calls: tuple[AggregateCallLayout, ...],
        first_lambda: int,
    ) -> None:
        for call in calls:
            spec = self.context.aggregate(call.aggregate)
            if spec.order_sensitive:
                raise IRValidationError(
                    "Bag GroupFold/GlobalFold cannot use order-sensitive aggregates"
                )
            if call.argument_child is None:
                if spec.input is not None:
                    raise IRValidationError("Aggregate requires an argument")
            else:
                if call.argument_child < first_lambda or call.argument_child >= len(
                    children
                ):
                    raise IRValidationError(
                        "Aggregate argument child is outside the fold"
                    )
                argument = _expect_type(
                    node_type,
                    child_sorts[call.argument_child],
                    RowFunctionSort,
                )
                if spec.input is None:
                    raise IRValidationError("Star aggregate cannot have an argument")
                _expect_sort(node_type, argument.input, RowSort(input_bag.schema))
                _expect_assignable_scalar(node_type, argument.result, spec.input)
            if call.filter_child is not None:
                if call.filter_child < first_lambda or call.filter_child >= len(
                    children
                ):
                    raise IRValidationError(
                        "Aggregate filter child is outside the fold"
                    )
                predicate = _expect_type(
                    node_type,
                    child_sorts[call.filter_child],
                    RowFunctionSort,
                )
                _expect_row_function(node_type, predicate, input_bag.schema, PREDICATE)


def _validate_payload_for_node(
    node_type: type[terms.TermNode], payload: object
) -> None:
    expected = node_type.payload_type
    if expected is None:
        if payload is not None:
            raise IRValidationError(f"{node_type.key} does not accept a payload")
        return
    if type(payload) is not expected:
        raise IRValidationError(
            f"{node_type.key} expects {expected.__name__}, got {payload!r}"
        )


def _arity(
    node_type: type[terms.TermNode], children: tuple[TermNode, ...], expected: int
) -> None:
    if len(children) != expected:
        raise IRValidationError(
            f"{node_type.key} expects {expected} children, got {len(children)}"
        )


def _expect_sort(node_type: type[terms.TermNode], actual: Sort, expected: Sort) -> None:
    if actual != expected:
        raise IRValidationError(f"{node_type.key} expects {expected!r}, got {actual!r}")


def _expect_type(node_type: type[terms.TermNode], actual: Sort, expected: type[T]) -> T:
    if not isinstance(actual, expected):
        raise IRValidationError(
            f"{node_type.key} expects {expected.__name__}, got {actual!r}"
        )
    return cast(T, actual)


def _expect_relation(node_type: type[terms.TermNode], actual: Sort) -> RelationSort:
    expect(
        is_relation_sort(actual), f"{node_type.key} expects a relation, got {actual!r}"
    )
    return cast(RelationSort, actual)


def _expect_assignable_scalar(
    node_type: type[terms.TermNode], actual: Sort, expected: ScalarSort
) -> None:
    scalar = _expect_type(node_type, actual, ScalarSort)
    expect(
        scalar.sql_type == expected.sql_type, f"{node_type.key} scalar SQL types differ"
    )
    expect(
        not scalar.nullable or expected.nullable,
        f"{node_type.key} nullable scalar is not assignable to non-null target",
    )


def _expect_row_function(
    node_type: type[terms.TermNode],
    function: RowFunctionSort,
    input_schema,
    result: Sort,
) -> None:
    expected = RowFunctionSort(RowSort(input_schema), result)
    expect(
        function == expected, f"{node_type.key} expects {expected!r}, got {function!r}"
    )


def _expect_bound(node_type: type[terms.TermNode], node: TermNode) -> None:
    scalar = _expect_type(node_type, node.sort, ScalarSort)
    if scalar.nullable or scalar.sql_type.kind is not TypeKind.INTEGER:
        raise IRValidationError(f"{node_type.key} requires a non-null integer bound")
    if isinstance(node, terms.Literal):
        literal = cast(LiteralPayload, node.payload)
        value = literal.value

        expect(value >= 0, f"{node_type.key} literal bound must be nonnegative")


def _validate_aggregate_input(
    context: Context,
    node_type: type[terms.TermNode],
    schema_id,
    expected: ScalarSort | None,
) -> None:
    schema = context.schema(schema_id)
    expected_count = 0 if expected is None else 1
    expect(
        len(schema.fields) == expected_count,
        f"{node_type.key} aggregate input requires {expected_count} fields",
    )
    if expected is not None:
        field = schema.fields[0]
        _expect_assignable_scalar(
            node_type,
            field,
            expected,
        )


def _validate_fold_output(
    context: Context,
    node_type: type[terms.TermNode],
    output_schema_id,
    key_schema_id,
    calls: tuple[AggregateCallLayout, ...],
) -> None:
    output = context.schema(output_schema_id)
    key_fields = () if key_schema_id is None else context.schema(key_schema_id).fields
    expect(
        len(output.fields) == len(key_fields) + len(calls),
        "Fold output schema has the wrong field count",
    )
    expect(
        output.fields[: len(key_fields)] == key_fields,
        "Fold output key fields do not match key schema",
    )

    for field, call in zip(output.fields[len(key_fields) :], calls, strict=True):
        expected = context.aggregate(call.aggregate).output
        expect(
            field == expected,
            f"{node_type.key} aggregate output field does not match aggregate result",
        )


def _validate_literal(value: object, kind: TypeKind) -> None:
    try:
        hash(value)
    except TypeError as error:
        raise IRValidationError(
            "Literal values must be immutable and hashable"
        ) from error
    valid = False
    if kind is TypeKind.BOOLEAN:
        valid = isinstance(value, bool)
    elif kind is TypeKind.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif kind is TypeKind.FLOAT:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if isinstance(value, float) and not isfinite(value):
            valid = False
    elif kind is TypeKind.DECIMAL:
        valid = isinstance(value, Decimal) and value.is_finite()
    elif kind is TypeKind.STRING:
        valid = isinstance(value, str)
    elif kind is TypeKind.DATE:
        valid = isinstance(value, date) and not isinstance(value, datetime)
    elif kind is TypeKind.TIME:
        valid = isinstance(value, time)
    elif kind is TypeKind.TIMESTAMP:
        valid = isinstance(value, datetime)
    elif kind is TypeKind.INTERVAL:
        valid = isinstance(value, IntervalValue)
    elif kind is TypeKind.OPAQUE:
        valid = False
    if not valid:
        raise IRValidationError(f"Invalid literal {value!r} for SQL type {kind.value}")


@dataclass(frozen=True, slots=True)
class TermView:
    """Arena-aware view of a complete expression rooted at a TermId."""

    arena: TermArena
    root: TermId

    def __repr__(self) -> str:
        from .printer import format_term

        return format_term(
            self.arena,
            self.root,
            show_sorts=True,
        )

    def __str__(self) -> str:
        from .printer import format_term

        return format_term(
            self.arena,
            self.root,
        )

    def dump(self) -> str:
        """Return the reachable hash-consed DAG representation."""
        from .printer import format_arena

        return format_arena(
            self.arena,
            self.root,
        )
