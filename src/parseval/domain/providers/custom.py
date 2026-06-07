from __future__ import annotations

from typing import Any, Callable, Optional

from parseval.dtype import TypeProfile
from parseval.identity import ColumnId
from ..compiler import ColumnDomainPlan
from ..spec import ColumnSpec
from .base import ValueProvider


class SemanticProvider(ValueProvider):
    def __init__(self, tag: str, generator: Callable[..., Any], priority: int = 100) -> None:
        self.tag = tag.lower()
        self.generator = generator
        self.priority = priority

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 100 if self.tag in spec.semantic_tags else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        return self.generator(
            spec=spec, runtime=runtime, row_context=row_context, null_rate=null_rate
        )


class ColumnOverrideProvider(ValueProvider):
    def __init__(
        self,
        column: ColumnId | ColumnSpec,
        generator: Callable[..., Any],
        priority: int = 100,
    ) -> None:
        self.column_id = column.id if isinstance(column, ColumnSpec) else column
        self.generator = generator
        self.priority = priority

    def supports(self, spec, type_profile: TypeProfile) -> int:
        return 100 if spec.id == self.column_id else 0

    def generate(
        self,
        spec,
        runtime,
        row_context,
        domain_plan: Optional[ColumnDomainPlan] = None,
        type_profile: Optional[TypeProfile] = None,
        null_rate: float = 0.0,
    ) -> Any:
        return self.generator(
            spec=spec, runtime=runtime, row_context=row_context, null_rate=null_rate
        )
