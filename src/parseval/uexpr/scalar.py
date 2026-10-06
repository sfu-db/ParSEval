from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, cast

from parseval.terms import terms as nodes
from parseval.terms.builder import TermRef
from parseval.terms.names import SchemaId
from parseval.terms.sorts import ScalarSort
from parseval.terms.terms import (
    FieldPayload,
    RowLambdaPayload,
    TermId,
    VariablePayload,
)

if TYPE_CHECKING:
    from .lowering import LoweringEnvironment, UExprCompiler


class ScalarTranslator:
    """Translate checked scalar expressions and apply source row lambdas."""

    def __init__(self, compiler: UExprCompiler) -> None:
        self.compiler = compiler
        self.source = compiler.source
        self.builder = compiler.builder

    def translate(
        self,
        term: TermId,
        environment: LoweringEnvironment,
    ) -> TermRef:
        if isinstance(self.source[term], nodes.RowLambda):
            return self.translate_lambda(term, environment)

        translated: dict[TermId, TermRef] = {}
        stack: list[tuple[TermId, bool]] = [(term, False)]
        while stack:
            current, expanded = stack.pop()
            if current in translated:
                continue
            node = self.source[current]
            if isinstance(node, nodes.RowVar):
                translated[current] = environment.row(
                    cast(VariablePayload, node.payload)
                )
                continue
            if isinstance(node, nodes.RowLambda):
                translated[current] = self.translate_lambda(current, environment)
                continue
            if not expanded:
                stack.append((current, True))
                for child in reversed(node.children):
                    if child in translated:
                        continue
                    if type(self.source[child]) in nodes.SQL_EXPR_NODES:
                        stack.append((child, False))
                    else:
                        translated[child] = self.compiler.translate(
                            child, environment
                        )
                continue

            if isinstance(node, nodes.Field):
                payload = cast(FieldPayload, node.payload)
                translated[current] = self.field(
                    translated[node.children[0]], payload.index
                )
                continue
            first_child_sort = (
                self.source[node.children[0]].sort if node.children else None
            )
            if (
                isinstance(node, nodes.Eq3)
                and node.children[0] == node.children[1]
                and isinstance(first_child_sort, ScalarSort)
                and not first_child_sort.nullable
            ):
                translated[current] = self.builder.true3()
                continue
            translated[current] = self.builder.checked(
                type(node),
                tuple(translated[child] for child in node.children),
                node.payload,
            )
        return translated[term]

    def translate_lambda(
        self,
        term: TermId,
        environment: LoweringEnvironment,
    ) -> TermRef:
        node = cast(nodes.RowLambda, self.source[term])
        payload = cast(RowLambdaPayload, node.payload)
        (body,) = node.children
        return self.builder.row_lambda(
            payload.input_schema,
            lambda row: self.compiler.translate(
                body, environment.bind_row(row)
            ),
        )

    def apply_lambda(
        self,
        function: TermId,
        argument: TermRef,
        environment: LoweringEnvironment,
    ) -> TermRef:
        node = cast(nodes.RowLambda, self.source[function])
        return self.compiler.translate(
            node.children[0], environment.bind_row(argument)
        )

    def field(self, row: TermRef, index: int) -> TermRef:
        return self.builder.project_field(row, index)

    def make_row(self, schema: SchemaId, fields: Iterable[TermRef]) -> TermRef:
        return self.builder.row(schema, fields)

    def concat_rows(
        self,
        left: TermRef,
        left_schema: SchemaId,
        right: TermRef,
        right_schema: SchemaId,
        output_schema: SchemaId,
    ) -> TermRef:
        left_width = len(self.source.context.schema(left_schema).fields)
        right_width = len(self.source.context.schema(right_schema).fields)
        return self.make_row(
            output_schema,
            (
                *(self.field(left, index) for index in range(left_width)),
                *(self.field(right, index) for index in range(right_width)),
            ),
        )
