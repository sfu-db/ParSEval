"""A database whose cells and multiplicities are concolic runtime inputs.

Every stored cell and every row multiplicity is an input of one
``parseval.symbolic.Runtime``. The instance owns the concrete assignment of
those inputs, so the same object is both the saved data and the symbolic
state that U-expression execution and the solver refer to.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from parseval.catalog import Catalog
from parseval.symbolic import Runtime, Semantics, ZValue
from parseval.terms.arena import TermArena
from parseval.terms.names import ParameterId, RelationId
from parseval.terms.sorts import INTEGER, ScalarSort

from .domain import carrier


@dataclass(frozen=True, slots=True)
class Slot:
    """One stored row: typed cell inputs and a natural-number multiplicity input."""

    relation: RelationId
    index: int
    cells: tuple[ZValue, ...]
    multiplicity: ZValue
    # Input ids of the cells, then of the multiplicity.
    parameters: tuple[ParameterId, ...] = field(init=False)

    def __post_init__(self) -> None:
        arena = self.runtime.arena
        object.__setattr__(self, "parameters", tuple(
            arena[value.expression.root].payload.parameter for value in (*self.cells, self.multiplicity)
        ))

    @property
    def runtime(self) -> Runtime:
        return self.multiplicity.runtime


class Instance:
    """Relations of slots plus the concrete values of all their inputs.

    Instances are persistent: ``insert`` and ``assign`` return new instances
    that share the runtime and every unchanged slot. A slot with multiplicity
    zero stores no row; it remains available as a symbolic candidate row.
    """

    __slots__ = ("catalog", "runtime", "_slots", "_values")

    def __init__(self, catalog: Catalog, runtime: Runtime | None = None, slots=None, values=None):
        self.catalog = catalog
        if runtime is None:
            semantics = Semantics(
                division_by_zero_is_null=catalog.dialect.division_by_zero_is_null,
                lenient_conversions=catalog.dialect.lenient_conversions,
                text_temporals=catalog.dialect.text_temporals,
            )
            runtime = Runtime(TermArena(catalog.context), semantics=semantics)
        self.runtime = runtime
        self._slots: dict[RelationId, tuple[Slot, ...]] = dict(slots or {})
        self._values: dict[ParameterId, object] = dict(values or {})

    @property
    def arena(self) -> TermArena:
        return self.runtime.arena

    @property
    def values(self) -> Mapping[ParameterId, object]:
        return MappingProxyType(self._values)

    def relations(self) -> tuple[RelationId, ...]:
        return tuple(relation for relation, _ in self.catalog.context.relations())

    def slots(self, relation: RelationId) -> tuple[Slot, ...]:
        return self._slots.get(relation, ())

    def all_slots(self) -> Iterable[Slot]:
        for relation in self.relations():
            yield from self.slots(relation)

    def value(self, value: ZValue) -> object:
        return self._values[self.arena[value.expression.root].payload.parameter]

    def multiplicity(self, slot: Slot) -> int:
        return self.value(slot.multiplicity)

    def row(self, slot: Slot) -> tuple[object, ...]:
        return tuple(self.value(cell) for cell in slot.cells)

    def insert(
        self, relation: RelationId, values: Sequence[object], multiplicity: int = 1
    ) -> tuple[Instance, Slot]:
        """Store a row in a fresh slot; multiplicity zero creates a candidate row."""
        context = self.catalog.context
        fields = context.schema(context.relation(relation).schema).fields
        index = len(self.slots(relation))
        # Instances share the runtime, so names must be unique across versions.
        prefix = f"{relation.value}:{index}@{len(self.runtime.inputs)}"
        cells = tuple(
            self.runtime.input(f"{prefix}:{position}", carrier(value, field), field)
            for position, (value, field) in enumerate(zip(values, fields, strict=True))
        )
        weight = self.runtime.input(f"{prefix}:#", multiplicity, ScalarSort(INTEGER))
        slot = Slot(relation, index, cells, weight)
        updated = Instance(
            self.catalog,
            self.runtime,
            {**self._slots, relation: (*self.slots(relation), slot)},
            self._values,
        )
        for value in (*cells, weight):
            updated._values[self.arena[value.expression.root].payload.parameter] = value.concrete
        return updated, slot

    def assign(self, values: Mapping[ParameterId, object]) -> Instance:
        """Replace the concrete values of existing inputs."""
        return Instance(self.catalog, self.runtime, self._slots, {**self._values, **values})

    def rows(self, relation: RelationId) -> tuple[tuple[object, ...], ...]:
        """Stored rows, each repeated by its multiplicity."""
        return tuple(
            self.row(slot)
            for slot in self.slots(relation)
            for _ in range(self.multiplicity(slot))
        )

    @property
    def row_count(self) -> int:
        return sum(self.multiplicity(slot) for slot in self.all_slots())

    def __repr__(self) -> str:
        tables = {
            str(relation.value): self.rows(relation)
            for relation in self.relations()
            if self.rows(relation)
        }
        return f"Instance({tables})"


__all__ = ["Instance", "Slot"]
