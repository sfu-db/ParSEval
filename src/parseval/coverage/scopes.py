"""Lexically scoped observation sites in a compiled U-expression."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from parseval.errors import IRValidationError
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.sorts import BagSort, RowSort, SeqSort
from parseval.terms.terms import TermId
from parseval.terms.verify import verify_closed
from parseval.uexpr.espnf import inspect_bag_espnf
from parseval.uexpr.normalize import to_espnf
from .witness import WitnessPlan, support_plans


@dataclass(frozen=True, slots=True)
class CoverageScope:
    bag: TermId
    source: TermId
    relations: tuple[TermId, ...]
    path: tuple[int, ...]
    contexts: tuple[WitnessPlan, ...] = ()


@dataclass(frozen=True, slots=True)
class UnitSite:
    term: TermId
    source: TermId
    relations: tuple[TermId, ...]
    path: tuple[int, ...]


def _bound_term(arena: TermArena, term: TermId, relations: tuple[TermId, ...]) -> TermId:
    for definition in relations:
        term = arena.intern_checked(nodes.LetRel, (definition, term))
    return term


def _sites(arena: TermArena, root: TermId):
    def visit(term: TermId, path: tuple[int, ...], relations: tuple[TermId, ...]):
        node = arena[term]
        yield term, path, relations
        if isinstance(node, nodes.LetRel):
            definition, body = node.children
            yield from visit(definition, (*path, 0), relations)
            yield from visit(body, (*path, 1), (definition, *relations))
        else:
            for index, child in enumerate(node.children):
                yield from visit(child, (*path, index), relations)

    return visit(root, (), ())


def closed_scopes(arena: TermArena, root: TermId) -> tuple[CoverageScope, ...]:
    result = []
    for term, path, relations in _sites(arena, root):
        if not isinstance(arena[term], nodes.BagLambda) and not (
            _is_relation_root(arena, root, path) and _coverable_bag(arena, term)
        ):
            continue
        contexts: list[tuple[WitnessPlan, ...]] = []
        try:
            verify_closed(arena, _bound_term(arena, term, relations))
            contexts.append(())
        except IRValidationError:
            for ancestor in sorted(result, key=lambda item: len(item.path), reverse=True):
                if not isinstance(arena[ancestor.source], nodes.BagLambda):
                    continue
                if _term_at_path(arena, root, ancestor.path) != ancestor.source:
                    continue
                if path[:len(ancestor.path) + 2] != (*ancestor.path, 0, 0):
                    continue
                view = inspect_bag_espnf(arena, ancestor.bag)
                for branch in view.alternatives:
                    for plan in support_plans(arena, ancestor.bag, branch):
                        chain = (*ancestor.contexts, plan)
                        try:
                            verify_closed(
                                arena, _bound_term(arena, term, relations),
                                rows=_context_rows(chain),
                            )
                        except IRValidationError:
                            continue
                        contexts.append(chain)
                if contexts:
                    break
        for context in contexts:
            result.append(CoverageScope(
                as_espnf_bag(arena, term), term, relations, path, context
            ))
            if not isinstance(arena[term], nodes.BagLambda):
                continue
            function = arena[term].children[0]
            body = arena[function].children[0]
            for nested, suffix, _ in _sites(arena, body):
                if not isinstance(arena[nested], nodes.Sum) or not _under_nonlinear(
                    arena, body, suffix
                ):
                    continue
                candidate = arena.rebuild(term, (arena.rebuild(function, (nested,)),))
                try:
                    verify_closed(
                        arena, _bound_term(arena, candidate, relations),
                        rows=_context_rows(context),
                    )
                except IRValidationError:
                    continue
                result.append(CoverageScope(
                    as_espnf_bag(arena, candidate), candidate, relations,
                    (*path, 0, 0, *suffix), context,
                ))
    return tuple(result)


def _term_at_path(arena: TermArena, root: TermId, path: tuple[int, ...]) -> TermId:
    for index in path:
        root = arena[root].children[index]
    return root


def _context_rows(contexts: tuple[WitnessPlan, ...]) -> tuple[RowSort, ...]:
    rows: tuple[RowSort, ...] = ()
    for plan in contexts:
        current = tuple(
            RowSort(plan.variables[index])
            for index in reversed(range(plan.output_variable))
        ) + (RowSort(plan.variables[plan.output_variable]),)
        rows = (*current, *rows)
    return rows


def _is_relation_root(arena: TermArena, root: TermId, path: tuple[int, ...]) -> bool:
    if not path:
        return True
    parent = root
    for index in path[:-1]:
        parent = arena[parent].children[index]
    return isinstance(arena[parent], nodes.LetRel) and path[-1] == 1


def _under_nonlinear(arena: TermArena, root: TermId, path: tuple[int, ...]) -> bool:
    term = root
    for index in path:
        if isinstance(arena[term], (nodes.Squash, nodes.UNot)):
            return True
        term = arena[term].children[index]
    return False


def unsupported_scopes(arena: TermArena, root: TermId) -> tuple[str, ...]:
    issues = []
    scopes = closed_scopes(arena, root)
    covered_paths = {scope.path for scope in scopes}
    if isinstance(arena[root].sort, SeqSort):
        issues.append("ordered output positions are not coverage goals")
    for term, path, relations in _sites(arena, root):
        node = arena[term]
        if isinstance(node, nodes.BagLambda) and path not in covered_paths:
            try:
                verify_closed(arena, _bound_term(arena, term, relations))
            except IRValidationError as error:
                issues.append(f"{node.key}@{path}: {error}")
        elif isinstance(node, (nodes.GroupFold, nodes.GlobalFold)):
            issues.append(
                f"{node.key}@{path}: aggregate value outcomes are not coverage goals"
            )
        elif isinstance(node, nodes.Window):
            issues.append(f"{node.key}@{path}: window outcomes are not coverage goals")
    for scope in scopes:
        view = inspect_bag_espnf(arena, scope.bag)
        for index, branch in enumerate(view.alternatives):
            if not support_plans(arena, scope.bag, branch):
                issues.append(
                    f"{type(arena[scope.source]).key}@{scope.path}: "
                    f"alternative {index} has no complete finite witness domain"
                )
    if not scopes:
        issues.append("no complete finite bag scope")
    return tuple(dict.fromkeys(issues))


def closed_unit_sites(arena: TermArena, root: TermId) -> tuple[UnitSite, ...]:
    result = []
    for term, path, relations in _sites(arena, root):
        if not isinstance(arena[term], (nodes.GroupFold, nodes.GlobalFold)):
            continue
        source = arena[term].children[0]
        try:
            verify_closed(arena, _bound_term(arena, source, relations))
        except IRValidationError:
            continue
        result.append(UnitSite(term, source, relations, path))
    return tuple(result)


def as_espnf_bag(arena: TermArena, source: TermId) -> TermId:
    if isinstance(arena[source], nodes.BagLambda):
        return to_espnf(arena, source)
    if not _coverable_bag(arena, source):
        raise TypeError(f"Coverage cannot observe bag source {type(arena[source]).key}")
    schema = cast(BagSort, arena[source].sort).schema
    builder = IRBuilder(arena)
    observed = builder.bag_lam(
        schema,
        lambda output: builder.sum(
            RowSort(schema),
            lambda row: builder.mul(
                builder.at(source, row),
                builder.indicator(builder.row_identity_eq(row, output)),
            ),
        ),
    )
    return to_espnf(arena, builder.resolve(observed))


def _coverable_bag(arena: TermArena, term: TermId) -> bool:
    return isinstance(
        arena[term],
        (nodes.BagLambda, nodes.Base, nodes.GroupFold, nodes.GlobalFold, nodes.RelVar),
    ) and isinstance(arena[term].sort, BagSort)


def fingerprint(arena: TermArena, root: TermId) -> str:
    memo: dict[TermId, str] = {}
    for term in arena.post_order((root,)):
        node = arena[term]
        children = ",".join(memo[child] for child in node.children)
        memo[term] = sha256(
            f"{type(node).key}|{node.payload!r}|{children}".encode()
        ).hexdigest()[:12]
    return memo[root]


__all__ = [
    "CoverageScope", "UnitSite", "as_espnf_bag", "closed_scopes",
    "closed_unit_sites", "fingerprint", "unsupported_scopes",
]
