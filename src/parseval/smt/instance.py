"""Finite weighted support and concrete model materialization."""
from __future__ import annotations

from collections.abc import Mapping
import z3

from parseval.catalog import Catalog
from parseval.instance import Instance
from parseval.terms.names import RelationId, SchemaId
from parseval.uexpr.evaluate import BagEntry
from .budget import Budget, BudgetExceeded
from .values import SymbolicEntry, SymbolicRow, SymbolicValue, _decode, _z3_sort


class SymbolicInstance:
    def __init__(self, catalog: Catalog, support: Mapping[RelationId, int]) -> None:
        if any(count < 0 for count in support.values()):
            raise ValueError("Symbolic support cannot be negative")
        self.catalog = catalog
        self.context = catalog.context
        self.relations = {
            relation: self._relation(relation, specification.schema, support.get(relation, 0))
            for relation, specification in self.context.relations()
        }
        self.completion: dict[RelationId, frozenset[int]] = {}

    def _relation(
        self, relation: RelationId, schema: SchemaId, count: int
    ) -> tuple[SymbolicEntry, ...]:
        fields = self.context.schema(schema).fields
        entries = []
        for row in range(count):
            prefix = f"r{relation.value}_row{row}"
            multiplicity = z3.Int(f"{prefix}_multiplicity")
            values = tuple(
                SymbolicValue(
                    z3.Const(f"{prefix}_col{column}", _z3_sort(field.sql_type)),
                    (
                        z3.Bool(f"{prefix}_col{column}_null")
                        if field.nullable
                        else z3.BoolVal(False)
                    ),
                    field.sql_type,
                )
                for column, field in enumerate(fields)
            )
            entries.append(
                BagEntry(
                    SymbolicRow(schema, values),
                    multiplicity,
                )
            )
        return tuple(entries)

    def materialize(self, model: z3.ModelRef, *, max_rows: int, budget: Budget) -> Instance:
        rows = {}
        total = 0
        for relation, entries in self.relations.items():
            materialized = []
            for entry in entries:
                budget.tick()
                weight = model.eval(entry.multiplicity, model_completion=True).as_long()
                total += weight
                if total > max_rows:
                    raise BudgetExceeded("materialized row limit exceeded")
                values = tuple(_decode(model, value) for value in entry.row.values) if weight else ()
                for _ in range(weight):
                    budget.tick(0)
                    row = list(values)
                    for position in self.completion.get(relation, ()):
                        row[position] = len(materialized) + 1
                    materialized.append(tuple(row))
            rows[relation] = tuple(materialized)
        return Instance.from_rows(self.catalog, rows)
