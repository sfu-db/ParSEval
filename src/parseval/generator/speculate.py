"""Speculative data seeding for SQL queries.

Given DDLs + SQL query + dialect, seed an Instance with data such that
the query would return at least one row --- without relying on real
database execution. Uses sqlglot AST analysis + the solver module.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from itertools import islice, product
from typing import Any, Mapping, Sequence

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import build_scope, traverse_scope

from parseval.coercion import CoercionError, coerce_literal_value
from parseval.domain.exceptions import DomainError
from parseval.generator.schema_constraints import (
    SchemaConstraintLoweringError,
    schema_constraints_for_solver_row,
)
from parseval.generator.helper import same_identifier
from parseval.instance import Instance
from parseval.instance.schema import table_key, normalize_identifier
from parseval.literals import literal_value
from parseval.solver import Problem, SolverVar
from parseval.solver.partition import flatten_conjuncts
from parseval.generator.config import GenerationConfig
from parseval.generator.budget import GenerationBudget
from parseval.plan.rex import negate_predicate

logger = logging.getLogger(__name__)

MIN_ROWS_CAP = 1000

ColumnKey = tuple[exp.Table, exp.Identifier | None, exp.Identifier]
OccurrenceKey = tuple[exp.Table, exp.Identifier | None]


@dataclass
class RelationScopeInfo:
    scopes: dict[int, Any] = field(default_factory=dict)
    physical_tables: set[exp.Table] = field(default_factory=set)


def _is_outer_join(join: exp.Join) -> bool:
    return join.args.get("side") in ("LEFT", "RIGHT", "FULL")


def _collect_operation_leaves(tree: exp.Expression) -> list[exp.Select]:
    """Flatten a set-operation tree into a left-to-right list of leaf SELECTs."""
    leaves: list[exp.Select] = []
    stack: list[exp.Expression] = [tree]
    while stack:
        node = stack.pop()
        while isinstance(node, exp.Subquery):
            node = node.this
        if isinstance(node, exp.Select):
            leaves.append(node)
        elif isinstance(node, exp.Union):
            stack.append(node.right)
            stack.append(node.left)
    return leaves


def speculate(
    ddls: str,
    query: str,
    dialect: str = "sqlite",
    *,
    config: GenerationConfig = GenerationConfig(),
    _budget: GenerationBudget | None = None,
) -> Instance:
    """Bootstrap candidate data for *query* before target-directed generation.

    When ``config.bootstrap_negatives`` is true, also seed additional
    rows that violate individual WHERE atoms, providing both matching
    and non-matching data.

    The returned rows are unproven candidates. The encode pipeline validates
    their coverage and generates any missing semantic witnesses.
    """
    instance = Instance(ddls, name="speculative", dialect=dialect)
    budget = _budget or GenerationBudget(config)
    try:
        tree = parse_one(query, dialect=dialect)
    except SqlglotError:
        logger.warning("Failed to parse query")
        _seed_base_rows(instance, budget=budget)
        return instance

    if isinstance(tree, exp.With):
        tree = tree.this

    if isinstance(tree, exp.Union):
        _speculate_set_operation(instance, tree, config, dialect, budget)
        return instance

    if not isinstance(tree, exp.Select):
        logger.warning("Only SELECT queries are supported, got %s", type(tree).__name__)
        _seed_base_rows(instance, budget=budget)
        return instance

    if not _speculate_select(instance, tree, config, dialect, budget):
        relation_info = _collect_relation_scope_info(tree, instance)
        query_tables: set[exp.Table] = set(relation_info.physical_tables)
        if query_tables:
            _seed_base_rows(instance, tables=query_tables, budget=budget)
        else:
            logger.debug("No tables found in query")
            _seed_base_rows(instance, budget=budget)
    return instance


def _speculate_set_operation(
    instance: Instance,
    tree: exp.Union,
    config: GenerationConfig,
    dialect: str,
    budget: GenerationBudget,
) -> None:
    """Seed data for a UNION/INTERSECT/EXCEPT set operation.

    Processes each leaf SELECT independently, accumulating rows in
    *instance*. If no branch is solved, seeds baseline candidates instead.
    """
    leaves = _collect_operation_leaves(tree)
    any_solved = False
    for leaf in leaves:
        if _speculate_select(instance, leaf, config, dialect, budget):
            any_solved = True

    if not any_solved:
        all_tables: set[exp.Table] = set()
        for leaf in leaves:
            info = _collect_relation_scope_info(leaf, instance)
            all_tables.update(info.physical_tables)
        if all_tables:
            _seed_base_rows(instance, tables=all_tables, budget=budget)
        else:
            _seed_base_rows(instance, budget=budget)


def _speculate_select(
    instance: Instance,
    tree: exp.Select,
    config: GenerationConfig,
    dialect: str,
    budget: GenerationBudget,
) -> bool:
    """Seed data for a single SELECT.

    Returns True if a solver solution was found and False when the caller
    should seed baseline bootstrap candidates.
    """
    relation_info = _collect_relation_scope_info(tree, instance)
    query_tables: set[exp.Table] = set(relation_info.physical_tables)

    col_var_map, table_vars, occurrence_tables = _collect_column_vars(
        tree, instance, relation_info,
    )

    if not col_var_map:
        _seed_base_rows(instance, tables=query_tables, budget=budget)
        return True

    where_atoms: list[exp.Expression] = []
    where_node = tree.args.get("where")
    if where_node is not None:
        for conj in flatten_conjuncts(where_node.this):
            if not _find_subquery_predicates(conj):
                replaced = _solver_predicate_or_none(
                    conj,
                    current_select=tree,
                    relation_info=relation_info,
                    col_var_map=col_var_map,
                    instance=instance,
                )
                if replaced is not None:
                    where_atoms.append(replaced)

    min_rows = config.bootstrap_rows

    group = tree.args.get("group")
    has_group_by = group is not None and group.expressions
    group_count = config.groups

    group_column_keys = _extract_group_column_keys(tree, relation_info, instance)
    agg_input_column_keys = _extract_aggregate_input_column_keys(tree, relation_info, instance)
    for key in (*group_column_keys, *agg_input_column_keys):
        _ensure_solver_var(key, instance, col_var_map, table_vars, occurrence_tables)

    # Compute keys for duplicate-value generation
    seed_duplicate_keys: list[ColumnKey] = list(group_column_keys)
    for expr in tree.expressions:
        unwrapped = expr
        if isinstance(unwrapped, exp.Alias):
            unwrapped = unwrapped.this
        if isinstance(unwrapped, exp.Column):
            key = _resolve_column_key(unwrapped, tree, relation_info, instance)
            if key is not None and key not in seed_duplicate_keys:
                table_node, _alias, col_ident = key
                if not instance.is_unique(table_node, col_ident):
                    seed_duplicate_keys.append(key)
    for table_node in query_tables:
        table_schema = instance.database_constraints(table_node)
        for fk in table_schema.foreign_keys:
            for col_ident in fk.source_columns:
                key = (table_node, None, col_ident)
                if key in col_var_map and key not in seed_duplicate_keys:
                    seed_duplicate_keys.append(key)

    null_column_keys: list[ColumnKey] = []
    for expr in tree.expressions:
        unwrapped = expr
        if isinstance(unwrapped, exp.Alias):
            unwrapped = unwrapped.this
        if isinstance(unwrapped, exp.Column):
            key = _resolve_column_key(unwrapped, tree, relation_info, instance)
            if key is not None and key not in null_column_keys and instance.nullable(key[0], key[2]):
                null_column_keys.append(key)

    not_exists_not_in_items: list[dict[str, Any]] = []
    for drop_optional in (False, True):
        predicates, equalities, not_exists_not_in_items = _extract_predicates(
            tree, instance, col_var_map, table_vars, occurrence_tables,
            relation_info=relation_info,
            drop_optional=drop_optional,
        )
        constraints: list[exp.Expression] = []
        for pred in predicates:
            constraints.extend(flatten_conjuncts(pred))

        if has_group_by:
            if not _prepare_database_constraints(
                instance, constraints, col_var_map, table_vars, occurrence_tables,
            ):
                return True
            seeded = _seed_group_rows(
                instance, tree, col_var_map, table_vars, occurrence_tables,
                constraints, list(equalities),
                relation_info=relation_info,
                group_column_keys=group_column_keys,
                aggregate_input_column_keys=agg_input_column_keys,
                group_count=group_count,
                dialect=dialect,
                budget=budget,
                bootstrap_rows=min_rows,
            )
            if not seeded:
                logger.info("speculate_generation_skipped:group_batch_solver_assignment")
            if config.bootstrap_negatives and len(query_tables) > 1:
                _seed_base_rows(instance, tables=query_tables, row_count=1, budget=budget)
            return True

        if not constraints and not equalities and not not_exists_not_in_items:
            base_row_count = max(1, group_count, min_rows)
            _seed_base_rows(
                instance,
                row_count=base_row_count,
                tables=query_tables,
                budget=budget,
                child_multiplier=2,
            )
            if base_row_count > 0 and seed_duplicate_keys:
                _seed_extra_duplicate_rows(
                    instance, seed_duplicate_keys, col_var_map,
                    table_vars, occurrence_tables,
                    extra_count=base_row_count,
                    budget=budget,
                )
            if not _prepare_database_constraints(
                instance, constraints, col_var_map, table_vars, occurrence_tables,
            ):
                return True
            _seed_case_arm_rows(
                instance, tree, col_var_map, table_vars, occurrence_tables,
                constraints, [],
                relation_info=relation_info,
                dialect=dialect,
                budget=budget,
            )
            return True

        if not _prepare_database_constraints(
            instance, constraints, col_var_map, table_vars, occurrence_tables,
        ):
            continue

        variables = set(col_var_map.values())
        problem = Problem(
            constraints=constraints,
            equalities=list(equalities),
            variables=variables,
        )
        result = budget.solve(problem, dialect=dialect)

        if result.sat:
            _seed_from_assignments(
                instance, result.assignments, col_var_map, table_vars, occurrence_tables,
                constraints, list(equalities),
                group_count=group_count,
                min_rows=min_rows,
                dialect=dialect,
                duplicate_column_keys=seed_duplicate_keys,
                aggregate_input_column_keys=agg_input_column_keys,
                null_column_keys=null_column_keys,
                budget=budget,
            )
            _seed_case_arm_rows(
                instance, tree, col_var_map, table_vars, occurrence_tables,
                constraints, list(equalities),
                relation_info=relation_info,
                dialect=dialect,
                budget=budget,
            )
            if not_exists_not_in_items:
                _seed_not_exists_not_in(
                    instance, not_exists_not_in_items, result.assignments,
                    col_var_map, table_vars, occurrence_tables, dialect,
                    budget,
                )
            if config.bootstrap_negatives and (where_atoms or null_column_keys):
                _seed_negative_rows(
                    instance, where_atoms, col_var_map, table_vars, occurrence_tables,
                    list(equalities), dialect,
                    budget,
                    null_column_keys=null_column_keys,
                )
            if config.bootstrap_negatives and len(query_tables) > 1:
                _seed_base_rows(instance, tables=query_tables, row_count=1, budget=budget)
            return True

        if not_exists_not_in_items and not result.sat:
            break

    return False


def _collect_relation_scope_info(tree: exp.Select, instance: Instance) -> RelationScopeInfo:
    """Classify scope sources, excluding CTE and derived-table aliases."""
    info = RelationScopeInfo()
    build_scope(tree)
    scopes = list(traverse_scope(tree))

    for scope in scopes:
        info.scopes[id(scope.expression)] = scope
        for source_name, (_node, source) in scope.selected_sources.items():
            del source_name
            if not isinstance(source, exp.Table):
                continue
            try:
                resolved = instance.resolve_table(source)
            except KeyError:
                logger.debug("Skipping unresolved physical source %s", source.sql())
                continue
            info.physical_tables.add(resolved)

    return info


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


def _lookup_identifier_key(
    mapping: Mapping[str, Any],
    key: exp.Identifier,
    dialect: str,
) -> Any | None:
    for candidate, value in mapping.items():
        if same_identifier(exp.to_identifier(candidate), key, dialect):
            return value
    return None


def _column_name_identifier(column: exp.Column) -> exp.Identifier | None:
    return column.this if isinstance(column.this, exp.Identifier) else None


def _projection_alias_identifier(projection: exp.Expression) -> exp.Identifier | None:
    alias = projection.args.get("alias")
    if isinstance(alias, exp.Identifier):
        return alias
    if isinstance(alias, exp.TableAlias) and isinstance(alias.this, exp.Identifier):
        return alias.this
    return None


def _scope_for_select(
    relation_info: RelationScopeInfo,
    select: exp.Select,
) -> Any | None:
    return relation_info.scopes.get(id(select))


def _scope_columns(
    relation_info: RelationScopeInfo,
    select: exp.Select,
) -> tuple[exp.Column, ...]:
    scope = _scope_for_select(relation_info, select)
    if scope is None:
        return ()
    else:
        columns = tuple(scope.columns)
        external_ids = {id(column) for column in scope.external_columns}
        local_columns = tuple(
            column for column in columns
            if id(column) not in external_ids
        )
        if local_columns:
            columns = local_columns
    return tuple(
        column for column in columns
        if not isinstance(column, exp.Star) and column.name != "*"
    )


def _unwrap_projection_expression(expression: exp.Expression) -> exp.Expression:
    while isinstance(expression, (exp.Alias, exp.Paren, exp.Ordered)):
        expression = expression.this
    return expression


def _occurrence_for_key(key: ColumnKey) -> OccurrenceKey:
    table, alias, _column = key
    return (table, alias)


def _physical_column_key(key: ColumnKey) -> tuple[exp.Table, exp.Identifier]:
    table, _alias, column = key
    return (table, column)


def _resolve_physical_column_key(
    column: exp.Column,
    source: exp.Table,
    alias_ident: exp.Identifier | None,
    instance: Instance,
) -> ColumnKey | None:
    try:
        table_node = instance.resolve_table(source)
        col_ident = instance.resolve_column(table_node, column)
    except KeyError:
        return None
    return (table_node, alias_ident, col_ident)


def _projection_output_matches(
    projection: exp.Expression,
    output_column: exp.Column,
    dialect: str,
) -> bool:
    output_name = _column_name_identifier(output_column)
    if output_name is None:
        return False
    if isinstance(projection, exp.Alias):
        alias = _projection_alias_identifier(projection)
        return alias is not None and same_identifier(alias, output_name, dialect)
    unwrapped = _unwrap_projection_expression(projection)
    if isinstance(unwrapped, exp.Column):
        unwrapped_name = _column_name_identifier(unwrapped)
        return unwrapped_name is not None and same_identifier(unwrapped_name, output_name, dialect)
    return False


def _resolve_derived_column_key(
    source_scope: Any,
    visible_alias: exp.Identifier | None,
    output_column: exp.Column,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> ColumnKey | None:
    source_select = source_scope.expression
    if not isinstance(source_select, exp.Select):
        return None

    matches: list[ColumnKey] = []
    for projection in source_select.expressions:
        unwrapped = _unwrap_projection_expression(projection)
        if isinstance(unwrapped, exp.Column) and unwrapped.name == "*":
            if unwrapped.table:
                qualifier = unwrapped.args.get("table")
                if not isinstance(qualifier, exp.Identifier):
                    continue
                selected = _lookup_identifier_key(
                    source_scope.selected_sources,
                    qualifier,
                    instance.dialect,
                )
                if selected is None:
                    continue
                _node, star_source = selected
                if isinstance(star_source, exp.Table):
                    key = _resolve_physical_column_key(
                        output_column,
                        star_source,
                        visible_alias,
                        instance,
                    )
                    if key is not None:
                        matches.append(key)
            continue

        if not _projection_output_matches(projection, output_column, instance.dialect):
            continue
        if not isinstance(unwrapped, exp.Column):
            continue
        key = _resolve_column_key(unwrapped, source_select, relation_info, instance)
        if key is not None:
            table_node, _alias, column_ident = key
            matches.append((table_node, visible_alias, column_ident))

    unique: dict[tuple[exp.Table, exp.Identifier], ColumnKey] = {
        _physical_column_key(match): match
        for match in matches
    }
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def _resolve_column_key(
    column: exp.Column,
    select: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> ColumnKey | None:
    scope = _scope_for_select(relation_info, select)
    if scope is None:
        return None

    if column.table:
        qualifier = column.args.get("table")
        if not isinstance(qualifier, exp.Identifier):
            return None
        selected = _lookup_identifier_key(
            scope.selected_sources,
            qualifier,
            instance.dialect,
        )
        if selected is None:
            return None
        _node, source = selected
        alias = normalize_identifier(qualifier, instance.dialect)
        if isinstance(source, exp.Table):
            return _resolve_physical_column_key(column, source, alias, instance)
        return _resolve_derived_column_key(
            source, alias, column, relation_info, instance,
        )

    matches: list[ColumnKey] = []
    for source_name, (_node, source) in scope.selected_sources.items():
        alias = normalize_identifier(source_name, instance.dialect)
        if isinstance(source, exp.Table):
            key = _resolve_physical_column_key(column, source, None, instance)
        else:
            key = _resolve_derived_column_key(
                source, alias, column, relation_info, instance,
            )
        if key is not None:
            matches.append(key)

    unique: dict[ColumnKey, ColumnKey] = {match: match for match in matches}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


# ---------------------------------------------------------------------------
# SolverVar collection
# ---------------------------------------------------------------------------


def _collect_column_vars(
    tree: exp.Select,
    instance: Instance,
    relation_info: RelationScopeInfo,
) -> tuple[dict[ColumnKey, SolverVar], dict[OccurrenceKey, list[SolverVar]], dict[OccurrenceKey, exp.Table]]:
    """Walk the AST and create a SolverVar per distinct (table, alias, column).

    When the same table appears only once in the query (no self-join),
    qualified (``T.col``) and unqualified (``col``) references to the same
    column share a single SolverVar.  Self-joins keep separate vars per alias.
    """
    col_var_map: dict[ColumnKey, SolverVar] = {}
    table_vars: dict[OccurrenceKey, list[SolverVar]] = {}
    occurrence_tables: dict[OccurrenceKey, exp.Table] = {}

    for col in _scope_columns(relation_info, tree):
        key = _resolve_column_key(col, tree, relation_info, instance)
        if key is None:
            continue
        _ensure_solver_var(key, instance, col_var_map, table_vars, occurrence_tables)

    return col_var_map, table_vars, occurrence_tables


def _ensure_solver_var(
    key: ColumnKey,
    instance: Instance,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
) -> SolverVar:
    existing = col_var_map.get(key)
    if existing is not None:
        return existing
    table_node, alias, col_ident = key
    dtype = instance.get_column_type(table_node, col_ident)
    nullable = instance.nullable(table_node, col_ident)
    if not nullable:
        dtype = dtype.copy()
        dtype.args["nullable"] = False
    tkey = table_key(table_node)
    alias_part = f".{alias.name}" if alias else ""
    var = SolverVar(
        key=f"{tkey}{alias_part}.{col_ident.name}",
        dtype=dtype,
        meta={
            "table": table_node,
            "alias": alias,
            "column": col_ident,
        },
    )
    occurrence = _occurrence_for_key(key)
    col_var_map[key] = var
    table_vars.setdefault(occurrence, []).append(var)
    occurrence_tables[occurrence] = table_node
    return var


def _extract_group_column_keys(
    tree: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> list[ColumnKey]:
    """Extract column keys for GROUP BY columns that are simple column references."""
    group = tree.args.get("group")
    if not group or not group.expressions:
        return []
    keys: list[ColumnKey] = []
    for expr in group.expressions:
        if isinstance(expr, exp.Column):
            key = _resolve_column_key(expr, tree, relation_info, instance)
            if key is not None:
                keys.append(key)
    return keys


def _extract_aggregate_input_column_keys(
    tree: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> list[ColumnKey]:
    """Extract column keys for columns referenced inside aggregate functions."""
    seen: set[ColumnKey] = set()
    keys: list[ColumnKey] = []
    for agg_func in tree.find_all(exp.AggFunc):
        for col in agg_func.find_all(exp.Column):
            key = _resolve_column_key(col, tree, relation_info, instance)
            if key is not None and key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


# ---------------------------------------------------------------------------
# Predicate extraction
# ---------------------------------------------------------------------------


def _extract_predicates(
    tree: exp.Select,
    instance: Instance,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    *,
    relation_info: RelationScopeInfo | None = None,
    drop_optional: bool = False,
) -> tuple[list[exp.Expression], list[tuple[SolverVar, SolverVar]], list[dict[str, Any]]]:
    """Extract predicates and equalities from the query AST.

    Returns ``(predicates, equalities, not_exists_not_in_items)``.
    """
    predicates: list[exp.Expression] = []
    equalities: list[tuple[SolverVar, SolverVar]] = []
    not_exists_not_in_items: list[dict[str, Any]] = []

    if relation_info is None:
        relation_info = _collect_relation_scope_info(tree, instance)

    dialect = instance.dialect

    # -- WHERE (essential) --
    where = tree.args.get("where")
    if where is not None and not drop_optional:
        non_subquery_conjuncts: list[exp.Expression] = []
        for conj in flatten_conjuncts(where.this):
            has_subquery = bool(_find_subquery_predicates(conj))
            if has_subquery:
                for kind, inner_select, outer_expr in _find_subquery_predicates(conj):
                    if inner_select is None:
                        continue
                    if kind in ("exists", "in"):
                        sp, se = _process_exists_in_subquery(
                            inner_select, kind, outer_expr,
                            instance, tree, col_var_map, table_vars,
                            occurrence_tables, dialect,
                            relation_info=relation_info,
                        )
                        predicates.extend(sp)
                        equalities.extend(se)
                    elif kind in ("not_exists", "not_in"):
                        items = _collect_not_exists_not_in_items(
                            inner_select, kind, outer_expr,
                            instance, tree, col_var_map, table_vars,
                            occurrence_tables,
                            relation_info=relation_info,
                        )
                        not_exists_not_in_items.extend(items)
            else:
                scalar_items = _find_scalar_subquery_items(conj)
                if scalar_items:
                    for kind, inner_select, outer_expr in scalar_items:
                        sp, se = _process_scalar_subquery(
                            inner_select, kind, outer_expr,
                            instance, tree, col_var_map, table_vars,
                            occurrence_tables, dialect,
                            relation_info=relation_info,
                        )
                        predicates.extend(sp)
                        equalities.extend(se)
                else:
                    non_subquery_conjuncts.append(conj)

        # Add non-subquery WHERE conjuncts
        for conj in non_subquery_conjuncts:
            _add_predicate(predicates, conj, tree, relation_info, col_var_map, instance)

    # -- JOIN ON (essential for INNER, no-op for outer/cross) --
    if not drop_optional:
        for join in (tree.args.get("joins") or []):
            if _is_outer_join(join):
                continue
            on = join.args.get("on")
            if on is None:
                continue
            for conjunct in flatten_conjuncts(on):
                eq = _extract_equality_pair(
                    conjunct, tree, relation_info, col_var_map, instance,
                )
                if eq is not None:
                    equalities.append(eq)
                else:
                    _add_predicate(predicates, conjunct, tree, relation_info, col_var_map, instance)

    # -- NOT NULL from schema (essential for NOT NULL columns) --
    if not drop_optional:
        _add_not_null_constraints(predicates, col_var_map)

    # -- ORDER BY (non-null for sort columns) --
    order_by = tree.args.get("order")
    if order_by is not None and not drop_optional:
        for ordered in order_by.expressions:
            expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            if isinstance(expr, exp.Column):
                _add_not_null_for_column(predicates, expr, tree, relation_info, col_var_map, instance)

    # -- HAVING (skippable) --
    having = tree.args.get("having")
    group = tree.args.get("group")
    has_group_by = group is not None and bool(group.expressions)
    if having is not None and not drop_optional and not has_group_by:
        _add_predicate(predicates, having.this, tree, relation_info, col_var_map, instance)

    return predicates, equalities, not_exists_not_in_items


def _add_predicate(
    predicates: list[exp.Expression],
    expr: exp.Expression,
    current_select: exp.Select,
    relation_info: RelationScopeInfo | None,
    col_var_map: dict[ColumnKey, SolverVar],
    instance: Instance,
) -> None:
    """Replace columns with SolverVars and append to *predicates*."""
    transformed = _solver_predicate_or_none(
        expr,
        current_select=current_select,
        relation_info=relation_info,
        col_var_map=col_var_map,
        instance=instance,
    )
    if transformed is not None:
        predicates.append(transformed)


def _case_branch_condition(case_expr: exp.Case, branch: exp.If) -> exp.Expression:
    """Return the predicate that makes *branch* reachable."""
    case_operand = case_expr.args.get("this")
    condition = branch.this
    if case_operand is None:
        return condition.copy()
    return exp.EQ(this=case_operand.copy(), expression=condition.copy())


def _case_arm_predicate_sets(case_expr: exp.Case) -> list[list[exp.Expression]]:
    """Return one predicate list for each reachable CASE arm."""
    prior_conditions: list[exp.Expression] = []
    predicate_sets: list[list[exp.Expression]] = []

    for branch in (case_expr.args.get("ifs") or []):
        condition = _case_branch_condition(case_expr, branch)
        predicate_sets.append(
            [negate_predicate(prev.copy()) for prev in prior_conditions]
            + [condition.copy()]
        )
        prior_conditions.append(condition)

    if case_expr.args.get("default") is not None:
        predicate_sets.append([
            negate_predicate(condition.copy())
            for condition in prior_conditions
        ])

    return predicate_sets


def _collect_case_arm_predicate_sets(tree: exp.Expression) -> list[list[exp.Expression]]:
    predicate_sets: list[list[exp.Expression]] = []
    for case_expr in tree.find_all(exp.Case):
        predicate_sets.extend(_case_arm_predicate_sets(case_expr))
    return predicate_sets


# ---------------------------------------------------------------------------
# Subquery handling
# ---------------------------------------------------------------------------


def _find_subquery_predicates(
    expr: exp.Expression,
) -> list[tuple[str, exp.Select | None, exp.Expression | None]]:
    """Find subquery predicates in *expr*.

    Returns list of ``(kind, inner_select, outer_expr)`` tuples:
    - ``kind`` is ``"exists"``, ``"not_exists"``, ``"in"``, or ``"not_in"``
    - ``inner_select`` is the inner ``exp.Select`` (``None`` if not a subquery)
    - ``outer_expr`` is the left operand for ``IN`` / ``NOT IN``, ``None`` for EXISTS variants
    """
    results: list[tuple[str, exp.Select | None, exp.Expression | None]] = []

    # Collect Not-wrapped subqueries first (NOT EXISTS, NOT IN)
    not_wrapped: set[int] = set()
    for not_node in list(expr.find_all(exp.Not)):
        inner = not_node.this
        if isinstance(inner, exp.Exists):
            sq = inner.args.get("this")
            if isinstance(sq, exp.Subquery) and isinstance(sq.this, exp.Select):
                results.append(("not_exists", sq.this, None))
                not_wrapped.add(id(inner))
        elif isinstance(inner, exp.In):
            sq = inner.args.get("query")
            if isinstance(sq, exp.Subquery) and isinstance(sq.this, exp.Select):
                results.append(("not_in", sq.this, inner.this))
                not_wrapped.add(id(inner))

    # Collect standalone Exists
    for node in list(expr.find_all(exp.Exists)):
        if id(node) in not_wrapped:
            continue
        sq = node.args.get("this")
        if isinstance(sq, exp.Subquery) and isinstance(sq.this, exp.Select):
            results.append(("exists", sq.this, None))

    # Collect standalone In with subquery
    for node in list(expr.find_all(exp.In)):
        if id(node) in not_wrapped:
            continue
        sq = node.args.get("query")
        if isinstance(sq, exp.Subquery) and isinstance(sq.this, exp.Select):
            results.append(("in", sq.this, node.this))

    return results


_SCALAR_CMP_KINDS: dict[type, str] = {
    exp.EQ: "eq",
    exp.NEQ: "neq",
    exp.GT: "gt",
    exp.GTE: "gte",
    exp.LT: "lt",
    exp.LTE: "lte",
}


def _find_scalar_subquery_items(
    expr: exp.Expression,
) -> list[tuple[str, exp.Select, exp.Expression]]:
    """Detect scalar subqueries in comparison expressions.

    For example ``col = (SELECT ...)``, ``col > (SELECT ...)``.

    Returns ``[(kind, inner_select, outer_expr), ...]`` where
    *kind* is ``"eq"``, ``"neq"``, ``"gt"``, ``"gte"``, ``"lt"``, or ``"lte"``.
    """
    cmp_cls = type(expr)
    kind = _SCALAR_CMP_KINDS.get(cmp_cls)
    if kind is None:
        return []
    left_sq = isinstance(expr.this, exp.Subquery) and isinstance(expr.this.this, exp.Select)
    right_sq = isinstance(expr.expression, exp.Subquery) and isinstance(expr.expression.this, exp.Select)
    if left_sq and not right_sq:
        return [(kind, expr.this.this, expr.expression)]
    if right_sq and not left_sq:
        return [(kind, expr.expression.this, expr.this)]
    return []


def _collect_inner_vars(
    inner_select: exp.Select,
    instance: Instance,
    relation_info: RelationScopeInfo,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
) -> None:
    """Collect SolverVars for inner-SELECT columns not already in *col_var_map*."""
    for col in _scope_columns(relation_info, inner_select):
        key = _resolve_column_key(col, inner_select, relation_info, instance)
        if key is None:
            continue
        _ensure_solver_var(key, instance, col_var_map, table_vars, occurrence_tables)


def _select_occurrences(
    select: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> set[OccurrenceKey]:
    occurrences: set[OccurrenceKey] = set()
    for col in _scope_columns(relation_info, select):
        key = _resolve_column_key(col, select, relation_info, instance)
        if key is not None:
            occurrences.add(_occurrence_for_key(key))
    return occurrences


def _extract_correlation_equalities(
    inner_where_expr: exp.Expression,
    outer_select: exp.Select,
    inner_select: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> list[tuple[exp.Column, exp.Column]]:
    """Extract equi-join correlations: ``outer.col = inner.col`` pairs.

    Returns ``[(outer_col, inner_col), ...]`` from equality predicates
    that cross the outer and inner scopes.
    """
    correlations: list[tuple[exp.Column, exp.Column]] = []
    for eq in list(inner_where_expr.find_all(exp.EQ)):
        if not isinstance(eq.this, exp.Column) or not isinstance(eq.expression, exp.Column):
            continue
        left_outer = _resolve_column_key(eq.this, outer_select, relation_info, instance)
        right_outer = _resolve_column_key(eq.expression, outer_select, relation_info, instance)
        left_inner = _resolve_column_key(eq.this, inner_select, relation_info, instance)
        right_inner = _resolve_column_key(eq.expression, inner_select, relation_info, instance)

        if left_outer is not None and right_inner is not None and right_outer is None:
            correlations.append((eq.this, eq.expression))
        elif right_outer is not None and left_inner is not None and left_outer is None:
            correlations.append((eq.expression, eq.this))
    return correlations


def _process_exists_in_subquery(
    inner_select: exp.Select,
    kind: str,
    outer_expr: exp.Expression | None,
    instance: Instance,
    outer_select: exp.Select,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    dialect: str,
    *,
    relation_info: RelationScopeInfo,
) -> tuple[list[exp.Expression], list[tuple[SolverVar, SolverVar]]]:
    """Process EXISTS / IN (subquery).

    Returns ``(predicates, equalities)`` the solver must satisfy for the
    EXISTS/IN condition to produce at least one matching row.
    """
    # Collect SolverVars for inner columns
    _collect_inner_vars(
        inner_select, instance, relation_info, col_var_map, table_vars, occurrence_tables,
    )

    inner_predicates: list[exp.Expression] = []
    inner_equalities: list[tuple[SolverVar, SolverVar]] = []

    # Extract inner WHERE predicates (correlation equalities feed into solver)
    inner_where = inner_select.args.get("where")
    if inner_where is not None:
        # Parse correlation equalities and add as join equalities
        correlations = _extract_correlation_equalities(
            inner_where.this, outer_select, inner_select, relation_info, instance,
        )
        remaining_conjuncts: list[exp.Expression] = []
        for conj in flatten_conjuncts(inner_where.this):
            is_correlation = False
            for outer_col, inner_col in correlations:
                if (isinstance(conj, exp.EQ)
                    and ((conj.this is outer_col and conj.expression is inner_col)
                         or (conj.this is inner_col and conj.expression is outer_col))):
                    # This is a correlation equality — add as SolverVar equality
                    outer_key = _resolve_column_key(outer_col, outer_select, relation_info, instance)
                    inner_key = _resolve_column_key(inner_col, inner_select, relation_info, instance)
                    if outer_key and inner_key:
                        ov = col_var_map.get(outer_key)
                        iv = col_var_map.get(inner_key)
                        if ov is not None and iv is not None:
                            inner_equalities.append((ov, iv))
                    is_correlation = True
                    break
            if not is_correlation:
                remaining_conjuncts.append(conj)

        # Add remaining inner WHERE predicates
        for conj in remaining_conjuncts:
            _add_predicate(
                inner_predicates, conj, inner_select, relation_info, col_var_map, instance,
            )

    # For IN, add equality: outer_expr = subquery SELECT expression
    if kind == "in" and outer_expr is not None:
        inner_projections = inner_select.args.get("expressions") or []
        if inner_projections:
            inner_proj = inner_projections[0]
            outer_rewritten = _replace_columns_with_vars(
                outer_expr,
                current_select=outer_select,
                relation_info=relation_info,
                col_var_map=col_var_map,
                instance=instance,
            )
            inner_rewritten = _replace_columns_with_vars(
                _unwrap_projection_expression(inner_proj),
                current_select=inner_select,
                relation_info=relation_info,
                col_var_map=col_var_map,
                instance=instance,
            )
            if isinstance(outer_rewritten, SolverVar) and isinstance(inner_rewritten, SolverVar):
                inner_equalities.append((outer_rewritten, inner_rewritten))
            else:
                inner_predicates.append(
                    exp.EQ(this=outer_rewritten, expression=inner_rewritten),
                )

    return inner_predicates, inner_equalities


def _process_scalar_subquery(
    inner_select: exp.Select,
    kind: str,
    outer_expr: exp.Expression,
    instance: Instance,
    outer_select: exp.Select,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    dialect: str,
    *,
    relation_info: RelationScopeInfo,
) -> tuple[list[exp.Expression], list[tuple[SolverVar, SolverVar]]]:
    """Process a scalar subquery in a comparison expression.

    Collects inner-SELECT solver vars and predicates, then links the
    outer expression to the inner projected column with the appropriate
    equality/inequality constraint.

    Returns ``(predicates, equalities)``.
    """
    _collect_inner_vars(
        inner_select, instance, relation_info, col_var_map, table_vars, occurrence_tables,
    )

    inner_predicates: list[exp.Expression] = []
    inner_equalities: list[tuple[SolverVar, SolverVar]] = []

    inner_where = inner_select.args.get("where")
    if inner_where is not None:
        correlations = _extract_correlation_equalities(
            inner_where.this, outer_select, inner_select, relation_info, instance,
        )
        remaining: list[exp.Expression] = []
        for conj in flatten_conjuncts(inner_where.this):
            is_correlation = False
            for outer_col, inner_col in correlations:
                if (isinstance(conj, exp.EQ)
                    and ((conj.this is outer_col and conj.expression is inner_col)
                         or (conj.this is inner_col and conj.expression is outer_col))):
                    outer_key = _resolve_column_key(outer_col, outer_select, relation_info, instance)
                    inner_key = _resolve_column_key(inner_col, inner_select, relation_info, instance)
                    if outer_key and inner_key:
                        ov = col_var_map.get(outer_key)
                        iv = col_var_map.get(inner_key)
                        if ov is not None and iv is not None:
                            inner_equalities.append((ov, iv))
                    is_correlation = True
                    break
            if not is_correlation:
                remaining.append(conj)

        for conj in remaining:
            _add_predicate(
                inner_predicates, conj, inner_select, relation_info, col_var_map, instance,
            )

    inner_projections = inner_select.args.get("expressions") or []
    if inner_projections:
        inner_proj = _unwrap_projection_expression(inner_projections[0])
        outer_rewritten = _replace_columns_with_vars(
            outer_expr,
            current_select=outer_select,
            relation_info=relation_info,
            col_var_map=col_var_map,
            instance=instance,
        )
        inner_rewritten = _replace_columns_with_vars(
            inner_proj,
            current_select=inner_select,
            relation_info=relation_info,
            col_var_map=col_var_map,
            instance=instance,
        )
        if kind == "eq":
            if isinstance(outer_rewritten, SolverVar) and isinstance(inner_rewritten, SolverVar):
                inner_equalities.append((outer_rewritten, inner_rewritten))
            else:
                inner_predicates.append(
                    exp.EQ(this=outer_rewritten, expression=inner_rewritten),
                )
        else:
            cls = _inverse_scalar_cmp(kind)
            inner_predicates.append(cls(this=outer_rewritten, expression=inner_rewritten))

    return inner_predicates, inner_equalities


def _inverse_scalar_cmp(kind: str) -> type:
    for cls, k in _SCALAR_CMP_KINDS.items():
        if k == kind:
            return cls
    raise ValueError(f"unknown scalar comparison kind: {kind}")



def _collect_not_exists_not_in_items(
    inner_select: exp.Select,
    kind: str,
    outer_expr: exp.Expression | None,
    instance: Instance,
    outer_select: exp.Select,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    *,
    relation_info: RelationScopeInfo,
) -> list[dict[str, Any]]:
    """Collect NOT EXISTS / NOT IN info for two-phase seeding.

    Returns a list of dicts with keys:
    - ``"inner_select"``: the inner SELECT
    - ``"inner_occurrences"``: inner table occurrences to seed
    - ``"correlation_cols"``: list of ``(outer_key, inner_key)`` pairs
    - ``"outer_in_expr"``: for NOT IN, the outer expression
    """
    _collect_inner_vars(
        inner_select, instance, relation_info, col_var_map, table_vars, occurrence_tables,
    )
    inner_occurrences = _select_occurrences(inner_select, relation_info, instance)

    inner_where = inner_select.args.get("where")
    correlations: list[tuple[exp.Column, exp.Column]] = []
    if inner_where is not None:
        for outer_col, inner_col in _extract_correlation_equalities(
            inner_where.this, outer_select, inner_select, relation_info, instance,
        ):
            correlations.append((outer_col, inner_col))

    correlation_cols: list[tuple[ColumnKey, ColumnKey]] = []
    for outer_col, inner_col in correlations:
        outer_k = _resolve_column_key(outer_col, outer_select, relation_info, instance)
        inner_k = _resolve_column_key(inner_col, inner_select, relation_info, instance)
        if outer_k and inner_k:
            correlation_cols.append((outer_k, inner_k))
            inner_occurrences.add(_occurrence_for_key(inner_k))

    if not correlation_cols and kind == "not_exists":
        return []

    return [{
        "kind": kind,
        "inner_select": inner_select,
        "inner_occurrences": inner_occurrences,
        "outer_select": outer_select,
        "relation_info": relation_info,
        "correlation_cols": correlation_cols,
        "outer_in_expr": outer_expr,
    }]


def _seed_not_exists_not_in(
    instance: Instance,
    items: list[dict[str, Any]],
    assignments: dict[SolverVar, Any],
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    dialect: str,
    budget: GenerationBudget,
) -> None:
    """Two-phase seeding for NOT EXISTS / NOT IN.

    For each item, seed inner-table rows with values that
    deliberately do NOT match the already-seeded outer correlation values.
    """
    for item in items:
        kind = item["kind"]
        inner_occurrences = item["inner_occurrences"]
        correlation_cols = item["correlation_cols"]
        outer_in_expr = item["outer_in_expr"]

        if kind == "not_exists" and not correlation_cols:
            continue

        inner_select = item["inner_select"]

        # Resolve outer correlation values from the solver assignment
        outer_values: list[Any] = []
        inner_vars: list[SolverVar] = []
        for outer_key, inner_key in correlation_cols:
            ov = col_var_map.get(outer_key)
            iv = col_var_map.get(inner_key)
            if ov is not None and iv is not None:
                val = assignments.get(ov)
                if val is not None:
                    outer_values.append(val)
                    inner_vars.append(iv)
                else:
                    outer_values.append(None)
                    inner_vars.append(iv)
            else:
                outer_values.append(None)
                inner_vars.append(None)

        if kind == "not_in" and outer_in_expr is not None:
            outer_rewritten = _replace_columns_with_vars(
                outer_in_expr,
                current_select=item["outer_select"],
                relation_info=item["relation_info"],
                col_var_map=col_var_map,
                instance=instance,
            )
            if isinstance(outer_rewritten, SolverVar):
                val = assignments.get(outer_rewritten)
                if val is not None:
                    outer_values = [val]
                    inner_projections = inner_select.args.get("expressions") or []
                    if inner_projections:
                        inner_proj = _unwrap_projection_expression(inner_projections[0])
                        inner_k = (
                            _resolve_column_key(
                                inner_proj,
                                inner_select,
                                item["relation_info"],
                                instance,
                            )
                            if isinstance(inner_proj, exp.Column)
                            else None
                        )
                        if inner_k:
                            inner_v = col_var_map.get(inner_k)
                            inner_vars = [inner_v] if inner_v else [None]

        # Build non-matching constraints
        non_matching_constraints: list[exp.Expression] = []
        for ov, iv in zip(outer_values, inner_vars):
            if ov is not None and iv is not None:
                non_matching_constraints.append(
                    exp.NEQ(this=iv.copy(), expression=_literal_for_value(ov)),
                )

        if not non_matching_constraints and kind != "not_in":
            continue

        # Use solver to find a non-matching row for each inner table
        for inner_occurrence in inner_occurrences:
            solver_constraints: list[exp.Expression] = []

            solver_constraints.extend(non_matching_constraints)
            table_subset = {
                occurrence: vars
                for occurrence, vars in table_vars.items()
                if occurrence == inner_occurrence
            }
            if not table_subset:
                continue
            subset_occurrence_tables = {
                occurrence: occurrence_tables[occurrence]
                for occurrence in table_subset
            }
            try:
                _ensure_check_constraint_vars(instance, col_var_map, table_subset, subset_occurrence_tables)
                _add_database_constraints(
                    solver_constraints,
                    instance,
                    col_var_map,
                    table_subset,
                    subset_occurrence_tables,
                    include_same_batch_fk_targets=True,
                )
            except SchemaConstraintLoweringError:
                continue

            problem = Problem(
                constraints=solver_constraints,
                equalities=[],
                variables={var for vars in table_subset.values() for var in vars},
            )
            result = budget.solve(problem, dialect=dialect)

            if result.sat:
                occurrence_rows = {
                    occurrence: _row_from_assignments(result.assignments, vars)
                    for occurrence, vars in table_subset.items()
                }
                rows_by_table = _rows_by_physical_table(
                    occurrence_rows, subset_occurrence_tables,
                )
                if rows_by_table:
                    _try_create_rows(
                        instance,
                        rows_by_table,
                        reason="not_exists_not_in_solver",
                        budget=budget,
                    )


def _extract_equality_pair(
    expr: exp.Expression,
    current_select: exp.Select,
    relation_info: RelationScopeInfo | None,
    col_var_map: dict[ColumnKey, SolverVar],
    instance: Instance,
) -> tuple[SolverVar, SolverVar] | None:
    """If *expr* is ``col_a = col_b``, return the SolverVar pair."""
    if not isinstance(expr, exp.EQ):
        return None
    if relation_info is None:
        relation_info = _collect_relation_scope_info(current_select, instance)
    left_key = (
        _resolve_column_key(expr.this, current_select, relation_info, instance)
        if isinstance(expr.this, exp.Column)
        else None
    )
    right_key = (
        _resolve_column_key(expr.expression, current_select, relation_info, instance)
        if isinstance(expr.expression, exp.Column)
        else None
    )
    if left_key is None or right_key is None:
        return None
    left_var = col_var_map.get(left_key)
    right_var = col_var_map.get(right_key)
    if left_var is not None and right_var is not None:
        return (left_var, right_var)
    return None


def _add_not_null_constraints(
    predicates: list[exp.Expression],
    col_var_map: dict[ColumnKey, SolverVar],
) -> None:
    """Add IS NOT NULL for every column that is NOT NULL in the schema."""
    for var in col_var_map.values():
        if var.dtype.args.get("nullable") is False:
            predicates.append(exp.Not(this=exp.Is(this=var, expression=exp.Null())))


def _add_not_null_for_column(
    predicates: list[exp.Expression],
    col: exp.Column,
    current_select: exp.Select,
    relation_info: RelationScopeInfo | None,
    col_var_map: dict[ColumnKey, SolverVar],
    instance: Instance,
) -> None:
    """Add a single IS NOT NULL constraint if the column has a SolverVar."""
    if relation_info is None:
        relation_info = _collect_relation_scope_info(current_select, instance)
    key = _resolve_column_key(col, current_select, relation_info, instance)
    if key is not None and key in col_var_map:
        var = col_var_map[key]
        predicates.append(exp.Not(this=exp.Is(this=var, expression=exp.Null())))


def _ensure_check_constraint_vars(
    instance: Instance,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
) -> None:
    """Add internal solver vars for supported CHECK columns missing from the query."""
    for occurrence in list(table_vars):
        table_node = occurrence_tables[occurrence]
        tkey = table_key(table_node)
        table_schema = instance.database_constraints(table_node)
        for check in table_schema.checks:
            if not check.supported:
                raise SchemaConstraintLoweringError(
                    f"unsupported_check_constraint:{tkey}:{check.reason or 'unknown'}"
                )
            for col_ident in check.referenced_columns:
                existing = next(
                    (
                        var for (table, alias, column), var in col_var_map.items()
                        if table == table_node
                        and alias == occurrence[1]
                        and column == col_ident
                    ),
                    None,
                )
                if existing is not None:
                    if existing not in table_vars.setdefault(occurrence, []):
                        table_vars[occurrence].append(existing)
                    continue
                dtype = instance.get_column_type(table_node, col_ident)
                if not instance.nullable(table_node, col_ident):
                    dtype = dtype.copy()
                    dtype.args["nullable"] = False
                key = (table_node, occurrence[1], col_ident)
                var = SolverVar(
                    key=f"{tkey}._check.{col_ident.name}",
                    dtype=dtype,
                    meta={
                        "internal": "check",
                        "table": table_node,
                        "alias": occurrence[1],
                        "column": col_ident,
                    },
                )
                col_var_map[key] = var
                if var not in table_vars.setdefault(occurrence, []):
                    table_vars[occurrence].append(var)


# ---------------------------------------------------------------------------
# Column to SolverVar replacement
# ---------------------------------------------------------------------------


def _replace_columns_with_vars(
    expr: exp.Expression,
    *,
    current_select: exp.Select,
    relation_info: RelationScopeInfo | None,
    col_var_map: Mapping[ColumnKey, SolverVar],
    instance: Instance,
) -> exp.Expression:
    """Return a copy of *expr* with ``exp.Column`` nodes replaced by SolverVars."""
    if relation_info is None:
        relation_info = _collect_relation_scope_info(current_select, instance)

    def replacer(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            key = _resolve_column_key(node, current_select, relation_info, instance)
            if key is not None and key in col_var_map:
                return col_var_map[key].copy()
        return node

    return expr.copy().transform(replacer)


def _solver_predicate_or_none(
    expr: exp.Expression,
    *,
    current_select: exp.Select,
    relation_info: RelationScopeInfo | None,
    col_var_map: Mapping[ColumnKey, SolverVar],
    instance: Instance,
) -> exp.Expression | None:
    transformed = _replace_columns_with_vars(
        expr,
        current_select=current_select,
        relation_info=relation_info,
        col_var_map=col_var_map,
        instance=instance,
    )
    if any(transformed.find_all(exp.Column)):
        return None
    return transformed


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _row_from_assignments(
    assignments: dict[SolverVar, Any],
    vars: list[SolverVar],
) -> dict[exp.Identifier, Any]:
    row: dict[exp.Identifier, Any] = {}
    for var in vars:
        if var not in assignments:
            continue
        col_ident = var.meta["column"]
        if not isinstance(col_ident, exp.Identifier):
            continue
        if var.meta.get("internal") == "check" and col_ident in row:
            continue
        row[col_ident] = assignments[var]
    return row


def _rows_by_physical_table(
    occurrence_rows: Mapping[OccurrenceKey, Mapping[exp.Identifier, Any]],
    occurrence_tables: Mapping[OccurrenceKey, exp.Table],
) -> dict[exp.Table, list[dict[exp.Identifier, Any]]]:
    rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
    for occurrence, row in occurrence_rows.items():
        table_node = occurrence_tables[occurrence]
        table_rows = rows_by_table.setdefault(table_node, [])
        for existing in table_rows:
            shared = set(existing).intersection(row)
            if all(existing[column] == row[column] for column in shared):
                existing.update(row)
                break
        else:
            table_rows.append(dict(row))
    return rows_by_table


def _rows_by_table_from_assignments(
    assignments: dict[SolverVar, Any],
    table_vars: Mapping[OccurrenceKey, list[SolverVar]],
    occurrence_tables: Mapping[OccurrenceKey, exp.Table],
) -> dict[exp.Table, list[dict[exp.Identifier, Any]]]:
    occurrence_rows = {
        occurrence: _row_from_assignments(assignments, vs)
        for occurrence, vs in table_vars.items()
    }
    return _rows_by_physical_table(occurrence_rows, occurrence_tables)


def _row_value_maps(instance: Instance, table_node: exp.Table) -> list[dict[exp.Identifier, Any]]:
    return [
        Instance._row_value_dict(row)
        for row in instance.get_rows(table_node)
    ]


def _normalize_pending_row(
    instance: Instance,
    table_node: exp.Table,
    row: Mapping[exp.Identifier | str, Any],
) -> dict[exp.Identifier, Any]:
    return {
        instance.resolve_column(table_node, column): value
        for column, value in row.items()
    }


def _reserve_unique_values(
    instance: Instance,
    rows_by_table: Mapping[exp.Table, Sequence[Mapping[exp.Identifier | str, Any]]],
) -> dict[exp.Table, list[dict[exp.Identifier, Any]]]:
    reserved: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
    for table_node, rows in rows_by_table.items():
        table_node = instance.resolve_table(table_node)
        table_schema = instance.database_constraints(table_node)
        seen = _row_value_maps(instance, table_node)
        table_rows: list[dict[exp.Identifier, Any]] = []
        for raw_row in rows:
            row = _normalize_pending_row(instance, table_node, raw_row)
            for group in table_schema.uniqueness_groups():
                if any(col not in row or row[col] is None for col in group):
                    continue
                target = tuple(row[col] for col in group)
                if not any(tuple(existing.get(col) for col in group) == target for existing in seen):
                    continue
                adjust_col = group[-1]
                avoid = [
                    existing.get(adjust_col)
                    for existing in seen
                    if all(
                        col == adjust_col or existing.get(col) == row.get(col)
                        for col in group
                    )
                ]
                try:
                    row[adjust_col] = instance._domain.next_value(
                        table_node,
                        adjust_col,
                        existing_rows=seen,
                        avoid=avoid,
                    )
                except DomainError:
                    logger.info(
                        "speculate_generation_skipped:unique_reservation_failed:%s:%s",
                        table_key(table_node),
                        tuple(col.name for col in group),
                    )
            table_rows.append(row)
            seen.append(dict(row))
            for fk in table_schema.foreign_keys:
                if len(fk.source_columns) != 1 or len(fk.target_columns) != 1:
                    continue
                if instance.resolve_table(fk.target_table) != table_node:
                    continue
                source_col = fk.source_columns[0]
                target_col = fk.target_columns[0]
                if source_col == target_col:
                    continue
                source_value = row.get(source_col)
                target_value = row.get(target_col)
                if source_value is None or target_value is None:
                    continue
                if instance.nullable(table_node, source_col):
                    row[source_col] = None
                    continue
                if any(existing.get(target_col) == source_value for existing in seen):
                    continue
                companion = {
                    target_col: source_value,
                    source_col: target_value,
                }
                table_rows.append(companion)
                seen.append(dict(companion))
        reserved[table_node] = table_rows
    return reserved


def _try_create_rows(
    instance: Instance,
    rows_by_table: Mapping[exp.Table, Sequence[Mapping[exp.Identifier | str, Any]]],
    *,
    reason: str,
    budget: GenerationBudget,
) -> bool:
    budget_reason = budget.row_reason(instance, rows_by_table)
    if budget_reason:
        logger.info("speculate_generation_skipped:%s:%s", reason, budget_reason)
        return False
    token = instance.checkpoint()
    try:
        instance.create_rows(_reserve_unique_values(instance, rows_by_table))
        return True
    except (DomainError, KeyError) as exc:
        instance.rollback(token)
        logger.info("speculate_generation_skipped:%s:%s", reason, exc)
        return False


def _prepare_database_constraints(
    instance: Instance,
    constraints: list[exp.Expression],
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
) -> bool:
    try:
        _ensure_check_constraint_vars(
            instance, col_var_map, table_vars, occurrence_tables,
        )
        _add_database_constraints(
            constraints, instance, col_var_map, table_vars, occurrence_tables,
            include_same_batch_fk_targets=True,
        )
    except SchemaConstraintLoweringError:
        return False
    return True


def _seed_case_arm_rows(
    instance: Instance,
    tree: exp.Select,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    constraints: Sequence[exp.Expression],
    equalities: Sequence[tuple[SolverVar, SolverVar]],
    *,
    relation_info: RelationScopeInfo,
    budget: GenerationBudget,
    dialect: str = "sqlite",
) -> None:
    arm_predicate_sets = _collect_case_arm_predicate_sets(tree)
    if not arm_predicate_sets:
        return

    variables = set(col_var_map.values())
    for arm_predicates in arm_predicate_sets:
        arm_constraints: list[exp.Expression] = list(constraints)
        for predicate in arm_predicates:
            lowered = _solver_predicate_or_none(
                predicate,
                current_select=tree,
                relation_info=relation_info,
                col_var_map=col_var_map,
                instance=instance,
            )
            if lowered is None:
                break
            arm_constraints.extend(flatten_conjuncts(lowered))
        else:
            problem = Problem(
                constraints=arm_constraints,
                equalities=list(equalities),
                variables=variables,
            )
            result = budget.solve(problem, dialect=dialect)
            if not result.sat:
                continue

            _try_create_rows(
                instance,
                _rows_by_table_from_assignments(
                    result.assignments, table_vars, occurrence_tables,
                ),
                reason="case_arm_solver_assignment",
                budget=budget,
            )


def _coerced_group_value(
    value: Any,
    var: SolverVar,
    instance: Instance,
) -> Any | None:
    try:
        return coerce_literal_value(
            value,
            var.dtype,
            instance.dialect,
            for_equality=True,
        )
    except CoercionError:
        return None


def _group_var_for_column(
    column: exp.Expression,
    tree: exp.Select,
    group_key_set: set[ColumnKey],
    col_var_map: Mapping[ColumnKey, SolverVar],
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> tuple[ColumnKey, SolverVar] | None:
    if not isinstance(column, exp.Column):
        return None
    key = _resolve_column_key(column, tree, relation_info, instance)
    if key not in group_key_set:
        return None
    var = col_var_map.get(key)
    if var is None:
        return None
    return key, var


def _group_candidate_values(
    tree: exp.Select,
    group_column_keys: Sequence[ColumnKey],
    col_var_map: Mapping[ColumnKey, SolverVar],
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> dict[ColumnKey, list[Any]]:
    candidates: dict[ColumnKey, list[Any]] = {}
    where = tree.args.get("where")
    if where is None:
        return candidates

    group_key_set = set(group_column_keys)
    for conjunct in flatten_conjuncts(where.this):
        if isinstance(conjunct, exp.In):
            group_var = _group_var_for_column(
                conjunct.this,
                tree,
                group_key_set,
                col_var_map,
                relation_info,
                instance,
            )
            if group_var is None:
                continue
            key, var = group_var
            values: list[Any] = []
            for expr in conjunct.expressions:
                value = _coerced_group_value(literal_value(expr), var, instance)
                if value is not None and value not in values:
                    values.append(value)
            if values:
                candidates[key] = values
        elif isinstance(conjunct, exp.EQ):
            column, literal = conjunct.this, conjunct.expression
            if not isinstance(column, exp.Column):
                column, literal = literal, column
            group_var = _group_var_for_column(
                column,
                tree,
                group_key_set,
                col_var_map,
                relation_info,
                instance,
            )
            if group_var is None:
                continue
            key, var = group_var
            value = _coerced_group_value(literal_value(literal), var, instance)
            if value is not None:
                candidates[key] = [value]
    return candidates


def _finite_group_target_tuples(
    tree: exp.Select,
    group_column_keys: Sequence[ColumnKey],
    col_var_map: Mapping[ColumnKey, SolverVar],
    relation_info: RelationScopeInfo,
    instance: Instance,
    *,
    group_count: int,
) -> list[tuple[Any, ...]]:
    finite_values = _group_candidate_values(
        tree,
        group_column_keys,
        col_var_map,
        relation_info,
        instance,
    )
    if finite_values and all(key in finite_values for key in group_column_keys):
        return list(islice(
            product(*(finite_values[key] for key in group_column_keys)),
            group_count,
        ))
    return []


def _int_literal(expr: exp.Expression) -> int | None:
    value = literal_value(expr)
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _count_column_key(
    count_expr: exp.Count,
    tree: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> tuple[bool, ColumnKey | None, bool]:
    operand = count_expr.this
    if isinstance(operand, exp.Star):
        return True, None, False
    is_distinct = False
    if isinstance(operand, exp.Distinct):
        expressions = operand.expressions
        if len(expressions) != 1:
            return False, None, False
        operand = expressions[0]
        is_distinct = True
    if isinstance(operand, exp.Column):
        key = _resolve_column_key(operand, tree, relation_info, instance)
        return (True, key, is_distinct) if key is not None else (False, None, False)
    return False, None, False


def _count_comparison(
    expr: exp.Expression,
    tree: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
) -> tuple[type[exp.Expression], int, ColumnKey | None, bool] | None:
    if not isinstance(expr, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return None

    left = expr.this
    right = expr.expression
    if isinstance(left, exp.Count):
        literal = _int_literal(right)
        supported, count_key, is_distinct = _count_column_key(
            left, tree, relation_info, instance,
        )
        if literal is None or not supported:
            return None
        return type(expr), literal, count_key, is_distinct

    if isinstance(right, exp.Count):
        literal = _int_literal(left)
        supported, count_key, is_distinct = _count_column_key(
            right, tree, relation_info, instance,
        )
        if literal is None or not supported:
            return None
        inverse: dict[type[exp.Expression], type[exp.Expression]] = {
            exp.EQ: exp.EQ,
            exp.GT: exp.LT,
            exp.GTE: exp.LTE,
            exp.LT: exp.GT,
            exp.LTE: exp.GTE,
        }
        return inverse[type(expr)], literal, count_key, is_distinct

    return None


def _apply_count_bound(
    lower: int,
    upper: int | None,
    exact: int | None,
    op: type[exp.Expression],
    target: int,
) -> tuple[int, int | None, int | None] | None:
    if op is exp.EQ:
        exact = target if exact is None else exact
        if exact != target:
            return None
    if op is exp.GT:
        lower = max(lower, target + 1)
    if op is exp.GTE:
        lower = max(lower, target)
    if op is exp.LT:
        upper = target - 1 if upper is None else min(upper, target - 1)
    if op is exp.LTE:
        upper = target if upper is None else min(upper, target)
    if exact is not None and (exact < lower or (upper is not None and exact > upper)):
        return None
    if upper is not None and upper < lower:
        return None
    return lower, upper, exact


def _count_target_sizes(
    comparisons: Sequence[tuple[type[exp.Expression], int, ColumnKey | None, bool]],
    *,
    group_count: int,
) -> tuple[int, ...]:
    lower = 1
    upper: int | None = None
    exact: int | None = None
    for op, target, _count_key, _is_distinct in comparisons:
        bounds = _apply_count_bound(lower, upper, exact, op, target)
        if bounds is None:
            return ()
        lower, upper, exact = bounds

    if exact is not None:
        return (exact,) if exact >= 1 and exact <= MIN_ROWS_CAP else ()
    if lower > MIN_ROWS_CAP:
        return ()
    upper = MIN_ROWS_CAP if upper is None else min(upper, MIN_ROWS_CAP)
    stop = min(upper, lower + group_count - 1)
    return tuple(range(lower, stop + 1))


def _having_count_plan(
    tree: exp.Select,
    relation_info: RelationScopeInfo,
    instance: Instance,
    *,
    group_count: int,
) -> tuple[tuple[int, ...], tuple[ColumnKey, ...], tuple[ColumnKey, ...]] | None:
    having = tree.args.get("having")
    if having is None:
        return tuple(range(1, group_count + 1)), (), ()

    comparisons: list[tuple[type[exp.Expression], int, ColumnKey | None, bool]] = []
    non_null_keys: list[ColumnKey] = []
    distinct_keys: list[ColumnKey] = []
    for conjunct in flatten_conjuncts(having.this):
        comparison = _count_comparison(conjunct, tree, relation_info, instance)
        if comparison is None:
            return None
        op, target, count_key, is_distinct = comparison
        comparisons.append((op, target, count_key, is_distinct))
        if count_key is not None and count_key not in non_null_keys:
            non_null_keys.append(count_key)
        if is_distinct and count_key is not None and count_key not in distinct_keys:
            distinct_keys.append(count_key)

    sizes = _count_target_sizes(comparisons, group_count=group_count)
    return sizes[:1], tuple(non_null_keys), tuple(distinct_keys)


def _row_scoped_solver_context(
    col_var_map: Mapping[ColumnKey, SolverVar],
    table_vars: Mapping[OccurrenceKey, list[SolverVar]],
    *,
    suffix: str,
) -> tuple[dict[ColumnKey, SolverVar], dict[OccurrenceKey, list[SolverVar]]]:
    row_col_var_map: dict[ColumnKey, SolverVar] = {}
    by_var_key: dict[str, SolverVar] = {}
    for key, var in col_var_map.items():
        row_var = SolverVar(
            key=f"{var.var_key}{suffix}",
            dtype=var.dtype,
            meta=var.meta,
        )
        row_col_var_map[key] = row_var
        by_var_key[var.var_key] = row_var

    row_table_vars: dict[OccurrenceKey, list[SolverVar]] = {}
    for occurrence, vars in table_vars.items():
        row_table_vars[occurrence] = [
            by_var_key[var.var_key]
            for var in vars
            if var.var_key in by_var_key
        ]

    return row_col_var_map, row_table_vars


def _remap_solver_vars(
    expr: exp.Expression,
    var_key_map: Mapping[str, SolverVar],
) -> exp.Expression:
    def replacer(node: exp.Expression) -> exp.Expression:
        if isinstance(node, SolverVar):
            new_var = var_key_map.get(node.var_key)
            if new_var is not None:
                return new_var.copy()
        return node

    return expr.copy().transform(replacer)


def _remap_equalities(
    equalities: Sequence[tuple[SolverVar, SolverVar]],
    var_key_map: Mapping[str, SolverVar],
) -> list[tuple[SolverVar, SolverVar]]:
    remapped: list[tuple[SolverVar, SolverVar]] = []
    for left, right in equalities:
        new_left = var_key_map.get(left.var_key)
        new_right = var_key_map.get(right.var_key)
        if new_left is not None and new_right is not None:
            remapped.append((new_left, new_right))
    return remapped


def _distinct_group_tuple_constraint(
    left_vars: Sequence[SolverVar],
    right_vars: Sequence[SolverVar],
) -> exp.Expression | None:
    comparisons = [
        exp.NEQ(this=left.copy(), expression=right.copy())
        for left, right in zip(left_vars, right_vars)
    ]
    if not comparisons:
        return None
    expr = comparisons[0]
    for comparison in comparisons[1:]:
        expr = exp.Or(this=expr, expression=comparison)
    return expr


def _seed_group_rows(
    instance: Instance,
    tree: exp.Select,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    constraints: Sequence[exp.Expression],
    equalities: Sequence[tuple[SolverVar, SolverVar]],
    *,
    relation_info: RelationScopeInfo,
    group_column_keys: Sequence[ColumnKey],
    aggregate_input_column_keys: Sequence[ColumnKey],
    group_count: int,
    budget: GenerationBudget,
    dialect: str = "sqlite",
    bootstrap_rows: int = 1,
) -> bool:
    group = tree.args.get("group")
    if (
        not group
        or not group.expressions
        or not group_column_keys
        or len(group_column_keys) != len(group.expressions)
    ):
        return False

    target_tuples = _finite_group_target_tuples(
        tree,
        group_column_keys,
        col_var_map,
        relation_info,
        instance,
        group_count=group_count,
    )
    if target_tuples:
        group_count = min(group_count, len(target_tuples))

    having_plan = _having_count_plan(
        tree,
        relation_info,
        instance,
        group_count=group_count,
    )
    if having_plan is None:
        target_sizes = [bootstrap_rows] * min(group_count, bootstrap_rows)
        having_non_null_column_keys = ()
        having_distinct_column_keys = ()
    else:
        having_sizes, having_non_null_column_keys, having_distinct_column_keys = having_plan
        target_sizes = list(having_sizes[:group_count])
        if not target_sizes:
            return False

    constrained_variable_keys = {
        variable.var_key
        for constraint in constraints
        for variable in constraint.find_all(SolverVar)
    }
    nullable_aggregate_keys = tuple(
        key
        for key in aggregate_input_column_keys
        if key in col_var_map
        and key not in group_column_keys
        and col_var_map[key].var_key not in constrained_variable_keys
        and instance.nullable(key[0], key[2])
    )
    occurrence_counts: dict[exp.Table, int] = {}
    for occurrence_table in occurrence_tables.values():
        table = instance.resolve_table(occurrence_table)
        occurrence_counts[table] = occurrence_counts.get(table, 0) + 1
    logical_capacity = budget.config.max_total_rows
    if occurrence_counts:
        logical_capacity = min(
            (
                budget.config.max_rows_per_table - len(instance.get_rows(table))
            )
            // count
            for table, count in occurrence_counts.items()
        )
        total_occurrences = sum(occurrence_counts.values())
        logical_capacity = min(
            logical_capacity,
            (
                budget.config.max_total_rows
                - sum(
                    len(instance.get_rows(table))
                    for table in instance.schema.fk_safe_table_order()
                )
            )
            // total_occurrences,
        )
    remaining_null_rows = max(logical_capacity - sum(target_sizes), 0)
    randomizer = random.Random(budget.config.seed)
    row_targets_by_group: list[list[ColumnKey | None]] = []
    for group_size in target_sizes:
        targets: list[ColumnKey | None] = [None] * group_size
        for key in nullable_aggregate_keys:
            null_count = min(
                randomizer.randint(1, max(group_size, 1)),
                remaining_null_rows,
            )
            targets.extend([key] * null_count)
            remaining_null_rows -= null_count
        row_targets_by_group.append(targets)

    batch_constraints: list[exp.Expression] = []
    batch_equalities: list[tuple[SolverVar, SolverVar]] = []
    variables: set[SolverVar] = set()
    rows: list[
        tuple[
            int,
            ColumnKey | None,
            dict[ColumnKey, SolverVar],
            dict[OccurrenceKey, list[SolverVar]],
        ]
    ] = []

    for group_index, row_targets in enumerate(row_targets_by_group):
        for row_index, null_key in enumerate(row_targets):
            suffix = f"._g{group_index}_r{row_index}"
            row_col_var_map, row_table_vars = _row_scoped_solver_context(
                col_var_map,
                table_vars,
                suffix=suffix,
            )
            row_var_key_map = {
                var.var_key: row_col_var_map[key]
                for key, var in col_var_map.items()
                if key in row_col_var_map
            }
            batch_constraints.extend(
                _remap_solver_vars(constraint, row_var_key_map)
                for constraint in constraints
            )
            batch_equalities.extend(_remap_equalities(equalities, row_var_key_map))
            try:
                _add_database_constraints(
                    batch_constraints,
                    instance,
                    row_col_var_map,
                    row_table_vars,
                    occurrence_tables,
                    include_same_batch_fk_targets=True,
                )
            except SchemaConstraintLoweringError:
                return False

            for key in group_column_keys:
                group_var = row_col_var_map[key]
                batch_constraints.append(
                    exp.Not(this=exp.Is(this=group_var, expression=exp.Null()))
                )
            for key in having_non_null_column_keys:
                if key == null_key:
                    continue
                count_var = row_col_var_map.get(key)
                if count_var is None:
                    return False
                batch_constraints.append(
                    exp.Not(this=exp.Is(this=count_var, expression=exp.Null()))
                )

            if null_key is not None:
                for key in nullable_aggregate_keys:
                    aggregate_var = row_col_var_map.get(key)
                    if aggregate_var is None:
                        return False
                    predicate = exp.Is(
                        this=aggregate_var,
                        expression=exp.Null(),
                    )
                    if key != null_key:
                        predicate = exp.Not(this=predicate)
                    batch_constraints.append(predicate)

            variables.update(row_col_var_map.values())
            rows.append((group_index, null_key, row_col_var_map, row_table_vars))

    first_row_by_group: dict[int, dict[ColumnKey, SolverVar]] = {}
    rows_by_group: dict[
        int,
        list[tuple[ColumnKey | None, dict[ColumnKey, SolverVar]]],
    ] = {}
    for group_index, null_key, row_col_var_map, _row_table_vars in rows:
        rows_by_group.setdefault(group_index, []).append((null_key, row_col_var_map))
        first = first_row_by_group.setdefault(group_index, row_col_var_map)
        if first is row_col_var_map:
            continue
        for key in group_column_keys:
            batch_equalities.append((row_col_var_map[key], first[key]))

    for distinct_key in having_distinct_column_keys:
        for group_rows in rows_by_group.values():
            distinct_vars = [
                row_col_var_map[distinct_key]
                for null_key, row_col_var_map in group_rows
                if null_key != distinct_key
            ]
            for left_index, left_var in enumerate(distinct_vars):
                for right_var in distinct_vars[left_index + 1:]:
                    batch_constraints.append(
                        exp.NEQ(this=left_var.copy(), expression=right_var.copy())
                    )

    if target_tuples:
        for group_index, target_values in enumerate(target_tuples[:len(target_sizes)]):
            first = first_row_by_group[group_index]
            for key, value in zip(group_column_keys, target_values):
                batch_constraints.append(
                    exp.EQ(this=first[key], expression=_literal_for_value(value))
                )

    group_vars = [
        [first_row_by_group[index][key] for key in group_column_keys]
        for index in range(len(target_sizes))
    ]
    for left_index, left_vars in enumerate(group_vars):
        for right_vars in group_vars[left_index + 1:]:
            distinct = _distinct_group_tuple_constraint(left_vars, right_vars)
            if distinct is not None:
                batch_constraints.append(distinct)

    result = budget.solve(
        Problem(
            constraints=batch_constraints,
            equalities=batch_equalities,
            variables=variables,
        ),
        dialect=dialect,
    )
    if not result.sat:
        return False

    rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
    for _group_index, _null_key, _row_col_var_map, row_table_vars in rows:
        row_rows_by_table = _rows_by_table_from_assignments(
            result.assignments,
            row_table_vars,
            occurrence_tables,
        )
        for table, table_rows in row_rows_by_table.items():
            rows_by_table.setdefault(table, []).extend(table_rows)

    return _try_create_rows(
        instance,
        rows_by_table,
        reason="group_batch_solver_assignment",
        budget=budget,
    )


def _seed_from_assignments(
    instance: Instance,
    first_assignments: dict[SolverVar, Any],
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    constraints: list[exp.Expression],
    equalities: list[tuple[SolverVar, SolverVar]],
    *,
    budget: GenerationBudget,
    group_count: int = 1,
    min_rows: int = 1,
    dialect: str = "sqlite",
    duplicate_column_keys: Sequence[ColumnKey] = (),
    aggregate_input_column_keys: Sequence[ColumnKey] = (),
    null_column_keys: Sequence[ColumnKey] = (),
) -> None:
    """Seed the instance with solver assignments, re-solving for extra rows.

    When *duplicate_column_keys* is provided, a portion of extra rows are
    generated with duplicate column values (multiple matching rows per
    group or duplicate projection values).
    When *aggregate_input_column_keys* is provided, a portion of extra rows
    set nullable aggregate-input columns to NULL.
    When *null_column_keys* is provided, a portion of extra rows also set
    nullable projection/FK columns to NULL.
    """
    total_rows = max(1, group_count, min_rows)

    if total_rows <= 1:
        _try_create_rows(
            instance,
            _rows_by_table_from_assignments(
                first_assignments, table_vars, occurrence_tables,
            ),
            reason="solver_assignment",
            budget=budget,
        )
        return

    if not _try_create_rows(
        instance,
        _rows_by_table_from_assignments(
            first_assignments, table_vars, occurrence_tables,
        ),
        reason="solver_assignment",
        budget=budget,
    ):
        return

    # Read first-row duplicate-key values from the instance (the instance
    # fills in random values even when solver assignments are empty).
    first_row_values: dict[ColumnKey, Any] = {}
    if duplicate_column_keys:
        for dkey in duplicate_column_keys:
            table_node, _alias, col_ident = dkey
            for occurrence in table_vars:
                if occurrence_tables[occurrence] == table_node:
                    existing = instance.get_rows(table_node)
                    if existing:
                        val = existing[0].column_values.get(col_ident)
                        if val is not None and hasattr(val, 'concrete'):
                            val = val.concrete
                        first_row_values[dkey] = val
                    break
    can_duplicate = bool(duplicate_column_keys) and len(first_row_values) == len(duplicate_column_keys)

    # Pre-compute nullable aggregate-input column keys for the NULL strategy
    nullable_agg_keys: list[ColumnKey] = []
    if aggregate_input_column_keys:
        for akey in aggregate_input_column_keys:
            var = col_var_map.get(akey)
            if var is not None and var.dtype.args.get("nullable") is not False:
                nullable_agg_keys.append(akey)
    nullable_fk_keys: list[ColumnKey] = [
        nkey for nkey in null_column_keys if nkey in col_var_map
    ]
    can_null = bool(nullable_agg_keys or nullable_fk_keys)

    extra_idx = 0
    while extra_idx < total_rows - 1:
        strategy = extra_idx % 3
        if (strategy == 1 and not can_duplicate) or (strategy == 2 and not can_null):
            strategy = 0

        suffix = f"_e{extra_idx}"
        new_col_var_map: dict[ColumnKey, SolverVar] = {}
        for key, var in col_var_map.items():
            new_var = SolverVar(
                key=f"{var.var_key}{suffix}",
                dtype=var.dtype,
                meta=var.meta,
            )
            new_col_var_map[key] = new_var

        var_key_map = {var.var_key: new_var for key, var in col_var_map.items() for new_key, new_var in new_col_var_map.items() if key == new_key}
        new_constraints: list[exp.Expression] = []
        for c in constraints:
            def replacer(node: exp.Expression) -> exp.Expression:
                if isinstance(node, SolverVar):
                    new = var_key_map.get(node.var_key)
                    if new is not None:
                        return new.copy()
                return node
            new_constraints.append(c.copy().transform(replacer))

        old_var_key = {v.var_key: k for k, v in col_var_map.items()}
        new_equalities: list[tuple[SolverVar, SolverVar]] = []
        for a, b in equalities:
            new_a = new_col_var_map.get(old_var_key.get(a.var_key))
            new_b = new_col_var_map.get(old_var_key.get(b.var_key))
            if new_a is not None and new_b is not None:
                new_equalities.append((new_a, new_b))

        if strategy == 1:
            for dkey, value in first_row_values.items():
                new_var = new_col_var_map.get(dkey)
                if new_var is not None and value is not None:
                    new_constraints.append(
                        exp.EQ(this=new_var, expression=_literal_for_value(value))
                    )
        elif strategy == 2:
            if nullable_agg_keys:
                for akey in nullable_agg_keys:
                    new_var = new_col_var_map.get(akey)
                    if new_var is not None:
                        new_constraints.append(
                            exp.Is(this=new_var, expression=exp.Null())
                        )
            elif nullable_fk_keys:
                for nkey in nullable_fk_keys:
                    new_var = new_col_var_map.get(nkey)
                    if new_var is not None:
                        new_constraints.append(
                            exp.Is(this=new_var, expression=exp.Null())
                        )

        _add_database_constraints(
            new_constraints,
            instance,
            new_col_var_map,
            table_vars,
            occurrence_tables,
            include_same_batch_fk_targets=True,
        )

        problem = Problem(
            constraints=new_constraints,
            equalities=new_equalities,
            variables=set(new_col_var_map.values()),
        )
        result = budget.solve(problem, dialect=dialect)

        if not result.sat:
            if strategy != 0:
                extra_idx += 1
                continue
            break

        extra_occurrence_rows: dict[OccurrenceKey, dict[exp.Identifier, Any]] = {}
        for occurrence, vs in table_vars.items():
            suffixed_assignments: dict[SolverVar, Any] = {}
            for var in vs:
                new_var_key = f"{var.var_key}{suffix}"
                new_var = next((v for v in result.assignments if v.var_key == new_var_key), None)
                if new_var is None or new_var not in result.assignments:
                    continue
                suffixed_assignments[var] = result.assignments[new_var]
            extra_occurrence_rows[occurrence] = _row_from_assignments(
                suffixed_assignments, vs,
            )
        extra_rows_by_table = _rows_by_physical_table(
            extra_occurrence_rows, occurrence_tables,
        )
        if _try_create_rows(
            instance,
            extra_rows_by_table,
            reason="extra_solver_assignment",
            budget=budget,
        ):
            extra_idx += 1
            continue
        extra_idx += 1


def _seed_extra_duplicate_rows(
    instance: Instance,
    duplicate_keys: Sequence[ColumnKey],
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    *,
    extra_count: int,
    budget: GenerationBudget,
) -> None:
    """Create *extra_count* rows duplicating first-row values for *duplicate_keys*.

    For multi-table queries, ``_ensure_fk_parents`` binds unset FK values
    to ``target_rows[0]``, producing FK concentration. For single-table
    queries, the explicit EQ constraints force duplicate column values
    (making DISTINCT observable).
    """
    if extra_count <= 0 or not duplicate_keys:
        return

    first_values: dict[ColumnKey, Any] = {}
    for dkey in duplicate_keys:
        table_node, _alias, col_ident = dkey
        for occurrence in table_vars:
            if occurrence_tables[occurrence] == table_node:
                existing = instance.get_rows(table_node)
                if existing:
                    val = existing[0].column_values.get(col_ident)
                    if val is not None and hasattr(val, 'concrete'):
                        val = val.concrete
                    first_values[dkey] = val
                break

    if not first_values:
        return

    values_by_table: dict[exp.Table, dict[exp.Identifier, Any]] = {}
    for dkey, val in first_values.items():
        table_node, _alias, col_ident = dkey
        if val is not None:
            values_by_table.setdefault(table_node, {})[col_ident] = val

    for _ in range(extra_count):
        rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
        for table_node in instance.schema.fk_safe_table_order():
            row_values = values_by_table.get(table_node, {})
            rows_by_table[table_node] = [dict(row_values)]
        _try_create_rows(instance, rows_by_table, reason="extra_duplicate_rows", budget=budget)


def _literal_for_value(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(str(value))
    return exp.Literal.string(str(value))


def _fk_candidate_expressions(
    instance: Instance,
    fk: Any,
    col_var_map: Mapping[ColumnKey, SolverVar],
) -> list[exp.Expression]:
    if len(fk.source_columns) != 1 or len(fk.target_columns) != 1:
        return []

    target_column = fk.target_columns[0]
    candidates: list[exp.Expression] = []
    for parent_row in instance.get_rows(fk.target_table):
        value = instance._row_value_dict(parent_row).get(target_column)
        if value is not None:
            candidates.append(_literal_for_value(value))

    candidates.extend(
        var
        for (table, _alias, column), var in col_var_map.items()
        if table == fk.target_table and column == target_column
    )
    return candidates


def _same_batch_fk_constraints(
    instance: Instance,
    table_node: exp.Table,
    sv_map: Mapping[str, SolverVar],
    col_var_map: Mapping[ColumnKey, SolverVar],
) -> list[exp.Expression]:
    constraints: list[exp.Expression] = []
    table_schema = instance.database_constraints(table_node)
    for fk in table_schema.foreign_keys:
        if len(fk.source_columns) != 1 or len(fk.target_columns) != 1:
            continue
        source_column = fk.source_columns[0]
        source_var = sv_map.get(source_column.name)
        if source_var is None:
            continue

        candidates = _fk_candidate_expressions(instance, fk, col_var_map)
        if candidates:
            constraints.append(exp.In(this=source_var, expressions=candidates))
    return constraints


def _add_database_constraints(
    constraints: list[exp.Expression],
    instance: Instance,
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    *,
    include_same_batch_fk_targets: bool = False,
) -> None:
    """Add uniqueness, FK, and CHECK constraints for all tables in *table_vars*.

    Follows the pattern of ``_database_constraints_for_solver`` in ``operator.py``.
    Unique-group and FK constraints add ``IN`` / ``NEQ`` predicates against
    existing rows; CHECK constraints embed the raw check expression with
    ``exp.Column`` nodes replaced by matching ``SolverVar`` nodes.

    Naturally a no-op for empty tables (no existing/parent rows yet), so safe
    to call for the first solve.
    """
    for occurrence in table_vars:
        table_node = occurrence_tables[occurrence]
        sv_map = {
            column.name: var
            for (table, alias, column), var in col_var_map.items()
            if table == table_node and alias == occurrence[1]
        }
        constraints.extend(
            schema_constraints_for_solver_row(
                instance,
                table_node,
                sv_map,
                exact_columns=set(sv_map),
                include_checks=True,
                include_existing_uniques=True,
                include_existing_fks=not include_same_batch_fk_targets,
            )
        )
        if include_same_batch_fk_targets:
            constraints.extend(
                _same_batch_fk_constraints(
                    instance,
                    table_node,
                    sv_map,
                    col_var_map,
                )
            )


def _check_aware_base_rows(
    instance: Instance,
    table_node: exp.Table,
    *,
    row_count: int,
    budget: GenerationBudget,
) -> list[dict[exp.Identifier, Any]] | None:
    table_schema = instance.database_constraints(table_node)
    check_columns = tuple(
        dict.fromkeys(
            column
            for check in table_schema.checks
            for column in check.referenced_columns
        )
    )
    if not check_columns:
        return None

    sv_map = {
        column.name: SolverVar(
            key=f"base.{table_key(table_node)}.{column.name}",
            dtype=table_schema.columns[column].datatype,
            meta={"column": column},
        )
        for column in check_columns
    }

    constraints = schema_constraints_for_solver_row(
        instance,
        table_node,
        sv_map,
        exact_columns=set(sv_map),
        include_checks=True,
        include_existing_uniques=True,
        include_existing_fks=True,
    )

    rows: list[dict[exp.Identifier, Any]] = []
    variables = set(sv_map.values())
    for _ in range(row_count):
        result = budget.solve(
            Problem(
                constraints=list(constraints),
                variables=variables,
            ),
            dialect=instance.dialect,
        )
        if not result.sat:
            return []
        row: dict[exp.Identifier, Any] = {}
        for column in check_columns:
            var = sv_map[column.name]
            if var not in result.assignments:
                return []
            row[column] = result.assignments[var]
        rows.append(row)

    return rows


def _seed_base_rows(
    instance: Instance,
    *,
    budget: GenerationBudget,
    row_count: int = 1,
    tables: set[exp.Table] | None = None,
    child_multiplier: int = 1,
) -> None:
    """Create *row_count* base rows per table (query tables only if *tables* is set).

    When *child_multiplier* > 1, child tables (tables whose FK targets are
    also in the seeded set) get ``row_count * child_multiplier`` rows,
    creating FK concentration via ``_ensure_fk_parents``.
    """
    active_tables: set[exp.Table] = set(
        tables or instance.schema.fk_safe_table_order()
    )
    child_tables: set[exp.Table] = set()
    for table_node in instance.schema.fk_safe_table_order():
        if tables is not None and table_node not in tables:
            continue
        table_schema = instance.database_constraints(table_node)
        for fk in table_schema.foreign_keys:
            if instance.resolve_table(fk.target_table) in active_tables:
                child_tables.add(table_node)
                break

    rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
    for table_node in instance.schema.fk_safe_table_order():
        if tables is not None and table_node not in tables:
            continue
        actual_count = (
            row_count * child_multiplier
            if table_node in child_tables
            else row_count
        )
        check_rows = _check_aware_base_rows(
            instance, table_node, row_count=actual_count, budget=budget,
        )
        rows_by_table[table_node] = (
            check_rows if check_rows is not None else [{} for _ in range(actual_count)]
        )
    _try_create_rows(instance, rows_by_table, reason="base_rows", budget=budget)


# ---------------------------------------------------------------------------
# Negative data seeding
# ---------------------------------------------------------------------------


def _seed_negative_rows(
    instance: Instance,
    where_atoms: list[exp.Expression],
    col_var_map: dict[ColumnKey, SolverVar],
    table_vars: dict[OccurrenceKey, list[SolverVar]],
    occurrence_tables: dict[OccurrenceKey, exp.Table],
    equalities: list[tuple[SolverVar, SolverVar]],
    dialect: str,
    budget: GenerationBudget,
    null_column_keys: list[ColumnKey] = (),
) -> None:
    """For each WHERE atom, seed a row that violates just that atom.

    For ``A AND B AND C``, this generates rows for ``NOT A AND B AND C``,
    ``A AND NOT B AND C``, and ``A AND B AND NOT C`` (where possible).

    When *null_column_keys* is provided, also seed rows where each nullable
    projection column is NULL while all WHERE atoms remain satisfied.
    """
    for i, atom in enumerate(where_atoms):
        negated = negate_predicate(atom)
        constraints: list[exp.Expression] = [negated]
        constraints.extend(
            a for j, a in enumerate(where_atoms) if j != i
        )
        try:
            _ensure_check_constraint_vars(instance, col_var_map, table_vars, occurrence_tables)
            _add_database_constraints(
                constraints, instance, col_var_map, table_vars, occurrence_tables,
                include_same_batch_fk_targets=True,
            )
        except SchemaConstraintLoweringError:
            continue

        problem = Problem(
            constraints=constraints,
            equalities=list(equalities),
            variables=set(col_var_map.values()),
        )
        result = budget.solve(problem, dialect=dialect)

        if result.sat:
            occurrence_rows = {
                occurrence: _row_from_assignments(result.assignments, vs)
                for occurrence, vs in table_vars.items()
            }
            rows_by_table = _rows_by_physical_table(occurrence_rows, occurrence_tables)
            _try_create_rows(
                instance,
                rows_by_table,
                reason="negative_rows",
                budget=budget,
            )

    for key in null_column_keys:
        var = col_var_map.get(key)
        if var is None:
            continue

        constraints: list[exp.Expression] = [
            exp.Is(this=var.copy(), expression=exp.Null()),
        ]
        constraints.extend(where_atoms)
        try:
            _ensure_check_constraint_vars(instance, col_var_map, table_vars, occurrence_tables)
            _add_database_constraints(
                constraints, instance, col_var_map, table_vars, occurrence_tables,
                include_same_batch_fk_targets=True,
            )
        except SchemaConstraintLoweringError:
            continue

        problem = Problem(
            constraints=constraints,
            equalities=list(equalities),
            variables=set(col_var_map.values()),
        )
        result = budget.solve(problem, dialect=dialect)

        if result.sat:
            occurrence_rows = {
                occurrence: _row_from_assignments(result.assignments, vs)
                for occurrence, vs in table_vars.items()
            }
            rows_by_table = _rows_by_physical_table(occurrence_rows, occurrence_tables)
            _try_create_rows(
                instance,
                rows_by_table,
                reason="null_rows",
                budget=budget,
            )
