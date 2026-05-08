from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..spec import ColumnSpec
from ..state import RowContext, SchemaRuntime


class ValueProvider(ABC):
    priority = 0

    @abstractmethod
    def supports(self, spec: ColumnSpec) -> int:
        """Return a positive score when this provider can generate the column."""

    @abstractmethod
    def generate(
        self,
        spec: ColumnSpec,
        runtime: SchemaRuntime,
        row_context: RowContext,
        null_rate: float = 0.0,
    ) -> Any:
        """Generate one schema-valid concrete value."""

    def validate(self, value: Any, spec: ColumnSpec, runtime: SchemaRuntime) -> bool:
        return True
