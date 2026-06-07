from __future__ import annotations

from typing import Any, Optional

from ..compiler import ColumnDomainPlan
from parseval.dtype import TypeProfile
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
        fk_value = self._resolve_foreign_key(spec, runtime)
        if fk_value is not None:
            return fk_value
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)
        return runtime.rng.choice([True, False])
