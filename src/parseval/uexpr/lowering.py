from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from parseval.errors import UExprTranslationError
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder, LexicalScope, TermRef, lexical_scope
from parseval.terms.sorts import Sort
from parseval.terms.terms import (
    COMPACT_ONLY_NODES,
    OCCURRENCE_NODES,
    SQL_EXPR_NODES,
    UEXPR_NODES,
    TermId,
    VariablePayload,
)
from parseval.terms.verify import verify_closed, verify_uexpr

from .aggregate import AggregateTranslator
from .bag import BagWeightTranslator
from .normalize import simplify_uexpr
from .scalar import ScalarTranslator


@dataclass(frozen=True, slots=True)
class LoweringEnvironment:
    """Target terms bound to source De Bruijn variables."""

    rows: tuple[TermRef, ...] = ()
    relations: tuple[TermRef, ...] = ()

    def bind_row(self, row: TermRef) -> LoweringEnvironment:
        return LoweringEnvironment((row, *self.rows), self.relations)

    def bind_relation(self, relation: TermRef) -> LoweringEnvironment:
        return LoweringEnvironment(self.rows, (relation, *self.relations))

    def row(self, variable: VariablePayload) -> TermRef:
        return self.rows[variable.depth]

    def relation(self, variable: VariablePayload) -> TermRef:
        return self.relations[variable.depth]

    @property
    def lexical_scope(self) -> LexicalScope:
        return LexicalScope.covering(
            lexical_scope(term) for term in (*self.rows, *self.relations)
        )


@dataclass(frozen=True, slots=True)
class CompiledUExpr:
    """Roots produced while translating one compact query."""

    simplified_root: TermId
    sort: Sort


class UExprCompiler:
    """Translate checked compact IR into a factorized U-expression."""

    def __init__(self, source: TermArena, target: TermArena) -> None:
        if source.context is not target.context:
            raise ValueError("Source and target arenas must share one Context")
        self.source = source
        self.target = target
        self.builder = IRBuilder(target)
        self._memo: dict[
            tuple[TermId, tuple[TermRef, ...], tuple[TermRef, ...]],
            TermRef,
        ] = {}
        self.scalar = ScalarTranslator(self)
        self.bags = BagWeightTranslator(self)
        self.aggregates = AggregateTranslator(self)

    def compile(self, root: TermId) -> CompiledUExpr:
        """Compile a closed compact root without forcing distributive expansion."""

        source_sort = verify_closed(self.source, root)
        translated = self.builder.resolve(
            self.translate(root, LoweringEnvironment())
        )
        simplified = simplify_uexpr(self.target, translated)
        target_sort = verify_uexpr(self.target, simplified)
        if target_sort != source_sort:
            raise UExprTranslationError(
                "Compact-to-U translation changed the root sort: "
                f"{source_sort!r} != {target_sort!r}"
            )
        return CompiledUExpr(simplified, source_sort)

    def translate(self, term: TermId, environment: LoweringEnvironment) -> TermRef:
        key = (
            term,
            environment.rows,
            environment.relations,
        )
        cached = self._memo.get(key)
        if cached is not None:
            return cached
        result = self.builder.build_in_scope(
            environment.lexical_scope,
            lambda: self._translate_uncached(term, environment),
        )
        self._memo[key] = result
        return result

    def _translate_uncached(
        self,
        term: TermId,
        environment: LoweringEnvironment,
    ) -> TermRef:
        node = self.source[term]
        node_type = type(node)
        if node_type in SQL_EXPR_NODES:
            return self.scalar.translate(term, environment)
        if node_type is nodes.RelVar:
            return environment.relation(cast(VariablePayload, node.payload))
        if node_type is nodes.LetRel:
            definition, body = node.children
            translated_definition = self.translate(definition, environment)
            return self.builder.let_rel(
                translated_definition,
                lambda relation: self.translate(
                    body, environment.bind_relation(relation)
                ),
            )
        if node_type in OCCURRENCE_NODES:
            return self.bags.translate_bag(term, environment)
        if node_type is nodes.TopK:
            source, offset, count, *keys = node.children
            ordered = self.builder.checked(
                nodes.OrderBy,
                (
                    self._translate_bag_input(source, environment),
                    *(self.translate(key, environment) for key in keys),
                ),
                node.payload,
            )
            return self.builder.slice(
                self.translate(offset, environment),
                self.translate(count, environment),
                ordered,
            )
        if node_type in {nodes.GlobalFold, nodes.GroupFold}:
            return self.aggregates.translate(term, environment)
        if node_type in {nodes.Fold, nodes.OrderBy, nodes.Window}:
            source, *remaining = node.children
            return self.builder.checked(
                node_type,
                (
                    self._translate_bag_input(source, environment),
                    *(self.translate(child, environment) for child in remaining),
                ),
                node.payload,
            )
        if node_type not in UEXPR_NODES:
            category = "compact" if node_type in COMPACT_ONLY_NODES else "semantic"
            raise UExprTranslationError(
                f"Missing translation for {category} operator {node_type.key}"
            )
        return self.builder.checked(
            node_type,
            (self.translate(child, environment) for child in node.children),
            node.payload,
        )

    def _translate_bag_input(
        self,
        term: TermId,
        environment: LoweringEnvironment,
    ) -> TermRef:
        if type(self.source[term]) in OCCURRENCE_NODES:
            return self.bags.translate_bag(term, environment)
        return self.translate(term, environment)
