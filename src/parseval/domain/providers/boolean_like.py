from __future__ import annotations

from typing import Any, Optional

from ..coercion import coerce_reference_value
from ..compiler import ColumnDomainPlan
from ..types import TypeProfile
from .base import ValueProvider


class BooleanLikeTinyIntProvider(ValueProvider):
    priority = 20

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 20 if type_profile.exact_type == "TINYINT" and type_profile.family.value == "boolean" else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)
        return runtime.rng.choice([True, False])
