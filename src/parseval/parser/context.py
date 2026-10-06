from __future__ import annotations

from typing import NoReturn

from sqlglot import exp

from parseval.catalog import Catalog
from parseval.errors import ErrorCode, fail
from parseval.terms import TermArena
from parseval.terms.builder import IRBuilder, TermRef
from parseval.terms import terms as nodes
from parseval.identifiers import Identifier, QualifiedName
from parseval.terms.decls import RowShape
from parseval.terms.sorts import (
    ScalarSort,
    TypeKind,
)
from parseval.terms.terms import LiteralPayload

from .scope import ColumnBinder


class LoweringSession:
    __slots__ = (
        "catalog",
        "arena",
        "builder",
        "dialect",
        "binder",
    )

    def __init__(
        self,
        catalog: Catalog,
        arena: TermArena,
        *,
        builder: IRBuilder | None = None,
    ) -> None:
        self.catalog = catalog
        self.arena = arena
        self.builder = builder if builder is not None else IRBuilder(arena)
        self.dialect = catalog.dialect
        self.binder = ColumnBinder(self.dialect)

    def schema_for_sorts(self, sorts: tuple[ScalarSort, ...]):
        return self.catalog.context.intern_schema(
            RowShape(sorts)
        )

    def term_scalar_sort(self, term: TermRef) -> ScalarSort:
        sort = self.arena[self.builder.resolve(term)].sort
        if not isinstance(sort, ScalarSort):
            fail(
                ErrorCode.TYPE_ERROR,
                f"Expected a scalar term, got {sort!r}",
            )
        return sort

    def resolve_table(self, name: QualifiedName):
        return self.catalog.resolve_table(name)

    def resolve_scalar_function(
        self,
        name: str | QualifiedName,
        arguments: tuple[ScalarSort, ...],
    ):
        return self.catalog.resolve_scalar_function(name, arguments)

    def cast_term(
        self,
        term: TermRef,
        actual: ScalarSort,
        target: ScalarSort,
        node: object,
    ) -> TermRef:
        if actual == target:
            return term
        if actual.sql_type == target.sql_type and actual.nullable <= target.nullable:
            return term
        if actual.sql_type.kind is TypeKind.STRING:
            source = self.arena[self.builder.resolve(term)]
            if (
                isinstance(source, nodes.Literal)
                and isinstance(source.payload, LiteralPayload)
                and isinstance(source.payload.value, str)
            ):
                try:
                    value = self.dialect.parse_string_cast_literal(
                        source.payload.value, target.sql_type
                    )
                except ValueError:
                    pass
                else:
                    return self.builder.literal(value, target.sql_type)

        # Non-literal casts are semantic scalar operations.  Represent them
        # explicitly rather than requiring a dedicated term node for every
        # source/target pair.  The exact source and result sorts make unlike
        # SQL casts distinct declarations in the context.
        return self.builder.apply(
            f"cast_{actual.sql_type.kind.value}_to_{target.sql_type.kind.value}",
            (term,),
            target,
        )

    def coerce_scalar_terms(
        self,
        terms: tuple[TermRef, TermRef],
        node: object,
        *,
        prefer_float: bool = False,
    ) -> tuple[TermRef, TermRef]:
        left, right = terms
        left_sort = self.term_scalar_sort(left)
        right_sort = self.term_scalar_sort(right)
        target = self.dialect.common_scalar_sort(
            left_sort,
            right_sort,
            prefer_float=prefer_float,
        )
        if target is None:
            fail(
                ErrorCode.TYPE_ERROR,
                f"Scalar types {left_sort!r} and {right_sort!r} "
                "cannot be coerced to a common type",
                node=node,
            )
        return (
            self.cast_term(left, left_sort, target, node),
            self.cast_term(right, right_sort, target, node),
        )

    def require_common_scalar_sort(
        self,
        left: ScalarSort,
        right: ScalarSort,
        node: object,
        *,
        prefer_float: bool = False,
        arithmetic: bool = False,
        message: str | None = None,
    ) -> ScalarSort:
        result = self.dialect.common_scalar_sort(
            left,
            right,
            prefer_float=prefer_float,
            arithmetic=arithmetic,
        )
        if result is None:
            fail(
                ErrorCode.TYPE_ERROR,
                message
                or f"Scalar types {left!r} and {right!r} "
                "cannot be coerced to a common type",
                node=node,
            )
        return result

    def output_name(
        self,
        expression: exp.Expression,
        *,
        fallback: str,
        inherited: Identifier | None = None,
    ) -> Identifier:
        if isinstance(expression, exp.Alias):
            return self.dialect.identifier(expression.args["alias"])
        return inherited if inherited is not None else Identifier(fallback)

    def nulls_first(self, ordered: exp.Ordered, *, descending: bool) -> bool:
        explicit = ordered.args.get("nulls_first")
        if explicit is not None:
            return bool(explicit)
        try:
            return self.dialect.nulls_first(descending=descending)
        except ValueError as error:
            fail(ErrorCode.TYPE_ERROR, str(error), node=ordered, cause=error)

    def unsupported(
        self,
        message: str,
        node: object,
        *,
        code: ErrorCode = ErrorCode.UNSUPPORTED_QUERY,
    ) -> NoReturn:
        fail(
            code,
            message,
            node=node,
            sql=self.dialect.sql(node),
        )
