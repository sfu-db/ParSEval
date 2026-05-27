from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


from ..coercion import coerce_reference_value
from ..spec import ColumnSpec
from ..state import RowContext, SchemaRuntime
from ..types import TypeProfile
from ..compiler import ColumnDomainPlan

class ValueProvider(ABC):
    priority = 0

    @abstractmethod
    def supports(self, spec: ColumnSpec, type_profile: TypeProfile) -> int:
        """Return a positive score when this provider can generate the column."""

    @abstractmethod
    def generate(
        self,
        spec: ColumnSpec,
        runtime: SchemaRuntime,
        row_context: RowContext,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        """Generate one schema-valid concrete value."""

    def _resolve_foreign_key(self, spec, runtime):
        """Check if column has a FK and return a referenced value if available."""
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        return None

    def validate(self, value: Any, spec: ColumnSpec, runtime: SchemaRuntime) -> bool:
        return True
