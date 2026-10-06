"""Lowering of window function calls and their frames."""

from __future__ import annotations

from dataclasses import replace

from sqlglot import exp

from parseval.errors import ErrorCode
from parseval.identifiers import Identifier
from parseval.parser.expression import ScalarPlan
from parseval.parser.helper import function_name, integer_literal
from parseval.parser.scope import ColumnBinding, EmitEnvironment, FieldSlot, Relation
from parseval.terms.builder import Direction, NullPlacement, OrderKey, WindowCall
from parseval.terms.sorts import INTEGER, ScalarSort
from parseval.terms.terms import (
    WindowBoundary,
    WindowBoundaryKind,
    WindowFrame,
    WindowFrameExclusion,
    WindowFrameMode,
    WindowFunctionKind,
)


class WindowLowering:
    """Lower window function calls into one window term. Mixed into ``QueryCompiler``."""

    __slots__ = ()

    def _window(
        self,
        source: Relation,
        environment: EmitEnvironment,
        expressions: tuple[exp.Window, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        calls = tuple(
            self._window_call(expression, source, environment)
            for expression in expressions
        )
        result_sorts = tuple(call.result for call in calls)
        source_sorts = self.context.catalog.context.schema(source.schema).fields
        output_schema = self.context.schema_for_sorts((*source_sorts, *result_sorts))
        term = self.context.builder.window(
            self.as_bag(source),
            calls,
            output_schema,
        )
        columns = source.columns + tuple(
            ColumnBinding(Identifier(f"_window{index}"), sort, frozenset())
            for index, sort in enumerate(result_sorts)
        )
        relation = Relation(term, output_schema, columns)
        slots = dict(environment.expressions)
        offset = len(source.columns)
        for index, (expression, sort) in enumerate(
            zip(expressions, result_sorts, strict=True)
        ):
            slots[self.analyzer.key(expression)] = FieldSlot(
                offset + index,
                sort,
            )
        return relation, EmitEnvironment(
            relation,
            relation.term,
            slots,
            environment.outer_scopes,
        )

    def _window_call(
        self,
        expression: exp.Window,
        source: Relation,
        environment: EmitEnvironment,
    ) -> WindowCall:
        function = expression.this
        filter_expression = None
        if isinstance(function, exp.Filter):
            filter_node = function.expression
            filter_expression = (
                filter_node.this if isinstance(filter_node, exp.Where) else filter_node
            )
            function = function.this

        aggregate_id = None
        distinct = False
        argument_plans: tuple[ScalarPlan, ...]
        name = function_name(function).casefold()
        if isinstance(function, (exp.Lag, exp.Lead)):
            kind = (
                WindowFunctionKind.LAG
                if isinstance(function, exp.Lag)
                else WindowFunctionKind.LEAD
            )
            operands = tuple(function.iter_expressions())
            if not operands:
                self.context.unsupported(
                    f"{name.upper()} requires a value argument",
                    expression,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )
            value_plan = self.scalar.plan(operands[0], environment)
            plans = [value_plan]
            if len(operands) >= 2:
                plans.append(
                    self.scalar.plan(
                        operands[1],
                        environment,
                        ScalarSort(INTEGER, False),
                    )
                )
            if len(operands) >= 3:
                plans.append(
                    self.scalar.plan(operands[2], environment, value_plan.sort)
                )
            argument_plans = tuple(plans)
            result_sort = ScalarSort(value_plan.sort.sql_type, True)
        elif isinstance(function, exp.AggFunc):
            argument = function.this
            if isinstance(argument, exp.Distinct):
                arguments = tuple(argument.expressions)
                if len(arguments) != 1:
                    self.context.unsupported(
                        "DISTINCT window aggregates require one argument",
                        expression,
                        code=ErrorCode.UNSUPPORTED_AGGREGATION,
                    )
                argument = arguments[0]
                distinct = True
            if argument is None or isinstance(argument, exp.Star):
                argument_plans = ()
                argument_sort = None
            else:
                plan = self.scalar.plan(argument, environment)
                argument_plans = (plan,)
                argument_sort = plan.sort
            aggregate = self._aggregate_spec(function, argument_sort)
            aggregate_id = self.context.catalog.context.intern_aggregate(aggregate)
            kind = WindowFunctionKind.AGGREGATE
            result_sort = aggregate.output
        else:
            ranking = {
                "row_number": WindowFunctionKind.ROW_NUMBER,
                "rank": WindowFunctionKind.RANK,
                "dense_rank": WindowFunctionKind.DENSE_RANK,
            }
            if name in ranking:
                kind = ranking[name]
                argument_plans = ()
                result_sort = ScalarSort(INTEGER, False)
            else:
                self.context.unsupported(
                    f"Unsupported window function {function_name(function)}",
                    expression,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )

        row_environment = lambda row: replace(
            environment,
            relation=source,
            row=row,
        )
        arguments = tuple(
            lambda row, plan=plan: plan.emit(row_environment(row))
            for plan in argument_plans
        )
        partition_plans = tuple(
            self.scalar.plan(item, environment)
            for item in expression.args.get("partition_by") or ()
        )
        partitions = tuple(
            lambda row, plan=plan: plan.emit(row_environment(row))
            for plan in partition_plans
        )
        order_keys: list[OrderKey] = []
        order = expression.args.get("order")
        if isinstance(order, exp.Order):
            for ordered in order.expressions:
                plan = self.scalar.plan(ordered.this, environment)
                descending = bool(ordered.args.get("desc"))
                order_keys.append(
                    self.context.builder.order_key(
                        lambda row, plan=plan: plan.emit(row_environment(row)),
                        direction=(Direction.DESC if descending else Direction.ASC),
                        nulls=(
                            NullPlacement.FIRST
                            if self.context.nulls_first(
                                ordered, descending=descending
                            )
                            else NullPlacement.LAST
                        ),
                        collation=self._collation(
                            ordered.this,
                            source,
                            environment.outer_scopes,
                            environment.expressions,
                        ),
                    )
                )

        filter_body = (
            None
            if filter_expression is None
            else lambda row: self.expressions.lower_condition(
                filter_expression,
                row_environment(row),
            )
        )
        return WindowCall(
            kind,
            result_sort,
            arguments,
            partitions,
            tuple(order_keys),
            self._window_frame(expression, bool(order_keys)),
            aggregate_id,
            filter_body,
            distinct,
        )

    def _window_frame(
        self,
        expression: exp.Window,
        ordered: bool,
    ) -> WindowFrame:
        spec = expression.args.get("spec")
        if not isinstance(spec, exp.WindowSpec):
            return WindowFrame(
                WindowFrameMode.RANGE,
                WindowBoundary(WindowBoundaryKind.UNBOUNDED_PRECEDING),
                WindowBoundary(
                    WindowBoundaryKind.CURRENT_ROW
                    if ordered
                    else WindowBoundaryKind.UNBOUNDED_FOLLOWING
                ),
            )
        mode = WindowFrameMode(str(spec.args.get("kind") or "range").casefold())
        exclusion_text = str(spec.args.get("exclude") or "no others")
        exclusion = WindowFrameExclusion(
            exclusion_text.casefold().replace(" ", "_")
        )
        return WindowFrame(
            mode,
            self._window_boundary(
                spec.args.get("start"),
                spec.args.get("start_side"),
            ),
            self._window_boundary(
                spec.args.get("end"),
                spec.args.get("end_side"),
            ),
            exclusion,
        )

    def _window_boundary(
        self,
        value: object,
        side: object,
    ) -> WindowBoundary:
        text = str(value or "CURRENT ROW").upper()
        direction = str(side or "").upper()
        if text == "UNBOUNDED":
            return WindowBoundary(
                WindowBoundaryKind.UNBOUNDED_PRECEDING
                if direction == "PRECEDING"
                else WindowBoundaryKind.UNBOUNDED_FOLLOWING
            )
        if text == "CURRENT ROW":
            return WindowBoundary(WindowBoundaryKind.CURRENT_ROW)
        if isinstance(value, exp.Expression):
            offset = integer_literal(value)
            if offset is not None:
                return WindowBoundary(
                    WindowBoundaryKind.OFFSET_PRECEDING
                    if direction == "PRECEDING"
                    else WindowBoundaryKind.OFFSET_FOLLOWING,
                    offset,
                )
        self.context.unsupported(
            "Window frame offsets must be nonnegative integer literals",
            value,
            code=ErrorCode.UNSUPPORTED_EXPRESSION,
        )
