from __future__ import annotations
from typing import Callable, List, TypeVar, Dict, TYPE_CHECKING, Tuple
import re
from sqlglot.expressions import convert, Expression, maybe_copy, Literal
from .dtype import DataType

if TYPE_CHECKING:
    from parseval.plan.rex import Symbol


def normalize_name(name) -> str:
    pattern = r"[^a-zA-Z0-9_]"
    cleaned_str = re.sub(pattern, "", name)
    return cleaned_str.lower()


def like_to_pattern(pattern: str) -> re.Pattern:
    """
    Convert SQL LIKE pattern to regex pattern.
    """
    regex = ""
    for ch in pattern:
        if ch == "%":
            regex += ".*"
        elif ch == "_":
            regex += "."
        else:
            regex += re.escape(ch)
    return re.compile(f"^{regex}$")


def group_by_concrete(
    items: List[Symbol],
    key_func: Callable = lambda x: x.concrete,
    ignore_null: bool = True,
) -> Dict:
    groups = {}
    for item in items:
        key = key_func(item)
        if ignore_null and key is None:
            continue
        groups.setdefault(key, []).append(item)
    return groups


def sort_by_concrete(
    items: List[Symbol],
    key_func: Callable = lambda x: x.concrete,
    reverse: bool = False,
    null_first: bool = False,
) -> List[Symbol]:
    null_values = [item for item in items if item.concrete is None]
    values = sorted(
        [item for item in items if item.concrete is not None],
        key=key_func,
        reverse=reverse,
    )
    return null_values + values if null_first else values + null_values


import math, numbers, datetime


def convert_to_literal(value, datatype=None, copy=False) -> Symbol:
    converted = None
    srctype = None
    if isinstance(value, Expression):
        converted = maybe_copy(value, copy)
        srctype = converted.args.get("datatype")
    elif isinstance(value, str):
        converted = Literal(this=value, is_string=True)
        srctype = "TEXT"
    elif isinstance(value, bool):
        converted = Literal(this=value, is_string=False)
        srctype = "BOOLEAN"
    elif value is None or (isinstance(value, float) and math.isnan(value)):
        converted = Literal(this=None, is_string=False)
    elif isinstance(value, numbers.Number):
        converted = Literal.number(value)
        srctype = "NUMERIC"
    elif isinstance(value, datetime.datetime):
        converted = Literal(this=value, is_string=False)
        srctype = "DATETIME"

    elif isinstance(value, datetime.date):
        converted = Literal(this=value, is_string=False)
        srctype = "DATE"
    else:
        raise ValueError(f"Unsupported literal type: {type(value)}")
    if datatype:
        converted.type = datatype
        converted.set("datatype", datatype)
    else:
        converted.type = DataType.build(srctype)
        converted.set("datatype", DataType.build(srctype))
    return converted


from sqlglot import exp

from datetime import date, datetime, time, timedelta

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")
_TIME_RE = re.compile(r"^\d{2}:\d{2}:\d{2}")
_ONE_DAY = timedelta(days=1)
_ONE_SEC = timedelta(seconds=1)
_ONE_HOUR = timedelta(hours=1)


def _parse_temporal_string(s: str) -> date | datetime | time | str:
    s = s.strip()
    if _DATETIME_RE.match(s):
        s_clean = re.sub(r"[+-]\d{2}:\d{2}$", "", s).replace("T", " ")
        try:
            return datetime.strptime(s_clean[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    if _DATE_RE.match(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            pass
    if _TIME_RE.match(s):
        try:
            return datetime.strptime(s[:8], "%H:%M:%S").time()
        except ValueError:
            pass
    return s


def to_concrete(value, datatype=None):
    datatype = type(value) if datatype is None else datatype
    datatype = DataType.build(datatype)

    if isinstance(value, Expression):
        return to_concrete(value.this, datatype=datatype)
    if value is None or isinstance(value, exp.Null):
        return None
    if datatype.is_type(*DataType.TEXT_TYPES):
        return str(value)
    elif datatype.is_type(*DataType.INTEGER_TYPES):
        return int(value)
    elif datatype.is_type(*DataType.REAL_TYPES):
        return float(value)
    elif datatype.is_type(DataType.Type.BOOLEAN):
        return bool(value)
    elif datatype.is_type(*DataType.TEMPORAL_TYPES):
        return _parse_temporal_string(str(value))
        return value
    return value


limit_pattern = re.compile(
    r"LIMIT\s+\d+\b(?:\s+OFFSET\s+\d+)?$", re.IGNORECASE
)  # LIMIT\s+(\d+)(?:\s*,\s*(\d+))?\s*$
orderby_pattern = re.compile(
    r"ORDER\s+BY\s+.*[^\)]$", re.IGNORECASE
)  ##ORDER\s+BY\s+([^,\s]+)\s*(ASC|DESC)?\s*$


def remove_limit(gold, pred):
    gold_limit_match = limit_pattern.search(gold)
    pred_limit_match = limit_pattern.search(pred)
    gold_limit = gold_limit_match.group(0) if gold_limit_match else None
    pred_limit = pred_limit_match.group(0) if pred_limit_match else None
    if gold_limit == pred_limit:
        query1 = re.sub(limit_pattern, "", gold)
        query2 = re.sub(limit_pattern, "", pred)
        return query1, query2
    return gold, pred


def compare_df(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    if not result1 and not result2:
        return -1
    sentinel = -99999
    result1_filled = [[sentinel if v is None else v for v in row] for row in result1]
    result2_filled = [[sentinel if v is None else v for v in row] for row in result2]

    # Check shape (number of rows and columns)
    if len(result1_filled) != len(result2_filled):
        return 0
    if len(result1_filled) > 0 and len(result1_filled[0]) != len(result2_filled[0]):
        return 0
    return set([tuple(a) for a in result1_filled]) == set(
        [tuple(b) for b in result2_filled]
    )

    if not order_matters:
        result1_filled = sorted(result1_filled)
        result2_filled = sorted(result2_filled)

    # Compare element-wise
    for r1, r2 in zip(result1_filled, result2_filled):
        if r1 != r2:
            return 0

    return 1
