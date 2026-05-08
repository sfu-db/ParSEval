from __future__ import annotations

import string

from ..coercion import coerce_reference_value
from parseval.dtype import DataType

from .base import ValueProvider


class StringProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(*DataType.TEXT_TYPES) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        prefix = spec.column
        length = spec.length or 12
        if spec.unique or spec.primary_key:
            candidate = f"{prefix}_{len(state.generated_values) + 1}"
            while candidate in state.used_values:
                candidate = f"{prefix}_{len(state.used_values) + 1}"
            return candidate[:length]
        alphabet = string.ascii_lowercase + string.digits
        size = min(max(1, length), 12)
        return "".join(runtime.rng.choice(alphabet) for _ in range(size))
