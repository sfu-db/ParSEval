"""Constraint propagation for requirements built from simple atoms.

Requirements are normalized into positive formulas over atoms that compare
one open input with a constant or with another input, test NULL, or match a
LIKE pattern. Three-valued TRUE, FALSE and UNKNOWN are pushed to the atoms
exactly, and multiplicity weights (CASE, +, * over 0/1 multiplicities)
become formulas stating which weights are positive.

The search chooses one disjunct at a time and narrows a value space per
input, so it is bounded by the structure of the formula rather than by a
clock. A requirement outside this fragment raises ``Unsupported`` and the
caller uses Z3. Assignments are verified by concrete execution of every
requirement before they are returned.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace

from parseval.instance.domain import Domain, Space, like, placeholder
from parseval.instance.valuation import Failure, Valuation
from parseval.terms import terms as nodes
from parseval.terms.names import ParameterId
from parseval.terms.sorts import TypeKind
from parseval.terms.terms import TermId

BRANCHES = 256
"""Disjunct choices explored before the search gives up."""

CASE_SPLITS = 16
"""Alternatives a predicate may split into by lifting CASE operands."""

NORMALIZED = 10_000
"""Predicates normalized before the search gives up; nested CASE operands
multiply them."""

OUTCOMES = ("true", "false", "unknown")


class Unsupported(Exception):
    """A requirement outside the propagation fragment."""


class _Exhausted(Exception):
    """Normalization exceeded ``NORMALIZED``."""


class Unsatisfiable(Exception):
    """Every disjunct contradicts itself; the requirements cannot hold."""


@dataclass(frozen=True, slots=True)
class Atom:
    """``input op value`` with op one of = != < <= > >=, null, notnull, like,
    notlike, eqvar / nevar relating two inputs (``value`` is an input), or
    test: a predicate Term over this input alone that must be TRUE."""

    input: ParameterId
    op: str
    value: object = None


# A formula is True, False, an Atom, or ("and" | "or", items).

_FLIP = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "!=": "!="}
_NEGATE = {"=": "!=", "!=": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}


def _and(*items):
    if any(item is False for item in items):
        return False
    items = [item for item in items if item is not True]
    return True if not items else items[0] if len(items) == 1 else ("and", tuple(items))


def _or(*items):
    if any(item is True for item in items):
        return True
    items = [item for item in items if item is not False]
    return False if not items else items[0] if len(items) == 1 else ("or", tuple(items))


class Normalizer:
    """Formulas for the TRUE, FALSE and UNKNOWN outcomes of predicate Terms."""

    def __init__(self, valuation: Valuation):
        self.v = valuation
        self.arena = valuation.arena
        self._memo: dict[tuple, object] = {}
        self._weights: dict[TermId, bool] = {}
        self._left = NORMALIZED

    def truth(self, term: TermId, outcome: str = "true"):
        key = (term, outcome)
        if key not in self._memo:
            self._left -= 1
            if self._left < 0:
                raise _Exhausted
            try:
                self._memo[key] = self._truth(term, outcome)
            except Unsupported:
                try:
                    self._memo[key] = self._split(term, outcome)
                except Unsupported:
                    self._memo[key] = self._test(term, outcome)
        return self._memo[key]

    def _split(self, term: TermId, outcome: str):
        """Lift CASE operands out of a predicate: P(CASE c a b) is
        (c TRUE and P(a)) or (c not TRUE and P(b)), as CASE evaluates only
        the selected arm."""
        node = self.arena[term]
        lifted = [self._lift(child) for child in node.children]
        if all(len(options) == 1 for options in lifted):
            raise Unsupported(f"{node.key} without a CASE operand")
        alternatives = [(True, ())]
        for options in lifted:
            alternatives = [
                (_and(condition, extra), (*children, child))
                for condition, children in alternatives
                for extra, child in options
            ]
            if len(alternatives) > CASE_SPLITS:
                raise Unsupported("too many CASE alternatives")
        return _or(*(
            _and(condition, self.truth(self.v.node(type(node), children, node.payload), outcome))
            for condition, children in alternatives
        ))

    def _lift(self, term: TermId) -> list:
        """(condition, CASE-free term) alternatives of a value Term."""
        key = ("lift", term)
        if key not in self._memo:
            self._memo[key] = self._lifted(term)
        return self._memo[key]

    def _lifted(self, term: TermId) -> list:
        node = self.arena[term]
        if not self.v.is_open(term) or not node.children or not isinstance(node, (nodes.Case, nodes.ScalarCall)):
            return [(True, term)]
        if isinstance(node, nodes.Case):
            condition, then, otherwise = node.children
            selected = self.truth(condition, "true")
            rejected = _or(self.truth(condition, "false"), self.truth(condition, "unknown"))
            return [
                *((_and(selected, extra), arm) for extra, arm in self._lift(then)),
                *((_and(rejected, extra), arm) for extra, arm in self._lift(otherwise)),
            ]
        alternatives = [(True, ())]
        for child in node.children:
            alternatives = [
                (_and(condition, extra), (*children, lifted))
                for condition, children in alternatives
                for extra, lifted in self._lift(child)
            ]
            if len(alternatives) > CASE_SPLITS:
                raise Unsupported("too many CASE alternatives")
        return [(condition, self.v.node(type(node), children, node.payload)) for condition, children in alternatives]

    def _test(self, term: TermId, outcome: str):
        """A predicate over a single input becomes a test checked by execution."""
        inputs = self.v.inputs(term)
        if len(inputs) != 1:
            raise Unsupported(f"{self.arena[term].key} relating several inputs")
        condition = {"true": self.v.is_true, "false": self.v.is_false, "unknown": self.v.is_unknown}[outcome](term)
        return Atom(next(iter(inputs)), "test", condition)

    def _truth(self, term: TermId, outcome: str):
        node = self.arena[term]
        if not self.v.is_open(term):
            value = self.v.value(term)
            return value is {"true": True, "false": False, "unknown": None}[outcome]
        if isinstance(node, nodes.Not3):
            return self.truth(node.children[0], {"true": "false", "false": "true", "unknown": "unknown"}[outcome])
        if isinstance(node, (nodes.And3, nodes.Or3)):
            parts = [{o: self.truth(child, o) for o in OUTCOMES} for child in node.children]
            strong, weak = ("true", "false") if isinstance(node, nodes.And3) else ("false", "true")
            if outcome == strong:
                return _and(*(part[strong] for part in parts))
            if outcome == weak:
                return _or(*(part[weak] for part in parts))
            return _and(_or(*(part["unknown"] for part in parts)), *(_or(part[strong], part["unknown"]) for part in parts))
        if isinstance(node, (nodes.IsNull, nodes.IsNotNull)):
            if outcome == "unknown":
                return False
            null = self._null(node.children[0], True)
            not_null = self._null(node.children[0], False)
            return null if isinstance(node, nodes.IsNull) == (outcome == "true") else not_null
        if isinstance(node, nodes.IsNotDistinct):
            return self._not_distinct(node, outcome)
        if isinstance(node, (nodes.Eq3, nodes.Lt3)):
            weight = self._weight_comparison(node, outcome)
            return weight if weight is not None else self._compare(node, outcome)
        if isinstance(node, nodes.Like3):
            (kind, value), (pattern_kind, pattern) = (self._operand(child) for child in node.children)
            if kind != "input" or pattern_kind != "constant":
                raise Unsupported("LIKE with a symbolic pattern")
            if pattern is None:
                return outcome == "unknown"
            if outcome == "unknown":
                return Atom(value, "null")
            return Atom(value, "like" if outcome == "true" else "notlike", pattern)
        if isinstance(node, nodes.ToPredicate):
            kind, value = self._operand(node.children[0])
            if kind != "input":
                raise Unsupported("a Boolean expression over inputs")
            return Atom(value, "null") if outcome == "unknown" else Atom(value, "=", outcome == "true")
        raise Unsupported(node.key)

    def _operand(self, term: TermId):
        """("input", parameter) or ("constant", value); anything else is unsupported."""
        node = self.arena[term]
        if isinstance(node, nodes.ExternalParameter) and node.payload.parameter in self.v.open:
            return "input", node.payload.parameter
        if not self.v.is_open(term):
            value = self.v.value(term)
            if isinstance(value, Failure):
                raise Unsupported("a constant that raises a SQL error")
            return "constant", value
        raise Unsupported(f"{node.key} over open inputs")

    def _null(self, term: TermId, null: bool):
        node = self.arena[term]
        if isinstance(node, nodes.ToBoolean):
            inner = node.children[0]
            return self.truth(inner, "unknown") if null else _or(self.truth(inner, "true"), self.truth(inner, "false"))
        kind, value = self._operand(term)
        if kind == "constant":
            return (value is None) == null
        return Atom(value, "null" if null else "notnull")

    def _not_distinct(self, node, outcome: str):
        if outcome == "unknown":
            return False
        left, right = node.children
        for boolean, other in ((left, right), (right, left)):
            inner = self.arena[boolean]
            if isinstance(inner, nodes.ToBoolean) and not self.v.is_open(other):
                target = {True: "true", False: "false", None: "unknown"}[self.v.value(other)]
                if outcome == "true":
                    return self.truth(inner.children[0], target)
                return _or(*(self.truth(inner.children[0], o) for o in OUTCOMES if o != target))
        (left_kind, a), (right_kind, b) = self._operand(left), self._operand(right)
        if left_kind == "constant":
            (left_kind, a), (right_kind, b) = (right_kind, b), (left_kind, a)
        if right_kind == "constant":
            if b is None:
                return Atom(a, "null" if outcome == "true" else "notnull")
            return Atom(a, "=", b) if outcome == "true" else _or(Atom(a, "null"), Atom(a, "!=", b))
        both = _and(Atom(a, "notnull"), Atom(b, "notnull"))
        if outcome == "true":
            return _or(_and(Atom(a, "null"), Atom(b, "null")), _and(both, Atom(a, "eqvar", b)))
        return _or(
            _and(Atom(a, "null"), Atom(b, "notnull")),
            _and(Atom(a, "notnull"), Atom(b, "null")),
            _and(both, Atom(a, "nevar", b)),
        )

    def _compare(self, node, outcome: str):
        op = "=" if isinstance(node, nodes.Eq3) else "<"
        (left_kind, a), (right_kind, b) = (self._operand(child) for child in node.children)
        if left_kind == "constant":
            (left_kind, a), (right_kind, b), op = (right_kind, b), (left_kind, a), _FLIP[op]
        if right_kind == "input":
            if op != "=":
                raise Unsupported("an order between two inputs")
            if outcome == "unknown":
                return _or(Atom(a, "null"), Atom(b, "null"))
            return _and(Atom(a, "notnull"), Atom(b, "notnull"), Atom(a, "eqvar" if outcome == "true" else "nevar", b))
        if b is None:
            return outcome == "unknown"
        if outcome == "unknown":
            return Atom(a, "null")
        return Atom(a, op if outcome == "true" else _NEGATE[op], b)

    # Multiplicity weights: non-negative INTEGER Terms over 0/1 multiplicities.

    def is_weight(self, term: TermId) -> bool:
        known = self._weights.get(term)
        if known is None:
            node = self.arena[term]
            if not self.v.is_open(term):
                value = self.v.value(term)
                known = isinstance(value, int) and not isinstance(value, bool) and value >= 0
            elif isinstance(node, nodes.ExternalParameter):
                known = self.v.is_binary(term)
            elif isinstance(node, nodes.Case):
                known = self.is_weight(node.children[1]) and self.is_weight(node.children[2])
            elif isinstance(node, nodes.ScalarCall):
                operator = self.arena.context.function(node.payload.function).operator
                known = operator in ("add", "mul") and all(self.is_weight(child) for child in node.children)
            else:
                known = False
            self._weights[term] = known
        return known

    def _weight_comparison(self, node, outcome: str):
        """``0 < w`` states that w is positive and ``w = 0`` that it is zero."""
        left, right = node.children
        zero_left = not self.v.is_open(left) and self.v.value(left) == 0
        zero_right = not self.v.is_open(right) and self.v.value(right) == 0
        if isinstance(node, nodes.Lt3) and zero_left and self.is_weight(right):
            positive = True
            weight = right
        elif isinstance(node, nodes.Eq3) and zero_right and self.is_weight(left):
            positive = False
            weight = left
        else:
            return None
        if outcome == "unknown":
            return False
        return self.positive(weight, positive == (outcome == "true"))

    def positive(self, term: TermId, positive: bool = True):
        """The formula for ``term > 0`` (or ``term = 0``) of a weight."""
        key = ("positive", term, positive)
        if key not in self._memo:
            self._memo[key] = self._positive(term, positive)
        return self._memo[key]

    def _positive(self, term: TermId, positive: bool):
        node = self.arena[term]
        if not self.v.is_open(term):
            return (self.v.value(term) > 0) == positive
        if isinstance(node, nodes.ExternalParameter):
            return Atom(node.payload.parameter, "=", 1 if positive else 0)
        if isinstance(node, nodes.Case):
            condition, then, otherwise = node.children
            selected = self.truth(condition, "true")
            rejected = _or(self.truth(condition, "false"), self.truth(condition, "unknown"))
            return _or(_and(selected, self.positive(then, positive)), _and(rejected, self.positive(otherwise, positive)))
        operator = self.arena.context.function(node.payload.function).operator
        parts = [self.positive(child, positive) for child in node.children]
        # A sum is positive if any part is; a product if all parts are.
        return (_or if (operator == "add") == positive else _and)(*parts)


def search(
    valuation: Valuation,
    requirements: Sequence[Sequence[TermId]],
    *,
    absent: Collection[ParameterId] = (),
    min_string_length: int = 0,
) -> dict[ParameterId, object] | None:
    """Values making one alternative of every requirement TRUE.

    Raises ``Unsupported`` outside the fragment and ``Unsatisfiable`` when
    every disjunct is contradictory, which is a proof because normalization
    is exact. Returns None when the normalization or branch bound is reached
    or picking a value fails, since none proves anything.
    """
    normalizer = Normalizer(valuation)
    try:
        formula = _and(*(_or(*(normalizer.truth(term) for term in requirement)) for requirement in requirements))
    except _Exhausted:
        return None
    sorts = valuation.runtime.inputs
    branches = [BRANCHES]
    inconclusive = [False]

    def narrow(item, spaces: dict, links: list, choices: list) -> bool:
        """Apply units and conjunctions; collect disjunctions for later."""
        pending = [item]
        while pending:
            item = pending.pop()
            if item is True:
                continue
            if item is False:
                return False
            if isinstance(item, Atom):
                if item.op in ("eqvar", "nevar"):
                    if not _consistent(links, item):
                        return False
                    links.append(item)
                    continue
                if item.input not in spaces:
                    sort = sorts[item.input].sort
                    spaces[item.input] = Space(
                        sort.sql_type.kind, sort.nullable, excluded=set(valuation.excluded.get(item.input, ()))
                    )
                if not spaces[item.input].narrow(item.op, item.value):
                    return False
            elif item[0] == "and":
                pending.extend(item[1])
            else:
                choices.append(item[1])
        return True

    def copy(spaces: dict) -> dict:
        return {key: replace(space, excluded=set(space.excluded)) for key, space in spaces.items()}

    def propagate(spaces: dict, links: list, choices: list):
        """Drop entailed disjunctions, prune contradictory options and apply
        disjunctions with one remaining option, until nothing changes."""
        changed = True
        while changed:
            changed = False
            remaining = []
            for options in choices:
                if any(_entailed(option, spaces, links) for option in options):
                    changed = True
                    continue
                viable = []
                for option in options:
                    trial_spaces, trial_links = copy(spaces), list(links)
                    if narrow(option, trial_spaces, trial_links, []):
                        viable.append(option)
                if not viable:
                    return None
                if len(viable) == 1:
                    if not narrow(viable[0], spaces, links, remaining):
                        return None
                    changed = True
                    continue
                if len(viable) < len(options):
                    changed = True
                remaining.append(tuple(viable))
            choices = remaining
        return choices

    def solve(spaces: dict, links: list, choices: list):
        """Propagate, then branch on the disjunction with the fewest options."""
        choices = propagate(spaces, links, choices)
        if choices is None:
            return None
        if not choices:
            assignment = _assign(valuation, spaces, links, absent, min_string_length)
            if assignment is not None and all(
                any(valuation.trial(term, assignment) is True for term in requirement) for requirement in requirements
            ):
                return assignment
            inconclusive[0] = True
            return None
        position = min(range(len(choices)), key=lambda index: len(choices[index]))
        options, rest = choices[position], choices[:position] + choices[position + 1:]
        for option in options:
            branches[0] -= 1
            if branches[0] < 0:
                inconclusive[0] = True
                return None
            trial_spaces, trial_links, trial_choices = copy(spaces), list(links), list(rest)
            if narrow(option, trial_spaces, trial_links, trial_choices):
                found = solve(trial_spaces, trial_links, trial_choices)
                if found is not None:
                    return found
        return None

    spaces: dict = {}
    links: list = []
    choices: list = []
    if not narrow(formula, spaces, links, choices):
        raise Unsatisfiable()
    found = solve(spaces, links, choices)
    if found is None and not inconclusive[0]:
        raise Unsatisfiable()
    return found


def _entailed(formula, spaces: dict, links: list) -> bool:
    """Whether the narrowed spaces already guarantee a formula."""
    if formula is True:
        return True
    if formula is False:
        return False
    if isinstance(formula, tuple):
        kind, items = formula
        check = all if kind == "and" else any
        return check(_entailed(item, spaces, links) for item in items)
    space = spaces.get(formula.input)
    if formula.op == "eqvar":
        return any(link.op == "eqvar" and {link.input, link.value} == {formula.input, formula.value} for link in links)
    if formula.op == "nevar":
        other = spaces.get(formula.value)
        return (
            space is not None and other is not None and space.equals is not None
            and other.equals is not None and space.equals != other.equals
        )
    if space is None or formula.op == "test":
        return False
    if formula.op == "null":
        return space.null is True
    if formula.op == "notnull":
        return space.null is False
    if space.equals is None:
        return False
    value = space.equals
    return {
        "=": lambda: value == formula.value,
        "!=": lambda: value != formula.value,
        "<": lambda: value < formula.value,
        "<=": lambda: value <= formula.value,
        ">": lambda: value > formula.value,
        ">=": lambda: value >= formula.value,
        "like": lambda: like(value, formula.value),
        "notlike": lambda: not like(value, formula.value),
    }[formula.op]()


def _consistent(links: list, new: Atom) -> bool:
    """Whether a new equality or disequality between inputs keeps the links satisfiable."""
    parent: dict[ParameterId, ParameterId] = {}

    def root(item):
        while parent.get(item, item) != item:
            item = parent[item]
        return item

    for link in (*links, new):
        if link.op == "eqvar":
            parent[root(link.input)] = root(link.value)
    return not any(
        link.op == "nevar" and root(link.input) == root(link.value) for link in (*links, new)
    )


def _assign(valuation: Valuation, spaces: dict, links: list, absent, min_string_length):
    """Pick one value per input; inputs linked by equality share it."""
    parent: dict[ParameterId, ParameterId] = {}

    def root(item):
        parent.setdefault(item, item)
        while parent[item] != item:
            item = parent[item]
        return item

    for link in links:
        root(link.input)
        root(link.value)
        if link.op == "eqvar":
            parent[root(link.input)] = root(link.value)
    groups: dict[ParameterId, list[ParameterId]] = {}
    for parameter in {*spaces, *parent}:
        groups.setdefault(root(parameter), []).append(parameter)
    sorts = valuation.runtime.inputs
    assignment: dict[ParameterId, object] = {}
    for members in groups.values():
        sort = sorts[members[0]].sort
        space = Space(
            sort.sql_type.kind, all(sorts[p].sort.nullable for p in members),
            excluded={value for p in members for value in valuation.excluded.get(p, ())},
        )
        for parameter in members:
            for op, value in _atoms(spaces.get(parameter)):
                if not space.narrow(op, value):
                    return None
        for link in links:
            # Inputs that must differ from an already chosen value exclude it.
            if link.op == "nevar":
                for mine, other in ((link.input, link.value), (link.value, link.input)):
                    if mine in members and assignment.get(other) is not None:
                        space.excluded.add(assignment[other])
        def passes(candidate) -> bool:
            if not space.admits(candidate):
                return False
            if sort.sql_type.kind is TypeKind.STRING and candidate is not None and len(candidate) < min_string_length:
                return False
            values = {parameter: candidate for parameter in members}
            return all(valuation.trial(test, values) is True for test in space.tests)

        if space.null is True:
            value = None if passes(None) else _MISSING
        else:
            preferred = 0 if members[0] in absent else valuation.instance.values.get(members[0])
            candidates = (
                *space.candidates(preferred, min_string_length),
                *(value for test in space.tests for value in _derived_values(valuation, test, sort.sql_type.kind)),
                placeholder(sort),
                None,
            )
            value = next((candidate for candidate in candidates if passes(candidate)), _MISSING)
        if value is _MISSING:
            return None
        for parameter in members:
            assignment[parameter] = value
    if any(link.op == "nevar" and assignment.get(link.input) == assignment.get(link.value) for link in links):
        return None
    return assignment


_MISSING = object()


def _derived_values(valuation: Valuation, term: TermId, kind: TypeKind):
    """Values of ``kind`` suggested by the constants inside a test."""
    for node in (valuation.arena[item] for item in valuation.arena.post_order((term,))):
        if isinstance(node, nodes.Literal):
            yield from Domain(kind).around(node.payload.value)


def _atoms(space: Space | None):
    """The atoms a space was narrowed by, to merge equal inputs."""
    if space is None:
        return
    if space.null is True:
        yield "null", None
        yield from (("test", test) for test in space.tests)
        return
    if space.null is False:
        yield "notnull", None
    if space.equals is not None:
        yield "=", space.equals
    for value in space.excluded:
        yield "!=", value
    if space.lower is not None:
        yield (">" if space.lower_strict else ">="), space.lower
    if space.upper is not None:
        yield ("<" if space.upper_strict else "<="), space.upper
    for pattern in space.patterns:
        yield "like", pattern
    for pattern in space.rejected:
        yield "notlike", pattern
    for test in space.tests:
        yield "test", test


__all__ = ["Atom", "Normalizer", "Unsatisfiable", "Unsupported", "search"]
