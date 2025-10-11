from __future__ import annotations
from typing import Any, Optional, Union
from sqlglot import exp


class DataType:
    """Enhanced data type for ParSEval"""

    __slots__ = ("name", "precision", "scale", "length", "nullable", "default")

    def __init__(
        self,
        name: str,
        precision: Optional[int] = None,
        scale: Optional[int] = None,
        length: Optional[int] = None,
        nullable: Optional[bool] = None,
        default: Optional[Any] = None,
    ):
        self.name = name
        self.precision = precision
        self.scale = scale
        self.length = length
        self.nullable = nullable
        self.default = default

    @classmethod
    def build(
        cls,
        name: str,
        precision: Optional[int] = None,
        scale: Optional[int] = None,
        length: Optional[int] = None,
        nullable: Optional[bool] = None,
        default: Optional[Any] = None,
    ) -> "DataType":
        if isinstance(name, DataType):
            return name
        return cls(
            name=name,
            precision=precision,
            scale=scale,
            length=length,
            nullable=nullable,
            default=default,
        )

    @classmethod
    def infer(cls, value: Any) -> "DataType":
        """Infer data type from a Python value"""
        if isinstance(value, bool):
            return cls("BOOLEAN")
        elif isinstance(value, int):
            return cls("INT")
        elif isinstance(value, float):
            return cls("FLOAT")
        elif isinstance(value, str):
            return cls("TEXT", length=len(value))
        elif value is None:
            return cls("NULL")
        else:
            return cls("TEXT")

    def is_numeric(self) -> bool:
        return exp.DataType.build(self.name).is_type(*exp.DataType.NUMERIC_TYPES)

    def is_string(self) -> bool:
        return exp.DataType.build(self.name).is_type(*exp.DataType.TEXT_TYPES)

    def is_boolean(self) -> bool:
        """self.name.upper() == BOOLEAN"""
        return exp.DataType.build(self.name).is_type(exp.DataType.Type.BOOLEAN)

    def is_date_time(self) -> bool:
        """self.name.upper() in ["DATE", "TIME", "TIMESTAMP", "DATETIME"]"""
        return exp.DataType.build(self.name).is_type(exp.DataType.TEMPORAL_TYPES)

    def can_cast_to(self, target_type: "DataType") -> bool:
        """Check if this type can be cast to target type"""
        if self == target_type:
            return True

        # Numeric types can generally be cast to other numeric types
        if self.is_numeric() and target_type.is_numeric():
            return True

        # String types can be cast to most other types
        if self.is_string():
            return True

        # Most types can be cast to string
        if target_type.is_string():
            return True

        return False

    def __str__(self):
        return self.name


DATATYPE = Union[str, DataType]
