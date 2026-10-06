"""Speculation: seed an instance from a query's compact IR before solving.

Column lineage maps every field of a relational term to the columns of base
relation *occurrences* it copies (through casts, unary functions, MIN and
MAX), is bounded by (AVG) or depends on (through arithmetic, CASE and SUM).
Each reference to a table is its own occurrence, so a self-join or a
subquery over the outer table reads different rows. The query then gives:

- classes of columns that must share values for rows to meet: equi-joins,
  IN, correlated equalities and foreign keys (each occurrence gets
  occurrences of its parents);
- constants compared with a class or a value depending on it, LIKE patterns
  and NULL tests included;
- formulas: the predicates of filters and joins, over atoms ``class op
  constant``, tests on one class, and joint tests on several (``x > g``,
  ``x + g = 9``), each checked by substituting values; an equality between
  members of a class (joins, IN, ``= (SELECT ...)``) is ``NOT class IS NULL``;
- keys: classes used for grouping, window partitions or the output's columns,
  which group the result even without GROUP BY;
- the size of a group: the least number of rows a lower bound on COUNT asks
  for (``COUNT(*) > n`` asks for ``n + 1``);
- the number of groups: a LIMIT matters only when the result exceeds it, so
  ``LIMIT c OFFSET o`` asks for ``o + c + 1`` result rows; a value compared
  strictly with the AVG, MIN or MAX of values it is among asks for two.

CHECK constraints, ENUM lists among them, hold in every goal; storage limits
bound every value. Each batch follows a goal. The positive goal makes every
formula TRUE; a negative goal makes one atom FALSE or UNKNOWN; further goals
repeat a group of output rows and make a projected class NULL. A goal assigns
only what its outcome needs: an OR is TRUE when one item is. Its atoms narrow
a ``Space`` per class, the value spaces the CSP solves with. The classes of a
joint test are chosen together, from their candidates or, for a predicate
candidates cannot meet, by solving the test alone for them. Classes outside
the goal take NULL, constants of the query and their neighbours, values
already stored, or fresh values from the configured provider, which also
fills the columns the query never mentions.

Occurrences met only under negation (NOT, NOT EXISTS, NOT IN, anti-joins)
are negative: their rows can only block output. The positive goal leaves
them out; another goal includes them, and so do the negative goals of atoms
over them. Occurrences on the NULL-padded side of outer joins are optional:
one goal leaves them out, so rows without matches occur. A batch adds groups
of tuples, one row per occurrence, closed under foreign-key parents; rows of
one relation that repeat a stored key are left out, the stored row taking
their place. Constant classes keep the first tuple's values through the
batch, key classes through a group.

A batch is kept when its rows satisfy integrity and concrete execution makes
the output productive and covers a new outcome without losing one. Until the
output is productive only positive goals are tried: the plain one, then one
per hint of each class that only receives hints, pinned to it. The values of
the batch that produced output hold for hint-only classes in later goals.
Like the concolic generator, each goal is tried once per kept instance, so
speculation ends when every goal fails on the current instance; the
generator then solves only for the outcomes it missed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice, product
from random import Random
from typing import TYPE_CHECKING

from parseval.catalog import Catalog
from parseval.instance import ExecutionError, Instance, Slot, Valuation
from parseval.instance.constraints import Integrity
from parseval.instance.domain import Domain, Provider, Space, capacity, carrier, fits, placeholder, sequential
from parseval.smt import Status, solve
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.constraints import CheckDecl, ForeignKeyDecl, PrimaryKeyDecl, UniqueDecl
from parseval.terms.context import AggregateKind
from parseval.terms.names import RelationId
from parseval.terms.sorts import BagSort, ScalarSort, SeqSort
from parseval.terms.terms import TermId

if TYPE_CHECKING:
    from parseval.generator.coverage import Coverage, Target

# A column of one occurrence of a relation in the query: (occurrence, position).
# Two references to one table (a self-join, or a subquery over the outer
# table) are different occurrences with different rows.
Column = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _Bounds:
    """An aggregate (AVG) that satisfies a comparison when all of the column's values do."""

    column: Column


@dataclass(frozen=True, slots=True)
class _Depends:
    """A value computed from a column; constants it is compared with hint at the column's values."""

    column: Column


# A field's origin: the base columns it copies, those it is bounded by
# (``_Bounds``) or depends on (``_Depends``), and the kind of aggregate
# computing it. Copies join classes; copies and bounds form atoms; all three
# receive constants.
Origin = frozenset
Evaluate = Callable[[Instance], "Coverage"]


@dataclass(frozen=True, slots=True)
class Atom:
    """``column op value`` with op one of = < > null like; test: a predicate
    on this column alone, ``value`` = (arena, term, fields), checked by substituting
    the column's value for those fields; reuse and differ: equal to, or
    non-NULL and different from, the value of a row known to be in the output."""

    column: Column
    op: str
    value: object = None


@dataclass(frozen=True, slots=True)
class Joint:
    """A predicate over several columns, checked by substituting their values
    together: ``fields`` maps each (depth, index) field to its column."""

    columns: tuple[Column, ...]
    arena: TermArena
    term: TermId
    fields: tuple[tuple[tuple[int, int], Column], ...]


@dataclass(slots=True)
class _Plan:
    """What a goal asks of a batch: a narrowed space per class, tests on one
    class (checked by substituting a value), and joint tests over several."""

    spaces: dict
    tests: dict
    joints: list


# A formula is None (outside the atoms), an Atom or a Joint, or
# ("and" | "or" | "not", items).
_NEGATE = {"=": "!=", "<": ">=", ">": "<=", "like": "notlike", "null": "notnull"}
_FLIP = {"true": "false", "false": "true", "unknown": "unknown"}
_TRUTH = {"true": True, "false": False, "unknown": None}

_EQUALITIES = (nodes.Eq3, nodes.IsNotDistinct)
_SAME_ROWS = (nodes.Filter, nodes.Distinct, nodes.OrderBy, nodes.ForgetOrder, nodes.TopK,
              nodes.Take, nodes.Drop, nodes.Slice, nodes.SemiJoin, nodes.AntiJoin)
_PAIRS = (nodes.Join, nodes.LeftJoin, nodes.RightJoin, nodes.FullJoin, nodes.Product)
_EXTREMES = frozenset({AggregateKind.AVG, AggregateKind.MIN, AggregateKind.MAX})


class _Lineage:
    """Walks a compact query, collecting classes, constants, keys and row demand."""

    def __init__(self, arena: TermArena, catalog: Catalog):
        self.arena = arena
        self.context = catalog.context
        self.parent: dict[Column, Column] = {}
        self.constants: dict[Column, list[object]] = {}
        self.keys: set[Column] = set()
        # The relation of each occurrence, and where it lies: "positive",
        # "negative" (under negation) or "optional" (the NULL-padded side of
        # an outer join).
        self.occurrences: dict[int, RelationId] = {}
        self.polarity: dict[int, str] = {}
        self.negated = False
        self.optional = False
        self.formulas: list = []
        # Columns aggregates take as arguments, where duplicates and NULLs matter.
        self.arguments: set[Column] = set()
        # Output origins of subqueries, which formulas compare with.
        self.subqueries: dict[TermId, Origin] = {}
        # Rows per group and groups in the result.
        self.size = 1
        self.groups = 1
        # Columns of values compared strictly with an AVG, MIN or MAX, and of its argument.
        self.extremes: list[tuple[frozenset[Column], frozenset[Column]]] = []

    def occurrence(self, relation: RelationId, state: str) -> int:
        occurrence = len(self.occurrences)
        self.occurrences[occurrence] = relation
        self.polarity[occurrence] = state
        return occurrence

    def find(self, column: Column) -> Column:
        parent = self.parent.setdefault(column, column)
        if parent != column:
            parent = self.parent[column] = self.find(parent)
        return parent

    def union(self, columns) -> None:
        columns = list(columns)
        for column in columns[1:]:
            self.parent[self.find(column)] = self.find(columns[0])

    # Relations.

    def relation(self, term: TermId, rows: tuple, rels: tuple) -> tuple[Origin, ...]:
        node = self.arena[term]
        if isinstance(node, nodes.Base):
            relation = node.payload.relation
            occurrence = self.occurrence(relation, "negative" if self.negated else "optional" if self.optional else "positive")
            return tuple(Origin({(occurrence, index)}) for index in range(self._width(self.context.relation(relation).schema)))
        if isinstance(node, nodes.RelVar):
            # Each reference to a CTE reads its own rows: walk its definition again.
            definition, scope_rows, scope_rels = rels[-1 - node.payload.depth]
            return self.relation(definition, scope_rows, scope_rels)
        if isinstance(node, nodes.LetRel):
            return self.relation(node.children[1], rows, (*rels, (node.children[0], rows, rels)))
        # Outer joins keep rows of one side without matches on the other.
        padded = {nodes.LeftJoin: (1,), nodes.RightJoin: (0,), nodes.FullJoin: (0, 1)}.get(type(node), ())
        sources = []
        for position, child in enumerate(child for child in node.children if self._relational(child)):
            optional, self.optional = self.optional, self.optional or position in padded
            sources.append(self.relation(child, rows, rels))
            self.optional = optional
        inputs = sum(sources, ())
        # The inner rows of an anti-join can only remove outer rows.
        negate = isinstance(node, nodes.AntiJoin)
        self.negated ^= negate
        bodies = [
            self.body(child, (*rows, inputs), rels)
            for child in node.children
            if isinstance(self.arena[child], nodes.RowLambda)
        ]
        self.negated ^= negate
        for child in node.children:
            if not self._relational(child) and not isinstance(self.arena[child], nodes.RowLambda):
                self.scalar(child, rows, rels)
        if isinstance(node, (nodes.Filter, *_PAIRS)) and isinstance(self.arena[node.children[-1]], nodes.RowLambda):
            self.formulas.append((self.negated, self.formula(self.arena[node.children[-1]].children[0], (*rows, inputs))))
        self._limit(node)
        if isinstance(node, _SAME_ROWS):
            return sources[0]
        if isinstance(node, (nodes.Map, nodes.SeqMap)):
            return bodies[0]
        if isinstance(node, _PAIRS):
            return inputs
        if isinstance(node, (nodes.DependentJoin, nodes.DependentLeftJoin)):
            return inputs + bodies[0]
        if isinstance(node, nodes.UnionAll):
            return tuple(left | right for left, right in zip(*sources))
        if isinstance(node, nodes.GroupFold):
            self.keys.update(column for origin in bodies[0] for column in _columns(origin))
            return bodies[0] + self._aggregates(node.payload.calls, bodies)
        if isinstance(node, nodes.GlobalFold):
            return self._aggregates(node.payload.calls, bodies)
        if isinstance(node, nodes.Window):
            for call in node.payload.calls:
                for index in call.partition_children:
                    self.keys.update(_columns(bodies[index - 1]))
            width = self._width(node.payload.output_schema)
            return inputs + (Origin(),) * (width - len(inputs))
        return (Origin(),) * self._width(node.sort.schema)

    def body(self, term: TermId, rows: tuple, rels: tuple):
        """A lambda body: a relation, a row of fields, or one value."""
        body = self.arena[term].children[0]
        node = self.arena[body]
        if self._relational(body):
            return self.relation(body, rows, rels)
        if isinstance(node, nodes.Row):
            return tuple(self.scalar(child, rows, rels) for child in node.children)
        return self.scalar(body, rows, rels)

    def _limit(self, node) -> None:
        if isinstance(node, nodes.TopK):
            offset, count = (self._literal(child) for child in node.children[1:3])
        elif isinstance(node, nodes.Take):
            offset, count = 0, self._literal(node.children[0])
        elif isinstance(node, nodes.Drop):
            offset, count = self._literal(node.children[0]), None
        elif isinstance(node, nodes.Slice):
            offset, count = (self._literal(child) for child in node.children[:2])
        else:
            return
        self.groups = max(self.groups, (offset or 0) + (count or 0) + 1)

    def _aggregates(self, calls, bodies) -> tuple[Origin, ...]:
        """MIN and MAX return one of their argument's values, AVG lies within them, SUM depends on them."""
        origins = []
        for call in calls:
            kind = self.context.aggregate(call.aggregate).kind
            origin = Origin({kind})
            if call.argument_child is not None:
                # Lambdas follow the source, the only relational child.
                argument = bodies[call.argument_child - 1]
                self.arguments |= _columns(argument)
                if kind in (AggregateKind.MIN, AggregateKind.MAX):
                    origin |= argument
                elif kind is AggregateKind.AVG:
                    origin |= Origin(_Bounds(item) if isinstance(item, tuple) else item
                                     for item in argument if isinstance(item, (tuple, _Bounds, _Depends)))
                elif kind is AggregateKind.SUM:
                    origin |= _depends(argument)
            origins.append(origin)
        return tuple(origins)

    # Scalars.

    def scalar(self, term: TermId, rows: tuple, rels: tuple) -> Origin:
        node = self.arena[term]
        if isinstance(node, nodes.Field):
            row = self.arena[node.children[0]]
            if isinstance(row, nodes.RowVar):
                return rows[-1 - row.payload.depth][node.payload.index]
            if isinstance(row, nodes.Row):
                return self.scalar(row.children[node.payload.index], rows, rels)
        if isinstance(node, nodes.Scalarize):
            origin = self.subqueries[term] = self.relation(node.children[0], rows, rels)[0]
            return origin
        if isinstance(node, nodes.Not3):
            self.negated = not self.negated
            self.scalar(node.children[0], rows, rels)
            self.negated = not self.negated
            return Origin()
        if isinstance(node, nodes.InSubquery):
            value = self.scalar(node.children[0], rows, rels)
            members = self.subqueries[node.children[1]] = self.relation(node.children[1], rows, rels)[0]
            self.compare(node.children[0], value, members, nodes.Eq3)
            return Origin()
        origins = [
            self.relation(child, rows, rels) if self._relational(child) else self.scalar(child, rows, rels)
            for child in node.children
        ]
        if isinstance(node, (*_EQUALITIES, nodes.Lt3)):
            self.compare(node.children[0], origins[0], origins[1], type(node), node.children[1])
        elif isinstance(node, (nodes.Like3, nodes.ILike3)):
            pattern = self._literal(node.children[1])
            if isinstance(pattern, str):
                self._constant(origins[0], pattern)
        elif isinstance(node, (nodes.IsNull, nodes.IsNotNull)):
            self._constant(origins[0], None)
        elif isinstance(node, nodes.ScalarCall) and len(origins) == 1:
            # Casts and unary functions keep the values their argument compares with.
            return origins[0]
        elif isinstance(node, (nodes.ScalarCall, nodes.Case)):
            return Origin().union(*(_depends(origin) for origin in origins if isinstance(origin, frozenset)))
        return Origin()

    def compare(self, left: TermId, a: Origin, b: Origin, kind, right: TermId | None = None) -> None:
        if kind in _EQUALITIES and _columns(a) and _columns(b):
            self.union(_columns(a) | _columns(b))
        if kind is nodes.Lt3 and not self.negated:
            # A value strictly beyond the AVG, MIN or MAX of values it is
            # among needs another row with a different value.
            for value, aggregate in ((a, b), (b, a)):
                if aggregate & _EXTREMES and _related(value) and _related(aggregate):
                    self.extremes.append((_related(value), _related(aggregate)))
        if right is None:
            return
        for term, origin in ((left, b), (right, a)):
            node = self.arena[term]
            if not isinstance(node, nodes.Literal):
                continue
            self._constant(origin, node.payload.value)
            if AggregateKind.COUNT in origin and isinstance(node.payload.value, int):
                self._count(node.payload.value, kind, term == left)

    def _count(self, n: int, kind, literal_first: bool) -> None:
        """The group size a lower bound on COUNT asks for; upper bounds hold for one row."""
        if kind is nodes.Lt3:
            # ``n < COUNT`` is a lower bound; under negation ``COUNT < n`` is.
            least = n + 1 if literal_first and not self.negated else n if not literal_first and self.negated else 1
        else:
            least = 1 if self.negated else n
        self.size = max(self.size, least)

    # Predicates as formulas over atoms of one column.

    def formula(self, term: TermId, rows: tuple):
        node = self.arena[term]
        if isinstance(node, (nodes.And3, nodes.Or3)):
            kind = "and" if isinstance(node, nodes.And3) else "or"
            return (kind, tuple(self.formula(child, rows) for child in node.children))
        if isinstance(node, nodes.Not3):
            return ("not", self.formula(node.children[0], rows))
        if isinstance(node, (nodes.IsNull, nodes.IsNotNull)):
            column = self._column(node.children[0], rows)
            atom = None if column is None else Atom(column, "null")
            return atom if isinstance(node, nodes.IsNull) or atom is None else ("not", atom)
        if isinstance(node, (nodes.Like3, nodes.ILike3)):
            column, pattern = self._column(node.children[0], rows), self._literal(node.children[1])
            return None if column is None or not isinstance(pattern, str) else Atom(column, "like", pattern)
        if isinstance(node, (nodes.Eq3, nodes.InSubquery)):
            # Columns compared for equality share a class, so only NULL can
            # stop them from being equal: the equality is ``NOT class IS NULL``.
            left, right = (self._column(child, rows) for child in node.children)
            if left is not None and right is not None:
                return ("not", Atom(left, "null"))
        if isinstance(node, (*_EQUALITIES, nodes.Lt3)):
            for side, (operand, other) in enumerate((node.children, node.children[::-1])):
                column, literal = self._column(operand, rows), self.arena[other]
                if column is not None and isinstance(literal, nodes.Literal):
                    op = "<>"[side] if isinstance(node, nodes.Lt3) else "="
                    return Atom(column, op, literal.payload.value)
        return self._test(term, rows)

    def _test(self, term: TermId, rows: tuple) -> Atom | Joint | None:
        """A predicate without subqueries whose every field is one column: a
        test atom on one column, or a joint test on several."""
        fields: dict[tuple[int, int], Column] = {}
        for item in self.arena.post_order((term,)):
            node = self.arena[item]
            if self._relational(item) or isinstance(node, (nodes.RowLambda, nodes.Scalarize, nodes.InSubquery)):
                return None
            if isinstance(node, nodes.Field) and isinstance(self.arena[node.children[0]], nodes.RowVar):
                depth = self.arena[node.children[0]].payload.depth
                columns = _bounded(rows[-1 - depth][node.payload.index])
                if len(columns) != 1:
                    return None
                fields[depth, node.payload.index] = next(iter(columns))
        columns = tuple(sorted(set(fields.values())))
        if not columns:
            return None
        if len(columns) == 1:
            return Atom(columns[0], "test", (self.arena, term, frozenset(fields)))
        return Joint(columns, self.arena, term, tuple(sorted(fields.items())))

    def _column(self, term: TermId, rows: tuple) -> Column | None:
        """The one base column a value or subquery copies or is bounded by.

        Functions of a column are not the column: predicates over them are tests.
        """
        node = self.arena[term]
        if isinstance(node, nodes.Field) and isinstance(self.arena[node.children[0]], nodes.RowVar):
            columns = _bounded(rows[-1 - self.arena[node.children[0]].payload.depth][node.payload.index])
        elif term in self.subqueries:
            columns = _bounded(self.subqueries[term])
        else:
            return None
        return next(iter(columns)) if len(columns) == 1 else None

    def _constant(self, origin: Origin, value) -> None:
        for column in _columns(origin) | {item.column for item in origin if isinstance(item, (_Bounds, _Depends))}:
            self.constants.setdefault(column, []).append(value)

    def _relational(self, term: TermId) -> bool:
        return isinstance(self.arena[term].sort, (BagSort, SeqSort))

    def _width(self, schema) -> int:
        return len(self.context.schema(schema).fields)

    def _literal(self, term: TermId):
        node = self.arena[term]
        return node.payload.value if isinstance(node, nodes.Literal) else None


def _room(binding) -> float:
    """How many values a column's storage holds; unbounded columns hold the most."""
    size = capacity(binding.storage_type)
    return float("inf") if size is None else size


def _columns(origin: Origin) -> frozenset[Column]:
    return frozenset(item for item in origin if isinstance(item, tuple))


def _related(origin: Origin) -> frozenset[Column]:
    """Columns a value copies, is bounded by or depends on."""
    return _columns(origin) | {item.column for item in origin if isinstance(item, (_Bounds, _Depends))}


def _bounded(origin: Origin) -> frozenset[Column]:
    """Columns whose atoms hold for the value: those it copies or is bounded by."""
    return _columns(origin) | {item.column for item in origin if isinstance(item, _Bounds)}


def _depends(origin: Origin) -> Origin:
    """What a value computed from ``origin`` depends on."""
    return Origin(_Depends(item if isinstance(item, tuple) else item.column)
                  for item in origin if isinstance(item, (tuple, _Bounds, _Depends)))


def _atoms(formula):
    if isinstance(formula, (Atom, Joint)):
        yield formula
    elif formula is not None:
        kind, items = formula
        for item in (items,) if kind == "not" else items:
            yield from _atoms(item)


def _resolve(formula, classes: dict[Column, Column]):
    """The formula over value classes instead of base columns."""
    if isinstance(formula, Atom):
        return Atom(classes[formula.column], formula.op, formula.value)
    if isinstance(formula, Joint):
        return _resolve_joint(formula, classes)
    if formula is None:
        return None
    kind, items = formula
    if kind == "not":
        return (kind, _resolve(items, classes))
    return (kind, tuple(_resolve(item, classes) for item in items))


def _classes(atom) -> tuple[Column, ...]:
    """The classes an atom or joint test constrains."""
    return atom.columns if isinstance(atom, Joint) else (atom.column,)


def _resolve_joint(joint: Joint, classes: dict[Column, Column]) -> Joint:
    """A joint test over classes; each field keeps its column for its sort."""
    return Joint(
        tuple(sorted({classes[column] for column in joint.columns})), joint.arena, joint.term,
        tuple((field, column) for field, column in joint.fields),
    )


class Speculator:
    """Coverage-greedy sampling of rows for the relations of one query."""

    def __init__(
        self, empty: Instance, arena: TermArena, root: TermId,
        seed: int = 0, provider: Provider = sequential, timeout_ms: int = 5_000,
    ):
        """``seed`` makes sampling reproducible; ``provider`` supplies fresh
        values; ``timeout_ms`` bounds a solver call for a joint test."""
        catalog = empty.catalog
        self.empty = empty
        self.arena = arena
        self.valuation = Valuation(empty)
        self.provider = provider
        self.timeout_ms = timeout_ms
        self.rng = Random(seed)
        tables = {table.relation: table for table in catalog.tables()}
        lineage = _Lineage(arena, catalog)
        # The output's columns group its rows as GROUP BY keys do: equal values
        # make duplicate rows, new values distinct ones.
        projection = {column for origin in lineage.relation(root, (), ()) for column in _columns(origin)}
        # Foreign keys: every occurrence gets occurrences of its parents, which
        # lie where it lies, with their columns joined. A self-reference stays
        # within the occurrence (a row may reference itself), and a cycle
        # reuses the ancestor occurrence.
        self.parents: dict[int, set[int]] = {}
        read = set(lineage.occurrences)
        pending = [(occurrence, {}) for occurrence in list(lineage.occurrences)]
        while pending:
            occurrence, ancestors = pending.pop()
            table = tables[lineage.occurrences[occurrence]]
            ancestors = {**ancestors, table.relation: occurrence}
            parents = self.parents[occurrence] = set()
            for item in table.constraints:
                if not isinstance(item, ForeignKeyDecl) or not item.metadata.proof_active:
                    continue
                parent = tables[item.target_relation]
                known = ancestors.get(parent.relation)
                target = known if known is not None else lineage.occurrence(parent.relation, lineage.polarity[occurrence])
                if target != occurrence:
                    parents.add(target)
                for source, column in zip(item.source, item.target, strict=True):
                    lineage.union(((occurrence, table.spec.column_position(source)),
                                   (target, parent.spec.column_position(column))))
                if known is None:
                    pending.append((target, ancestors))
        self.relation_of = dict(lineage.occurrences)
        self.occurrences = sorted(self.relation_of)
        self.size = lineage.size
        self.groups = lineage.groups
        # One row is never strictly beyond the AVG, MIN or MAX of values it is
        # among, read from the same table column or joined to it: such a
        # comparison needs two groups, which differ in value.
        def among(columns):
            return {lineage.find(column) for column in columns} | {
                (self.relation_of[occurrence], position) for occurrence, position in columns
            }

        if any(among(value) & among(aggregate) for value, aggregate in lineage.extremes):
            self.groups = max(self.groups, 2)
        relations = set(self.relation_of.values())
        self.fields = {relation: catalog.context.schema(tables[relation].schema).fields for relation in relations}
        self.unique = {
            relation: [
                tuple(tables[relation].spec.column_position(column) for column in item.columns)
                for item in tables[relation].constraints
                if isinstance(item, (PrimaryKeyDecl, UniqueDecl)) and item.metadata.proof_active
            ]
            for relation in relations
        }
        # A class is named by its root column and samples values of its first column.
        self.classes: dict[Column, Column] = {}
        self.columns = {}
        self.storages: dict[Column, list] = {}
        self.domains: dict[Column, Domain] = {}
        self.nullable: dict[Column, bool] = {}
        for occurrence in self.occurrences:
            table = tables[self.relation_of[occurrence]]
            for position, field in enumerate(self.fields[table.relation]):
                cls = self.classes[occurrence, position] = lineage.find((occurrence, position))
                binding = table.columns[position]
                self.storages.setdefault(cls, []).append(binding.storage_type)
                # Fresh values come from the column with the least room, so they fit them all.
                current = self.columns.get(cls)
                if current is None or _room(binding) < _room(current[1]):
                    self.columns[cls] = (table, binding)
                self.domains.setdefault(cls, Domain.of(field))
                self.nullable[cls] = self.nullable.get(cls, False) or field.nullable
        self.pools: dict[Column, list] = {}
        for column, constants in lineage.constants.items():
            self._hint(self.classes[column], constants)
        # CHECK constraints (ENUM lists among them) hold in every goal: as
        # tests on one column, checked by substituting a value; their
        # constants are hints like the query's.
        self.checks: list[tuple[Atom, str]] = []
        for occurrence in self.occurrences:
            table = tables[self.relation_of[occurrence]]
            row = (tuple(Origin({(occurrence, index)}) for index in range(len(self.fields[table.relation]))),)
            for item in table.constraints:
                if not isinstance(item, CheckDecl) or not item.metadata.proof_active:
                    continue
                check = _Lineage(catalog.constraint_arena, catalog)
                body = catalog.constraint_arena[item.predicate.term].children[0]
                check.scalar(body, row, ())
                for column, constants in check.constants.items():
                    self._hint(self.classes[column], constants)
                atom = check._test(body, row)
                if isinstance(atom, Atom):
                    self.checks.append((Atom(self.classes[atom.column], "test", atom.value), "true"))
                elif isinstance(atom, Joint):
                    self.checks.append((_resolve_joint(atom, self.classes), "true"))
        self.keyed = {self.classes[column] for column in lineage.keys | projection}
        # Duplicates and NULLs matter in the output and in aggregate arguments
        # (COUNT(x) against COUNT(DISTINCT x) and COUNT(*)).
        self.aggregated = {self.classes[column] for column in lineage.arguments}
        projected = {self.classes[column] for column in projection}
        repeated = projected | self.aggregated
        # When every column of a class alone forms a key, a repeated value adds
        # no row anywhere, so its values must be new. A class with another
        # column (a foreign key) repeats values so that new rows meet stored ones.
        keys = {
            (occurrence, key[0])
            for occurrence in self.occurrences
            for key in self.unique[self.relation_of[occurrence]] if len(key) == 1
        }
        self.distinct = set(self.classes.values())
        for column, cls in self.classes.items():
            if column not in keys:
                self.distinct.discard(cls)
        self.used: dict[Column, list] = {}
        # Parents the query never reads only satisfy foreign keys: classes
        # within them and their references keep one value, so each is stored
        # once and later rows reference it. Classes the query constrains,
        # and keys of the rows it reads, vary as usual.
        constrained = self.keyed | self.aggregated | projected | set(self.pools) | {
            cls for formula in lineage.formulas for atom in _atoms(_resolve(formula[1], self.classes))
            for cls in _classes(atom)
        } | {cls for atom, _ in self.checks for cls in _classes(atom)}
        read_keys = {
            self.classes[occurrence, position] for occurrence in read
            for key in self.unique[self.relation_of[occurrence]] for position in key
        }
        self.reused = {
            cls for (occurrence, _), cls in self.classes.items() if occurrence not in read
        } - constrained - read_keys
        self.reuse: dict[Column, object] = {}
        # Values of the latest batch that produced output, and the pins that
        # keep its hint-only classes in later goals.
        self.anchors: dict[Column, tuple[Atom, str]] = {}
        self.productive: dict[Column, object] = {}
        # Values in use per kind, so fresh values differ from every stored one.
        self.taken: dict = {}
        self.formulas = [_resolve(formula, self.classes) for _, formula in lineage.formulas]
        negative = {occurrence for occurrence, state in lineage.polarity.items() if state == "negative"}
        optional = {occurrence for occurrence, state in lineage.polarity.items() if state == "optional"}
        # Occurrences of a batch: without negative ones, also without the
        # padded sides of outer joins, or all of them.
        self.scopes = {
            "base": self._closure(set(self.occurrences) - negative),
            "inner": self._closure(set(self.occurrences) - negative - optional),
            "all": self._closure(self.occurrences),
        }
        atoms = {}
        for negated, formula in lineage.formulas:
            for atom in _atoms(_resolve(formula, self.classes)):
                atoms[atom] = atoms.get(atom, False) or negated
        # A goal is (forced literal, scope of occurrences, rows per group,
        # whether aggregate arguments repeat in a group): the positive goal,
        # the goals leaving out padded sides or adding negative occurrences,
        # repeated output rows and aggregate arguments, each outcome of each
        # atom (TRUE, FALSE, UNKNOWN if NULL is allowed: coverage records
        # every part of a predicate), and a NULL output column or argument.
        # Positive goals: no forced literal, or a class that only receives
        # hints (constants compared with values depending on it) pinned to
        # each hint in turn, as in HAVING SUM(x) / COUNT(*) > 400.
        constrained = {cls for atom in atoms for cls in _classes(atom)}
        self.hinted = [cls for cls in self.pools if cls not in constrained]
        self.positives = list(dict.fromkeys([
            (None, "base", self.size, False),
            *(((Atom(cls, "=", value), "true"), "base", self.size, False)
              for cls in self.hinted for value in self.pools[cls] if value is not None),
        ]))
        self.projected = projected
        self.goals = list(dict.fromkeys([
            *self.positives,
            (None, "inner" if optional else "base", self.size, False),
            (None, "all" if negative else "base", self.size, False),
            (None, "base", max(self.size, 2), True),
            *(((atom, outcome), "all" if negated else "base", self.size, False)
              for atom, negated in atoms.items()
              for outcome in ("true", "false", "unknown")
              if outcome != "unknown" or any(self.nullable[cls] for cls in _classes(atom))),
            *(((Atom(cls, "null"), "true"), "base", self.size, False) for cls in repeated if self.nullable[cls]),
            # An output column's outcomes: NULL (above), a duplicate (a new
            # group equal to an output row in that column and different in
            # another) and a distinct value (a new group different there).
            *(((Atom(cls, "reuse"), "true"), "base", self.size, False)
              for cls in sorted(projected - self.distinct) if len(projected) > 1),
            *(((Atom(cls, "differ"), "true"), "base", self.size, False) for cls in sorted(projected)),
        ]))

    def _hint(self, cls: Column, constants) -> None:
        """Constants and their neighbours that fit every column of the class."""
        pool = self.pools.setdefault(cls, [])
        for constant in constants:
            values = [None] if constant is None else self.domains[cls].around(constant)
            pool.extend(value for value in values if value not in pool and self._fits(cls, value))

    def _fits(self, cls: Column, value) -> bool:
        return all(fits(value, storage) for storage in self.storages[cls])

    def run(self, evaluate: Evaluate, output: Target, deadline: float | None = None) -> Instance:
        """The first kept batch makes the output productive; later ones lose nothing.

        At the ``deadline`` (a ``time.monotonic()`` value) the current
        instance is returned.

        Stored rows are permanent, so rows that do not produce output could
        block it, as a row of ``s`` blocks ``NOT EXISTS (SELECT 1 FROM s)``.
        Without productive output the empty instance is returned.
        """
        instance = self.empty
        try:
            covered = evaluate(instance).covered
        except ExecutionError:
            return instance
        tried: set = set()
        while True:
            goals = self.goals if output in covered else self.positives
            goal = next((goal for goal in goals if goal not in tried), None)
            if goal is None or deadline is not None and time.monotonic() >= deadline:
                return instance
            tried.add(goal)
            literal, scope, size, repeat = goal
            groups = self.groups if output not in covered else 1
            # Classes whose values must be new cannot repeat within a group.
            keyed = (self.keyed | self.aggregated if repeat else self.keyed) - self.distinct
            # A goal about aggregate arguments (repeated or NULL) forms its own
            # group, so the aggregate sees only the goal's rows.
            own = repeat or literal is not None and isinstance(literal[0], Atom) and literal[0].column in self.aggregated
            candidate, slots, values = self._batch(
                instance, groups, size, keyed, self._plan(literal), self.scopes[scope], own
            )
            coverage = self._evaluate(candidate, slots, evaluate)
            if (
                coverage is None
                or output not in coverage.covered
                or not coverage.covered - covered
                or (output in covered and covered - coverage.covered)
            ):
                continue
            if output not in covered:
                tried.clear()
                # This batch made the output productive: reuse goals repeat
                # its values, and classes that only receive hints keep theirs.
                self.productive = dict(values)
                self.anchors = {
                    cls: (Atom(cls, "=", self.productive[cls]), "true")
                    for cls in self.hinted if self.productive.get(cls) is not None
                }
            instance, covered = candidate, coverage.covered
            for cls, value in values:
                self.used.setdefault(cls, []).append(value)

    def _evaluate(self, instance: Instance, slots: list[Slot], evaluate: Evaluate) -> Coverage | None:
        if not slots:
            return None
        cells = {parameter for slot in slots for parameter in slot.parameters}
        if Integrity(Valuation(instance)).constraints(slots, cells):
            return None
        try:
            return evaluate(instance)
        except ExecutionError:
            return None

    # Goals.

    def _plan(self, goal) -> _Plan:
        """Spaces and tests making every formula TRUE, except the atom of a
        negative goal."""
        prefer = None if goal is None else goal[0]
        literals = [literal for formula in self.formulas for literal in self._want(formula, "true", prefer)]
        literals += self.checks
        literals += [anchor for cls, anchor in self.anchors.items() if prefer is None or cls not in _classes(prefer)]
        if isinstance(prefer, Atom) and prefer.op == "reuse":
            # Equal in one output column, different in the others.
            literals += [(Atom(cls, "differ"), "true") for cls in self.projected if cls != prefer.column]
        if goal is not None:
            literals = [(atom, goal[1] if atom == prefer else truth) for atom, truth in literals]
            if all(atom != prefer for atom, _ in literals):
                literals.append(goal)
        joints = [(atom, truth) for atom, truth in literals if isinstance(atom, Joint)]
        spaces: dict[Column, Space | None] = {}
        tests: dict[Column, list] = {}
        for atom, truth in literals:
            if isinstance(atom, Joint):
                continue
            cls = atom.column
            if cls not in spaces:
                spaces[cls] = Space(
                    self.domains[cls].kind, self.nullable[cls],
                    case_insensitive=self.valuation.runtime.semantics.case_insensitive_text,
                )
            if atom.op == "test" and truth != "unknown":
                tests.setdefault(cls, []).append((atom, truth))
                continue
            space = spaces[cls]
            # Contradicting literals leave their class to sampling.
            if space is not None and not all(space.narrow(op, value) for op, value in self._ops(atom, truth)):
                spaces[cls] = None
        return _Plan({cls: space for cls, space in spaces.items() if space is not None}, tests, joints)

    def _want(self, formula, truth: str, prefer: Atom | None) -> list[tuple[Atom, str]]:
        """Outcomes of atoms that give a formula ``truth``; a choice includes ``prefer`` if it can.

        Only what the outcome needs is assigned: AND is TRUE when all items
        are and FALSE when one is; OR the other way round. UNKNOWN needs one
        UNKNOWN item and the others TRUE under AND, FALSE under OR.
        """
        if formula is None:
            return []
        if isinstance(formula, (Atom, Joint)):
            return [(formula, truth)]
        kind, items = formula
        if kind == "not":
            return self._want(items, _FLIP[truth], prefer)
        identity = "true" if kind == "and" else "false"
        if truth == identity:
            return [literal for item in items for literal in self._want(item, truth, prefer)]
        choices = [item for item in items if item is not None]
        if not choices:
            return []
        chosen = next((item for item in choices if prefer in _atoms(item)), None) or self.rng.choice(choices)
        if truth != "unknown":
            return self._want(chosen, truth, prefer)
        return [literal for item in items for literal in self._want(item, truth if item is chosen else identity, prefer)]

    def _ops(self, atom: Atom, truth: str) -> list[tuple[str, object]]:
        if truth == "unknown":
            return [("null", None)]
        if atom.op == "null":
            return [("null" if truth == "true" else "notnull", None)]
        if atom.op == "reuse":
            value = self.productive.get(atom.column)
            return [] if value is None else [("=", value)]
        if atom.op == "differ":
            value = self.productive.get(atom.column)
            return [("notnull", None)] + ([] if value is None else [("!=", value)])
        value = atom.value if atom.op == "like" else next(self.domains[atom.column].around(atom.value), None)
        if value is None:
            return []
        return [("notnull", None), (atom.op if truth == "true" else _NEGATE[atom.op], value)]

    def _pick(self, cls: Column, plan: _Plan):
        """The class's usual sample if the plan admits it, else a random value of its space."""
        space = plan.spaces[cls]
        if space.null is True:
            return None
        taken = self.taken.get(space.kind, ()) if cls in self.distinct else ()
        sample = self._sample(cls)
        if sample is not None and sample not in taken and self._admits(cls, sample, plan):
            return sample
        candidates = [
            value for value in (*self.pools.get(cls, ()), *islice(space.candidates(sample, 1), 16))
            if value is not None and value not in taken and self._admits(cls, value, plan)
        ]
        return self.rng.choice(candidates) if candidates else sample

    def _admits(self, cls: Column, value, plan: _Plan) -> bool:
        """Whether a non-NULL value fits the class's storage, space and tests."""
        space = plan.spaces.get(cls)
        return (
            self._fits(cls, value) and (space is None or space.admits(value))
            and all(self._holds(atom, value) is _TRUTH[truth] for atom, truth in plan.tests.get(cls, ()))
        )

    def _satisfy(self, joint: Joint, truth: str, values: dict, pinned: dict, plan: _Plan) -> None:
        """Choose the joint test's classes together so that it has ``truth``.

        Classes pinned in the batch keep their value; the others try their
        current value, their hints, the value of the batch that produced
        output and one fresh value, within their space and storage.
        """
        want = _TRUTH[truth]
        for cls in joint.columns:
            if cls not in values:
                values[cls] = self._sample(cls)
        if self._joint(joint, values) is want:
            return
        options = []
        for cls in joint.columns:
            if cls in pinned:
                options.append([values[cls]])
                continue
            found = [values[cls], *self.pools.get(cls, ()), self.productive.get(cls), self._fresh(cls)]
            if want is None and self.nullable[cls]:
                found.append(None)
            options.append([
                value for value in dict.fromkeys(found)
                if (value is None and want is None) or value is not None and self._admits(cls, value, plan)
            ])
        combinations = list(product(*options))
        self.rng.shuffle(combinations)
        for combination in combinations:
            trial = {**values, **dict(zip(joint.columns, combination))}
            if self._joint(joint, trial) is want:
                values.update(zip(joint.columns, combination))
                return
        # Candidates cannot meet it (an equation such as x + g = 9 needs a
        # computed value): solve the test over its free classes.
        self._solve(joint, truth, values, pinned, plan)

    def _solve(self, joint: Joint, truth: str, values: dict, pinned: dict, plan: _Plan) -> None:
        """Solve a joint test for its unpinned classes, within their spaces.

        Each free class becomes an open input; pinned classes keep their
        values. The requirements are the test with ``truth`` and what the
        classes' spaces state; a solution that does not fit storage is dropped.
        """
        free = [cls for cls in joint.columns if cls not in pinned]
        if not free:
            return
        runtime = self.empty.runtime
        inputs = {}
        for cls in free:
            occurrence, position = cls
            sort = self.fields[self.relation_of[occurrence]][position]
            inputs[cls] = runtime.input(f"speculate:{len(runtime.inputs)}", placeholder(sort), sort)
        v = Valuation(self.empty, frozenset(
            runtime.arena[value.expression.root].payload.parameter for value in inputs.values()
        ))
        terms = {cls: value.expression.root for cls, value in inputs.items()}
        literals = {}
        for field, column in joint.fields:
            cls = self.classes[column]
            occurrence, position = column
            sort = self.fields[self.relation_of[occurrence]][position]
            value = values[cls]
            literals[field] = terms[cls] if cls in terms else (
                v.builder.resolve(v.builder.null(sort.sql_type)) if value is None else v.literal(value, sort.sql_type)
            )
        test = self._substitute(joint.arena, joint.term, literals, v)
        requirement = {"true": test, "false": v.not3(test), "unknown": v.is_unknown(test)}[truth]
        requirements = [[requirement], *([term] for cls in free for term in self._class_terms(v, cls, plan, terms[cls]))]
        solution = solve(v, requirements, timeout_ms=self.timeout_ms)
        if solution.status is not Status.SAT:
            return
        chosen = {
            cls: solution.values.get(runtime.arena[inputs[cls].expression.root].payload.parameter, values[cls])
            for cls in free
        }
        if all(self._fits(cls, value) for cls, value in chosen.items()):
            values.update(chosen)

    def _class_terms(self, v: Valuation, cls: Column, plan: _Plan, term: TermId) -> list[TermId]:
        """Predicates stating what the plan asks of a class, for an open input."""
        result = []
        for atom, truth in plan.tests.get(cls, ()):
            arena, test, fields = atom.value
            holds = self._substitute(arena, test, dict.fromkeys(fields, term), v)
            result.append({"true": holds, "false": v.not3(holds)}[truth])
        space = plan.spaces.get(cls)
        if space is None:
            return result
        if space.null is True:
            return [v.is_null(term)]
        sort = v.runtime.inputs[v.arena[term].payload.parameter].sort.sql_type
        if space.null is False:
            result.append(v.not3(v.is_null(term)))
        if space.equals is not None:
            result.append(v.eq3(term, v.literal(space.equals, sort)))
        result.extend(v.not3(v.eq3(term, v.literal(value, sort))) for value in space.excluded)
        if space.lower is not None:
            bound = v.literal(space.lower, sort)
            result.append(v.lt3(bound, term) if space.lower_strict else v.not3(v.lt3(term, bound)))
        if space.upper is not None:
            bound = v.literal(space.upper, sort)
            result.append(v.lt3(term, bound) if space.upper_strict else v.not3(v.lt3(bound, term)))
        for pattern in space.patterns:
            result.append(v.node(nodes.Like3, (term, v.literal(pattern, sort))))
        for pattern in space.rejected:
            result.append(v.not3(v.node(nodes.Like3, (term, v.literal(pattern, sort)))))
        return result

    def _joint(self, joint: Joint, values: dict):
        """The truth of a joint test under the classes' values."""
        v = self.valuation
        literals = {}
        for field, column in joint.fields:
            occurrence, position = column
            sort = self.fields[self.relation_of[occurrence]][position]
            value = values[self.classes[column]]
            literals[field] = (
                v.builder.resolve(v.builder.null(sort.sql_type)) if value is None else v.literal(value, sort.sql_type)
            )
        return v.value(self._substitute(joint.arena, joint.term, literals))

    def _substitute(self, arena: TermArena, term: TermId, literals: dict, valuation: Valuation | None = None) -> TermId:
        """A predicate with the given fields replaced, folded in the instance's arena."""
        v = valuation or self.valuation
        memo: dict[TermId, TermId] = {}

        def visit(current: TermId) -> TermId:
            if current not in memo:
                node = arena[current]
                field = (arena[node.children[0]].payload.depth, node.payload.index) if (
                    isinstance(node, nodes.Field) and isinstance(arena[node.children[0]], nodes.RowVar)
                ) else None
                if field in literals:
                    memo[current] = literals[field]
                else:
                    memo[current] = v.node(type(node), tuple(visit(child) for child in node.children), node.payload)
            return memo[current]

        return visit(term)

    def _holds(self, atom: Atom, value):
        """The truth of a test atom when its column takes ``value``."""
        arena, term, fields = atom.value
        occurrence, position = atom.column
        literal = self.valuation.literal(value, self.fields[self.relation_of[occurrence]][position].sql_type)
        return self.valuation.value(self._substitute(arena, term, dict.fromkeys(fields, literal)))

    # Batches.

    def _batch(
        self, instance: Instance, groups: int, size: int, keyed: set[Column],
        plan: _Plan, occurrences: list[int], own: bool = False,
    ):
        """Tuples of one row per occurrence; occurrences of one relation that
        agree on a key share the stored row."""
        stored = {
            (relation, key): {tuple(row[position] for position in key) for row in instance.rows(relation)}
            for relation, keys in self.unique.items()
            for key in keys
        }
        slots: list[Slot] = []
        fixed: dict[Column, object] = {}
        sampled = []
        for group, index in product(range(groups), range(size)):
            if index == 0:
                # Group keys of a group of its own are fresh.
                shared = {cls: self._fresh(cls) for cls in self.keyed - self.distinct
                          if own and cls not in fixed and cls not in plan.spaces}
            values = {**fixed, **shared}
            for cls in plan.spaces:
                if cls not in values:
                    values[cls] = self._pick(cls, plan)
            for joint, truth in plan.joints:
                self._satisfy(joint, truth, values, {**fixed, **shared}, plan)
            for occurrence in occurrences:
                relation = self.relation_of[occurrence]
                row = tuple(
                    self._cell(values, self.classes[occurrence, position], field)
                    for position, field in enumerate(self.fields[relation])
                )
                keys = [(stored[relation, key], tuple(row[position] for position in key)) for key in self.unique[relation]]
                if any(None not in value and value in seen for seen, value in keys):
                    continue
                for seen, value in keys:
                    seen.add(value)
                for field, value in zip(self.fields[relation], row):
                    if value is not None:
                        self.taken.setdefault(field.sql_type.kind, set()).add(value)
                instance, slot = instance.insert(relation, row)
                slots.append(slot)
            if group == index == 0:
                fixed = {cls: value for cls, value in values.items() if cls in self.pools}
            if index == 0:
                shared = {cls: value for cls, value in values.items() if cls in keyed}
            sampled.extend(values.items())
        return instance, slots, sampled

    def _closure(self, occurrences) -> list[int]:
        """The occurrences with their foreign-key parents, in a fixed order."""
        pending = list(occurrences)
        chosen = set(pending)
        while pending:
            for parent in self.parents[pending.pop()]:
                if parent not in chosen:
                    chosen.add(parent)
                    pending.append(parent)
        return [occurrence for occurrence in self.occurrences if occurrence in chosen]

    def _cell(self, values: dict[Column, object], cls: Column, field: ScalarSort):
        if cls not in values:
            if cls in self.reused:
                if cls not in self.reuse:
                    self.reuse[cls] = self._fresh(cls)
                values[cls] = self.reuse[cls]
            else:
                values[cls] = self._sample(cls)
        value = values[cls]
        if value is None:
            return None if field.nullable else placeholder(field)
        domain = Domain.of(field)
        if domain != self.domains[cls]:
            value = next(domain.around(value), placeholder(field))
        return carrier(value, field)

    def _sample(self, cls: Column):
        """NULL, a value the query compares with, a value already stored, or a fresh one."""
        draw = self.rng.random()
        pool = self.pools.get(cls)
        used = self.used.get(cls)
        if self.nullable[cls] and draw < 0.1:
            return None
        if pool and draw < 0.55:
            return self.rng.choice(pool)
        if used and draw < 0.8 and cls not in self.distinct:
            return self.rng.choice(used)
        return self._fresh(cls)

    def _fresh(self, cls: Column):
        """A new value from the provider, for the class's most restrictive column."""
        table, column = self.columns[cls]
        taken = self.taken.setdefault(self.domains[cls].kind, set())
        value = self.provider(table, column, taken, cls in self.distinct)
        taken.add(value)
        return value


def speculate(
    empty: Instance, arena: TermArena, root: TermId, evaluate: Evaluate, output: Target,
    seed: int = 0, provider: Provider = sequential, timeout_ms: int = 5_000, deadline: float | None = None,
) -> Instance:
    """Grow ``empty`` with sampled rows; ``evaluate`` executes the query concretely."""
    return Speculator(empty, arena, root, seed, provider, timeout_ms).run(evaluate, output, deadline)


__all__ = ["Speculator", "speculate"]
