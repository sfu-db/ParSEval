from __future__ import annotations
from typing import Any, Optional, Union
from sqlglot import exp


class DataType(exp.DataType):
    arg_types = {
        "this": True,
        "precision": False,
        "scale": False,
        "length": False,
        "nullable": False,
        "default": False,
    }

    @property
    def precision(self) -> Optional[int]:
        return self.args.get("precision")

    @property
    def scale(self) -> Optional[int]:
        return self.args.get("scale")

    @property
    def length(self) -> Optional[int]:
        return self.args.get("length")

    @property
    def nullable(self) -> Optional[bool]:
        return self.args.get("nullable")

    @nullable.setter
    def nullable(self, value: bool):
        self.set("nullable", value)

    @property
    def default(self) -> Optional[Any]:
        return self.args.get("default")

    @classmethod
    def infer(cls, value: Any) -> "DataType":
        """Infer data type from a Python value"""
        if value is None:
            return DataType.build("NULL")
        if isinstance(value, bool):
            return DataType.build("BOOLEAN")
        elif isinstance(value, int):
            return DataType.build("INT")
        elif isinstance(value, float):
            return DataType.build("FLOAT")
        elif isinstance(value, str):
            return DataType.build("TEXT", length=len(value))
        else:
            return DataType.build("TEXT")

    @classmethod
    def build(
        cls,
        dtype,
        dialect=None,
        udt: bool = False,
        copy: bool = True,
        **kwargs,
    ) -> DataType:
        """
        Constructs a DataType object.

        Args:
            dtype: the data type of interest.
            dialect: the dialect to use for parsing `dtype`, in case it's a string.
            udt: when set to True, `dtype` will be used as-is if it can't be parsed into a
                DataType, thus creating a user-defined type.
            copy: whether to copy the data type.
            kwargs: additional arguments to pass in the constructor of DataType.

        Returns:
            The constructed DataType object.
        """
        from sqlglot import parse_one

        if isinstance(dtype, str):
            if dtype.upper() == "UNKNOWN":
                return DataType(this=DataType.Type.UNKNOWN, **kwargs)
            t = parse_one(dtype, into=exp.DataType)
            return DataType(this=t.this)
        elif isinstance(dtype, DataType.Type):
            data_type_exp = DataType(this=dtype)
        elif isinstance(dtype, DataType):
            return dtype
        else:
            raise ValueError(
                f"Invalid data type: {type(dtype)}. Expected str or DataType.Type"
            )

        return DataType(**{**data_type_exp.args, **kwargs})


# class DataType:
#     """Enhanced data type for ParSEval"""

#     __slots__ = ("name", "precision", "scale", "length", "nullable", "default")

#     def __init__(
#         self,
#         name: str,
#         precision: Optional[int] = None,
#         scale: Optional[int] = None,
#         length: Optional[int] = None,
#         nullable: Optional[bool] = None,
#         default: Optional[Any] = None,
#     ):
#         self.name = name
#         self.precision = precision
#         self.scale = scale
#         self.length = length
#         self.nullable = nullable
#         self.default = default

#     @classmethod
#     def build(
#         cls,
#         name: str,
#         precision: Optional[int] = None,
#         scale: Optional[int] = None,
#         length: Optional[int] = None,
#         nullable: Optional[bool] = None,
#         default: Optional[Any] = None,
#     ) -> "DataType":
#         if isinstance(name, DataType):
#             return name
#         return cls(
#             name=name,
#             precision=precision,
#             scale=scale,
#             length=length,
#             nullable=nullable,
#             default=default,
#         )

#     @classmethod
#     def infer(cls, value: Any) -> "DataType":
#         """Infer data type from a Python value"""
#         if value is None:
#             return cls("NULL")
#         if isinstance(value, bool):
#             return cls("BOOLEAN")
#         elif isinstance(value, int):
#             return cls("INT")
#         elif isinstance(value, float):
#             return cls("FLOAT")
#         elif isinstance(value, str):
#             return cls("TEXT", length=len(value))
#         elif value is None:
#             return cls("NULL")
#         else:
#             return cls("TEXT")

#     def is_integer(self) -> bool:
#         return exp.DataType.build(self.name).is_type(*exp.DataType.INTEGER_TYPES)

#     def is_numeric(self) -> bool:
#         return exp.DataType.build(self.name).is_type(*exp.DataType.NUMERIC_TYPES)

#     def is_string(self) -> bool:
#         return exp.DataType.build(self.name).is_type(*exp.DataType.TEXT_TYPES)

#     def is_boolean(self) -> bool:
#         """self.name.upper() == BOOLEAN"""
#         return exp.DataType.build(self.name).is_type(exp.DataType.Type.BOOLEAN)

#     def is_datetime(self) -> bool:
#         """self.name.upper() in ["DATE", "TIME", "TIMESTAMP", "DATETIME"]"""
#         return exp.DataType.build(self.name).is_type(*exp.DataType.TEMPORAL_TYPES)

#     def can_cast_to(self, target_type: "DataType") -> bool:
#         """Check if this type can be cast to target type"""
#         if self == target_type:
#             return True

#         # Numeric types can generally be cast to other numeric types
#         if self.is_numeric() and target_type.is_numeric():
#             return True

#         # String types can be cast to most other types
#         if self.is_string():
#             return True

#         # Most types can be cast to string
#         if target_type.is_string():
#             return True

#         return False

#     def __str__(self):
#         return self.name


DATATYPE = Union[str, DataType]
