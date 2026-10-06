"""Type rules and concrete execution shared by methods and Term replay."""

import operator
import re
from datetime import date, datetime, time

from parseval.terms.sorts import (
    BOOLEAN,
    FLOAT,
    INTEGER,
    INTERVAL,
    IntervalValue,
    STRING,
    TIMESTAMP,
    ScalarSort,
    ScalarType,
    TypeKind,
    parse_iso_temporal_value,
    shift_temporal,
)

NUMERIC = frozenset((TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL))
TEMPORAL = frozenset((TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP))
ARITHMETIC = {"add", "sub", "mul", "div", "mod"}
COMPARISONS = {"eq", "ne", "lt", "le", "gt", "ge", "is_not_distinct"}
OPERATIONS = (
    ARITHMETIC
    | COMPARISONS
    | {
        "neg",
        "abs",
        "and",
        "or",
        "not",
        "is_null",
        "is_not_null",
        "is_true",
        "is_false",
        "is_unknown",
        "lower",
        "upper",
        "length",
        "substring",
        "like",
        "ilike",
        "contains",
        "startswith",
        "endswith",
        "concat",
    }
)


def promoted_type(left, right):
    a, b = left.kind, right.kind
    if a not in NUMERIC or b not in NUMERIC:
        if left.value_type != right.value_type:
            raise TypeError(f"Incompatible scalar types: {a.value}, {b.value}")
        return left.value_type
    if TypeKind.FLOAT in (a, b):
        return FLOAT
    if TypeKind.DECIMAL in (a, b):
        return ScalarType(TypeKind.DECIMAL)
    return INTEGER


def result_sort(op, sorts):
    nullable = any(sort.nullable for sort in sorts)
    kinds = tuple(sort.sql_type.kind for sort in sorts)
    if op in ARITHMETIC:
        if len(sorts) != 2:
            raise TypeError(f"{op} requires two operands")
        if all(kind in NUMERIC for kind in kinds):
            return ScalarSort(
                promoted_type(*(sort.sql_type for sort in sorts)), nullable
            )
        if op == "add" and kinds == (TypeKind.STRING, TypeKind.STRING):
            return ScalarSort(STRING, nullable)
        if op in {"add", "sub"} and kinds == (TypeKind.INTERVAL, TypeKind.INTERVAL):
            return ScalarSort(INTERVAL, nullable)
        if (
            op in {"add", "sub"}
            and kinds[0] in {TypeKind.DATE, TypeKind.TIMESTAMP}
            and kinds[1] is TypeKind.INTERVAL
        ):
            return ScalarSort(TIMESTAMP, nullable)
        if op == "sub" and kinds == (TypeKind.DATE, TypeKind.DATE):
            return ScalarSort(INTEGER, nullable)
        raise TypeError(f"{op} is not defined for {kinds}")
    if op in COMPARISONS:
        if len(sorts) != 2:
            raise TypeError("Comparison requires two operands")
        promoted_type(*(sort.sql_type for sort in sorts))
        return ScalarSort(BOOLEAN, nullable and op != "is_not_distinct")
    if op in {"neg", "abs"}:
        if len(sorts) != 1 or kinds[0] not in NUMERIC:
            if op == "neg" and kinds == (TypeKind.INTERVAL,):
                return sorts[0]
            raise TypeError(f"{op} requires a numeric operand")
        return ScalarSort(sorts[0].sql_type.value_type, nullable)
    if op in {"and", "or", "not"}:
        if len(sorts) != (1 if op == "not" else 2) or any(
            kind is not TypeKind.BOOLEAN for kind in kinds
        ):
            raise TypeError(f"{op} requires Boolean operands")
        return ScalarSort(BOOLEAN, nullable)
    if op in {"is_null", "is_not_null", "is_true", "is_false", "is_unknown"}:
        if len(sorts) != 1:
            raise TypeError(f"{op} requires one operand")
        if op not in {"is_null", "is_not_null"} and kinds[0] is not TypeKind.BOOLEAN:
            raise TypeError(f"{op} requires a Boolean")
        return ScalarSort(BOOLEAN)
    if op in {"lower", "upper", "length"}:
        if kinds != (TypeKind.STRING,):
            raise TypeError(f"{op} requires a string")
        return ScalarSort(INTEGER if op == "length" else STRING, nullable)
    if op == "substring":
        if kinds not in (
            (TypeKind.STRING, TypeKind.INTEGER),
            (TypeKind.STRING, TypeKind.INTEGER, TypeKind.INTEGER),
        ):
            raise TypeError(
                "substring requires text, integer start, and optional length"
            )
        return ScalarSort(STRING, nullable)
    if op in {"like", "ilike", "contains", "startswith", "endswith"}:
        if kinds != (TypeKind.STRING, TypeKind.STRING):
            raise TypeError(f"{op} requires two strings")
        return ScalarSort(BOOLEAN, nullable)
    if op == "concat":
        if kinds != (TypeKind.STRING, TypeKind.STRING):
            raise TypeError("concat requires strings")
        return ScalarSort(STRING, nullable)
    raise NotImplementedError(f"Unsupported concolic operation: {op}")


_NUMERIC_PREFIX = re.compile(r"\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")


def _timestamp(value):
    return value if isinstance(value, datetime) else datetime.combine(value, time())


def _truncate_division(a, b):
    if b == 0:
        raise ZeroDivisionError("Integer division by zero")
    quotient = abs(a) // abs(b)
    return -quotient if (a < 0) != (b < 0) else quotient


def like(value, pattern, insensitive=False):
    pieces = []
    escaped = False
    for char in pattern:
        if escaped:
            pieces.append(re.escape(char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "%":
            pieces.append(".*")
        elif char == "_":
            pieces.append(".")
        else:
            pieces.append(re.escape(char))
    if escaped:
        raise ValueError("LIKE pattern ends with an escape")
    flags = re.DOTALL | (re.IGNORECASE | re.ASCII if insensitive else 0)
    return re.fullmatch("".join(pieces), value, flags) is not None


_OPERATORS = {
    "add": operator.add,
    "sub": operator.sub,
    "mul": operator.mul,
    "div": operator.truediv,
    "mod": operator.mod,
    "neg": operator.neg,
    "abs": abs,
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "le": operator.le,
    "gt": operator.gt,
    "ge": operator.ge,
    "not": operator.not_,
    "concat": operator.add,
    "lower": str.lower,
    "upper": str.upper,
    "length": len,
    "like": like,
    "ilike": lambda a, b: like(a, b, True),
    "contains": lambda a, b: b in a,
    "startswith": str.startswith,
    "endswith": str.endswith,
}


def cast(value, source, target, semantics):
    """Explicit conversions; implicit numeric promotion is handled by Runtime."""
    a, b = source.kind, target.kind
    supported = (
        a == b
        or (a in NUMERIC and b in NUMERIC)
        or (
            a is TypeKind.STRING
            and b in NUMERIC | {TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP}
        )
        or (
            b is TypeKind.STRING
            and a
            in NUMERIC
            | {TypeKind.BOOLEAN, TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP}
        )
        or (a, b)
        in {
            (TypeKind.BOOLEAN, TypeKind.INTEGER),
            (TypeKind.INTEGER, TypeKind.BOOLEAN),
            (TypeKind.DATE, TypeKind.TIMESTAMP),
            (TypeKind.TIMESTAMP, TypeKind.DATE),
        }
        or (a in TEMPORAL and b in NUMERIC and semantics.text_temporals)
    )
    if not supported:
        raise TypeError(f"Unsupported cast: {a.value} to {b.value}")
    if value is None:
        return None
    if semantics.text_temporals and a is TypeKind.STRING and b in TEMPORAL:
        try:
            return parse_iso_temporal_value(value, target)
        except ValueError:
            return None
    if semantics.text_temporals and a in TEMPORAL and b in NUMERIC:
        value, a = cast(value, source, STRING, semantics), TypeKind.STRING
    if a is TypeKind.STRING and b in NUMERIC and semantics.lenient_conversions:
        match = _NUMERIC_PREFIX.match(value)
        value = float(match[0]) if match else 0
        if b is TypeKind.INTEGER:
            return int(value)
    if b is TypeKind.INTEGER:
        result = int(value)
    elif b is TypeKind.FLOAT:
        result = float(value)
    elif b is TypeKind.DECIMAL:
        result = float(value)
        if target.scale is not None:
            result = round(result, target.scale)
        if target.precision is not None and abs(result) >= 10.0 ** (
            target.precision - (target.scale or 0)
        ):
            raise OverflowError("Decimal cast exceeds precision")
    elif b is TypeKind.STRING:
        # SQL renders timestamps with a space between date and time.
        result = (
            value.isoformat(sep=" ") if isinstance(value, datetime)
            else value.isoformat() if isinstance(value, (date, time))
            else str(value)
        )
    elif b is TypeKind.BOOLEAN:
        result = bool(value)
    elif b is TypeKind.DATE:
        result = (
            value.date()
            if isinstance(value, datetime)
            else parse_iso_temporal_value(value, target)
            if isinstance(value, str)
            else value
        )
    elif b is TypeKind.TIMESTAMP:
        result = (
            parse_iso_temporal_value(value, target)
            if isinstance(value, str)
            else datetime.combine(value, time())
            if not isinstance(value, datetime)
            else value
        )
    elif b is TypeKind.TIME:
        result = parse_iso_temporal_value(value, target) if isinstance(value, str) else value
    else:
        result = value
    return result


def execute(op, args, sorts, result, semantics):
    if op.startswith("cast_"):
        return cast(args[0], sorts[0].sql_type, result.sql_type, semantics)
    if op not in OPERATIONS:
        raise NotImplementedError(f"Unsupported concolic operation: {op}")
    if op == "is_null" or op == "is_unknown":
        return args[0] is None
    if op == "is_not_null":
        return args[0] is not None
    if op == "is_true":
        return args[0] is True
    if op == "is_false":
        return args[0] is False
    if op == "is_not_distinct":
        return args[0] == args[1]
    if op == "and":
        return False if False in args else None if None in args else True
    if op == "or":
        return True if True in args else None if None in args else False
    if any(arg is None for arg in args):
        return None
    if op in ("div", "mod") and args[1] == 0 and semantics.division_by_zero_is_null:
        return None
    if op == "div" and result.sql_type.kind is TypeKind.INTEGER:
        value = _truncate_division(*args)
    elif op == "mod" and result.sql_type.kind is TypeKind.INTEGER:
        value = args[0] - _truncate_division(*args) * args[1]
    elif (
        op in {"add", "sub"}
        and sorts[0].sql_type.kind in {TypeKind.DATE, TypeKind.TIMESTAMP}
        and sorts[1].sql_type.kind is TypeKind.INTERVAL
    ):
        value = shift_temporal(args[0], args[1] if op == "add" else -args[1])
    elif (
        op == "sub"
        and sorts[0].sql_type.kind is TypeKind.DATE
        and sorts[1].sql_type.kind is TypeKind.DATE
    ):
        value = (args[0] - args[1]).days
    elif op == "sub" and result.sql_type.kind is TypeKind.INTERVAL and isinstance(args[0], datetime):
        delta = args[0] - _timestamp(args[1])
        value = IntervalValue(days=delta.days, microseconds=delta.seconds * 1_000_000 + delta.microseconds)
    elif op == "substring":
        text, start, *length = args
        if length and length[0] < 0:
            if not semantics.lenient_conversions:
                raise ValueError("Negative substring length")
            # SQLite: |length| characters preceding the start position.
            return text[max(start - 1 + length[0], 0) : max(start - 1, 0)]
        begin = max(start - 1, 0)
        end = max(start - 1 + length[0], 0) if length else len(text)
        value = text[begin:end]
    else:
        value = _OPERATORS[op](*args)
    return value
