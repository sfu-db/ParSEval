"""Concolic generation of one database that covers U-semiring branches.

Each round executes the query on the current instance. Every relation also
holds a few candidate rows with multiplicity zero whose inputs are open, so
execution yields, for every reached branch outcome, a predicate over those
inputs. The solver activates candidate rows so that an uncovered outcome
holds while every covered outcome that depends on candidate rows keeps
holding. Accepted rows are appended; stored rows never change. Generation
starts from rows sampled from the query's compact IR (``parseval.speculate``),
so solving is left to the outcomes speculation misses.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from parseval.catalog import Catalog
from parseval.instance import (
    ExecutionError,
    Instance,
    Machine,
    TimeLimit,
    UnsupportedQuery,
    Valuation,
)
from parseval.instance.constraints import Integrity
from parseval.instance.domain import Provider, sequential
from parseval.parser.query import lower_query
from parseval.smt import Status, solve
from parseval.speculate import speculate
from parseval.terms.constraints import ForeignKeyDecl, PrimaryKeyDecl, UniqueDecl
from parseval.terms import terms as nodes
from parseval.terms.terms import TermId
from parseval.uexpr.lowering import UExprCompiler

from .coverage import Coverage, Recorder, Sites, Target


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Generation settings.

    ``timeout_ms`` bounds one solver call; ``time_limit_s`` bounds the whole
    generation: when it passes, execution stops and the latest accepted
    database is returned (``None`` runs until done). Generated non-NULL
    strings have at least ``min_string_length`` characters. ``speculate`` samples rows from the
    query before solving (``parseval.speculate``), reproducibly for a
    ``seed``; without it generation starts from an empty database.
    ``provider`` supplies the values of cells no constraint decides, in
    speculated rows and candidate rows alike. With ``set_semantics`` the
    query's result is read as a set, so duplicate output rows are not
    generated for their own sake.

    Each uncovered outcome is solved at most once per database version, and
    a version only follows a solve that covers a new outcome, so generation
    terminates without row or attempt limits.
    """

    timeout_ms: int = 5_000
    time_limit_s: float | None = None
    min_string_length: int = 1
    speculate: bool = True
    seed: int = 0
    provider: Provider = sequential
    set_semantics: bool = False


@dataclass(frozen=True, slots=True)
class Attempt:
    """One solve for an uncovered outcome and how it ended."""

    target: Target
    label: str
    status: Status
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """The generated database (None without rows) and the labels of the
    outcomes its execution reaches and covers; ``timed_out`` when the time
    limit stopped generation before every outcome was tried."""

    instance: Instance | None
    nonempty: bool
    reached: tuple[str, ...] = ()
    covered: tuple[str, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    unsupported: str | None = None
    timed_out: bool = False

    @property
    def failed(self) -> dict[str, str]:
        """The status of the last failed solve of each outcome left uncovered."""
        covered = set(self.covered)
        return {
            attempt.label: attempt.status.value
            for attempt in self.attempts
            if not attempt.accepted and attempt.label not in covered
        }

OUTPUT = Target(Sites.ROOT, "output")

# Bounds on symbolic work, not on the data. Once the output is productive, a
# binding may combine BUDGET candidate rows, widened to MAX_BUDGET when no
# outcome is left to try; stored rows are always enumerated. Every relation
# starts with one candidate row and grows by measured demand (Session._grow).
# The recorder keeps up to WITNESSES conditions per outcome: alternatives for
# an uncovered target, and conditions that keep a covered outcome covered.
BUDGET = 1
MAX_BUDGET = 2
WITNESSES = 4

AttemptCallback = Callable[[Attempt], None]
InstanceCallback = Callable[[Instance], object]


@dataclass(frozen=True, slots=True)
class Round:
    """One execution of the query on an instance and its candidate rows."""

    instance: Instance
    valuation: Valuation
    coverage: Coverage
    budget: int | None


class Session:
    """Generation state for one query."""

    def __init__(self, catalog: Catalog, sql: str, config: GenerationConfig):
        self.catalog = catalog
        self.config = config
        self.sql = sql
        query = lower_query(sql, catalog, ignore_root_limit=True)
        # Rows without a duplicate-row outcome: distinct by construction, or a set result.
        self.distinct = config.set_semantics or distinct_rows(query.arena, query.root)
        self.empty = Instance(catalog)
        self.root = UExprCompiler(query.arena, self.empty.arena).compile(query.root).simplified_root
        self.sites = Sites()
        # Candidate rows per relation: one to start, growing by measured demand.
        self.candidates = dict.fromkeys(self.empty.relations(), 1)
        # When generation must stop (``time.monotonic()``), set by ``run``.
        self.deadline: float | None = None
        self._tables = {table.relation: table for table in catalog.tables()}
        # Positions of columns that alone form a key, whose values must be new.
        self._unique = {
            relation: {
                table.spec.column_position(item.columns[0])
                for item in table.constraints
                if isinstance(item, (PrimaryKeyDecl, UniqueDecl)) and len(item.columns) == 1
            }
            for relation, table in self._tables.items()
        }

    def speculate(self) -> Instance:
        """Rows sampled from the compact query; its root LIMIT counts toward their number."""
        if not self.config.speculate:
            return self.empty
        query = lower_query(self.sql, self.catalog)

        def evaluate(instance: Instance):
            return self._execute(instance, None).coverage

        return speculate(
            self.empty, query.arena, query.root, evaluate, OUTPUT, self.config.seed, self.config.provider,
            self.config.timeout_ms, self.deadline,
        )

    def label(self, target: Target) -> str:
        term = self.sites.terms.get(target.site)
        key = "query" if term is None else self.empty.arena[term].key
        path = ".".join(map(str, self.sites.path(target.site)))
        return f"{path}/{key}:{target.outcome}"

    def with_candidates(self, instance: Instance, counts: dict | None = None) -> Instance:
        """Keep ``counts`` (by default ``self.candidates``) rows of multiplicity zero in each relation.

        Their cells start with provider values: the solver changes the cells
        a requirement mentions, and the others keep varied, valid values.
        """
        taken: dict = {}
        for slot in instance.all_slots():
            for field, value in zip(self._fields(slot.relation), instance.row(slot)):
                if value is not None:
                    taken.setdefault(field.sql_type.kind, set()).add(value)
        for relation in instance.relations():
            missing = (counts or self.candidates)[relation] - sum(not instance.multiplicity(slot) for slot in instance.slots(relation))
            table = self._tables[relation]
            for _ in range(missing):
                values = []
                for position, (field, column) in enumerate(zip(self._fields(relation), table.columns)):
                    existing = taken.setdefault(field.sql_type.kind, set())
                    value = self.config.provider(table, column, existing, position in self._unique[relation])
                    existing.add(value)
                    values.append(value)
                instance, _ = instance.insert(relation, values, 0)
        return instance

    def _fields(self, relation):
        return self.catalog.context.schema(self.catalog.context.relation(relation).schema).fields

    def execute(self, instance: Instance, budget: int | None = None) -> Round:
        """Execute with a budget of candidate rows per binding.

        Stored rows decide coverage concretely under any budget; the budget
        only bounds symbolic witnesses. Witnesses that extend productive
        stored data need few new rows. Until the output is productive, the
        output can need new rows in every joined relation at once, so an
        unproductive execution is repeated without a budget.
        """
        if budget is not None:
            return self._execute(instance, budget)
        bounded = self._execute(instance, BUDGET)
        if OUTPUT in bounded.coverage.covered:
            return bounded
        return self._execute(instance, None)

    def _execute(
        self, instance: Instance, budget: int | None, relaxed: bool = False, every: Target | None = None
    ) -> Round:
        open_inputs = frozenset(
            parameter
            for slot in instance.all_slots()
            if not instance.multiplicity(slot)
            for parameter in slot.parameters
        )
        candidates = [slot for slot in instance.all_slots() if not instance.multiplicity(slot)]
        # Candidate rows are stored at most once (see Integrity), so their
        # multiplicities are 0 or 1, except in a relaxed execution.
        binary = frozenset() if relaxed else frozenset(slot.parameters[-1] for slot in candidates)
        valuation = Valuation(instance, open_inputs, binary)
        recorder = Recorder(self.sites, valuation, WITNESSES, every)
        machine = Machine(valuation, recorder, budget=budget, deadline=self.deadline)
        execution = machine.run(self.root, Sites.ROOT, distinct=self.distinct)
        productive = valuation.value(execution.output)
        recorder.observe(
            Sites.ROOT, self.root, OUTPUT.outcome, isinstance(productive, int) and productive > 0,
            lambda: valuation.positive(execution.output),
        )
        return Round(instance, valuation, recorder.coverage, budget)

    def requirements(self, current: Round, target: Target) -> list[list[TermId]]:
        """The target, and every covered outcome once the output is productive.

        Outcomes covered before the query produces output are vacuous, such
        as an aggregate over empty input, and are not preserved.
        """
        coverage = current.coverage
        groups = [list(coverage.candidates[target])]
        if OUTPUT not in coverage.covered:
            return groups
        groups.extend(
            list(coverage.witnesses[covered])
            for covered in sorted(coverage.covered - coverage.stable)
        )
        return groups

    def integrity(self, current: Round, relaxed: bool = False):
        """Integrity constraints of the candidate rows owning newly mentioned inputs."""
        integrity = Integrity(current.valuation, bounded=not relaxed)
        owners = {
            parameter: slot
            for slot in current.instance.all_slots()
            if not current.instance.multiplicity(slot)
            for parameter in slot.parameters
        }
        mentioned: set = set()

        def closure(inputs):
            mentioned.update(inputs)
            slots = {id(owners[parameter]): owners[parameter] for parameter in inputs if parameter in owners}
            return integrity.constraints(slots.values(), mentioned)

        return closure

    def attempt(self, current: Round, target: Target) -> tuple[Round | None, Attempt]:
        """Solve for a target; when UNSAT, try all its conditions, then grow candidate rows by demand.

        Only ``WITNESSES`` conditions are kept per outcome and all may be
        infeasible, so a target whose kept conditions are UNSAT is tried once
        more with all of them.
        """
        following, attempt = self._attempt(current, target)
        if attempt.status is Status.UNSAT and len(current.coverage.candidates.get(target, ())) >= WITNESSES:
            current = self._execute(current.instance, current.budget, every=target)
            following, attempt = self._attempt(current, target)
        while attempt.status is Status.UNSAT and self._grow(current, target):
            current = self.execute(self.with_candidates(current.instance), current.budget)
            following, attempt = self._attempt(current, target)
        return following, attempt

    def _grow(self, current: Round, target: Target) -> bool:
        """Measure how many candidate rows a target needs; True if more were added.

        The target is solved with candidate rows allowed to repeat, while each
        row prefers to occur once, so repetition marks copies that are really
        missing. The multiplicities of that model are the demand of each
        relation; relations whose demand exceeds their candidates grow.
        Repetition only adds rows equal to existing ones. When it cannot
        satisfy the target, a target may need a row with different values
        (``x > (SELECT AVG(x) ...)``), so one more row is tried in each
        relation the target involves. Growth ends when neither helps: no
        demand increases, or a further row leaves the target unsatisfiable.
        """
        multiplicities = {
            slot.parameters[-1]: relation
            for relation in current.instance.relations()
            for slot in current.instance.slots(relation)
            if not current.instance.multiplicity(slot)
        }
        solution = self._relaxed(current.instance, current.budget, target, multiplicities)
        if solution is None or solution.status is Status.UNSAT:
            return self._distinct(current, target)
        if solution.status is not Status.SAT:
            return False
        demand = dict.fromkeys(self.candidates, 0)
        for parameter, relation in multiplicities.items():
            demand[relation] += solution.values.get(parameter) or 0
        # When a key lies within a foreign key, distinct rows reference
        # distinct parents, so the parent needs at least as many rows.
        changed = True
        while changed:
            changed = False
            for relation, table in self._tables.items():
                keys = [set(item.columns) for item in table.constraints if isinstance(item, (PrimaryKeyDecl, UniqueDecl))]
                for item in table.constraints:
                    if isinstance(item, ForeignKeyDecl) and any(key <= set(item.source) for key in keys):
                        if demand[relation] > demand[item.target_relation]:
                            demand[item.target_relation] = demand[relation]
                            changed = True
        grown = False
        for relation, count in demand.items():
            if count > self.candidates[relation]:
                self.candidates[relation] = count
                grown = True
        return grown

    def _relaxed(self, instance: Instance, budget: int | None, target: Target, single=()):
        """Solve a target with candidate rows allowed to repeat; None if no binding reaches it."""
        relaxed = self._execute(instance, budget, relaxed=True)
        self._on_time()
        if not relaxed.coverage.candidates.get(target):
            return None
        return solve(
            relaxed.valuation,
            self.requirements(relaxed, target),
            timeout_ms=self.config.timeout_ms,
            closure=self.integrity(relaxed, relaxed=True),
            min_string_length=self.config.min_string_length,
            single=single,
        )

    def _distinct(self, current: Round, target: Target) -> bool:
        """Add one candidate row to each relation the target involves, if that
        makes the relaxed target satisfiable."""
        inputs = frozenset().union(*(current.valuation.inputs(term) for term in current.coverage.candidates.get(target, ())))
        involved = {
            slot.relation for slot in current.instance.all_slots()
            if not current.instance.multiplicity(slot) and not inputs.isdisjoint(slot.parameters)
        }
        if not involved:
            return False
        counts = {relation: count + (relation in involved) for relation, count in self.candidates.items()}
        solution = self._relaxed(self.with_candidates(current.instance, counts), current.budget, target)
        if solution is None or solution.status is not Status.SAT:
            return False
        self.candidates = counts
        return True

    def _attempt(self, current: Round, target: Target) -> tuple[Round | None, Attempt]:
        label = self.label(target)
        if not current.coverage.candidates.get(target):
            return None, Attempt(target, label, Status.UNSAT, False, "no binding within the budget reaches it")
        self._on_time()
        absent = [
            current.instance.arena[slot.multiplicity.expression.root].payload.parameter
            for slot in current.instance.all_slots()
        ]
        solution = solve(
            current.valuation,
            self.requirements(current, target),
            timeout_ms=self.config.timeout_ms,
            absent=absent,
            closure=self.integrity(current),
            min_string_length=self.config.min_string_length,
        )
        if solution.status is not Status.SAT:
            return None, Attempt(target, label, solution.status, False, solution.reason)
        instance = self.with_candidates(current.instance.assign(solution.values))
        try:
            following = self.execute(instance)
        except TimeLimit:
            raise
        except ExecutionError as error:
            return None, Attempt(target, label, solution.status, False, f"execution failed: {error}")
        lost = current.coverage.covered - following.coverage.covered if OUTPUT in current.coverage.covered else set()
        if target not in following.coverage.covered or lost:
            reason = "replay did not cover the target" if target not in following.coverage.covered else (
                "replay lost " + ", ".join(self.label(item) for item in sorted(lost))
            )
            return None, Attempt(target, label, solution.status, False, reason)
        return following, Attempt(target, label, solution.status, True)

    def run(
        self, on_attempt: AttemptCallback | None = None, on_instance: InstanceCallback | None = None
    ) -> GenerationResult:
        """Generate; ``on_instance`` receives every accepted database version
        and stops generation by returning a true value.

        Each uncovered outcome is tried once per version, first within
        ``BUDGET`` candidate rows per binding, then within ``MAX_BUDGET``: a
        new row whose key is a foreign key needs a new parent too. At the
        configured time limit the latest accepted version is returned.
        """
        if self.config.time_limit_s is not None:
            self.deadline = time.monotonic() + self.config.time_limit_s
        seed = self.speculate()
        if on_instance is not None and seed.row_count and on_instance(seed):
            return GenerationResult(seed, True)
        attempts: list[Attempt] = []
        current = None
        timed_out = False
        try:
            current = self.execute(self.with_candidates(seed))
            tried: set[Target] = set()
            while True:
                target = self._next(current, tried)
                if target is None and current.budget == BUDGET:
                    current, tried = self.execute(current.instance, MAX_BUDGET), set()
                    target = self._next(current, tried)
                if target is None:
                    break
                tried.add(target)
                following, attempt = self.attempt(current, target)
                attempts.append(attempt)
                if on_attempt is not None:
                    on_attempt(attempt)
                if following is None:
                    continue
                current, tried = following, set()
                if on_instance is not None and on_instance(current.instance):
                    break
        except TimeLimit:
            timed_out = True
        if current is None:
            # The time limit passed before the seed was executed.
            return GenerationResult(seed if seed.row_count else None, bool(seed.row_count), timed_out=True)
        coverage, instance = current.coverage, current.instance
        return GenerationResult(
            instance if instance.row_count else None,
            OUTPUT in coverage.covered,
            tuple(map(self.label, sorted(coverage.reached))),
            tuple(map(self.label, sorted(coverage.covered))),
            tuple(attempts),
            timed_out=timed_out,
        )

    def _on_time(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TimeLimit("the time limit passed")

    def _next(self, current: Round, tried: set[Target]) -> Target | None:
        coverage = current.coverage
        pending = [target for target in sorted(coverage.uncovered) if target not in tried]
        if OUTPUT in pending:
            return OUTPUT
        return pending[0] if pending else None


# Nodes whose rows are rows of their source.
_SOURCES = {
    nodes.Filter: 0, nodes.OrderBy: 0, nodes.ForgetOrder: 0, nodes.TopK: 0,
    nodes.Take: 1, nodes.Drop: 1, nodes.Slice: 2,
}


def distinct_rows(arena, root: TermId) -> bool:
    """Whether a compact query's rows are distinct by construction.

    Below projections and row-preserving nodes lies a global aggregate (one
    row), or a DISTINCT or GROUP BY whose columns or keys the output keeps:
    those rows differ there, so rows keeping all of them differ too.
    """
    kept = None  # fields of the current node the output copies; None for all
    while True:
        node = arena[root]
        if isinstance(node, nodes.GlobalFold):
            return True
        if isinstance(node, nodes.Distinct):
            width = len(arena.context.schema(node.sort.schema).fields)
            return kept is None or set(range(width)) <= kept
        if isinstance(node, nodes.GroupFold):
            keys = len(arena[arena[node.children[1]].children[0]].children)
            return kept is None or set(range(keys)) <= kept
        if isinstance(node, (nodes.Map, nodes.SeqMap)):
            body = arena[arena[node.children[1]].children[0]]
            if not isinstance(body, nodes.Row):
                return False
            copies = {}
            for column, child in enumerate(body.children):
                field = arena[child]
                if isinstance(field, nodes.Field) and isinstance(arena[field.children[0]], nodes.RowVar) \
                        and arena[field.children[0]].payload.depth == 0:
                    copies[column] = field.payload.index
            kept = {index for column, index in copies.items() if kept is None or column in kept}
            root = node.children[0]
        elif type(node) in _SOURCES:
            root = node.children[_SOURCES[type(node)]]
        else:
            return False


def generate(
    sql: str,
    catalog: Catalog,
    *,
    config: GenerationConfig | None = None,
    on_attempt: AttemptCallback | None = None,
    on_instance: InstanceCallback | None = None,
) -> GenerationResult:
    """Grow one database whose execution covers the query's branch outcomes."""
    try:
        return Session(catalog, sql, config or GenerationConfig()).run(on_attempt, on_instance)
    except UnsupportedQuery as error:
        return GenerationResult(None, False, unsupported=str(error))


__all__ = ["Attempt", "GenerationConfig", "GenerationResult", "Session", "distinct_rows", "generate"]
