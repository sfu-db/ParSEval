from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from parseval.errors import IRValidationError, expect

from . import terms
from .arena import TermArena
from .context import ScalarFunctionSpec, Volatility
from .names import (
    AggregateSpecId,
    CallSiteId,
    CollationId,
    FunctionId,
    ParameterId,
    RelationId,
    SchemaId,
)
from .sorts import (
    BagSort,
    RowFunctionSort,
    RowSort,
    ScalarSort,
    is_relation_sort,
    relation_schema,
)
from .terms import (
    AggregateCallLayout,
    AggregateMode,
    AggregatePayload,
    BaseRelationPayload,
    Direction,
    ExternalParameterPayload,
    FieldPayload,
    FoldPayload,
    LiteralPayload,
    NullPayload,
    NullPlacement,
    OrderKeySpec,
    OrderPayload,
    RowLambdaPayload,
    ScalarCallPayload,
    SchemaPayload,
    TermId,
    TermPayload,
    WindowCallLayout,
    WindowFrame,
    WindowFunctionKind,
    WindowPayload,
)
from .types import ScalarType


@dataclass(frozen=True, slots=True)
class LexicalScope:
    """Immutable identity of the binders visible at a construction site."""

    rows: tuple[object, ...] = ()
    relations: tuple[object, ...] = ()

    def bind_row(self) -> LexicalScope:
        return LexicalScope((*self.rows, object()), self.relations)

    def bind_relation(self) -> LexicalScope:
        return LexicalScope(self.rows, (*self.relations, object()))

    def contains(self, other: LexicalScope) -> bool:
        return (
            self.rows[: len(other.rows)] == other.rows
            and self.relations[: len(other.relations)] == other.relations
        )

    @classmethod
    def covering(cls, scopes: Iterable[LexicalScope]) -> LexicalScope:
        """Return the least scope containing every compatible input scope."""

        rows: tuple[object, ...] = ()
        relations: tuple[object, ...] = ()
        for scope in scopes:
            if len(scope.rows) > len(rows):
                if scope.rows[: len(rows)] != rows:
                    raise IRValidationError("Row scopes belong to sibling binders")
                rows = scope.rows
            elif rows[: len(scope.rows)] != scope.rows:
                raise IRValidationError("Row scopes belong to sibling binders")

            if len(scope.relations) > len(relations):
                if scope.relations[: len(relations)] != relations:
                    raise IRValidationError(
                        "Relation scopes belong to sibling binders"
                    )
                relations = scope.relations
            elif relations[: len(scope.relations)] != scope.relations:
                raise IRValidationError("Relation scopes belong to sibling binders")
        return cls(rows, relations)


@dataclass(frozen=True, slots=True)
class ScopedTerm:
    """Term handle paired with the lexical scope in which it was built."""

    term_id: TermId
    scope: LexicalScope


TermRef = TermId | ScopedTerm
RowBody = Callable[[TermRef], TermRef]


def lexical_scope(term: TermRef) -> LexicalScope:
    return term.scope if isinstance(term, ScopedTerm) else LexicalScope()


@dataclass(frozen=True, slots=True)
class AggregateCall:
    aggregate: AggregateSpecId
    argument: RowBody | None = None
    filter: RowBody | None = None
    distinct: bool = False


@dataclass(frozen=True, slots=True)
class OrderKey:
    expression: RowBody
    direction: Direction = Direction.ASC
    nulls: NullPlacement = NullPlacement.LAST
    collation: CollationId | None = None


@dataclass(frozen=True, slots=True)
class WindowCall:
    kind: WindowFunctionKind
    result: ScalarSort
    arguments: tuple[RowBody, ...]
    partition_by: tuple[RowBody, ...]
    order_by: tuple[OrderKey, ...]
    frame: WindowFrame
    aggregate: AggregateSpecId | None = None
    filter: RowBody | None = None
    distinct: bool = False


class IRBuilder:
    """HOAS-style ergonomic construction over one checked TermArena."""

    __slots__ = (
        "arena",
        "_lexical_scope",
        "_rebase_cache",
    )

    def __init__(self, arena: TermArena) -> None:
        self.arena = arena
        self._lexical_scope = LexicalScope()
        self._rebase_cache: dict[tuple[TermId, int, int], TermId] = {}

    def finish(self, term: TermRef) -> TermId:
        """Resolve a completed term and verify that no binder variable escapes."""

        term_id = self.resolve(term)
        from .verify import verify_closed

        verify_closed(self.arena, term_id)
        return term_id

    def checked(
        self,
        node_type: type[terms.TermNode],
        children: Iterable[TermRef] = (),
        payload: TermPayload = None,
    ) -> TermRef:
        child_ids = tuple(self.resolve(child) for child in children)
        return self._scope(self.arena.intern_checked(node_type, child_ids, payload))

    def build_in_scope(
        self,
        scope: LexicalScope,
        body: Callable[[], TermRef],
    ) -> TermRef:
        """Build a term in a visible ancestor scope and restore the caller scope."""

        if not self._lexical_scope.contains(scope):
            raise IRValidationError("Cannot build in an unrelated lexical scope")
        caller_scope = self._lexical_scope
        self._lexical_scope = scope
        try:
            return body()
        finally:
            self._lexical_scope = caller_scope

    def resolve(self, term: TermRef) -> TermId:
        if isinstance(term, TermId):
            self.arena[term]
            return term

        if not isinstance(term, ScopedTerm):
            raise TypeError(f"Expected TermRef, got {term!r}")
        if not self._lexical_scope.contains(term.scope):
            raise IRValidationError(
                "A scoped term escaped its lexical binder path or crossed a sibling"
            )
        row_delta = len(self._lexical_scope.rows) - len(term.scope.rows)
        relation_delta = len(self._lexical_scope.relations) - len(
            term.scope.relations
        )
        if row_delta == 0 and relation_delta == 0:
            return term.term_id

        key = (term.term_id, row_delta, relation_delta)
        cached = self._rebase_cache.get(key)
        if cached is not None:
            return cached

        from .binding import shift_vars

        rebased = shift_vars(
            self.arena,
            term.term_id,
            row_delta=row_delta,
            relation_delta=relation_delta,
        )
        self._rebase_cache[key] = rebased
        return rebased

    def _scope(self, term_id: TermId, scope: LexicalScope | None = None) -> TermRef:
        active = self._lexical_scope if scope is None else scope
        if not active.rows and not active.relations:
            return term_id
        return ScopedTerm(term_id, active)

    # SQL values and predicates.

    def literal(self, value: object, sql_type: ScalarType) -> TermRef:
        if value is None:
            return self.null(sql_type)
        return self.checked(terms.Literal, payload=LiteralPayload(value, sql_type))

    def null(self, sql_type: ScalarType) -> TermRef:
        return self.checked(terms.Null, payload=NullPayload(sql_type))

    def external_parameter(self, parameter: ParameterId) -> TermRef:
        return self.checked(
            terms.ExternalParameter,
            payload=ExternalParameterPayload(parameter),
        )

    def field(self, row: TermRef, index: int) -> TermRef:
        return self.checked(terms.Field, (row,), FieldPayload(index))

    def project_field(self, row: TermRef, index: int) -> TermRef:
        """Project a field, reducing rows constructed in the current scope."""

        if isinstance(row, ScopedTerm):
            if not self._lexical_scope.contains(row.scope):
                raise IRValidationError(
                    "A scoped term escaped its lexical binder path or crossed a sibling"
                )
            row_id = row.term_id
            row_scope = row.scope
        else:
            row_id = self.resolve(row)
            row_scope = LexicalScope()
        node = self.arena[row_id]
        if isinstance(node, terms.Row):
            if index < 0 or index >= len(node.children):
                raise IRValidationError("ROW field index is out of range")
            expected = self.arena.context.schema(node.sort.schema).fields[index]
            child = node.children[index]
            if self.arena[child].sort == expected:
                return self._scope(child, row_scope)
        return self.field(row, index)

    def scalar_call(
        self,
        function: FunctionId,
        arguments: Iterable[TermRef],
        *,
        call_site: CallSiteId | None = None,
    ) -> TermRef:
        return self.checked(
            terms.ScalarCall,
            arguments,
            ScalarCallPayload(function, call_site),
        )

    def apply(
        self,
        operator: str,
        arguments: Iterable[TermRef],
        result: ScalarSort,
        *,
        volatility: Volatility = Volatility.IMMUTABLE,
    ) -> TermRef:
        """Construct a canonical typed scalar operation."""

        arguments = tuple(arguments)
        parameters = tuple(self.arena[self.resolve(arg)].sort for arg in arguments)
        if not all(isinstance(sort, ScalarSort) for sort in parameters):
            raise IRValidationError("Scalar operation arguments must be scalar")
        function = self.arena.context.intern_function(
            ScalarFunctionSpec(parameters, result, volatility, operator)
        )
        return self.scalar_call(function, arguments)

    def case(
        self,
        condition: TermRef,
        then: TermRef,
        otherwise: TermRef,
    ) -> TermRef:
        return self.checked(terms.Case, (condition, then, otherwise))

    def scalarize(self, source: TermRef) -> TermRef:
        return self.checked(terms.Scalarize, (source,))

    def to_predicate(self, value: TermRef) -> TermRef:
        """Use a SQL Boolean value as a condition; NULL becomes UNKNOWN."""
        return self.checked(terms.ToPredicate, (value,))

    def to_boolean(self, predicate: TermRef) -> TermRef:
        """Use a predicate as a SQL value; UNKNOWN becomes NULL."""
        return self.checked(terms.ToBoolean, (predicate,))

    def true3(self) -> TermRef:
        return self.checked(terms.True3)

    def false3(self) -> TermRef:
        return self.checked(terms.False3)

    def unknown3(self) -> TermRef:
        return self.checked(terms.Unknown3)

    def eq3(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.Eq3, (left, right))

    def lt3(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.Lt3, (left, right))

    def like3(self, value: TermRef, pattern: TermRef) -> TermRef:
        return self.checked(terms.Like3, (value, pattern))

    def ilike3(self, value: TermRef, pattern: TermRef) -> TermRef:
        return self.checked(terms.ILike3, (value, pattern))

    def is_null(self, value: TermRef) -> TermRef:
        return self.checked(terms.IsNull, (value,))

    def is_not_null(self, value: TermRef) -> TermRef:
        return self.checked(terms.IsNotNull, (value,))

    def is_not_distinct(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.IsNotDistinct, (left, right))

    def in_subquery(self, needle: TermRef, candidates: TermRef) -> TermRef:
        """Build SQL three-valued membership over a single-column candidate bag.

        The result is TRUE when a non-NULL candidate equals ``needle``; UNKNOWN
        when no match exists but either ``needle`` or a candidate is NULL; and
        FALSE otherwise. SQL ``NOT IN`` is represented by applying ``NOT3``.
        """
        return self.checked(terms.InSubquery, (needle, candidates))

    def and3(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.And3, (left, right))

    def or3(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.Or3, (left, right))

    def not3(self, value: TermRef) -> TermRef:
        return self.checked(terms.Not3, (value,))

    def row(self, schema: SchemaId, values: Iterable[TermRef]) -> TermRef:
        return self.checked(terms.Row, values, SchemaPayload(schema))

    def row_identity_eq(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.RowIdentityEq, (left, right))

    def row_lambda(self, input_schema: SchemaId, body: RowBody) -> TermRef:
        row_sort = RowSort(input_schema)
        outer_scope = self._lexical_scope
        self._lexical_scope = outer_scope.bind_row()
        try:
            variable = self._scope(self.arena.row_var(0, row_sort))
            body_ref = body(variable)
            body_id = self.resolve(body_ref)
        finally:
            self._lexical_scope = outer_scope
        return self.checked(
            terms.RowLambda,
            (body_id,),
            RowLambdaPayload(input_schema),
        )

    def relation_lambda(
        self,
        input_schema: SchemaId,
        body: RowBody,
    ) -> TermRef:
        function = self.row_lambda(input_schema, body)
        function_id = self.resolve(function)
        function_sort = self.arena[function_id].sort
        expect(isinstance(function_sort, RowFunctionSort), "Expected a row function")
        expect(
            isinstance(function_sort.result, BagSort),
            "Relation lambda body must produce BagSort",
        )
        return function

    def dependent_join(
        self,
        outer: TermRef,
        inner: RowBody,
    ) -> TermRef:
        outer_schema = self._bag_schema(outer)
        function = self.relation_lambda(
            outer_schema,
            inner,
        )
        return self.checked(
            terms.DependentJoin,
            (outer, function),
        )

    def dependent_left_join(
        self,
        outer: TermRef,
        inner: RowBody,
    ) -> TermRef:
        outer_schema = self._bag_schema(outer)
        function = self.relation_lambda(outer_schema, inner)
        return self.checked(terms.DependentLeftJoin, (outer, function))

    def semi_join(
        self,
        outer: TermRef,
        inner: RowBody,
    ) -> TermRef:
        outer_schema = self._bag_schema(outer)
        function = self.relation_lambda(
            outer_schema,
            inner,
        )
        return self.checked(
            terms.SemiJoin,
            (outer, function),
        )

    def anti_join(
        self,
        outer: TermRef,
        inner: RowBody,
    ) -> TermRef:
        outer_schema = self._bag_schema(outer)
        function = self.relation_lambda(
            outer_schema,
            inner,
        )
        return self.checked(
            terms.AntiJoin,
            (outer, function),
        )

    # U-expression core.

    def zero(self) -> TermRef:
        return self.checked(terms.Zero)

    def one(self) -> TermRef:
        return self.checked(terms.One)

    def add(self, *summands: TermRef) -> TermRef:
        return self.checked(terms.Add, summands)

    def mul(self, *factors: TermRef) -> TermRef:
        return self.checked(terms.Mul, factors)

    def indicator(self, predicate: TermRef) -> TermRef:
        """Embed SQL truth as a canonical U-semiring multiplicity.

        An indicator is one exactly when its predicate is TRUE; FALSE and
        UNKNOWN both denote zero.  Consequently conjunction is multiplication
        and disjunction is the squash of addition.  The arena normalizes those two
        connectives so every U-expression consumer sees the same
        multiplicative/additive shape.

        Negation deliberately remains an opaque predicate.  In SQL three-valued
        logic ``[NOT p]`` is not ``unot([p])`` when ``p`` is UNKNOWN.
        """

        return self.checked(terms.Indicator, (predicate,))

    def squash(self, multiplicity: TermRef) -> TermRef:
        return self.checked(terms.Squash, (multiplicity,))

    def unot(self, multiplicity: TermRef) -> TermRef:
        return self.checked(terms.UNot, (multiplicity,))

    def sum(self, domain: RowSort, body: RowBody) -> TermRef:
        expect(
            isinstance(domain, RowSort),
            "U-expression SUM currently ranges over RowSort only",
        )
        function = self.row_lambda(domain.schema, body)
        return self.checked(terms.Sum, (function,))

    def at(self, bag: TermRef, row: TermRef) -> TermRef:
        return self.checked(terms.At, (bag, row))

    def bag_lam(self, schema: SchemaId, body: RowBody) -> TermRef:
        function = self.row_lambda(schema, body)
        return self.checked(terms.BagLambda, (function,))

    def base(self, relation: RelationId) -> TermRef:
        return self.checked(terms.Base, payload=BaseRelationPayload(relation))

    # Derived relational operators.

    def empty(self, schema: SchemaId) -> TermRef:
        return self.checked(terms.Empty, payload=SchemaPayload(schema))

    def filter(self, source: TermRef, predicate: RowBody) -> TermRef:
        schema = self._bag_schema(source)
        function = self.row_lambda(schema, predicate)
        return self.checked(terms.Filter, (source, function))

    def map(self, source: TermRef, mapper: RowBody) -> TermRef:
        schema = self._bag_schema(source)
        function = self.row_lambda(schema, mapper)
        return self.checked(terms.Map, (source, function))

    def product(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.Product, (left, right))

    def join(self, left: TermRef, right: TermRef, predicate: RowBody) -> TermRef:
        left_schema = self._bag_schema(left)
        right_schema = self._bag_schema(right)
        combined = self.arena.context.concat_schema(left_schema, right_schema)
        function = self.row_lambda(combined, predicate)
        return self.checked(terms.Join, (left, right, function))

    def left_join(self, left: TermRef, right: TermRef, predicate: RowBody) -> TermRef:
        return self._outer_join(terms.LeftJoin, left, right, predicate)

    def right_join(self, left: TermRef, right: TermRef, predicate: RowBody) -> TermRef:
        return self._outer_join(terms.RightJoin, left, right, predicate)

    def full_join(self, left: TermRef, right: TermRef, predicate: RowBody) -> TermRef:
        return self._outer_join(terms.FullJoin, left, right, predicate)

    def _outer_join(
        self,
        node_type: type[terms.TermNode],
        left: TermRef,
        right: TermRef,
        predicate: RowBody,
    ) -> TermRef:
        expect(
            node_type in {terms.LeftJoin, terms.RightJoin, terms.FullJoin},
            f"Not an outer-join operator: {node_type.key}",
        )
        left_schema = self._bag_schema(left)
        right_schema = self._bag_schema(right)
        combined = self.arena.context.concat_schema(left_schema, right_schema)
        function = self.row_lambda(combined, predicate)
        return self.checked(node_type, (left, right, function))

    def union_all(self, left: TermRef, right: TermRef) -> TermRef:
        return self.checked(terms.UnionAll, (left, right))

    def distinct(self, source: TermRef) -> TermRef:
        return self.checked(terms.Distinct, (source,))

    # Aggregation extension.

    def aggregate_call(
        self,
        aggregate: AggregateSpecId,
        *,
        argument: RowBody | None = None,
        filter: RowBody | None = None,
        distinct: bool = False,
    ) -> AggregateCall:
        return AggregateCall(aggregate, argument, filter, distinct)

    def fold(self, aggregate: AggregateSpecId, source: TermRef) -> TermRef:
        return self.checked(terms.Fold, (source,), AggregatePayload(aggregate))

    def group_fold(
        self,
        source: TermRef,
        keys: RowBody,
        calls: Iterable[AggregateCall],
        output_schema: SchemaId,
    ) -> TermRef:
        input_schema = self._bag_schema(source)
        key_lambda = self.row_lambda(input_schema, keys)
        children: list[TermRef] = [source, key_lambda]
        layouts = self._compile_aggregate_calls(input_schema, calls, children)
        return self.checked(
            terms.GroupFold,
            children,
            FoldPayload(output_schema, layouts),
        )

    def global_fold(
        self,
        source: TermRef,
        calls: Iterable[AggregateCall],
        output_schema: SchemaId,
    ) -> TermRef:
        input_schema = self._bag_schema(source)
        children: list[TermRef] = [source]
        layouts = self._compile_aggregate_calls(input_schema, calls, children)
        return self.checked(
            terms.GlobalFold,
            children,
            FoldPayload(output_schema, layouts),
        )

    def _compile_aggregate_calls(
        self,
        input_schema: SchemaId,
        calls: Iterable[AggregateCall],
        children: list[TermRef],
    ) -> tuple[AggregateCallLayout, ...]:
        layouts: list[AggregateCallLayout] = []
        for call in calls:
            expect(
                isinstance(call, AggregateCall),
                "Fold calls must be AggregateCall values",
            )
            argument_child = None
            filter_child = None
            if call.argument is not None:
                argument_child = len(children)
                children.append(self.row_lambda(input_schema, call.argument))
            if call.filter is not None:
                filter_child = len(children)
                children.append(self.row_lambda(input_schema, call.filter))
            layouts.append(
                AggregateCallLayout(
                    call.aggregate,
                    AggregateMode.DISTINCT if call.distinct else AggregateMode.ALL,
                    argument_child,
                    filter_child,
                )
            )
        return tuple(layouts)

    # Analytic windows.

    def window(
        self,
        source: TermRef,
        calls: Iterable[WindowCall],
        output_schema: SchemaId,
    ) -> TermRef:
        input_schema = self._bag_schema(source)
        children: list[TermRef] = [source]
        layouts: list[WindowCallLayout] = []
        for call in calls:
            expect(isinstance(call, WindowCall), "Window calls must be WindowCall values")
            argument_children = tuple(
                self._append_row_lambda(children, input_schema, argument)
                for argument in call.arguments
            )
            partition_children = tuple(
                self._append_row_lambda(children, input_schema, partition)
                for partition in call.partition_by
            )
            order_children: list[int] = []
            order_specs: list[OrderKeySpec] = []
            for key in call.order_by:
                expect(isinstance(key, OrderKey), "Window order keys must be OrderKey values")
                order_children.append(
                    self._append_row_lambda(children, input_schema, key.expression)
                )
                order_specs.append(OrderKeySpec(key.direction, key.nulls, key.collation))
            layouts.append(
                WindowCallLayout(
                    call.kind,
                    call.result,
                    argument_children,
                    partition_children,
                    tuple(order_children),
                    tuple(order_specs),
                    call.frame,
                    call.aggregate,
                    (
                        self._append_row_lambda(children, input_schema, call.filter)
                        if call.filter is not None
                        else None
                    ),
                    call.distinct,
                )
            )
        return self.checked(
            terms.Window,
            children,
            WindowPayload(output_schema, tuple(layouts)),
        )

    def _append_row_lambda(
        self,
        children: list[TermRef],
        input_schema: SchemaId,
        body: RowBody,
    ) -> int:
        index = len(children)
        children.append(self.row_lambda(input_schema, body))
        return index

    # Deterministic ordering extension.

    def order_key(
        self,
        expression: RowBody,
        *,
        direction: Direction = Direction.ASC,
        nulls: NullPlacement = NullPlacement.LAST,
        collation: CollationId | None = None,
    ) -> OrderKey:
        return OrderKey(expression, direction, nulls, collation)

    def order_by(
        self,
        source: TermRef,
        keys: Iterable[OrderKey],
    ) -> TermRef:
        input_schema = self._bag_schema(source)
        children: list[TermRef] = [source]
        specs = self._compile_order_keys(input_schema, keys, children)
        return self.checked(terms.OrderBy, children, OrderPayload(specs))

    def sequence_map(self, source: TermRef, mapper: RowBody) -> TermRef:
        schema = self.relation_schema(source)
        function = self.row_lambda(schema, mapper)
        return self.checked(terms.SeqMap, (source, function))

    def take(self, count: TermRef, sequence: TermRef) -> TermRef:
        return self.checked(terms.Take, (count, sequence))

    def drop(self, count: TermRef, sequence: TermRef) -> TermRef:
        return self.checked(terms.Drop, (count, sequence))

    def slice(
        self,
        offset: TermRef,
        count: TermRef,
        sequence: TermRef,
    ) -> TermRef:
        return self.checked(terms.Slice, (offset, count, sequence))

    def forget_order(self, sequence: TermRef) -> TermRef:
        return self.checked(terms.ForgetOrder, (sequence,))

    def topk(
        self,
        source: TermRef,
        keys: Iterable[OrderKey],
        offset: TermRef,
        count: TermRef,
    ) -> TermRef:
        input_schema = self._bag_schema(source)
        children: list[TermRef] = [source, offset, count]
        specs = self._compile_order_keys(input_schema, keys, children)
        return self.checked(terms.TopK, children, OrderPayload(specs))

    def _compile_order_keys(
        self,
        input_schema: SchemaId,
        keys: Iterable[OrderKey],
        children: list[TermRef],
    ) -> tuple[OrderKeySpec, ...]:
        specs: list[OrderKeySpec] = []
        for key in keys:
            expect(isinstance(key, OrderKey), "Ordering keys must be OrderKey values")
            children.append(self.row_lambda(input_schema, key.expression))
            specs.append(OrderKeySpec(key.direction, key.nulls, key.collation))
        return tuple(specs)

    # Relational let binding.

    def let_rel(
        self, definition: TermRef, body: Callable[[TermRef], TermRef]
    ) -> TermRef:
        definition_id = self.resolve(definition)
        definition_sort = self.arena[definition_id].sort
        expect(
            is_relation_sort(definition_sort),
            "LET_REL definition must be relation-valued",
        )
        outer_scope = self._lexical_scope
        self._lexical_scope = outer_scope.bind_relation()
        try:
            variable = self._scope(self.arena.rel_var(0, definition_sort))
            body_ref = body(variable)
            body_id = self.resolve(body_ref)
        finally:
            self._lexical_scope = outer_scope
        return self.checked(terms.LetRel, (definition_id, body_id))

    def _bag_schema(self, term: TermRef) -> SchemaId:
        sort = self.arena[self.resolve(term)].sort
        expect(isinstance(sort, BagSort), f"Expected BagSort, got {sort!r}")
        return sort.schema

    def relation_schema(self, term: TermRef) -> SchemaId:
        return relation_schema(self.arena[self.resolve(term)].sort)
