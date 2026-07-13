from __future__ import annotations

from dataclasses import dataclass, replace
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
    Projection,
    Sort,
    Step,
    TableScan,
    explain,
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
    plan = explain(instance.ddls, query, dialect)
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


# def _step_semantic_targets(
#     index: int,
#     step: Step,
#     instance: Instance | None,
# ) -> tuple[SemanticTarget, ...]:
#     step_type = step.type_name
#     prefix = f"{index}.{step_type}"
#     targets: list[SemanticTarget] = []
#     if isinstance(step, TableScan):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.row",
#                 step=step,
#                 step_type=step_type,
#                 kind="base_row",
#                 target="exists",
#             )
#         )
#     if (
#         isinstance(step, Filter)
#         and step.condition is not None
#         and not _is_not_null_filter(step.condition)
#     ):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.true",
#                 step=step,
#                 step_type=step_type,
#                 kind="filter",
#                 target="true",
#                 expression=step.condition,
#             )
#         )
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.false",
#                 step=step,
#                 step_type=step_type,
#                 kind="filter",
#                 target="false",
#                 expression=step.condition,
#             )
#         )
#         if instance is None or _condition_has_nullable_input(instance, step):
#             targets.append(
#                 SemanticTarget(
#                     id=f"{prefix}.null",
#                     step=step,
#                     step_type=step_type,
#                     kind="filter",
#                     target="null",
#                     expression=step.condition,
#                 )
#             )
#     if isinstance(step, Join):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.match",
#                 step=step,
#                 step_type=step_type,
#                 kind="join",
#                 target="match",
#             )
#         )
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.no_match",
#                 step=step,
#                 step_type=step_type,
#                 kind="join",
#                 target="no_match",
#             )
#         )
#         if step.join_type.upper() in {"LEFT", "RIGHT", "FULL"}:
#             targets.append(
#                 SemanticTarget(
#                     id=f"{prefix}.preserved_unmatched",
#                     step=step,
#                     step_type=step_type,
#                     kind="join",
#                     target="preserved_unmatched",
#                 )
#             )
#     if isinstance(step, Aggregate):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.group",
#                 step=step,
#                 step_type=step_type,
#                 kind="group_existence",
#                 target="group",
#             )
#         )
#         targets.extend(_aggregate_targets(prefix, step))
#     if isinstance(step, Distinct):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.duplicate_eliminated",
#                 step=step,
#                 step_type=step_type,
#                 kind="distinct",
#                 target="duplicate_eliminated",
#             )
#         )
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.distinct_preserved",
#                 step=step,
#                 step_type=step_type,
#                 kind="distinct",
#                 target="distinct_preserved",
#             )
#         )
#     if isinstance(step, Sort):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.selected",
#                 step=step,
#                 step_type=step_type,
#                 kind="ordering",
#                 target="selected",
#             )
#         )
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.competitor",
#                 step=step,
#                 step_type=step_type,
#                 kind="ordering",
#                 target="excluded_competitor",
#             )
#         )
#     if isinstance(step, Limit):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.selected",
#                 step=step,
#                 step_type=step_type,
#                 kind="limit_window",
#                 target="selected",
#             )
#         )
#         if step.offset:
#             targets.append(
#                 SemanticTarget(
#                     id=f"{prefix}.offset_skipped",
#                     step=step,
#                     step_type=step_type,
#                     kind="limit_window",
#                     target="offset_skipped",
#                 )
#             )
#     if isinstance(step, Projection):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.projection",
#                 step=step,
#                 step_type=step_type,
#                 kind="projection_visible",
#                 target="visible",
#             )
#         )
#         for case_index, case in enumerate(_case_expressions(step.projections)):
#             targets.extend(_case_targets(prefix, step, case_index, case))
#     return tuple(targets)


# def _aggregate_targets(
#     prefix: str,
#     step: Aggregate,
# ) -> tuple[SemanticTarget, ...]:
#     targets: list[SemanticTarget] = []
#     has_count_star = False
#     has_count_column = False
#     for aggregate_index, aggregate in enumerate(step.aggregations):
#         expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
#         if isinstance(expression, exp.Avg):
#             targets.append(
#                 SemanticTarget(
#                     id=f"{prefix}.agg{aggregate_index}.multi_row",
#                     step=step,
#                     step_type=step.type_name,
#                     kind="multi_row_aggregate_witness",
#                     target="multi_row",
#                     expression=expression,
#                 )
#             )
#         if isinstance(expression, exp.Count):
#             source = expression.this
#             if source is None or isinstance(source, (exp.Star, exp.Literal)):
#                 has_count_star = True
#             elif not isinstance(source, exp.Distinct):
#                 has_count_column = True
#             if isinstance(source, exp.Distinct):
#                 targets.append(
#                     SemanticTarget(
#                         id=f"{prefix}.agg{aggregate_index}.distinct",
#                         step=step,
#                         step_type=step.type_name,
#                         kind="distinct_aggregate_witness",
#                         target="duplicate_eliminated",
#                         expression=expression,
#                     )
#                 )
#         if isinstance(expression, exp.Sum) and isinstance(expression.this, exp.Case):
#             targets.append(
#                 SemanticTarget(
#                     id=f"{prefix}.agg{aggregate_index}.case",
#                     step=step,
#                     step_type=step.type_name,
#                     kind="conditional_aggregate_case",
#                     target="case_path",
#                     expression=expression.this,
#                 )
#             )
#             targets.extend(
#                 _case_targets(prefix, step, aggregate_index, expression.this)
#             )
#     if has_count_star and has_count_column:
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.null_sensitive_count",
#                 step=step,
#                 step_type=step.type_name,
#                 kind="null_sensitive_aggregate_witness",
#                 target="count_star_vs_count_column",
#             )
#         )
#     return tuple(targets)


# def _case_targets(
#     prefix: str,
#     step: Step,
#     case_index: int,
#     case: exp.Case,
# ) -> tuple[SemanticTarget, ...]:
#     targets: list[SemanticTarget] = []
#     for when_index, branch in enumerate(case.args.get("ifs", ()) or ()):
#         targets.append(
#             SemanticTarget(
#                 id=f"{prefix}.case{case_index}.when{when_index}.true",
#                 step=step,
#                 step_type=step.type_name,
#                 kind="case",
#                 target=f"when_{when_index}_true",
#                 expression=branch.this,
#             )
#         )
#     targets.append(
#         SemanticTarget(
#             id=f"{prefix}.case{case_index}.default",
#             step=step,
#             step_type=step.type_name,
#             kind="case",
#             target="default",
#             expression=case,
#         )
#     )
#     return tuple(targets)


# class SemanticCoverageRecorder:
#     def __init__(self, plan: Plan, instance: Instance) -> None:
#         self._plan = plan
#         self._instance = instance

#     def evaluate(self, targets: Sequence[SemanticTarget]) -> dict[str, str]:
#         return {target.id: self._evaluate_target(target) for target in targets}

#     def _evaluate_target(self, target: SemanticTarget) -> str:
#         if target.kind == "base_row" and isinstance(target.step, TableScan):
#             return (
#                 "covered"
#                 if self._instance.get_rows(target.step.table)
#                 else "pending"
#             )
#         if target.kind == "filter" and isinstance(target.step, Filter):
#             return self._evaluate_predicate_target(target)
#         if target.kind == "join" and isinstance(target.step, Join):
#             return self._evaluate_join_target(target)
#         if target.kind in {
#             "projection_visible",
#             "group_existence",
#             "ordering",
#             "limit_window",
#         }:
#             return "covered" if _query_has_existing_rows(self._plan, self._instance) else "pending"
#         return "unsupported"

#     def _evaluate_predicate_target(self, target: SemanticTarget) -> str:
#         if target.expression is None:
#             return "unsupported"
#         outcomes = set()
#         for row in _existing_step_rows(self._instance, target.step):
#             outcomes.add(concrete(target.expression, Environment.from_row(row)))
#         if target.target == "true":
#             return "covered" if True in outcomes else "pending"
#         if target.target == "false":
#             return "covered" if False in outcomes else "unsupported"
#         if target.target == "null":
#             return "covered" if None in outcomes else "unsupported"
#         return "unsupported"

#     def _evaluate_join_target(self, target: SemanticTarget) -> str:
#         step = target.step
#         assert isinstance(step, Join)
#         left_rows = _existing_step_rows(self._instance, step.left) if step.left else ()
#         right_rows = _existing_step_rows(self._instance, step.right) if step.right else ()
#         if not left_rows or not right_rows:
#             return "pending" if target.target == "match" else "unsupported"
#         matches: list[tuple[Mapping[exp.Identifier, Any], Mapping[exp.Identifier, Any]]] = []
#         for left, right in product(left_rows, right_rows):
#             if _join_rows_match(step, left, right):
#                 matches.append((left, right))
#         if target.target == "match":
#             return "covered" if matches else "pending"
#         if target.target == "no_match":
#             matched_left = {id(left) for left, _right in matches}
#             matched_right = {id(right) for _left, right in matches}
#             has_no_match = any(id(row) not in matched_left for row in left_rows) or any(
#                 id(row) not in matched_right for row in right_rows
#             )
#             return "covered" if has_no_match else "unsupported"
#         if target.target == "preserved_unmatched":
#             join_type = step.join_type.upper()
#             matched_left = {id(left) for left, _right in matches}
#             matched_right = {id(right) for _left, right in matches}
#             if join_type == "LEFT":
#                 return (
#                     "covered"
#                     if any(id(row) not in matched_left for row in left_rows)
#                     else "unsupported"
#                 )
#             if join_type == "RIGHT":
#                 return (
#                     "covered"
#                     if any(id(row) not in matched_right for row in right_rows)
#                     else "unsupported"
#                 )
#             if join_type == "FULL":
#                 return (
#                     "covered"
#                     if any(id(row) not in matched_left for row in left_rows)
#                     or any(id(row) not in matched_right for row in right_rows)
#                     else "unsupported"
#                 )
#         return "unsupported"


# def _query_has_existing_rows(plan: Plan, instance: Instance) -> bool:
#     leaves = [step for step in _ordered_steps(plan.root) if isinstance(step, TableScan)]
#     return bool(leaves) and all(instance.get_rows(step.table) for step in leaves)


# def _existing_step_rows(
#     instance: Instance,
#     step: Step | None,
# ) -> tuple[Mapping[exp.Identifier, Any], ...]:
#     scans = tuple(_leaf_table_scans(step)) if step is not None else ()
#     if not scans:
#         return ()
#     row_groups: list[list[Mapping[exp.Identifier, Any]]] = []
#     for scan in scans:
#         rows = [Instance._row_value_dict(row) for row in instance.get_rows(scan.table)]
#         if not rows:
#             return ()
#         row_groups.append(rows)
#     combined: list[Mapping[exp.Identifier, Any]] = []
#     for row_tuple in product(*row_groups):
#         merged: dict[exp.Identifier, Any] = {}
#         for row in row_tuple:
#             merged.update(row)
#         combined.append(merged)
#     return tuple(combined)


# def _leaf_table_scans(step: Step | None) -> tuple[TableScan, ...]:
#     if step is None:
#         return ()
#     if isinstance(step, TableScan):
#         return (step,)
#     scans: list[TableScan] = []
#     for dependency in step.dependencies:
#         scans.extend(_leaf_table_scans(dependency))
#     return tuple(scans)


# def _join_rows_match(
#     step: Join,
#     left: Mapping[exp.Identifier, Any],
#     right: Mapping[exp.Identifier, Any],
# ) -> bool:
#     for left_key, right_key in step.on_keys:
#         left_value = concrete(left_key, Environment.from_row(left))
#         right_value = concrete(right_key, Environment.from_row(right))
#         if left_value is None or right_value is None or left_value != right_value:
#             return False
#     if step.condition is not None:
#         merged = dict(left)
#         merged.update(right)
#         return concrete(step.condition, Environment.from_row(merged)) is True
#     return bool(step.on_keys)


# def _condition_has_nullable_input(instance: Instance, step: Filter) -> bool:
#     if step.condition is None:
#         return False
#     scans = _leaf_table_scans(_single_dependency_or_none(step))
#     for column in step.condition.find_all(exp.Column):
#         for scan in scans:
#             table_schema = instance.schema.get_table(scan.table)
#             matching = [
#                 stored
#                 for stored in table_schema.columns
#                 if stored.name == column.name
#                 or stored.name.casefold() == column.name.casefold()
#             ]
#             if matching and instance.nullable(scan.table, matching[0]):
#                 return True
#     return False


# def _is_not_null_filter(expression: exp.Expression) -> bool:
#     if isinstance(expression, exp.Not) and isinstance(expression.this, exp.Is):
#         return isinstance(expression.this.expression, exp.Null)
#     return False


# def _case_expressions(
#     expressions: Sequence[exp.Expression],
# ) -> tuple[exp.Case, ...]:
#     cases: list[exp.Case] = []
#     for expression in expressions:
#         cases.extend(expression.find_all(exp.Case))
#     return tuple(cases)


# def _has_pending_generation(
#     targets: Sequence[SemanticTarget],
#     statuses: Mapping[str, str],
# ) -> bool:
#     return any(
#         statuses.get(target.id) == "pending" and _target_has_generation_strategy(target)
#         for target in targets
#     )


# def _target_has_generation_strategy(target: SemanticTarget) -> bool:
#     if target.kind == "base_row":
#         return True
#     if target.kind == "filter" and target.target == "true":
#         return True
#     if target.kind == "join" and target.target == "match":
#         return True
#     if target.kind in {
#         "projection_visible",
#         "group_existence",
#         "ordering",
#         "limit_window",
#     }:
#         return True
#     return False


# def _single_dependency_or_none(step: Step) -> Step | None:
#     if len(step.dependencies) != 1:
#         return None
#     return next(iter(step.dependencies))


# def _copy_create_rows(
#     create_rows: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, object]]],
# ) -> dict[exp.Table, list[Mapping[exp.Identifier, object]]]:
#     return {
#         table: [dict(row) for row in rows]
#         for table, rows in create_rows.items()
#     }




# def _coverage_ratio(obligations: Sequence[CoverageObligation]) -> float:
#     if not obligations:
#         return 1.0
#     covered = sum(1 for obligation in obligations if obligation.status == "covered")
#     return covered / len(obligations)
