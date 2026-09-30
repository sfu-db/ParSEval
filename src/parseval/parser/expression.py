"""Shared SQL scalar-value and three-valued-condition compilation."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Mapping, Protocol

from sqlglot import exp

from parseval.errors import ErrorCode, fail
from parseval.terms.context import Volatility
from parseval.terms.sorts import (
    ScalarSort,
    BOOLEAN,
    DATE,
    DECIMAL,
    FLOAT,
    INTEGER,
    INTERVAL,
    STRING,
    TIME,
    TIMESTAMP,
    IntervalValue,
    TypeKind,
    parse_interval_value,
)
from parseval.terms.builder import TermRef

from .context import LoweringSession
from .dialect import SQLDialect
from .scope import EmitEnvironment, OuterScope, Relation
from .syntax import boolean_literal, function_name, query_body, strip_alias


class RelationalExpressionService(Protocol):
    """Relational operations needed only by expressions containing subqueries."""

    active_ctes: Mapping[str, Relation]

    def lower(
        self,
        expression: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...] = (),
        ctes: Mapping[str, Relation] | None = None,
    ) -> Relation: ...

    def as_bag(self, relation: Relation) -> TermRef: ...

    def infer_scalar_query_sort(self, query: exp.Expression) -> ScalarSort: ...


@dataclass(frozen=True, slots=True)
class ExpressionCapabilities:
    """Contextual SQL features available while compiling an expression."""

    allow_subqueries: bool = True
    allow_aggregates: bool = True
    allow_windows: bool = True
    allow_collations: bool = True
    allow_non_immutable_functions: bool = True


QUERY_EXPRESSIONS = ExpressionCapabilities()
SCHEMA_EXPRESSIONS = ExpressionCapabilities(
    allow_subqueries=False,
    allow_aggregates=False,
    allow_windows=False,
    allow_collations=False,
    allow_non_immutable_functions=False,
)


def scalar_sort_from_expression(
    expression: object,
    dialect: SQLDialect,
    *,
    nullable: bool = True,
) -> ScalarSort:
    data_type = getattr(expression, "type", None)
    sql_type = dialect.sql_type(data_type)
    meta = getattr(expression, "meta", {}) or {}
    inferred_nullable = not bool(meta.get("nonnull"))
    return ScalarSort(sql_type, nullable and inferred_nullable)


@dataclass(frozen=True, slots=True)
class ScalarPlan:
    sort: ScalarSort
    emit: Callable[[EmitEnvironment], TermRef]


class ScalarCompiler:
    __slots__ = (
        "context",
        "capabilities",
        "condition",
        "relations",
        "expression_key",
    )

    def __init__(
        self,
        context: LoweringSession,
        capabilities: ExpressionCapabilities,
        relations: RelationalExpressionService | None,
        expression_key: Callable[[exp.Expression], str],
    ) -> None:
        self.context = context
        self.capabilities = capabilities
        self.condition = None
        self.relations = relations
        self.expression_key = expression_key

    def emit(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> TermRef:
        return self.plan(expression, environment).emit(environment)

    def coercible_pair(
        self,
        left: exp.Expression,
        right: exp.Expression,
        environment: EmitEnvironment,
    ) -> tuple[ScalarPlan, ScalarPlan]:
        """Elaborate operands while retaining SQL's contextual literal typing."""

        left_is_literal = isinstance(left, (exp.Literal, exp.Null))
        right_is_literal = isinstance(right, (exp.Literal, exp.Null))
        if left_is_literal and not right_is_literal:
            right_plan = self.plan(right, environment)
            return self.plan(left, environment, right_plan.sort), right_plan
        if right_is_literal and not left_is_literal:
            left_plan = self.plan(left, environment)
            return left_plan, self.plan(right, environment, left_plan.sort)
        return self.plan(left, environment), self.plan(right, environment)

    def plan(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
        expected: ScalarSort | None = None,
    ) -> ScalarPlan:
        expression = strip_alias(expression)
        materialized = environment.expressions.get(
            self.expression_key(expression)
        )
        if materialized is not None:
            return ScalarPlan(
                materialized.sort,
                lambda current: self.context.builder.field(
                    current.row, materialized.index
                ),
            )

        if isinstance(expression, exp.Subquery):
            return self._subquery_plan(expression)
        if isinstance(expression, exp.Column):
            return self._column_plan(expression, environment)
        if isinstance(expression, exp.Null):
            sql_type = (
                expected.sql_type
                if expected is not None
                else self.context.dialect.sql_type(expression.type)
            )
            return ScalarPlan(
                ScalarSort(sql_type, True),
                lambda _current: self.context.builder.null(sql_type),
            )
        if isinstance(expression, exp.Boolean):
            value = boolean_literal(expression)
            return ScalarPlan(
                ScalarSort(BOOLEAN, False),
                lambda _current: self.context.builder.literal(value, BOOLEAN),
            )
        if isinstance(expression, exp.Literal):
            if expected is not None:
                sql_type = expected.sql_type
            elif expression.type is None:
                sql_type = (
                    STRING
                    if expression.is_string
                    else INTEGER
                    if expression.is_int
                    else DECIMAL
                )
            else:
                sql_type = self.context.dialect.sql_type(expression.type)
            value = self.context.dialect.literal_value(expression, sql_type)
            return ScalarPlan(
                ScalarSort(sql_type, False),
                lambda _current: self.context.builder.literal(value, sql_type),
            )
        if isinstance(expression, exp.Interval):
            return self._interval_plan(expression)
        if isinstance(expression, exp.Cast):
            if expression.type is None and isinstance(
                expression.args.get("to"), exp.DataType
            ):
                target = ScalarSort(
                    self.context.dialect.scalar_type(expression.args["to"]),
                    True,
                )
            else:
                target = scalar_sort_from_expression(
                    expression, self.context.dialect
                )
            argument = self.plan(expression.this, environment, target)
            target = ScalarSort(target.sql_type, argument.sort.nullable)
            return ScalarPlan(
                target,
                lambda current: self.context.cast_term(
                    argument.emit(current),
                    argument.sort,
                    target,
                    expression,
                ),
            )
        if isinstance(expression, exp.If):
            return self._case_plan(
                exp.Case(
                    ifs=[
                        exp.If(
                            this=expression.this.copy(),
                            true=expression.args["true"].copy(),
                        )
                    ],
                    default=(
                        expression.args["false"].copy()
                        if isinstance(expression.args.get("false"), exp.Expression)
                        else exp.Null()
                    ),
                ),
                environment,
            )
        if isinstance(expression, exp.Case):
            return self._case_plan(expression, environment)
        if isinstance(expression, (exp.Predicate, exp.Connector, exp.Not)):
            return ScalarPlan(
                ScalarSort(BOOLEAN, True),
                lambda current: self.condition_value(expression, current),
            )

        if isinstance(expression, exp.DPipe):
            plans = (
                self.plan(expression.this, environment),
                self.plan(expression.expression, environment),
            )
            arguments = tuple(
                ScalarSort(STRING, plan.sort.nullable) for plan in plans
            )
            result = ScalarSort(STRING, any(sort.nullable for sort in arguments))
            return ScalarPlan(
                result,
                lambda current: self.context.builder.apply(
                    "concat",
                    tuple(
                        self.context.cast_term(
                            plan.emit(current), plan.sort, target, expression
                        )
                            for plan, target in zip(plans, arguments, strict=True)
                    ),
                    result,
                ),
            )

        if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
            return self._binary_plan(expression, environment)
        if isinstance(expression, exp.Neg):
            return self._negation_plan(expression, environment)
        if isinstance(expression, exp.Func) and not isinstance(expression, exp.AggFunc):
            return self._function_plan(expression, environment)

        self.context.unsupported(
            "Unsupported scalar SQL expression",
            expression,
            code=ErrorCode.UNSUPPORTED_EXPRESSION,
        )

    def condition_value(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> TermRef:
        predicate = self.condition.emit(expression, environment)
        true_value = self.context.builder.literal(True, BOOLEAN)
        false_value = self.context.builder.literal(False, BOOLEAN)
        return self.context.builder.case(
            predicate,
            true_value,
            self.context.builder.case(
                self.context.builder.not3(predicate),
                false_value,
                self.context.builder.null(BOOLEAN),
            ),
        )

    def _column_plan(
        self,
        expression: exp.Column,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        resolved = self.context.binder.resolve(
            expression,
            environment.relation,
            outer_scopes=environment.outer_scopes,
        )
        scope, index, binding = resolved
        return ScalarPlan(
            binding.sort,
            lambda current: self.context.builder.field(
                current.row if scope is None else scope.row,
                index,
            ),
        )

    def _case_plan(
        self,
        expression: exp.Case,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        operand = expression.this
        operand_plan = (
            self.plan(operand, environment)
            if isinstance(operand, exp.Expression)
            else None
        )
        default = expression.args.get("default")
        branches = tuple(expression.args.get("ifs") or ())
        value_expressions = [branch.args["true"] for branch in branches]
        if isinstance(default, exp.Expression) and not isinstance(default, exp.Null):
            value_expressions.append(default)
        nullable = default is None or isinstance(default, exp.Null)
        value_plans: list[ScalarPlan] = []
        for value in value_expressions:
            if isinstance(value, exp.Null):
                nullable = True
                continue
            plan = self.plan(value, environment)
            nullable = nullable or plan.sort.nullable
            value_plans.append(plan)
        if value_plans:
            result_sort = value_plans[0].sort
            for candidate in value_plans[1:]:
                result_sort = self.context.require_common_scalar_sort(
                    result_sort,
                    candidate.sort,
                    expression,
                    message="CASE branches have incompatible scalar types",
                )
            result_sort = ScalarSort(result_sort.sql_type, nullable)
        else:
            result_sort = scalar_sort_from_expression(expression, self.context.dialect)

        plans = {
            id(value): self.plan(value, environment)
            for value in value_expressions
            if not isinstance(value, exp.Null)
        }
        condition_plans = (
            {
                id(branch.this): self.plan(
                    branch.this,
                    environment,
                    operand_plan.sort,
                )
                for branch in branches
            }
            if operand_plan is not None
            else {}
        )

        def emit_case(current: EmitEnvironment) -> TermRef:
            result = (
                self._emit_case_value(default, plans, result_sort, current)
                if isinstance(default, exp.Expression)
                else self.context.builder.null(result_sort.sql_type)
            )
            for branch in reversed(branches):
                if operand_plan is None:
                    condition = self.condition.emit(branch.this, current)
                else:
                    left, right = self.context.coerce_scalar_terms(
                        (
                            operand_plan.emit(current),
                            condition_plans[id(branch.this)].emit(current),
                        ),
                        branch,
                    )
                    condition = self.context.builder.eq3(left, right)
                result = self.context.builder.case(
                    condition,
                    self._emit_case_value(
                        branch.args["true"],
                        plans,
                        result_sort,
                        current,
                    ),
                    result,
                )
            return result

        return ScalarPlan(result_sort, emit_case)

    def _emit_case_value(
        self,
        expression: exp.Expression,
        plans: dict[int, ScalarPlan],
        target: ScalarSort,
        environment: EmitEnvironment,
    ) -> TermRef:
        if isinstance(expression, exp.Null):
            return self.context.builder.null(target.sql_type)
        plan = plans[id(expression)]
        return self.context.cast_term(
            plan.emit(environment),
            plan.sort,
            target,
            expression,
        )

    def _subquery_plan(
        self,
        expression: exp.Subquery,
    ) -> ScalarPlan:
        query = query_body(expression)
        if not isinstance(query, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            self.context.unsupported(
                "Scalar subqueries must contain a query expression",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        if self.relations is None:
            self.context.unsupported(
                "Subqueries are unavailable in this expression context",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        inner = self.relations.infer_scalar_query_sort(query)
        return ScalarPlan(
            ScalarSort(inner.sql_type, True),
            lambda environment: self._emit_subquery(expression, environment),
        )

    def _emit_subquery(
        self,
        expression: exp.Subquery,
        environment: EmitEnvironment,
    ) -> TermRef:
        if self.relations is None:
            self.context.unsupported(
                "Subqueries are unavailable in this expression context",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        inner = self.relations.lower(
            expression,
            outer_scopes=(
                OuterScope(environment.relation, environment.row),
                *environment.outer_scopes,
            ),
            ctes=self.relations.active_ctes,
        )
        if len(inner.columns) != 1:
            self.context.unsupported(
                "Scalar subqueries must project exactly one column",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        return self.context.builder.scalarize(self.relations.as_bag(inner))

    def _application_plan(
        self,
        expression: exp.Expression,
        operator: str,
        plans: tuple[ScalarPlan, ...],
        parameters: tuple[ScalarSort, ...],
        result: ScalarSort,
        *,
        volatility: Volatility = Volatility.IMMUTABLE,
    ) -> ScalarPlan:
        if len(plans) != len(parameters):
            raise AssertionError("Scalar application arity mismatch")
        if (
            not self.capabilities.allow_non_immutable_functions
            and volatility is not Volatility.IMMUTABLE
        ):
            self.context.unsupported(
                "Non-immutable functions are unavailable in this expression context",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )

        def emit(current: EmitEnvironment) -> TermRef:
            arguments = tuple(
                self.context.cast_term(
                    plan.emit(current), plan.sort, target, expression
                )
                for plan, target in zip(plans, parameters, strict=True)
            )
            return self.context.builder.apply(
                operator, arguments, result, volatility=volatility
            )

        return ScalarPlan(result, emit)

    def _binary_plan(
        self,
        expression: exp.Binary,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        operator = expression.key
        plans = self.coercible_pair(
            expression.this,
            expression.expression,
            environment,
        )
        left, right = (plan.sort for plan in plans)
        left_kind = left.sql_type.kind
        right_kind = right.sql_type.kind
        nullable = left.nullable or right.nullable
        temporal = {TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP}
        numeric = {TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL}

        if operator in {"add", "sub"}:
            if left_kind is TypeKind.INTERVAL and right_kind is TypeKind.INTERVAL:
                result = ScalarSort(INTERVAL, nullable)
                return self._application_plan(
                    expression, operator, plans, (left, right), result
                )
            if (
                operator == "add"
                and left_kind is TypeKind.INTERVAL
                and right_kind in temporal
            ):
                output = TIMESTAMP if right_kind is TypeKind.DATE else right.sql_type
                return self._application_plan(
                    expression,
                    operator,
                    plans,
                    (left, right),
                    ScalarSort(output, nullable),
                )
            if right_kind is TypeKind.INTERVAL and left_kind in temporal:
                output = TIMESTAMP if left_kind is TypeKind.DATE else left.sql_type
                return self._application_plan(
                    expression,
                    operator,
                    plans,
                    (left, right),
                    ScalarSort(output, nullable),
                )
            if operator == "sub" and left_kind in temporal and right_kind == left_kind:
                output = INTEGER if left_kind is TypeKind.DATE else INTERVAL
                return self._application_plan(
                    expression,
                    operator,
                    plans,
                    (left, right),
                    ScalarSort(output, nullable),
                )

        if operator in {"mul", "div"} and TypeKind.INTERVAL in {
            left_kind,
            right_kind,
        }:
            multiplier = right if left_kind is TypeKind.INTERVAL else left
            if (
                multiplier.sql_type.kind in numeric
                and not (operator == "div" and left_kind is not TypeKind.INTERVAL)
            ):
                return self._application_plan(
                    expression,
                    operator,
                    plans,
                    (left, right),
                    ScalarSort(INTERVAL, nullable),
                )

        common = self.context.require_common_scalar_sort(
            left,
            right,
            expression,
            prefer_float=operator == "div",
            arithmetic=True,
        )
        if common.sql_type.kind not in numeric:
            self.context.unsupported(
                "Arithmetic requires numeric or temporal operands",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        if operator == "div" and common.sql_type.kind is TypeKind.INTEGER:
            self.context.unsupported(
                "Integer division requires explicit SQL cast semantics",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        result = ScalarSort(common.sql_type, nullable)
        return self._application_plan(
            expression, operator, plans, (common, common), result
        )

    def _negation_plan(
        self,
        expression: exp.Neg,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        plan = self.plan(expression.this, environment)
        if plan.sort.sql_type.kind not in (
            TypeKind.INTEGER,
            TypeKind.FLOAT,
            TypeKind.DECIMAL,
            TypeKind.INTERVAL,
        ):
            self.context.unsupported(
                "Unary minus requires a numeric or interval operand",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        return self._application_plan(
            expression, "neg", (plan,), (plan.sort,), plan.sort
        )

    def _interval_plan(self, expression: exp.Interval) -> ScalarPlan:
        if not isinstance(expression.this, exp.Literal):
            self.context.unsupported(
                "INTERVAL requires a literal value",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        text = str(expression.this.this)
        unit = expression.args.get("unit")
        try:
            if isinstance(unit, exp.Expression):
                value = IntervalValue.from_unit(Decimal(text), unit.name)
            else:
                value = parse_interval_value(text)
        except (ValueError, ArithmeticError) as error:
            self.context.unsupported(
                str(error),
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        return ScalarPlan(
            ScalarSort(INTERVAL, False),
            lambda _current: self.context.builder.literal(value, INTERVAL),
        )

    def _function_plan(
        self,
        expression: exp.Func,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        if isinstance(expression, exp.Extract):
            operands = (expression.expression,)
        elif isinstance(expression, exp.Substring):
            operands = tuple(
                operand
                for operand in (
                    expression.this,
                    expression.args.get("start"),
                    expression.args.get("length"),
                )
                if isinstance(operand, exp.Expression)
            )
        else:
            operands = tuple(expression.iter_expressions())
        if isinstance(expression, (exp.Coalesce, exp.Nullif)) and any(
            isinstance(operand, exp.Null) for operand in operands
        ):
            known = tuple(
                self.plan(operand, environment)
                for operand in operands
                if not isinstance(operand, exp.Null)
            )
            if known:
                target = known[0].sort
                for plan in known[1:]:
                    target = self.context.require_common_scalar_sort(
                        target, plan.sort, expression
                    )
            else:
                target = ScalarSort(STRING, True)
            plans = tuple(
                ScalarPlan(
                    ScalarSort(target.sql_type, True),
                    lambda _current, sql_type=target.sql_type: self.context.builder.null(
                        sql_type
                    ),
                )
                if isinstance(operand, exp.Null)
                else self.plan(operand, environment)
                for operand in operands
            )
        else:
            plans = tuple(self.plan(operand, environment) for operand in operands)
        sorts = tuple(plan.sort for plan in plans)
        signature = self._builtin_signature(expression, sorts)
        if signature is not None:
            operator, parameters, result, volatility = signature
            return self._application_plan(
                expression,
                operator,
                plans,
                parameters,
                result,
                volatility=volatility,
            )

        # Anonymous names are the only function form resolved through the SQL
        # namespace. Canonical SQLGlot builtin nodes never fall back to it.
        if isinstance(expression, exp.Anonymous):
            declaration = self.context.resolve_scalar_function(
                expression.name, sorts
            )
            specification = self.context.catalog.context.function(
                declaration.function
            )
            if (
                not self.capabilities.allow_non_immutable_functions
                and specification.volatility is not Volatility.IMMUTABLE
            ):
                self.context.unsupported(
                    "Non-immutable functions are unavailable in this "
                    "expression context",
                    expression,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )
            return ScalarPlan(
                specification.result,
                lambda current: self.context.builder.scalar_call(
                    declaration.function,
                    tuple(plan.emit(current) for plan in plans),
                ),
            )
        self.context.unsupported(
            f"Unsupported builtin function {function_name(expression)}",
            expression,
            code=ErrorCode.UNSUPPORTED_EXPRESSION,
        )

    def _builtin_signature(
        self,
        expression: exp.Func,
        arguments: tuple[ScalarSort, ...],
    ) -> tuple[
        str,
        tuple[ScalarSort, ...],
        ScalarSort,
        Volatility,
    ] | None:
        numeric = {TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL}
        name = (
            expression.name.casefold()
            if isinstance(expression, exp.Anonymous)
            else function_name(expression).casefold()
        )

        if isinstance(expression, exp.Abs):
            if len(arguments) != 1 or arguments[0].sql_type.kind not in numeric:
                return None
            return "abs", arguments, arguments[0], Volatility.IMMUTABLE

        if isinstance(expression, (exp.Lower, exp.Upper, exp.Length)):
            if len(arguments) != 1:
                return None
            result_type = INTEGER if isinstance(expression, exp.Length) else STRING
            parameters = (ScalarSort(STRING, arguments[0].nullable),)
            result = ScalarSort(result_type, arguments[0].nullable)
            return expression.key, parameters, result, Volatility.IMMUTABLE

        if isinstance(expression, exp.Substring) or name in {"substr", "substring"}:
            if len(arguments) not in (2, 3):
                return None
            parameters = (
                ScalarSort(STRING, arguments[0].nullable),
                *(ScalarSort(INTEGER, item.nullable) for item in arguments[1:]),
            )
            return (
                "substring",
                parameters,
                ScalarSort(STRING, any(item.nullable for item in arguments)),
                Volatility.IMMUTABLE,
            )

        if isinstance(expression, exp.Coalesce):
            if not arguments:
                return None
            common = arguments[0]
            for argument in arguments[1:]:
                common = self.context.require_common_scalar_sort(
                    common, argument, expression
                )
            parameters = tuple(
                ScalarSort(common.sql_type, argument.nullable)
                for argument in arguments
            )
            result = ScalarSort(
                common.sql_type, all(argument.nullable for argument in arguments)
            )
            return "coalesce", parameters, result, Volatility.IMMUTABLE

        if isinstance(expression, exp.Nullif):
            if len(arguments) != 2:
                return None
            common = self.context.require_common_scalar_sort(
                arguments[0], arguments[1], expression
            )
            return (
                "nullif",
                (common, common),
                ScalarSort(arguments[0].sql_type, True),
                Volatility.IMMUTABLE,
            )

        if isinstance(expression, exp.Round):
            if not arguments or len(arguments) > 2 or arguments[0].sql_type.kind not in numeric:
                return None
            parameters = (arguments[0],)
            if len(arguments) == 2:
                parameters += (ScalarSort(INTEGER, arguments[1].nullable),)
            return (
                "round",
                parameters,
                ScalarSort(
                    arguments[0].sql_type,
                    any(argument.nullable for argument in arguments),
                ),
                Volatility.IMMUTABLE,
            )

        if isinstance(expression, exp.Extract):
            if len(arguments) != 1 or arguments[0].sql_type.kind not in (
                TypeKind.DATE,
                TypeKind.TIME,
                TypeKind.TIMESTAMP,
                TypeKind.INTERVAL,
            ):
                return None
            unit = str(expression.this.name).casefold()
            return (
                f"extract_{unit}",
                arguments,
                ScalarSort(INTEGER, arguments[0].nullable),
                Volatility.IMMUTABLE,
            )

        if name in {
            "date",
            "julianday",
            "year",
            "ts_or_ds_to_timestamp",
        } and len(arguments) == 1:
            output = {
                "date": DATE,
                "julianday": FLOAT,
                "year": INTEGER,
                "ts_or_ds_to_timestamp": TIMESTAMP,
            }[name]
            return (
                name,
                arguments,
                ScalarSort(output, arguments[0].nullable),
                Volatility.IMMUTABLE,
            )

        if name == "time_to_str" and len(arguments) == 2:
            parameters = (
                arguments[0],
                ScalarSort(STRING, arguments[1].nullable),
            )
            return (
                name,
                parameters,
                ScalarSort(STRING, any(item.nullable for item in arguments)),
                Volatility.IMMUTABLE,
            )

        if name in {"current_timestamp", "curdate"} and not arguments:
            output = TIMESTAMP if name == "current_timestamp" else DATE
            return name, (), ScalarSort(output, False), Volatility.STABLE

        if name == "datetime":
            return (
                name,
                arguments,
                ScalarSort(TIMESTAMP, any(item.nullable for item in arguments)),
                Volatility.STABLE,
            )

        if name in {"time", "age"} and len(arguments) == 1:
            output = TIME if name == "time" else INTEGER
            return (
                name,
                arguments,
                ScalarSort(output, arguments[0].nullable),
                Volatility.IMMUTABLE,
            )

        if name == "date_part" and len(arguments) == 2:
            parameters = (
                ScalarSort(STRING, arguments[0].nullable),
                arguments[1],
            )
            return (
                name,
                parameters,
                ScalarSort(INTEGER, any(item.nullable for item in arguments)),
                Volatility.IMMUTABLE,
            )

        if name == "datediff" and len(arguments) == 2:
            return (
                name,
                arguments,
                ScalarSort(INTEGER, any(item.nullable for item in arguments)),
                Volatility.IMMUTABLE,
            )

        if name == "instr" and len(arguments) == 2:
            parameters = tuple(ScalarSort(STRING, item.nullable) for item in arguments)
            return (
                name,
                parameters,
                ScalarSort(INTEGER, any(item.nullable for item in arguments)),
                Volatility.IMMUTABLE,
            )
        return None


class ConditionCompiler:
    __slots__ = ("context", "scalar", "relations")

    def __init__(
        self,
        context: LoweringSession,
        scalar: ScalarCompiler,
        relations: RelationalExpressionService | None,
    ) -> None:
        self.context = context
        self.scalar = scalar
        self.relations = relations

    def emit(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> TermRef:
        expression = strip_alias(expression)
        builder = self.context.builder

        if isinstance(expression, exp.Boolean):
            return (
                builder.true3()
                if boolean_literal(expression)
                else builder.false3()
            )
        if isinstance(expression, exp.Null):
            return builder.unknown3()
        if isinstance(expression, exp.And):
            return builder.and3(
                self.emit(expression.this, environment),
                self.emit(expression.expression, environment),
            )
        if isinstance(expression, exp.Or):
            return builder.or3(
                self.emit(expression.this, environment),
                self.emit(expression.expression, environment),
            )
        if isinstance(expression, exp.Not):
            inner = expression.this
            if isinstance(inner, exp.Is) and isinstance(inner.expression, exp.Null):
                return builder.is_not_null(self.scalar.emit(inner.this, environment))
            return builder.not3(self.emit(expression.this, environment))
        if isinstance(expression, exp.EQ):
            if isinstance(expression.expression, exp.Any):
                return self._equals_any(
                    expression.this,
                    expression.expression,
                    environment,
                )
            return self._comparison(expression, environment, "eq")
        if isinstance(expression, exp.NEQ):
            return builder.not3(self._comparison(expression, environment, "eq"))
        if isinstance(expression, exp.LT):
            return self._comparison(expression, environment, "lt")
        if isinstance(expression, exp.GT):
            return self._comparison(expression, environment, "gt")
        if isinstance(expression, exp.LTE):
            return builder.not3(self._comparison(expression, environment, "gt"))
        if isinstance(expression, exp.GTE):
            return builder.not3(self._comparison(expression, environment, "lt"))
        if isinstance(expression, exp.NullSafeEQ):
            left, right = self._operands(expression, environment)
            return builder.is_not_distinct(left, right)
        if isinstance(expression, exp.NullSafeNEQ):
            left, right = self._operands(expression, environment)
            return builder.not3(builder.is_not_distinct(left, right))
        if isinstance(expression, exp.Is):
            value = self.scalar.emit(expression.this, environment)
            target = expression.expression
            if isinstance(target, exp.Null):
                return builder.is_null(value)
            if isinstance(target, exp.Boolean):
                value_sort = self.context.term_scalar_sort(value)
                if value_sort.sql_type.kind is not TypeKind.BOOLEAN:
                    fail(
                        ErrorCode.TYPE_ERROR,
                        "Boolean truth tests require a Boolean operand",
                        node=expression,
                    )
                return builder.is_not_distinct(
                    value,
                    builder.literal(boolean_literal(target), BOOLEAN),
                )
            self.context.unsupported(
                "Unsupported IS truth test",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        if isinstance(expression, exp.Between):
            return self._between(expression, environment)
        if isinstance(expression, exp.In):
            return self._in(expression, environment)
        if isinstance(expression, exp.Exists):
            if self.relations is None:
                self.context.unsupported(
                    "Subqueries are unavailable in this expression context",
                    expression,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )
            relation = self.relations.lower(
                expression.this,
                outer_scopes=(
                    OuterScope(environment.relation, environment.row),
                    *environment.outer_scopes,
                ),
                ctes=self.relations.active_ctes,
            )
            schema = self.context.schema_for_sorts(
                (ScalarSort(INTEGER, False),)
            )
            present = self.context.builder.scalarize(
                self.context.builder.distinct(
                    self.context.builder.map(
                        self.relations.as_bag(relation),
                        lambda _row: self.context.builder.row(
                            schema,
                            (self.context.builder.literal(1, INTEGER),),
                        ),
                    )
                )
            )
            return builder.is_not_null(present)
        if isinstance(expression, exp.Like):
            return self._like(expression, environment, case_sensitive=True)
        if isinstance(expression, exp.ILike):
            return self._like(expression, environment, case_sensitive=False)
        if isinstance(expression, exp.Escape):
            self.context.unsupported(
                "LIKE ... ESCAPE is not supported",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        if isinstance(expression, exp.Predicate):
            self.context.unsupported(
                f"{type(expression).__name__} requires a dedicated "
                "predicate lowering rule",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )

        scalar = self.scalar.emit(expression, environment)
        scalar_sort = self.context.term_scalar_sort(scalar)
        if scalar_sort.sql_type.kind is TypeKind.BOOLEAN:
            return builder.eq3(
                scalar,
                builder.literal(True, scalar_sort.sql_type),
            )
        if (
            self.context.dialect.name in ("sqlite", "mysql")
            and scalar_sort.sql_type.kind
            in (TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL)
        ):
            numeric_sort = ScalarSort(FLOAT, scalar_sort.nullable)
            numeric = self.context.cast_term(
                scalar,
                scalar_sort,
                numeric_sort,
                expression,
            )
            return builder.not3(
                builder.eq3(numeric, builder.literal(0.0, FLOAT))
            )
        self.context.unsupported(
            "Unsupported SQL predicate",
            expression,
            code=ErrorCode.UNSUPPORTED_EXPRESSION,
        )

    def _operands(
        self,
        expression: exp.Binary,
        environment: EmitEnvironment,
    ) -> tuple[TermRef, TermRef]:
        plans = self.scalar.coercible_pair(
            expression.this,
            expression.expression,
            environment,
        )
        return self.context.coerce_scalar_terms(
            tuple(plan.emit(environment) for plan in plans),
            expression,
        )

    def _equals_any(
        self,
        needle: exp.Expression,
        quantified: exp.Any,
        environment: EmitEnvironment,
    ) -> TermRef:
        source = quantified.this
        while isinstance(source, exp.Paren):
            source = source.this
        if isinstance(source, exp.Subquery):
            synthetic = exp.In(this=needle.copy(), query=source.copy())
            return self._in_subquery(synthetic, source, environment)
        if isinstance(source, exp.Array):
            candidates = tuple(source.expressions)
        elif isinstance(source, exp.Literal) and source.is_string:
            try:
                values = self.context.dialect.array_literal_values(str(source.this))
            except ValueError as error:
                self.context.unsupported(
                    str(error),
                    quantified,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )
            candidates = tuple(
                exp.Null() if value is None else exp.Literal.string(value)
                for value in values
            )
        else:
            self.context.unsupported(
                "ANY requires a subquery or array literal",
                quantified,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        if not candidates:
            return self.context.builder.false3()
        needle_plan = self.scalar.plan(needle, environment)
        predicates: list[TermRef] = []
        for candidate in candidates:
            candidate_plan = self.scalar.plan(
                candidate,
                environment,
                needle_plan.sort,
            )
            predicates.append(
                self.context.builder.eq3(
                    *self.context.coerce_scalar_terms(
                        (
                            needle_plan.emit(environment),
                            candidate_plan.emit(environment),
                        ),
                        quantified,
                    )
                )
            )
        result = predicates[0]
        for predicate in predicates[1:]:
            result = self.context.builder.or3(result, predicate)
        return result

    def _comparison(
        self,
        expression: exp.Binary,
        environment: EmitEnvironment,
        operation: str,
    ) -> TermRef:
        left, right = self._operands(expression, environment)
        if operation == "eq":
            return self.context.builder.eq3(left, right)
        if operation == "lt":
            return self.context.builder.lt3(left, right)
        return self.context.builder.lt3(right, left)

    def _between(
        self,
        expression: exp.Between,
        environment: EmitEnvironment,
    ) -> TermRef:
        value_plan = self.scalar.plan(expression.this, environment)
        value = value_plan.emit(environment)
        low_plan = self.scalar.plan(
            expression.args["low"], environment, value_plan.sort
        )
        high_plan = self.scalar.plan(
            expression.args["high"], environment, value_plan.sort
        )
        low_value, low = self.context.coerce_scalar_terms(
            (
                value,
                low_plan.emit(environment),
            ),
            expression,
        )
        high, high_value = self.context.coerce_scalar_terms(
            (
                high_plan.emit(environment),
                value,
            ),
            expression,
        )
        return self.context.builder.and3(
            self.context.builder.not3(self.context.builder.lt3(low_value, low)),
            self.context.builder.not3(self.context.builder.lt3(high, high_value)),
        )

    def _in(
        self,
        expression: exp.In,
        environment: EmitEnvironment,
    ) -> TermRef:
        query = expression.args.get("query")
        if query is not None:
            return self._in_subquery(expression, query, environment)
        values = tuple(expression.expressions)
        if not values:
            return self.context.builder.false3()
        if isinstance(expression.this, exp.Tuple):
            return self._row_in_values(expression.this, values, environment)
        needle = self.scalar.emit(expression.this, environment)
        predicates = [
            self.context.builder.eq3(
                *self.context.coerce_scalar_terms(
                    (
                        needle,
                        self.scalar.emit(value, environment),
                    ),
                    expression,
                )
            )
            for value in values
        ]
        result = predicates[0]
        for predicate in predicates[1:]:
            result = self.context.builder.or3(result, predicate)
        return result

    def _row_in_values(
        self,
        needle: exp.Tuple,
        candidates: tuple[exp.Expression, ...],
        environment: EmitEnvironment,
    ) -> TermRef:
        width = len(needle.expressions)
        rows: list[TermRef] = []
        for candidate in candidates:
            if not isinstance(candidate, exp.Tuple) or len(candidate.expressions) != width:
                fail(
                    ErrorCode.TYPE_ERROR,
                    "Row IN candidates must have matching widths",
                    node=candidate,
                )
            fields: list[TermRef] = []
            for left, right in zip(
                needle.expressions,
                candidate.expressions,
                strict=True,
            ):
                plans = self.scalar.coercible_pair(left, right, environment)
                operands = self.context.coerce_scalar_terms(
                    tuple(plan.emit(environment) for plan in plans),
                    candidate,
                )
                fields.append(self.context.builder.eq3(*operands))
            row = fields[0] if fields else self.context.builder.true3()
            for field in fields[1:]:
                row = self.context.builder.and3(row, field)
            rows.append(row)
        result = rows[0]
        for row in rows[1:]:
            result = self.context.builder.or3(result, row)
        return result

    def _in_subquery(
        self,
        expression: exp.In,
        query: exp.Expression,
        environment: EmitEnvironment,
    ) -> TermRef:
        if self.relations is None:
            self.context.unsupported(
                "Subqueries are unavailable in this expression context",
                expression,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        candidates = self.relations.lower(
            query,
            outer_scopes=(
                OuterScope(environment.relation, environment.row),
                *environment.outer_scopes,
            ),
            ctes=self.relations.active_ctes,
        )
        if len(candidates.columns) != 1:
            self.context.unsupported(
                "IN subqueries must project exactly one column",
                query,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )

        needle = self.scalar.emit(expression.this, environment)
        needle_sort = self.context.term_scalar_sort(needle)
        candidate_sort = candidates.columns[0].sort
        common = self.context.dialect.common_scalar_sort(needle_sort, candidate_sort)
        if common is None:
            self.context.unsupported(
                "IN subquery operands have incompatible SQL types",
                expression,
                code=ErrorCode.TYPE_ERROR,
            )

        needle_target = ScalarSort(common.sql_type, needle_sort.nullable)
        needle = self.context.cast_term(
            needle,
            needle_sort,
            needle_target,
            expression,
        )
        candidate_bag = self.relations.as_bag(candidates)
        if candidate_sort.sql_type != common.sql_type:
            candidate_target = ScalarSort(
                common.sql_type,
                candidate_sort.nullable,
            )
            output_schema = self.context.schema_for_sorts((candidate_target,))
            candidate_bag = self.context.builder.map(
                candidate_bag,
                lambda row: self.context.builder.row(
                    output_schema,
                    (
                        self.context.cast_term(
                            self.context.builder.field(row, 0),
                            candidate_sort,
                            candidate_target,
                            expression,
                        ),
                    ),
                ),
            )
        return self.context.builder.in_subquery(needle, candidate_bag)

    def _like(
        self,
        expression: exp.Like | exp.ILike,
        environment: EmitEnvironment,
        *,
        case_sensitive: bool,
    ) -> TermRef:
        if isinstance(expression.this, exp.Null) or isinstance(
            expression.expression, exp.Null
        ):
            predicate = self.context.builder.unknown3()
        else:
            value = self.scalar.emit(expression.this, environment)
            pattern = self.scalar.emit(expression.expression, environment)
            value_sort = self.context.term_scalar_sort(value)
            pattern_sort = self.context.term_scalar_sort(pattern)
            constructor = (
                self.context.builder.like3
                if case_sensitive
                else self.context.builder.ilike3
            )
            predicate = constructor(
                self.context.cast_term(
                    value,
                    value_sort,
                    ScalarSort(STRING, value_sort.nullable),
                    expression,
                ),
                self.context.cast_term(
                    pattern,
                    pattern_sort,
                    ScalarSort(STRING, pattern_sort.nullable),
                    expression,
                ),
            )
        return (
            self.context.builder.not3(predicate)
            if expression.args.get("negate")
            else predicate
        )


class ExpressionCompiler:
    """Compile SQL values and conditions against an explicit expression scope."""

    __slots__ = ("context", "capabilities", "scalar", "condition")

    def __init__(
        self,
        context: LoweringSession,
        *,
        relations: RelationalExpressionService | None = None,
        capabilities: ExpressionCapabilities = QUERY_EXPRESSIONS,
        expression_key: Callable[[exp.Expression], str] | None = None,
    ) -> None:
        self.context = context
        self.capabilities = capabilities
        key = expression_key or context.dialect.sql
        self.scalar = ScalarCompiler(context, capabilities, relations, key)
        self.condition = ConditionCompiler(context, self.scalar, relations)
        self.scalar.condition = self.condition

    def lower_value(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
        expected: ScalarSort | None = None,
    ) -> TermRef:
        self._validate(expression, environment)
        return self.scalar.plan(expression, environment, expected).emit(environment)

    def lower_condition(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> TermRef:
        self._validate(expression, environment)
        return self.condition.emit(expression, environment)

    def _validate(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> None:
        for node in expression.walk():
            if (
                isinstance(node, exp.Subquery)
                and not self.capabilities.allow_subqueries
            ):
                self._unsupported("Subqueries are unavailable", node)
            if (
                isinstance(node, exp.AggFunc)
                and not self.capabilities.allow_aggregates
            ):
                self._unsupported("Aggregate functions are unavailable", node)
            if isinstance(node, exp.Window) and not self.capabilities.allow_windows:
                self._unsupported("Window functions are unavailable", node)
            if (
                isinstance(node, exp.Column)
                and not self.capabilities.allow_collations
            ):
                resolved = self.context.binder.resolve(node, environment.relation)
                if resolved is not None and resolved[2].collation is not None:
                    self._unsupported("Collated expressions are unavailable", node)

    def _unsupported(self, feature: str, node: exp.Expression) -> None:
        self.context.unsupported(
            f"{feature} in this expression context",
            node,
            code=ErrorCode.UNSUPPORTED_EXPRESSION,
        )
