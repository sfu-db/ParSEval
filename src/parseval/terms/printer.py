"""Human-readable formatting for Parseval semantic IR terms.

The pretty printer renders a term graph as one U-expression-oriented expression.
It is intentionally separate from serialization: printed text is for people and
is not a stable interchange format.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from . import terms as nodes
from .arena import TermArena
from .sorts import (
    MULTIPLICITY,
    PREDICATE,
    AggStateSort,
    BagSort,
    RowFunctionSort,
    RowSort,
    ScalarSort,
    SeqSort,
    Sort,
)
from .terms import (
    AggregateMode,
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
    VariablePayload,
)
from .walk import post_order


@dataclass(frozen=True, slots=True)
class PrinterOptions:
    """Formatting choices for :func:`format_term`."""

    show_sorts: bool = False
    multiline: bool = False
    indent: str = "  "


NameResolver = Callable[[str, int], str]


def default_name_resolver(kind: str, value: int) -> str:
    prefixes = {
        "relation": "R",
        "function": "f",
        "aggregate": "agg",
        "parameter": "p",
        "collation": "coll",
        "schema": "S",
        "call_site": "site",
    }
    return f"{prefixes.get(kind, kind)}{value}"


class UExprPrinter:
    """Pretty-print checked terms with binder-aware variable names."""

    __slots__ = ("arena", "options", "name_resolver")

    def __init__(
        self,
        arena: TermArena,
        *,
        options: PrinterOptions | None = None,
        name_resolver: NameResolver = default_name_resolver,
    ) -> None:
        self.arena = arena
        self.options = options or PrinterOptions()
        self.name_resolver = name_resolver

    def format(self, root: TermId) -> str:
        self.arena[root]
        text = self._format(root, (), (), 0)
        if self.options.show_sorts:
            return f"{text} : {format_sort(self.arena[root].sort, self.name_resolver)}"
        return text

    def _format(
        self,
        term_id: TermId,
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        node = self.arena[term_id]
        node_type = type(node)
        children = node.children
        payload = node.payload

        if node_type is nodes.Literal:
            assert isinstance(payload, LiteralPayload)
            return _format_literal(payload.value)
        if node_type is nodes.Null:
            assert isinstance(payload, NullPayload)
            return f"NULL::{_format_sql_type(payload.sql_type)}"
        if node_type is nodes.RowVar:
            assert isinstance(payload, VariablePayload)
            return _bound_name(row_names, payload.depth, "free_row")
        if node_type is nodes.RelVar:
            assert isinstance(payload, VariablePayload)
            return _bound_name(relation_names, payload.depth, "free_rel")
        if node_type is nodes.Field:
            assert isinstance(payload, FieldPayload)
            formatted_row = self._format(children[0], row_names, relation_names, level)
            return f"{formatted_row}.{payload.index}"
        if node_type is nodes.ExternalParameter:
            assert isinstance(payload, ExternalParameterPayload)
            return self.name_resolver("parameter", payload.parameter.value)
        if node_type is nodes.ScalarCall:
            assert isinstance(payload, ScalarCallPayload)
            name = self.name_resolver("function", payload.function.value)
            args = self._join(
                [
                    self._format(child, row_names, relation_names, level + 1)
                    for child in children
                ],
                level,
            )
            suffix = ""
            if payload.call_site is not None:
                suffix = f"@{self.name_resolver('call_site', payload.call_site.value)}"
            return f"{name}{suffix}({args})"
        if node_type is nodes.Case:
            condition, then, otherwise = children
            return (
                "case "
                f"{self._format(condition, row_names, relation_names, level + 1)} "
                "then "
                f"{self._format(then, row_names, relation_names, level + 1)} "
                "else "
                f"{self._format(otherwise, row_names, relation_names, level + 1)}"
            )
        if node_type is nodes.Scalarize:
            return (
                "scalarize("
                + self._format(children[0], row_names, relation_names, level + 1)
                + ")"
            )
        if node_type is nodes.ToPredicate:
            return self._call("to_predicate", children, row_names, relation_names, level)
        if node_type is nodes.ToBoolean:
            return self._call("to_boolean", children, row_names, relation_names, level)
        if node_type is nodes.Row:
            values = self._join(
                [
                    self._format(child, row_names, relation_names, level + 1)
                    for child in children
                ],
                level,
            )
            return f"row({values})"

        if node_type is nodes.True3:
            return "TRUE3"
        if node_type is nodes.False3:
            return "FALSE3"
        if node_type is nodes.Unknown3:
            return "UNKNOWN3"
        if node_type is nodes.Eq3:
            return self._binary("=3", children, row_names, relation_names, level)
        if node_type is nodes.Lt3:
            return self._binary("<3", children, row_names, relation_names, level)
        if node_type is nodes.Like3:
            return self._binary("like3", children, row_names, relation_names, level)
        if node_type is nodes.ILike3:
            return self._binary("ilike3", children, row_names, relation_names, level)
        if node_type is nodes.IsNull:
            value = self._format(children[0], row_names, relation_names, level + 1)
            return f"is_null({value})"
        if node_type is nodes.IsNotNull:
            value = self._format(children[0], row_names, relation_names, level + 1)
            return f"is_not_null({value})"
        if node_type is nodes.IsNotDistinct:
            return self._binary(
                "is_not_distinct", children, row_names, relation_names, level
            )
        if node_type is nodes.InSubquery:
            return self._binary(
                "in_subquery", children, row_names, relation_names, level
            )
        if node_type is nodes.And3:
            return self._binary("and3", children, row_names, relation_names, level)
        if node_type is nodes.Or3:
            return self._binary("or3", children, row_names, relation_names, level)
        if node_type is nodes.Not3:
            value = self._format(children[0], row_names, relation_names, level + 1)
            return f"not3({value})"
        if node_type is nodes.RowIdentityEq:
            return self._binary("===", children, row_names, relation_names, level)

        if node_type is nodes.RowLambda:
            return self._format_lambda(term_id, row_names, relation_names, level)

        if node_type is nodes.Zero:
            return "0"
        if node_type is nodes.One:
            return "1"
        if node_type is nodes.Add:
            return self._variadic("+", children, row_names, relation_names, level)
        if node_type is nodes.Mul:
            return self._variadic("*", children, row_names, relation_names, level)
        if node_type is nodes.Squash:
            value = self._format(children[0], row_names, relation_names, level + 1)
            return f"squash({value})"
        if node_type is nodes.UNot:
            value = self._format(children[0], row_names, relation_names, level + 1)
            return f"unot({value})"
        if node_type is nodes.Indicator:
            return (
                f"[{self._format(children[0], row_names, relation_names, level + 1)}]"
            )
        if node_type is nodes.Sum:
            return self._format_binder_call(
                "sum", children[0], row_names, relation_names, level
            )
        if node_type is nodes.BagLambda:
            return self._format_binder_call(
                "bag", children[0], row_names, relation_names, level
            )
        if node_type is nodes.At:
            bag, row = children
            return (
                f"{self._format(bag, row_names, relation_names, level + 1)}"
                f"({self._format(row, row_names, relation_names, level + 1)})"
            )
        if node_type is nodes.Base:
            assert isinstance(payload, BaseRelationPayload)
            return self.name_resolver("relation", payload.relation.value)

        if node_type is nodes.Empty:
            assert isinstance(payload, SchemaPayload)
            return f"empty<{self.name_resolver('schema', payload.schema.value)}>"
        if node_type is nodes.Filter:
            return self._call("filter", children, row_names, relation_names, level)
        if node_type is nodes.Map:
            return self._call("map", children, row_names, relation_names, level)
        if node_type is nodes.SeqMap:
            return self._call(
                "sequence_map", children, row_names, relation_names, level
            )
        if node_type is nodes.Product:
            return self._call("product", children, row_names, relation_names, level)
        if node_type is nodes.Join:
            return self._call("join", children, row_names, relation_names, level)
        if node_type is nodes.UnionAll:
            return self._call("union_all", children, row_names, relation_names, level)
        if node_type is nodes.Distinct:
            return self._call("distinct", children, row_names, relation_names, level)

        if node_type is nodes.Fold:
            assert isinstance(payload, AggregatePayload)
            aggregate = self.name_resolver("aggregate", payload.aggregate.value)
            source = self._format(children[0], row_names, relation_names, level + 1)
            return f"fold<{aggregate}>({source})"
        if node_type in (nodes.GroupFold, nodes.GlobalFold):
            assert isinstance(payload, FoldPayload)
            return self._format_fold(
                node_type, children, payload, row_names, relation_names, level
            )

        if node_type is nodes.OrderBy:
            assert isinstance(payload, OrderPayload)
            return self._format_order(
                "order_by", children, payload, row_names, relation_names, level
            )
        if node_type is nodes.TopK:
            assert isinstance(payload, OrderPayload)
            return self._format_order(
                "topk", children, payload, row_names, relation_names, level
            )
        if node_type is nodes.Take:
            return self._call("take", children, row_names, relation_names, level)
        if node_type is nodes.Drop:
            return self._call("drop", children, row_names, relation_names, level)
        if node_type is nodes.Slice:
            return self._call("slice", children, row_names, relation_names, level)
        if node_type is nodes.ForgetOrder:
            return self._call(
                "forget_order", children, row_names, relation_names, level
            )

        if node_type is nodes.LetRel:
            definition, body = children
            name = f"r{len(relation_names)}"
            definition_text = self._format(
                definition, row_names, relation_names, level + 1
            )
            body_text = self._format(
                body, row_names, (name, *relation_names), level + 1
            )
            return f"let {name} = {definition_text} in {body_text}"

        return self._call(node_type.key, children, row_names, relation_names, level)

    def _format_lambda(
        self,
        lambda_id: TermId,
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        node = self.arena[lambda_id]
        if not isinstance(node, nodes.RowLambda) or not isinstance(
            node.payload, RowLambdaPayload
        ):
            return self._format(lambda_id, row_names, relation_names, level)
        name = f"t{len(row_names)}"
        schema = self.name_resolver("schema", node.payload.input_schema.value)
        body = self._format(
            node.children[0], (name, *row_names), relation_names, level + 1
        )
        return f"lambda {name}:{schema} => {body}"

    def _format_binder_call(
        self,
        keyword: str,
        lambda_id: TermId,
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        node = self.arena[lambda_id]
        if not isinstance(node, nodes.RowLambda) or not isinstance(
            node.payload, RowLambdaPayload
        ):
            inner = self._format(lambda_id, row_names, relation_names, level + 1)
            return f"{keyword}({inner})"
        name = f"t{len(row_names)}"
        schema = self.name_resolver("schema", node.payload.input_schema.value)
        body = self._format(
            node.children[0], (name, *row_names), relation_names, level + 1
        )
        return f"{keyword} {name}:{schema} => {body}"

    def _format_fold(
        self,
        node_type: type[nodes.TermNode],
        children: tuple[TermId, ...],
        payload: FoldPayload,
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        source = self._format(children[0], row_names, relation_names, level + 1)
        parts = [f"source={source}"]
        first_layout_child = 1
        if node_type is nodes.GroupFold:
            parts.append(
                "keys="
                + self._format(children[1], row_names, relation_names, level + 1)
            )
            first_layout_child = 2
        calls: list[str] = []
        for call in payload.calls:
            aggregate = self.name_resolver("aggregate", call.aggregate.value)
            mode = "distinct " if call.mode is AggregateMode.DISTINCT else ""
            call_parts: list[str] = []
            if call.argument_child is not None:
                call_parts.append(
                    "arg="
                    + self._format(
                        children[call.argument_child],
                        row_names,
                        relation_names,
                        level + 1,
                    )
                )
            if call.filter_child is not None:
                call_parts.append(
                    "filter="
                    + self._format(
                        children[call.filter_child],
                        row_names,
                        relation_names,
                        level + 1,
                    )
                )
            arguments = ", ".join(call_parts)
            calls.append(f"{mode}{aggregate}({arguments})")
        if not payload.calls and len(children) > first_layout_child:
            calls.append("<malformed>")
        parts.append("calls=[" + ", ".join(calls) + "]")
        parts.append(
            f"output={self.name_resolver('schema', payload.output_schema.value)}"
        )
        name = "group_fold" if node_type is nodes.GroupFold else "global_fold"
        return f"{name}({', '.join(parts)})"

    def _format_order(
        self,
        name: str,
        children: tuple[TermId, ...],
        payload: OrderPayload,
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        if name == "topk":
            source, offset, count, *key_children = children
            prefix = [
                self._format(source, row_names, relation_names, level + 1),
                "offset=" + self._format(offset, row_names, relation_names, level + 1),
                "count=" + self._format(count, row_names, relation_names, level + 1),
            ]
        else:
            source, *key_children = children
            prefix = [self._format(source, row_names, relation_names, level + 1)]
        keys: list[str] = []
        for child, spec in zip(key_children, payload.keys, strict=False):
            expression = self._format(child, row_names, relation_names, level + 1)
            collation = ""
            if spec.collation is not None:
                collation = " collate " + self.name_resolver(
                    "collation", spec.collation.value
                )
            keys.append(
                f"{expression} {spec.direction.value} "
                f"nulls {spec.nulls.value}{collation}"
            )
        prefix.append("keys=[" + ", ".join(keys) + "]")
        return f"{name}({', '.join(prefix)})"

    def _binary(
        self,
        symbol: str,
        children: tuple[TermId, ...],
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        left, right = children
        return (
            "("
            + self._format(left, row_names, relation_names, level + 1)
            + f" {symbol} "
            + self._format(right, row_names, relation_names, level + 1)
            + ")"
        )

    def _variadic(
        self,
        symbol: str,
        children: tuple[TermId, ...],
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        values = (
            self._format(child, row_names, relation_names, level + 1)
            for child in children
        )
        return "(" + f" {symbol} ".join(values) + ")"

    def _call(
        self,
        name: str,
        children: tuple[TermId, ...],
        row_names: tuple[str, ...],
        relation_names: tuple[str, ...],
        level: int,
    ) -> str:
        values = [
            self._format(child, row_names, relation_names, level + 1)
            for child in children
        ]
        return f"{name}({self._join(values, level)})"

    def _join(self, values: list[str], level: int) -> str:
        if not self.options.multiline or len(values) <= 1:
            return ", ".join(values)
        separator = ",\n" + self.options.indent * (level + 1)
        return (
            "\n"
            + self.options.indent * (level + 1)
            + separator.join(values)
            + "\n"
            + self.options.indent * level
        )


def format_term(
    arena: TermArena,
    root: TermId,
    *,
    show_sorts: bool = False,
    multiline: bool = False,
    name_resolver: NameResolver = default_name_resolver,
) -> str:
    """Return a binder-aware, human-readable representation of ``root``."""

    return UExprPrinter(
        arena,
        options=PrinterOptions(show_sorts=show_sorts, multiline=multiline),
        name_resolver=name_resolver,
    ).format(root)


def format_arena(
    arena: TermArena,
    root: TermId | None = None,
    *,
    name_resolver: NameResolver = default_name_resolver,
) -> str:
    """Return a deterministic node listing for debugging the hash-consed DAG."""

    ids = (
        tuple(post_order(arena, (root,)))
        if root is not None
        else tuple(term_id for term_id, _ in arena.terms())
    )
    lines: list[str] = []
    for term_id in ids:
        node = arena[term_id]
        children = ", ".join(f"%{child.index}" for child in node.children)
        payload = "" if node.payload is None else f" payload={node.payload!r}"
        lines.append(
            f"%{term_id.index} = {type(node).key}({children})"
            f" : {format_sort(node.sort, name_resolver)}{payload}"
        )
    return "\n".join(lines)


def format_sort(sort: Sort, name_resolver: NameResolver = default_name_resolver) -> str:
    if sort == PREDICATE:
        return "Pred3"
    if sort == MULTIPLICITY:
        return "Multiplicity"
    if isinstance(sort, ScalarSort):
        nullable = "?" if sort.nullable else ""
        return f"Scalar<{_format_sql_type(sort.sql_type)}>{nullable}"
    if isinstance(sort, RowSort):
        return f"Row<{name_resolver('schema', sort.schema.value)}>"
    if isinstance(sort, BagSort):
        return f"Bag<{name_resolver('schema', sort.schema.value)}>"
    if isinstance(sort, SeqSort):
        return f"Seq<{name_resolver('schema', sort.schema.value)}>"
    if isinstance(sort, AggStateSort):
        return f"AggState<{name_resolver('aggregate', sort.aggregate.value)}>"
    if isinstance(sort, RowFunctionSort):
        input_sort = format_sort(sort.input, name_resolver)
        result_sort = format_sort(sort.result, name_resolver)
        return f"({input_sort} -> {result_sort})"
    return repr(sort)


def _bound_name(names: tuple[str, ...], depth: int, prefix: str) -> str:
    try:
        return names[depth]
    except IndexError:
        return f"{prefix}{depth}"


def _format_literal(value: object) -> str:
    if isinstance(value, str):
        return repr(value)
    if isinstance(value, Decimal):
        return f"decimal({str(value)!r})"
    return repr(value)


def _format_sql_type(sql_type) -> str:
    result = sql_type.kind.value
    if sql_type.precision is not None:
        result += f"({sql_type.precision}"
        if sql_type.scale is not None:
            result += f",{sql_type.scale}"
        result += ")"
    return result
