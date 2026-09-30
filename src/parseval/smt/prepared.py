"""Arena-only analysis, shared by every support attempt of a solve."""

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.terms import TermId
from parseval.terms.binding import row_binders_in_child, relation_binders_in_child
from parseval.terms.builder import IRBuilder
from parseval.uexpr.espnf import inspect_bag_espnf
from parseval.uexpr.normalize import to_espnf
from parseval.uexpr.witness import WitnessPlan, plan_product


class PreparedTerms:
    def __init__(self, arena: TermArena) -> None:
        self.arena = arena
        self._dependencies = {}
        self._plans = {}
        self._sum_bags = {}

    def dependencies(self, term: TermId) -> tuple[tuple[int, ...], tuple[int, ...]]:
        """Free De Bruijn indices in both namespaces, accounting for binders."""
        pending = [(term, False)]
        while pending:
            current, expanded = pending.pop()
            if current in self._dependencies:
                continue
            node = self.arena[current]
            if not expanded:
                pending.append((current, True))
                pending.extend((child, False) for child in node.children)
                continue
            rows = {node.payload.depth} if isinstance(node, nodes.RowVar) else set()
            relations = {node.payload.depth} if isinstance(node, nodes.RelVar) else set()
            for position, child in enumerate(node.children):
                child_rows, child_relations = self._dependencies[child]
                rb = row_binders_in_child(type(node), position)
                relb = relation_binders_in_child(type(node), position)
                rows.update(index - rb for index in child_rows if index >= rb)
                relations.update(index - relb for index in child_relations if index >= relb)
            self._dependencies[current] = (tuple(sorted(rows)), tuple(sorted(relations)))
        return self._dependencies[term]

    def plans(self, bag: TermId) -> tuple[WitnessPlan, ...]:
        if bag not in self._plans:
            view = inspect_bag_espnf(self.arena, to_espnf(self.arena, bag))
            self._plans[bag] = tuple(plan_product(self.arena, view.schema, branch)
                                     for branch in view.alternatives)
        return self._plans[bag]

    def sum_bag(self, term: TermId) -> TermId:
        if term not in self._sum_bags:
            builder = IRBuilder(self.arena)
            self._sum_bags[term] = builder.resolve(
                builder.checked(nodes.BagLambda, (self.arena[term].children[0],))
            )
        return self._sum_bags[term]
