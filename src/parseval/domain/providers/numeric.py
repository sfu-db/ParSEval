from __future__ import annotations

from decimal import Decimal

from ..coercion import coerce_reference_value
from ..constraints import RangeConstraint
from parseval.dtype import DataType

from .base import ValueProvider


class IntegerProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(*DataType.INTEGER_TYPES) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        candidate = len(state.used_values) + 1 if spec.unique or spec.primary_key else runtime.rng.randint(1, 1000)
        
        # Apply RangeConstraint if present
        for check in spec.checks:
            if isinstance(check, RangeConstraint):
                mini = check.minimum if check.minimum is not None else -2147483648
                maxi = check.maximum if check.maximum is not None else 2147483647
                if not (check.minimum_inclusive) and check.minimum is not None:
                    mini += 1
                if not (check.maximum_inclusive) and check.maximum is not None:
                    maxi -= 1
                
                if spec.unique or spec.primary_key:
                    candidate = mini + len(state.used_values)
                else:
                    candidate = runtime.rng.randint(mini, maxi)
                
                # Check if we went over maxi
                if candidate > maxi and (spec.unique or spec.primary_key):
                     # Fallback or error? For now let's just use randint if we can't be unique easily
                     # or just accept it might not be unique.
                     # Actually, let's just stick to the constraint first.
                     candidate = runtime.rng.randint(mini, maxi)
                break

        if spec.unique or spec.primary_key:
            while candidate in state.used_values:
                candidate += 1
        return candidate


class RealProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(*DataType.REAL_TYPES) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        if spec.scale is not None:
            value = Decimal(len(state.generated_values) + 1).scaleb(-spec.scale)
            
            # Apply RangeConstraint to Decimal
            for check in spec.checks:
                if isinstance(check, RangeConstraint):
                    if check.minimum is not None:
                        mini = Decimal(str(check.minimum))
                        value = mini + Decimal(len(state.generated_values)).scaleb(-spec.scale)
                        if not check.minimum_inclusive:
                             value += Decimal(1).scaleb(-spec.scale)
                    break

            if spec.unique:
                while value in state.used_values:
                    value += Decimal(1).scaleb(-spec.scale)
            return value
        
        value = round(runtime.rng.uniform(1.0, 1000.0), 6)
        # Apply RangeConstraint to float
        for check in spec.checks:
            if isinstance(check, RangeConstraint):
                mini = float(check.minimum) if check.minimum is not None else 0.0
                maxi = float(check.maximum) if check.maximum is not None else 1000.0
                value = runtime.rng.uniform(mini, maxi)
                break

        while spec.unique and value in state.used_values:
            value = round(value + 1.0, 6)
        return value
