"""Speculative data seeding for SQL queries.

Given DDLs + SQL query + dialect, seed an Instance with data such that
the query would return at least one row --- without relying on real
database execution. Uses sqlglot AST analysis + the solver module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from sqlglot import exp, parse_one
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import build_scope, traverse_scope

from parseval.domain.exceptions import DomainError
from parseval.generator.schema_constraints import (
    SchemaConstraintLoweringError,
    schema_constraints_for_solver_row,
)
from parseval.generator.helper import same_identifier
from parseval.instance import Instance
from parseval.instance.schema import table_key, normalize_identifier
from parseval.solver import Problem, SolverVar
from parseval.solver.api import Solver
from parseval.solver.partition import flatten_conjuncts
from parseval.generator.bounds import BmcBounds
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


def _int_expression_value(node: exp.Expression | None) -> int | None:
    if node is None:
        return None
    try:
        return int(node.sql())
    except (TypeError, ValueError):
        return None


def _extract_limit_offset(tree: exp.Select) -> tuple[int | None, int | None]:
    limit_node = tree.args.get("limit")
    offset_node = tree.args.get("offset")

    limit_val: int | None = None
    if limit_node is not None:
        limit_val = _int_expression_value(limit_node.args.get("expression"))

    offset_val: int | None = None
    if offset_node is not None:
        offset_val = _int_expression_value(offset_node.args.get("expression"))

    return limit_val, offset_val


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
    bounds: BmcBounds | None = None,
    generate_negatives: bool = True,
) -> Instance | None:
    """Seed data for *query* so it returns at least one row.

    When *generate_negatives* is True (default), also seed additional
    rows that violate individual WHERE atoms, providing both matching
    and non-matching data.

    Returns an ``Instance`` with seeded rows, or a base-row-only
    ``Instance`` if the query is unsatisfiable within the solver's
    capabilities.
    """
    instance = Instance(ddls, name="speculative", dialect=dialect)
    bounds = bounds or BmcBounds()

    try:
        tree = parse_one(query, dialect=dialect)
    except SqlglotError:
        logger.warning("Failed to parse query")
        _seed_base_rows(instance)
        return instance

    if isinstance(tree, exp.With):
        tree = tree.this

    if isinstance(tree, exp.Union):
        _speculate_set_operation(instance, tree, bounds, generate_negatives, dialect)
        return instance

    if not isinstance(tree, exp.Select):
        logger.warning("Only SELECT queries are supported, got %s", type(tree).__name__)
        _seed_base_rows(instance)
        return instance

    if not _speculate_select(instance, tree, bounds, generate_negatives, dialect):
        relation_info = _collect_relation_scope_info(tree, instance)
        query_tables: set[exp.Table] = set(relation_info.physical_tables)
        if query_tables:
            _seed_base_rows(instance, tables=query_tables)
        else:
            logger.debug("No tables found in query")
            _seed_base_rows(instance)
    return instance


def _speculate_set_operation(
    instance: Instance,
    tree: exp.Union,
    bounds: BmcBounds,
    generate_negatives: bool,
    dialect: str,
) -> None:
    """Seed data for a UNION/INTERSECT/EXCEPT set operation.

    Processes each leaf SELECT independently, accumulating rows in
    *instance*.  Falls back to base rows only if all branches fail.
    """
    leaves = _collect_operation_leaves(tree)
    any_solved = False
    for leaf in leaves:
        if _speculate_select(instance, leaf, bounds, generate_negatives, dialect):
            any_solved = True

    if not any_solved:
        all_tables: set[exp.Table] = set()
        for leaf in leaves:
            info = _collect_relation_scope_info(leaf, instance)
            all_tables.update(info.physical_tables)
        if all_tables:
            _seed_base_rows(instance, tables=all_tables)
        else:
            _seed_base_rows(instance)


def _speculate_select(
    instance: Instance,
    tree: exp.Select,
    bounds: BmcBounds,
    generate_negatives: bool,
    dialect: str,
) -> bool:
    """Seed data for a single SELECT.

    Returns True if a solver solution was found, False if UNSAT (caller
    should seed base rows as fallback).
    """
    relation_info = _collect_relation_scope_info(tree, instance)
    query_tables: set[exp.Table] = set(relation_info.physical_tables)

    col_var_map, table_vars, occurrence_tables = _collect_column_vars(
        tree, instance, relation_info,
    )

    if not col_var_map:
        _seed_base_rows(instance, tables=query_tables)
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

    limit_val, offset_val = _extract_limit_offset(tree)
    min_rows = 3

    group = tree.args.get("group")
    has_group_by = group is not None and group.expressions
    group_count = max(int(getattr(bounds, "groups", 1) or 1), 1)
    if has_group_by and group_count < 6:
        group_count = max(6, len(group.expressions) * 4)
    if limit_val is not None and offset_val is not None:
        min_rows = max(3, min(offset_val + limit_val, MIN_ROWS_CAP))
    elif limit_val is not None:
        min_rows = max(3, min(limit_val, MIN_ROWS_CAP))
    elif offset_val is not None:
        min_rows = max(3, min(offset_val + 1, MIN_ROWS_CAP))

    group_column_keys = _extract_group_column_keys(tree, relation_info, instance)
    agg_input_column_keys = _extract_aggregate_input_column_keys(tree, relation_info, instance)

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

        if not constraints and not equalities and not not_exists_not_in_items:
            _seed_base_rows(
                instance,
                row_count=max(1, group_count, min_rows),
                tables=query_tables,
            )
            try:
                _ensure_check_constraint_vars(instance, col_var_map, table_vars, occurrence_tables)
                _add_database_constraints(constraints, instance, col_var_map, table_vars, occurrence_tables)
            except SchemaConstraintLoweringError:
                return True
            _seed_case_arm_rows(
                instance, tree, col_var_map, table_vars, occurrence_tables,
                constraints, [],
                relation_info=relation_info,
                dialect=dialect,
            )
            return True

        try:
            _ensure_check_constraint_vars(instance, col_var_map, table_vars, occurrence_tables)
            _add_database_constraints(constraints, instance, col_var_map, table_vars, occurrence_tables)
        except SchemaConstraintLoweringError:
            continue

        variables = set(col_var_map.values())
        problem = Problem(
            constraints=constraints,
            equalities=list(equalities),
            variables=variables,
        )
        solver = Solver(dialect=dialect)
        result = solver.solve(problem)

        if result.sat:
            _seed_from_assignments(
                instance, result.assignments, col_var_map, table_vars, occurrence_tables,
                constraints, list(equalities),
                group_count=group_count,
                min_rows=min_rows,
                dialect=dialect,
                group_key_column_keys=group_column_keys,
                aggregate_input_column_keys=agg_input_column_keys,
            )
            _seed_case_arm_rows(
                instance, tree, col_var_map, table_vars, occurrence_tables,
                constraints, list(equalities),
                relation_info=relation_info,
                dialect=dialect,
            )
            if not_exists_not_in_items:
                _seed_not_exists_not_in(
                    instance, not_exists_not_in_items, result.assignments,
                    col_var_map, table_vars, occurrence_tables, dialect,
                )
            if generate_negatives and where_atoms:
                _seed_negative_rows(
                    instance, where_atoms, col_var_map, table_vars, occurrence_tables,
                    list(equalities), dialect,
                )
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
    if having is not None and not drop_optional:
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
                )
            except SchemaConstraintLoweringError:
                continue

            problem = Problem(
                constraints=solver_constraints,
                equalities=[],
                variables={var for vars in table_subset.values() for var in vars},
            )
            solver = Solver(dialect=dialect)
            result = solver.solve(problem)

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
) -> bool:
    token = instance.checkpoint()
    try:
        instance.create_rows(_reserve_unique_values(instance, rows_by_table))
        return True
    except (DomainError, KeyError) as exc:
        instance.rollback(token)
        logger.info("speculate_generation_skipped:%s:%s", reason, exc)
        return False


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
    dialect: str = "sqlite",
) -> None:
    arm_predicate_sets = _collect_case_arm_predicate_sets(tree)
    if not arm_predicate_sets:
        return

    variables = set(col_var_map.values())
    solver = Solver(dialect=dialect)

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
            result = solver.solve(problem)
            if not result.sat:
                continue

            occurrence_rows = {
                occurrence: _row_from_assignments(result.assignments, vs)
                for occurrence, vs in table_vars.items()
            }
            rows_by_table = _rows_by_physical_table(
                occurrence_rows, occurrence_tables,
            )
            _try_create_rows(
                instance,
                rows_by_table,
                reason="case_arm_solver_assignment",
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
    group_count: int = 1,
    min_rows: int = 1,
    dialect: str = "sqlite",
    group_key_column_keys: Sequence[ColumnKey] = (),
    aggregate_input_column_keys: Sequence[ColumnKey] = (),
) -> None:
    """Seed the instance with solver assignments, re-solving for extra rows.

    When *group_key_column_keys* is provided, a portion of extra rows are
    generated with duplicate group-key values (multiple rows per group).
    When *aggregate_input_column_keys* is provided, a portion of extra rows
    set nullable aggregate-input columns to NULL.
    """
    total_rows = max(1, group_count, min_rows)
    def _build_rows_by_table(assignments: dict[SolverVar, Any]) -> dict[exp.Table, list[dict]]:
        occurrence_rows = {
            occurrence: _row_from_assignments(assignments, vs)
            for occurrence, vs in table_vars.items()
        }
        return _rows_by_physical_table(occurrence_rows, occurrence_tables)

    if total_rows <= 1:
        _try_create_rows(
            instance,
            _build_rows_by_table(first_assignments),
            reason="solver_assignment",
        )
        return

    if not _try_create_rows(
        instance,
        _build_rows_by_table(first_assignments),
        reason="solver_assignment",
    ):
        return

    # Read first-row group-key values from the instance (the instance fills
    # in random values even when solver assignments are empty).
    first_row_group_values: dict[ColumnKey, Any] = {}
    if group_key_column_keys:
        for gkey in group_key_column_keys:
            table_node, _alias, col_ident = gkey
            for occurrence in table_vars:
                if occurrence_tables[occurrence] == table_node:
                    existing = instance.get_rows(table_node)
                    if existing:
                        val = existing[0].column_values.get(col_ident)
                        if val is not None and hasattr(val, 'concrete'):
                            val = val.concrete
                        first_row_group_values[gkey] = val
                    break
    can_duplicate = bool(group_key_column_keys) and len(first_row_group_values) == len(group_key_column_keys)

    # Pre-compute nullable aggregate-input column keys for the NULL strategy
    nullable_agg_keys: list[ColumnKey] = []
    if aggregate_input_column_keys:
        for akey in aggregate_input_column_keys:
            var = col_var_map.get(akey)
            if var is not None and var.dtype.args.get("nullable") is not False:
                nullable_agg_keys.append(akey)
    can_null = bool(nullable_agg_keys)

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
            for gkey, value in first_row_group_values.items():
                new_var = new_col_var_map.get(gkey)
                if new_var is not None:
                    new_constraints.append(
                        exp.EQ(this=new_var, expression=_literal_for_value(value))
                    )
        elif strategy == 2:
            for akey in nullable_agg_keys:
                new_var = new_col_var_map.get(akey)
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
        solver = Solver(dialect=dialect)
        result = solver.solve(problem)

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
        ):
            extra_idx += 1
            continue
        extra_idx += 1


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


def _seed_base_rows(
    instance: Instance,
    *,
    row_count: int = 1,
    tables: set[exp.Table] | None = None,
) -> None:
    """Create *row_count* base rows per table (query tables only if *tables* is set)."""
    rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
    for table_node in instance.schema.fk_safe_table_order():
        if tables is not None and table_node not in tables:
            continue
        rows_by_table[table_node] = [{} for _ in range(row_count)]
    _try_create_rows(instance, rows_by_table, reason="base_rows")


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
) -> None:
    """For each WHERE atom, seed a row that violates just that atom.

    For ``A AND B AND C``, this generates rows for ``NOT A AND B AND C``,
    ``A AND NOT B AND C``, and ``A AND B AND NOT C`` (where possible).
    """
    for i, atom in enumerate(where_atoms):
        negated = negate_predicate(atom)
        constraints: list[exp.Expression] = [negated]
        constraints.extend(
            a for j, a in enumerate(where_atoms) if j != i
        )
        try:
            _ensure_check_constraint_vars(instance, col_var_map, table_vars, occurrence_tables)
            _add_database_constraints(constraints, instance, col_var_map, table_vars, occurrence_tables)
        except SchemaConstraintLoweringError:
            continue

        problem = Problem(
            constraints=constraints,
            equalities=list(equalities),
            variables=set(col_var_map.values()),
        )
        solver = Solver(dialect=dialect)
        result = solver.solve(problem)

        if result.sat:
            occurrence_rows = {
                occurrence: _row_from_assignments(result.assignments, vs)
                for occurrence, vs in table_vars.items()
            }
            rows_by_table = _rows_by_physical_table(occurrence_rows, occurrence_tables)
            _try_create_rows(instance, rows_by_table, reason="negative_rows")
