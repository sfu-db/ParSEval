from __future__ import annotations

import string
from typing import Any, Optional

from ..compiler import ColumnDomainPlan
from parseval.dtype import DataType, TypeFamily, TypeProfile

from .base import ValueProvider


class StringProvider(ValueProvider):
    priority = 10

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 10 if type_profile.family == TypeFamily.TEXT else 0

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

        # Use domain_plan if available
        if domain_plan and domain_plan.allowed_values:
            return runtime.rng.choice(domain_plan.allowed_values)

        prefix = ""
        if domain_plan and domain_plan.pattern:
            # Simple pattern support: if it looks like a prefix, use it
            import re
            match = re.match(r"^\^?([a-zA-Z0-9_-]+)", domain_plan.pattern)
            if match:
                prefix = match.group(1)
        if domain_plan and domain_plan.prefix:
            prefix = domain_plan.prefix

        min_len = 1
        max_len = spec.length or (type_profile.length if type_profile else None) or 12
        if domain_plan:
            if domain_plan.minimum_length is not None:
                min_len = domain_plan.minimum_length
            if domain_plan.maximum_length is not None:
                max_len = domain_plan.maximum_length
        suffix = domain_plan.suffix if domain_plan else None
        contains = list(domain_plan.contains) if domain_plan else []

        alphabet = string.ascii_lowercase + string.digits
        enforce_unique = spec.unique or spec.primary_key
        
        def _gen(unique_index: Optional[int] = None):
            if enforce_unique:
                candidate = self._unique_candidate(
                    spec.column,
                    unique_index or (len(state.used_values) + 1),
                    prefix,
                    suffix,
                    contains,
                    min_len,
                    max_len,
                )
                return candidate
            
            length = runtime.rng.randint(min_len, max_len)
            base = "".join(runtime.rng.choice(alphabet) for _ in range(length))
            return self._decorate(
                base, prefix, suffix, contains, min_len, max_len
            )

        value = _gen()
        if enforce_unique:
            counter = len(state.used_values) + 1
            while value in state.used_values:
                counter += 1
                value = _gen(unique_index=counter)
        return value

    def _unique_candidate(
        self,
        base_hint: str,
        index: int,
        prefix,
        suffix,
        contains,
        min_len: int,
        max_len: int,
    ) -> str:
        prefix = prefix or ""
        suffix = suffix or ""
        contains_text = "".join(contains)
        structural = prefix + contains_text + suffix
        structural_len = len(structural)
        if structural_len >= max_len:
            return structural[:max_len]

        counter_text = str(index)
        remaining = max_len - structural_len
        separator = "_" if remaining > len(counter_text) else ""
        stem_budget = max(0, remaining - len(counter_text) - len(separator))
        stem = (base_hint or "v")[:stem_budget]
        core = f"{stem}{separator}{counter_text}" if (stem or separator) else counter_text
        if len(core) < min_len - structural_len:
            padding = "x" * max(0, (min_len - structural_len) - len(core))
            core = f"{core}{padding}"
        return prefix + core[:remaining] + contains_text + suffix

    def _decorate(
        self, base: str, prefix, suffix, contains, min_len: int, max_len: int
    ) -> str:
        prefix = prefix or ""
        suffix = suffix or ""
        contains_text = "".join(contains)
        structural = prefix + contains_text + suffix
        structural_len = len(structural)
        if structural_len > max_len:
            return structural[:max_len]

        remaining = max_len - structural_len
        core = base[:remaining]
        min_core = max(0, min_len - structural_len)
        if len(core) < min_core:
            core = core + ("x" * (min_core - len(core)))
        return prefix + core + contains_text + suffix
