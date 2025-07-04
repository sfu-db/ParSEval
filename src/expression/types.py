"""Base type system definitions"""
from __future__ import annotations
from enum import auto
from .helper import AutoName
import datetime
from typing import TYPE_CHECKING, Union, List, Tuple

class Type(AutoName):
    ARRAY = auto()
    AGGREGATEFUNCTION = auto()
    SIMPLEAGGREGATEFUNCTION = auto()
    BIGDECIMAL = auto()
    BIGINT = auto()
    BIGSERIAL = auto()
    BINARY = auto()
    BIT = auto()
    BOOLEAN = auto()
    BPCHAR = auto()
    CHAR = auto()
    DATE = auto()
    DATE32 = auto()
    DATEMULTIRANGE = auto()
    DATERANGE = auto()
    DATETIME = auto()
    DATETIME64 = auto()
    DECIMAL = auto()
    DOUBLE = auto()
    ENUM = auto()
    ENUM8 = auto()
    ENUM16 = auto()
    FIXEDSTRING = auto()
    FLOAT = auto()
    GEOGRAPHY = auto()
    GEOMETRY = auto()
    HLLSKETCH = auto()
    HSTORE = auto()
    IMAGE = auto()
    INET = auto()
    INT = auto()
    INT128 = auto()
    INT256 = auto()
    INT4MULTIRANGE = auto()
    INT4RANGE = auto()
    INT8MULTIRANGE = auto()
    INT8RANGE = auto()
    INTERVAL = auto()
    IPADDRESS = auto()
    IPPREFIX = auto()
    IPV4 = auto()
    IPV6 = auto()
    JSON = auto()
    JSONB = auto()
    LONGBLOB = auto()
    LONGTEXT = auto()
    LOWCARDINALITY = auto()
    MAP = auto()
    MEDIUMBLOB = auto()
    MEDIUMINT = auto()
    MEDIUMTEXT = auto()
    MONEY = auto()
    NAME = auto()
    NCHAR = auto()
    NESTED = auto()
    NULL = auto()
    NULLABLE = auto()
    NUMMULTIRANGE = auto()
    NUMRANGE = auto()
    NVARCHAR = auto()
    OBJECT = auto()
    ROWVERSION = auto()
    SERIAL = auto()
    SET = auto()
    SMALLINT = auto()
    SMALLMONEY = auto()
    SMALLSERIAL = auto()
    STRUCT = auto()
    SUPER = auto()
    TEXT = auto()
    TINYBLOB = auto()
    TINYTEXT = auto()
    TIME = auto()
    TIMETZ = auto()
    TIMESTAMP = auto()
    TIMESTAMPLTZ = auto()
    TIMESTAMPTZ = auto()
    TIMESTAMP_S = auto()
    TIMESTAMP_MS = auto()
    TIMESTAMP_NS = auto()
    TINYINT = auto()
    TSMULTIRANGE = auto()
    TSRANGE = auto()
    TSTZMULTIRANGE = auto()
    TSTZRANGE = auto()
    UBIGINT = auto()
    UINT = auto()
    UINT128 = auto()
    UINT256 = auto()
    UMEDIUMINT = auto()
    UDECIMAL = auto()
    UNIQUEIDENTIFIER = auto()
    UNKNOWN = auto()  # Sentinel value, useful for type annotation
    USERDEFINED = "USER-DEFINED"
    USMALLINT = auto()
    UTINYINT = auto()
    UUID = auto()
    VARBINARY = auto()
    VARCHAR = auto()
    VARIANT = auto()
    XML = auto()
    YEAR = auto()

class DataType:
    def __init__(self, this, kind = None, **kwargs) -> None:
        self.this = this
        self.kind = kind
        for k, v in kwargs.items():
            setattr(self, k, v)

    STRUCT_TYPES = {
        Type.NESTED,
        Type.OBJECT,
        Type.STRUCT,
    }

    NESTED_TYPES = {
        *STRUCT_TYPES,
        Type.ARRAY,
        Type.MAP,
    }

    TEXT_TYPES = {
        Type.CHAR,
        Type.NCHAR,
        Type.NVARCHAR,
        Type.TEXT,
        Type.VARCHAR,
        Type.NAME,
    }

    SIGNED_INTEGER_TYPES = {
        Type.BIGINT,
        Type.INT,
        Type.INT128,
        Type.INT256,
        Type.MEDIUMINT,
        Type.SMALLINT,
        Type.TINYINT,
    }

    UNSIGNED_INTEGER_TYPES = {
        Type.UBIGINT,
        Type.UINT,
        Type.UINT128,
        Type.UINT256,
        Type.UMEDIUMINT,
        Type.USMALLINT,
        Type.UTINYINT,
    }

    INTEGER_TYPES = {
        *SIGNED_INTEGER_TYPES,
        *UNSIGNED_INTEGER_TYPES,
        Type.BIT,
    }

    FLOAT_TYPES = {
        Type.DOUBLE,
        Type.FLOAT,
    }

    REAL_TYPES = {
        *FLOAT_TYPES,
        Type.BIGDECIMAL,
        Type.DECIMAL,
        Type.MONEY,
        Type.SMALLMONEY,
        Type.UDECIMAL,
    }

    NUMERIC_TYPES = {
        *INTEGER_TYPES,
        *REAL_TYPES,
    }

    TEMPORAL_TYPES = {
        Type.DATE,
        Type.DATE32,
        Type.DATETIME,
        Type.DATETIME64,
        Type.TIME,
        Type.TIMESTAMP,
        Type.TIMESTAMPLTZ,
        Type.TIMESTAMPTZ,
        Type.TIMESTAMP_MS,
        Type.TIMESTAMP_NS,
        Type.TIMESTAMP_S,
        Type.TIMETZ,
    }

    @classmethod
    def build(
        cls,
        dtype: DATA_TYPE,
        copy: bool = True,
        udt: bool = False,
        **kwargs,
    ) -> DataType:
        """
        Constructs a DataType object.
        Args:
            dtype: the data type of interest.
            dialect: the dialect to use for parsing `dtype`, in case it's a string.
            copy: whether to copy the data type.
            kwargs: additional arguments to pass in the constructor of DataType.
        Returns:
            The constructed DataType object.
        """
        if isinstance(dtype, str):
            try:
                name = Type[dtype.upper()]
                return DataType(this = name, kind= dtype,  **kwargs)
            except KeyError:
                if udt:
                    return DataType(this=Type.USERDEFINED, kind=dtype, **kwargs)
                raise KeyError(f"Unknown type: {dtype}")
        elif isinstance(dtype, Type):
            return DataType(this=dtype)
        elif isinstance(dtype, DataType):
            return dtype


    def is_type(self, *dtypes: DATA_TYPE) -> bool:
        """
        Checks whether this DataType matches one of the provided data types. Nested types or precision
        will be compared using "structural equivalence" semantics, so e.g. array<int> != array<float>.

        Args:
            dtypes: the data types to compare this DataType to.

        Returns:
            True, if and only if there is a type in `dtypes` which is equal to this DataType.
        """
        for dtype in dtypes:
            other = DataType.build(dtype, copy=False, udt=True)
            if (
                self.this == Type.USERDEFINED
                or other.this == Type.USERDEFINED
            ):
                matches = self == other
            else:
                matches = self.this == other.this

            if matches:
                return True
        return False

    def __str__(self) -> str:
        return str(self.this)
DATA_TYPE = Union[str, DataType, Type]


SymbolLiterals = Union[str, float, int, bool, List, Tuple, datetime.date, datetime.datetime, None]


def can_coerce(from_type: DataType, to_type: DataType) -> bool:
    """Check if one type can be safely coerced to another"""
    if from_type == to_type:
        return True
    
    if from_type.is_type(*DataType.INTEGER_TYPES):
        return to_type.is_type(*DataType.NUMERIC_TYPES)
    
    if from_type.is_type(*DataType.NUMERIC_TYPES):
        return to_type.is_type(*DataType.TEXT_TYPES)
    
    return False
