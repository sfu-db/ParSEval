"""Speculative data seeding for SQL queries.

Given DDLs + SQL query + dialect, seed an Instance with data such that
the query would return at least one row --- without relying on real
database execution. Uses sqlglot AST analysis + the solver module.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence, Tuple

from sqlglot import exp, parse_one
from sqlglot.optimizer.scope import build_scope, traverse_scope

from parseval.domain.exceptions import DomainError
from parseval.instance import Instance
from parseval.instance.schema import table_key, normalize_identifier
from parseval.solver import Problem, SolverVar
from parseval.solver.api import Solver
from parseval.solver.partition import flatten_conjuncts
from parseval.generator.bounds import BmcBounds
from parseval.plan.rex import negate_predicate

logger = logging.getLogger(__name__)

MIN_ROWS_CAP = 1000


@dataclass
class RelationScopeInfo:
    alias_maps: dict[int, dict[exp.Identifier, exp.Table]] = field(default_factory=dict)
    physical_tables: set[exp.Table] = field(default_factory=set)


def _is_outer_join(join: exp.Join) -> bool:
    return join.args.get("side") in ("LEFT", "RIGHT", "FULL")


def _extract_limit_offset(tree: exp.Select) -> tuple[int | None, int | None]:
    limit_node = tree.args.get("limit")
    offset_node = tree.args.get("offset")

    limit_val: int | None = None
    if limit_node is not None:
        raw = limit_node.args.get("expression")
        if raw is not None:
            try:
                limit_val = int(raw.sql())
            except (TypeError, ValueError):
                pass

    offset_val: int | None = None
    if offset_node is not None:
        raw = offset_node.args.get("expression")
        if raw is not None:
            try:
                offset_val = int(raw.sql())
            except (TypeError, ValueError):
                pass

    return limit_val, offset_val


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
    except Exception:
        logger.warning("Failed to parse query")
        _seed_base_rows(instance)
        return instance

    if isinstance(tree, exp.With):
        tree = tree.this
    if not isinstance(tree, exp.Select):
        logger.warning("Only SELECT queries are supported, got %s", type(tree).__name__)
        _seed_base_rows(instance)
        return instance

    relation_info = _collect_relation_scope_info(tree, instance)
    alias_map = _build_alias_map(tree, instance, relation_info=relation_info)
    query_tables: set[exp.Table] = set(relation_info.physical_tables)
    if not alias_map:
        if query_tables:
            _seed_base_rows(instance, tables=query_tables)
            return instance
        logger.debug("No tables found in query")
        _seed_base_rows(instance)
        return instance

    col_var_map, table_vars = _collect_column_vars(tree, instance, alias_map)
    _ensure_check_constraint_vars(instance, col_var_map, table_vars)

    if not col_var_map:
        _seed_base_rows(instance, tables=query_tables)
        return instance

    # Extract WHERE atoms for negative-data generation
    where_atoms: list[exp.Expression] = []
    where_node = tree.args.get("where")
    if where_node is not None:
        for conj in flatten_conjuncts(where_node.this):
            if not _find_subquery_predicates(conj):
                replaced = _replace_columns_with_vars(conj, col_var_map, alias_map, instance)
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

    group_column_keys = _extract_group_column_keys(tree, alias_map, instance)
    agg_input_column_keys = _extract_aggregate_input_column_keys(tree, alias_map, instance)

    not_exists_not_in_items: list[dict[str, Any]] = []
    for drop_optional in (False, True):
        predicates, equalities, not_exists_not_in_items = _extract_predicates(
            tree, instance, alias_map, col_var_map, table_vars,
            relation_info=relation_info,
            drop_optional=drop_optional,
        )

        constraints: list[exp.Expression] = []
        for pred in predicates:
            constraints.extend(flatten_conjuncts(pred))

        if not constraints and not equalities and not not_exists_not_in_items:
            _seed_base_rows(instance, row_count=max(1, group_count, min_rows), tables=query_tables)
            return instance

        _add_database_constraints(constraints, instance, col_var_map, table_vars)

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
                instance, result.assignments, col_var_map, table_vars,
                constraints, list(equalities),
                group_count=group_count,
                min_rows=min_rows,
                dialect=dialect,
                group_key_column_keys=group_column_keys,
                aggregate_input_column_keys=agg_input_column_keys,
            )
            # Two-phase seeding for NOT EXISTS / NOT IN
            if not_exists_not_in_items:
                _seed_not_exists_not_in(
                    instance, not_exists_not_in_items, result.assignments,
                    col_var_map, table_vars, dialect,
                )

            # Negative data: seed rows that violate individual WHERE atoms
            if generate_negatives and where_atoms:
                _seed_negative_rows(
                    instance, where_atoms, col_var_map, table_vars,
                    list(equalities), dialect,
                )

            return instance

        if not_exists_not_in_items and not result.sat:
            break

    _seed_base_rows(instance, tables=query_tables)
    return instance


# ---------------------------------------------------------------------------
# Alias map
# ---------------------------------------------------------------------------


def _build_alias_map(
    tree: exp.Select,
    instance: Instance,
    *,
    relation_info: RelationScopeInfo | None = None,
) -> dict[exp.Identifier, exp.Table]:
    """Build ``{normalized_alias: resolved_Table}`` for physical sources only."""
    info = relation_info or _collect_relation_scope_info(tree, instance)
    return dict(info.alias_maps.get(id(tree), {}))


def _collect_relation_scope_info(tree: exp.Select, instance: Instance) -> RelationScopeInfo:
    """Classify scope sources, excluding CTE and derived-table aliases."""
    info = RelationScopeInfo()
    try:
        build_scope(tree)
        scopes = list(traverse_scope(tree))
    except Exception:
        logger.debug("Failed to build sqlglot scope", exc_info=True)
        scopes = []

    for scope in scopes:
        alias_map: dict[exp.Identifier, exp.Table] = {}
        for source_name, (_node, source) in scope.selected_sources.items():
            if not isinstance(source, exp.Table):
                continue
            try:
                resolved = instance.resolve_table(source)
            except KeyError:
                logger.debug("Skipping unresolved physical source %s", source.sql())
                continue
            alias = normalize_identifier(source_name, instance.dialect)
            alias_map[alias] = resolved
            info.physical_tables.add(resolved)
        info.alias_maps[id(scope.expression)] = alias_map

    return info


# ---------------------------------------------------------------------------
# Column resolution
# ---------------------------------------------------------------------------


def _resolve_column_table(
    col: exp.Column,
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> exp.Table | None:
    """Resolve a ``Column`` node to its ``exp.Table`` via alias map."""
    if col.table:
        key = normalize_identifier(col.table, instance.dialect)
        table = alias_map.get(key)
        if table is not None:
            return table
        return None

    candidates: list[exp.Table] = []
    for table in alias_map.values():
        try:
            instance.resolve_column(table, col.name)
            candidates.append(table)
        except KeyError:
            continue
    return candidates[0] if candidates else None


def _column_key(
    col: exp.Column,
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> tuple[str, str | None, str] | None:
    """Compute ``(resolved_table_name, alias_or_None, resolved_column_name)`` for a Column."""
    table_node = _resolve_column_table(col, alias_map, instance)
    if table_node is None:
        return None
    try:
        col_ident = instance.resolve_column(table_node, col.name)
    except KeyError:
        return None
    alias = normalize_identifier(col.table, instance.dialect) if col.table else None
    return (table_key(table_node), alias, col_ident.name)


# ---------------------------------------------------------------------------
# SolverVar collection
# ---------------------------------------------------------------------------


def _collect_column_vars(
    tree: exp.Select,
    instance: Instance,
    alias_map: dict[exp.Identifier, exp.Table],
) -> tuple[dict[tuple[str, str | None, str], SolverVar], dict[exp.Table, list[SolverVar]]]:
    """Walk the AST and create a SolverVar per distinct (table, alias, column).

    When the same table appears only once in the query (no self-join),
    qualified (``T.col``) and unqualified (``col``) references to the same
    column share a single SolverVar.  Self-joins keep separate vars per alias.
    """
    col_var_map: dict[tuple[str, str | None, str], SolverVar] = {}
    table_vars: dict[exp.Table, list[SolverVar]] = {}

    table_occurrences: dict[str, int] = {}
    for tn in alias_map.values():
        k = table_key(tn)
        table_occurrences[k] = table_occurrences.get(k, 0) + 1

    for col in tree.find_all(exp.Column):
        table_node = _resolve_column_table(col, alias_map, instance)
        if table_node is None:
            continue
        col_ident = instance.resolve_column(table_node, col.name)
        tkey = table_key(table_node)
        alias = normalize_identifier(col.table, instance.dialect) if col.table else None
        key = (tkey, alias, col_ident.name)
        if key in col_var_map:
            continue

        if table_occurrences.get(tkey, 0) == 1:
            existing = next(
                (ev for (ek, _ea, ec), ev in col_var_map.items()
                 if ek == tkey and ec == col_ident.name),
                None,
            )
            if existing is not None:
                col_var_map[key] = existing
                continue

        dtype = instance.get_column_type(table_node, col_ident)
        nullable = instance.nullable(table_node, col_ident)
        if not nullable:
            dtype = dtype.copy()
            dtype.args["nullable"] = False
        alias_part = f".{alias.name}" if alias else ""
        var = SolverVar(key=f"{tkey}{alias_part}.{col_ident.name}", dtype=dtype)
        col_var_map[key] = var
        table_vars.setdefault(table_node, []).append(var)

    return col_var_map, table_vars


def _extract_group_column_keys(
    tree: exp.Select,
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> list[tuple[str, str | None, str]]:
    """Extract column keys for GROUP BY columns that are simple column references."""
    group = tree.args.get("group")
    if not group or not group.expressions:
        return []
    keys: list[tuple[str, str | None, str]] = []
    for expr in group.expressions:
        if isinstance(expr, exp.Column):
            key = _column_key(expr, alias_map, instance)
            if key is not None:
                keys.append(key)
    return keys


def _extract_aggregate_input_column_keys(
    tree: exp.Select,
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> list[tuple[str, str | None, str]]:
    """Extract column keys for columns referenced inside aggregate functions."""
    seen: set[tuple[str, str | None, str]] = set()
    keys: list[tuple[str, str | None, str]] = []
    for agg_func in tree.find_all(exp.AggFunc):
        for col in agg_func.find_all(exp.Column):
            key = _column_key(col, alias_map, instance)
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
    alias_map: dict[exp.Identifier, exp.Table],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
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

    dialect = instance.dialect

    # -- WHERE (essential) --
    where = tree.args.get("where")
    if where is not None and not drop_optional:
        subqueries = _find_subquery_predicates(where.this)
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
                            instance, alias_map, col_var_map, table_vars, dialect,
                            relation_info=relation_info,
                        )
                        predicates.extend(sp)
                        equalities.extend(se)
                    elif kind in ("not_exists", "not_in"):
                        items = _collect_not_exists_not_in_items(
                            inner_select, kind, outer_expr,
                            instance, alias_map, col_var_map, table_vars,
                            relation_info=relation_info,
                        )
                        not_exists_not_in_items.extend(items)
            else:
                non_subquery_conjuncts.append(conj)

        # Add non-subquery WHERE conjuncts
        for conj in non_subquery_conjuncts:
            _add_predicate(predicates, conj, col_var_map, alias_map, instance)

    # -- JOIN ON (essential for INNER, no-op for outer/cross) --
    if not drop_optional:
        for join in (tree.args.get("joins") or []):
            if _is_outer_join(join):
                continue
            on = join.args.get("on")
            if on is None:
                continue
            for conjunct in flatten_conjuncts(on):
                eq = _extract_equality_pair(conjunct, col_var_map, alias_map, instance)
                if eq is not None:
                    equalities.append(eq)
                else:
                    _add_predicate(predicates, conjunct, col_var_map, alias_map, instance)

    # -- NOT NULL from schema (essential for NOT NULL columns) --
    if not drop_optional:
        _add_not_null_constraints(predicates, col_var_map, instance)

    # -- ORDER BY (non-null for sort columns) --
    order_by = tree.args.get("order")
    if order_by is not None and not drop_optional:
        for ordered in order_by.expressions:
            expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            if isinstance(expr, exp.Column):
                _add_not_null_for_column(predicates, expr, col_var_map, alias_map, instance)

    # -- HAVING (skippable) --
    having = tree.args.get("having")
    if having is not None and not drop_optional:
        _add_predicate(predicates, having.this, col_var_map, alias_map, instance)

    # -- CASE WHEN (skippable) --
    if not drop_optional:
        for case_expr in tree.find_all(exp.Case):
            for branch in (case_expr.args.get("ifs") or []):
                condition = branch.this
                _add_predicate(predicates, condition, col_var_map, alias_map, instance)

    return predicates, equalities, not_exists_not_in_items


def _add_predicate(
    predicates: list[exp.Expression],
    expr: exp.Expression,
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> None:
    """Replace columns with SolverVars and append to *predicates*."""
    transformed = _replace_columns_with_vars(expr, col_var_map, alias_map, instance)
    predicates.append(transformed)


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


def _collect_inner_vars(
    inner_select: exp.Select,
    instance: Instance,
    alias_map: dict[exp.Identifier, exp.Table],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
) -> None:
    """Collect SolverVars for inner-SELECT columns not already in *col_var_map*."""
    for col in list(inner_select.find_all(exp.Column)):
        table_node = _resolve_column_table(col, alias_map, instance)
        if table_node is None:
            continue
        col_ident = instance.resolve_column(table_node, col.name)
        alias = normalize_identifier(col.table, instance.dialect) if col.table else None
        key = (table_key(table_node), alias, col_ident.name)
        if key in col_var_map:
            continue
        dtype = instance.get_column_type(table_node, col_ident)
        nullable = instance.nullable(table_node, col_ident)
        if not nullable:
            dtype = dtype.copy()
            dtype.args["nullable"] = False
        alias_part = f".{alias.name}" if alias else ""
        var = SolverVar(key=f"{key[0]}{alias_part}.{key[2]}", dtype=dtype)
        col_var_map[key] = var
        table_vars.setdefault(table_node, []).append(var)


def _correlation_pairs(
    inner_select: exp.Select,
    outer_alias_map: dict[exp.Identifier, exp.Table],
    inner_alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> list[tuple[exp.Column, exp.Column]]:
    """Find correlation column pairs between outer and inner queries.

    Returns ``[(outer_col, inner_col), ...]`` where *inner_col* is a Column
    inside the inner WHERE that resolves to an outer table.
    """
    pairs: list[tuple[exp.Column, exp.Column]] = []
    inner_where = inner_select.args.get("where")
    if inner_where is None:
        return pairs
    for col in list(inner_where.this.find_all(exp.Column)):
        outer_table = _resolve_column_table(col, outer_alias_map, instance)
        if outer_table is not None:
            # This column is a correlated reference to the outer query
            # Find the corresponding inner column(s) it's compared to
            pairs.append((col, col))
    return pairs


def _extract_correlation_equalties(
    inner_where_expr: exp.Expression,
    outer_alias_map: dict[exp.Identifier, exp.Table],
    inner_alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> list[tuple[exp.Expression, exp.Expression]]:
    """Extract equi-join correlations: ``outer.col = inner.col`` pairs.

    Returns ``[(outer_expr, inner_expr), ...]`` from equality predicates
    that cross outer and inner alias maps.
    """
    correlations: list[tuple[exp.Expression, exp.Expression]] = []
    combined = {**outer_alias_map, **inner_alias_map}
    for eq in list(inner_where_expr.find_all(exp.EQ)):
        left_table = _resolve_column_table(eq.this, outer_alias_map, instance) if isinstance(eq.this, exp.Column) else None
        right_table = _resolve_column_table(eq.expression, outer_alias_map, instance) if isinstance(eq.expression, exp.Column) else None
        left_inner = _resolve_column_table(eq.this, inner_alias_map, instance) if isinstance(eq.this, exp.Column) else None
        right_inner = _resolve_column_table(eq.expression, inner_alias_map, instance) if isinstance(eq.expression, exp.Column) else None

        left_is_outer = left_table is not None
        right_is_outer = right_table is not None
        left_is_inner = left_inner is not None and not left_is_outer
        right_is_inner = right_inner is not None and not right_is_outer

        if left_is_outer and right_is_inner:
            correlations.append((eq.this, eq.expression))
        elif right_is_outer and left_is_inner:
            correlations.append((eq.expression, eq.this))
    return correlations


def _process_exists_in_subquery(
    inner_select: exp.Select,
    kind: str,
    outer_expr: exp.Expression | None,
    instance: Instance,
    outer_alias_map: dict[exp.Identifier, exp.Table],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
    dialect: str,
    *,
    relation_info: RelationScopeInfo | None = None,
) -> tuple[list[exp.Expression], list[tuple[SolverVar, SolverVar]]]:
    """Process EXISTS / IN (subquery).

    Returns ``(predicates, equalities)`` the solver must satisfy for the
    EXISTS/IN condition to produce at least one matching row.
    """
    inner_alias_map = _build_alias_map(
        inner_select, instance, relation_info=relation_info,
    )
    if not inner_alias_map:
        return [], []

    combined_map: dict[exp.Identifier, exp.Table] = {**outer_alias_map, **inner_alias_map}

    # Collect SolverVars for inner columns
    _collect_inner_vars(inner_select, instance, combined_map, col_var_map, table_vars)

    inner_predicates: list[exp.Expression] = []
    inner_equalities: list[tuple[SolverVar, SolverVar]] = []

    # Extract inner WHERE predicates (correlation equalities feed into solver)
    inner_where = inner_select.args.get("where")
    if inner_where is not None:
        # Parse correlation equalities and add as join equalities
        correlations = _extract_correlation_equalties(
            inner_where.this, outer_alias_map, inner_alias_map, instance,
        )
        remaining_conjuncts: list[exp.Expression] = []
        for conj in flatten_conjuncts(inner_where.this):
            is_correlation = False
            for outer_col, inner_col in correlations:
                if (isinstance(conj, exp.EQ)
                    and ((conj.this is outer_col and conj.expression is inner_col)
                         or (conj.this is inner_col and conj.expression is outer_col))):
                    # This is a correlation equality — add as SolverVar equality
                    outer_key = _column_key(outer_col, combined_map, instance)
                    inner_key = _column_key(inner_col, combined_map, instance)
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
            _add_predicate(inner_predicates, conj, col_var_map, combined_map, instance)

    # For IN, add equality: outer_expr = subquery SELECT expression
    if kind == "in" and outer_expr is not None:
        inner_projections = inner_select.args.get("expressions") or []
        if inner_projections:
            inner_proj = inner_projections[0]
            outer_rewritten = _replace_columns_with_vars(
                outer_expr, col_var_map, combined_map, instance,
            )
            inner_rewritten = _replace_columns_with_vars(
                inner_proj, col_var_map, combined_map, instance,
            )
            if isinstance(outer_rewritten, SolverVar) and isinstance(inner_rewritten, SolverVar):
                inner_equalities.append((outer_rewritten, inner_rewritten))
            else:
                inner_predicates.append(
                    exp.EQ(this=outer_rewritten, expression=inner_rewritten),
                )

    return inner_predicates, inner_equalities


def _collect_not_exists_not_in_items(
    inner_select: exp.Select,
    kind: str,
    outer_expr: exp.Expression | None,
    instance: Instance,
    outer_alias_map: dict[exp.Identifier, exp.Table],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
    *,
    relation_info: RelationScopeInfo | None = None,
) -> list[dict[str, Any]]:
    """Collect NOT EXISTS / NOT IN info for two-phase seeding.

    Returns a list of dicts with keys:
    - ``"inner_select"``: the inner SELECT
    - ``"inner_alias_map"``: alias map for inner tables
    - ``"inner_tables"``: set of inner tables
    - ``"correlation_cols"``: list of ``(outer_key, inner_key)`` pairs
    - ``"outer_on_exprs"``: outer side of correlation (column refs)
    - ``"inner_on_exprs"``: inner side of correlation (column refs)
    - ``"outer_in_expr"``: for NOT IN, the outer expression
    """
    inner_alias_map = _build_alias_map(
        inner_select, instance, relation_info=relation_info,
    )
    if not inner_alias_map:
        return []

    combined_map = {**outer_alias_map, **inner_alias_map}
    _collect_inner_vars(inner_select, instance, combined_map, col_var_map, table_vars)

    inner_where = inner_select.args.get("where")
    correlations: list[tuple[exp.Column, exp.Column]] = []
    if inner_where is not None:
        for outer_col, inner_col in _extract_correlation_equalties(
            inner_where.this, outer_alias_map, inner_alias_map, instance,
        ):
            correlations.append((outer_col, inner_col))

    outer_on_exprs: list[exp.Expression] = []
    inner_on_exprs: list[exp.Expression] = []
    correlation_cols: list[tuple[tuple[str, str | None, str], tuple[str, str | None, str]]] = []
    for outer_col, inner_col in correlations:
        outer_k = _column_key(outer_col, combined_map, instance)
        inner_k = _column_key(inner_col, combined_map, instance)
        if outer_k and inner_k:
            correlation_cols.append((outer_k, inner_k))
            outer_on_exprs.append(outer_col)
            inner_on_exprs.append(inner_col)

    inner_tables: set[exp.Table] = set()
    for table_node in inner_alias_map.values():
        if table_key(table_node) not in {table_key(t) for t in outer_alias_map.values()}:
            inner_tables.add(table_node)

    if not correlation_cols and kind == "not_exists":
        return []

    return [{
        "kind": kind,
        "inner_select": inner_select,
        "inner_alias_map": inner_alias_map,
        "inner_tables": inner_tables,
        "correlation_cols": correlation_cols,
        "outer_on_exprs": outer_on_exprs,
        "inner_on_exprs": inner_on_exprs,
        "outer_in_expr": outer_expr,
    }]


def _seed_not_exists_not_in(
    instance: Instance,
    items: list[dict[str, Any]],
    assignments: dict[SolverVar, Any],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
    dialect: str,
) -> None:
    """Two-phase seeding for NOT EXISTS / NOT IN.

    For each item, seed inner-table rows with values that
    deliberately do NOT match the already-seeded outer correlation values.
    """
    for item in items:
        kind = item["kind"]
        inner_alias_map = item["inner_alias_map"]
        inner_tables = item["inner_tables"]
        correlation_cols = item["correlation_cols"]
        outer_on_exprs = item["outer_on_exprs"]
        inner_on_exprs = item["inner_on_exprs"]
        outer_in_expr = item["outer_in_expr"]

        if kind == "not_exists" and not correlation_cols:
            continue

        inner_select = item["inner_select"]
        inner_where = inner_select.args.get("where")

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
                outer_in_expr, col_var_map, inner_alias_map, instance,
            )
            if isinstance(outer_rewritten, SolverVar):
                val = assignments.get(outer_rewritten)
                if val is not None:
                    outer_values = [val]
                    inner_projections = inner_select.args.get("expressions") or []
                    if inner_projections:
                        inner_proj = inner_projections[0]
                        inner_k = _column_key(inner_proj, inner_alias_map, instance)
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

        # Also add inner WHERE predicates as constraints
        combined_map = {**{k: v for k, v in zip(
            [exp.to_identifier("_outer")], [exp.table_("_outer")]
        )}, **inner_alias_map}
        if inner_where is not None:
            for table_node in inner_alias_map.values():
                for col in list(inner_where.this.find_all(exp.Column)):
                    if _resolve_column_table(col, inner_alias_map, instance) is not None:
                        continue

        if not non_matching_constraints and kind != "not_in":
            continue

        # Use solver to find a non-matching row for each inner table
        for table_node in inner_alias_map.values():
            inner_cols = instance.column_names(table_node)
            solver_vars_map: dict[str, SolverVar] = {}
            solver_constraints: list[exp.Expression] = []

            for col_name in inner_cols:
                alias = exp.to_identifier(list(inner_alias_map.keys())[0].name) if inner_alias_map else None
                col_ident = instance.resolve_column(table_node, col_name)
                alias_normalized = normalize_identifier(alias, dialect) if alias else None
                key = (table_key(table_node), alias_normalized, col_ident.name)
                sv = col_var_map.get(key)
                if sv is not None:
                    solver_vars_map[col_name] = sv

            solver_constraints.extend(non_matching_constraints)

            problem = Problem(
                constraints=solver_constraints,
                equalities=[],
                variables=set(solver_vars_map.values()),
            )
            solver = Solver(dialect=dialect)
            result = solver.solve(problem)

            if result.sat:
                row_data: dict[exp.Identifier, Any] = {}
                for col_name, sv in solver_vars_map.items():
                    if sv in result.assignments:
                        try:
                            col_ident = instance.resolve_column(table_node, col_name)
                            row_data[col_ident] = result.assignments[sv]
                        except KeyError:
                            continue
                if row_data:
                    _try_create_rows(
                        instance,
                        {table_node: [row_data]},
                        reason="not_exists_not_in_solver",
                    )
            else:
                # Fallback heuristic: use non-matching values
                fallback_row: dict[exp.Identifier, Any] = {}
                for col_name, sv in solver_vars_map.items():
                    try:
                        col_ident = instance.resolve_column(table_node, col_name)
                        if outer_values:
                            ov = outer_values[0]
                            if isinstance(ov, (int, float)):
                                fallback_row[col_ident] = ov + 1
                            else:
                                fallback_row[col_ident] = f"no_match_{ov}"
                        else:
                            fallback_row[col_ident] = 1
                    except KeyError:
                        continue
                if fallback_row:
                    _try_create_rows(
                        instance,
                        {table_node: [fallback_row]},
                        reason="not_exists_not_in_fallback",
                    )


def _extract_equality_pair(
    expr: exp.Expression,
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> tuple[SolverVar, SolverVar] | None:
    """If *expr* is ``col_a = col_b``, return the SolverVar pair."""
    if not isinstance(expr, exp.EQ):
        return None
    left_key = _column_key_from_node(expr.this, alias_map, instance)
    right_key = _column_key_from_node(expr.expression, alias_map, instance)
    if left_key is None or right_key is None:
        return None
    left_var = col_var_map.get(left_key)
    right_var = col_var_map.get(right_key)
    if left_var is not None and right_var is not None:
        return (left_var, right_var)
    return None


def _column_key_from_node(
    node: exp.Expression,
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> tuple[str, str | None, str] | None:
    """Extract column key from a node that may be a Column."""
    if isinstance(node, exp.Column):
        return _column_key(node, alias_map, instance)
    return None


def _add_not_null_constraints(
    predicates: list[exp.Expression],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    instance: Instance,
) -> None:
    """Add IS NOT NULL for every column that is NOT NULL in the schema."""
    for (_table_name, _alias, _col_name), var in col_var_map.items():
        if var.dtype.args.get("nullable") is False:
            predicates.append(exp.Not(this=exp.Is(this=var, expression=exp.Null())))


def _add_not_null_for_column(
    predicates: list[exp.Expression],
    col: exp.Column,
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> None:
    """Add a single IS NOT NULL constraint if the column has a SolverVar."""
    key = _column_key(col, alias_map, instance)
    if key is not None and key in col_var_map:
        var = col_var_map[key]
        predicates.append(exp.Not(this=exp.Is(this=var, expression=exp.Null())))


def _ensure_check_constraint_vars(
    instance: Instance,
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
) -> None:
    """Add internal solver vars for supported CHECK columns missing from the query."""
    for table_node in list(table_vars):
        tkey = table_key(table_node)
        table_schema = instance.database_constraints(table_node)
        for check in table_schema.checks:
            if not check.supported:
                continue
            for col_ident in check.referenced_columns:
                if any(tk == tkey and cn == col_ident.name for tk, _alias, cn in col_var_map):
                    continue
                dtype = instance.get_column_type(table_node, col_ident)
                if not instance.nullable(table_node, col_ident):
                    dtype = dtype.copy()
                    dtype.args["nullable"] = False
                key = (tkey, None, col_ident.name)
                var = SolverVar(
                    key=f"{tkey}._check.{col_ident.name}",
                    dtype=dtype,
                    meta={"internal": "check"},
                )
                col_var_map[key] = var
                table_vars.setdefault(table_node, []).append(var)


# ---------------------------------------------------------------------------
# Column to SolverVar replacement
# ---------------------------------------------------------------------------


def _replace_columns_with_vars(
    expr: exp.Expression,
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    alias_map: dict[exp.Identifier, exp.Table],
    instance: Instance,
) -> exp.Expression:
    """Return a copy of *expr* with ``exp.Column`` nodes replaced by SolverVars."""

    def replacer(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            key = _column_key(node, alias_map, instance)
            if key is not None and key in col_var_map:
                return col_var_map[key].copy()
        return node

    return expr.copy().transform(replacer)


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def _row_from_assignments(
    instance: Instance,
    assignments: dict[SolverVar, Any],
    vars: list[SolverVar],
    table_node: exp.Table,
) -> dict[exp.Identifier, Any]:
    row: dict[exp.Identifier, Any] = {}
    for var in vars:
        if var not in assignments:
            continue
        col_name = var.var_key.split(".")[-1]
        try:
            col_ident = instance.resolve_column(table_node, col_name)
        except KeyError:
            continue
        row[col_ident] = assignments[var]
    return row


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


def _seed_from_assignments(
    instance: Instance,
    first_assignments: dict[SolverVar, Any],
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
    constraints: list[exp.Expression],
    equalities: list[tuple[SolverVar, SolverVar]],
    *,
    group_count: int = 1,
    min_rows: int = 1,
    dialect: str = "sqlite",
    group_key_column_keys: Sequence[tuple[str, str | None, str]] = (),
    aggregate_input_column_keys: Sequence[tuple[str, str | None, str]] = (),
) -> None:
    """Seed the instance with solver assignments, re-solving for extra rows.

    When *group_key_column_keys* is provided, a portion of extra rows are
    generated with duplicate group-key values (multiple rows per group).
    When *aggregate_input_column_keys* is provided, a portion of extra rows
    set nullable aggregate-input columns to NULL.
    """
    total_rows = max(1, group_count, min_rows)
    def _build_rows_by_table(assignments: dict[SolverVar, Any]) -> dict[exp.Table, list[dict]]:
        return {
            t: [_row_from_assignments(instance, assignments, vs, t)]
            for t, vs in table_vars.items()
        }

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
    first_row_group_values: dict[tuple[str, str | None, str], Any] = {}
    if group_key_column_keys:
        for gkey in group_key_column_keys:
            tk, alias, cn = gkey
            for tn, vs in table_vars.items():
                if table_key(tn) == tk:
                    existing = instance.get_rows(tn)
                    if existing:
                        col_ident = instance.resolve_column(tn, cn)
                        val = existing[0].column_values.get(col_ident)
                        if val is not None and hasattr(val, 'concrete'):
                            val = val.concrete
                        first_row_group_values[gkey] = val
                    break
    can_duplicate = bool(group_key_column_keys) and len(first_row_group_values) == len(group_key_column_keys)

    # Pre-compute nullable aggregate-input column keys for the NULL strategy
    nullable_agg_keys: list[tuple[str, str | None, str]] = []
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
        new_col_var_map: dict[tuple[str, str | None, str], SolverVar] = {}
        for key, var in col_var_map.items():
            new_var = SolverVar(key=f"{var.var_key}{suffix}", dtype=var.dtype)
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

        _add_database_constraints(new_constraints, instance, new_col_var_map, table_vars)

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

        extra_rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {}
        for table_node, vs in table_vars.items():
            row: dict[exp.Identifier, Any] = {}
            for var in vs:
                new_var_key = f"{var.var_key}{suffix}"
                new_var = next((v for v in result.assignments if v.var_key == new_var_key), None)
                if new_var is None or new_var not in result.assignments:
                    continue
                col_name = var.var_key.split(".")[-1]
                try:
                    col_ident = instance.resolve_column(table_node, col_name)
                except KeyError:
                    continue
                row[col_ident] = result.assignments[new_var]
            extra_rows_by_table[table_node] = [row]
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


def _unique_non_collision_constraint(
    sv_map: dict[str, SolverVar],
    names: tuple[str, ...],
    existing: list[Any],
) -> exp.Expression:
    atoms = [
        exp.NEQ(this=sv_map[name], expression=_literal_for_value(value))
        for name, value in zip(names, existing)
    ]
    if not atoms:
        return exp.EQ(this=exp.Literal.number("1"), expression=exp.Literal.number("0"))
    if len(atoms) == 1:
        return atoms[0]
    expr = atoms[0]
    for atom in atoms[1:]:
        expr = exp.Or(this=expr, expression=atom)
    return expr


def _add_database_constraints(
    constraints: list[exp.Expression],
    instance: Instance,
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
) -> None:
    """Add uniqueness, FK, and CHECK constraints for all tables in *table_vars*.

    Follows the pattern of ``_database_constraints_for_solver`` in ``operator.py``.
    Unique-group and FK constraints add ``IN`` / ``NEQ`` predicates against
    existing rows; CHECK constraints embed the raw check expression with
    ``exp.Column`` nodes replaced by matching ``SolverVar`` nodes.

    Naturally a no-op for empty tables (no existing/parent rows yet), so safe
    to call for the first solve.
    """
    for table_node in table_vars:
        existing_rows = instance.get_rows(table_node)
        tkey = table_key(table_node)
        table_schema = instance.database_constraints(table_node)

        # -- Unique-group constraints (PK + all UNIQUE) --
        if existing_rows:
            for group in table_schema.uniqueness_groups():
                names = tuple(c.name for c in group)
                sv_map: dict[str, SolverVar] = {}
                for c in group:
                    for (tk, _alias, cn), var in col_var_map.items():
                        if tk == tkey and cn == c.name:
                            sv_map[c.name] = var
                            break
                if set(names) > sv_map.keys():
                    continue
                for row in existing_rows:
                    vd = Instance._row_value_dict(row)
                    vals = [vd.get(c) for c in group]
                    if all(v is not None for v in vals):
                        constraints.append(
                            _unique_non_collision_constraint(sv_map, names, vals)
                        )

        # -- FK constraints (single-column FKs only) --
        for fk in instance.get_foreign_keys(table_node):
            if len(fk.source_columns) != 1:
                continue
            fk_sv: SolverVar | None = None
            for (tk, _alias, cn), var in col_var_map.items():
                if tk == tkey and cn == fk.source_columns[0].name:
                    fk_sv = var
                    break
            if fk_sv is None:
                continue

            # Skip FK constraint when the FK source column belongs to a
            # unique group (PK or UNIQUE). The Instance-level FK handling
            # (_ensure_fk_parents) creates matching parent rows for each
            # unique FK value, avoiding contradiction with uniqueness.
            fk_col_name = fk.source_columns[0].name
            if any(
                any(c.name == fk_col_name for c in group)
                for group in table_schema.uniqueness_groups()
            ):
                continue

            target_table = instance.resolve_table(fk.target_table)
            target_values: list[Any] = []
            for parent_row in instance.get_rows(target_table):
                vd = Instance._row_value_dict(parent_row)
                value = vd.get(fk.target_columns[0])
                if value is not None:
                    target_values.append(value)
            if target_values:
                constraints.append(
                    exp.In(
                        this=fk_sv,
                        expressions=[
                            _literal_for_value(v)
                            for v in dict.fromkeys(target_values)
                        ],
                    )
                )

        # -- CHECK constraints --
        for check in table_schema.checks:
            if not check.supported:
                continue
            ref_names = {c.name for c in check.referenced_columns}
            if not ref_names:
                continue
            col_to_sv: dict[str, SolverVar] = {}
            for col_name in ref_names:
                for (tk, _alias, cn), var in col_var_map.items():
                    if tk == tkey and cn == col_name:
                        col_to_sv[col_name] = var
                        break
                if col_name not in col_to_sv:
                    break
            else:
                rewritten = deepcopy(check.expression)
                for col_node in list(rewritten.find_all(exp.Column)):
                    if isinstance(col_node.this, exp.Identifier) and col_node.this.name in col_to_sv:
                        col_node.replace(col_to_sv[col_node.this.name])
                constraints.append(rewritten)


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
    col_var_map: dict[tuple[str, str | None, str], SolverVar],
    table_vars: dict[exp.Table, list[SolverVar]],
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
        _add_database_constraints(constraints, instance, col_var_map, table_vars)

        problem = Problem(
            constraints=constraints,
            equalities=list(equalities),
            variables=set(col_var_map.values()),
        )
        solver = Solver(dialect=dialect)
        result = solver.solve(problem)

        if result.sat:
            rows_by_table: dict[exp.Table, list[dict[exp.Identifier, Any]]] = {
                t: [_row_from_assignments(instance, result.assignments, vs, t)]
                for t, vs in table_vars.items()
            }
            _try_create_rows(instance, rows_by_table, reason="negative_rows")
