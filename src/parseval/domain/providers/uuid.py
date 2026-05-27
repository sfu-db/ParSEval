from __future__ import annotations

from typing import Any, Optional
import uuid

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
        fk_value = self._resolve_foreign_key(spec, runtime)
        if fk_value is not None:
            return fk_value
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)
        candidate = uuid.UUID(int=runtime.rng.getrandbits(128))
        while spec.unique and candidate in state.used_values:
            candidate = uuid.UUID(int=runtime.rng.getrandbits(128))
        return candidate
