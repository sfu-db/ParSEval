from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Optional, Union
from sqlglot.expressions import DataType as sqlglot_datatype

from enum import Enum

def precision(self) -> Optional[int]:
    """
    Get the precision of the data type.

    Precision is typically used for numeric types like DECIMAL to specify
    the total number of digits.

    Returns:
        Optional[int]: The precision of the data type, or None if not specified.
    """
    return self.args.get("precision")

def scale(self) -> Optional[int]:
    """
    Get the scale of the data type.

    Scale is typically used for numeric types like DECIMAL to specify
    the number of digits after the decimal point.

    Returns:
        Optional[int]: The scale of the data type, or None if not specified.
    """
    return self.args.get("scale")

def length(self) -> Optional[int]:
    """
    Get the length of the data type.

    Length is typically used for string types like VARCHAR to specify
    the maximum number of characters.

    Returns:
        Optional[int]: The length of the data type, or None if not specified.
    """
    return self.args.get("length")

def nullable(self) -> Optional[bool]:
    """
    Get whether the data type allows NULL values.

    Returns:
        Optional[bool]: True if the data type is nullable, False if not,
        or None if not specified.
    """
    return self.args.get("nullable")


def default(self) -> Optional[Any]:
    return self.args.get("default")

setattr(sqlglot_datatype, "precision", property(precision))
setattr(sqlglot_datatype, "scale", property(scale))
setattr(sqlglot_datatype, "length", property(length))
setattr(sqlglot_datatype, "nullable", property(nullable))
setattr(sqlglot_datatype, "default", property(default))

DataType = sqlglot_datatype


class TypeFamily(str, Enum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"
    UUID = "uuid"
    JSON = "json"
    BINARY = "binary"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TypeProfile:
    """A normalized snapshot of SQL datatype characteristics."""

    datatype: DataType
    dialect: Optional[str]
    family: TypeFamily
    exact_type: str
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    unsigned: Optional[bool] = None
    timezone: Optional[bool] = None
    metadata: dict[str, Any] = field(default_factory=dict)


class TypeService:
    """Resolve and cache dialect-aware type profiles."""

    def __init__(self, registry=None) -> None:
        if registry is None:
            from parseval.domain.adapters.registry import TypeAdapterRegistry

            registry = TypeAdapterRegistry.with_builtin_adapters()
        self.registry = registry
        self._profile_cache: dict[tuple[str, Optional[str]], TypeProfile] = {}

    def profile(self, column_spec) -> TypeProfile:
        """Resolve the TypeProfile for a ColumnSpec, caching the result."""
        datatype = DataType.build(column_spec.datatype)
        dialect = getattr(column_spec, "dialect", None)
        key = (datatype.sql(dialect=dialect), dialect)
        if key not in self._profile_cache:
            adapter = self.registry.resolve(datatype, dialect)
            self._profile_cache[key] = adapter.profile(datatype, dialect)
        return self._profile_cache[key]

    def profile_datatype(
        self, datatype: DataType, dialect: Optional[str] = None
    ) -> TypeProfile:
        """Resolve the TypeProfile for a raw DataType, caching the result."""
        datatype = DataType.build(datatype)
        key = (datatype.sql(dialect=dialect), dialect)
        if key not in self._profile_cache:
            adapter = self.registry.resolve(datatype, dialect)
            self._profile_cache[key] = adapter.profile(datatype, dialect)
        return self._profile_cache[key]

class StorageLiteral(str):
    """String value that should be stored without type coercion."""

def type_family(dtype: DataType) -> TypeFamily:
    """Map a SQL DataType to a neutral ParSEval type family."""
    dtype = DataType.build(dtype)
    if dtype.is_type(DataType.Type.UUID):
        return TypeFamily.UUID
    if dtype.is_type(DataType.Type.JSON):
        return TypeFamily.JSON
    if dtype.is_type(DataType.Type.BINARY, DataType.Type.VARBINARY):
        return TypeFamily.BINARY
    if dtype.is_type(*DataType.INTEGER_TYPES):
        return TypeFamily.INTEGER
    if dtype.is_type(*DataType.REAL_TYPES):
        return TypeFamily.DECIMAL
    if dtype.is_type(DataType.Type.BOOLEAN):
        return TypeFamily.BOOLEAN
    if dtype.is_type(
        DataType.Type.DATETIME,
        DataType.Type.DATETIME64,
        DataType.Type.TIMESTAMP,
        DataType.Type.TIMESTAMPLTZ,
        DataType.Type.TIMESTAMPTZ,
        DataType.Type.TIMESTAMP_MS,
        DataType.Type.TIMESTAMP_NS,
        DataType.Type.TIMESTAMP_S,
    ):
        return TypeFamily.DATETIME
    if dtype.is_type(DataType.Type.DATE, DataType.Type.DATE32):
        return TypeFamily.DATE
    if dtype.is_type(DataType.Type.TIME, DataType.Type.TIMETZ):
        return TypeFamily.TIME
    if dtype.is_type(*DataType.TEXT_TYPES):
        return TypeFamily.TEXT
    if dtype.is_type(DataType.Type.UNKNOWN):
        return TypeFamily.UNKNOWN
    return TypeFamily.TEXT


def parse_date(value: Any) -> Optional[date]:
    """Parse a value into a date, or None if unparseable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                return datetime.fromisoformat(value.replace(" ", "T")).date()
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def parse_time(value: Any) -> Optional[dt_time]:
    """Parse a value into a time, or None if unparseable."""
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, dt_time):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                return datetime.fromisoformat(value.replace(" ", "T")).time().replace(
                    microsecond=0
                )
            return dt_time.fromisoformat(value[:8])
        except ValueError:
            return None
    return None


def parse_datetime(value: Any) -> Optional[datetime]:
    """Parse a value into a datetime, or None if unparseable."""
    if isinstance(value, datetime):
        return _normalize_datetime(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        # Strip single-digit fractional seconds (.0, .1, etc.) that Python 3.10
        # rejects.  Multi-digit fractions (.000) are valid ISO-8601.
        if re.search(r"\.\d{1}$", normalized):
            normalized = normalized[:-2]
        for candidate in (normalized.replace(" ", "T"), normalized):
            try:
                return _normalize_datetime(datetime.fromisoformat(candidate))
            except ValueError:
                continue
    return None


def _normalize_datetime(value: datetime) -> datetime:
    value = value.replace(microsecond=0)
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def date_to_epoch_day(value: Any) -> int:
    """Convert a date/datetime/string to days since Unix epoch."""
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"Cannot parse as date: {value!r}")
    return (parsed - date(1970, 1, 1)).days


def time_to_seconds(value: Any) -> int:
    """Convert a time/datetime/string to seconds since midnight."""
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError(f"Cannot parse as time: {value!r}")
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def datetime_to_epoch_second(value: Any) -> int:
    """Convert a datetime/date/string to Unix epoch seconds."""
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Cannot parse as datetime: {value!r}")
    return int((parsed - datetime(1970, 1, 1)).total_seconds())


def epoch_day_to_date(days: int) -> date:
    """Convert days since Unix epoch to a date."""
    return date(1970, 1, 1) + timedelta(days=days)


def seconds_to_time(seconds: int) -> dt_time:
    """Convert seconds since midnight to a time."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return dt_time(h, m, s)


def epoch_second_to_datetime(value: int) -> datetime:
    """Convert Unix epoch seconds to a timezone-naive datetime."""
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


def infer_type_from_value(value: Any) -> DataType:
    """Infer a SQL DataType from a Python value's runtime type."""
    if value is None:
        return DataType.build("NULL")
    if isinstance(value, bool):
        return DataType.build("BOOLEAN")
    if isinstance(value, int):
        return DataType.build("INT")
    if isinstance(value, float):
        return DataType.build("FLOAT")
    if isinstance(value, str):
        return DataType.build("TEXT", length=len(value))
    if isinstance(value, dt_time):
        return DataType.build("TIME")
    if isinstance(value, datetime):
        return DataType.build("DATETIME")
    if isinstance(value, date):
        return DataType.build("DATE")
    return DataType.build("TEXT")


def infer_type_from_string(value: str) -> Any:
    """Try to parse a string as a typed Python value."""
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    has_time = ":" in value or (" " in value and len(value) > 10)
    if not has_time:
        parsed = parse_date(value)
        if parsed is not None and not isinstance(parsed, datetime):
            return parsed
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    parsed = parse_time(value)
    if parsed is not None:
        return parsed
    return value


_NUMERIC_LITERAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")


def infer_semantic_datatype_from_literal(value: Any) -> DataType | None:
    """Infer semantic SQL type from a string literal used in a predicate."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if _NUMERIC_LITERAL_RE.match(text):
        return DataType.build("REAL") if _numeric_literal_is_decimal(text) else DataType.build("INT")
    temporal = _temporal_literal_semantic_datatype(text)
    if temporal is not None:
        return temporal
    return None


def merge_semantic_datatypes(datatypes: tuple[DataType, ...]) -> DataType | None:
    """Merge compatible semantic datatype hints into one widened datatype."""
    if not datatypes:
        return None
    families = {type_family(DataType.build(datatype)) for datatype in datatypes}
    if families <= {TypeFamily.INTEGER}:
        return DataType.build("INT")
    if families <= {TypeFamily.INTEGER, TypeFamily.DECIMAL}:
        return DataType.build("REAL")
    if families <= {TypeFamily.DATE}:
        return DataType.build("DATE")
    if families <= {TypeFamily.DATE, TypeFamily.DATETIME}:
        return DataType.build("DATETIME")
    return None


def semantic_cast_datatype(datatype: DataType) -> DataType | None:
    """Return the normalized cast target used for semantic literal casts."""
    family = type_family(DataType.build(datatype))
    if family == TypeFamily.INTEGER:
        return DataType.build("INT")
    if family == TypeFamily.DECIMAL:
        return DataType.build("REAL")
    if family == TypeFamily.DATE:
        return DataType.build("DATE")
    if family == TypeFamily.DATETIME:
        return DataType.build("DATETIME")
    return None


def _numeric_literal_is_decimal(value: str) -> bool:
    return "." in value or "e" in value.lower()


def _temporal_literal_semantic_datatype(value: str) -> DataType | None:
    parsed = parse_datetime(value)
    if parsed is None:
        return None
    return (
        DataType.build("DATETIME")
        if _literal_has_time_component(value)
        else DataType.build("DATE")
    )


def _literal_has_time_component(value: str) -> bool:
    return bool(re.search(r"(?:\d{1,2}:\d{2}|[Tt]\d{1,2})", value))

# # dd = exp.DataType.build("INT", dialect= 'sqlite', precision=10)

# # print(dd.precision)



# class DataType(sqlglot_datatype):
#     """
#     Represents a data type in the logical plan, extending the `DataType` class from `sqlglot`.

#     This class provides additional properties and methods to handle attributes such as
#     precision, scale, length, nullability, and default values for the data type.

#     Attributes:
#         arg_types (dict): A dictionary defining the argument types for the data type.
#             - "this": The main data type (e.g., INT, VARCHAR).
#             - "precision": The precision of the data type (e.g., for DECIMAL types).
#             - "scale": The scale of the data type (e.g., for DECIMAL types).
#             - "length": The length of the data type (e.g., for VARCHAR types).
#             - "nullable": Whether the data type allows NULL values.
#             - "default": The default value for the data type.
#     """

#     arg_types = {
#         "this": True,
#         "precision": False,
#         "scale": False,
#         "length": False,
#         "nullable": False,
#         "default": False,
#     }

#     @property
#     def precision(self) -> Optional[int]:
#         """
#         Get the precision of the data type.

#         Precision is typically used for numeric types like DECIMAL to specify
#         the total number of digits.

#         Returns:
#             Optional[int]: The precision of the data type, or None if not specified.
#         """
#         return self.args.get("precision")

#     @property
#     def scale(self) -> Optional[int]:
#         """
#         Get the scale of the data type.

#         Scale is typically used for numeric types like DECIMAL to specify
#         the number of digits after the decimal point.

#         Returns:
#             Optional[int]: The scale of the data type, or None if not specified.
#         """
#         return self.args.get("scale")

#     @property
#     def length(self) -> Optional[int]:
#         """
#         Get the length of the data type.

#         Length is typically used for string types like VARCHAR to specify
#         the maximum number of characters.

#         Returns:
#             Optional[int]: The length of the data type, or None if not specified.
#         """
#         return self.args.get("length")

#     @property
#     def nullable(self) -> Optional[bool]:
#         """
#         Get whether the data type allows NULL values.

#         Returns:
#             Optional[bool]: True if the data type is nullable, False if not,
#             or None if not specified.
#         """
#         return self.args.get("nullable")

#     @property
#     def default(self) -> Optional[Any]:
#         return self.args.get("default")

#     @classmethod
#     def infer(cls, value: Any) -> "DataType":
#         """Infer data type from a Python value"""
#         if value is None:
#             return DataType.build("NULL")
#         if isinstance(value, bool):
#             return DataType.build("BOOLEAN")
#         elif isinstance(value, int):
#             return DataType.build("INT")
#         elif isinstance(value, float):
#             return DataType.build("FLOAT")
#         elif isinstance(value, str):
#             return DataType.build("TEXT", length=len(value))
#         else:
#             return DataType.build("TEXT")

#     @classmethod
#     def build(
#         cls,
#         dtype,
#         dialect=None,
#         udt: bool = False,
#         copy: bool = True,
#         **kwargs,
#     ) -> DataType:
#         """
#         Constructs a DataType object.

#         Args:
#             dtype: the data type of interest.
#             dialect: the dialect to use for parsing `dtype`, in case it's a string.
#             udt: when set to True, `dtype` will be used as-is if it can't be parsed into a
#                 DataType, thus creating a user-defined type.
#             copy: whether to copy the data type.
#             kwargs: additional arguments to pass in the constructor of DataType.

#         Returns:
#             The constructed DataType object.
#         """
#         from sqlglot import parse_one

#         if isinstance(dtype, str):
#             if dtype.upper() == "UNKNOWN":
#                 return DataType(this=DataType.Type.UNKNOWN, **kwargs)
#             t = parse_one(dtype, into=sqlglot_datatype, dialect=dialect)
#             return DataType(**{**t.args, **kwargs})
#         elif isinstance(dtype, DataType.Type):
#             data_type_exp = DataType(this=dtype)
#         elif isinstance(dtype, DataType):
#             return dtype
#         else:
#             raise ValueError(
#                 f"Invalid data type: {type(dtype)}. Expected str or DataType.Type"
#             )

#         return DataType(**{**data_type_exp.args, **kwargs})


DATATYPE = Union[str, "DataType"]
