from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..spec import ColumnSpec
from ..types import TypeService
from .base import ValueProvider
from .boolean_like import BooleanLikeTinyIntProvider
from .boolean import BooleanProvider
from .custom import ColumnOverrideProvider, SemanticProvider
from .enum import EnumProvider
from .numeric import IntegerProvider, RealProvider
from .string import StringProvider
from .temporal import DateProvider, DatetimeProvider, TimeProvider
from .uuid import UUIDProvider


@dataclass(frozen=True)
class ProviderMatch:
    score: int
    priority: int
    provider: ValueProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: List[ValueProvider] = []
        self._semantic_providers: Dict[str, ValueProvider] = {}
        self._column_providers: Dict[str, ValueProvider] = {}
        self.type_service = TypeService()

    @classmethod
    def with_builtin_providers(cls) -> "ProviderRegistry":
        registry = cls()
        registry.register(UUIDProvider())
        registry.register(EnumProvider())
        registry.register(BooleanLikeTinyIntProvider())
        registry.register(IntegerProvider())
        registry.register(RealProvider())
        registry.register(StringProvider())
        registry.register(BooleanProvider())
        registry.register(DateProvider())
        registry.register(DatetimeProvider())
        registry.register(TimeProvider())
        return registry

    def register(self, provider: ValueProvider) -> None:
        self._providers.append(provider)

    def register_semantic(self, tag: str, provider: ValueProvider) -> None:
        self._semantic_providers[tag.lower()] = provider

    def register_column(self, qualified_name: str, provider: ValueProvider) -> None:
        self._column_providers[qualified_name.lower()] = provider

    def resolve(self, spec: ColumnSpec) -> ValueProvider:
        type_profile = self.type_service.profile(spec)
        column_provider = self._column_providers.get(spec.qualified_name)
        if column_provider is not None:
            return column_provider

        semantic_provider = self._resolve_semantic(spec)
        if semantic_provider is not None:
            return semantic_provider

        candidates: List[ProviderMatch] = []
        for provider in self._providers:
            score = provider.supports(spec, type_profile)
            if score > 0:
                candidates.append(
                    ProviderMatch(score=score, priority=provider.priority, provider=provider)
                )
        if not candidates:
            raise ValueError(f"No provider registered for {spec.qualified_name}")
        candidates.sort(key=lambda item: (item.score, item.priority), reverse=True)
        return candidates[0].provider

    def _resolve_semantic(self, spec: ColumnSpec) -> Optional[ValueProvider]:
        matches: List[Tuple[int, ValueProvider]] = []
        for tag in spec.semantic_tags:
            provider = self._semantic_providers.get(tag.lower())
            if provider is not None:
                matches.append((provider.priority, provider))
        if not matches:
            return None
        matches.sort(key=lambda item: item[0], reverse=True)
        return matches[0][1]


__all__ = ["ProviderRegistry", "ProviderMatch"]
