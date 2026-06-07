from __future__ import annotations

from typing import Any, Optional

from ..compiler import ColumnDomainPlan
from parseval.dtype import TypeProfile
from .base import ValueProvider


class EnumProvider(ValueProvider):
    priority = 20

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 20 if type_profile.exact_type == "ENUM" else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)
        expressions = spec.datatype.args.get("expressions") or []
        values = []
        for expr in expressions:
            if isinstance(expr, str):
                literal = expr
            else:
                value = getattr(expr, "this", expr)
                literal = getattr(value, "this", value)
            values.append(literal)
        return runtime.rng.choice(values)
