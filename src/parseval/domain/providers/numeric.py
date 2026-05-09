from __future__ import annotations

from decimal import Decimal

from typing import Any, Optional

from ..coercion import coerce_reference_value
from ..compiler import ColumnDomainPlan
from parseval.dtype import DataType
from ..types import TypeFamily, TypeProfile

from .base import ValueProvider


class IntegerProvider(ValueProvider):
    priority = 10

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 10 if type_profile.family == TypeFamily.INTEGER else 0

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
        
        # Use domain_plan if available
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)

        mini = -2147483648
        maxi = 2147483647
        if domain_plan:
            if domain_plan.minimum is not None:
                mini = int(domain_plan.minimum)
                if not domain_plan.minimum_inclusive:
                    mini += 1
            if domain_plan.maximum is not None:
                maxi = int(domain_plan.maximum)
                if not domain_plan.maximum_inclusive:
                    maxi -= 1
            if domain_plan.modulo_divisor:
                remainder = domain_plan.modulo_remainder
                if mini % domain_plan.modulo_divisor != remainder:
                    mini += (
                        remainder - (mini % domain_plan.modulo_divisor)
                    ) % domain_plan.modulo_divisor

        if spec.unique or spec.primary_key:
            candidate = mini + len(state.used_values)
            if domain_plan and domain_plan.modulo_divisor:
                step = domain_plan.modulo_divisor
                if candidate % step != domain_plan.modulo_remainder:
                    candidate += (
                        domain_plan.modulo_remainder - (candidate % step)
                    ) % step
        else:
            if domain_plan and domain_plan.modulo_divisor:
                step = domain_plan.modulo_divisor
                first = mini
                if first % step != domain_plan.modulo_remainder:
                    first += (
                        domain_plan.modulo_remainder - (first % step)
                    ) % step
                if first > maxi:
                    raise ValueError(f"No values satisfy modulo constraint for {spec.qualified_name}")
                count = ((maxi - first) // step) + 1
                candidate = first + step * runtime.rng.randrange(count)
            else:
                candidate = runtime.rng.randint(mini, maxi)

        if spec.unique or spec.primary_key:
            while candidate in state.used_values:
                candidate += domain_plan.modulo_divisor if domain_plan and domain_plan.modulo_divisor else 1
        return candidate


class RealProvider(ValueProvider):
    priority = 10

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 10 if type_profile.family == TypeFamily.DECIMAL else 0

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

        # Use domain_plan if available
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)

        scale = spec.scale if spec.scale is not None else (
            type_profile.scale if type_profile is not None else None
        )
        if scale is not None:
            value = Decimal(len(state.generated_values) + 1).scaleb(-scale)
            
            if domain_plan and domain_plan.minimum is not None:
                mini = Decimal(str(domain_plan.minimum))
                value = mini + Decimal(len(state.generated_values)).scaleb(-scale)
                if not domain_plan.minimum_inclusive:
                        value += Decimal(1).scaleb(-scale)

            if spec.unique:
                while value in state.used_values:
                    value += Decimal(1).scaleb(-scale)
            return value
        
        mini = 0.0
        maxi = 1000.0
        if domain_plan:
            if domain_plan.minimum is not None:
                mini = float(domain_plan.minimum)
            if domain_plan.maximum is not None:
                maxi = float(domain_plan.maximum)
        
        value = round(runtime.rng.uniform(mini, maxi), 6)

        while spec.unique and value in state.used_values:
            value = round(value + 1.0, 6)
        return value
