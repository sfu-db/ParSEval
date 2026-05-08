from __future__ import annotations

from datetime import date, datetime, time, timedelta

from ..coercion import coerce_reference_value
from parseval.dtype import DataType

from .base import ValueProvider


class DateProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(DataType.Type.DATE, DataType.Type.DATE32) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        return date(2020, 1, 1) + timedelta(days=len(state.generated_values))


class DatetimeProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(
            DataType.Type.DATETIME,
            DataType.Type.DATETIME64,
            DataType.Type.TIMESTAMP,
            DataType.Type.TIMESTAMP_S,
            DataType.Type.TIMESTAMP_MS,
            DataType.Type.TIMESTAMP_NS,
            DataType.Type.TIMESTAMPTZ,
            DataType.Type.TIMESTAMPLTZ,
        ) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        return datetime(2020, 1, 1, 0, 0, 0) + timedelta(seconds=len(state.generated_values))


class TimeProvider(ValueProvider):
    priority = 10

    def supports(self, spec) -> int:
        return 10 if spec.datatype.is_type(DataType.Type.TIME, DataType.Type.TIMETZ) else 0

    def generate(self, spec, runtime, row_context, null_rate: float = 0.0):
        state = runtime.column_state(spec.table, spec.column)
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        seconds = len(state.generated_values) % 86400
        hour, rem = divmod(seconds, 3600)
        minute, second = divmod(rem, 60)
        return time(hour, minute, second)
