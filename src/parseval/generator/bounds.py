from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar, Tuple


@dataclass(frozen=True)
class BmcBounds:
    table_rows: int = 1
    join_width: int = 1
    groups: int = 1
    rows_per_group: int = 1
    subquery_rows: int = 1
    order_competitors: int = 0
    max_iterations: int = 4
    iteration: int = 0
    max_table_rows: int = 512

    EXPANSION_ORDER: ClassVar[Tuple[str, ...]] = (
        "subquery_rows",
        "table_rows",
        "join_width",
        "rows_per_group",
        "groups",
        "order_competitors",
    )

    @property
    def exhausted(self) -> bool:
        return self.iteration >= self.max_iterations

    @property
    def exhaustion_status(self) -> str:
        return "bounded_unknown" if self.exhausted else ""

    def raise_table_rows(self, required: int) -> tuple["BmcBounds", str]:
        if required > self.max_table_rows:
            return (
                self,
                f"structural_exceeds_cap:required={required},max={self.max_table_rows}",
            )
        raised = max(self.table_rows, required)
        if raised == self.table_rows:
            return self, ""
        return replace(self, table_rows=raised), ""

    def expand_next(self) -> "BmcBounds":
        field = self.EXPANSION_ORDER[self.iteration % len(self.EXPANSION_ORDER)]
        new_value = getattr(self, field) + 1
        if field == "table_rows":
            new_value = min(new_value, self.max_table_rows)
        return replace(self, **{field: new_value}, iteration=self.iteration + 1)
