"""Translate folded scalar and predicate Terms into Z3.

Terms built by concolic execution mention only open instance inputs, so the
translation covers the scalar vocabulary: literals, inputs, scalar calls,
CASE and three-valued predicates. A scalar becomes a value and a NULL flag; a
predicate becomes TRUE and UNKNOWN flags. Each Term also has a definedness
condition that excludes SQL runtime errors such as division by zero; CASE
only requires the definedness of the arm it selects.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count, product
from datetime import date, datetime, timedelta

import z3

from parseval.instance.valuation import Valuation
from parseval.terms import terms as nodes
from parseval.terms.names import ParameterId
from parseval.terms.sorts import IntervalValue, ScalarSort, TypeKind
from parseval.terms.terms import TermId

DAY = 86_400_000_000
EPOCH = datetime(1, 1, 1)


class Unsupported(Exception):
    """A Term has no Z3 encoding."""


@dataclass(frozen=True, slots=True)
class Scalar:
    value: object
    null: z3.BoolRef


@dataclass(frozen=True, slots=True)
class Truth:
    true: z3.BoolRef
    unknown: z3.BoolRef

    @property
    def false(self) -> z3.BoolRef:
        return z3.And(z3.Not(self.true), z3.Not(self.unknown))


def _z3_sort(kind: TypeKind):
    if kind is TypeKind.INTEGER or kind in (TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP):
        return z3.IntSort()
    if kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        return z3.RealSort()
    if kind is TypeKind.STRING:
        return z3.StringSort()
    if kind is TypeKind.BOOLEAN:
        return z3.BoolSort()
    raise Unsupported(f"No Z3 sort for {kind.value}")


def encode(value, kind: TypeKind):
    if kind is TypeKind.INTERVAL:
        return value
    if kind is TypeKind.DATE:
        return z3.IntVal(value.toordinal())
    if kind is TypeKind.TIME:
        return z3.IntVal(((value.hour * 60 + value.minute) * 60 + value.second) * 1_000_000 + value.microsecond)
    if kind is TypeKind.TIMESTAMP:
        delta = value - EPOCH
        return z3.IntVal(delta.days * DAY + delta.seconds * 1_000_000 + delta.microseconds)
    if kind is TypeKind.BOOLEAN:
        return z3.BoolVal(value)
    if kind is TypeKind.STRING:
        return z3.StringVal(value)
    if kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        return z3.RealVal(value)
    return z3.IntVal(value)


def decode(value, kind: TypeKind):
    if kind is TypeKind.BOOLEAN:
        return z3.is_true(value)
    if kind is TypeKind.STRING:
        return value.as_string()
    if kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        fraction = value.as_fraction() if z3.is_rational_value(value) else value.approx(17).as_fraction()
        return float(fraction)
    number = value.as_long()
    if kind is TypeKind.DATE:
        return date.fromordinal(number)
    if kind is TypeKind.TIME:
        return (datetime.min + timedelta(microseconds=number)).time()
    if kind is TypeKind.TIMESTAMP:
        return EPOCH + timedelta(microseconds=number)
    return number


def _default(kind: TypeKind):
    sort = _z3_sort(kind)
    return z3.BoolVal(False) if sort == z3.BoolSort() else z3.StringVal("") if sort == z3.StringSort() else z3.IntVal(0) if sort == z3.IntSort() else z3.RealVal(0)


def _truncate(a, b):
    """Integer division toward zero."""
    quotient = z3.If(a >= 0, a, -a) / z3.If(b >= 0, b, -b)
    return z3.If((a >= 0) == (b >= 0), quotient, -quotient)


def _like_holds(text, pattern: str):
    """LIKE as containment when the pattern has only leading or trailing %."""
    core = pattern.strip("%")
    if "%" not in core and "_" not in core and "\\" not in core:
        literal = z3.StringVal(core)
        if pattern.startswith("%") and pattern.endswith("%") and len(pattern) > len(core) + 1:
            return z3.Contains(text, literal)
        if pattern.endswith("%") and not pattern.startswith("%"):
            return z3.PrefixOf(literal, text)
        if pattern.startswith("%") and not pattern.endswith("%"):
            return z3.SuffixOf(literal, text)
        if pattern == core:
            return text == literal
    return z3.InRe(text, _like(pattern))


def _string_less(left, right):
    """Lexicographic order; against a constant, by the first differing position."""
    if z3.is_string_value(right):
        constant, flip = right.as_string(), False
        other = left
    elif z3.is_string_value(left):
        constant, flip = left.as_string(), True
        other = right
    else:
        return left < right
    alternatives = []
    for k in range(len(constant) + 1):
        prefix = z3.PrefixOf(z3.StringVal(constant[:k]), other)
        if not flip:
            # other < constant: other ends at k, or its k-th character is smaller.
            if k < len(constant):
                smaller = z3.And(z3.Length(other) > k, z3.StrToCode(z3.SubString(other, k, 1)) < ord(constant[k]))
                alternatives.append(z3.And(prefix, z3.Or(z3.Length(other) == k, smaller)))
        else:
            # constant < other: constant ends at k with more text, or other's k-th character is larger.
            if k == len(constant):
                alternatives.append(z3.And(prefix, z3.Length(other) > k))
            else:
                larger = z3.And(z3.Length(other) > k, z3.StrToCode(z3.SubString(other, k, 1)) > ord(constant[k]))
                alternatives.append(z3.And(prefix, larger))
    return z3.Or(*alternatives)


def _like(pattern: str):
    pieces = []
    literal = []
    escaped = False

    def flush():
        if literal:
            pieces.append(z3.Re(z3.StringVal("".join(literal))))
            literal.clear()

    for char in pattern:
        if escaped:
            literal.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in "%_":
            flush()
            anything = z3.AllChar(z3.ReSort(z3.StringSort()))
            pieces.append(z3.Star(anything) if char == "%" else anything)
        else:
            literal.append(char)
    flush()
    if not pieces:
        return z3.Re(z3.StringVal(""))
    return pieces[0] if len(pieces) == 1 else z3.Concat(*pieces)


def _literal(node):
    if not isinstance(node, nodes.Literal):
        raise Unsupported("A function argument must be a constant")
    return node.payload.value


def _civil(ordinal):
    """Year, month and day of a proleptic Gregorian day ordinal (days_from_civil inverse)."""
    z = ordinal - date(1970, 1, 1).toordinal() + 719468
    era = z / 146097
    doe = z - era * 146097
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
    mp = (5 * doy + 2) / 153
    day = doy - (153 * mp + 2) / 5 + 1
    month = z3.If(mp < 10, mp + 3, mp - 9)
    year = yoe + era * 400 + z3.If(month <= 2, 1, 0)
    return {"year": year, "month": month, "day": day}


def _clock(micros):
    seconds = micros / 1_000_000
    return {"hour": seconds / 3600, "minute": seconds / 60 % 60, "second": seconds % 60}


def _padded(number, width):
    text = z3.IntToStr(number)
    for digits in range(width - 1, 0, -1):
        text = z3.If(number < 10 ** digits, z3.Concat(z3.StringVal("0" * (width - digits)), z3.IntToStr(number)), text)
    return text


def _format(pattern: str, ordinal, micros):
    """strftime for numeric date and time fields."""
    fields = {**_civil(ordinal), **_clock(micros)}
    codes = _FORMAT_FIELDS
    pieces = []
    index = 0
    while index < len(pattern):
        if pattern[index] == "%" and index + 1 < len(pattern):
            code = pattern[index + 1]
            if code not in codes:
                raise Unsupported(f"No Z3 encoding for the format %{code}")
            name, width = codes[code]
            pieces.append(_padded(fields[name], width))
            index += 2
        else:
            pieces.append(z3.StringVal(pattern[index]))
            index += 1
    return pieces[0] if len(pieces) == 1 else z3.Concat(*pieces)


_FORMAT_FIELDS = {"Y": ("year", 4), "m": ("month", 2), "d": ("day", 2), "H": ("hour", 2), "M": ("minute", 2), "S": ("second", 2)}


def _parse_format(pattern: str, text: str):
    """Field values of ``text`` formatted by a numeric strftime pattern, or None."""
    fields = []
    position = 0
    index = 0
    while index < len(pattern):
        if pattern[index] == "%" and index + 1 < len(pattern):
            field = _FORMAT_FIELDS.get(pattern[index + 1])
            if field is None:
                return None
            name, width = field
            digits = text[position : position + width]
            if len(digits) != width or not digits.isdigit():
                return None
            fields.append((name, int(digits)))
            position += width
            index += 2
        else:
            if text[position : position + 1] != pattern[index]:
                return None
            position += 1
            index += 1
    return fields if position == len(text) else None


def _lexicographic_less(left, right):
    result = z3.BoolVal(False)
    for a, b in reversed(list(zip(left, right))):
        result = z3.Or(a < b, z3.And(a == b, result))
    return result


def equality_strings(valuation: Valuation, terms) -> frozenset[ParameterId]:
    """String inputs that are only tested for equality, NULL and length.

    Such inputs, and constants equal to them, can be encoded as integer codes:
    distinct values decode to distinct strings, so equality is preserved
    exactly while the solver avoids string reasoning. An input compared in
    any other way, or equal to one that is, keeps the string encoding.
    """
    arena = valuation.arena
    parent: dict[object, object] = {}

    def find(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def atom(term):
        node = arena[term]
        if isinstance(node, nodes.ExternalParameter) and node.payload.parameter in valuation.open:
            return node.payload.parameter
        if isinstance(node, nodes.Literal) and isinstance(node.payload.value, str):
            return ("literal", node.payload.value)
        if isinstance(node, nodes.Null):
            return ("null",)
        return None

    concrete = set()
    strings = set()
    for term in arena.post_order(terms):
        node = arena[term]
        children = [(child, atom(child)) for child in node.children]
        for child, item in children:
            if not isinstance(item, ParameterId) or arena[child].sort.sql_type.kind is not TypeKind.STRING:
                continue
            strings.add(item)
            find(item)
            if isinstance(node, (nodes.Eq3, nodes.IsNotDistinct)):
                other = next(other for other_child, other in children if other_child != child) if len(
                    {c for c, _ in children}) > 1 else item
                if other is None:
                    concrete.add(item)
                elif other != ("null",):
                    parent[find(item)] = find(other)
            elif isinstance(node, (nodes.IsNull, nodes.IsNotNull)):
                continue
            elif isinstance(node, nodes.ScalarCall) and arena.context.function(node.payload.function).operator == "length":
                continue
            else:
                concrete.add(item)
    blocked = {find(item) for item in concrete}
    return frozenset(item for item in strings if find(item) not in blocked)


def _names():
    """Short distinct strings, in order of length."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for length in count(1):
        for letters in product(alphabet, repeat=length):
            yield "".join(letters)


class Translator:
    def __init__(self, valuation: Valuation, *, min_string_length: int = 0, abstract: frozenset = frozenset()):
        """``abstract`` string inputs are encoded as integer codes (see
        ``equality_strings``)."""
        self.v = valuation
        self.min_string_length = min_string_length
        self.abstract = abstract
        self.codes: dict[str, int] = {}
        self.arena = valuation.arena
        self.inputs: dict[ParameterId, Scalar] = {}
        self._memo: dict[TermId, tuple[Scalar | Truth, z3.BoolRef]] = {}

    def code(self, text: str) -> z3.ArithRef:
        return z3.IntVal(self.codes.setdefault(text, len(self.codes)))

    def _coded(self, value):
        """An abstract operand as an integer code; a string constant gets its code."""
        if z3.is_string_value(value):
            return self.code(value.as_string())
        return value

    def holds(self, predicate: TermId) -> z3.BoolRef:
        """The predicate is TRUE and every operation it evaluates is defined."""
        truth, defined = self.translate(predicate)
        return z3.And(truth.true, defined)

    def domain(self) -> list[z3.BoolRef]:
        """Representable values of the inputs translated so far.

        Generated non-NULL strings have at least ``min_string_length``
        characters; this restricts the search, not SQL semantics.
        """
        constraints = []
        for parameter, scalar in self.inputs.items():
            kind = self.v.runtime.inputs[parameter].sort.sql_type.kind
            if kind is TypeKind.DATE:
                constraints.append(z3.And(scalar.value >= 1, scalar.value <= date.max.toordinal()))
            elif kind is TypeKind.TIME:
                constraints.append(z3.And(scalar.value >= 0, scalar.value < DAY))
            elif kind is TypeKind.TIMESTAMP:
                constraints.append(z3.And(scalar.value >= 0, scalar.value < date.max.toordinal() * DAY))
            # Values the input never takes (Valuation.excluded); as codes,
            # abstract strings also keep fresh names off them.
            for value in self.v.excluded.get(parameter, ()):
                excluded = self.code(value) if parameter in self.abstract else encode(value, kind)
                constraints.append(z3.Or(scalar.null, scalar.value != excluded))
            if kind is TypeKind.STRING and self.min_string_length:
                if parameter in self.abstract:
                    # Fresh names are nonempty; only short constants can violate the bound.
                    constraints.extend(
                        z3.Or(scalar.null, scalar.value != z3.IntVal(code))
                        for text, code in self.codes.items() if len(text) < self.min_string_length
                    )
                else:
                    constraints.append(z3.Or(scalar.null, z3.Length(scalar.value) >= self.min_string_length))
        return constraints

    def model(self, model: z3.ModelRef) -> dict[ParameterId, object]:
        values = {}
        literals = {code: text for text, code in self.codes.items()}
        fresh: dict[int, str] = {}
        names = (name for name in _names() if name not in self.codes)
        for parameter, scalar in self.inputs.items():
            sort = self.v.runtime.inputs[parameter].sort
            if z3.is_true(model.eval(scalar.null, model_completion=True)):
                values[parameter] = None
            elif parameter in self.abstract:
                number = model.eval(scalar.value, model_completion=True).as_long()
                if number not in literals and number not in fresh:
                    fresh[number] = next(names)
                values[parameter] = literals.get(number, fresh.get(number))
            else:
                values[parameter] = decode(model.eval(scalar.value, model_completion=True), sort.sql_type.kind)
        return values

    def translate(self, term: TermId):
        """Translate in post-order, so deep Terms need no recursion."""
        memo = self._memo
        pending = [(term, False)]
        while pending:
            current, ready = pending.pop()
            if current in memo:
                continue
            if not ready:
                pending.append((current, True))
                pending.extend((child, False) for child in self.arena[current].children if child not in memo)
                continue
            memo[current] = self._translate(current)
        return memo[term]

    def _translate(self, term: TermId):
        node = self.arena[term]
        true = z3.BoolVal(True)
        if isinstance(node, nodes.Literal):
            kind = node.payload.sql_type.kind
            return Scalar(encode(node.payload.value, kind), z3.BoolVal(False)), true
        if isinstance(node, nodes.Null):
            kind = node.payload.sql_type.kind
            return Scalar(None if kind is TypeKind.INTERVAL else _default(kind), true), true
        if isinstance(node, (nodes.True3, nodes.False3, nodes.Unknown3)):
            return Truth(z3.BoolVal(isinstance(node, nodes.True3)), z3.BoolVal(isinstance(node, nodes.Unknown3))), true
        if isinstance(node, nodes.ExternalParameter):
            return self._input(node.payload.parameter), true
        if isinstance(node, (nodes.Eq3, nodes.Lt3)):
            compared = self._formatted_comparison(node)
            if compared is not None:
                return compared
        children = [self.translate(child) for child in node.children]
        values = [value for value, _ in children]
        defined = z3.And(*(item for _, item in children)) if children else true
        if isinstance(node, nodes.Case):
            condition, then, otherwise = values
            (_, condition_defined), (_, then_defined), (_, else_defined) = children
            result = Scalar(
                z3.If(condition.true, then.value, otherwise.value),
                z3.If(condition.true, then.null, otherwise.null),
            )
            return result, z3.And(condition_defined, z3.If(condition.true, then_defined, else_defined))
        if isinstance(node, nodes.ScalarCall):
            function = self.arena.context.function(node.payload.function)
            sorts = [self.arena[child].sort for child in node.children]
            literals = [self.arena[child] for child in node.children]
            result, condition = self._call(function.operator, values, sorts, function.result, literals)
            return result, z3.And(defined, condition)
        return self._predicate(node, values, [self.arena[child].sort for child in node.children]), defined

    def _formatted_comparison(self, node):
        """Compare ``strftime(pattern, t)`` with a constant in the same format.

        Fixed-width, zero-padded numeric fields order like the tuple of their
        values, so the comparison avoids string reasoning. Constants that do
        not parse under the pattern use the string encoding instead.
        """
        left, right = node.children
        for formatted, constant, flipped in ((left, right, False), (right, left, True)):
            call, literal = self.arena[formatted], self.arena[constant]
            if not (isinstance(call, nodes.ScalarCall) and isinstance(literal, nodes.Literal)):
                continue
            if self.arena.context.function(call.payload.function).operator != "time_to_str":
                continue
            pattern = self.arena[call.children[1]]
            if not isinstance(pattern, nodes.Literal):
                continue
            expected = _parse_format(pattern.payload.value, literal.payload.value)
            if expected is None:
                continue
            argument, defined = self.translate(call.children[0])
            kind = self.arena[call.children[0]].sort.sql_type.kind
            micros = argument.value if kind is not TypeKind.DATE else (argument.value - 1) * DAY
            ordinal = argument.value if kind is TypeKind.DATE else micros / DAY + 1
            fields = {**_civil(ordinal), **_clock(micros % DAY)}
            actual = [fields[name] for name, _ in expected]
            values = [z3.IntVal(value) for _, value in expected]
            if isinstance(node, nodes.Eq3):
                holds = z3.And(*(a == b for a, b in zip(actual, values)))
            else:
                first, second = (values, actual) if flipped else (actual, values)
                holds = _lexicographic_less(first, second)
            return Truth(z3.And(z3.Not(argument.null), holds), argument.null), defined
        return None

    def _equality_operands(self, values):
        """Operands of an equality; with an abstract side, both as integer codes."""
        left, right = values
        if z3.is_int(left.value) != z3.is_int(right.value) and (
            z3.is_seq(left.value) or z3.is_seq(right.value)
        ):
            left = Scalar(self._coded(left.value), left.null)
            right = Scalar(self._coded(right.value), right.null)
        return left, right

    def _input(self, parameter: ParameterId) -> Scalar:
        scalar = self.inputs.get(parameter)
        if scalar is None:
            spec = self.v.runtime.inputs[parameter]
            name = f"p{parameter.value}"
            sort = z3.IntSort() if parameter in self.abstract else _z3_sort(spec.sort.sql_type.kind)
            value = z3.Const(name, sort)
            null = z3.Bool(f"{name}_null") if spec.sort.nullable else z3.BoolVal(False)
            scalar = self.inputs[parameter] = Scalar(value, null)
        return scalar

    def _predicate(self, node, values, sorts) -> Truth:
        false = z3.BoolVal(False)
        if any(isinstance(sort, ScalarSort) and sort.sql_type.kind is TypeKind.INTERVAL for sort in sorts):
            raise Unsupported("No Z3 encoding for symbolic intervals")
        if isinstance(node, (nodes.Eq3, nodes.Lt3)):
            left, right = self._equality_operands(values) if isinstance(node, nodes.Eq3) else values
            unknown = z3.Or(left.null, right.null)
            if isinstance(node, nodes.Eq3):
                holds = left.value == right.value
            elif sorts[0].sql_type.kind is TypeKind.BOOLEAN:
                holds = z3.And(z3.Not(left.value), right.value)
            elif sorts[0].sql_type.kind is TypeKind.STRING:
                holds = _string_less(left.value, right.value)
            else:
                holds = left.value < right.value
            return Truth(z3.And(z3.Not(unknown), holds), unknown)
        if isinstance(node, nodes.IsNull):
            return Truth(values[0].null, false)
        if isinstance(node, nodes.IsNotNull):
            return Truth(z3.Not(values[0].null), false)
        if isinstance(node, nodes.IsNotDistinct):
            left, right = self._equality_operands(values)
            both = z3.And(z3.Not(left.null), z3.Not(right.null), left.value == right.value)
            return Truth(z3.Or(z3.And(left.null, right.null), both), false)
        if isinstance(node, nodes.Not3):
            (inner,) = values
            return Truth(inner.false, inner.unknown)
        if isinstance(node, nodes.And3):
            true = z3.And(*(item.true for item in values))
            any_false = z3.Or(*(item.false for item in values))
            return Truth(true, z3.And(z3.Not(true), z3.Not(any_false)))
        if isinstance(node, nodes.Or3):
            true = z3.Or(*(item.true for item in values))
            all_false = z3.And(*(item.false for item in values))
            return Truth(true, z3.And(z3.Not(true), z3.Not(all_false)))
        if isinstance(node, nodes.ToPredicate):
            (inner,) = values
            return Truth(z3.And(z3.Not(inner.null), inner.value), inner.null)
        if isinstance(node, nodes.ToBoolean):
            (inner,) = values
            return Scalar(inner.true, inner.unknown)
        if isinstance(node, (nodes.Like3, nodes.ILike3)):
            text, pattern = values
            pattern_node = self.arena[node.children[1]]
            if not isinstance(pattern_node, nodes.Literal):
                raise Unsupported("LIKE with a symbolic pattern")
            source = pattern_node.payload.value
            if isinstance(node, nodes.ILike3) and source.lower() != source.upper():
                raise Unsupported("ILIKE with letters in the pattern")
            unknown = z3.Or(text.null, pattern.null)
            return Truth(z3.And(z3.Not(unknown), _like_holds(text.value, source)), unknown)
        raise Unsupported(f"No Z3 encoding for {node.key}")

    def _call(self, operator: str | None, values, sorts, result: ScalarSort, arguments):
        true = z3.BoolVal(True)
        null = z3.Or(*(value.null for value in values)) if values else z3.BoolVal(False)
        kinds = [sort.sql_type.kind for sort in sorts]
        if operator is None:
            raise Unsupported("Scalar call without an operator")
        if operator == "sql_error":
            return Scalar(_default(result.sql_type.kind), z3.BoolVal(False)), z3.BoolVal(False)
        if operator == "coalesce":
            value, is_null = values[-1].value, values[-1].null
            for item in reversed(values[:-1]):
                value = z3.If(item.null, value, item.value)
                is_null = z3.And(item.null, is_null)
            return Scalar(value, is_null), true
        if operator == "nullif":
            left, right = values
            same = z3.And(z3.Not(right.null), left.value == right.value)
            return Scalar(left.value, z3.Or(left.null, same)), true
        if operator.startswith("cast_"):
            return Scalar(self._cast(values[0].value, kinds[0], result.sql_type.kind), null), self._cast_defined(values[0], kinds[0], result.sql_type.kind)
        if operator in ("add", "sub") and TypeKind.INTERVAL in kinds:
            return self._shift(operator, values, kinds, result), true
        if result.sql_type.kind is TypeKind.INTERVAL:
            raise Unsupported("No Z3 encoding for symbolic intervals")
        a = values[0].value
        b = values[1].value if len(values) > 1 else None
        kind = result.sql_type.kind
        if operator == "add":
            return Scalar(z3.Concat(a, b) if kind is TypeKind.STRING else a + b, null), true
        if operator == "sub":
            return Scalar(a - b, null), true
        if operator == "mul":
            return Scalar(a * b, null), true
        if operator in ("div", "mod"):
            if operator == "div":
                quotient = _truncate(a, b) if kind is TypeKind.INTEGER else a / b
            elif kind is TypeKind.INTEGER:
                quotient = a - _truncate(a, b) * b
            else:
                raise Unsupported("No Z3 encoding for a fractional remainder")
            if self.v.runtime.semantics.division_by_zero_is_null:
                return Scalar(quotient, z3.Or(null, b == 0)), true
            return Scalar(quotient, null), z3.Or(null, b != 0)
        if operator == "neg":
            return Scalar(-a, null), true
        if operator == "abs":
            return Scalar(z3.If(a >= 0, a, -a), null), true
        if operator == "concat":
            return Scalar(z3.Concat(a, b), null), true
        if operator == "length":
            if z3.is_int(a):
                # Abstract strings: constants keep their length, fresh names have one letter.
                length = z3.IntVal(1)
                for text, code in self.codes.items():
                    length = z3.If(a == z3.IntVal(code), len(text), length)
                return Scalar(length, null), true
            return Scalar(z3.Length(a), null), true
        if operator == "substring":
            begin = z3.If(b - 1 > 0, b - 1, 0)
            if len(values) == 3:
                length = values[2].value
                end = z3.If(length + b - 1 > 0, length + b - 1, 0)
                forward = z3.SubString(a, begin, z3.If(end > begin, end - begin, 0))
                if not self.v.runtime.semantics.lenient_conversions:
                    return Scalar(forward, null), z3.Or(null, length >= 0)
                start = z3.If(b - 1 + length > 0, b - 1 + length, 0)
                backward = z3.SubString(a, start, z3.If(begin > start, begin - start, 0))
                return Scalar(z3.If(length >= 0, forward, backward), null), true
            return Scalar(z3.SubString(a, begin, z3.Length(a)), null), true
        if operator == "contains":
            return Scalar(z3.Contains(a, b), null), true
        if operator == "startswith":
            return Scalar(z3.PrefixOf(b, a), null), true
        if operator == "endswith":
            return Scalar(z3.SuffixOf(b, a), null), true
        if operator == "instr":
            return Scalar(z3.IndexOf(a, b, 0) + 1, null), true
        if operator == "round":
            scale = 10 ** _literal(arguments[1]) if len(values) > 1 else 1
            return Scalar(z3.ToReal(z3.ToInt(a * scale + z3.RealVal(0.5))) / scale, null), true
        temporal = self._temporal(operator, values, kinds, arguments)
        if temporal is not None:
            return Scalar(temporal, null), true
        raise Unsupported(f"No Z3 encoding for {operator}")

    def _temporal(self, operator: str, values, kinds, arguments):
        """Calendar functions over day ordinals and microsecond timestamps."""
        if operator == "date_part":
            operator, values, kinds = f"extract_{_literal(arguments[0]).casefold()}", values[1:], kinds[1:]
        if not values or kinds[0] not in (TypeKind.DATE, TypeKind.TIMESTAMP, TypeKind.TIME):
            return None
        value, kind = values[0].value, kinds[0]
        micros = value if kind is not TypeKind.DATE else (value - 1) * DAY
        ordinal = value if kind is TypeKind.DATE else micros / DAY + 1
        if operator == "ts_or_ds_to_timestamp":
            return micros
        if operator == "date":
            return ordinal
        if operator == "time":
            return micros % DAY
        if operator == "julianday":
            return z3.ToReal(micros) / DAY + z3.RealVal("1721424.5")
        if operator == "datediff":
            other = values[1].value if kinds[1] is TypeKind.DATE else values[1].value / DAY + 1
            return ordinal - other
        if operator == "time_to_str":
            return _format(_literal(arguments[1]), ordinal, micros % DAY)
        fields = {**_civil(ordinal), **_clock(micros % DAY)}
        unit = operator.removeprefix("extract_")
        if operator == "year" or unit in fields:
            return fields["year" if operator == "year" else unit]
        return None

    def _cast(self, value, source: TypeKind, target: TypeKind):
        numeric = (TypeKind.FLOAT, TypeKind.DECIMAL)
        if source == target or (source in numeric and target in numeric):
            return value
        if source is TypeKind.INTEGER and target in numeric:
            return z3.ToReal(value)
        if source in numeric and target is TypeKind.INTEGER:
            return z3.If(value >= 0, z3.ToInt(value), -z3.ToInt(-value))
        if source is TypeKind.INTEGER and target is TypeKind.STRING:
            return z3.If(value >= 0, z3.IntToStr(value), z3.Concat(z3.StringVal("-"), z3.IntToStr(-value)))
        if source is TypeKind.STRING and target in (TypeKind.INTEGER, *numeric):
            # Digit strings convert exactly; other text is 0 when lenient and
            # excluded by definedness otherwise.
            number = z3.StrToInt(value)
            number = z3.If(number >= 0, number, 0)
            return number if target is TypeKind.INTEGER else z3.ToReal(number)
        if source is TypeKind.BOOLEAN and target is TypeKind.INTEGER:
            return z3.If(value, 1, 0)
        if source is TypeKind.INTEGER and target is TypeKind.BOOLEAN:
            return value != 0
        if source is TypeKind.DATE and target is TypeKind.STRING:
            return _format("%Y-%m-%d", value, z3.IntVal(0))
        if source is TypeKind.TIMESTAMP and target is TypeKind.STRING:
            return _format("%Y-%m-%d %H:%M:%S", value / DAY + 1, value % DAY)
        if source is TypeKind.DATE and target is TypeKind.TIMESTAMP:
            return (value - 1) * DAY
        if source is TypeKind.TIMESTAMP and target is TypeKind.DATE:
            return value / DAY + 1
        raise Unsupported(f"No Z3 encoding for a cast from {source.value} to {target.value}")

    def _cast_defined(self, scalar: Scalar, source: TypeKind, target: TypeKind):
        numeric = target in (TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL)
        if source is TypeKind.STRING and numeric and not self.v.runtime.semantics.lenient_conversions:
            return z3.Or(scalar.null, z3.StrToInt(scalar.value) >= 0)
        return z3.BoolVal(True)

    def _shift(self, operator, values, kinds, result: ScalarSort):
        """Date and timestamp arithmetic with a constant day-time interval."""
        if kinds[0] is TypeKind.INTERVAL:
            values, kinds = values[::-1], kinds[::-1]
        point, interval = values
        if not isinstance(interval.value, IntervalValue) or interval.value.months:
            raise Unsupported("Interval arithmetic needs a constant day-time interval")
        delta = interval.value.days * DAY + interval.value.microseconds
        if kinds[0] is TypeKind.DATE:
            start = (point.value - 1) * DAY
        else:
            start = point.value
        shifted = start + delta if operator == "add" else start - delta
        if result.sql_type.kind is TypeKind.DATE:
            shifted = shifted / DAY + 1
        return Scalar(shifted, z3.Or(point.null, interval.null))


__all__ = ["Translator", "Unsupported", "decode", "encode"]
