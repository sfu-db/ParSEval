"""Configuration for coverage-directed instance generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Budgets shared by exploration and the bounded SMT backend."""

    group_size: int = 3
    timeout_ms: int | None = 5_000
    max_support: int = 8
    max_encoding_steps: int = 100_000
    max_rows: int = 10_000
    max_attempts: int | None = None
    minimize: bool = True

    def __post_init__(self) -> None:
        if self.group_size < 1:
            raise ValueError("group_size must be positive")
        if self.timeout_ms is not None and self.timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive or None")
        if min(self.max_support, self.max_encoding_steps, self.max_rows) < 1:
            raise ValueError("support, encoding, and row limits must be positive")
        if self.max_attempts is not None and self.max_attempts < 1:
            raise ValueError("max_attempts must be positive or None")


__all__ = ["GenerationConfig"]
