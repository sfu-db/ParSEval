from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GenerationConfig:
    """Resource and witness-shape limits for symbolic generation."""

    bootstrap_rows: int = 3
    bootstrap_negatives: bool = True
    root_rows: int = 3
    groups: int = 3
    rows_per_group: int = 3
    subquery_rows: int = 1
    order_competitors: int = 1
    max_rows_per_table: int = 128
    max_total_rows: int = 512
    max_solver_calls: int = 48
    solver_timeout_ms: int = 1000
    seed: int = 12322

    def __post_init__(self) -> None:
        positive = {
            "bootstrap_rows": self.bootstrap_rows,
            "root_rows": self.root_rows,
            "groups": self.groups,
            "rows_per_group": self.rows_per_group,
            "subquery_rows": self.subquery_rows,
            "max_rows_per_table": self.max_rows_per_table,
            "max_total_rows": self.max_total_rows,
            "max_solver_calls": self.max_solver_calls,
            "solver_timeout_ms": self.solver_timeout_ms,
        }
        invalid = [name for name, value in positive.items() if value < 1]
        if invalid:
            raise ValueError(f"GenerationConfig fields must be positive: {', '.join(invalid)}")
        if self.order_competitors < 0:
            raise ValueError("order_competitors must be non-negative")


__all__ = ["GenerationConfig"]
