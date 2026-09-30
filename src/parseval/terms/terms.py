from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar, TypeAlias

from .names import (
    AggregateSpecId,
    CallSiteId,
    CollationId,
    FunctionId,
    ParameterId,
    RelationId,
    SchemaId,
)
from .sorts import ScalarSort, Sort
from .types import ScalarType


@dataclass(frozen=True, slots=True, order=True)
class TermId:
    """Term handle owned by exactly one arena."""

    arena: int
    index: int

    def __post_init__(self) -> None:
        for value, name in ((self.arena, "arena"), (self.index, "index")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"TermId.{name} must be an integer")
            if value < 0:
                raise ValueError(f"TermId.{name} must be nonnegative")


class Direction(str, Enum):
    ASC = "asc"
    DESC = "desc"


class NullPlacement(str, Enum):
    FIRST = "first"
    LAST = "last"


class AggregateMode(str, Enum):
    ALL = "all"
    DISTINCT = "distinct"


class WindowFunctionKind(str, Enum):
    AGGREGATE = "aggregate"
    ROW_NUMBER = "row_number"
    RANK = "rank"
    DENSE_RANK = "dense_rank"
    LAG = "lag"
    LEAD = "lead"


class WindowFrameMode(str, Enum):
    ROWS = "rows"
    RANGE = "range"
    GROUPS = "groups"


class WindowBoundaryKind(str, Enum):
    UNBOUNDED_PRECEDING = "unbounded_preceding"
    OFFSET_PRECEDING = "offset_preceding"
    CURRENT_ROW = "current_row"
    OFFSET_FOLLOWING = "offset_following"
    UNBOUNDED_FOLLOWING = "unbounded_following"


class WindowFrameExclusion(str, Enum):
    NO_OTHERS = "no_others"
    CURRENT_ROW = "current_row"
    GROUP = "group"
    TIES = "ties"


@dataclass(frozen=True, slots=True)
class LiteralPayload:
    value: object
    sql_type: ScalarType


@dataclass(frozen=True, slots=True)
class NullPayload:
    sql_type: ScalarType


@dataclass(frozen=True, slots=True)
class VariablePayload:
    depth: int

    def __post_init__(self) -> None:
        if isinstance(self.depth, bool) or not isinstance(self.depth, int):
            raise TypeError("Variable depth must be an integer")
        if self.depth < 0:
            raise ValueError("Variable depth must be nonnegative")


@dataclass(frozen=True, slots=True)
class FieldPayload:
    index: int

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise TypeError("Field index must be an integer")
        if self.index < 0:
            raise ValueError("Field index must be nonnegative")


@dataclass(frozen=True, slots=True)
class ExternalParameterPayload:
    parameter: ParameterId


@dataclass(frozen=True, slots=True)
class ScalarCallPayload:
    function: FunctionId
    call_site: CallSiteId | None = None


@dataclass(frozen=True, slots=True)
class SchemaPayload:
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class RowLambdaPayload:
    input_schema: SchemaId


@dataclass(frozen=True, slots=True)
class BaseRelationPayload:
    relation: RelationId


@dataclass(frozen=True, slots=True)
class AggregatePayload:
    aggregate: AggregateSpecId


@dataclass(frozen=True, slots=True)
class AggregateCallLayout:
    aggregate: AggregateSpecId
    mode: AggregateMode = AggregateMode.ALL
    argument_child: int | None = None
    filter_child: int | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.argument_child, "argument_child"),
            (self.filter_child, "filter_child"),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{label} must be a nonnegative child index")
        if self.mode is AggregateMode.DISTINCT and self.argument_child is None:
            raise ValueError("DISTINCT aggregate calls require an argument")


@dataclass(frozen=True, slots=True)
class FoldPayload:
    output_schema: SchemaId
    calls: tuple[AggregateCallLayout, ...]

    def __post_init__(self) -> None:
        if not self.calls:
            raise ValueError("A fold requires at least one aggregate call")


@dataclass(frozen=True, slots=True)
class OrderKeySpec:
    direction: Direction
    nulls: NullPlacement
    collation: CollationId | None = None


@dataclass(frozen=True, slots=True)
class OrderPayload:
    """SQL ordering keys.

    An empty tuple denotes unconstrained input order, as used by LIMIT/OFFSET
    without ORDER BY. Nonempty keys may contain ties; totality is not an IR
    construction requirement.
    """

    keys: tuple[OrderKeySpec, ...]


@dataclass(frozen=True, slots=True)
class WindowBoundary:
    kind: WindowBoundaryKind
    offset: int | None = None

    def __post_init__(self) -> None:
        requires_offset = self.kind in {
            WindowBoundaryKind.OFFSET_PRECEDING,
            WindowBoundaryKind.OFFSET_FOLLOWING,
        }
        if requires_offset != (self.offset is not None):
            raise ValueError("Window boundary offset does not match its kind")
        if self.offset is not None and self.offset < 0:
            raise ValueError("Window boundary offset must be nonnegative")


@dataclass(frozen=True, slots=True)
class WindowFrame:
    mode: WindowFrameMode
    start: WindowBoundary
    end: WindowBoundary
    exclusion: WindowFrameExclusion = WindowFrameExclusion.NO_OTHERS


@dataclass(frozen=True, slots=True)
class WindowCallLayout:
    kind: WindowFunctionKind
    result: ScalarSort
    argument_children: tuple[int, ...]
    partition_children: tuple[int, ...]
    order_children: tuple[int, ...]
    order: tuple[OrderKeySpec, ...]
    frame: WindowFrame
    aggregate: AggregateSpecId | None = None
    filter_child: int | None = None
    distinct: bool = False

    def __post_init__(self) -> None:
        if len(self.order_children) != len(self.order):
            raise ValueError("Window order children do not match their specifications")
        if self.kind is WindowFunctionKind.AGGREGATE:
            if self.aggregate is None:
                raise ValueError("Aggregate windows require an aggregate declaration")
        elif self.aggregate is not None:
            raise ValueError("Only aggregate windows may carry an aggregate declaration")
        if self.filter_child is not None and self.kind is not WindowFunctionKind.AGGREGATE:
            raise ValueError("Only aggregate windows may carry a filter")
        if self.distinct and self.kind is not WindowFunctionKind.AGGREGATE:
            raise ValueError("Only aggregate windows may be distinct")


@dataclass(frozen=True, slots=True)
class WindowPayload:
    output_schema: SchemaId
    calls: tuple[WindowCallLayout, ...]

    def __post_init__(self) -> None:
        if not self.calls:
            raise ValueError("A window relation requires at least one call")


TermPayload: TypeAlias = (
    None
    | LiteralPayload
    | NullPayload
    | VariablePayload
    | FieldPayload
    | ExternalParameterPayload
    | ScalarCallPayload
    | SchemaPayload
    | RowLambdaPayload
    | BaseRelationPayload
    | AggregatePayload
    | FoldPayload
    | OrderPayload
    | WindowPayload
)


@dataclass(frozen=True, slots=True)
class TermNode:
    """Immutable structural node whose concrete class identifies its operation.

    Checked nodes are created by TermArena. Children remain arena-owned handles
    so shared subexpressions form a DAG. Keys are used only for codecs and display.
    """

    key: ClassVar[str]
    payload_type: ClassVar[type[object] | None] = None

    sort: Sort
    children: tuple[TermId, ...] = ()
    payload: TermPayload = None

    def __post_init__(self) -> None:
        if not hasattr(type(self), "key"):
            raise TypeError(
                f"{type(self).__name__} must be instantiated through a concrete subclass"
            )


class SQLExpr(TermNode):
    """SQL values, predicates, and row functions."""

    __slots__ = ()


class Value(SQLExpr):
    """SQL scalar and row value operations."""

    __slots__ = ()


class Predicate(SQLExpr):
    """SQL predicate operations, including three-valued logic."""

    __slots__ = ()


class UCore(TermNode):
    """Core U-expression algebra and bag operations."""

    __slots__ = ()


class UAgg(TermNode):
    """U-expression aggregation operations."""

    __slots__ = ()


class UOrder(TermNode):
    """U-expression ordering and sequence operations."""

    __slots__ = ()


class UWindow(TermNode):
    """Partitioned analytic operations over a relation."""

    __slots__ = ()


class UBind(TermNode):
    """U-expression relation binding and references."""

    __slots__ = ()


class CompactTerm(TermNode):
    """Derived relational operations outside the core U-expression language."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Literal(Value):
    key: ClassVar[str] = "value.literal"
    payload_type: ClassVar[type[object] | None] = LiteralPayload
    payload: LiteralPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Null(Value):
    key: ClassVar[str] = "value.null"
    payload_type: ClassVar[type[object] | None] = NullPayload
    payload: NullPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class RowVar(Value):
    key: ClassVar[str] = "value.row_var"
    payload_type: ClassVar[type[object] | None] = VariablePayload
    payload: VariablePayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Field(Value):
    key: ClassVar[str] = "value.field"
    payload_type: ClassVar[type[object] | None] = FieldPayload
    payload: FieldPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class ExternalParameter(Value):
    key: ClassVar[str] = "value.external_param"
    payload_type: ClassVar[type[object] | None] = ExternalParameterPayload
    payload: ExternalParameterPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class ScalarCall(Value):
    key: ClassVar[str] = "value.scalar_call"
    payload_type: ClassVar[type[object] | None] = ScalarCallPayload
    payload: ScalarCallPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Case(Value):
    key: ClassVar[str] = "value.case"


@dataclass(frozen=True, slots=True)
class Row(Value):
    key: ClassVar[str] = "value.row"
    payload_type: ClassVar[type[object] | None] = SchemaPayload
    payload: SchemaPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Scalarize(Value):
    key: ClassVar[str] = "subquery.scalarize"


@dataclass(frozen=True, slots=True)
class ToPredicate(Predicate):
    """Interpret a nullable SQL Boolean as TRUE, FALSE, or UNKNOWN."""

    key: ClassVar[str] = "predicate.from_boolean"


@dataclass(frozen=True, slots=True)
class ToBoolean(Value):
    """Expose a predicate as a SQL Boolean, mapping UNKNOWN to NULL."""

    key: ClassVar[str] = "value.from_predicate"


@dataclass(frozen=True, slots=True)
class True3(Predicate):
    key: ClassVar[str] = "predicate.true3"


@dataclass(frozen=True, slots=True)
class False3(Predicate):
    key: ClassVar[str] = "predicate.false3"


@dataclass(frozen=True, slots=True)
class Unknown3(Predicate):
    key: ClassVar[str] = "predicate.unknown3"


@dataclass(frozen=True, slots=True)
class Eq3(Predicate):
    key: ClassVar[str] = "predicate.eq3"


@dataclass(frozen=True, slots=True)
class Lt3(Predicate):
    key: ClassVar[str] = "predicate.lt3"


@dataclass(frozen=True, slots=True)
class Like3(Predicate):
    key: ClassVar[str] = "predicate.like3"


@dataclass(frozen=True, slots=True)
class ILike3(Predicate):
    """Case-insensitive SQL pattern matching in three-valued logic."""

    key: ClassVar[str] = "predicate.ilike3"


@dataclass(frozen=True, slots=True)
class IsNull(Predicate):
    key: ClassVar[str] = "predicate.is_null"


@dataclass(frozen=True, slots=True)
class IsNotNull(Predicate):
    key: ClassVar[str] = "predicate.is_not_null"


@dataclass(frozen=True, slots=True)
class IsNotDistinct(Predicate):
    key: ClassVar[str] = "predicate.is_not_distinct"


@dataclass(frozen=True, slots=True)
class InSubquery(Predicate):
    key: ClassVar[str] = "predicate.in_subquery"


@dataclass(frozen=True, slots=True)
class And3(Predicate):
    key: ClassVar[str] = "predicate.and3"


@dataclass(frozen=True, slots=True)
class Or3(Predicate):
    key: ClassVar[str] = "predicate.or3"


@dataclass(frozen=True, slots=True)
class Not3(Predicate):
    key: ClassVar[str] = "predicate.not3"


@dataclass(frozen=True, slots=True)
class RowIdentityEq(Predicate):
    key: ClassVar[str] = "predicate.row_identity_eq"


@dataclass(frozen=True, slots=True)
class RowLambda(SQLExpr):
    key: ClassVar[str] = "bind.row_lambda"
    payload_type: ClassVar[type[object] | None] = RowLambdaPayload
    payload: RowLambdaPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Zero(UCore):
    key: ClassVar[str] = "u.zero"


@dataclass(frozen=True, slots=True)
class One(UCore):
    key: ClassVar[str] = "u.one"


@dataclass(frozen=True, slots=True)
class Add(UCore):
    key: ClassVar[str] = "u.add"


@dataclass(frozen=True, slots=True)
class Mul(UCore):
    key: ClassVar[str] = "u.mul"


@dataclass(frozen=True, slots=True)
class Sum(UCore):
    key: ClassVar[str] = "u.sum"


@dataclass(frozen=True, slots=True)
class Squash(UCore):
    key: ClassVar[str] = "u.squash"


@dataclass(frozen=True, slots=True)
class UNot(UCore):
    key: ClassVar[str] = "u.not"


@dataclass(frozen=True, slots=True)
class Indicator(UCore):
    key: ClassVar[str] = "u.indicator"


@dataclass(frozen=True, slots=True)
class Base(UCore):
    key: ClassVar[str] = "bag.base"
    payload_type: ClassVar[type[object] | None] = BaseRelationPayload
    payload: BaseRelationPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class At(UCore):
    key: ClassVar[str] = "bag.at"


@dataclass(frozen=True, slots=True)
class BagLambda(UCore):
    key: ClassVar[str] = "bag.lambda"


@dataclass(frozen=True, slots=True)
class Fold(UAgg):
    key: ClassVar[str] = "agg.fold"
    payload_type: ClassVar[type[object] | None] = AggregatePayload
    payload: AggregatePayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class GroupFold(UAgg):
    key: ClassVar[str] = "agg.group_fold"
    payload_type: ClassVar[type[object] | None] = FoldPayload
    payload: FoldPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class GlobalFold(UAgg):
    key: ClassVar[str] = "agg.global_fold"
    payload_type: ClassVar[type[object] | None] = FoldPayload
    payload: FoldPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class OrderBy(UOrder):
    key: ClassVar[str] = "order.by"
    payload_type: ClassVar[type[object] | None] = OrderPayload
    payload: OrderPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class SeqMap(UOrder):
    key: ClassVar[str] = "order.map"


@dataclass(frozen=True, slots=True)
class Take(UOrder):
    key: ClassVar[str] = "order.take"


@dataclass(frozen=True, slots=True)
class Drop(UOrder):
    key: ClassVar[str] = "order.drop"


@dataclass(frozen=True, slots=True)
class Slice(UOrder):
    key: ClassVar[str] = "order.slice"


@dataclass(frozen=True, slots=True)
class ForgetOrder(UOrder):
    key: ClassVar[str] = "order.forget"


@dataclass(frozen=True, slots=True)
class Window(UWindow):
    key: ClassVar[str] = "window.apply"
    payload_type: ClassVar[type[object] | None] = WindowPayload
    payload: WindowPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class LetRel(UBind):
    key: ClassVar[str] = "bind.let_rel"


@dataclass(frozen=True, slots=True)
class RelVar(UBind):
    key: ClassVar[str] = "bind.rel_var"
    payload_type: ClassVar[type[object] | None] = VariablePayload
    payload: VariablePayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Empty(CompactTerm):
    key: ClassVar[str] = "derived.empty"
    payload_type: ClassVar[type[object] | None] = SchemaPayload
    payload: SchemaPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Filter(CompactTerm):
    key: ClassVar[str] = "derived.filter"


@dataclass(frozen=True, slots=True)
class Map(CompactTerm):
    key: ClassVar[str] = "derived.map"


@dataclass(frozen=True, slots=True)
class Product(CompactTerm):
    key: ClassVar[str] = "derived.product"


@dataclass(frozen=True, slots=True)
class Join(CompactTerm):
    key: ClassVar[str] = "derived.join"


@dataclass(frozen=True, slots=True)
class LeftJoin(CompactTerm):
    key: ClassVar[str] = "derived.left_join"


@dataclass(frozen=True, slots=True)
class RightJoin(CompactTerm):
    key: ClassVar[str] = "derived.right_join"


@dataclass(frozen=True, slots=True)
class FullJoin(CompactTerm):
    key: ClassVar[str] = "derived.full_join"


@dataclass(frozen=True, slots=True)
class UnionAll(CompactTerm):
    key: ClassVar[str] = "derived.union_all"


@dataclass(frozen=True, slots=True)
class Distinct(CompactTerm):
    key: ClassVar[str] = "derived.distinct"


@dataclass(frozen=True, slots=True)
class TopK(CompactTerm):
    key: ClassVar[str] = "derived.topk"
    payload_type: ClassVar[type[object] | None] = OrderPayload
    payload: OrderPayload = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class DependentJoin(CompactTerm):
    key: ClassVar[str] = "derived.dependent_join"


@dataclass(frozen=True, slots=True)
class DependentLeftJoin(CompactTerm):
    key: ClassVar[str] = "derived.dependent_left_join"


@dataclass(frozen=True, slots=True)
class SemiJoin(CompactTerm):
    key: ClassVar[str] = "derived.semi_join"


@dataclass(frozen=True, slots=True)
class AntiJoin(CompactTerm):
    key: ClassVar[str] = "derived.anti_join"


NODE_TYPES: tuple[type[TermNode], ...] = (
    Literal,
    Null,
    RowVar,
    Field,
    ExternalParameter,
    ScalarCall,
    Case,
    Row,
    Scalarize,
    ToPredicate,
    ToBoolean,
    True3,
    False3,
    Unknown3,
    Eq3,
    Lt3,
    Like3,
    ILike3,
    IsNull,
    IsNotNull,
    IsNotDistinct,
    InSubquery,
    And3,
    Or3,
    Not3,
    RowIdentityEq,
    RowLambda,
    Zero,
    One,
    Add,
    Mul,
    Sum,
    Squash,
    UNot,
    Indicator,
    Base,
    At,
    BagLambda,
    Fold,
    GroupFold,
    GlobalFold,
    OrderBy,
    SeqMap,
    Take,
    Drop,
    Slice,
    ForgetOrder,
    Window,
    LetRel,
    RelVar,
    Empty,
    Filter,
    Map,
    Product,
    Join,
    LeftJoin,
    RightJoin,
    FullJoin,
    UnionAll,
    Distinct,
    TopK,
    DependentJoin,
    DependentLeftJoin,
    SemiJoin,
    AntiJoin,
)

NODE_TYPES_BY_KEY = {node_type.key: node_type for node_type in NODE_TYPES}


SQL_EXPR_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, SQLExpr)
)

UCORE_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, UCore)
)

UAGG_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, UAgg)
)

UORDER_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, UOrder)
)

UWINDOW_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, UWindow)
)

UBIND_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, UBind)
)

COMPACT_ONLY_NODES: frozenset[type[TermNode]] = frozenset(
    node_type for node_type in NODE_TYPES if issubclass(node_type, CompactTerm)
)

UEXPR_NODES: frozenset[type[TermNode]] = (
    SQL_EXPR_NODES
    | UCORE_NODES
    | UAGG_NODES
    | UORDER_NODES
    | UWINDOW_NODES
    | UBIND_NODES
)


OCCURRENCE_NODES: frozenset[type[TermNode]] = frozenset(
    (
        Empty,
        Base,
        Filter,
        Map,
        Product,
        Join,
        LeftJoin,
        RightJoin,
        FullJoin,
        UnionAll,
        Distinct,
        DependentJoin,
        DependentLeftJoin,
        SemiJoin,
        AntiJoin,
    )
)
