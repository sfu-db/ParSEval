"""Demand-driven witnesses and exact shared circuits for weighted U-expressions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import z3

from parseval.coverage import CoverageTarget
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.context import AggregateKind
from parseval.terms.names import SchemaId
from parseval.terms.sorts import (
    RowSort,
    ScalarSort,
    ScalarType,
)
from parseval.terms.terms import (
    AggregateMode,
    BaseRelationPayload,
    FieldPayload,
    FoldPayload,
    LiteralPayload,
    ScalarCallPayload,
    TermId,
    VariablePayload,
)
from parseval.uexpr.observation import (
    BagCardinalityCondition,
    GroupCardinalityCondition,
    Condition,
    NullCondition,
    PredicateCondition,
    WeightCondition,
)
from parseval.uexpr.evaluate import BagEntry

from parseval.uexpr.witness import (
    UnitWitnessPlan,
    WitnessPlan,
    ScanVariable,
)

from .text import ascii_domain, ascii_lower, dynamic_like, valid_like_pattern, iso_date
from .instance import SymbolicInstance
from .budget import Budget
from .circuit import Circuit, memoized
from .schema import schema_constraints
from .prepared import PreparedTerms
from .factors import product_count
from .values import (
    TRUE, FALSE, UNKNOWN, _sum, SymbolicEntry, SymbolicRow, SymbolicValue,
    UnsupportedEncodingError, _default, _literal, _lexical_placeholder,
    _row_equal, _rows_equal, _nullable_value_equal, _cast, _like_regex,
)


@dataclass(frozen=True, slots=True)
class _Environment:
    rows: tuple[SymbolicRow, ...] = ()
    relations: tuple[tuple[SymbolicEntry, ...], ...] = ()

    def bind(self, row: SymbolicRow) -> _Environment:
        return _Environment((row, *self.rows), self.relations)

    def bind_relation(self, relation: tuple[SymbolicEntry, ...]) -> _Environment:
        return _Environment(self.rows, (relation, *self.relations))


class UExprEncoder:
    """Encode coverage observations without converting terms back to SQL ASTs."""

    def __init__(
        self,
        arena: TermArena,
        database: SymbolicInstance,
        *,
        budget: Budget | None = None,
        prepared: PreparedTerms | None = None,
    ) -> None:
        if arena.context is not database.context:
            raise ValueError("The arena and symbolic instance must share one Context")
        self.arena = arena
        self.database = database
        self.constraints: list[z3.BoolRef] = []
        self.budget = budget or Budget()
        self.cache = {}
        self.prepared = prepared or PreparedTerms(arena)
        if self.prepared.arena is not arena:
            raise ValueError("Prepared terms must belong to the encoder arena")
        self._bindings = {}
        self._base_bindings = {}
        self._memberships = {}
        self._placeholders = {}
        self.circuit = Circuit(self.constraints)
        self.observed_values: list[z3.ExprRef] = []

    def target(self, target: CoverageTarget) -> z3.BoolRef:
        obligation = target.obligation
        environment = _Environment()
        for definition in reversed(obligation.relations):
            environment = environment.bind_relation(self.bag(definition, environment))
        alternatives = []
        for scoped, reach in self._context_environments(obligation.contexts, environment):
            if isinstance(obligation.plan, UnitWitnessPlan):
                alternatives.append(z3.And(
                    *reach,
                    *(self.condition(condition, scoped) for condition in obligation.conditions),
                ))
                continue
            alternatives.extend(
                z3.And(
                    *reach, *guards,
                    *(self.condition(condition, bound) for condition in obligation.conditions),
                )
                for bound, guards, _output in self._witness_environments(
                    obligation.plan, scoped
                )
            )
        return z3.Or(*alternatives)

    def _context_environments(
        self, contexts: tuple[WitnessPlan, ...], environment: _Environment
    ):
        current = ((environment, ()),)
        for plan in contexts:
            current = tuple(
                (bound, (*reach, *guards))
                for scoped, reach in current
                for bound, guards, _output in self._witness_environments(plan, scoped)
            )
        return current

    def condition(self, condition: Condition, environment: _Environment) -> z3.BoolRef:
        if isinstance(condition, GroupCardinalityCondition):
            node = self.arena[condition.term]
            if not isinstance(node, (nodes.GroupFold, nodes.GlobalFold)):
                raise UnsupportedEncodingError("group cardinality requires a fold")
            guards = ()
            if isinstance(node, nodes.GroupFold):
                source = self.bag(node.children[0], environment)
                selected, guards = self._select(source, self.arena[node.children[0]].sort.schema)
                key = cast(SymbolicRow, self._apply(node.children[1], selected, environment))
                weights = (
                    z3.If(_row_equal(cast(SymbolicRow, self._apply(node.children[1], entry.row, environment)), key),
                          entry.multiplicity, 0)
                    for entry in source
                )
                count = self.circuit.share(_sum(*weights))
            else:
                count = self.count(node.children[0], environment)
            return z3.And(*guards, count >= condition.minimum,
                          *(() if condition.maximum is None else (count <= condition.maximum,)))
        if isinstance(condition, WeightCondition):
            if condition.minimum == 1 and condition.maximum is None:
                return self.positive(condition.term, environment)
            if condition.minimum == 0 and condition.maximum == 0:
                return z3.Not(self.positive(condition.term, environment))
            value = self.multiplicity(condition.term, environment)
            return z3.And(
                value >= condition.minimum,
                *(() if condition.maximum is None else (value <= condition.maximum,)),
            )
        if isinstance(condition, PredicateCondition):
            return self.predicate(condition.term, environment) == condition.truth.value
        if isinstance(condition, NullCondition):
            value = cast(SymbolicValue, self.value(condition.term, environment))
            return value.is_null if condition.is_null else z3.Not(value.is_null)
        if isinstance(condition, BagCardinalityCondition):
            count = self.count(condition.term, environment)
            return z3.And(
                count >= condition.minimum,
                *(() if condition.maximum is None else (count <= condition.maximum,)),
            )
        raise UnsupportedEncodingError(f"observation:{type(condition).__name__}")

    def schema_constraints(self) -> tuple[z3.BoolRef, ...]:
        return schema_constraints(self)

    @memoized
    def value(self, term: TermId, environment: _Environment) -> SymbolicValue | SymbolicRow:
        node = self.arena[term]
        if isinstance(node, nodes.Literal):
            payload = cast(LiteralPayload, node.payload)
            return SymbolicValue(_literal(payload.value, payload.sql_type), z3.BoolVal(False), payload.sql_type)
        if isinstance(node, nodes.Null):
            sql_type = cast(ScalarSort, node.sort).sql_type
            return SymbolicValue(_default(sql_type), z3.BoolVal(True), sql_type)
        if isinstance(node, nodes.RowVar):
            return environment.rows[cast(VariablePayload, node.payload).depth]
        if isinstance(node, nodes.Field):
            row = cast(SymbolicRow, self.value(node.children[0], environment))
            value = row.values[cast(FieldPayload, node.payload).index]
            self.observed_values.extend((value.value, value.is_null))
            return value
        if isinstance(node, nodes.Row):
            return SymbolicRow(
                cast(RowSort, node.sort).schema,
                tuple(cast(SymbolicValue, self.value(child, environment)) for child in node.children),
            )
        if isinstance(node, nodes.Case):
            condition, then, otherwise = node.children
            left = cast(SymbolicValue, self.value(then, environment))
            right = cast(SymbolicValue, self.value(otherwise, environment))
            select = self.predicate(condition, environment) == TRUE
            return SymbolicValue(
                z3.If(select, left.value, right.value),
                z3.If(select, left.is_null, right.is_null),
                left.sql_type,
            )
        if isinstance(node, nodes.ToBoolean):
            predicate = self.predicate(node.children[0], environment)
            return SymbolicValue(predicate == TRUE, predicate == UNKNOWN, cast(ScalarSort, node.sort).sql_type)
        if isinstance(node, nodes.Scalarize):
            entries = self.bag(node.children[0], environment)
            active = tuple(entry.multiplicity > 0 for entry in entries)
            self.constraints.append(
                _sum(*(entry.multiplicity for entry in entries)) <= 1
            )
            result_type = cast(ScalarSort, node.sort).sql_type
            result = _default(result_type)
            is_null: z3.BoolRef = z3.BoolVal(True)
            for entry, condition in reversed(tuple(zip(entries, active, strict=True))):
                value = entry.row.values[0]
                result = z3.If(condition, value.value, result)
                is_null = z3.If(condition, value.is_null, is_null)
            return SymbolicValue(result, is_null, result_type)
        if isinstance(node, nodes.ScalarCall):
            payload = cast(ScalarCallPayload, node.payload)
            operator = self.arena.context.function(payload.function).operator
            if operator is None:
                raise UnsupportedEncodingError("scalar function has no operator")
            arguments = tuple(cast(SymbolicValue, self.value(child, environment)) for child in node.children)
            return self._call(operator, arguments, cast(ScalarSort, node.sort).sql_type)
        if isinstance(node, nodes.Fold):
            entries = self.bag(node.children[0], environment)
            values = tuple(
                (entry.row.values[0], entry.multiplicity) for entry in entries
            )
            return self._aggregate(node.payload.aggregate, values)
        raise UnsupportedEncodingError(f"value:{type(node).key}")

    @memoized
    def bag(
        self,
        term: TermId,
        environment: _Environment,
    ) -> tuple[SymbolicEntry, ...]:
        node = self.arena[term]
        if isinstance(node, nodes.LetRel):
            definition, body = node.children
            return self.bag(body, environment.bind_relation(self.bag(definition, environment)))
        if isinstance(node, nodes.RelVar):
            return environment.relations[cast(VariablePayload, node.payload).depth]
        if isinstance(node, nodes.Base):
            relation = cast(BaseRelationPayload, node.payload).relation
            return self.database.relations[relation]
        if isinstance(node, nodes.BagLambda):
            entries: list[SymbolicEntry] = []
            for plan in self.prepared.plans(term):
                entries.extend(self._product_entries(plan, environment))
            return tuple(entries)
        if isinstance(node, (nodes.GlobalFold, nodes.GroupFold)):
            return self._aggregate_entries(term, environment)
        raise UnsupportedEncodingError(f"bag:{type(node).key}")

    @memoized
    def count(self, term: TermId, environment: _Environment):
        """Encode cardinality without constructing output tuples when possible."""
        node = self.arena[term]
        if isinstance(node, nodes.LetRel):
            definition, body = node.children
            return self.count(body, environment.bind_relation(self.bag(definition, environment)))
        if isinstance(node, nodes.BagLambda):
            return self.circuit.share(_sum(*(
                product_count(self, plan, environment) for plan in self.prepared.plans(term)
            )))
        return self.circuit.share(_sum(*(entry.multiplicity for entry in self.bag(term, environment))))

    def base_row(self, source, entry):
        """A row bound under its active guard carries canonical base membership.

        Inactive slots may alias active rows, so this provenance belongs only
        to guarded scan bindings, never to arbitrary symbolic row expressions.
        """
        relation = self.arena[source].payload.relation
        key = (relation, id(entry))
        if key not in self._base_bindings:
            row = SymbolicRow(entry.row.schema, entry.row.values)
            self._base_bindings[key] = row
            self._memberships[id(row)] = (relation, entry.multiplicity)
        return self._base_bindings[key]

    def membership(self, row):
        return self._memberships.get(id(row))

    def _product_entries(
        self,
        plan: WitnessPlan,
        environment: _Environment,
    ) -> tuple[SymbolicEntry, ...]:
        entries: list[SymbolicEntry] = []
        for bound, guards, output in self._bound_plan_environments(plan, environment):
            multiplicity = z3.If(z3.And(*guards), 1, 0)
            for factor in plan.product.factors:
                multiplicity = self.circuit.share(multiplicity * self.multiplicity(factor, bound))
            entries.append(
                BagEntry(output, multiplicity)
            )
        return tuple(entries)

    def _select(self, entries: tuple[SymbolicEntry, ...], schema: SchemaId):
        """Select one active row without enumerating combinations of scans.

        The range guard is returned locally, so an unselected OR branch with
        empty support cannot accidentally make the whole target unsatisfiable.
        """
        self.budget.tick(len(entries))
        if not entries:
            return _lexical_placeholder(self.arena.context, schema), (z3.BoolVal(False),)
        if len(entries) == 1:
            return entries[0].row, (entries[0].active,)
        selector = z3.FreshInt("witness")
        values = []
        for column, field in enumerate(self.arena.context.schema(schema).fields):
            value = entries[-1].row.values[column].value
            null = entries[-1].row.values[column].is_null
            for index in reversed(range(len(entries) - 1)):
                candidate = entries[index].row.values[column]
                value = z3.If(selector == index, candidate.value, value)
                null = z3.If(selector == index, candidate.is_null, null)
            values.append(SymbolicValue(self.circuit.share(value), self.circuit.share(null), field.sql_type))
        guard = z3.Or(*(z3.And(selector == index, entry.active) for index, entry in enumerate(entries)))
        return SymbolicRow(schema, tuple(values)), (guard,)

    def _witness_environments(self, plan: WitnessPlan, environment: _Environment):
        """Existential bindings: one selector per scan, no pairwise deduplication."""
        for binding in plan.bindings:
            self.budget.tick()
            assigned: dict[int, SymbolicRow] = {}
            guards = []
            for step in binding.steps:
                scoped = self._scope_environment(plan, step.scope, assigned, environment)
                if isinstance(step, ScanVariable):
                    row, active = self._select(self.bag(step.source, scoped), step.schema)
                    guards.extend(active)
                else:
                    row = cast(SymbolicRow, self.value(step.expression, scoped))
                assigned[step.variable] = row
            output = assigned[plan.output_variable]
            rows = tuple(assigned[index] for index in reversed(range(plan.output_variable)))
            yield _Environment((*rows, output, *environment.rows), environment.relations), tuple(guards), output

    def _bound_plan_environments(
        self,
        plan: WitnessPlan,
        environment: _Environment,
    ):
        # A single plan scanning unique base support cannot produce the same
        # assignment twice. Projection may merge output rows, but the summed
        # input binders remain different and must contribute independently.
        unique_bindings = len(plan.bindings) == 1 and all(
            not isinstance(step, ScanVariable)
            or (step.variable <= plan.output_variable
                and isinstance(self.arena[step.source], nodes.Base))
            for step in plan.bindings[0].steps
        )
        yielded: list[
            tuple[tuple[SymbolicRow, ...], tuple[z3.BoolRef, ...]]
        ] = []

        def execute(
            steps,
            position: int,
            assigned: dict[int, SymbolicRow],
            guards: tuple[z3.BoolRef, ...],
        ):
            self.budget.tick()
            if position == len(steps):
                if not unique_bindings:
                    self.budget.tick(len(yielded))
                output = assigned[plan.output_variable]
                signature = tuple(
                    assigned[index]
                    for index in range(plan.output_variable + 1)
                )
                active = z3.And(*guards)
                unique = active if unique_bindings else z3.And(
                    active,
                    *(
                        z3.Or(
                            z3.Not(z3.And(*previous_guards)),
                            z3.Not(_rows_equal(previous, signature)),
                        )
                        for previous, previous_guards in yielded
                    ),
                )
                if not unique_bindings:
                    yielded.append((signature, guards))
                rows = tuple(
                    assigned[index]
                    for index in reversed(range(plan.output_variable))
                )
                yield (
                    _Environment((*rows, output, *environment.rows), environment.relations),
                    (self.circuit.share(unique),),
                    output,
                )
                return

            step = steps[position]
            scoped = self._scope_environment(plan, step.scope, assigned, environment)
            if isinstance(step, ScanVariable):
                domain = self.bag(step.source, scoped)
                choices = (
                    (self.base_row(step.source, entry) if isinstance(self.arena[step.source], nodes.Base) else entry.row,
                     entry.active if isinstance(self.arena[step.source], nodes.Base)
                     else self._representative(entry, domain))
                    for entry in domain
                )
            else:
                value = cast(SymbolicRow, self.value(step.expression, scoped))
                choices = ((value, z3.BoolVal(True)),)
            for row, guard in choices:
                assigned[step.variable] = row
                yield from execute(
                    steps,
                    position + 1,
                    assigned,
                    (*guards, guard),
                )
                del assigned[step.variable]

        for binding in plan.bindings:
            yield from execute(binding.steps, 0, {}, ())

    def _scope_environment(
        self,
        plan: WitnessPlan,
        scope: tuple[int, ...],
        assigned: dict[int, SymbolicRow],
        outer: _Environment,
    ) -> _Environment:
        return _Environment(
            tuple(assigned[variable] if variable in assigned else self.placeholder(plan.variables[variable])
                  for variable in scope) + outer.rows,
            outer.relations,
        )

    def placeholder(self, schema):
        if schema not in self._placeholders:
            self._placeholders[schema] = _lexical_placeholder(self.arena.context, schema)
        return self._placeholders[schema]

    def environment_key(self, term, environment):
        rows, relations = self.prepared.dependencies(term)
        bindings = tuple(environment.rows[index] for index in rows) + tuple(
            environment.relations[index] for index in relations)
        # Retain each object so Python cannot reuse an identity during this attempt.
        # Keys avoid recursive hashing of entire bags and Z3 expressions.
        for binding in bindings:
            self._bindings[id(binding)] = binding
        return tuple(id(binding) for binding in bindings)

    def _aggregate_entries(
        self,
        term: TermId,
        environment: _Environment,
    ) -> tuple[SymbolicEntry, ...]:
        node = self.arena[term]
        payload = cast(FoldPayload, node.payload)
        if isinstance(node, nodes.GlobalFold) and all(
            layout.argument_child is None and layout.filter_child is None
            and layout.mode is not AggregateMode.DISTINCT
            and self.arena.context.aggregate(layout.aggregate).kind is AggregateKind.COUNT
            for layout in payload.calls
        ):
            count = self.count(node.children[0], environment)
            values = tuple(SymbolicValue(count, z3.BoolVal(False),
                self.arena.context.aggregate(layout.aggregate).output.sql_type)
                for layout in payload.calls)
            return (BagEntry(SymbolicRow(payload.output_schema, values), z3.IntVal(1)),)
        source = self.bag(node.children[0], environment)
        if isinstance(node, nodes.GlobalFold):
            values = tuple(
                self._aggregate_call(node, layout, source, environment)
                for layout in payload.calls
            )
            row = SymbolicRow(payload.output_schema, values)
            return (BagEntry(row, z3.IntVal(1)),)

        key_function = node.children[1]
        keys = tuple(
            cast(SymbolicRow, self._apply(key_function, entry.row, environment))
            for entry in source
        )
        entries = []
        for index, (candidate, key) in enumerate(zip(source, keys, strict=True)):
            representative = z3.And(
                candidate.active,
                *(
                    z3.Or(
                        z3.Not(previous.active),
                        z3.Not(_row_equal(previous_key, key)),
                    )
                    for previous, previous_key in zip(
                        source[:index], keys[:index], strict=True
                    )
                ),
            )
            group = tuple(
                BagEntry(
                    entry.row,
                    z3.If(
                        _row_equal(entry_key, key),
                        entry.multiplicity,
                        0,
                    ),
                )
                for entry, entry_key in zip(source, keys, strict=True)
            )
            aggregates = tuple(
                self._aggregate_call(node, layout, group, environment)
                for layout in payload.calls
            )
            row = SymbolicRow(
                payload.output_schema,
                (*key.values, *aggregates),
            )
            entries.append(
                BagEntry(row, z3.If(representative, 1, 0))
            )
        return tuple(entries)

    def _aggregate_call(
        self,
        node,
        layout,
        entries: tuple[SymbolicEntry, ...],
        environment: _Environment,
    ) -> SymbolicValue:
        admitted = entries
        if layout.filter_child is not None:
            function = node.children[layout.filter_child]
            admitted = tuple(
                BagEntry(
                    entry.row,
                    z3.If(
                        self._apply(function, entry.row, environment) == TRUE,
                        entry.multiplicity,
                        0,
                    ),
                )
                for entry in admitted
            )
        if layout.argument_child is None:
            values = tuple(
                (
                    SymbolicValue(z3.IntVal(1), z3.BoolVal(False), cast(ScalarSort, self.arena.context.aggregate(layout.aggregate).output).sql_type),
                    entry.multiplicity,
                )
                for entry in admitted
            )
        else:
            function = node.children[layout.argument_child]
            values = tuple(
                (
                    cast(SymbolicValue, self._apply(function, entry.row, environment)),
                    entry.multiplicity,
                )
                for entry in admitted
            )
        if layout.mode is AggregateMode.DISTINCT:
            distinct: list[tuple[SymbolicValue, z3.ArithRef]] = []
            for index, (value, multiplicity) in enumerate(values):
                first = z3.And(
                    multiplicity > 0,
                    *(
                        z3.Or(
                            previous_multiplicity == 0,
                            z3.Not(_nullable_value_equal(previous, value)),
                        )
                        for previous, previous_multiplicity in values[:index]
                    ),
                )
                distinct.append((value, z3.If(first, 1, 0)))
            values = tuple(distinct)
        return self._aggregate(layout.aggregate, values)

    def _aggregate(
        self,
        aggregate,
        values: tuple[tuple[SymbolicValue, z3.ArithRef], ...],
    ) -> SymbolicValue:
        specification = self.arena.context.aggregate(aggregate)
        result_type = specification.output.sql_type
        if specification.kind is AggregateKind.COUNT:
            count = _sum(
                *(
                    multiplicity
                    if specification.input is None
                    else z3.If(value.is_null, 0, multiplicity)
                    for value, multiplicity in values
                )
            )
            return SymbolicValue(count, z3.BoolVal(False), result_type)
        admitted = tuple(
            z3.And(z3.Not(value.is_null), multiplicity > 0)
            for value, multiplicity in values
        )
        any_value = z3.Or(*admitted) if admitted else z3.BoolVal(False)
        if specification.kind in (AggregateKind.SUM, AggregateKind.AVG):
            total = _sum(
                *(
                    z3.If(
                        value.is_null,
                        _default(result_type),
                        value.value * multiplicity,
                    )
                    for value, multiplicity in values
                )
            )
            if specification.kind is AggregateKind.SUM:
                result = total
            else:
                count = _sum(
                    *(
                        z3.If(value.is_null, 0, multiplicity)
                        for value, multiplicity in values
                    )
                )
                numerator = z3.ToReal(total) if z3.is_int(total) else total
                denominator = z3.ToReal(count) if z3.is_int(count) else count
                result = z3.If(count > 0, numerator / denominator, _default(result_type))
            return SymbolicValue(result, z3.Not(any_value), result_type)
        if specification.operator in {"stddev", "stddev_samp", "stddev_pop"}:
            count = _sum(*(z3.If(value.is_null, 0, weight) for value, weight in values))
            total = _sum(*(z3.If(value.is_null, 0, value.value * weight) for value, weight in values))
            squares = _sum(*(z3.If(value.is_null, 0, value.value * value.value * weight) for value, weight in values))
            population = specification.operator == "stddev_pop"
            defined = count > (0 if population else 1)
            result = z3.FreshReal("stddev")
            # Avoid division and select the nonnegative square root. Bag weights
            # participate in all moments; DISTINCT/FILTER were applied above.
            denominator = count * (count if population else count - 1)
            self.constraints.append(z3.If(defined,
                z3.And(result >= 0, result * result * denominator == count * squares - total * total),
                result == 0))
            return SymbolicValue(result, z3.Not(defined), result_type)
        if specification.kind in (AggregateKind.MIN, AggregateKind.MAX):
            result = _default(result_type)
            selected = z3.BoolVal(False)
            for (value, _multiplicity), admitted_value in zip(values, admitted, strict=True):
                better = (
                    value.value < result
                    if specification.kind is AggregateKind.MIN
                    else value.value > result
                )
                result = z3.If(
                    admitted_value,
                    z3.If(z3.Or(z3.Not(selected), better), value.value, result),
                    result,
                )
                selected = z3.Or(selected, admitted_value)
            return SymbolicValue(result, z3.Not(any_value), result_type)
        raise UnsupportedEncodingError(f"aggregate:{specification.operator}")

    def _apply(
        self,
        function: TermId,
        row: SymbolicRow,
        environment: _Environment,
    ):
        node = self.arena[function]
        body = node.children[0]
        bound = environment.bind(row)
        return (
            self.predicate(body, bound)
            if isinstance(self.arena[body], nodes.Predicate)
            else self.value(body, bound)
        )

    @memoized
    def predicate(self, term: TermId, environment: _Environment) -> z3.ArithRef:
        node = self.arena[term]
        if isinstance(node, nodes.True3):
            return TRUE
        if isinstance(node, nodes.False3):
            return FALSE
        if isinstance(node, nodes.Unknown3):
            return UNKNOWN
        if isinstance(node, nodes.ToPredicate):
            value = cast(SymbolicValue, self.value(node.children[0], environment))
            return z3.If(value.is_null, UNKNOWN, z3.If(value.value, TRUE, FALSE))
        if isinstance(node, (nodes.Eq3, nodes.Lt3)):
            left = cast(SymbolicValue, self.value(node.children[0], environment))
            right = cast(SymbolicValue, self.value(node.children[1], environment))
            comparison = left.value == right.value if isinstance(node, nodes.Eq3) else left.value < right.value
            return z3.If(z3.Or(left.is_null, right.is_null), UNKNOWN, z3.If(comparison, TRUE, FALSE))
        if isinstance(node, nodes.IsNull):
            value = cast(SymbolicValue, self.value(node.children[0], environment))
            return z3.If(value.is_null, TRUE, FALSE)
        if isinstance(node, nodes.IsNotNull):
            value = cast(SymbolicValue, self.value(node.children[0], environment))
            return z3.If(value.is_null, FALSE, TRUE)
        if isinstance(node, nodes.IsNotDistinct):
            left = cast(SymbolicValue, self.value(node.children[0], environment))
            right = cast(SymbolicValue, self.value(node.children[1], environment))
            same = z3.Or(z3.And(left.is_null, right.is_null), z3.And(z3.Not(left.is_null), z3.Not(right.is_null), left.value == right.value))
            return z3.If(same, TRUE, FALSE)
        if isinstance(node, (nodes.Like3, nodes.ILike3)):
            value = cast(SymbolicValue, self.value(node.children[0], environment))
            pattern = cast(SymbolicValue, self.value(node.children[1], environment))
            null = z3.Or(value.is_null, pattern.is_null)
            text, template = value.value, pattern.value
            insensitive = isinstance(node, nodes.ILike3)
            literal = z3.simplify(template)
            if insensitive:
                self.constraints.append(z3.Implies(z3.Not(null), z3.And(ascii_domain(text), ascii_domain(template))))
            self.constraints.append(z3.Implies(z3.Not(null), valid_like_pattern(template)))
            if z3.is_string_value(literal):
                match = z3.InRe(text, _like_regex(literal.as_string(), insensitive=insensitive))
            else:
                if insensitive:
                    text, template = ascii_lower(text), ascii_lower(template)
                match = dynamic_like(text, template)
            return z3.If(null, UNKNOWN, z3.If(match, TRUE, FALSE))
        if isinstance(node, nodes.And3):
            values = tuple(self.predicate(child, environment) for child in node.children)
            return z3.If(z3.Or(*(value == FALSE for value in values)), FALSE, z3.If(z3.Or(*(value == UNKNOWN for value in values)), UNKNOWN, TRUE))
        if isinstance(node, nodes.Or3):
            values = tuple(self.predicate(child, environment) for child in node.children)
            return z3.If(z3.Or(*(value == TRUE for value in values)), TRUE, z3.If(z3.Or(*(value == UNKNOWN for value in values)), UNKNOWN, FALSE))
        if isinstance(node, nodes.Not3):
            value = self.predicate(node.children[0], environment)
            return z3.If(value == UNKNOWN, UNKNOWN, z3.If(value == TRUE, FALSE, TRUE))
        if isinstance(node, nodes.RowIdentityEq):
            left = cast(SymbolicRow, self.value(node.children[0], environment))
            right = cast(SymbolicRow, self.value(node.children[1], environment))
            return z3.If(_row_equal(left, right), TRUE, FALSE)
        if isinstance(node, nodes.InSubquery):
            needle = cast(SymbolicValue, self.value(node.children[0], environment))
            entries = self.bag(node.children[1], environment)
            matches = tuple(
                z3.And(
                    entry.multiplicity > 0,
                    z3.Not(value.is_null),
                    needle.value == value.value,
                )
                for entry in entries
                for value in (entry.row.values[0],)
            )
            contains_null = tuple(
                z3.And(entry.multiplicity > 0, entry.row.values[0].is_null)
                for entry in entries
            )
            return z3.If(
                needle.is_null,
                UNKNOWN,
                z3.If(
                    z3.Or(*matches),
                    TRUE,
                    z3.If(z3.Or(*contains_null), UNKNOWN, FALSE),
                ),
            )
        raise UnsupportedEncodingError(f"predicate:{type(node).key}")

    @memoized
    def positive(self, term: TermId, environment: _Environment) -> z3.BoolRef:
        """Exact positivity in the natural-number carrier.

        Absence and nested sums keep their complete finite semantics. In
        particular, never negate a fresh existential witness selector.
        """
        node = self.arena[term]
        if isinstance(node, nodes.Zero):
            return z3.BoolVal(False)
        if isinstance(node, nodes.One):
            return z3.BoolVal(True)
        if isinstance(node, nodes.Add):
            return self.circuit.share(z3.Or(*(self.positive(child, environment) for child in node.children)))
        if isinstance(node, nodes.Mul):
            return self.circuit.share(z3.And(*(self.positive(child, environment) for child in node.children)))
        if isinstance(node, nodes.Indicator):
            return self.predicate(node.children[0], environment) == TRUE
        if isinstance(node, nodes.Squash):
            return self.positive(node.children[0], environment)
        if isinstance(node, nodes.UNot):
            return z3.Not(self.positive(node.children[0], environment))
        return self.multiplicity(term, environment) > 0

    @memoized
    def multiplicity(
        self, term: TermId, environment: _Environment
    ) -> z3.ArithRef:
        """Interpret one MultiplicitySort term in the Z3 integer carrier."""

        node = self.arena[term]
        if isinstance(node, nodes.Zero):
            return z3.IntVal(0)
        if isinstance(node, nodes.One):
            return z3.IntVal(1)
        if isinstance(node, nodes.Add):
            return self.circuit.share(_sum(
                *(self.multiplicity(child, environment) for child in node.children)
            ))
        if isinstance(node, nodes.Mul):
            result = z3.IntVal(1)
            for child in node.children:
                result = self.circuit.share(result * self.multiplicity(child, environment))
            return result
        if isinstance(node, nodes.Indicator):
            return z3.If(self.predicate(node.children[0], environment) == TRUE, 1, 0)
        if isinstance(node, nodes.Squash):
            return z3.If(
                self.positive(node.children[0], environment), 1, 0
            )
        if isinstance(node, nodes.UNot):
            return z3.If(
                z3.Not(self.positive(node.children[0], environment)), 1, 0
            )
        if isinstance(node, nodes.At):
            bag = self.arena[node.children[0]]
            row = cast(SymbolicRow, self.value(node.children[1], environment))
            membership = self.membership(row)
            if isinstance(bag, nodes.Base) and membership is not None and membership[0] == bag.payload.relation:
                return membership[1]
            if isinstance(bag, nodes.BagLambda):
                function = self.arena[bag.children[0]]
                return self.multiplicity(
                    function.children[0], environment.bind(row)
                )
            return self.circuit.share(_sum(
                *(
                    z3.If(_row_equal(entry.row, row), entry.multiplicity, 0)
                    for entry in self.bag(node.children[0], environment)
                )
            ))
        if isinstance(node, nodes.Sum):
            return self.count(self.prepared.sum_bag(term), environment)
        raise UnsupportedEncodingError(f"multiplicity:{type(node).key}")

    @staticmethod
    def _representative(
        entry: SymbolicEntry,
        domain: tuple[SymbolicEntry, ...],
    ) -> z3.BoolRef:
        position = next(
            index for index, candidate in enumerate(domain) if candidate is entry
        )
        return z3.And(
            entry.active,
            *(
                z3.Or(
                    z3.Not(previous.active),
                    z3.Not(_row_equal(previous.row, entry.row)),
                )
                for previous in domain[:position]
            ),
        )

    def _call(
        self,
        operator: str,
        arguments: tuple[SymbolicValue, ...],
        result_type: ScalarType,
    ) -> SymbolicValue:
        if operator == "coalesce":
            value = arguments[-1].value
            null = arguments[-1].is_null
            for argument in reversed(arguments[:-1]):
                value = z3.If(argument.is_null, value, argument.value)
                null = z3.And(argument.is_null, null)
            return SymbolicValue(value, null, result_type)
        if operator == "nullif":
            left, right = arguments
            null = z3.Or(left.is_null, z3.And(z3.Not(right.is_null), left.value == right.value))
            return SymbolicValue(left.value, null, result_type)
        null = z3.Or(*(argument.is_null for argument in arguments)) if arguments else z3.BoolVal(False)
        values = tuple(argument.value for argument in arguments)
        if operator in {"div", "mod"}:
            self.constraints.append(
                z3.Implies(z3.Not(null), values[1] != 0)
            )
        if operator == "lower":
            self.constraints.append(z3.Implies(z3.Not(null), ascii_domain(values[0])))
            lowered = ascii_lower(values[0])
            self.constraints.append(z3.Length(lowered) == z3.Length(values[0]))
            return SymbolicValue(lowered, null, result_type)
        if operator == "cast_string_to_date":
            ordinal, valid = iso_date(values[0])
            self.constraints.append(z3.Implies(z3.Not(null), valid))
            return SymbolicValue(ordinal, null, result_type)
        operations = {
            "add": lambda: values[0] + values[1],
            "sub": lambda: values[0] - values[1],
            "mul": lambda: values[0] * values[1],
            "div": lambda: values[0] / values[1],
            "mod": lambda: values[0] % values[1],
            "neg": lambda: -values[0],
            "abs": lambda: z3.If(values[0] >= 0, values[0], -values[0]),
            "length": lambda: z3.Length(values[0]),
            "substring": lambda: z3.SubString(
                values[0],
                values[1] - 1,
                values[2] if len(values) == 3 else z3.Length(values[0]),
            ),
        }
        if operator.startswith("cast_"):
            source, target = operator.removeprefix("cast_").split("_to_", 1)
            return SymbolicValue(
                _cast(values[0], source, target),
                arguments[0].is_null,
                result_type,
            )
        operation = operations.get(operator)
        if operation is None:
            raise UnsupportedEncodingError(f"scalar:{operator}")
        return SymbolicValue(operation(), null, result_type)
