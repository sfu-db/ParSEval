from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from parseval.dtype import DataType, TypeProfile


class TypeAdapter(ABC):
    priority = 0

    @abstractmethod
    def supports(self, datatype: DataType, dialect: Optional[str]) -> int:
        pass

    @abstractmethod
    def profile(self, datatype: DataType, dialect: Optional[str]) -> TypeProfile:
        pass

    @abstractmethod
    def coerce_in(self, value: Any, profile: TypeProfile) -> Any:
        pass

    def coerce_out(self, value: Any, profile: TypeProfile) -> Any:
        return self.coerce_in(value, profile)

    def storage_key(self, value: Any, profile: TypeProfile) -> Any:
        return self.coerce_in(value, profile)

    def validate_storage_value(self, value: Any, profile: TypeProfile) -> None:
        self.coerce_in(value, profile)

    def equivalent(
        self,
        left: Any,
        left_profile: TypeProfile,
        right: Any,
        right_profile: TypeProfile,
    ) -> bool:
        return left == right
