"""Concolic execution of U-expressions over an instance.

The machine interprets the compiled U-expression directly. Every value it
produces is a folded Term (see ``Valuation``): concrete for stored data and
symbolic in the open inputs of candidate rows. Sums range over the entries of
the bags their row variables are drawn from, so a candidate row with
multiplicity zero contributes a Term whose concrete value is zero.

Every decision of the U-semiring is reported to an ``Observer`` together with
the presence of the rows that reached it; coverage is computed from those
observations, not from individual rows.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from parseval.terms import terms as nodes
from parseval.terms.binding import row_binders_in_child
from parseval.terms.sorts import INTEGER, PREDICATE, RowSort, ScalarSort
from parseval.terms.terms import Direction, NullPlacement, TermId

from .aggregates import Aggregates, concrete_aggregate
from .model import Instance
from .ordering import compare_keys, order, window
from .relations import Bag, Binding, Entry, Environment, Relation, RowValue, Sequence
from .valuation import ExecutionError, Failure, Valuation

_RELATIONAL_VALUES = (nodes.Scalarize, nodes.Fold, nodes.InSubquery)


class UnsupportedQuery(Exception):
    """The U-expression uses a construct the machine cannot execute."""


class TimeLimit(ExecutionError):
    """Execution passed its deadline."""


class Observer(Protocol):
    """Receives each U-semiring decision reached during execution.

    ``covered`` tells whether stored rows reach the decision with that
    outcome. ``condition`` builds, on demand, the predicate over open inputs
    that holds exactly when the outcome is reached; observers call it only
    when they keep a witness, so covered branches cost no symbolic work.
    """

    def child(self, site: int, index: int) -> int: ...

    def observe(self, site: int, term: TermId, outcome: str, covered: bool, condition: Callable[[], TermId]) -> None: ...

    def wants(self, site: int, outcome: str, covered: bool) -> bool:
        """Whether another witness of this outcome would be kept."""
        ...


class Unobserved:
    """An observer for plain execution: it records no decisions."""

    def child(self, site: int, index: int) -> int:
        return 0

    def observe(self, site: int, term: TermId, outcome: str, covered: bool, condition: Callable[[], TermId]) -> None:
        return None

    def wants(self, site: int, outcome: str, covered: bool) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Execution:
    instance: Instance
    relation: Relation
    output: TermId
    """Total multiplicity of the query result, before any outer ordering."""


class Machine:
    def __init__(
        self, valuation: Valuation, observer: Observer | None = None, *,
        budget: int | None = None, deadline: float | None = None,
    ):
        """``budget`` bounds the candidate rows in one binding; None is unbounded.

        Stored rows are always enumerated. A small budget keeps symbolic work
        proportional to the stored data instead of to every combination of
        candidate rows across joined relations. Past the ``deadline`` (a
        ``time.monotonic()`` value) execution raises ``TimeLimit``.
        """
        self.budget = budget
        self.deadline = deadline
        self.v = valuation
        self.arena = valuation.arena
        self.observer = observer if observer is not None else Unobserved()
        self.aggregates = Aggregates(valuation)
        self._free: dict[TermId, frozenset[int]] = {}
        self._binds: dict[tuple, bool] = {}
        self._relational: dict[TermId, bool] = {}
        self._scalars: dict[tuple, TermId] = {}
        self._relations: dict[tuple, Relation] = {}
        self._bases: dict = {}
        self._indexes: dict = {}
        self._inputs: dict[TermId, TermId] = {}
        # Candidate rows of the instance: at most this many rows can move a
        # stored row across a LIMIT or OFFSET.
        self._movers = sum(1 for slot in valuation.instance.all_slots() if slot.parameters[-1] in valuation.open)

    # Entry point.

    def run(self, root: TermId, site: int = 0, *, distinct: bool = False) -> Execution:
        """``distinct``: the query's rows are distinct by construction, or
        duplicates do not matter (set semantics), so its result has no
        duplicate-row outcome."""
        relation = self.relation(root, Environment(), self.v.one, site)
        self._projection(root, relation, site, distinct)
        return Execution(self.v.instance, relation, self._output(root, relation))

    def _output(self, root: TermId, relation: Relation) -> TermId:
        """The productive multiplicity of the result.

        A global aggregate returns one row even for empty input. The result
        is productive when it has rows, every uncorrelated global aggregate
        has input rows, and every value of the result can be computed.
        """
        v = self.v
        computable = v.and3(*(
            v.or3(v.eq3(entry.weight, v.zero), v.and3(*(v.defined(cell) for cell in entry.row.cells)))
            for entry in relation.entries
        ))
        inputs = (v.squash(count) for count in self._inputs.values())
        return v.mul(v.indicator(computable), self.total(relation), *inputs)

    def _projection(self, root: TermId, relation: Relation, site: int, distinct: bool) -> None:
        """Outcomes of the final projection: duplicate rows, and per column a
        NULL, a repeated non-NULL value and two different values. Without the
        duplicate-row outcome (``distinct``), a single column has no repeated
        value outcome either: a repeated value would be a duplicate row.

        Column ``c`` uses the site ``child(site, -1 - c)``.
        """
        v = self.v
        for entry in relation.entries:
            if self.present(entry.weight):
                for cell in entry.row.cells:
                    value = v.value(cell)
                    if isinstance(value, Failure):
                        raise ExecutionError(value.reason)
        entries = [(entry.weight, entry.row.cells) for entry in relation.entries]
        if not distinct:
            self._occurrences(site, root, v.one, entries, ("duplicate",))
        width = len(self.arena.context.schema(relation.schema).fields)
        outcomes = ("null", "distinct") if distinct and width == 1 else ("null", "duplicate", "distinct")
        for column in range(width):
            column_site = self.observer.child(site, -1 - column)
            items = [(weight, (cells[column],)) for weight, cells in entries]
            self._occurrences(column_site, root, v.one, items, outcomes)

    def _occurrences(self, site: int, term: TermId, context: TermId, items, outcomes) -> None:
        """Report value outcomes over weighted occurrences of value tuples.

        ``null``: an occurrence holds NULL; ``duplicate``: two occurrences hold
        the same non-NULL values; ``distinct``: two hold different non-NULL
        values. Coverage is decided by hashing the concrete values of present
        occurrences. Symbolic witnesses are built from occurrences that
        depend on candidate rows, only while the observer wants more.
        """
        v = self.v
        reached = self.present(context)
        rows = []
        for weight, cells in items:
            values = tuple(v.value(cell) for cell in cells)
            stored = v.constant(weight) and not self.is_open(cells)
            rows.append((weight, cells, self.count(weight), values, stored))
        valid = [row for row in rows if not any(isinstance(value, Failure) for value in row[3])]
        # Prefer stored witnesses: their conditions are constant, hence stable.
        present = sorted((row for row in valid if reached and row[2] > 0), key=lambda row: not row[4])
        groups: dict[tuple, list] = {}
        for row in present:
            if None not in row[3]:
                groups.setdefault(row[3], []).append(row)

        def presence(*members):
            weight = context
            for member in members:
                weight = v.mul(weight, member[0])
            return v.positive(weight)

        def complete(*members):
            return v.and3(*(v.not3(v.is_null(cell)) for member in members for cell in member[1]))

        def null(row):
            return v.and3(presence(row), v.or3(*(v.is_null(cell) for cell in row[1])))

        def repeated(row):
            return v.and3(presence(row), v.at_least(row[0], 2), complete(row))

        def pair(left, right, same):
            relation = v.same_row(left[1], right[1])
            return v.and3(presence(left, right), complete(left, right), relation if same else v.not3(relation))

        firsts = [group[0] for group in groups.values()]
        covering = {
            "null": next((lambda row=row: null(row) for row in present if None in row[3]), None),
            "duplicate": next((
                (lambda group=group: repeated(group[0])) if group[0][2] > 1
                else (lambda group=group: pair(group[0], group[1], True))
                for group in groups.values() if sum(row[2] for row in group) > 1
            ), None),
            "distinct": (lambda: pair(firsts[0], firsts[1], False)) if len(firsts) > 1 else None,
        }
        symbolic = [row for row in valid if not row[4]]
        for outcome in outcomes:
            witness = covering[outcome]
            if witness is not None:
                self.observer.observe(site, term, outcome, True, witness)
            for condition in self._symbolic_witnesses(outcome, symbolic, valid, null, repeated, pair):
                if not self.observer.wants(site, outcome, witness is not None):
                    break
                # Pairs of occurrences grow quadratically with the rows.
                self._on_time()
                self.observer.observe(site, term, outcome, False, condition)

    @staticmethod
    def _symbolic_witnesses(outcome, symbolic, rows, null, repeated, pair):
        """Lazily enumerate witness conditions involving candidate occurrences."""
        if outcome == "null":
            for row in symbolic:
                yield lambda row=row: null(row)
            return
        if outcome == "duplicate":
            for row in symbolic:
                yield lambda row=row: repeated(row)
        for row in symbolic:
            for other in rows:
                if other is not row:
                    yield lambda row=row, other=other: pair(row, other, outcome == "duplicate")

    def total(self, relation: Relation) -> TermId:
        return self.v.add(*(entry.weight for entry in relation.entries))

    def is_open(self, cells: Iterable[TermId]) -> bool:
        return any(self.v.is_open(cell) for cell in cells)

    # Static analysis of Terms.

    def free(self, term: TermId) -> frozenset[int]:
        """Free row De Bruijn indices of a Term."""
        known = self._free
        pending = [term]
        while pending:
            current = pending[-1]
            if current in known:
                pending.pop()
                continue
            node = self.arena[current]
            missing = [child for child in node.children if child not in known]
            if missing:
                pending.extend(missing)
                continue
            pending.pop()
            if isinstance(node, nodes.RowVar):
                known[current] = frozenset((node.payload.depth,))
            else:
                known[current] = frozenset(
                    index - row_binders_in_child(type(node), position)
                    for position, child in enumerate(node.children)
                    for index in known[child]
                    if index >= row_binders_in_child(type(node), position)
                )
        return known[term]

    def can_bind(self, term: TermId, unbound: frozenset[int]) -> bool:
        """Whether evaluating a multiplicity enumerates all ``unbound`` variables."""
        if not unbound:
            return True
        key = (term, unbound)
        cached = self._binds.get(key)
        if cached is not None:
            return cached
        node = self.arena[term]
        result = False
        if isinstance(node, nodes.At):
            row = self.arena[node.children[1]]
            result = (
                isinstance(row, nodes.RowVar)
                and unbound == {row.payload.depth}
                and not self.free(node.children[0]) & unbound
            )
        elif isinstance(node, nodes.Indicator):
            result = any(
                unbound == {variable} and not self.free(definition) & unbound
                for variable, definition in self._identity_sides(node.children[0])
            )
        elif isinstance(node, nodes.Mul):
            result = self._schedule(node.children, unbound) is not None
        elif isinstance(node, nodes.Add):
            result = all(self.can_bind(child, unbound) for child in node.children)
        elif isinstance(node, nodes.Sum):
            body = self.arena[node.children[0]].children[0]
            result = self.can_bind(body, frozenset((0, *(index + 1 for index in unbound))))
        elif isinstance(node, nodes.Squash):
            result = self.can_bind(node.children[0], unbound)
        self._binds[key] = result
        return result

    def _schedule(self, factors: tuple[TermId, ...], unbound: frozenset[int]) -> list[int] | None:
        """Factor order that binds every variable before it is consumed."""
        needs = [self.free(factor) & unbound for factor in factors]
        pending = list(range(len(factors)))
        bound: set[int] = set()
        plan = []
        while pending:
            # Filters apply as soon as their rows are bound; enumeration waits.
            ready = next((i for i in pending if not needs[i] - bound), None)
            if ready is None:
                binders = [i for i in pending if self.can_bind(factors[i], frozenset(needs[i] - bound))]
                if not binders:
                    return None
                # A row an equi-join links to bound rows or a constant is
                # probed rather than scanned, so it goes first.
                ready = next((i for i in binders if self._linked(factors, i, pending, unbound - bound)), binders[0])
            plan.append(ready)
            bound |= needs[ready]
            pending.remove(ready)
        return plan if unbound <= bound else None

    def _linked(self, factors: tuple[TermId, ...], index: int, pending: list[int], unbound: frozenset[int]) -> bool:
        node = self.arena[factors[index]]
        row = self.arena[node.children[1]] if isinstance(node, nodes.At) else None
        if not isinstance(row, nodes.RowVar):
            return False
        variable = row.payload.depth
        return any(
            self._equi_join(factors[other], variable, set(unbound) - {variable}) is not None
            for other in pending if other != index
        )

    def _identity_sides(self, predicate: TermId) -> list[tuple[int, TermId]]:
        """``(variable, definition)`` pairs of a row identity ``t === e``."""
        node = self.arena[predicate]
        if not isinstance(node, nodes.RowIdentityEq):
            return []
        sides = []
        for variable, other in (node.children, reversed(node.children)):
            variable_node = self.arena[variable]
            if isinstance(variable_node, nodes.RowVar):
                depth = variable_node.payload.depth
                if depth not in self.free(other):
                    sides.append((depth, other))
        return sides

    def relational(self, term: TermId) -> bool:
        """Whether a scalar Term contains a subquery."""
        known = self._relational
        pending = [term]
        while pending:
            current = pending[-1]
            if current in known:
                pending.pop()
                continue
            node = self.arena[current]
            if isinstance(node, _RELATIONAL_VALUES):
                known[current] = True
                pending.pop()
                continue
            missing = [child for child in node.children if child not in known]
            if missing:
                pending.extend(missing)
                continue
            pending.pop()
            known[current] = any(known[child] for child in node.children)
        return known[term]

    # Scalars, predicates and rows.

    def scalar(self, term: TermId, env: Environment, context: TermId, site: int) -> TermId:
        """A scalar or predicate Term instantiated in an environment."""
        free = self.free(term)
        if not free and not self.relational(term):
            return self.v.fold(term)
        key = (term, site, env.key(free), context)
        cached = self._scalars.get(key)
        if cached is not None:
            return cached
        node = self.arena[term]
        child = self.observer.child
        if isinstance(node, nodes.Field):
            result = self.row(node.children[0], env, context, child(site, 0)).cells[node.payload.index]
        elif isinstance(node, nodes.RowIdentityEq):
            left, right = (self.row(item, env, context, child(site, index)) for index, item in enumerate(node.children))
            result = self.v.same_row(left.cells, right.cells)
        elif isinstance(node, nodes.Case):
            condition, then, otherwise = (
                self.scalar(item, env, context, child(site, index)) for index, item in enumerate(node.children)
            )
            result = self.v.case(condition, then, otherwise)
        elif isinstance(node, nodes.Scalarize):
            result = self._scalarize(term, node, env, context, site)
        elif isinstance(node, nodes.Fold):
            result = self._fold(node, env, context, site)
        elif isinstance(node, nodes.InSubquery):
            result = self._in_subquery(node, env, context, site)
        else:
            result = self.v.node(
                type(node),
                (self.scalar(item, env, context, child(site, index)) for index, item in enumerate(node.children)),
                node.payload,
            )
        if node.sort == PREDICATE:
            self._predicate(site, term, context, result)
        self._scalars[key] = result
        return result

    def _predicate(self, site: int, term: TermId, context: TermId, result: TermId) -> None:
        v = self.v
        reached = self.present(context)
        value = v.value(result)
        outcomes = [("true", value is True, v.is_true), ("false", value is False, v.is_false)]
        if may_be_unknown(self.arena, term):
            outcomes.append(("unknown", value is None, v.is_unknown))
        for outcome, holds, test in outcomes:
            self.note(site, term, outcome, context, reached and holds, lambda test=test: test(result))

    def note(self, site: int, term: TermId, outcome: str, context: TermId, covered: bool, condition) -> None:
        """Report an outcome reached under ``context``; the condition is built lazily."""
        v = self.v
        self.observer.observe(site, term, outcome, covered, lambda: v.and3(v.positive(context), condition()))

    def present(self, weight: TermId) -> bool:
        return self.count(weight) > 0

    def count(self, weight: TermId) -> int:
        """The concrete value of a weight; a failing weight counts as absent."""
        value = self.v.value(weight)
        return 0 if isinstance(value, Failure) else value

    def row(self, term: TermId, env: Environment, context: TermId, site: int) -> RowValue:
        node = self.arena[term]
        if isinstance(node, nodes.RowVar):
            row = env.rows[node.payload.depth]
            if row is None:
                raise UnsupportedQuery("A row variable is used before it is bound")
            return row
        if isinstance(node, nodes.Row):
            return RowValue(
                node.payload.schema,
                tuple(
                    self.scalar(item, env, context, self.observer.child(site, index))
                    for index, item in enumerate(node.children)
                ),
            )
        raise UnsupportedQuery(f"Unsupported row expression {node.key}")

    def apply(self, function: TermId, row: RowValue, env: Environment, context: TermId, site: int):
        """Apply a row lambda to a row: a RowValue, scalar or predicate Term."""
        body = self.arena[function].children[0]
        inner = env.push(row)
        site = self.observer.child(site, 0)
        if isinstance(self.arena[body].sort, RowSort):
            return self.row(body, inner, context, site)
        return self.scalar(body, inner, context, site)

    # Multiplicities.

    def bindings(self, term: TermId, env: Environment, context: TermId, site: int) -> list[Binding]:
        """Assignments to free row variables of ``env`` with their weights."""
        self._on_time()
        v = self.v
        node = self.arena[term]
        child = self.observer.child
        if isinstance(node, nodes.Zero):
            return [Binding((), v.zero, v.one)]
        if isinstance(node, nodes.One):
            return [Binding((), v.one, v.one)]
        if isinstance(node, nodes.At):
            return self._at(node, env, context, site)
        if isinstance(node, nodes.Indicator):
            for variable, definition in self._identity_sides(node.children[0]):
                if env.rows[variable] is None and not (self.free(definition) & env.free):
                    row = self.row(definition, env, context, child(child(site, 0), 1))
                    return [Binding(((variable, row),), v.one, v.one)]
            predicate = self.scalar(node.children[0], env, context, child(site, 0))
            return [Binding((), v.indicator(predicate), v.one)]
        if isinstance(node, nodes.Mul):
            return self._product(node.children, env, context, site)
        if isinstance(node, nodes.Add):
            result = []
            for index, summand in enumerate(node.children):
                summand_site = child(site, index)
                for binding in self.bindings(summand, env, context, summand_site):
                    self._positive(summand_site, summand, v.mul(context, binding.presence), binding.weight)
                    result.append(binding)
            return result
        if isinstance(node, nodes.Sum):
            return self._sum(term, node, env, context, site)
        if isinstance(node, (nodes.Squash, nodes.UNot)):
            inner = self.bindings(node.children[0], env, context, child(site, 0))
            if self.free(term) & env.free:
                if isinstance(node, nodes.UNot):
                    raise UnsupportedQuery("NOT cannot enumerate rows")
                return self._distinct(inner, site, term, context)
            total = v.add(*(binding.weight for binding in inner))
            reached = self.present(context)
            self._absence(site, term, "zero", context, reached and self.count(total) == 0, lambda: v.eq3(total, v.zero))
            self.note(site, term, "positive", context, reached and self.present(total), lambda: v.positive(total))
            weight = v.squash(total) if isinstance(node, nodes.Squash) else v.unot(total)
            return [Binding((), weight, v.one)]
        raise UnsupportedQuery(f"Unsupported multiplicity {node.key}")

    def _on_time(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TimeLimit("the time limit passed")

    def _absence(self, site: int, term: TermId, outcome: str, context: TermId, covered: bool, condition) -> None:
        """Report that nothing is present, only for decisions correlated with outer rows.

        An uncorrelated absence describes the whole database rather than a
        branch exercised by the rows that reach it.
        """
        if self.free(term):
            self.note(site, term, outcome, context, covered, condition)

    def _positive(self, site: int, term: TermId, context: TermId, weight: TermId) -> None:
        self.note(site, term, "positive", context, self.present(context) and self.present(weight),
                  lambda: self.v.positive(weight))

    def _at(self, node, env: Environment, context: TermId, site: int) -> list[Binding]:
        v = self.v
        child = self.observer.child
        relation = self.relation(node.children[0], env, context, child(site, 0))
        if isinstance(relation, Sequence):
            relation = self._forget(relation)
        row_node = self.arena[node.children[1]]
        if isinstance(row_node, nodes.RowVar) and env.rows[row_node.payload.depth] is None:
            depth = row_node.payload.depth
            return [
                Binding(((depth, entry.row),), entry.weight, entry.weight, entry.candidates)
                for entry in relation.entries if self._admits(env, entry)
            ]
        row = self.row(node.children[1], env, context, child(site, 1))
        return [Binding((), self.multiplicity(relation, row), v.one)]

    def multiplicity(self, bag: Bag, row: RowValue) -> TermId:
        """``bag(row)``: the summed weight of the entries equal to ``row``."""
        v = self.v
        index, open_entries = self._index(bag)
        if self.is_open(row.cells):
            candidates = bag.entries
        else:
            candidates = (*index.get(row.cells, ()), *open_entries)
        return v.add(*(
            v.mul(entry.weight, v.indicator(v.same_row(entry.row.cells, row.cells)))
            for entry in candidates
        ))

    def _index(self, bag: Bag):
        cached = self._indexes.get(id(bag))
        if cached is None or cached[2] is not bag:
            index: dict = {}
            open_entries = []
            for entry in bag.entries:
                if self.is_open(entry.row.cells):
                    open_entries.append(entry)
                else:
                    index.setdefault(entry.row.cells, []).append(entry)
            cached = self._indexes[id(bag)] = (index, tuple(open_entries), bag)
        return cached[0], cached[1]

    def _product(self, factors: tuple[TermId, ...], env: Environment, context: TermId, site: int) -> list[Binding]:
        """Join factors in schedule order, dropping bindings with zero weight."""
        v = self.v
        current = [Binding((), v.one, v.one)]
        plan = self._plan(factors, env)
        probes = self._probes(factors, plan, env)
        for position, index in enumerate(plan):
            factor_site = self.observer.child(site, index)
            extended = []
            for binding in current:
                bound = env.bind(binding.rows, binding.candidates)
                # A factor is reached when every factor scheduled before it is
                # nonzero; this matches the pruning of zero-weight bindings.
                reach = v.mul(context, binding.weight)
                results = None
                if position in probes:
                    results = self._probe(factors, index, probes[position], bound, reach, site)
                if results is None:
                    results = self.bindings(factors[index], bound, reach, factor_site)
                for result in results:
                    self._on_time()
                    weight = v.mul(binding.weight, result.weight)
                    if weight != v.zero:
                        # A binding with a constant zero weight contributes
                        # nothing, whatever the candidate rows become.
                        extended.append(Binding(
                            binding.rows + result.rows, weight, v.mul(binding.presence, result.presence),
                            binding.candidates + result.candidates,
                        ))
            current = extended
        return current

    def _probes(self, factors: tuple[TermId, ...], plan: list[int], env: Environment) -> dict[int, tuple]:
        """Equi-join filters that can probe the relation a row is drawn from.

        For a factor ``R(t)`` that enumerates ``t``, a later filter
        ``[t.c = e]`` whose ``e`` is bound before ``t`` lets enumeration look
        up the entries of ``R`` with ``c = e`` instead of scanning ``R``.
        """
        probes = {}
        unbound = set(env.free)
        for position, index in enumerate(plan):
            node = self.arena[factors[index]]
            row = self.arena[node.children[1]] if isinstance(node, nodes.At) else None
            if isinstance(row, nodes.RowVar) and row.payload.depth in unbound:
                variable = row.payload.depth
                for later in plan[position + 1:]:
                    probe = self._equi_join(factors[later], variable, unbound - {variable})
                    if probe is not None:
                        probes[position] = (later, *probe)
                        break
            unbound -= self.free(factors[index])
        return probes

    def _equi_join(self, factor: TermId, variable: int, unbound: set[int]):
        node = self.arena[factor]
        if not isinstance(node, nodes.Indicator) or not isinstance(self.arena[node.children[0]], nodes.Eq3):
            return None
        sides = self.arena[node.children[0]].children
        for side, (field, other) in enumerate(((sides[0], sides[1]), (sides[1], sides[0]))):
            field_node = self.arena[field]
            if (
                isinstance(field_node, nodes.Field)
                and isinstance(self.arena[field_node.children[0]], nodes.RowVar)
                and self.arena[field_node.children[0]].payload.depth == variable
                and not self.free(other) & (unbound | {variable})
            ):
                return field_node.payload.index, other, 1 - side
        return None

    def _probe(self, factors, index: int, probe: tuple, env: Environment, context: TermId, site: int):
        """Enumerate the entries matching an equi-join key, or None to scan.

        Stored entries are looked up by their concrete key; candidate entries
        always remain. Skipped stored entries cover the filter's FALSE (and,
        for a NULL key, UNKNOWN) outcomes concretely.
        """
        v = self.v
        child = self.observer.child
        later, column, other, other_side = probe
        if not v.constant(context) or not self.present(context):
            return None
        at = self.arena[factors[index]]
        relation = self._forget(self.relation(at.children[0], env, context, child(child(site, index), 0)))
        predicate_site = child(child(site, later), 0)
        key = self.scalar(other, env, context, child(predicate_site, other_side))
        value = v.value(key)
        if not v.constant(key) or isinstance(value, Failure):
            return None
        index_by_value, open_entries, present = self._column_index(relation, column)
        matched = index_by_value.get(value, ()) if value is not None else ()
        # Present stored entries the lookup skips, and those with a NULL key.
        skipped = sum(present.values()) - (present.get(value, 0) if value is not None else 0)
        nulls = present.get(None, 0)
        predicate = self.arena[factors[later]].children[0]
        if value is not None and skipped > nulls:
            self.observer.observe(predicate_site, predicate, "false", True, lambda: v.true)
        if (nulls or value is None and skipped) and may_be_unknown(self.arena, predicate):
            self.observer.observe(predicate_site, predicate, "unknown", True, lambda: v.true)
        depth = self.arena[at.children[1]].payload.depth
        return [
            Binding(((depth, entry.row),), entry.weight, entry.weight, entry.candidates)
            for entry in (*matched, *open_entries) if self._admits(env, entry)
        ]

    def _admits(self, env: Environment, entry: Entry) -> bool:
        return self.budget is None or env.candidates + entry.candidates <= self.budget

    def _column_index(self, bag: Bag, column: int):
        key = (id(bag), column)
        cached = self._indexes.get(key)
        if cached is None or cached[3] is not bag:
            index: dict = {}
            open_entries = []
            present: dict = {}
            for entry in bag.entries:
                cell = entry.row.cells[column]
                value = self.v.value(cell)
                if self.v.is_open(cell) or isinstance(value, Failure):
                    open_entries.append(entry)
                else:
                    index.setdefault(value, []).append(entry)
                    if self.present(entry.weight):
                        present[value] = present.get(value, 0) + 1
            cached = self._indexes[key] = (index, tuple(open_entries), present, bag)
        return cached[0], cached[1], cached[2]

    def _plan(self, factors: tuple[TermId, ...], env: Environment) -> list[int]:
        """The schedule, with each probed equi-join filter right after the row it probes.

        A lookup applies the filter to stored entries as they are enumerated,
        so the filter must also apply there for candidate entries: factors
        between the two would otherwise be reached by candidate entries whose
        key does not match, which stored entries never do.
        """
        unbound = frozenset().union(*(self.free(factor) for factor in factors)) & env.free
        plan = self._schedule(factors, unbound)
        if plan is None:
            raise UnsupportedQuery("A product cannot bind all of its row variables")
        for position, (later, *_) in sorted(self._probes(factors, plan, env).items(), reverse=True):
            plan.remove(later)
            plan.insert(position + 1, later)
        return plan

    def _sum(self, term: TermId, node, env: Environment, context: TermId, site: int) -> list[Binding]:
        v = self.v
        body = self.arena[node.children[0]].children[0]
        body_site = self.observer.child(self.observer.child(site, 0), 0)
        inner = self.bindings(body, env.push(None), context, body_site)
        result = []
        for binding in inner:
            rows = dict(binding.rows)
            if 0 not in rows:
                raise UnsupportedQuery("A sum does not bind its row variable")
            self._positive(site, term, v.mul(context, binding.presence), binding.weight)
            result.append(Binding(
                tuple((index - 1, row) for index, row in binding.rows if index > 0),
                binding.weight,
                binding.presence,
                binding.candidates,
            ))
        if self.free(term) & env.free:
            return result
        return [Binding((), v.add(*(binding.weight for binding in result)), v.one)]

    def _distinct(self, inner: list[Binding], site: int, term: TermId, context: TermId) -> list[Binding]:
        """Squash enumerating rows: one binding per distinct assignment."""
        v = self.v
        groups: dict[tuple, list[Binding]] = {}
        for binding in inner:
            groups.setdefault(tuple(sorted(binding.rows, key=lambda item: item[0])), []).append(binding)
        cells = {rows: tuple(cell for _, row in rows for cell in row.cells) for rows in groups}
        open_groups = {rows for rows in groups if self.is_open(cells[rows])}
        representatives = list(groups)
        result = []
        for position, rows in enumerate(representatives):
            members = []
            for other, bindings in groups.items():
                if other == rows:
                    members.extend(binding.weight for binding in bindings)
                elif rows in open_groups or other in open_groups:
                    equal = v.indicator(v.same_row(cells[other], cells[rows]))
                    members.extend(v.mul(binding.weight, equal) for binding in bindings)
            total = v.add(*members)
            earlier = v.or3(*(
                v.same_row(cells[previous], cells[rows])
                for previous in representatives[:position]
                if rows in open_groups or previous in open_groups
            ))
            weight = v.indicator(v.and3(v.not3(earlier), v.positive(total)))
            self.note(site, term, "duplicate", context,
                      self.present(context) and v.value(earlier) is False and self.count(total) > 1,
                      lambda earlier=earlier, total=total: v.and3(v.not3(earlier), v.at_least(total, 2)))
            result.append(Binding(rows, weight, weight, max(binding.candidates for binding in groups[rows])))
        return result

    # Relations.

    def relation(self, term: TermId, env: Environment, context: TermId, site: int) -> Relation:
        free = self.free(term)
        if not free:
            # An uncorrelated relation is reached independently of its consumer.
            context = self.v.one
        key = (term, site, env.key(free), context)
        cached = self._relations.get(key)
        if cached is None:
            cached = self._relations[key] = self._relation(term, env, context, site)
        return cached

    def _relation(self, term: TermId, env: Environment, context: TermId, site: int) -> Relation:
        v = self.v
        node = self.arena[term]
        child = self.observer.child
        if isinstance(node, nodes.Base):
            return self._base(node)
        if isinstance(node, nodes.RelVar):
            return env.relations[node.payload.depth]
        if isinstance(node, nodes.LetRel):
            definition = self.relation(node.children[0], env, context, child(site, 0))
            return self.relation(node.children[1], env.push_relation(definition), context, child(site, 1))
        if isinstance(node, nodes.BagLambda):
            body = self.arena[node.children[0]].children[0]
            schema = node.sort.schema
            bindings = self.bindings(body, env.push(None), context, child(child(site, 0), 0))
            entries = []
            for binding in bindings:
                rows = dict(binding.rows)
                if 0 not in rows:
                    raise UnsupportedQuery("A bag does not construct its output row")
                entries.append(Entry(RowValue(schema, rows[0].cells), binding.weight, binding.candidates))
            return Bag(schema, tuple(entries))
        if isinstance(node, (nodes.GroupFold, nodes.GlobalFold)):
            return self._group(term, node, env, context, site)
        if isinstance(node, nodes.Window):
            return self._window(node, env, context, site)
        if isinstance(node, nodes.ForgetOrder):
            return self._forget(self.relation(node.children[0], env, context, child(site, 0)))
        if isinstance(node, nodes.OrderBy):
            return self._order(node, env, context, site)
        if isinstance(node, nodes.SeqMap):
            sequence = self.relation(node.children[0], env, context, child(site, 0))
            entries = tuple(
                Entry(
                    self.apply(node.children[1], entry.row, env, v.mul(context, entry.weight), child(site, 1)),
                    entry.weight, entry.candidates,
                )
                for entry in sequence.entries
            )
            return Sequence(node.sort.schema, entries, sequence.keys, sequence.specs, sequence.positions)
        if isinstance(node, (nodes.Take, nodes.Drop, nodes.Slice)):
            *counts, source = node.children
            counts = [self.scalar(count, env, context, child(site, index)) for index, count in enumerate(counts)]
            sequence = self.relation(source, env, context, child(site, len(counts)))
            if sequence.positions is None and all(self._count(count) is not None for count in counts):
                bounds = [self._count(count) for count in counts]
                if isinstance(node, nodes.Take):
                    return self._limit(sequence, 0, bounds[0])
                if isinstance(node, nodes.Drop):
                    return self._limit(sequence, bounds[0], None)
                return self._limit(sequence, bounds[0], bounds[0] + bounds[1])
            if isinstance(node, nodes.Take):
                return self._take(sequence, counts[0])
            if isinstance(node, nodes.Drop):
                return self._drop(sequence, counts[0])
            return self._take(self._drop(sequence, counts[0]), counts[1])
        raise UnsupportedQuery(f"Unsupported relation {node.key}")

    def _base(self, node) -> Bag:
        relation = node.payload.relation
        bag = self._bases.get(relation)
        if bag is None:
            v = self.v
            schema = node.sort.schema
            bag = self._bases[relation] = Bag(schema, tuple(
                Entry(
                    RowValue(schema, tuple(v.input(cell) for cell in slot.cells)),
                    v.input(slot.multiplicity),
                    int(v.is_open(v.input(slot.multiplicity))),
                )
                for slot in v.instance.slots(relation)
            ))
        return bag

    def occurrences(self, relation: Relation) -> list[RowValue]:
        """Present rows in result order, each repeated by its concrete multiplicity."""
        v = self.v
        entries = list(relation.entries)
        if isinstance(relation, Sequence):
            if relation.positions is not None:
                positions = [v.concrete(position) for position in relation.positions]
                entries = [entries[i] for i in sorted(range(len(entries)), key=positions.__getitem__)]
            else:
                present = [i for i, entry in enumerate(entries) if v.concrete(entry.weight)]
                keys = [tuple(v.concrete(term) for term in relation.keys[i]) for i in present]
                entries = [entries[present[i]] for i in order(keys, relation.specs)]
        return [entry.row for entry in entries for _ in range(v.concrete(entry.weight))]

    def _forget(self, relation: Relation) -> Bag:
        if isinstance(relation, Bag):
            return relation
        return Bag(relation.schema, relation.entries)

    def _order(self, node, env: Environment, context: TermId, site: int) -> Sequence:
        v = self.v
        child = self.observer.child
        bag = self._forget(self.relation(node.children[0], env, context, child(site, 0)))
        keys = tuple(
            tuple(
                self.apply(function, entry.row, env, v.mul(context, entry.weight), child(site, index + 1))
                for index, function in enumerate(node.children[1:])
            )
            for entry in bag.entries
        )
        return Sequence(node.sort.schema, bag.entries, keys, node.payload.keys)

    def positions(self, sequence: Sequence) -> tuple[TermId, ...]:
        """Sums of the weights of the entries ordered before each entry.

        Ties may be broken in any order SQL permits. This one puts entries
        with stored keys first, in entry order, then open keys grouped by
        key Term, each group in entry order. Stored keys are sorted once;
        open entries are compared per distinct key, not per entry, so the
        work grows with entries times distinct open keys.
        """
        if sequence.positions is not None:
            return sequence.positions
        v = self.v
        entries, keys, specs = sequence.entries, sequence.keys, sequence.specs
        closed = [not self.is_open(key) and not isinstance(self._values(key), Failure) for key in keys]
        stored = [e for e in range(len(entries)) if closed[e]]
        groups: dict[tuple[TermId, ...], list[int]] = {}
        for e in range(len(entries)):
            if not closed[e]:
                groups.setdefault(keys[e], []).append(e)
        weights = {key: v.add(*(entries[f].weight for f in members)) for key, members in groups.items()}
        positions: list[TermId | None] = [None] * len(entries)
        # Stored keys: the weights before them in their order, plus open keys strictly before.
        ranked = [stored[index] for index in order([self._values(keys[e]) for e in stored], specs)]
        sums = self._prefix_sums([entries[e].weight for e in ranked])
        for rank, e in enumerate(ranked):
            later = [v.mul(weight, v.indicator(self._before(key, keys[e], specs, False))) for key, weight in weights.items()]
            positions[e] = v.add(sums[rank], *later)
        # Stored entries with equal keys share one comparison.
        heaps: dict[tuple[TermId, ...], TermId] = {}
        for e in stored:
            heaps[keys[e]] = v.add(heaps.get(keys[e], v.zero), entries[e].weight)
        group_keys = list(groups)
        for rank, key in enumerate(group_keys):
            before = [v.mul(weight, v.indicator(self._before(other, key, specs, True))) for other, weight in heaps.items()]
            before += [
                v.mul(weights[other], v.indicator(self._before(other, key, specs, index < rank)))
                for index, other in enumerate(group_keys) if other != key
            ]
            base = v.add(*before)
            sums = self._prefix_sums([entries[f].weight for f in groups[key]])
            for rank, f in enumerate(groups[key]):
                positions[f] = v.add(base, sums[rank])
        return tuple(positions)

    def _prefix_sums(self, weights: list[TermId]) -> list[TermId]:
        """``sums[i]`` adds ``weights[:i]`` from aligned power-of-two blocks,
        so every sum is O(log n) deep and all of them share their blocks."""
        v = self.v
        blocks: dict[tuple[int, int], TermId] = {}

        def block(start: int, size: int) -> TermId:
            if (start, size) not in blocks:
                half = size // 2
                blocks[start, size] = weights[start] if size == 1 else v.add(block(start, half), block(start + half, half))
            return blocks[start, size]

        sums = []
        for end in range(len(weights) + 1):
            parts, start, size = [], 0, 1 << max(len(weights).bit_length(), 1)
            while start < end:
                if start + size <= end:
                    parts.append(block(start, size))
                    start += size
                size //= 2
            sums.append(v.add(*parts))
        return sums

    def _values(self, key: tuple[TermId, ...]) -> tuple:
        values = tuple(self.v.value(term) for term in key)
        failure = next((value for value in values if isinstance(value, Failure)), None)
        return failure if failure is not None else values

    def _before(self, left: tuple[TermId, ...], right: tuple[TermId, ...], specs, tie: bool) -> TermId:
        """Whether ``left`` sorts strictly before ``right``; equal keys use ``tie``."""
        v = self.v
        result = v.truth(tie)
        for a, b, spec in reversed(list(zip(left, right, specs, strict=True))):
            if spec.direction is Direction.DESC:
                smaller = v.lt3(b, a)
            else:
                smaller = v.lt3(a, b)
            null_first = spec.nulls is NullPlacement.FIRST
            a_null, b_null = v.is_null(a), v.is_null(b)
            first = v.and3(a_null, v.not3(b_null)) if null_first else v.and3(v.not3(a_null), b_null)
            strictly = v.or3(first, v.is_true(smaller))
            result = v.or3(strictly, v.and3(v.same(a, b), result))
        return result

    def _count(self, count: TermId) -> int | None:
        value = self.v.value(count) if self.v.constant(count) else None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _limit(self, sequence: Sequence, lo: int, hi: int | None) -> Sequence:
        """The occurrences at positions ``lo`` to ``hi`` (exclusive; None for no end).

        Only membership in the window matters, not positions. Entries with a
        stored key, or present in the stored data, are ranked once by their
        concrete keys and weights, so stored data is evaluated exactly.
        Candidate rows can move an entry by at most the number of candidate
        rows, so only entries ranked that close to an edge get symbolic
        positions; the others are in or out by rank. An absent entry whose key
        is open, from candidate rows, is in when it sorts after the ranked
        entry at position ``lo - 1`` and before the one at ``hi - 1``, ties
        going to ranked entries; when ranked occurrences end before ``lo``,
        it follows them and the open entries sorting before it, so enough
        candidate rows reach the window. Coverage replays every solution concretely,
        so these bounds only need to hold for the rows a solution activates.
        """
        v = self.v
        entries, keys, specs = sequence.entries, sequence.keys, sequence.specs
        values = [self._values(key) for key in keys]
        ranked = [
            e for e in range(len(entries))
            if not isinstance(values[e], Failure) and (not self.is_open(keys[e]) or v.concrete(entries[e].weight))
        ]
        groups: dict[tuple[TermId, ...], list[int]] = {}
        for e in sorted(set(range(len(entries))) - set(ranked)):
            groups.setdefault(keys[e], []).append(e)
        ranked = [ranked[index] for index in order([values[e] for e in ranked], specs)]
        open_weights = {key: v.add(*(entries[f].weight for f in members)) for key, members in groups.items()}
        sums = self._prefix_sums([entries[e].weight for e in ranked])
        weights: list[TermId | None] = [None] * len(entries)
        edges: dict[int, tuple[TermId, ...]] = {}
        rank = 0  # concrete occurrences before the current entry
        for index, e in enumerate(ranked):
            weight = entries[e].weight
            near = any(edge is not None and edge - self._movers <= rank < edge + self._movers for edge in (lo, hi))
            if near:
                later = [
                    v.mul(total, v.indicator(self._before(key, keys[e], specs, False)))
                    for key, total in open_weights.items()
                ]
                position = v.add(sums[index], *later)
            else:
                position = v.literal(rank, INTEGER)
            weights[e] = self._inside(weight, position, lo, hi)
            count = v.concrete(weight)
            # The entries whose occurrences cover the window's edges.
            for edge in (lo, hi):
                if edge and rank < edge <= rank + count:
                    edges[edge] = keys[e]
            rank += count
        for key, members in groups.items():
            if lo and lo not in edges:
                # Every ranked occurrence lies before the window: an open
                # entry follows them and the open entries sorting before it.
                position = v.add(v.literal(rank, INTEGER), *(
                    v.mul(total, v.indicator(self._before(other, key, specs, False)))
                    for other, total in open_weights.items() if other != key
                ))
                for f in members:
                    weights[f] = self._inside(entries[f].weight, position, lo, hi)
                    position = v.add(position, entries[f].weight)
                continue
            inside = v.true
            if lo:
                inside = self._before(edges[lo], key, specs, True)
            if hi is not None and hi in edges:
                inside = v.and3(inside, self._before(key, edges[hi], specs, False))
            for f in members:
                weights[f] = v.mul(entries[f].weight, v.indicator(inside))
        return Sequence(
            sequence.schema,
            tuple(Entry(entry.row, weight, entry.candidates) for entry, weight in zip(entries, weights, strict=True)),
            keys, specs,
        )

    def _inside(self, weight: TermId, position: TermId, lo: int, hi: int | None) -> TermId:
        """How many of ``weight`` occurrences starting at ``position`` lie in the window."""
        v = self.v
        skipped = v.maximum(v.zero, v.minimum(weight, v.sub(v.literal(lo, INTEGER), position)))
        inside = v.sub(weight, skipped)
        if hi is not None:
            shifted = v.maximum(v.zero, v.sub(position, v.literal(lo, INTEGER)))
            inside = v.maximum(v.zero, v.minimum(inside, v.sub(v.literal(hi - lo, INTEGER), shifted)))
        return inside

    def _take(self, sequence: Sequence, count: TermId) -> Sequence:
        """Occurrences before ``count``: clamp(count - position, 0, weight) per entry."""
        v = self.v
        positions = self.positions(sequence)
        entries = tuple(
            Entry(entry.row, v.maximum(v.zero, v.minimum(entry.weight, v.sub(count, position))), entry.candidates)
            for entry, position in zip(sequence.entries, positions, strict=True)
        )
        return Sequence(sequence.schema, entries, sequence.keys, sequence.specs, positions)

    def _drop(self, sequence: Sequence, count: TermId) -> Sequence:
        v = self.v
        entries = []
        positions = []
        for entry, position in zip(sequence.entries, self.positions(sequence), strict=True):
            skipped = v.maximum(v.zero, v.minimum(entry.weight, v.sub(count, position)))
            entries.append(Entry(entry.row, v.sub(entry.weight, skipped), entry.candidates))
            positions.append(v.maximum(v.zero, v.sub(position, count)))
        return Sequence(sequence.schema, tuple(entries), sequence.keys, sequence.specs, tuple(positions))

    def _window(self, node, env: Environment, context: TermId, site: int) -> Bag:
        v = self.v
        context_ = self.arena.context
        rows = self.occurrences(self.relation(node.children[0], env, context, self.observer.child(site, 0)))
        outputs = [list(row.cells) for row in rows]
        for call in node.payload.calls:
            def evaluate(child_index, row_index):
                value = self.apply(node.children[child_index], rows[row_index], env, context, self.observer.child(site, child_index))
                return v.concrete(value if not isinstance(value, RowValue) else value.cells[0])

            spec = context_.aggregate(call.aggregate) if call.aggregate is not None else None
            values = window(call, len(rows), evaluate, lambda items: concrete_aggregate(spec, items))
            for output, value in zip(outputs, values, strict=True):
                output.append(v.literal(value, call.result.sql_type) if value is not None
                              else v.builder.resolve(v.builder.null(call.result.sql_type)))
        schema = node.payload.output_schema
        return Bag(schema, tuple(Entry(RowValue(schema, tuple(cells)), v.one) for cells in outputs))

    # Aggregation.

    def _group(self, term: TermId, node, env: Environment, context: TermId, site: int) -> Bag:
        v = self.v
        child = self.observer.child
        payload = node.payload
        source = self._forget(self.relation(node.children[0], env, context, child(site, 0)))
        entries = source.entries
        reach = [v.mul(context, entry.weight) for entry in entries]
        grouped = isinstance(node, nodes.GroupFold)
        keys = [
            self.apply(node.children[1], entry.row, env, reach[index], child(site, 1)).cells
            for index, entry in enumerate(entries)
        ] if grouped else [() for _ in entries]
        arguments = {}
        filters = {}
        for call in payload.calls:
            for position, store in ((call.argument_child, arguments), (call.filter_child, filters)):
                if position is not None and position not in store:
                    store[position] = [
                        self.apply(node.children[position], entry.row, env, reach[index], child(site, position))
                        for index, entry in enumerate(entries)
                    ]
        representatives = list(dict.fromkeys(keys)) if grouped else [()]
        open_keys = {key for key in representatives if self.is_open(key)}
        output = []
        for position, key in enumerate(representatives):
            members = []
            for index, entry in enumerate(entries):
                if keys[index] == key:
                    members.append((index, entry.weight))
                elif key in open_keys or keys[index] in open_keys:
                    members.append((index, v.mul(entry.weight, v.indicator(v.same_row(keys[index], key)))))
            count = v.add(*(weight for _, weight in members))
            if grouped:
                earlier = v.or3(*(
                    v.same_row(previous, key) for previous in representatives[:position]
                    if previous in open_keys or key in open_keys
                ))
                exists = v.and3(v.not3(earlier), v.positive(count))
                weight = v.indicator(exists)
                reached = self.present(context) and v.value(earlier) is False
                self.note(site, term, "group", context, reached and self.present(count), lambda exists=exists: exists)
                self.note(site, term, "multiple", context, reached and self.count(count) > 1,
                          lambda earlier=earlier, count=count: v.and3(v.not3(earlier), v.at_least(count, 2)))
            else:
                weight = v.one
                if not self.free(term):
                    self._inputs[term] = count
                reached = self.present(context)
                self._absence(site, term, "empty", context, reached and self.count(count) == 0,
                              lambda count=count: v.eq3(count, v.zero))
                self.note(site, term, "nonempty", context, reached and self.present(count),
                          lambda count=count: v.positive(count))
            values = list(key)
            for call in payload.calls:
                spec = self.arena.context.aggregate(call.aggregate)
                inputs = []
                for index, weight_ in members:
                    if call.filter_child is not None:
                        weight_ = v.mul(weight_, v.indicator(filters[call.filter_child][index]))
                    value = arguments[call.argument_child][index] if call.argument_child is not None else None
                    inputs.append((weight_, value))
                if call.argument_child is not None:
                    self._occurrences(
                        child(site, -1 - payload.calls.index(call)), term, context,
                        [(weight_, (value,)) for weight_, value in inputs], ("null", "duplicate"),
                    )
                values.append(self.aggregates.build(spec, inputs, call.mode is nodes.AggregateMode.DISTINCT))
            candidates = max((entries[index].candidates for index, _ in members), default=0)
            output.append(Entry(RowValue(payload.output_schema, tuple(values)), weight, candidates))
        return Bag(payload.output_schema, tuple(output))

    def _fold(self, node, env: Environment, context: TermId, site: int) -> TermId:
        source = self._forget(self.relation(node.children[0], env, context, self.observer.child(site, 0)))
        spec = self.arena.context.aggregate(node.payload.aggregate)
        return self.aggregates.build(spec, [(entry.weight, entry.row.cells[0]) for entry in source.entries], False)

    def _scalarize(self, term: TermId, node, env: Environment, context: TermId, site: int) -> TermId:
        v = self.v
        source = self._forget(self.relation(node.children[0], env, context, self.observer.child(site, 0)))
        total = v.add(*(entry.weight for entry in source.entries))
        reached = self.present(context)
        self._absence(site, term, "empty", context, reached and self.count(total) == 0, lambda: v.eq3(total, v.zero))
        self.note(site, term, "nonempty", context, reached and self.present(total), lambda: v.positive(total))
        if v.concrete(context) > 0 and v.concrete(total) > 1:
            raise ExecutionError("A scalar subquery returned more than one row")
        sort = node.sort
        null = v.builder.resolve(v.builder.null(sort.sql_type))
        # The first present row, chosen by a balanced tournament of
        # (weight, value) pairs so the Term stays shallow.
        pairs = [(entry.weight, entry.row.cells[0]) for entry in source.entries]
        while len(pairs) > 1:
            pairs = [
                (v.add(pairs[i][0], pairs[i + 1][0]), v.guard(pairs[i][0], pairs[i][1], pairs[i + 1][1]))
                if i + 1 < len(pairs) else pairs[i]
                for i in range(0, len(pairs), 2)
            ]
        result = v.guard(pairs[0][0], pairs[0][1], null) if pairs else null
        # More than one row is a SQL error, which the solver must avoid.
        error = v.apply("sql_error", (), ScalarSort(sort.sql_type, True))
        return v.case(v.at_least(total, 2), error, result)

    def _in_subquery(self, node, env: Environment, context: TermId, site: int) -> TermId:
        v = self.v
        needle = self.scalar(node.children[0], env, context, self.observer.child(site, 0))
        source = self._forget(self.relation(node.children[1], env, context, self.observer.child(site, 1)))
        return v.or3(*(
            v.and3(v.positive(entry.weight), v.eq3(needle, entry.row.cells[0])) for entry in source.entries
        ))


def may_be_unknown(arena, term: TermId) -> bool:
    """Whether a predicate can evaluate to UNKNOWN for some input."""
    pending = [term]
    while pending:
        node = arena[pending.pop()]
        if isinstance(node, (nodes.And3, nodes.Or3, nodes.Not3)):
            pending.extend(node.children)
        elif isinstance(node, (nodes.InSubquery, nodes.Unknown3)):
            return True
        elif not isinstance(node, (nodes.IsNull, nodes.IsNotNull, nodes.IsNotDistinct, nodes.RowIdentityEq,
                                   nodes.True3, nodes.False3)) and any(
            isinstance(arena[child].sort, ScalarSort) and arena[child].sort.nullable for child in node.children
        ):
            return True
    return False


__all__ = ["Execution", "Machine", "Observer", "Unobserved", "UnsupportedQuery", "may_be_unknown"]
