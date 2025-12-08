from __future__ import annotations
from sqlglot.expressions import Identifier, to_identifier
from typing import Dict
from src.parseval.dtype import DataType
from src.parseval.states import SyntaxException
from .rex import ColumnRef


def to_columnref(
    name: str | Identifier,
    datatype: str | DataType | Dict,
    index=None,
    **kwargs,
):
    """
    Converts a name and datatype into a ColumnRef object.

    Args:
        name (str | Identifier): The name of the column, either as a string or an Identifier object.
        datatype (str | DataType | Dict): The datatype of the column, which can be a string,
            a DataType object, or a dictionary defining the datatype.
        index (optional): The index or reference for the column. Defaults to None.
        **kwargs: Additional keyword arguments to pass to the ColumnRef constructor.

    Returns:
        ColumnRef: The constructed ColumnRef object.

    Raises:
        SyntaxException: If the datatype is invalid.
    """
    if isinstance(name, ColumnRef):
        return name
    name = to_identifier(name)
    datatype = to_type(datatype)
    return ColumnRef(this=name, ref=index, datatype=datatype, **kwargs)


def to_type(type_def: str | DataType | dict) -> DataType:
    """
    Converts a type definition into a DataType object.

    Args:
        type_def (str | DataType | dict): The type definition, which can be a string,
            a DataType object, or a dictionary defining the datatype.

    Returns:
        DataType: The constructed DataType object.

    Raises:
        SyntaxException: If the type definition is invalid.
    """
    if isinstance(type_def, (DataType, str)):
        return DataType.build(type_def)
    elif isinstance(type_def, dict):
        if "name" in type_def:
            type_def["dtype"] = type_def.pop("name")
    else:
        raise SyntaxException(f"Invalid type definition: {type_def}")
    return DataType.build(**type_def)
