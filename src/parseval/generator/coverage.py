from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any, Mapping, Optional, Sequence

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan.rex import Environment, concrete
from parseval.plan.explain import (
    Aggregate,
    Distinct,
    Filter,
    Join,
    Limit,
    Plan,
    PlanError,
    Projection,
    Sort,
    Step,
    TableScan,
    explain,
)
from parseval.generator.symbolic_resolution import (
    join_alias_tables,
    join_order_keys_supported,
    order_expression_value,
    resolved_order_expressions,
    single_dependency_join,
    single_leaf_scan,
    storage_table_for_join_column,
)
from .bounds import BmcBounds


@dataclass(frozen=True)
class CoverageObligation:
    id: str
    step_type: str
    kind: str
    target: str
    status: str


@dataclass(frozen=True)
class SemanticTarget:
    id: str
    step: Step
    step_type: str
    kind: str
    target: str
    expression: exp.Expression | None = None

    def obligation(self, status: str) -> CoverageObligation:
        return CoverageObligation(
            id=self.id,
            step_type=self.step_type,
            kind=self.kind,
            target=self.target,
            status=status,
        )


@dataclass(frozen=True)
class CoverageTreeNode:
    id: str
    step: Step
    step_type: str
    targets: tuple[SemanticTarget, ...]
    children: tuple["CoverageTreeNode", ...]
    obligations: tuple[CoverageObligation, ...] = ()

    def iter_targets(self) -> tuple[SemanticTarget, ...]:
        targets: list[SemanticTarget] = list(self.targets)
        for child in self.children:
            targets.extend(child.iter_targets())
        return tuple(targets)

    def iter_obligations(self) -> tuple[CoverageObligation, ...]:
        obligations: list[CoverageObligation] = list(self.obligations)
        for child in self.children:
            obligations.extend(child.iter_obligations())
        return tuple(obligations)

    def with_statuses(self, statuses: Mapping[str, str]) -> "CoverageTreeNode":
        return CoverageTreeNode(
            id=self.id,
            step=self.step,
            step_type=self.step_type,
            targets=self.targets,
            children=tuple(child.with_statuses(statuses) for child in self.children),
            obligations=tuple(
                target.obligation(statuses.get(target.id, "unsupported"))
                for target in self.targets
            ),
        )

    def coverage_ratio(self) -> float:
        return _coverage_ratio(self.iter_obligations())


def generate_query_database(
    ddl: str | Instance,
    query: str,
    *,
    dialect: str = "sqlite",
    bounds: Optional[BmcBounds] = None,
) -> Instance | object:
    """Generate witness rows for ``query`` and return the mutated Instance."""
    if isinstance(ddl, Instance):
        instance = ddl
        plan = explain(instance.ddls, query, instance.dialect)
        from parseval.generator.symbolic.generate import generate as symbolic_generate

        symbolic_generate(
            plan,
            instance,
            query=query,
            bounds=bounds,
        )
        return instance.generation

    instance = Instance(ddl, name="generation", dialect=dialect)
    try:
        plan = explain(instance.ddls, query, dialect)
    except ValueError as e:
        raise PlanError(f"Failed to generate plan for query: {query}") from e
    from parseval.generator.symbolic.generate import generate as symbolic_generate

    return symbolic_generate(
        plan,
        instance,
        query=query,
        bounds=bounds,
    )

def _ordered_steps(root: Step) -> tuple[Step, ...]:
    ordered: list[Step] = []
    seen: set[int] = set()

    def visit(step: Step) -> None:
        identity = id(step)
        if identity in seen:
            return
        seen.add(identity)
        for dependency in step.dependencies:
            visit(dependency)
        ordered.append(step)

    visit(root)
    return tuple(ordered)


def _step_semantic_targets(
    step: Step,
    instance: Instance | None,
    path: str,
) -> tuple[SemanticTarget, ...]:
    step_type = step.type_name
    prefix = f"{path}.{step_type}"
    targets: list[SemanticTarget] = []
    if isinstance(step, TableScan):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.row",
                step=step,
                step_type=step_type,
                kind="base_row",
                target="exists",
            )
        )
    if (
        isinstance(step, Filter)
        and step.condition is not None
        and not _is_not_null_filter(step.condition)
    ):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.true",
                step=step,
                step_type=step_type,
                kind="filter",
                target="true",
                expression=step.condition,
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.false",
                step=step,
                step_type=step_type,
                kind="filter",
                target="false",
                expression=step.condition,
            )
        )
        if instance is None or _condition_has_nullable_input(instance, step):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.null",
                    step=step,
                    step_type=step_type,
                    kind="filter",
                    target="null",
                    expression=step.condition,
                )
            )
    if isinstance(step, Join):
        jt = step.join_type.upper()
        targets.append(
            SemanticTarget(
                id=f"{prefix}.match",
                step=step,
                step_type=step_type,
                kind="join",
                target="match",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.no_match",
                step=step,
                step_type=step_type,
                kind="join",
                target="no_match",
            )
        )
        if jt in {"LEFT", "RIGHT", "FULL"}:
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.preserved_unmatched",
                    step=step,
                    step_type=step_type,
                    kind="join",
                    target="preserved_unmatched",
                )
            )
        if jt == "SEMI":
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.semi_match",
                    step=step,
                    step_type=step_type,
                    kind="semi_join",
                    target="semi_match",
                )
            )
        if jt == "ANTI":
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.anti_no_match",
                    step=step,
                    step_type=step_type,
                    kind="anti_join",
                    target="anti_no_match",
                )
            )
    if isinstance(step, Aggregate):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.group",
                step=step,
                step_type=step_type,
                kind="group_existence",
                target="group",
            )
        )
        targets.extend(_aggregate_targets(prefix, step))
    if isinstance(step, Distinct):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.duplicate_eliminated",
                step=step,
                step_type=step_type,
                kind="distinct",
                target="duplicate_eliminated",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.distinct_preserved",
                step=step,
                step_type=step_type,
                kind="distinct",
                target="distinct_preserved",
            )
        )
    if isinstance(step, Sort):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.selected",
                step=step,
                step_type=step_type,
                kind="ordering",
                target="selected",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.competitor",
                step=step,
                step_type=step_type,
                kind="ordering",
                target="excluded_competitor",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.rank_tie",
                step=step,
                step_type=step_type,
                kind="ordering",
                target="rank_tie",
            )
        )
    if isinstance(step, Limit):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.selected",
                step=step,
                step_type=step_type,
                kind="limit_window",
                target="selected",
            )
        )
        if step.offset:
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.offset_skipped",
                    step=step,
                    step_type=step_type,
                    kind="limit_window",
                    target="offset_skipped",
                )
            )
    if isinstance(step, Projection):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.projection",
                step=step,
                step_type=step_type,
                kind="projection_visible",
                target="visible",
            )
        )
        for case_index, case in enumerate(_case_expressions(step.projections)):
            targets.extend(_case_targets(prefix, step, case_index, case))
    return tuple(targets)


def _aggregate_targets(
    prefix: str,
    step: Aggregate,
) -> tuple[SemanticTarget, ...]:
    targets: list[SemanticTarget] = []
    has_count_star = False
    has_count_column = False
    for aggregate_index, aggregate in enumerate(step.aggregations):
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        if isinstance(expression, exp.Avg):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.agg{aggregate_index}.multi_row",
                    step=step,
                    step_type=step.type_name,
                    kind="multi_row_aggregate_witness",
                    target="multi_row",
                    expression=expression,
                )
            )
        if isinstance(expression, exp.Count):
            source = expression.this
            if source is None or isinstance(source, (exp.Star, exp.Literal)):
                has_count_star = True
            elif not isinstance(source, exp.Distinct):
                has_count_column = True
            if isinstance(source, exp.Distinct):
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.agg{aggregate_index}.distinct",
                        step=step,
                        step_type=step.type_name,
                        kind="distinct_aggregate_witness",
                        target="duplicate_eliminated",
                        expression=expression,
                    )
                )
        if isinstance(expression, exp.Sum) and isinstance(expression.this, exp.Case):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.agg{aggregate_index}.case",
                    step=step,
                    step_type=step.type_name,
                    kind="conditional_aggregate_case",
                    target="case_path",
                    expression=expression.this,
                )
            )
            targets.extend(
                _case_targets(prefix, step, aggregate_index, expression.this)
            )
    if has_count_star and has_count_column:
        targets.append(
            SemanticTarget(
                id=f"{prefix}.null_sensitive_count",
                step=step,
                step_type=step.type_name,
                kind="null_sensitive_aggregate_witness",
                target="count_star_vs_count_column",
            )
        )
    return tuple(targets)


def _case_targets(
    prefix: str,
    step: Step,
    case_index: int,
    case: exp.Case,
) -> tuple[SemanticTarget, ...]:
    targets: list[SemanticTarget] = []
    for when_index, branch in enumerate(case.args.get("ifs", ()) or ()):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.case{case_index}.when{when_index}.true",
                step=step,
                step_type=step.type_name,
                kind="case",
                target=f"when_{when_index}_true",
                expression=branch.this,
            )
        )
    targets.append(
        SemanticTarget(
            id=f"{prefix}.case{case_index}.default",
            step=step,
            step_type=step.type_name,
            kind="case",
            target="default",
            expression=case,
        )
    )
    return tuple(targets)


def _case_expressions(
    expressions: Sequence[exp.Expression],
) -> tuple[exp.Case, ...]:
    cases: list[exp.Case] = []
    for expression in expressions:
        cases.extend(expression.find_all(exp.Case))
    return tuple(cases)


def _leaf_table_scans(step: Step | None) -> tuple[TableScan, ...]:
    if step is None:
        return ()
    if isinstance(step, TableScan):
        return (step,)
    scans: list[TableScan] = []
    for dependency in step.dependencies:
        scans.extend(_leaf_table_scans(dependency))
    return tuple(scans)


class SemanticCoverageRecorder:
    def __init__(self, plan: Plan, instance: Instance) -> None:
        self._plan = plan
        self._instance = instance

    def evaluate_tree(
        self,
        tree: CoverageTreeNode,
        statuses: Mapping[str, str] | None = None,
    ) -> CoverageTreeNode:
        statuses = {
            target.id: self._evaluate_target(target)
            for target in tree.iter_targets()
        } | dict(statuses or {})
        return tree.with_statuses(statuses)

    def _evaluate_target(self, target: SemanticTarget) -> str:
        if target.kind == "base_row" and isinstance(target.step, TableScan):
            return "covered" if self._instance.get_rows(target.step.table) else "pending"
        if target.kind in {"filter", "case"} and target.expression is not None:
            return self._evaluate_predicate_target(target)
        if target.kind == "join" and isinstance(target.step, Join):
            return self._evaluate_join_target(target)
        if target.kind in {
            "projection_visible",
            "group_existence",
            "distinct",
            "multi_row_aggregate_witness",
            "distinct_aggregate_witness",
            "conditional_aggregate_case",
            "null_sensitive_aggregate_witness",
        }:
            return "covered" if _query_has_existing_rows(self._plan, self._instance) else "pending"
        if target.kind == "ordering" and isinstance(target.step, Sort):
            return self._evaluate_ordering_target(target)
        if target.kind == "limit_window" and isinstance(target.step, (Limit, Sort)):
            return self._evaluate_limit_target(target)
        return "unsupported"

    def _evaluate_predicate_target(self, target: SemanticTarget) -> str:
        if target.expression is None:
            return "unsupported"
        outcomes = set()
        for row in _existing_step_rows(self._instance, target.step):
            try:
                outcomes.add(concrete(target.expression, Environment.from_row(row)))
            except Exception:
                continue
        if target.kind == "case":
            if target.target == "default":
                return "covered" if outcomes else "pending"
            return "covered" if True in outcomes else "pending"
        if target.target == "true":
            return "covered" if True in outcomes else "pending"
        if target.target == "false":
            return "covered" if False in outcomes else "unsupported"
        if target.target == "null":
            return "covered" if None in outcomes else "unsupported"
        return "unsupported"

    def _evaluate_join_target(self, target: SemanticTarget) -> str:
        step = target.step
        assert isinstance(step, Join)
        left_rows = _existing_step_rows(self._instance, step.left) if step.left else ()
        right_rows = _existing_step_rows(self._instance, step.right) if step.right else ()
        if not left_rows or not right_rows:
            return "pending" if target.target == "match" else "unsupported"
        matches: list[tuple[Mapping[exp.Identifier, Any], Mapping[exp.Identifier, Any]]] = []
        for left, right in product(left_rows, right_rows):
            if _join_rows_match(step, left, right):
                matches.append((left, right))
        if target.target == "match":
            return "covered" if matches else "pending"
        if target.target == "no_match":
            matched_left = {id(left) for left, _right in matches}
            matched_right = {id(right) for _left, right in matches}
            has_no_match = any(id(row) not in matched_left for row in left_rows) or any(
                id(row) not in matched_right for row in right_rows
            )
            return "covered" if has_no_match else "unsupported"
        if target.target == "preserved_unmatched":
            join_type = step.join_type.upper()
            matched_left = {id(left) for left, _right in matches}
            matched_right = {id(right) for _left, right in matches}
            if join_type == "LEFT":
                return "covered" if any(id(row) not in matched_left for row in left_rows) else "unsupported"
            if join_type == "RIGHT":
                return "covered" if any(id(row) not in matched_right for row in right_rows) else "unsupported"
            if join_type == "FULL":
                return (
                    "covered"
                    if any(id(row) not in matched_left for row in left_rows)
                    or any(id(row) not in matched_right for row in right_rows)
                    else "unsupported"
                )
        return "unsupported"

    def _evaluate_ordering_target(self, target: SemanticTarget) -> str:
        context = _topk_context(self._plan, self._instance, target.step)
        if context is None:
            return "unsupported"
        rows, keys, offset, limit = context
        if len(rows) < offset + limit or not rows[offset : offset + limit]:
            return "pending"
        if target.target == "selected":
            return "covered"
        if target.target == "excluded_competitor":
            return "covered" if len(rows) > offset + limit else "pending"
        if target.target == "rank_tie":
            selected = rows[offset : offset + limit]
            selected_keys = {keys[id(row)] for row in selected}
            return (
                "covered"
                if any(keys[id(row)] in selected_keys and row not in selected for row in rows)
                else "pending"
            )
        return "unsupported"

    def _evaluate_limit_target(self, target: SemanticTarget) -> str:
        sort = _single_dependency_of_type(target.step, Sort)
        context = _topk_context(self._plan, self._instance, sort or target.step)
        if context is None:
            return "unsupported"
        rows, _keys, offset, limit = context
        if target.target == "selected":
            return "covered" if len(rows[offset : offset + limit]) == limit else "pending"
        if target.target == "offset_skipped":
            return "covered" if offset > 0 and len(rows[:offset]) == offset else "pending"
        return "unsupported"


def _topk_context(
    plan: Plan,
    instance: Instance,
    step: Step,
) -> tuple[list[Mapping[exp.Identifier, Any]], dict[int, tuple[Any, ...]], int, int] | None:
    sort = step if isinstance(step, Sort) else _single_dependency_of_type(step, Sort)
    if sort is None or not sort.key:
        return None
    scan = single_leaf_scan(sort)
    if scan is None:
        return _join_topk_context(plan, instance, sort)
    order_expressions = resolved_order_expressions(instance, scan.table, sort)
    if order_expressions is None:
        return None
    offset, limit = _sort_window(plan, sort)
    rows = [Instance._row_value_dict(row) for row in instance.get_rows(scan.table)]
    if not rows:
        return [], {}, offset, limit
    keys: dict[int, tuple[Any, ...]] = {}
    for row in rows:
        values = []
        for expr in order_expressions:
            try:
                values.append(concrete(expr, Environment.from_row(row)))
            except Exception:
                return None
        keys[id(row)] = tuple(values)
    for index, ordered in reversed(tuple(enumerate(sort.key))):
        descending = isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))
        rows.sort(key=lambda row: sql_order_key(keys[id(row)][index]), reverse=descending)
    return rows, keys, offset, limit


def _join_topk_context(
    plan: Plan,
    instance: Instance,
    sort: Sort,
) -> tuple[list[Mapping[exp.Identifier, Any]], dict[int, tuple[Any, ...]], int, int] | None:
    join = single_dependency_join(sort)
    if join is None or len(join.on_keys) != 1 or join.condition is not None:
        return None
    left_key, right_key = join.on_keys[0]
    if not isinstance(left_key, exp.Column) or not isinstance(right_key, exp.Column):
        return None
    left_table = storage_table_for_join_column(join, left_key, instance)
    right_table = storage_table_for_join_column(join, right_key, instance)
    if left_table is None or right_table is None:
        return None
    alias_tables = dict(join_alias_tables(join))
    if not join_order_keys_supported(instance, alias_tables, sort.key):
        return None
    left_ident = instance.resolve_column(left_table, left_key.name)
    right_ident = instance.resolve_column(right_table, right_key.name)
    rows: list[Mapping[exp.Identifier, Any]] = []
    for left_row in instance.get_rows(left_table):
        left_values = Instance._row_value_dict(left_row)
        left_value = left_values.get(left_ident)
        for right_row in instance.get_rows(right_table):
            right_values = Instance._row_value_dict(right_row)
            if left_value is None or left_value != right_values.get(right_ident):
                continue
            rows.append({**left_values, **right_values})
    if not rows:
        return [], {}, *_sort_window(plan, sort)
    keys: dict[int, tuple[Any, ...]] = {}
    for row in rows:
        values = []
        for ordered in sort.key:
            expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            value = order_expression_value(instance, alias_tables, row, expr)
            if value is None:
                return None
            values.append(value)
        keys[id(row)] = tuple(values)
    for index, ordered in reversed(tuple(enumerate(sort.key))):
        descending = isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))
        rows.sort(key=lambda row: sql_order_key(keys[id(row)][index]), reverse=descending)
    offset, limit = _sort_window(plan, sort)
    return rows, keys, offset, limit


def _query_has_existing_rows(plan: Plan, instance: Instance) -> bool:
    scans = [step for step in _ordered_steps(plan.root) if isinstance(step, TableScan)]
    return bool(scans) and all(instance.get_rows(step.table) for step in scans)


def _existing_step_rows(
    instance: Instance,
    step: Step | None,
) -> tuple[Mapping[exp.Identifier, Any], ...]:
    scans = tuple(_leaf_table_scans(step)) if step is not None else ()
    if not scans:
        return ()
    row_groups: list[list[Mapping[exp.Identifier, Any]]] = []
    for scan in scans:
        rows = [Instance._row_value_dict(row) for row in instance.get_rows(scan.table)]
        if not rows:
            return ()
        row_groups.append(rows)
    return tuple({key: value for row in rows for key, value in row.items()} for rows in product(*row_groups))


def _join_rows_match(
    step: Join,
    left: Mapping[exp.Identifier, Any],
    right: Mapping[exp.Identifier, Any],
) -> bool:
    for left_key, right_key in step.on_keys:
        left_value = concrete(left_key, Environment.from_row(left))
        right_value = concrete(right_key, Environment.from_row(right))
        if left_value is None or right_value is None or left_value != right_value:
            return False
    if step.condition is not None:
        merged = dict(left)
        merged.update(right)
        return concrete(step.condition, Environment.from_row(merged)) is True
    return bool(step.on_keys)


def _single_dependency_of_type(step: Step, step_type: type) -> Any | None:
    if len(step.dependencies) != 1:
        return None
    dependency = next(iter(step.dependencies))
    return dependency if isinstance(dependency, step_type) else None


def _sort_window(plan: Plan, sort: Sort) -> tuple[int, int]:
    offset = 0
    limit = sort.fetch or 1
    for step in _ordered_steps(plan.root):
        if isinstance(step, Limit) and sort in step.dependencies:
            offset = step.offset or 0
            limit = step.fetch or max(limit - offset, 1)
            break
    return offset, max(limit, 1)


def _coverage_ratio(obligations: Sequence[CoverageObligation]) -> float:
    if not obligations:
        return 1.0
    covered = sum(1 for obligation in obligations if obligation.status == "covered")
    return covered / len(obligations)


def _condition_has_nullable_input(instance: Instance, step: Filter) -> bool:
    scans = _leaf_table_scans(step)
    for column in step.condition.find_all(exp.Column) if step.condition is not None else ():
        for scan in scans:
            try:
                stored = instance.resolve_column(scan.table, column)
            except KeyError:
                continue
            if instance.nullable(scan.table, stored):
                return True
    return False


def _is_not_null_filter(expression: exp.Expression) -> bool:
    return (
        isinstance(expression, exp.Not)
        and isinstance(expression.this, exp.Is)
        and isinstance(expression.this.expression, exp.Null)
    )
    
    
def sql_order_key(value: Any) -> tuple[int, int, Any]:
    """Return a deterministic SQL-like key for generated ORDER BY values."""
    if value is None:
        return (0, 0, None)
    if isinstance(value, bool):
        return (1, 1, int(value))
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (1, 1, float(value))
    if isinstance(value, (datetime, date, time)):
        return (1, 2, value.isoformat())
    if isinstance(value, bytes):
        return (1, 4, value)
    if isinstance(value, str):
        return (1, 3, value)
    return (1, 5, str(value))
