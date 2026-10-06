"""Integrity constraints of the catalog as predicates over instance slots.

``integrity`` states what must hold for a set of slots whose inputs are open,
relative to every other slot of the instance: storage limits, keys, foreign
keys, CHECK constraints and generated columns. Stored slots already satisfy
these constraints, so only constraints mentioning an open slot are produced.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable

from parseval.catalog import Catalog, TableDecl
from parseval.terms import terms as nodes
from parseval.terms.constraints import (
    CheckDecl,
    ForeignKeyDecl,
    GeneratedColumnDecl,
    NullConflictPolicy,
    PrimaryKeyDecl,
    UniqueDecl,
)
from parseval.terms.names import ParameterId
from parseval.terms.sorts import INTEGER, ScalarSort
from parseval.terms.terms import TermId

from .domain import fits, limit
from .model import Slot
from .valuation import Valuation


class Integrity:
    def __init__(self, valuation: Valuation, *, bounded: bool = True):
        """``bounded`` stores a candidate row at most once; a relaxed problem
        lets it repeat, to measure how many rows a requirement needs."""
        self.v = valuation
        self.bounded = bounded
        self.catalog: Catalog = valuation.instance.catalog
        self.tables = {table.relation: table for table in self.catalog.tables()}
        # Keys of stored rows, compared by value: terms between two stored
        # rows would only fold to constants.
        self._keys: dict[tuple, Counter] = {}
        self._candidates: dict = {}
        self._terms: dict = {}

    def constraints(self, slots: Iterable[Slot], cells: Collection[ParameterId]) -> list[TermId]:
        """Constraints of open slots; storage limits only for the given cell inputs.

        A cell that no requirement mentions keeps its stored placeholder,
        which already satisfies its storage limit.
        """
        result = []
        for slot in slots:
            table = self.tables[slot.relation]
            result.extend(self._slot(table, slot, cells))
        return [constraint for constraint in result if constraint != self.v.true]

    def _cells(self, slot: Slot) -> tuple[TermId, ...]:
        key = (slot.relation, slot.index)
        if key not in self._terms:
            self._terms[key] = tuple(self.v.input(cell) for cell in slot.cells)
        return self._terms[key]

    def _present(self, slot: Slot) -> TermId:
        return self.v.positive(self.v.input(slot.multiplicity))

    def _closed(self, slot: Slot) -> bool:
        return self.v.open.isdisjoint(slot.parameters)

    def _open(self, relation) -> list[Slot]:
        """The relation's slots with an open input."""
        if relation not in self._candidates:
            self._candidates[relation] = [slot for slot in self.v.instance.slots(relation) if not self._closed(slot)]
        return self._candidates[relation]

    def _stored(self, relation, positions: tuple[int, ...]) -> Counter:
        """How often each key occurs among stored rows that no input leaves open."""
        key = (relation, positions)
        if key not in self._keys:
            instance = self.v.instance
            self._keys[key] = Counter(
                tuple(row[position] for position in positions)
                for slot in instance.slots(relation)
                if self._closed(slot) and instance.multiplicity(slot)
                for row in (instance.row(slot),)
            )
        return self._keys[key]

    def _slot(self, table: TableDecl, slot: Slot, active: Collection[ParameterId]) -> list[TermId]:
        v = self.v
        present = self._present(slot)
        if self._closed(slot):
            # A stored row is checked by its values.
            if not v.instance.multiplicity(slot):
                return []
            row = v.instance.row(slot)
            if not all(value is None or fits(value, binding.storage_type) for value, binding in zip(row, table.columns)):
                return [v.false]
            result = []
        else:
            weight = v.input(slot.multiplicity)
            # A candidate row is stored at most once; duplicates use separate slots.
            result = [v.not3(v.lt3(weight, v.zero))]
            if self.bounded:
                result.append(v.not3(v.lt3(v.one, weight)))
            mentioned = [parameter in active for parameter in slot.parameters[:-1]]
            result.extend(self._storage(table, self._cells(slot), mentioned))
        for item in table.constraints:
            if not item.metadata.proof_active:
                continue
            if isinstance(item, (PrimaryKeyDecl, UniqueDecl)):
                result.extend(self._unique(table, slot, item))
            elif isinstance(item, ForeignKeyDecl):
                result.append(self._foreign(table, slot, item))
            elif isinstance(item, CheckDecl):
                check = self._expression(item.predicate.term, self._cells(slot))
                result.append(v.or3(v.not3(present), v.not3(v.is_false(check))))
            elif isinstance(item, GeneratedColumnDecl):
                position = table.spec.column_position(item.column)
                value = self._expression(item.expression.term, self._cells(slot))
                result.append(v.or3(v.not3(present), v.same(self._cells(slot)[position], value)))
        return result

    def _storage(self, table: TableDecl, cells: tuple[TermId, ...], mentioned: list[bool]) -> list[TermId]:
        v = self.v
        result = []
        for binding, cell, used in zip(table.columns, cells, mentioned, strict=True):
            if not used:
                continue
            storage = binding.storage_type
            bound = limit(storage)
            if bound is None:
                continue
            kind, size = bound
            if kind == "length":
                length = v.apply("length", (cell,), ScalarSort(INTEGER, True))
                within = v.not3(v.lt3(v.literal(size, INTEGER), length))
            elif kind == "integer":
                within = v.and3(v.not3(v.lt3(cell, v.literal(-size, INTEGER))), v.lt3(cell, v.literal(size, INTEGER)))
            else:
                upper = v.literal(size, storage.value_type)
                lower = v.apply("neg", (upper,), ScalarSort(storage.value_type))
                within = v.and3(v.lt3(lower, cell), v.lt3(cell, upper))
            result.append(v.or3(v.is_null(cell), within))
        return result

    def _unique(self, table: TableDecl, slot: Slot, item) -> list[TermId]:
        v = self.v
        positions = [table.spec.column_position(column) for column in item.columns]
        distinct_nulls = item.null_policy is NullConflictPolicy.NULLS_DISTINCT
        closed = self._closed(slot)
        if closed:
            if not v.instance.multiplicity(slot):
                return []
            value = tuple(v.instance.row(slot)[position] for position in positions)
            if not (distinct_nulls and None in value) and self._stored(slot.relation, tuple(positions))[value] > 1:
                return [v.false]
        # An open single-column key avoids stored keys by its domain (Valuation.excluded).
        domain = len(positions) == 1 and slot.parameters[positions[0]] in v.excluded
        result = []
        others = self._open(slot.relation) if closed or domain else v.instance.slots(slot.relation)
        key = [self._cells(slot)[position] for position in positions] if others else []
        for other in others:
            if other.index == slot.index:
                continue
            other_cells = self._cells(other)
            other_key = [other_cells[position] for position in positions]
            conflict = v.and3(self._present(slot), self._present(other), v.same_row(tuple(key), tuple(other_key)))
            if distinct_nulls:
                conflict = v.and3(conflict, *(v.not3(v.is_null(cell)) for cell in key))
            result.append(v.not3(conflict))
        return result

    def _foreign(self, table: TableDecl, slot: Slot, item: ForeignKeyDecl) -> TermId:
        v = self.v
        parent = self.tables[item.target_relation]
        positions = [parent.spec.column_position(column) for column in item.target]
        matches = []
        closed = self._closed(slot)
        if closed:
            value = tuple(v.instance.row(slot)[table.spec.column_position(column)] for column in item.source)
            if not v.instance.multiplicity(slot) or None in value or self._stored(parent.relation, tuple(positions))[value]:
                return v.true
        cells = self._cells(slot)
        source = tuple(cells[table.spec.column_position(column)] for column in item.source)
        for candidate in self._open(item.target_relation) if closed else v.instance.slots(item.target_relation):
            target_cells = self._cells(candidate)
            target = tuple(target_cells[position] for position in positions)
            matches.append(v.and3(self._present(candidate), v.same_row(source, target)))
        return v.or3(v.not3(self._present(slot)), *(v.is_null(cell) for cell in source), *matches)

    def _expression(self, term: TermId, cells: tuple[TermId, ...]) -> TermId:
        """Instantiate a catalog row function on a slot's cells."""
        arena = self.catalog.constraint_arena
        body = arena[term].children[0]
        memo: dict[TermId, TermId] = {}

        def visit(current: TermId) -> TermId:
            if current in memo:
                return memo[current]
            node = arena[current]
            if isinstance(node, nodes.Field) and isinstance(arena[node.children[0]], nodes.RowVar):
                result = cells[node.payload.index]
            else:
                result = self.v.node(type(node), tuple(visit(child) for child in node.children), node.payload)
            memo[current] = result
            return result

        return visit(body)


__all__ = ["Integrity"]
