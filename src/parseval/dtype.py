from __future__ import annotations
from typing import Any, Optional, Union
from sqlglot.expressions import DataType as sqlglot_datatype


class DataType(sqlglot_datatype):
    """
    Represents a data type in the logical plan, extending the `DataType` class from `sqlglot`.

    This class provides additional properties and methods to handle attributes such as
    precision, scale, length, nullability, and default values for the data type.

    Attributes:
        arg_types (dict): A dictionary defining the argument types for the data type.
            - "this": The main data type (e.g., INT, VARCHAR).
            - "precision": The precision of the data type (e.g., for DECIMAL types).
            - "scale": The scale of the data type (e.g., for DECIMAL types).
            - "length": The length of the data type (e.g., for VARCHAR types).
            - "nullable": Whether the data type allows NULL values.
            - "default": The default value for the data type.
    """

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
        """
        Get the precision of the data type.

        Precision is typically used for numeric types like DECIMAL to specify
        the total number of digits.

        Returns:
            Optional[int]: The precision of the data type, or None if not specified.
        """
        return self.args.get("precision")

    @property
    def scale(self) -> Optional[int]:
        """
        Get the scale of the data type.

        Scale is typically used for numeric types like DECIMAL to specify
        the number of digits after the decimal point.

        Returns:
            Optional[int]: The scale of the data type, or None if not specified.
        """
        return self.args.get("scale")

    @property
    def length(self) -> Optional[int]:
        """
        Get the length of the data type.

        Length is typically used for string types like VARCHAR to specify
        the maximum number of characters.

        Returns:
            Optional[int]: The length of the data type, or None if not specified.
        """
        return self.args.get("length")

    @property
    def nullable(self) -> Optional[bool]:
        """
        Get whether the data type allows NULL values.

        Returns:
            Optional[bool]: True if the data type is nullable, False if not,
            or None if not specified.
        """
        return self.args.get("nullable")

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
            t = parse_one(dtype, into=sqlglot_datatype, dialect=dialect)
            return DataType(**{**t.args, **kwargs})
        elif isinstance(dtype, DataType.Type):
            data_type_exp = DataType(this=dtype)
        elif isinstance(dtype, DataType):
            return dtype
        else:
            raise ValueError(
                f"Invalid data type: {type(dtype)}. Expected str or DataType.Type"
            )

        return DataType(**{**data_type_exp.args, **kwargs})


DATATYPE = Union[str, "DataType"]
