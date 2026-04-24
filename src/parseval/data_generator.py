from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import reduce
import time, threading
import random
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TYPE_CHECKING,
)

import logging
from sqlglot import exp

from parseval.constants import PBit, PlausibleType, StepType
from parseval.helper import convert_to_literal, group_by_concrete, normalize_name
from parseval.instance import Instance
from parseval.plan import (
    Planner,
    Context,
    DerivedSchema,
    build_context_from_instance,
    build_graph_from_scopes,
)
from parseval.plan.helper import to_literal
from parseval.plan.rex import Const, Row, Symbol, negate_predicate
from parseval.solver.smt import SMTSolver
from parseval.uexpr.uexprs import Constraint, UExprToConstraint
from parseval.dtype import DataType

from .configuration import Config

if TYPE_CHECKING:
    from parseval.plan.planner import ScopeNode
    from parseval.uexpr.uexprs import PlausibleBranch


logger = logging.getLogger("parseval.coverage")


@dataclass
class OperatorConstraintRequest:
    bit: PBit
    node: Constraint
    context: Dict[str, Any]


@dataclass
class OperatorConstraintResult:
    referenced_columns: List[exp.Column] = field(default_factory=list)
    constraints: List[exp.Expression] = field(default_factory=list)
    requires_fk_closure: bool = True


@dataclass
class SubqueryBinding:
    scope_id: int
    expression: exp.Expression
    output_columns: Tuple[str, ...]
    rows: List[Tuple[Any, ...]]
    exists: bool
    scalar_value: Optional[Any] = None
    values: List[Any] = field(default_factory=list)
    correlated: bool = False


@dataclass
class ScopeSolveResult:
    scope_id: int
    scope_expression: exp.Expression
    tracer: UExprToConstraint
    context: Optional[Context]
    binding: Optional[SubqueryBinding]


class BaseGenerator(ABC):
    def __init__(self, expr: exp.Expression, instance: Instance, generator_config=None):
        super().__init__()
        self.expr = expr
        self.instance = instance
        self._table_alias: Optional[Dict[str, str]] = None
        self.generator_config = generator_config or Config()

    @property
    def dialect(self) -> Optional[str]:
        return self.instance.dialect if self.instance else None

    @property
    def table_alias(self) -> Dict[str, str]:
        if self._table_alias is None:
            alias = {}
            for table in self.expr.find_all(exp.Table):
                alias[table.alias_or_name] = self.instance._normalize_name(table.name)
            self._table_alias = alias
        return self._table_alias

    @abstractmethod
    def generate(
        self,
        timeout: int = 360,
        early_stop: Optional[Callable] = None,
        skips: Optional[Set[StepType]] = None,
    ):
        raise NotImplementedError

    def randomdb(self, min_rows: int, early_stop: Optional[Callable] = None):
        limit = self.expr.find(exp.Limit)
        offset = self.expr.find(exp.Offset)
        limit_value = int(limit.expression.this) if limit else 0
        offset_value = int(offset.expression.this) if offset else 0
        concretes = {table_name: {} for table_name in self.table_alias.values()}
        tries = 0
        while (
            tries
            < self.generator_config.positive_threshold
            + self.generator_config.negative_threshold
        ):
            for _ in range(max(limit_value + offset_value, min_rows)):
                for table_name in self.table_alias.values():
                    self.instance.create_row(table_name)
            tries += 1
            if early_stop and early_stop(self.instance):
                break


class OperatorRuleRegistry:
    def __init__(self):
        self._handlers = {
            PBit.TRUE: self._predicate,
            PBit.FALSE: self._predicate,
            PBit.JOIN_TRUE: self._join_true,
            PBit.JOIN_LEFT: self._join_left,
            PBit.JOIN_RIGHT: self._join_right,
            PBit.NULL: self._null,
            PBit.DUPLICATE: self._duplicate,
            PBit.GROUP_COUNT: self._group_count,
            PBit.GROUP_SIZE: self._group_size,
            PBit.GROUP_NULL: self._group_null,
            PBit.GROUP_DUPLICATE: self._group_duplicate,
            PBit.AGGREGATE_SIZE: self._aggregate_size,
            PBit.HAVING_TRUE: self._having,
            PBit.HAVING_FALSE: self._having,
            PBit.MAX: self._sort_extreme,
            PBit.MIN: self._sort_extreme,
        }

    def build(self, request: OperatorConstraintRequest) -> OperatorConstraintResult:
        handler = self._handlers.get(request.bit)
        if handler is None:
            return OperatorConstraintResult(
                referenced_columns=self._columns_from_expr(request.node.sql_condition)
            )
        return handler(request)

    def _result(
        self,
        request: OperatorConstraintRequest,
        *constraints: exp.Expression,
        referenced_columns: Optional[Sequence[exp.Column]] = None,
    ) -> OperatorConstraintResult:
        refs = list(
            referenced_columns or self._columns_from_expr(request.node.sql_condition)
        )
        items = [constraint for constraint in constraints if constraint is not None]
        return OperatorConstraintResult(referenced_columns=refs, constraints=items)

    def _columns_from_expr(
        self, expression: Optional[exp.Expression]
    ) -> List[exp.Column]:
        if expression is None:
            return []
        columns: Dict[str, exp.Column] = {}
        for column in expression.find_all(exp.Column):
                columns.setdefault(column.sql(), column)
        return list(columns.values())

    def _preferred_datatype(self, *candidates: Any) -> Optional[exp.DataType]:
        for candidate in candidates:
            if candidate is None:
                continue
            for attr in ("datatype", "type"):
                try:
                    datatype = getattr(candidate, attr, None)
                except Exception:
                    datatype = None
                if datatype is not None:
                    return datatype
        return None

    def _literal_for(self, value: Any, *type_candidates: Any) -> exp.Expression:
        return to_literal(value, self._preferred_datatype(*type_candidates))

    def _null_for(self, columnref: exp.Column) -> exp.Null:
        kwargs = {}
        datatype = self._preferred_datatype(columnref)
        if getattr(columnref, "type", None) is not None:
            kwargs["_type"] = columnref.type
        if datatype is not None:
            kwargs["datatype"] = datatype
        return exp.Null(**kwargs)

    def _select_group(self, node: Constraint, context: Dict[str, Any]):
        agg_group = context.get("agg_group")
        if agg_group is not None:
            return agg_group
        positive_bit = (
            PBit.AGGREGATE_SIZE
            if PBit.AGGREGATE_SIZE in node.coverage
            else PBit.GROUP_SIZE
        )
        for group in node.coverage.get(positive_bit, []):
            group_keys = getattr(group, "group_key", None)
            if group_keys and any(key.concrete is None for key in group_keys):
                continue
            context["agg_group"] = group
            return group
        context["agg_group"] = None
        return None

    def _predicate(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        constraint = request.node.sql_condition
        if request.bit == PBit.FALSE:
            constraint = negate_predicate(constraint)
        return self._result(request, constraint)

    def _join_true(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        return self._result(request, request.node.sql_condition)

    def _join_left(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:

        return self._result(request, negate_predicate(request.node.sql_condition))

    def _join_right(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        sql_condition = request.node.sql_condition
        left_column, right_column = sql_condition.this, sql_condition.expression
        return self._result(request, right_column)
        return self._result(request, negate_predicate(request.node.sql_condition))

    def _null(self, request: OperatorConstraintRequest) -> OperatorConstraintResult:
        constraints = []
        refs = self._columns_from_expr(request.node.sql_condition)
        for columnref in refs:
            constraints.append(
                exp.Is(
                    this=columnref,
                    expression=self._null_for(columnref),
                )
            )
        constraint = self._or_all(constraints)
        return self._result(request, constraint, referenced_columns=refs)

    def _duplicate(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        sql_condition = request.node.sql_condition
        refs = self._columns_from_expr(sql_condition)
        if not isinstance(sql_condition, exp.Column):
            return self._result(request, referenced_columns=refs)

        value_counts = group_by_concrete(request.node.coverage.get(request.bit, []))
        if not value_counts:
            positive_bit = (
                PBit.TRUE if PBit.TRUE in request.node.coverage else request.bit
            )
            value_counts = group_by_concrete(
                request.node.coverage.get(positive_bit, [])
            )
        if not value_counts:
            return self._result(request, referenced_columns=refs)

        _, symbols = sorted(value_counts.items(), key=lambda item: len(item[1]))[0]
        literal = self._literal_for(symbols[0].concrete, sql_condition)
        return self._result(request, sql_condition.eq(literal), referenced_columns=refs)

    def _group_count(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        node = request.node
        values = set()
        dtype = None
        for group in node.coverage.get(PBit.GROUP_SIZE, []):
            group_keys = getattr(group, "group_key", None)
            if group_keys and any(key.concrete is None for key in group_keys):
                continue
            for row in getattr(group, "group_values", []):
                value = row.get(node.sql_condition.table, node.sql_condition.name)
                values.add(value.concrete)
                dtype = value.datatype
                break
        constraints = [
            exp.NEQ(
                this=node.sql_condition,
                expression=self._literal_for(value, node.sql_condition, dtype),
            )
            for value in values
        ]
        return self._result(request, self._and_all(constraints))

    def _group_size(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        agg_group = self._select_group(request.node, request.context)
        if not agg_group:
            constraint = exp.Is(
                this=request.node.sql_condition,
                expression=self._null_for(request.node.sql_condition),
            ).not_()
            return self._result(request, constraint)

        for row in getattr(agg_group, "group_values", []):
            value = row.get(
                request.node.sql_condition.table, request.node.sql_condition.name
            )
            literal = self._literal_for(
                value.concrete, request.node.sql_condition, value
            )
            return self._result(
                request, exp.EQ(this=request.node.sql_condition, expression=literal)
            )
        return self._result(request)

    def _aggregate_size(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        self._select_group(request.node, request.context)
        return self._result(request)

    def _group_null(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        agg_group = self._select_group(request.node, request.context)
        if agg_group is None or len(getattr(agg_group, "group_values", [])) < 1:
            return self._result(request)
        constraints = []
        refs = self._columns_from_expr(request.node.sql_condition)
        for columnref in refs:
            constraints.append(
                exp.Is(
                    this=columnref,
                    expression=self._null_for(columnref),
                )
            )
        return self._result(request, self._or_all(constraints), referenced_columns=refs)

    def _group_duplicate(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        agg_group = self._select_group(request.node, request.context)
        if agg_group is None or len(getattr(agg_group, "group_values", [])) < 1:
            return self._result(request)

        refs = self._columns_from_expr(request.node.sql_condition)
        values_by_column: Dict[str, List[Any]] = {}
        refs_by_key = {column.sql(): column for column in refs}
        for row in getattr(agg_group, "group_values", []):
            for columnref in refs:
                value = row[columnref.name]
                if value.concrete is not None:
                    values_by_column.setdefault(columnref.sql(), []).append(value)

        disjuncts = []
        for key, values in values_by_column.items():
            columnref = refs_by_key[key]
            if columnref.args.get("is_unique", False):
                continue
            column_constraints = [
                exp.EQ(
                    this=columnref,
                    expression=self._literal_for(value.concrete, columnref, value),
                )
                for value in values
            ]
            disjunct = self._or_all(column_constraints)
            if disjunct is not None:
                disjuncts.append(disjunct)
        return self._result(request, self._or_all(disjuncts), referenced_columns=refs)

    def _having(self, request: OperatorConstraintRequest) -> OperatorConstraintResult:
        agg_group = self._select_group(request.node, request.context)
        sql_condition = request.node.sql_condition.copy()
        if agg_group is None or len(getattr(agg_group, "group_values", [])) < 1:
            if any(sql_condition.find_all(exp.AggFunc)):
                return self._result(
                    request, referenced_columns=self._columns_from_expr(sql_condition)
                )
            if request.bit == PBit.HAVING_FALSE:
                sql_condition = negate_predicate(sql_condition)
            return self._result(request, sql_condition)

        refs = self._columns_from_expr(sql_condition)
        group_rows = list(getattr(agg_group, "group_values", []))

        replacements: Dict[int, exp.Expression] = {}
        for agg_func in sql_condition.find_all(exp.AggFunc):
            replacement = self._aggregate_replacement(
                agg_func=agg_func,
                group_rows=group_rows,
            )
            if replacement is not None:
                replacements[id(agg_func)] = replacement

        if replacements:
            sql_condition = sql_condition.transform(
                lambda node: replacements.get(id(node), node)
            )
        if any(sql_condition.find_all(exp.AggFunc)):
            return self._result(request, referenced_columns=refs)
        if request.bit == PBit.HAVING_FALSE:
            sql_condition = negate_predicate(sql_condition)
        return self._result(request, sql_condition, referenced_columns=refs)

    def _aggregate_replacement(
        self,
        agg_func: exp.AggFunc,
        group_rows: Sequence[Row],
    ) -> Optional[exp.Expression]:
        def operand_expressions() -> List[exp.Expression]:
            distinct = agg_func.find(exp.Distinct)
            if distinct is not None:
                return list(distinct.expressions)
            if agg_func.this is None:
                return []
            return [agg_func.this]

        def evaluate_expression(expression: exp.Expression, row: Row) -> Any:
            transformed = expression.transform(
                lambda node: row[node.name] if isinstance(node, exp.Column) else node,
                copy=True,
            )
            if isinstance(transformed, Symbol):
                return transformed.concrete
            return getattr(transformed, "concrete", None)

        def expression_datatype(expression: exp.Expression) -> Optional[exp.DataType]:
            datatype = getattr(expression, "type", None)
            if datatype is not None:
                return datatype
            for column in expression.find_all(exp.Column):
                dtype = getattr(column, "type", None)
                if dtype is not None:
                    return dtype
            return None

        operands = operand_expressions()
        columns = list(agg_func.find_all(exp.Column))
        operand_dtype = expression_datatype(operands[0]) if operands else None

        if isinstance(agg_func, exp.Count):
            if not operands:
                return to_literal(len(group_rows), exp.DataType.build("INT"))
            if agg_func.find(exp.Distinct):
                values = []
                for row in group_rows:
                    evaluated = tuple(evaluate_expression(expr, row) for expr in operands)
                    if any(value is None for value in evaluated):
                        continue
                    values.append(evaluated)
                distinct_values = list(dict.fromkeys(values))
                return to_literal(len(distinct_values), exp.DataType.build("INT"))
            values = [
                evaluate_expression(operands[0], row)
                for row in group_rows
            ]
            return to_literal(
                len([value for value in values if value is not None]),
                exp.DataType.build("INT"),
            )
        if not operands:
            return None
        column = columns[0] if columns else None
        values = [evaluate_expression(operands[0], row) for row in group_rows]
        values = [value for value in values if value is not None]
        if not values:
            return self._null_for(column or exp.Column(this="expr", _type=operand_dtype))
        if agg_func.find(exp.Distinct):
            values = list(dict.fromkeys(values))
        if isinstance(agg_func, exp.Sum):
            return to_literal(sum(values), operand_dtype or getattr(column, "type", None))
        if isinstance(agg_func, exp.Max):
            return to_literal(max(values), operand_dtype or getattr(column, "type", None))
        if isinstance(agg_func, exp.Min):
            return to_literal(min(values), operand_dtype or getattr(column, "type", None))
        if isinstance(agg_func, exp.Avg):
            return to_literal(
                sum(values) / len(values),
                operand_dtype or getattr(column, "type", None),
            )
        return None

    def _sort_extreme(
        self, request: OperatorConstraintRequest
    ) -> OperatorConstraintResult:
        refs = self._columns_from_expr(request.node.sql_condition)
        values = []
        positive_bit = PBit.TRUE if PBit.TRUE in request.node.coverage else request.bit
        for smt_expr in request.node.coverage.get(positive_bit, []):
            values.extend(
                var.concrete
                for var in smt_expr.find_all(Symbol)
                if var.concrete is not None
            )
        if not refs or not values:
            return self._result(request, referenced_columns=refs)
        target = max(values) if request.bit == PBit.MAX else min(values)
        return self._result(
            request,
            exp.EQ(this=refs[0], expression=self._literal_for(target, refs[0])),
            referenced_columns=refs,
        )

    def _and_all(
        self, constraints: Sequence[exp.Expression]
    ) -> Optional[exp.Expression]:
        items = [constraint for constraint in constraints if constraint is not None]
        if not items:
            return None
        return reduce(lambda left, right: exp.And(this=left, expression=right), items)

    def _or_all(
        self, constraints: Sequence[exp.Expression]
    ) -> Optional[exp.Expression]:
        items = [constraint for constraint in constraints if constraint is not None]
        if not items:
            return None
        return reduce(lambda left, right: exp.Or(this=left, expression=right), items)


class DataGenerator(BaseGenerator):
    def __init__(
        self,
        expr: exp.Expression,
        instance: Instance,
        verbose: bool = False,
        config: Optional[Config] = None,
    ):
        super().__init__(expr, instance, generator_config=config)
        self.verbose = verbose
        self.constraints: Dict[str, Set[exp.Expression]] = {}
        self.variables: Dict[Tuple[str, str], exp.Column] = {}
        self.var_to_columnref: Dict[Tuple[str, str], exp.Column] = {}
        self.table_to_vars: Dict[str, List[exp.Column]] = {}
        self.table_column_to_vars: Dict[Tuple[str, str], List[exp.Column]] = {}
        self.columnref_to_vars: Dict[Tuple[str, str], List[exp.Column]] = {}
        self.bound_column_to_vars: Dict[Tuple[str, str], List[exp.Column]] = {}
        self.active_bound_tables: Dict[str, DerivedSchema] = {}
        self.scope_results: Dict[int, ScopeSolveResult] = {}
        self.operator_rules = OperatorRuleRegistry()

    def _predicate_constraints_for(
        self, expression: exp.Expression
    ) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        seen = set()
        predicate_types = (
            exp.EQ,
            exp.NEQ,
            exp.GT,
            exp.GTE,
            exp.LT,
            exp.LTE,
            exp.Like,
            exp.ILike,
            exp.Between,
            exp.In,
        )
        for predicate_type in predicate_types:
            for predicate in expression.find_all(predicate_type):
                normalized = self._normalize_predicate_constraint(predicate)
                candidate = normalized
                if (
                    candidate is None
                    and self._extract_seedable_predicate(predicate) is None
                    and self._extract_text_seedable_predicate(predicate) is None
                ):
                    continue
                if candidate is None:
                    candidate = predicate.copy()
                sql = candidate.sql(dialect=self.dialect)
                if sql in seen:
                    continue
                seen.add(sql)
                constraints.append(candidate)
        return constraints

    def _predicate_constraints(self) -> List[exp.Expression]:
        return self._predicate_constraints_for(self.expr)

    def _normalize_predicate_constraint(
        self, predicate: exp.Expression
    ) -> Optional[exp.Expression]:
        if isinstance(predicate, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)):
            left = predicate.args.get("this")
            right = predicate.args.get("expression")
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                return predicate.copy()
            if isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                swapped = predicate.copy()
                swapped.set("this", right.copy())
                swapped.set("expression", left.copy())
                return swapped
            return None
        if isinstance(predicate, exp.Between):
            if isinstance(predicate.this, exp.Column):
                return predicate.copy()
            return None
        if isinstance(predicate, exp.In):
            if isinstance(predicate.this, exp.Column) and all(
                isinstance(item, exp.Literal) for item in predicate.expressions
            ):
                return predicate.copy()
            return None
        return None

    def _extract_seed_target(
        self, expression: exp.Expression
    ) -> Optional[Tuple[str, exp.Column]]:
        if isinstance(expression, exp.Column):
            return "raw", expression

        if isinstance(expression, (exp.Cast, exp.TsOrDsToTimestamp)):
            inner = expression.this
            if isinstance(inner, exp.Column):
                return self._extract_seed_target(inner)

        is_strftime = isinstance(expression, exp.TimeToStr) or (
            isinstance(expression, exp.Anonymous)
            and (expression.name or "").upper() == "STRFTIME"
        )
        if not is_strftime:
            return None

        if isinstance(expression, exp.TimeToStr):
            fmt_expr = expression.args.get("format")
            value_expr = expression.this
        else:
            args = list(expression.expressions)
            if len(args) != 2:
                return None
            fmt_expr, value_expr = args

        if not isinstance(fmt_expr, exp.Literal) or fmt_expr.name != "%Y":
            return None

        if isinstance(value_expr, (exp.Cast, exp.TsOrDsToTimestamp)):
            value_expr = value_expr.this
        if isinstance(value_expr, exp.Column):
            return "strftime_year", value_expr
        return None

    def _extract_text_seed_target(
        self, expression: exp.Expression
    ) -> Optional[Tuple[str, exp.Column, Dict[str, Any]]]:
        if isinstance(expression, exp.Length):
            column = expression.this
            if isinstance(column, exp.Column):
                return "length", column, {}

        is_substr = isinstance(expression, exp.Substring) or (
            isinstance(expression, exp.Anonymous)
            and (expression.name or "").upper() in {"SUBSTR", "SUBSTRING"}
        )
        if not is_substr:
            return None

        if isinstance(expression, exp.Substring):
            source = expression.this
            start_expr = expression.args.get("start")
            length_expr = expression.args.get("length")
        else:
            args = list(expression.expressions)
            if len(args) < 2:
                return None
            source = args[0]
            start_expr = args[1]
            length_expr = args[2] if len(args) > 2 else None

        if not isinstance(source, exp.Column):
            return None

        def to_int(expr):
            if isinstance(expr, exp.Literal):
                return int(expr.this)
            if isinstance(expr, exp.Neg) and isinstance(expr.this, exp.Literal):
                return -int(expr.this.this)
            return None

        start = to_int(start_expr)
        length = to_int(length_expr) if length_expr is not None else None
        if start is None:
            return None
        return "substr", source, {"start": start, "length": length}

    def _extract_seedable_predicate(
        self, predicate: exp.Expression
    ) -> Optional[Tuple[str, exp.Column, str, object]]:
        if isinstance(predicate, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left = predicate.args.get("this")
            right = predicate.args.get("expression")
            if isinstance(right, exp.Literal):
                target = self._extract_seed_target(left)
                if target is not None:
                    kind, column = target
                    return kind, column, predicate.key.upper(), right.name
            if isinstance(left, exp.Literal):
                target = self._extract_seed_target(right)
                if target is not None:
                    kind, column = target
                    flipped = {
                        "GT": "LT",
                        "GTE": "LTE",
                        "LT": "GT",
                        "LTE": "GTE",
                    }.get(predicate.key.upper(), predicate.key.upper())
                    return kind, column, flipped, left.name
            return None

        if isinstance(predicate, exp.Between):
            target = self._extract_seed_target(predicate.this)
            if (
                target is not None
                and isinstance(predicate.args.get("low"), exp.Literal)
                and isinstance(predicate.args.get("high"), exp.Literal)
            ):
                kind, column = target
                return (
                    kind,
                    column,
                    "BETWEEN",
                    (predicate.args["low"].name, predicate.args["high"].name),
                )
            return None

        if isinstance(predicate, exp.In):
            target = self._extract_seed_target(predicate.this)
            if target is not None and all(
                isinstance(item, exp.Literal) for item in predicate.expressions
            ):
                kind, column = target
                return kind, column, "IN", [item.name for item in predicate.expressions]
            return None
        return None

    def _extract_text_seedable_predicate(
        self, predicate: exp.Expression
    ) -> Optional[Tuple[str, exp.Column, Dict[str, Any], str, object]]:
        if isinstance(predicate, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left = predicate.args.get("this")
            right = predicate.args.get("expression")
            if isinstance(right, exp.Literal):
                target = self._extract_text_seed_target(left)
                if target is not None:
                    kind, column, meta = target
                    return kind, column, meta, predicate.key.upper(), right.name
            if isinstance(left, exp.Literal):
                target = self._extract_text_seed_target(right)
                if target is not None:
                    kind, column, meta = target
                    flipped = {
                        "GT": "LT",
                        "GTE": "LTE",
                        "LT": "GT",
                        "LTE": "GTE",
                    }.get(predicate.key.upper(), predicate.key.upper())
                    return kind, column, meta, flipped, left.name
            return None

        if isinstance(predicate, exp.Between):
            target = self._extract_text_seed_target(predicate.this)
            if (
                target is not None
                and isinstance(predicate.args.get("low"), exp.Literal)
                and isinstance(predicate.args.get("high"), exp.Literal)
            ):
                kind, column, meta = target
                return (
                    kind,
                    column,
                    meta,
                    "BETWEEN",
                    (predicate.args["low"].name, predicate.args["high"].name),
                )
            return None

        if isinstance(predicate, exp.In):
            target = self._extract_text_seed_target(predicate.this)
            if target is not None and all(
                isinstance(item, exp.Literal) for item in predicate.expressions
            ):
                kind, column, meta = target
                return (
                    kind,
                    column,
                    meta,
                    "IN",
                    [item.name for item in predicate.expressions],
                )
        return None

    def _seed_text_of_length(self, length: int) -> str:
        return "a" * max(1, length)

    def _apply_length_seed(self, existing: Optional[str], op: str, value) -> str:
        if op == "IN":
            target = int(value[0])
        elif op == "BETWEEN":
            low, high = value
            target = max(int(low), 1)
            target = min(target, int(high))
        else:
            target = int(value)
            if op == "GT":
                target += 1
            elif op == "LT":
                target = max(target - 1, 1)
        if op == "GTE":
            target = int(value)
        if op == "LTE":
            target = int(value)

        current = existing or ""
        if len(current) < target:
            current = current + ("a" * (target - len(current)))
        elif len(current) > target:
            current = current[:target]
        if not current:
            current = self._seed_text_of_length(target)
        return current

    def _apply_substr_seed(
        self,
        existing: Optional[str],
        start: int,
        length: Optional[int],
        op: str,
        value,
    ) -> str:
        target = value
        if op == "IN":
            target = value[0]
        elif op == "BETWEEN":
            target = value[0]
        elif op == "NEQ":
            target = f"{value}x"

        target = str(target)
        length = length or len(target)
        base = existing or ""

        if start > 0:
            min_len = start - 1 + max(length, len(target))
            if len(base) < min_len:
                base = base + ("a" * (min_len - len(base)))
            index = start - 1
        else:
            min_len = max(abs(start), len(target))
            if len(base) < min_len:
                base = ("a" * (min_len - len(base))) + base
            index = len(base) + start
            if index < 0:
                base = ("a" * (-index)) + base
                index = 0

        segment = target[:length].ljust(length, "a")
        chars = list(base)
        for offset, ch in enumerate(segment):
            pos = index + offset
            if pos >= len(chars):
                chars.extend("a" * (pos - len(chars) + 1))
            chars[pos] = ch
        return "".join(chars)

    def _generate_temporal_seed_value(self, pool, op: str, value):
        def year_value(year: int, month: int, day: int):
            if pool.datatype.is_type(exp.DataType.Type.DATE):
                return date(year, month, day)
            return datetime(year, month, day)

        def parse_year(raw) -> int:
            return int(str(raw)[:4])

        if op == "IN":
            candidates = value if isinstance(value, list) else [value]
            return year_value(parse_year(candidates[0]), 1, 1)
        if op == "BETWEEN":
            low, high = value
            low_year = parse_year(low)
            high_year = parse_year(high)
            return year_value((low_year + high_year) // 2, 1, 1)

        year = parse_year(value)
        if op == "EQ":
            return year_value(year, 1, 1)
        if op == "NEQ":
            return year_value(year + 1, 1, 1)
        if op == "GT":
            return year_value(year + 1, 1, 1)
        if op == "GTE":
            return year_value(year, 1, 1)
        if op == "LT":
            return year_value(year - 1, 12, 31)
        if op == "LTE":
            return year_value(year, 12, 31)
        return None

    def _coerce_seed_value(self, pool, value):
        datatype = getattr(pool, "datatype", None)
        if datatype is None or value is None:
            return value
        text = value if isinstance(value, str) else str(value)
        try:
            if datatype.is_type(*exp.DataType.INTEGER_TYPES):
                return int(text)
            if datatype.is_type(*exp.DataType.REAL_TYPES):
                return float(text)
            if datatype.is_type(exp.DataType.Type.BOOLEAN):
                lowered = text.lower()
                if lowered in {"true", "1"}:
                    return True
                if lowered in {"false", "0"}:
                    return False
        except Exception:
            return value
        return value

    def _generate_raw_seed_value(self, pool, op: str, value):
        if op == "EQ":
            return self._coerce_seed_value(pool, value)
        if op == "IN":
            candidates = value if isinstance(value, list) else [value]
            return self._coerce_seed_value(pool, candidates[0]) if candidates else None
        if op == "BETWEEN":
            low, high = value
            low = self._coerce_seed_value(pool, low)
            high = self._coerce_seed_value(pool, high)
            if isinstance(low, int) and isinstance(high, int):
                return (low + high) // 2
            if isinstance(low, float) and isinstance(high, float):
                return (low + high) / 2.0
            return low
        return pool.generate_for_spec(op, value)

    def _seed_structured_scalar_rows(
        self, expression: Optional[exp.Expression] = None
    ) -> None:
        target = expression or self.expr
        parent: Dict[Tuple[str, str], Tuple[str, str]] = {}
        literals: Dict[Tuple[str, str], Any] = {}

        def find(key: Tuple[str, str]) -> Tuple[str, str]:
            parent.setdefault(key, key)
            if parent[key] != key:
                parent[key] = find(parent[key])
            return parent[key]

        def union(left: Tuple[str, str], right: Tuple[str, str]) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        def column_key(column: exp.Column) -> Optional[Tuple[str, str]]:
            table_ref = self.get_tableref(column.table) or column.table
            if not table_ref:
                return None
            return (table_ref, self.instance._normalize_name(column.name))

        def literal_value(column: exp.Column, literal: exp.Literal) -> Any:
            table_ref = self.get_tableref(column.table) or column.table
            if not table_ref:
                return literal.name
            try:
                pool = self.instance.column_domains.get_or_create_pool(
                    table_ref, column.name
                )
            except KeyError:
                return literal.name
            return self._generate_raw_seed_value(pool, "EQ", literal.name)

        for predicate in target.find_all(exp.EQ):
            left = predicate.args.get("this")
            right = predicate.args.get("expression")
            if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                left_key = column_key(left)
                right_key = column_key(right)
                if left_key is not None and right_key is not None:
                    union(left_key, right_key)
            elif isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                key = column_key(left)
                if key is not None:
                    literals[key] = literal_value(left, right)
            elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                key = column_key(right)
                if key is not None:
                    literals[key] = literal_value(right, left)

        component_values: Dict[Tuple[str, str], Any] = {}
        for key, value in literals.items():
            root = find(key)
            component_values.setdefault(root, value)

        values_by_table: Dict[str, Dict[str, Any]] = {}
        for key in list(parent) + list(literals):
            root = find(key)
            if root not in component_values:
                continue
            table_name, column_name = key
            values_by_table.setdefault(table_name, {})[column_name] = component_values[
                root
            ]

        for table_name, values in values_by_table.items():
            if values:
                self.instance.create_row(table_name, values=values)

    def _propagate_query_constraints_to_domains(
        self, expression: Optional[exp.Expression] = None
    ) -> int:
        applied = 0
        target = expression or self.expr
        for predicate in self._predicate_constraints_for(target):
            columns = list(predicate.find_all(exp.Column))
            if not columns:
                continue
            column = columns[0]
            table_ref = self.get_tableref(column.table) or column.table
            if not table_ref:
                continue
            try:
                pool = self.instance.column_domains.get_or_create_pool(
                    table_ref, column.name
                )
            except KeyError:
                continue
            pool.propagate_constraint(predicate)
            applied += 1
        return applied

    def _fallback_random_witness_search(
        self,
        early_stop: Optional[Callable] = None,
        deadline: Optional[float] = None,
    ) -> None:
        if early_stop is None:
            return
        self._propagate_query_constraints_to_domains()
        self._seed_literal_rows()
        if early_stop(self.instance):
            return
        limit = self.expr.find(exp.Limit)
        offset = self.expr.find(exp.Offset)
        limit_value = int(limit.expression.this) if limit else 0
        offset_value = int(offset.expression.this) if offset else 0
        min_rows = max(limit_value + offset_value, self.generator_config.min_rows, 1)
        table_refs = list(dict.fromkeys(self.table_alias.values()))
        rounds = max(
            1,
            self.generator_config.positive_threshold
            + self.generator_config.negative_threshold
            + 2,
        )
        for _ in range(rounds):
            if deadline is not None and time.monotonic() >= deadline:
                return
            for _ in range(min_rows):
                for table_name in table_refs:
                    try:
                        self.instance.create_row(table_name)
                    except Exception:
                        logger.debug(
                            "Fallback row generation skipped table %s due to generation error",
                            table_name,
                            exc_info=True,
                        )
            if early_stop(self.instance):
                return
            if deadline is not None and time.monotonic() >= deadline:
                return

    def _seed_literal_rows(self, expression: Optional[exp.Expression] = None) -> None:
        seeded_rows: Dict[str, Any] = {}
        literal_values = self._literal_values_by_table(expression)
        join_hints = self._join_value_hints()
        pending = [table_name for table_name in dict.fromkeys(self.table_alias.values()) if table_name]
        stalled = set()

        while pending:
            next_pending = []
            progress = False
            for table_name in pending:
                values = dict(literal_values.get(table_name, {}))
                unresolved_dependency = False

                for local_col, ref_table, ref_col in join_hints.get(table_name, []):
                    if local_col in values:
                        continue
                    if ref_table not in seeded_rows:
                        unresolved_dependency = True
                        continue
                    try:
                        values[local_col] = seeded_rows[ref_table][ref_col].concrete
                    except Exception:
                        continue

                for fk in self.instance.get_foreign_key(table_name):
                    local_col = self.instance._normalize_name(fk.expressions[0].name)
                    ref_table = self.instance._normalize_name(
                        fk.args.get("reference").find(exp.Table).name, is_table=True
                    )
                    ref_col = self.instance._normalize_name(
                        fk.args.get("reference").this.expressions[0].name
                    )
                    if local_col in values:
                        continue
                    if ref_table not in seeded_rows:
                        unresolved_dependency = True
                        continue
                    values[local_col] = seeded_rows[ref_table][ref_col].concrete

                if unresolved_dependency and table_name not in stalled:
                    next_pending.append(table_name)
                    continue

                try:
                    created = self.instance.create_row(table_name, values=values)
                except Exception:
                    stalled.add(table_name)
                    next_pending.append(table_name)
                    continue
                pos = created["positions"].get(table_name)
                if pos is None:
                    continue
                seeded_rows[table_name] = self.instance.get_row(table_name, pos)
                progress = True

            if not next_pending:
                break
            if not progress:
                stalled.update(next_pending)
            pending = next_pending

    def _literal_values_by_table(
        self, expression: Optional[exp.Expression] = None
    ) -> Dict[str, Dict[str, Any]]:
        values: Dict[str, Dict[str, Any]] = {}
        target = expression or self.expr
        for predicate in self._predicate_constraints_for(target):
            seed_target = self._extract_seedable_predicate(predicate)
            text_seed_target = self._extract_text_seedable_predicate(predicate)
            if seed_target is None and text_seed_target is None:
                continue
            if seed_target is not None:
                kind, column, op, seed_value = seed_target
            else:
                kind, column, meta, op, seed_value = text_seed_target
            table_ref = self.get_tableref(column.table) or column.table
            if not table_ref:
                continue
            try:
                pool = self.instance.column_domains.get_or_create_pool(
                    table_ref, column.name
                )
            except KeyError:
                continue
            concrete = None
            try:
                if kind == "raw":
                    concrete = self._generate_raw_seed_value(pool, op, seed_value)
                elif kind == "strftime_year":
                    concrete = self._generate_temporal_seed_value(
                        pool, op, seed_value
                    )
                elif kind == "length":
                    current = values.setdefault(table_ref, {}).get(column.name)
                    concrete = self._apply_length_seed(current, op, seed_value)
                elif kind == "substr":
                    current = values.setdefault(table_ref, {}).get(column.name)
                    concrete = self._apply_substr_seed(
                        current,
                        meta["start"],
                        meta["length"],
                        op,
                        seed_value,
                    )
            except Exception:
                logger.debug(
                    "Skipping literal seeding for predicate %s due to pool generation error",
                    predicate,
                    exc_info=True,
                )
            if concrete is not None:
                values.setdefault(table_ref, {})[column.name] = concrete
        return values

    def _join_value_hints(self) -> Dict[str, List[Tuple[str, str, str]]]:
        hints: Dict[str, List[Tuple[str, str, str]]] = {}
        for predicate in self.expr.find_all(exp.EQ):
            left = predicate.args.get("this")
            right = predicate.args.get("expression")
            if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                continue
            left_table = self.get_tableref(left.table) or left.table
            right_table = self.get_tableref(right.table) or right.table
            if not left_table or not right_table or left_table == right_table:
                continue
            hints.setdefault(left_table, []).append(
                (self.instance._normalize_name(left.name), right_table, self.instance._normalize_name(right.name))
            )
            hints.setdefault(right_table, []).append(
                (self.instance._normalize_name(right.name), left_table, self.instance._normalize_name(left.name))
            )
        return hints

    def get_tableref(self, alias_or_name: str) -> Optional[str]:
        if (
            alias_or_name not in self.table_alias
            and alias_or_name in self.instance.tables
        ):
            return alias_or_name
        return self.table_alias.get(alias_or_name)

    def declare_constraint(self, label, constraints):
        if constraints is None:
            return
        if not isinstance(constraints, list):
            constraints = [constraints]
        self.constraints.setdefault(str(label), set()).update(
            constraint for constraint in constraints if constraint is not None
        )

    def declare_variable(
        self, columnref: exp.Column, reuse: bool = True
    ) -> Tuple[str, str]:
        key = (columnref.table, columnref.name)
        if reuse and key in self.variables:
            return key

        def declare_synthetic(dtype=None):
            variable = exp.Column(this=key[1], table=key[0])
            variable.type = dtype or columnref.type or DataType.build("UNKNOWN")
            self.variables[key] = variable
            self.var_to_columnref[key] = columnref
            return key

        table_ref = self.get_tableref(columnref.table)
        if table_ref is None and columnref.table in self.active_bound_tables:
            variable = exp.Column(this=key[1], table=key[0])
            variable.type = (
                columnref.type
                or self.active_bound_tables[columnref.table].get_column_type(columnref.name)
                or DataType.build("UNKNOWN")
            )
            self.variables[key] = variable
            self.var_to_columnref[key] = columnref
            self.bound_column_to_vars.setdefault((columnref.table, columnref.name), []).append(
                variable
            )
            return key
        if table_ref is None:
            return ()
        if columnref.name not in self.instance.tables.get(table_ref, {}):
            return declare_synthetic()
        if not reuse:
            suffix = len(self.columnref_to_vars.get(key, []))
            while (columnref.table, f"{columnref.name}_{suffix}") in self.variables:
                suffix += 1
            key = (columnref.table, f"{columnref.name}_{suffix}")
        domain = self.instance.column_domains.get_or_create_pool(
            table=table_ref, column=columnref.name, alias=".".join(key)
        )
        variable = exp.Column(this=key[1], table=key[0])
        variable.type = domain.datatype
        self.variables[key] = variable
        self.var_to_columnref[key] = columnref
        self.table_to_vars.setdefault(table_ref, []).append(variable)
        self.columnref_to_vars.setdefault((columnref.table, columnref.name), []).append(
            variable
        )
        self.table_column_to_vars.setdefault((table_ref, columnref.name), []).append(
            variable
        )
        return key

    def _flatten_foreign_key_info(self, table_to_vars):
        fk_infos = {}
        for local_tbl in table_to_vars:
            table_ref = self.get_tableref(local_tbl)
            if table_ref is None:
                continue
            for fk in self.instance.get_foreign_key(table_ref):
                local_col = self.instance._normalize_name(fk.expressions[0].name)
                ref_table = self.instance._normalize_name(
                    fk.args.get("reference").find(exp.Table).name, is_table=True
                )
                ref_col = self.instance._normalize_name(
                    fk.args.get("reference").this.expressions[0].name
                )
                fk_infos[(local_tbl, local_col)] = (ref_table, ref_col)
        return fk_infos

    def _declare_fk_constraints(self):
        fk_infos = self._flatten_foreign_key_info(self.table_to_vars)
        for local_tbl, local_col in fk_infos:
            ref_table, ref_col = fk_infos[(local_tbl, local_col)]
            existing_values = self.instance.get_column_data(ref_table, ref_col)
            concretes = [
                convert_to_literal(value.concrete, value.datatype)
                for value in existing_values
            ]
            concretes.extend(self.table_column_to_vars.get((ref_table, ref_col), []))
            for variable in self.table_column_to_vars.get((local_tbl, local_col), []):
                fk_constraints = [variable.eq(concrete) for concrete in concretes]
                if fk_constraints:
                    self.declare_constraint(
                        "foreign_key",
                        reduce(lambda left, right: left.or_(right), fk_constraints),
                    )

    def _declare_pk_constraints(self):
        for (table_name, column_name), variables in self.table_column_to_vars.items():
            pk_columns = self.instance.get_primary_key(table_name)
            if column_name not in pk_columns:
                continue
            existing_values = self.instance.get_column_data(table_name, column_name)
            concretes = self._dedupe_literal_expressions(
                [
                    convert_to_literal(value.concrete, value.datatype)
                    for value in existing_values
                ]
            )
            if len(concretes) + len(variables) > 1:
                self.declare_constraint(
                    "primary_key",
                    exp.Distinct(expressions=concretes + variables, _type="bool"),
                )
            self.declare_constraint(
                "not_null",
                [
                    variable.is_(exp.Null(_type=variable.type)).not_()
                    for variable in variables
                ],
            )

    def _declare_column_constraints(self):
        for (table_name, column_name), variables in self.table_column_to_vars.items():
            for column_constraint in self.instance.get_column_constraints(
                table_name, column_name
            ):
                existing_values = self.instance.get_column_data(table_name, column_name)
                concretes = self._dedupe_literal_expressions(
                    [
                        convert_to_literal(value.concrete, value.datatype)
                        for value in existing_values
                    ]
                )
                if (
                    isinstance(
                        column_constraint.kind,
                        (exp.PrimaryKeyColumnConstraint, exp.UniqueColumnConstraint),
                    )
                    and len(concretes + variables) > 1
                ):
                    self.declare_constraint(
                        "unique_constraint",
                        exp.Distinct(expressions=concretes + variables, _type="bool"),
                    )
                if isinstance(
                    column_constraint.kind,
                    (exp.NotNullColumnConstraint, exp.PrimaryKeyColumnConstraint),
                ) and not column_constraint.kind.args.get("allow_null", False):
                    self.declare_constraint(
                        "not_null",
                        [
                            variable.is_(exp.Null(_type=variable.type)).not_()
                            for variable in variables
                        ],
                    )

    def _declare_db_constraints(self):
        self._declare_pk_constraints()
        self._declare_fk_constraints()
        self._declare_column_constraints()

    def _declare_bound_table_constraints(self):
        for (alias, column_name), variables in self.bound_column_to_vars.items():
            table = self.active_bound_tables.get(alias)
            if table is None:
                continue
            datatype = table.get_column_type(column_name)
            literals = self._dedupe_literal_expressions(
                [
                    to_literal(row[column_name].concrete, datatype)
                    for row in table.rows
                    if column_name in row
                ]
            )
            if not literals:
                continue
            for variable in variables:
                clauses = [variable.eq(literal) for literal in literals]
                self.declare_constraint(
                    "bound_table",
                    reduce(lambda left, right: left.or_(right), clauses),
                )

    def _reset_generation_state(self):
        self.variables.clear()
        self.constraints.clear()
        self.var_to_columnref.clear()
        self.table_to_vars.clear()
        self.table_column_to_vars.clear()
        self.columnref_to_vars.clear()
        self.bound_column_to_vars.clear()

    def _path_to_root(
        self, plausible: PlausibleBranch
    ) -> List[Tuple[Constraint, PBit]]:
        path = []
        node = plausible.parent
        bit = plausible.bit()
        while node is not None and node.step_type != StepType.ROOT:
            path.append((node, bit))
            bit = node.bit()
            node = node.parent
        return path

    def _dedupe_columns(self, columns: Sequence[exp.Column]) -> List[exp.Column]:
        deduped: Dict[str, exp.Column] = {}
        for column in columns:
            deduped.setdefault(column.sql(), column)
        return list(deduped.values())

    def _declare_variables(
        self,
        plausible: PlausibleBranch,
        scope_id: int,
        extra_columns: Optional[Sequence[exp.Column]] = None,
    ):
        columns = list(extra_columns or [])
        for node, _ in self._path_to_root(plausible):
            if node.scope_id != scope_id or node.sql_condition is None:
                continue
            columns.extend(node.sql_condition.find_all(exp.Column))
        for columnref in self._dedupe_columns(columns):
            self.declare_variable(columnref)

        fk_infos = self._flatten_foreign_key_info(self.table_to_vars)
        queue = deque(self.table_column_to_vars.keys())
        visited = set(self.table_column_to_vars.keys())
        while queue:
            local_table, local_col = queue.popleft()
            if (local_table, local_col) not in fk_infos:
                continue
            ref_table_name, ref_col_name = fk_infos[(local_table, local_col)]
            domain = self.instance.column_domains.get_or_create_pool(
                table=ref_table_name, column=ref_col_name
            )
            ref_column = exp.Column(
                this=ref_col_name, table=ref_table_name, _type=domain.datatype
            )
            ref_column.type = domain.datatype
            self.declare_variable(ref_column, reuse=False)
            fk_key = (ref_table_name, ref_col_name)
            if fk_key not in visited:
                visited.add(fk_key)
                queue.append(fk_key)

    def _apply_operator_rules(self, plausible: PlausibleBranch) -> List[exp.Column]:
        referenced_columns = []
        shared_context: Dict[str, Any] = {}
        for node, bit in self._path_to_root(plausible):
            request = OperatorConstraintRequest(
                bit=bit, node=node, context=shared_context
            )
            result = self.operator_rules.build(request)
            referenced_columns.extend(result.referenced_columns)
            self.declare_constraint(bit, result.constraints)
        return self._dedupe_columns(referenced_columns)

    def _create_rows_from_solver(self, result: Dict[str, Any]):

        concretes = {}
        # table_name: {} for table_name in self.table_to_vars

        for key in self.variables:
            var_name = ".".join(key)
            if var_name not in result:
                continue
            value = result[var_name]
            columnref = self.var_to_columnref[key]
            table_name = self.get_tableref(columnref.table)
            if table_name is None:
                continue
            if columnref.name not in self.instance.tables.get(table_name, {}):
                continue
            if table_name not in concretes:
                concretes[table_name] = {}
            concretes[table_name].setdefault(columnref.name, []).append(value)
        if any(values for values in concretes.values()):
            self.instance.create_rows(concretes)
        self._dedupe_instance_rows()

    def _dedupe_literal_expressions(
        self, expressions: Sequence[exp.Expression]
    ) -> List[exp.Expression]:
        deduped: Dict[str, exp.Expression] = {}
        for expression in expressions:
            if expression is None:
                continue
            normalized = (
                expression
                if isinstance(expression, exp.Expression)
                else convert_to_literal(expression)
            )
            deduped.setdefault(normalized.sql(dialect=self.dialect), normalized)
        return list(deduped.values())

    def _print_constraints(self, pattern: Tuple[PBit, ...]):
        if not self.verbose:
            return
        lines = [
            f"Coverage constraints for {'/'.join(str(bit.value) for bit in pattern)}"
        ]
        for label, constraints in self.constraints.items():
            for constraint in constraints:
                lines.append(f"[{label}] {constraint}")
        logger.info("\n".join(lines))

    def _const_from_value(self, value: Any, dtype=None) -> Const:
        if dtype is None or DataType.build(dtype).is_type(DataType.Type.UNKNOWN):
            if value is None:
                dtype = DataType.build("NULL")
            elif isinstance(value, bool):
                dtype = DataType.build("BOOLEAN")
            elif isinstance(value, int):
                dtype = DataType.build("INT")
            elif isinstance(value, float):
                dtype = DataType.build("FLOAT")
            elif isinstance(value, datetime):
                dtype = DataType.build("DATETIME")
            elif isinstance(value, date):
                dtype = DataType.build("DATE")
            else:
                dtype = DataType.build("TEXT")
        const = Const(this=value, _type=dtype)
        const.type = dtype
        return const

    def _binding_tables(
        self, scope_node: ScopeNode, binding: Optional[SubqueryBinding]
    ) -> Dict[str, DerivedSchema]:
        if binding is None or binding.correlated:
            return {}
        wrapper = self._find_subquery_wrapper(scope_node.scope.expression, binding.expression)
        if wrapper is None or not isinstance(wrapper, exp.Subquery):
            return {}
        if not isinstance(wrapper.parent, (exp.From, exp.Join)):
            return {}
        alias = wrapper.alias_or_name
        if not alias or not binding.output_columns:
            return {}

        datatypes = {}
        if isinstance(binding.expression, exp.Select):
            for project, label in zip(binding.expression.expressions, binding.output_columns):
                expr = project.this if isinstance(project, exp.Alias) else project
                datatypes[label] = expr.type or DataType.build("UNKNOWN")

        rows = []
        for index, values in enumerate(binding.rows):
            columns = {}
            for label, value in zip(binding.output_columns, values):
                columns[label] = self._const_from_value(value, datatypes.get(label))
            rows.append(Row(this=(f"{alias}_{index}",), columns=columns))

        return {
            alias: DerivedSchema(
                columns=binding.output_columns,
                rows=rows,
                datatypes=datatypes,
            )
        }

    def _planner_context(
        self,
        external: Optional[Context] = None,
        bound_tables: Optional[Dict[str, DerivedSchema]] = None,
    ) -> Context:
        base = build_context_from_instance(self.instance)
        tables = dict(base.tables)
        if bound_tables:
            tables.update(bound_tables)
        return Context(tables=tables, external=external)

    def _encode_scope(
        self,
        scope_node: ScopeNode,
        tracer: UExprToConstraint,
        external: Optional[Context] = None,
        bound_tables: Optional[Dict[str, DerivedSchema]] = None,
    ) -> Optional[Context]:
        tracer.reset()
        planner = Planner(
            ctx=self._planner_context(external=external, bound_tables=bound_tables),
            scope_node=scope_node,
            tracer=tracer,
            dialect=self.dialect,
            verbose=self.verbose,
        )
        r = planner.encode()
        return r

    def _bootstrap_scope_rows(self, scope_node: ScopeNode, min_rows: int = 1):
        table_refs = []
        for table in scope_node.scope.expression.find_all(exp.Table):
            table_ref = self.instance._normalize_name(table.name, is_table=True)
            if table_ref not in table_refs:
                table_refs.append(table_ref)
        for _ in range(min_rows):
            for table_ref in table_refs:
                self.instance.create_row(table_ref, values={})

    def _materialize_binding(
        self, scope_node: ScopeNode, context: Optional[Context]
    ) -> Optional[SubqueryBinding]:
        def empty_binding() -> SubqueryBinding:
            return SubqueryBinding(
                scope_id=scope_node.node_id,
                expression=scope_node.scope.expression,
                output_columns=tuple(),
                rows=[],
                exists=False,
                scalar_value=None,
                values=[],
                correlated=scope_node.is_correlated_dependency,
            )

        if context is None or not context.tables:
            return empty_binding()
        table = context.table
        output_columns = tuple()
        if isinstance(scope_node.scope.expression, exp.Select):
            output_columns = tuple(
                self._column_label(column)
                for column in scope_node.scope.expression.expressions
            )
        if not output_columns:
            output_columns = tuple(
                self._column_label(column) for column in table.columns
            )
        rows: List[Tuple[Any, ...]] = []
        for row in table.rows:
            materialized = []
            for column_name in output_columns:
                try:
                    materialized.append(row[column_name].concrete)
                except KeyError:
                    matched = None
                    for key, value in row.items():
                        if self.instance._normalize_name(
                            str(key), dialect=self.dialect
                        ) == self.instance._normalize_name(
                            column_name, dialect=self.dialect
                        ):
                            matched = value.concrete
                            break
                    if matched is None:
                        if scope_node.scope.is_subquery:
                            raise
                        logger.debug(
                            "Skipping root binding materialization for scope %s: missing projected column %s",
                            scope_node.node_id,
                            column_name,
                        )
                        return empty_binding()
                    materialized.append(matched)
            rows.append(tuple(materialized))
        limit_one = False
        if isinstance(scope_node.scope.expression, exp.Select):
            limit = scope_node.scope.expression.args.get("limit")
            limit_expr = getattr(limit, "expression", None)
            if isinstance(limit_expr, exp.Literal):
                try:
                    limit_one = int(limit_expr.this) == 1
                except Exception:
                    limit_one = False
        scalar_value = None
        if len(output_columns) == 1 and rows:
            if len(rows) == 1 or limit_one:
                scalar_value = rows[0][0]
        values = [row[0] for row in rows] if len(output_columns) == 1 else []
        return SubqueryBinding(
            scope_id=scope_node.node_id,
            expression=scope_node.scope.expression,
            output_columns=output_columns,
            rows=rows,
            exists=bool(rows),
            scalar_value=scalar_value,
            values=values,
            correlated=scope_node.is_correlated_dependency,
        )

    def _binding_literal(
        self, binding: SubqueryBinding, datatype=None
    ) -> Optional[exp.Expression]:
        if binding.scalar_value is None:
            return None
        return to_literal(binding.scalar_value, datatype)

    def _projected_source_column(
        self, expression: exp.Expression
    ) -> Optional[exp.Column]:
        target = expression.this if isinstance(expression, exp.Alias) else expression
        while isinstance(target, (exp.Cast, exp.TsOrDsToTimestamp)):
            target = target.this
        return target if isinstance(target, exp.Column) else None

    def _required_non_null_output_columns(
        self, scope_graph, scope_node: ScopeNode
    ) -> List[exp.Column]:
        required: Dict[str, exp.Column] = {}
        for dependent_id in scope_node.dependents:
            dependent = scope_graph.get_node(dependent_id)
            if dependent is None:
                continue
            wrapper = self._find_subquery_wrapper(
                dependent.scope.expression, scope_node.scope.expression
            )
            if wrapper is None or not isinstance(wrapper.parent, exp.In):
                continue
            if not isinstance(scope_node.scope.expression, exp.Select):
                continue
            for projection in scope_node.scope.expression.expressions:
                column = self._projected_source_column(projection)
                if column is None:
                    continue
                required.setdefault(column.sql(dialect=self.dialect), column)
        return list(required.values())

    def _repair_null_output_columns(
        self, columns: Optional[Sequence[exp.Column]]
    ) -> bool:
        if not columns:
            return False
        changed = False
        for columnref in columns:
            table_ref = self.get_tableref(columnref.table) or columnref.table
            if table_ref is None:
                continue
            try:
                pool = self.instance.column_domains.get_or_create_pool(
                    table_ref, columnref.name
                )
            except Exception:
                continue
            for row in self.instance.get_rows(table_ref):
                try:
                    current = row[columnref.name]
                except KeyError:
                    continue
                if current.concrete is not None:
                    continue
                replacement = None
                for _ in range(16):
                    candidate = pool.generate()
                    if candidate is not None:
                        replacement = candidate
                        break
                if replacement is None:
                    continue
                row.args.setdefault("columns", {})[
                    self.instance._normalize_name(columnref.name, dialect=self.dialect)
                ] = convert_to_literal(replacement, current.datatype)
                changed = True
        return changed

    def _find_subquery_wrapper(
        self, scope_expression: exp.Expression, child_expression: exp.Expression
    ) -> Optional[exp.Expression]:
        child_sql = child_expression.sql(dialect=self.dialect)
        for wrapper in scope_expression.find_all(exp.Subquery):
            if wrapper.this is child_expression:
                return wrapper
            if wrapper.this.sql(dialect=self.dialect) == child_sql:
                return wrapper
        return None

    def _apply_subquery_binding(
        self, scope_node: ScopeNode, binding: SubqueryBinding
    ) -> None:
        if binding.correlated:
            return
        wrapper = self._find_subquery_wrapper(
            scope_node.scope.expression, binding.expression
        )
        if wrapper is None or wrapper.parent is None:
            return
        parent = wrapper.parent
        if isinstance(parent, exp.Exists):
            parent.replace(to_literal(binding.exists, exp.DataType.build("BOOLEAN")))
            return
        if isinstance(parent, exp.In):
            values = [to_literal(value, None) for value in binding.values if value is not None]
            lhs = parent.this.copy()
            negate = isinstance(parent.parent, exp.Not) and parent.parent.this is parent
            if not values:
                replacement = exp.Boolean(this=negate)
                if negate and isinstance(parent.parent, exp.Not):
                    parent.parent.replace(replacement)
                else:
                    parent.replace(replacement)
                return
            predicates = [exp.EQ(this=lhs.copy(), expression=value) for value in values]
            constraint = predicates[0]
            for predicate in predicates[1:]:
                constraint = exp.Or(this=constraint, expression=predicate)
            replacement = constraint.not_() if negate else constraint
            if negate and isinstance(parent.parent, exp.Not):
                parent.parent.replace(replacement)
            else:
                parent.replace(replacement)
            return
        literal = self._binding_literal(binding, datatype=getattr(parent, "type", None))
        if literal is not None:
            wrapper.replace(literal)

    def _solve_plausible(
        self,
        scope_node: ScopeNode,
        plausible: PlausibleBranch,
    ) -> bool:
        extra_columns = self._apply_operator_rules(plausible)
        self._declare_variables(
            plausible=plausible,
            scope_id=scope_node.node_id,
            extra_columns=extra_columns,
        )
        self._declare_db_constraints()
        self._declare_bound_table_constraints()
        self._print_constraints(plausible.pattern())

        solver = SMTSolver(self.variables, verbose=self.verbose)
        try:
            for label, constraints in self.constraints.items():
                for constraint in constraints:
                    if not isinstance(constraint, exp.Column):
                        solver.add(solver._to_z3_expr(constraint))
        except Exception:
            return "unsat"
        sat, result = solver.solve()
        if sat != "sat":
            return "unsat"
        if result:
            self._create_rows_from_solver(result)
        return "sat"

    def _solve_scope(
        self,
        scope_node: ScopeNode,
        skips: Optional[Set[StepType]] = None,
        early_stop: Optional[Callable] = None,
        external: Optional[Context] = None,
        bound_tables: Optional[Dict[str, DerivedSchema]] = None,
        required_non_null_columns: Optional[Sequence[exp.Column]] = None,
        deadline: Optional[float] = None,
    ) -> ScopeSolveResult:
        skips = skips or set()
        tracer = UExprToConstraint()
        self.active_bound_tables = bound_tables or {}
        context = self._encode_scope(
            scope_node, tracer, external=external, bound_tables=bound_tables
        )
        if not tracer.leaves:
            self._bootstrap_scope_rows(
                scope_node,
                min_rows=max(1, self.generator_config.group_size_threshold),
            )
            context = self._encode_scope(
                scope_node, tracer, external=external, bound_tables=bound_tables
            )

        while True:
            if deadline is not None and time.monotonic() >= deadline:
                break
            pattern, plausible = tracer.next_path(
                config=self.generator_config, skips=skips
            )

            if pattern is None or plausible is None:
                break
            if plausible.plausible_type == PlausibleType.INFEASIBLE:
                continue
            if required_non_null_columns:
                for columnref in required_non_null_columns:
                    self.declare_variable(columnref)
                    key = (columnref.table, columnref.name)
                    variable = self.variables.get(key)
                    if variable is None:
                        continue
                    self.declare_constraint(
                        "subquery_output_not_null",
                        variable.is_(exp.Null(_type=variable.type)).not_(),
                    )
            if self._solve_plausible(scope_node, plausible) == "unsat":
                plausible.mark_infeasible()
            self._reset_generation_state()

            context = self._encode_scope(
                scope_node, tracer, external=external, bound_tables=bound_tables
            )
            if early_stop is not None and early_stop(self.instance):
                break

        if self._repair_null_output_columns(required_non_null_columns):
            context = self._encode_scope(
                scope_node, tracer, external=external, bound_tables=bound_tables
            )

        binding = self._materialize_binding(scope_node, context)
        return ScopeSolveResult(
            scope_id=scope_node.node_id,
            scope_expression=scope_node.scope.expression,
            tracer=tracer,
            context=context,
            binding=binding,
        )

    def _dedupe_instance_rows(self):
        self.instance._dedupe_primary_key_rows()
        self.instance._dedupe_unique_rows()
        self.instance._dedupe_null_rows()

    def _seed_correlated_exists_rows(
        self, scope_node: ScopeNode, external: Optional[Context]
    ) -> bool:
        if external is None:
            return False
        scope_expr = scope_node.scope.expression
        local_aliases = {
            table.alias_or_name for table in scope_expr.find_all(exp.Table)
        }
        if not local_aliases:
            return False

        seeded = False
        seen_values: Set[Tuple[str, str, Any]] = set()
        for predicate in scope_expr.find_all(exp.EQ):
            if not isinstance(predicate.this, exp.Column) or not isinstance(
                predicate.expression, exp.Column
            ):
                continue
            left, right = predicate.this, predicate.expression
            if left.table in local_aliases and right.table not in local_aliases:
                local_col, outer_col = left, right
            elif right.table in local_aliases and left.table not in local_aliases:
                local_col, outer_col = right, left
            else:
                continue

            try:
                outer_ref = self.get_tableref(outer_col.table) or outer_col.table
                outer_table = external.resolve_table(outer_ref)
            except Exception:
                continue
            table_ref = self.get_tableref(local_col.table)
            if table_ref is None:
                continue

            for outer_row in outer_table.rows:
                try:
                    value = outer_row[outer_col.name].concrete
                except Exception:
                    continue
                key = (table_ref, normalize_name(local_col.name), value)
                if value is None or key in seen_values:
                    continue
                seen_values.add(key)
                self.instance.create_row(
                    table_ref,
                    values={local_col.name: value},
                )
                seeded = True
        return seeded

    def generate(
        self,
        early_stop: Optional[Callable[[], bool]],
        stop_event: threading.Event,
        timeout: Optional[float] = None,
        skips: Optional[Set[StepType]] = None,
    ):
        random.seed(142)
        deadline = None
        if timeout is not None and timeout > 0:
            deadline = time.monotonic() + timeout
        scope_graph = build_graph_from_scopes(self.expr)
        self.scope_results.clear()
        dependency_order = scope_graph.get_dependency_order()
        for node_id in dependency_order:
            if deadline is not None and time.monotonic() >= deadline:
                break
            scope_node = scope_graph.get_node(node_id)
            if scope_node is None:
                continue
            bound_tables: Dict[str, DerivedSchema] = {}
            has_scalar_dependency = False
            for dep_id in scope_node.dependencies:
                dep_result = self.scope_results.get(dep_id)
                if dep_result is None or dep_result.binding is None:
                    continue
                if dep_result.binding.scalar_value is not None:
                    has_scalar_dependency = True
                self._apply_subquery_binding(scope_node, dep_result.binding)
                bound_tables.update(self._binding_tables(scope_node, dep_result.binding))

            external = None
            if scope_node.is_correlated_dependency:
                external = self._planner_context(bound_tables=bound_tables)
                if self._seed_correlated_exists_rows(scope_node, external):
                    result = ScopeSolveResult(
                        scope_id=scope_node.node_id,
                        scope_expression=scope_node.scope.expression,
                        tracer=UExprToConstraint(),
                        context=None,
                        binding=SubqueryBinding(
                            scope_id=scope_node.node_id,
                            expression=scope_node.scope.expression,
                            output_columns=tuple(),
                            rows=[],
                            exists=False,
                            scalar_value=None,
                            values=[],
                            correlated=True,
                        ),
                    )
                    self.scope_results[node_id] = result
                    continue
            if stop_event.is_set():
                break
            if has_scalar_dependency:
                self._seed_structured_scalar_rows(scope_node.scope.expression)
                self._propagate_query_constraints_to_domains(scope_node.scope.expression)
                self._seed_literal_rows(scope_node.scope.expression)
            required_non_null_columns = self._required_non_null_output_columns(
                scope_graph, scope_node
            )
            result = self._solve_scope(
                scope_node,
                skips=skips,
                early_stop=None,
                external=external,
                bound_tables=bound_tables,
                required_non_null_columns=required_non_null_columns,
                deadline=deadline,
            )
            self.scope_results[node_id] = result
            if early_stop is not None and early_stop(self.instance):
                break

        if not stop_event.is_set():
            self._fallback_random_witness_search(
                early_stop=early_stop,
                deadline=deadline,
            )
        return self.instance

    def _column_label(self, column: Any) -> str:
        if isinstance(column, exp.Expression):
            return column.alias_or_name or column.sql()
        return str(column)
