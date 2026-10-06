"""Lowering of GROUP BY, grouping sets, aggregate calls, and grouped DISTINCT."""

from __future__ import annotations

from dataclasses import replace

from sqlglot import exp

from parseval.errors import CatalogError, ErrorCode
from parseval.identifiers import Identifier
from parseval.parser.helper import aggregate_expression, function_name, strip_alias
from parseval.parser.scope import ColumnBinding, EmitEnvironment, FieldSlot, OuterScope, Relation
from parseval.terms.builder import AggregateCall, TermRef
from parseval.terms.context import AggregateKind, AggregateSpec
from parseval.terms.names import SchemaId
from parseval.terms.sorts import DECIMAL, FLOAT, INTEGER, ScalarSort, TypeKind

from .analysis import Projection, SelectFacts


class AggregateLowering:
    """Lower grouping and aggregate calls into folds. Mixed into ``QueryCompiler``."""

    __slots__ = ()

    def _aggregate(
        self,
        source: Relation,
        facts: SelectFacts,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        if facts.grouping_calls or (
            facts.grouping_sets is not None and len(facts.grouping_sets) > 1
        ):
            return self._aggregate_sets(
                source,
                facts,
                outer_scopes=outer_scopes,
            )

        source_environment = self._environment(
            source,
            None,
            outer_scopes=outer_scopes,
        )
        key_plans = tuple(
            self.scalar.plan(expression, source_environment)
            for expression in facts.group_keys
        )
        key_sorts = tuple(plan.sort for plan in key_plans)
        calls, aggregate_sorts, bare_sorts = self._aggregate_calls(facts, source_environment)

        sorts = (*key_sorts, *aggregate_sorts, *bare_sorts)
        output_schema = self.context.schema_for_sorts(sorts)
        key_schema = self.context.schema_for_sorts(key_sorts)

        def key_row(row: TermRef) -> TermRef:
            environment = replace(source_environment, row=row)
            return self.context.builder.row(
                key_schema,
                tuple(plan.emit(environment) for plan in key_plans),
            )

        if facts.group_keys:
            term = (
                self.context.builder.group_fold(
                    source.term,
                    key_row,
                    calls,
                    output_schema,
                )
                if calls
                else self.context.builder.distinct(
                    self.context.builder.map(source.term, key_row)
                )
            )
        else:
            term = self.context.builder.global_fold(source.term, calls, output_schema)

        return self._grouped(term, output_schema, facts, (key_sorts, aggregate_sorts, bare_sorts, ()), outer_scopes)

    def _aggregate_sets(
        self,
        source: Relation,
        facts: SelectFacts,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        source_environment = self._environment(
            source,
            None,
            outer_scopes=outer_scopes,
        )
        grouping_sets = facts.grouping_sets
        if grouping_sets is None:
            grouping_sets = (facts.group_keys,)

        key_plans = {
            self.analyzer.key(expression): self.scalar.plan(
                expression, source_environment
            )
            for expression in facts.group_keys
        }
        key_ids = tuple(
            self.analyzer.key(expression)
            for expression in facts.group_keys
        )
        key_sorts = tuple(key_plans[identity].sort for identity in key_ids)
        output_key_sorts = tuple(
            ScalarSort(
                sort.sql_type,
                sort.nullable
                or any(
                    identity
                    not in {
                        self.analyzer.key(expression)
                        for expression in grouping_set
                    }
                    for grouping_set in grouping_sets
                ),
            )
            for identity, sort in zip(key_ids, key_sorts, strict=True)
        )

        calls, aggregate_sorts, bare_sorts = self._aggregate_calls(facts, source_environment)

        grouping_sorts = tuple(
            ScalarSort(INTEGER, False) for _ in facts.grouping_calls
        )
        output_sorts = (
            *output_key_sorts,
            *aggregate_sorts,
            *bare_sorts,
            *grouping_sorts,
        )
        output_schema = self.context.schema_for_sorts(output_sorts)
        branches: list[TermRef] = []

        for grouping_set in grouping_sets:
            active_ids = tuple(
                self.analyzer.key(expression)
                for expression in grouping_set
            )
            active_sorts = tuple(key_plans[identity].sort for identity in active_ids)
            key_schema = self.context.schema_for_sorts(active_sorts)

            def key_row(row: TermRef, identities=active_ids) -> TermRef:
                current = replace(source_environment, row=row)
                return self.context.builder.row(
                    key_schema,
                    tuple(key_plans[identity].emit(current) for identity in identities),
                )

            branch_schema = self.context.schema_for_sorts(
                (*active_sorts, *aggregate_sorts, *bare_sorts)
            )
            if active_ids:
                branch = (
                    self.context.builder.group_fold(
                        source.term,
                        key_row,
                        calls,
                        branch_schema,
                    )
                    if calls
                    else self.context.builder.distinct(
                        self.context.builder.map(source.term, key_row)
                    )
                )
            elif calls:
                branch = self.context.builder.global_fold(
                    source.term, calls, branch_schema
                )
            else:
                branch = self._singleton().term

            active_positions = {
                identity: index for index, identity in enumerate(active_ids)
            }
            call_offset = len(active_ids)
            active_set = frozenset(active_ids)

            def project_branch(
                row: TermRef,
                positions=active_positions,
                offset=call_offset,
                included=active_set,
            ) -> TermRef:
                keys = tuple(
                    self.context.builder.field(row, positions[identity])
                    if identity in positions
                    else self.context.builder.null(sort.sql_type)
                    for identity, sort in zip(
                        key_ids, output_key_sorts, strict=True
                    )
                )
                aggregate_values = tuple(
                    self.context.builder.field(row, offset + index)
                    for index in range(len(calls))
                )
                grouping_values = tuple(
                    self.context.builder.literal(
                        self._grouping_mask(expression, included, key_ids),
                        INTEGER,
                    )
                    for expression in facts.grouping_calls
                )
                return self.context.builder.row(
                    output_schema,
                    (*keys, *aggregate_values, *grouping_values),
                )

            branches.append(self.context.builder.map(branch, project_branch))

        term = branches[0]
        for branch in branches[1:]:
            term = self.context.builder.union_all(term, branch)

        return self._grouped(
            term, output_schema, facts, (output_key_sorts, aggregate_sorts, bare_sorts, grouping_sorts), outer_scopes
        )

    def _aggregate_calls(
        self, facts: SelectFacts, environment: EmitEnvironment
    ) -> tuple[list[AggregateCall], tuple[ScalarSort, ...], tuple[ScalarSort, ...]]:
        """Calls for the block's aggregates, then arbitrary-value calls for its bare columns."""
        calls: list[AggregateCall] = []
        aggregate_sorts: list[ScalarSort] = []
        for expression in facts.aggregates:
            call, sort = self._aggregate_call(expression, environment)
            calls.append(call)
            aggregate_sorts.append(sort)
        bare_sorts: list[ScalarSort] = []
        for expression in facts.bare_columns:
            call, sort = self._any_value_call(expression, environment)
            calls.append(call)
            bare_sorts.append(sort)
        return calls, tuple(aggregate_sorts), tuple(bare_sorts)

    def _grouped(
        self,
        term: TermRef,
        schema: SchemaId,
        facts: SelectFacts,
        sorts: tuple[tuple[ScalarSort, ...], ...],
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        """Bind a grouping's output: group keys, aggregates, bare columns, then GROUPING calls."""
        groups = (facts.group_keys, facts.aggregates, facts.bare_columns, facts.grouping_calls)
        columns: list[ColumnBinding] = []
        slots: dict[str, FieldSlot] = {}
        for prefix, expressions, group_sorts in zip(("_g", "_a", "_b", "_grouping"), groups, sorts, strict=True):
            for index, (expression, sort) in enumerate(zip(expressions, group_sorts, strict=True)):
                slots[self.analyzer.key(expression)] = FieldSlot(len(columns), sort)
                columns.append(ColumnBinding(Identifier(f"{prefix}{index}"), sort, frozenset()))
        relation = Relation(term, schema, tuple(columns))
        return relation, EmitEnvironment(relation, relation.term, slots, outer_scopes)

    def _grouping_mask(
        self,
        expression: exp.Anonymous,
        included: frozenset[str],
        group_keys: tuple[str, ...],
    ) -> int:
        mask = 0
        known = frozenset(group_keys)
        arguments = tuple(expression.expressions)
        for index, argument in enumerate(arguments):
            identity = self.analyzer.key(strip_alias(argument))
            if identity not in known:
                self.context.unsupported(
                    "GROUPING arguments must be GROUP BY expressions",
                    argument,
                    code=ErrorCode.UNSUPPORTED_AGGREGATION,
                )
            if identity not in included:
                mask |= 1 << (len(arguments) - index - 1)
        return mask

    def _aggregate_call(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> tuple[AggregateCall, ScalarSort]:
        filter_expression = None
        aggregate = aggregate_expression(expression)
        if isinstance(aggregate, exp.Filter):
            filter_node = aggregate.expression
            filter_expression = (
                filter_node.this if isinstance(filter_node, exp.Where) else filter_node
            )
            aggregate = aggregate.this

        argument = aggregate.this
        distinct = False
        if isinstance(argument, exp.Distinct):
            values = tuple(argument.expressions)
            if len(values) != 1:
                self.context.unsupported(
                    "DISTINCT aggregates require one argument",
                    expression,
                    code=ErrorCode.UNSUPPORTED_AGGREGATION,
                )
            argument = values[0]
            distinct = True
        if isinstance(argument, exp.Star) or argument is None:
            argument = None
            argument_sort = None
            argument_plan = None
        else:
            argument_plan = self.scalar.plan(argument, environment)
            argument_sort = argument_plan.sort

        aggregate_spec = self._aggregate_spec(aggregate, argument_sort)
        aggregate_id = self.context.catalog.context.intern_aggregate(aggregate_spec)
        if argument_plan is None:
            argument_body = None
        else:
            def lower_argument(row: TermRef) -> TermRef:
                value = argument_plan.emit(replace(environment, row=row))
                return value

            argument_body = lower_argument

        filter_body = (
            None
            if filter_expression is None
            else lambda row: self.expressions.lower_condition(
                filter_expression,
                replace(environment, row=row),
            )
        )
        return (
            self.context.builder.aggregate_call(
                aggregate_id,
                argument=argument_body,
                filter=filter_body,
                distinct=distinct,
            ),
            aggregate_spec.output,
        )

    def _any_value_call(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> tuple[AggregateCall, ScalarSort]:
        plan = self.scalar.plan(expression, environment)
        output_sort = ScalarSort(plan.sort.sql_type, True)
        name = "__sqlite_arbitrary_value"
        aggregate = self.context.catalog.context.intern_aggregate(
            AggregateSpec(plan.sort, output_sort, operator=name)
        )
        return (
            self.context.builder.aggregate_call(
                aggregate,
                argument=lambda row: plan.emit(replace(environment, row=row)),
            ),
            output_sort,
        )

    def _aggregate_spec(
        self,
        expression: exp.AggFunc,
        input_sort: ScalarSort | None,
    ) -> AggregateSpec:
        """Type one aggregate and return its canonical IR declaration."""

        name = function_name(expression).casefold()
        if isinstance(expression, exp.Count):
            return AggregateSpec(
                input_sort,
                ScalarSort(INTEGER, False),
                kind=AggregateKind.COUNT,
                operator="count",
            )
        if input_sort is None:
            raise CatalogError(f"Aggregate {name} requires one argument")

        if isinstance(expression, (exp.Min, exp.Max)):
            kind = (
                AggregateKind.MIN
                if isinstance(expression, exp.Min)
                else AggregateKind.MAX
            )
            return AggregateSpec(
                input_sort,
                ScalarSort(input_sort.sql_type, True),
                kind=kind,
                operator=name,
            )

        numeric = input_sort.sql_type.kind in (
            TypeKind.INTEGER,
            TypeKind.FLOAT,
            TypeKind.DECIMAL,
        )
        if not numeric and self.context.dialect.name != "sqlite":
            raise CatalogError(f"Aggregate {name} requires a numeric argument")

        if isinstance(expression, exp.Sum):
            if self.context.dialect.name == "sqlite":
                output_type = (
                    INTEGER
                    if input_sort.sql_type.kind in (TypeKind.BOOLEAN, TypeKind.INTEGER)
                    else FLOAT
                )
            else:
                output_type = input_sort.sql_type
            return AggregateSpec(
                input_sort,
                ScalarSort(output_type, True),
                kind=AggregateKind.SUM,
                operator="sum",
            )

        if isinstance(expression, exp.Avg):
            # The average of decimals keeps fractional digits beyond the
            # argument's scale (PostgreSQL numeric, MySQL scale + 4).
            output_type = (
                DECIMAL
                if input_sort.sql_type.kind is TypeKind.DECIMAL
                and self.context.dialect.name != "sqlite"
                else FLOAT
            )
            return AggregateSpec(
                input_sort,
                ScalarSort(output_type, True),
                kind=AggregateKind.AVG,
                operator="avg",
            )

        if isinstance(expression, exp.StddevSamp):
            return AggregateSpec(
                input_sort,
                ScalarSort(FLOAT, True),
                operator="stddev_samp",
            )
        raise CatalogError(f"Unsupported aggregate {name}")

    def _distinct_groups(
        self,
        source: Relation,
        environment: EmitEnvironment,
        projections: tuple[Projection, ...],
        hidden: tuple[Projection, ...],
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        """Group DISTINCT rows and retain an arbitrary SQLite order value."""
        key_plans = tuple(
            self._projection_plan(projection, environment)
            for projection in projections
        )
        key_sorts = tuple(plan.sort for plan in key_plans)
        calls: list[AggregateCall] = []
        hidden_sorts: list[ScalarSort] = []
        for projection in hidden:
            call, result_sort = self._any_value_call(
                projection.expression, environment
            )
            calls.append(call)
            hidden_sorts.append(result_sort)

        key_schema = self.context.schema_for_sorts(key_sorts)
        output_schema = self.context.schema_for_sorts(
            (*key_sorts, *hidden_sorts)
        )

        def key_row(row: TermRef) -> TermRef:
            current = replace(environment, row=row)
            return self.context.builder.row(
                key_schema,
                tuple(plan.emit(current) for plan in key_plans),
            )

        term = self.context.builder.group_fold(
            self.as_bag(source), key_row, calls, output_schema
        )
        all_projections = (*projections, *hidden)
        all_sorts = (*key_sorts, *hidden_sorts)
        relation = Relation(
            term,
            output_schema,
            tuple(
                ColumnBinding(
                    projection.name,
                    sort,
                    frozenset(),
                )
                for projection, sort in zip(
                    all_projections, all_sorts, strict=True
                )
            ),
        )
        slots = {
            projection.key: FieldSlot(index, all_sorts[index])
            for index, projection in enumerate(all_projections)
        }
        return relation, EmitEnvironment(
            relation,
            relation.term,
            slots,
            outer_scopes,
        )
