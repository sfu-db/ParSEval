from __future__ import annotations

from ..coercion import coerce_reference_value
from parseval.dtype import DataType

from .base import ValueProvider


class BooleanProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(DataType.Type.BOOLEAN) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        return runtime.rng.choice([True, False])
