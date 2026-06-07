from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional

from ..compiler import ColumnDomainPlan
from parseval.dtype import DataType, TypeFamily, TypeProfile

from .base import ValueProvider


class DateProvider(ValueProvider):
    priority = 10

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 10 if type_profile.family == TypeFamily.DATE else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        state = runtime.column_state(spec.id)
        fk_value = self._resolve_foreign_key(spec, runtime)
        if fk_value is not None:
            return fk_value

        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)

        mini = date(2020, 1, 1)
        maxi = date(2025, 12, 31)
        if domain_plan:
            if domain_plan.minimum is not None:
                mini = domain_plan.minimum
            if domain_plan.maximum is not None:
                maxi = domain_plan.maximum

        days_diff = (maxi - mini).days
        if days_diff <= 0:
            return mini
            
        value = mini + timedelta(days=runtime.rng.randint(0, days_diff))
        if spec.unique:
            while value in state.used_values:
                value = mini + timedelta(days=runtime.rng.randint(0, days_diff))
        return value


class DatetimeProvider(ValueProvider):
    priority = 10

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 10 if type_profile.family == TypeFamily.DATETIME else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        state = runtime.column_state(spec.id)
        fk_value = self._resolve_foreign_key(spec, runtime)
        if fk_value is not None:
            return fk_value

        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)

        mini = datetime(2020, 1, 1, 0, 0, 0)
        maxi = datetime(2025, 12, 31, 23, 59, 59)
        if domain_plan:
            if domain_plan.minimum is not None:
                mini = domain_plan.minimum
            if domain_plan.maximum is not None:
                maxi = domain_plan.maximum

        seconds_diff = int((maxi - mini).total_seconds())
        if seconds_diff <= 0:
            return mini
            
        value = mini + timedelta(seconds=runtime.rng.randint(0, seconds_diff))
        if spec.unique:
            while value in state.used_values:
                value = mini + timedelta(seconds=runtime.rng.randint(0, seconds_diff))
        return value


class TimeProvider(ValueProvider):
    priority = 10

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 10 if type_profile.family == TypeFamily.TIME else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        state = runtime.column_state(spec.id)
        fk_value = self._resolve_foreign_key(spec, runtime)
        if fk_value is not None:
            return fk_value

        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)

        mini_secs = 0
        maxi_secs = 86399
        if domain_plan:
            if domain_plan.minimum is not None:
                m = domain_plan.minimum
                mini_secs = m.hour * 3600 + m.minute * 60 + m.second
            if domain_plan.maximum is not None:
                m = domain_plan.maximum
                maxi_secs = m.hour * 3600 + m.minute * 60 + m.second

        if maxi_secs <= mini_secs:
            seconds = mini_secs
        else:
            if spec.unique:
                seconds = (mini_secs + len(state.used_values)) % 86400
            else:
                seconds = runtime.rng.randint(mini_secs, maxi_secs)
        
        hour, rem = divmod(seconds, 3600)
        minute, second = divmod(rem, 60)
        value = time(hour, minute, second)
        
        if spec.unique:
            while value in state.used_values:
                seconds = (seconds + 1) % 86400
                hour, rem = divmod(seconds, 3600)
                minute, second = divmod(rem, 60)
                value = time(hour, minute, second)
        return value
