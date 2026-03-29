from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from functools import reduce
import time, threading
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
    build_context_from_instance,
    build_graph_from_scopes,
)
from parseval.plan.helper import to_literal
from parseval.plan.rex import Symbol, negate_predicate
from parseval.solver.smt import SMTSolver
from parseval.uexpr.uexprs import Constraint, UExprToConstraint

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
        concretes: Dict[str, List[Any]] = {}
        for column in refs:
            values = []
            for row in getattr(agg_group, "group_values", []):
                values.append(row[column.name])
            concretes[column.sql()] = values

        replacements: Dict[int, exp.Expression] = {}
        for agg_func in sql_condition.find_all(exp.AggFunc):
            replacement = self._aggregate_replacement(
                agg_func=agg_func,
                concretes=concretes,
                group_size=len(getattr(agg_group, "group_values", [])),
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
        concretes: Dict[str, List[Any]],
        group_size: int,
    ) -> Optional[exp.Expression]:
        columns = list(agg_func.find_all(exp.Column))
        if isinstance(agg_func, exp.Count):
            if not columns:
                return to_literal(group_size, exp.DataType.build("INT"))
            key = columns[0].sql()
            values = [
                value.concrete
                for value in concretes.get(key, [])
                if value.concrete is not None
            ]
            if agg_func.find(exp.Distinct):
                values = list(dict.fromkeys(values))
            return to_literal(len(values), exp.DataType.build("INT"))
        if not columns:
            return None
        column = columns[0]
        key = column.sql()
        values = [
            value.concrete
            for value in concretes.get(key, [])
            if value.concrete is not None
        ]
        if not values:
            return self._null_for(column)
        if agg_func.find(exp.Distinct):
            values = list(dict.fromkeys(values))
        if isinstance(agg_func, exp.Sum):
            return self._literal_for(sum(values), column)
        if isinstance(agg_func, exp.Max):
            return self._literal_for(max(values), column)
        if isinstance(agg_func, exp.Min):
            return self._literal_for(min(values), column)
        if isinstance(agg_func, exp.Avg):
            return self._literal_for(sum(values) / len(values), column)
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
        self.scope_results: Dict[int, ScopeSolveResult] = {}
        self.operator_rules = OperatorRuleRegistry()

    def _predicate_constraints(self) -> List[exp.Expression]:
        constraints: List[exp.Expression] = []
        for predicate in self.expr.find_all(exp.Predicate):
            normalized = self._normalize_predicate_constraint(predicate)
            if normalized is not None:
                constraints.append(normalized)
        for between in self.expr.find_all(exp.Between):
            normalized = self._normalize_predicate_constraint(between)
            if normalized is not None:
                constraints.append(normalized)
        for in_expr in self.expr.find_all(exp.In):
            normalized = self._normalize_predicate_constraint(in_expr)
            if normalized is not None:
                constraints.append(normalized)
        return constraints

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

    def _propagate_query_constraints_to_domains(self) -> int:
        applied = 0
        for predicate in self._predicate_constraints():
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

    def _seed_literal_rows(self) -> None:
        seeded_rows: Dict[str, Any] = {}
        literal_values = self._literal_values_by_table()
        join_hints = self._join_value_hints()
        for table_name in dict.fromkeys(self.table_alias.values()):
            if not table_name:
                continue
            values = dict(literal_values.get(table_name, {}))
            for local_col, ref_table, ref_col in join_hints.get(table_name, []):
                if local_col in values or ref_table not in seeded_rows:
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
                if local_col in values or ref_table not in seeded_rows:
                    continue
                values[local_col] = seeded_rows[ref_table][ref_col].concrete
            try:
                created = self.instance.create_row(table_name, values=values)
            except Exception:
                continue
            pos = created["positions"].get(table_name)
            if pos is None:
                continue
            seeded_rows[table_name] = self.instance.get_row(table_name, pos)

    def _literal_values_by_table(self) -> Dict[str, Dict[str, Any]]:
        values: Dict[str, Dict[str, Any]] = {}
        for predicate in self._predicate_constraints():
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
            concrete = None
            try:
                if isinstance(predicate, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Like, exp.ILike)):
                    rhs = predicate.args.get("expression")
                    if isinstance(rhs, exp.Literal):
                        concrete = pool.generate_for_spec(predicate.key.upper(), rhs.name)
                elif isinstance(predicate, exp.Between):
                    low = predicate.args.get("low")
                    high = predicate.args.get("high")
                    if isinstance(low, exp.Literal) and isinstance(high, exp.Literal):
                        concrete = pool.generate_for_spec("BETWEEN", (low.name, high.name))
                elif isinstance(predicate, exp.In):
                    literals = [
                        item.name
                        for item in predicate.expressions
                        if isinstance(item, exp.Literal)
                    ]
                    if literals:
                        concrete = pool.generate_for_spec("IN", literals)
            except Exception:
                logger.debug(
                    "Skipping literal seeding for predicate %s due to pool generation error",
                    predicate,
                    exc_info=True,
                )
            if concrete is not None:
                values.setdefault(table_ref, {}).setdefault(column.name, concrete)
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
        table_ref = self.get_tableref(columnref.table)
        if table_ref is None:
            return ()
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

    def _reset_generation_state(self):
        self.variables.clear()
        self.constraints.clear()
        self.var_to_columnref.clear()
        self.table_to_vars.clear()
        self.table_column_to_vars.clear()
        self.columnref_to_vars.clear()

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

    def _planner_context(self, external: Optional[Context] = None) -> Context:
        context = build_context_from_instance(self.instance)
        context.external = external
        return context

    def _encode_scope(
        self,
        scope_node: ScopeNode,
        tracer: UExprToConstraint,
        external: Optional[Context] = None,
    ) -> Optional[Context]:
        tracer.reset()
        planner = Planner(
            ctx=self._planner_context(external=external),
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
                correlated=scope_node.scope.is_correlated_subquery,
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
            correlated=scope_node.scope.is_correlated_subquery,
        )

    def _binding_literal(
        self, binding: SubqueryBinding, datatype=None
    ) -> Optional[exp.Expression]:
        if binding.scalar_value is None:
            return None
        return to_literal(binding.scalar_value, datatype)

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
            values = [to_literal(value, None) for value in binding.values]
            lhs = parent.this.copy()
            if not values:
                parent.replace(exp.Boolean(this=False))
                return
            predicates = [exp.EQ(this=lhs.copy(), expression=value) for value in values]
            constraint = predicates[0]
            for predicate in predicates[1:]:
                constraint = exp.Or(this=constraint, expression=predicate)
            parent.replace(constraint)
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
        deadline: Optional[float] = None,
    ) -> ScopeSolveResult:
        skips = skips or set()
        tracer = UExprToConstraint()
        context = self._encode_scope(scope_node, tracer, external=external)
        if not tracer.leaves:
            self._bootstrap_scope_rows(
                scope_node,
                min_rows=max(1, self.generator_config.group_size_threshold),
            )
            context = self._encode_scope(scope_node, tracer, external=external)

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
            if self._solve_plausible(scope_node, plausible) == "unsat":
                plausible.mark_infeasible()
            self._reset_generation_state()

            context = self._encode_scope(scope_node, tracer, external=external)
            if early_stop is not None and early_stop(self.instance):
                break

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
            for dep_id in scope_node.dependencies:
                dep_result = self.scope_results.get(dep_id)
                if dep_result is None or dep_result.binding is None:
                    continue
                self._apply_subquery_binding(scope_node, dep_result.binding)

            external = None
            if scope_node.scope.is_correlated_subquery:
                external = self._planner_context()
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
            result = self._solve_scope(
                scope_node,
                skips=skips,
                early_stop=early_stop,
                external=external,
                deadline=deadline,
            )
            self.scope_results[node_id] = result

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
