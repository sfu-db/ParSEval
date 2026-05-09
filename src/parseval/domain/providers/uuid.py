from __future__ import annotations

from typing import Any, Optional
import uuid

from ..coercion import coerce_reference_value
from ..compiler import ColumnDomainPlan
from ..types import TypeFamily, TypeProfile
from .base import ValueProvider


class UUIDProvider(ValueProvider):
    priority = 20

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 20 if type_profile.family == TypeFamily.UUID else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)
        candidate = uuid.UUID(int=runtime.rng.getrandbits(128))
        while spec.unique and candidate in state.used_values:
            candidate = uuid.UUID(int=runtime.rng.getrandbits(128))
        return candidate
