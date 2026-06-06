"""Dynamic plan evaluator — discovers branches by running concrete evaluation.

The evaluator walks a :class:`Plan` bottom-up, evaluates each step's
predicates against the current :class:`Instance` rows using
:func:`concrete`, and records atom-level observations into a
:class:`BranchTree`.

All branch nodes store live :class:`exp.Expression` objects — no SQL
text round-tripping. The constraint generator operates on these directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.helper import normalize_name
from parseval.identity import ColumnId
from parseval.plan import Plan, Step
from parseval.plan.planner import (
    Aggregate,
    Filter,
    Having,
    Join,
    Limit,
    Project,
    Scan,
    SetOperation,
    Sort,
    SubPlan,
    SubPlanKind,
)
from parseval.plan.context import Context, DerivedSchema, Row, build_context_from_instance
from parseval.plan.rex import Const, Environment, Variable, concrete, column_meta
from parseval.instance import Instance

from .types import (
    AtomObservation,
    BranchTree,
    BranchType,
)


# =============================================================================
# Atom decomposition
# =============================================================================


def decompose_atoms(predicate: exp.Expression) -> Tuple[exp.Expression, ...]:
    """Break a compound predicate into its atomic sub-predicates.

    Atoms are the leaves of the AND/OR/NOT tree. We do NOT descend into
    subqueries (those are handled as SubPlan branches), and we skip atoms
    that contain subqueries since they can't be concretely evaluated.
    """
    atoms: List[exp.Expression] = []

    def _walk(node: exp.Expression) -> None:
        if isinstance(node, exp.And):
            _walk(node.left)
            _walk(node.right)
        elif isinstance(node, exp.Or):
            _walk(node.left)
            _walk(node.right)
        elif isinstance(node, exp.Not):
            _walk(node.this)
        elif isinstance(node, exp.Paren):
            _walk(node.this)
        else:
            # Skip atoms containing subqueries — they need SubPlan evaluation.
            if node.find(exp.Subquery) or node.find(exp.Exists):
                return
            atoms.append(node)

    _walk(predicate)
    return tuple(atoms)


def _classify_outcome(value: Any) -> BranchType:
    """Map a Python evaluation result to an atom-level BranchType."""
    if value is None:
        return BranchType.ATOM_NULL
    if value is True or (value and value is not None):
        return BranchType.ATOM_TRUE
    return BranchType.ATOM_FALSE


def _classify_filter_outcome(value: Any) -> BranchType:
    if value is True:
        return BranchType.FILTER_TRUE
    if value is None:
        return BranchType.FILTER_NULL
    return BranchType.FILTER_FALSE


def _classify_having_outcome(value: Any) -> BranchType:
    if value is True:
        return BranchType.HAVING_PASS
    if value is None:
        return BranchType.HAVING_NULL
    return BranchType.HAVING_FAIL


def _row_ids(row: Row) -> Tuple[Any, ...]:
    return row.rowid if hasattr(row, "rowid") else ()


def _concrete_values(expr: exp.Expression, env: Environment) -> Tuple[Tuple[str, Any], ...]:
    values: List[Tuple[str, Any]] = []
    seen: set[str] = set()
    for col in expr.find_all(exp.Column):
        if col.table:
            key = f"{normalize_name(col.table)}.{normalize_name(col.name)}"
        else:
            key = normalize_name(col.name)
        if key in seen:
            continue
        seen.add(key)
        values.append((key, concrete(col, env)))
    return tuple(values)


def _try_early_classify(atom: exp.Expression) -> Optional[BranchType]:
    """Try to classify an atom from column metadata alone.

    Returns a :class:`BranchType` if the atom is trivially resolvable
    (e.g. ``IS NULL`` on a NOT NULL column is always FALSE), or ``None``
    if full ``concrete()`` evaluation is needed.
    """
    # IS NULL on a NOT NULL column → always FALSE
    if isinstance(atom, (exp.Is,)) and isinstance(atom.expression, exp.Null):
        col = atom.this
        if isinstance(col, exp.Column):
            meta = column_meta(col)
            if meta is not None and not meta["nullable"]:
                return BranchType.ATOM_FALSE

    # IS NOT NULL on a NOT NULL column → always TRUE
    if isinstance(atom, exp.Not):
        inner = atom.this
        if isinstance(inner, (exp.Is,)) and isinstance(inner.expression, exp.Null):
            col = inner.this
            if isinstance(col, exp.Column):
                meta = column_meta(col)
                if meta is not None and not meta["nullable"]:
                    return BranchType.ATOM_TRUE

    return None


# =============================================================================
# Environment builder
# =============================================================================


def _symbol_value(sym: Any) -> Any:
    """Extract the concrete Python value from a Symbol or pass through."""
    if isinstance(sym, (Variable, Const)):
        return sym.concrete
    return sym


def _row_column_name(column: Any) -> str:
    if isinstance(column, ColumnId):
        return column.name.normalized
    return str(column)


def _row_items_by_name(row: Row):
    for column, symbol in row.items():
        yield _row_column_name(column), symbol


def _annotation_table_names(annotation) -> Tuple[str, ...]:
    names: List[str] = []
    seen: set[str] = set()
    for relation in getattr(annotation, "source_relations", ()):
        name = relation.name.normalized if relation.name is not None else relation.display
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def _derived_variable(name: str, value: Any, row_ids: Tuple[Any, ...]) -> Variable:
    normalized = normalize_name(name)
    row_suffix = "_".join(str(row_id) for row_id in row_ids) or "scalar"
    return Variable(
        this=f"derived_{normalized}_{row_suffix}",
        concrete=value,
        is_bound=True,
        is_null=value is None,
        column=normalized,
        rowid=row_ids,
        source="evaluator",
    )


def _retained_columns(row: Row, table_name: str) -> Dict[str, Any]:
    columns: Dict[str, Any] = {}
    for col_name, symbol in _row_items_by_name(row):
        columns[col_name] = symbol
        if "." not in col_name:
            columns[f"{table_name}.{col_name}"] = symbol
    return columns


def _case_arm_condition(case_expr: exp.Case, arm_pred: exp.Expression) -> exp.Expression:
    if isinstance(case_expr.this, exp.Expression):
        return exp.EQ(this=case_expr.this.copy(), expression=arm_pred.copy())
    return arm_pred


def _qualified_bindings_from_row(row: Row, table_name: str) -> Dict[str, Any]:
    """Build qualified-only bindings for a row."""
    bindings: Dict[str, Any] = {}
    for col_name, symbol in _row_items_by_name(row):
        value = _symbol_value(symbol)
        if "." in col_name:
            bindings[col_name] = value
        else:
            bindings[f"{table_name}.{col_name}"] = value
    return bindings


def _env_from_row(
    row: Row,
    table_name: str,
    outer_bindings: Optional[Dict[str, Any]] = None,
) -> Environment:
    """Build an Environment with both bare and table-qualified keys."""
    bindings: Dict[str, Any] = {}
    if outer_bindings:
        bindings.update(outer_bindings)
    for col_name, symbol in _row_items_by_name(row):
        value = _symbol_value(symbol)
        bindings[col_name] = value
        if "." not in col_name:
            bindings[f"{table_name}.{col_name}"] = value
    return Environment(bindings)


def _env_from_join(
    source_row: Row,
    source_name: str,
    join_row: Row,
    join_name: str,
    outer_bindings: Optional[Dict[str, Any]] = None,
) -> Environment:
    """Build an Environment from two joined rows with both table qualifiers."""
    bindings: Dict[str, Any] = dict(outer_bindings) if outer_bindings else {}
    for col_name, symbol in _row_items_by_name(source_row):
        value = _symbol_value(symbol)
        bindings[col_name] = value
        if "." not in col_name:
            bindings[f"{source_name}.{col_name}"] = value
    for col_name, symbol in _row_items_by_name(join_row):
        value = _symbol_value(symbol)
        bindings[col_name] = value
        if "." not in col_name:
            bindings[f"{join_name}.{col_name}"] = value
    return Environment(bindings)


def _qualified_columns(row: Row, table_name: str) -> Dict[str, Any]:
    columns: Dict[str, Any] = {}
    for col_name, symbol in _row_items_by_name(row):
        if "." in col_name:
            columns[col_name] = symbol
        else:
            columns[f"{table_name}.{col_name}"] = symbol
    return columns


def _joined_row(source_row: Row, source_name: str, join_row: Row, join_name: str) -> Row:
    return Row(
        this=source_row.rowid + join_row.rowid,
        columns={
            **_qualified_columns(source_row, source_name),
            **_qualified_columns(join_row, join_name),
        },
    )


def _null_join_row(table: DerivedSchema, table_name: str, row_ids: Tuple[Any, ...]) -> Row:
    return Row(
        this=(),
        columns={
            (column if "." in column else f"{table_name}.{column}"): _derived_variable(
                column if "." in column else f"{table_name}.{column}",
                None,
                row_ids,
            )
            for column in table.columns
        },
    )


# =============================================================================
# PlanEvaluator
# =============================================================================


class PlanEvaluator:
    """Evaluate a Plan against an Instance, recording branch observations.

    Call :meth:`evaluate` to run one full pass. The returned
    :class:`BranchTree` accumulates observations across multiple calls.
    """

    def __init__(self, plan: Plan, instance: Instance, dialect: str = "sqlite"):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        if getattr(self.plan, "_instance", None) is not instance:
            self.plan._instance = instance
            self.plan._annotations = None

    def evaluate(self, tree: Optional[BranchTree] = None) -> BranchTree:
        if tree is None:
            tree = BranchTree()
        self.evaluate_context(tree)
        return tree

    def evaluate_context(self, tree: Optional[BranchTree] = None) -> Context:
        if tree is None:
            tree = BranchTree()
        ctx = build_context_from_instance(self.instance)
        return self._walk(self.plan.root, ctx, tree)

    def _evaluate_subtree(
        self,
        root: Step,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Context:
        ctx = build_context_from_instance(self.instance)
        return self._walk(root, ctx, BranchTree(), observe=False, outer_bindings=outer_bindings)

    def _walk(
        self,
        step: Step,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Context:
        """Recursively evaluate the plan bottom-up."""
        dep_contexts: Dict[str, DerivedSchema] = {}
        for dep in step.chain_dependencies:
            dep_ctx = self._walk(
                dep,
                ctx,
                tree,
                observe=observe,
                outer_bindings=outer_bindings,
            )
            for name, table in dep_ctx.tables.items():
                dep_contexts[name] = table

        input_ctx = Context(tables=dep_contexts) if dep_contexts else ctx

        # Walk subplan dependencies (EXISTS, IN, scalar subqueries) for
        # branch observation recording.  They don't transform the context.
        if observe:
            for sub in step.subplan_dependencies:
                self._walk(sub, input_ctx, tree)

        if isinstance(step, Scan):
            return self._eval_scan(step, ctx)
        elif isinstance(step, Filter):
            return self._eval_filter(
                step,
                input_ctx,
                tree,
                observe=observe,
                outer_bindings=outer_bindings,
            )
        elif isinstance(step, Join):
            return self._eval_join(
                step,
                input_ctx,
                tree,
                observe=observe,
                outer_bindings=outer_bindings,
            )
        elif isinstance(step, Aggregate):
            return self._eval_aggregate(step, input_ctx, tree, observe=observe)
        elif isinstance(step, Having):
            return self._eval_having(step, input_ctx, tree, observe=observe)
        elif isinstance(step, Project):
            return self._eval_project(step, input_ctx, tree, observe=observe)
        elif isinstance(step, SubPlan):
            return self._eval_subplan(step, input_ctx, tree)
        elif isinstance(step, Sort):
            return self._eval_sort(step, input_ctx)
        elif isinstance(step, Limit):
            return self._eval_limit(step, input_ctx)
        elif isinstance(step, SetOperation):
            return input_ctx
        return input_ctx

    def _eval_scan(self, step: Scan, ctx: Context) -> Context:
        if step.source is None or not isinstance(step.source, exp.Table):
            table_name = step.name
            if table_name in ctx.tables:
                return Context(tables={step.name: ctx.tables[table_name]})
            return Context(tables={step.name: DerivedSchema(columns=(), rows=[])})

        table_name = step.source.name
        if table_name not in ctx.tables:
            return Context(tables={step.name: DerivedSchema(columns=(), rows=[])})
        return Context(tables={step.name: ctx.tables[table_name]})

    def _eval_filter(
        self,
        step: Filter,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Context:
        if step.condition is None:
            return ctx

        predicate = step.condition
        atoms = decompose_atoms(predicate) if observe else ()
        node = None
        if observe:
            annotation = self.plan.annotation_for(step)
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Filter",
                site="filter",
                predicate=predicate,
                atoms=atoms,
                tables=_annotation_table_names(annotation),
            )

        passing_rows: List[Row] = []
        for table_name, table in ctx.tables.items():
            for row in table.rows:
                env = _env_from_row(row, table_name, outer_bindings)
                row_bindings = dict(outer_bindings) if outer_bindings else {}
                row_bindings.update(_qualified_bindings_from_row(row, table_name))
                predicate_for_row = self._resolve_subquery_predicates(
                    predicate,
                    step.subplan_dependencies,
                    row_bindings,
                    env,
                )
                predicate_value = concrete(predicate_for_row, env)
                # Record per-atom observations.
                for atom_id, atom in enumerate(atoms):
                    atom_for_row = self._resolve_subquery_predicates(
                        atom,
                        step.subplan_dependencies,
                        row_bindings,
                        env,
                    )
                    outcome = _try_early_classify(atom)
                    if outcome is None:
                        value = concrete(atom_for_row, env)
                        outcome = _classify_outcome(value)
                    if node is not None:
                        tree.record_observation(
                            node,
                            AtomObservation(
                                atom_id=atom_id,
                                outcome=outcome,
                                row_ids=_row_ids(row),
                                concrete_values=_concrete_values(atom_for_row, env),
                            ),
                        )
                if node is not None:
                    tree.record_observation(
                        node,
                        AtomObservation(
                            atom_id=-1,
                            outcome=_classify_filter_outcome(predicate_value),
                            row_ids=_row_ids(row),
                            concrete_values=_concrete_values(predicate_for_row, env),
                        ),
                    )
                # Full predicate for pass/fail.
                if predicate_value is True:
                    passing_rows.append(row)

        return Context(
            tables={
                name: DerivedSchema(columns=table.columns, rows=passing_rows, column_range=table.column_range)
                if passing_rows and len(passing_rows[0]) == len(table.columns)
                else DerivedSchema(columns=tuple(passing_rows[0].columns) if passing_rows else table.columns, rows=passing_rows)
                for name, table in ctx.tables.items()
            }
        )

    def _eval_join(
        self,
        step: Join,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Context:
        for join_name, join_data in (step.joins or {}).items():
            condition = join_data.get("condition")
            if condition is None or not isinstance(condition, exp.Expression):
                continue

            atoms = decompose_atoms(condition) if observe else ()
            node = None
            if observe:
                annotation = self.plan.annotation_for(step)
                node = tree.get_or_create_node(
                    step_id=annotation.step_id,
                    step_type="Join",
                    site="join_on",
                    predicate=condition,
                    atoms=atoms,
                    tables=_annotation_table_names(annotation),
                )

            source_name = step.source_name or step.name
            source_table = ctx.tables.get(source_name)
            join_table = ctx.tables.get(join_name)
            if source_table is None or join_table is None:
                continue

            side = str(join_data.get("side") or "").lower()
            preserves_source = side in {"left", "full"}
            preserves_join = side in {"right", "full"}

            def join_key_values(env: Environment) -> Tuple[Tuple[str, Any], ...]:
                return tuple(
                    value
                    for key_expr in (
                        tuple(join_data.get("source_key", ()))
                        + tuple(join_data.get("join_key", ()))
                    )
                    for value in _concrete_values(key_expr, env)
                )

            def record_preserved_row(row: Row, env: Environment) -> None:
                if node is None:
                    return
                tree.record_observation(
                    node,
                    AtomObservation(
                        atom_id=-1,
                        outcome=BranchType.JOIN_NO_MATCH,
                        row_ids=_row_ids(row),
                        concrete_values=join_key_values(env),
                    ),
                )

            def evaluate_join_pair(env: Environment) -> Tuple[bool, bool, BranchType]:
                source_key = tuple(concrete(key, env) for key in join_data.get("source_key", ()))
                join_key = tuple(concrete(key, env) for key in join_data.get("join_key", ()))
                keys_match = (not source_key and not join_key) or source_key == join_key
                condition_value = concrete(condition, env)
                condition_matches = condition_value is True
                outcome = (
                    BranchType.JOIN_NULL
                    if any(value is None for value in source_key + join_key) or condition_value is None
                    else BranchType.JOIN_MATCH
                    if keys_match and condition_matches
                    else BranchType.JOIN_NO_MATCH
                )
                return keys_match, condition_matches, outcome

            def record_join_pair(env: Environment, row_ids: Tuple[Any, ...], outcome: BranchType) -> None:
                if node is None:
                    return
                for atom_id, atom in enumerate(atoms):
                    atom_outcome = _try_early_classify(atom)
                    if atom_outcome is None:
                        value = concrete(atom, env)
                        atom_outcome = _classify_outcome(value)
                    tree.record_observation(
                        node,
                        AtomObservation(
                            atom_id=atom_id,
                            outcome=atom_outcome,
                            row_ids=row_ids,
                            concrete_values=_concrete_values(atom, env),
                        ),
                    )
                tree.record_observation(
                    node,
                    AtomObservation(
                        atom_id=-1,
                        outcome=outcome,
                        row_ids=row_ids,
                        concrete_values=join_key_values(env),
                    ),
                )

            joined_rows: List[Row] = []
            matched_join_rows: set[int] = set()
            for source_row in source_table.rows:
                source_matched = False
                for join_index, join_row in enumerate(join_table.rows):
                    env = _env_from_join(
                        source_row,
                        source_name,
                        join_row,
                        join_name,
                        outer_bindings,
                    )
                    joined_row_ids = _row_ids(source_row) + _row_ids(join_row)
                    keys_match, condition_matches, join_outcome = evaluate_join_pair(env)
                    record_join_pair(env, joined_row_ids, join_outcome)

                    if keys_match and condition_matches:
                        source_matched = True
                        matched_join_rows.add(join_index)
                        joined_rows.append(_joined_row(source_row, source_name, join_row, join_name))
                if preserves_source and not source_matched:
                    null_right = _null_join_row(join_table, join_name, _row_ids(source_row))
                    preserved = _joined_row(
                        source_row,
                        source_name,
                        null_right,
                        join_name,
                    )
                    joined_rows.append(preserved)
                    record_preserved_row(
                        preserved,
                        _env_from_join(source_row, source_name, null_right, join_name, outer_bindings),
                    )

            if preserves_join:
                for join_index, join_row in enumerate(join_table.rows):
                    if join_index in matched_join_rows:
                        continue
                    null_source = _null_join_row(source_table, source_name, _row_ids(join_row))
                    preserved = _joined_row(
                        null_source,
                        source_name,
                        join_row,
                        join_name,
                    )
                    joined_rows.append(preserved)
                    record_preserved_row(
                        preserved,
                        _env_from_join(null_source, source_name, join_row, join_name, outer_bindings),
                    )

            columns = tuple(joined_rows[0].columns) if joined_rows else (
                tuple(f"{source_name}.{col}" for col in source_table.columns)
                + tuple(f"{join_name}.{col}" for col in join_table.columns)
            )
            ctx = Context(
                tables={
                    step.name: DerivedSchema(columns=columns, rows=joined_rows),
                }
            )

        return ctx

    def _eval_aggregate(
        self,
        step: Aggregate,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
        if not step.group and not step.aggregations:
            return ctx

        node = None
        if observe:
            annotation = self.plan.annotation_for(step)
            # Use a synthetic "group_cardinality" atom for group-size branches.
            group_pred = exp.Literal.number(1)  # placeholder expression
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Aggregate",
                site="group",
                predicate=group_pred,
                atoms=(group_pred,),
                tables=_annotation_table_names(annotation),
            )

        for table_name, table in ctx.tables.items():
            groups: Dict[tuple, int] = {}
            if step.group:
                for row in table.rows:
                    env = _env_from_row(row, table_name)
                    key = tuple(concrete(g, env) for g in step.group.values())
                    groups[key] = groups.get(key, 0) + 1
                output_rows = self._grouped_aggregate_rows(step, list(table.rows), table_name)
            else:
                row_ids = tuple(row_id for row in table.rows for row_id in _row_ids(row))
                columns = {
                    alias: _derived_variable(
                        alias,
                        self._aggregate_expression_value(
                            aggregate,
                            list(table.rows),
                            table_name,
                            operands=getattr(step, "operands", ()) or (),
                        ),
                        row_ids,
                    )
                    for aggregate in step.aggregations
                    for alias in (normalize_name(aggregate.alias_or_name),)
                }
                output_rows = [Row(this=row_ids, columns=columns)]
                groups[((),)] = len(table.rows)

            if node is not None:
                for count in groups.values():
                    outcome = BranchType.GROUP_SINGLE if count == 1 else BranchType.GROUP_MULTI
                    tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))

            return Context(
                tables={
                    step.name: DerivedSchema(
                        columns=tuple(output_rows[0].columns) if output_rows else self._aggregate_columns(step),
                        rows=output_rows,
                    )
                }
            )

        return Context(tables={step.name: DerivedSchema(columns=self._aggregate_columns(step), rows=[])})

    def _eval_having(
        self,
        step: Having,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
        if step.condition is None:
            return ctx

        predicate = step.condition
        atoms = decompose_atoms(predicate) if observe else ()
        node = None
        if observe:
            annotation = self.plan.annotation_for(step)
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Having",
                site="having",
                predicate=predicate,
                atoms=atoms,
                tables=_annotation_table_names(annotation),
            )

        passing_rows: List[Row] = []
        columns: Tuple[str, ...] = ()
        for table_name, table in ctx.tables.items():
            columns = table.columns
            for row in table.rows:
                env = _env_from_row(row, table_name)
                predicate_value = concrete(predicate, env)
                for atom_id, atom in enumerate(atoms):
                    outcome = _try_early_classify(atom)
                    if outcome is None:
                        value = concrete(atom, env)
                        outcome = _classify_outcome(value)
                    if node is not None:
                        tree.record_observation(
                            node,
                            AtomObservation(
                                atom_id=atom_id,
                                outcome=outcome,
                                row_ids=_row_ids(row),
                                concrete_values=_concrete_values(atom, env),
                            ),
                        )
                if node is not None:
                    tree.record_observation(
                        node,
                        AtomObservation(
                            atom_id=-1,
                            outcome=_classify_having_outcome(predicate_value),
                            row_ids=_row_ids(row),
                            concrete_values=_concrete_values(predicate, env),
                        ),
                    )
                if predicate_value is True:
                    passing_rows.append(row)

        return Context(tables={step.name: DerivedSchema(columns=columns, rows=passing_rows)})

    def _eval_project(
        self,
        step: Project,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
        if observe:
            annotation = self.plan.annotation_for(step)
            for projection in step.projections:
                if not isinstance(projection, exp.Expression):
                    continue
                for case_expr in projection.find_all(exp.Case):
                    ifs = case_expr.args.get("ifs") or []
                    for arm_index, arm in enumerate(ifs):
                        del arm_index
                        raw_arm_pred = arm.args.get("this")
                        if not isinstance(raw_arm_pred, exp.Expression):
                            continue
                        arm_pred = _case_arm_condition(case_expr, raw_arm_pred)

                        atoms = decompose_atoms(arm_pred)
                        node = tree.get_or_create_node(
                            step_id=annotation.step_id,
                            step_type="Project",
                            site="case_arm",
                            predicate=arm_pred,
                            atoms=atoms,
                            tables=_annotation_table_names(annotation),
                        )

                        for table_name, table in ctx.tables.items():
                            for row in table.rows:
                                env = _env_from_row(row, table_name)
                                arm_value = concrete(arm_pred, env)
                                for atom_id, atom in enumerate(atoms):
                                    outcome = _try_early_classify(atom)
                                    if outcome is None:
                                        value = concrete(atom, env)
                                        outcome = _classify_outcome(value)
                                    tree.record_observation(
                                        node,
                                        AtomObservation(
                                            atom_id=atom_id,
                                            outcome=outcome,
                                            row_ids=_row_ids(row),
                                            concrete_values=_concrete_values(atom, env),
                                        ),
                                    )
                                tree.record_observation(
                                    node,
                                    AtomObservation(
                                        atom_id=-1,
                                        outcome=(
                                            BranchType.CASE_ARM_TAKEN
                                            if arm_value is True
                                            else BranchType.CASE_ARM_SKIPPED
                                        ),
                                        row_ids=_row_ids(row),
                                        concrete_values=_concrete_values(arm_pred, env),
                                    ),
                                )

            # Track DISTINCT
            if step.distinct:
                distinct_node = tree.get_or_create_node(
                    step_id=annotation.step_id,
                    step_type="Project",
                    site="distinct",
                    predicate=exp.Literal.string("DISTINCT"),
                    atoms=(exp.Literal.string("DISTINCT"),),
                    tables=_annotation_table_names(annotation),
                )

                seen = set()
                has_duplicates = False
                for table_name, table in ctx.tables.items():
                    visible_columns = self._projected_columns(step, table.columns)
                    for row in table.rows:
                        projected = Row(
                            this=_row_ids(row),
                            columns=self._projected_values(step, row, table_name),
                        )
                        key = self._projected_key(projected, visible_columns)
                        if key in seen:
                            has_duplicates = True
                            break
                        seen.add(key)
                    if has_duplicates:
                        break

                outcome = BranchType.DISTINCT_DUPLICATE if has_duplicates else BranchType.DISTINCT_UNIQUE
                tree.record_observation(distinct_node, AtomObservation(atom_id=0, outcome=outcome))

        return self._materialize_project(step, ctx)

    def _eval_sort(self, step: Sort, ctx: Context) -> Context:
        output: Dict[str, DerivedSchema] = {}
        for table_name, table in ctx.tables.items():
            rows = list(table.rows)
            for ordered in reversed(getattr(step, "key", ()) or ()):
                expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
                descending = bool(ordered.args.get("desc")) if isinstance(ordered, exp.Ordered) else False
                rows.sort(
                    key=lambda row: self._sort_key_value(expr, row, table_name),
                    reverse=descending,
                )
            output[step.name] = DerivedSchema(columns=table.columns, rows=rows)
            break
        return Context(tables=output)

    def _eval_limit(self, step: Limit, ctx: Context) -> Context:
        output: Dict[str, DerivedSchema] = {}
        for table_name, table in ctx.tables.items():
            del table_name
            offset = max(int(getattr(step, "offset", 0) or 0), 0)
            if step.limit == float("inf"):
                rows = list(table.rows)[offset:]
            else:
                limit_value = max(int(step.limit), 0)
                rows = list(table.rows)[offset : offset + limit_value]
            output[step.name] = DerivedSchema(columns=table.columns, rows=rows)
            break
        return Context(tables=output)

    def _materialize_project(self, step: Project, ctx: Context) -> Context:
        output: Dict[str, DerivedSchema] = {}
        for table_name, table in ctx.tables.items():
            rows: List[Row] = []
            visible_columns = self._projected_columns(step, table.columns)
            for row in table.rows:
                projected = self._projected_values(step, row, table_name)
                rows.append(Row(this=_row_ids(row), columns=projected))

            if step.distinct:
                distinct_rows: List[Row] = []
                seen: set[Tuple[Any, ...]] = set()
                for row in rows:
                    key = self._projected_key(row, visible_columns)
                    if key in seen:
                        continue
                    seen.add(key)
                    distinct_rows.append(row)
                rows = distinct_rows

            columns = tuple(rows[0].columns) if rows else visible_columns
            output[step.name] = DerivedSchema(columns=columns, rows=rows)
            break
        return Context(tables=output)

    def _projected_columns(self, step: Project, input_columns: Tuple[str, ...]) -> Tuple[str, ...]:
        columns: List[str] = []
        for projection in step.projections:
            if self._is_star_projection(projection):
                columns.extend(input_columns)
            else:
                columns.append(self._projection_name(projection))
        return tuple(columns)

    def _projected_values(self, step: Project, row: Row, table_name: str) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        env = _env_from_row(row, table_name)
        for projection in step.projections:
            if self._is_star_projection(projection):
                values.update(dict(_row_items_by_name(row)))
                continue
            name = self._projection_name(projection)
            expr = projection.this if isinstance(projection, exp.Alias) else projection
            values[name] = self._projection_value(expr, row, env)
        for col_name, symbol in _retained_columns(row, table_name).items():
            values.setdefault(col_name, symbol)
        return values

    def _projected_key(self, row: Row, visible_columns: Tuple[str, ...]) -> Tuple[Any, ...]:
        return tuple(_symbol_value(row[column]) for column in visible_columns)

    def _projection_name(self, projection: exp.Expression) -> str:
        return normalize_name(projection.alias_or_name or projection.sql(dialect=self.dialect))

    def _projection_value(self, expr: exp.Expression, row: Row, env: Environment) -> Any:
        if isinstance(expr, exp.Column):
            try:
                return row[expr]
            except KeyError:
                pass
        value = concrete(expr, env)
        return _derived_variable(expr.alias_or_name or expr.sql(dialect=self.dialect), value, _row_ids(row))

    def _is_star_projection(self, projection: exp.Expression) -> bool:
        if isinstance(projection, exp.Star):
            return True
        if isinstance(projection, exp.Column):
            return isinstance(projection.this, exp.Star) or projection.name == "*"
        return False

    def _eval_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate a SubPlan and record branch observations."""
        if step.kind is SubPlanKind.EXISTS:
            return self._eval_exists_subplan(step, ctx, tree)
        elif step.kind is SubPlanKind.IN:
            return self._eval_in_subplan(step, ctx, tree)
        return ctx

    def _eval_exists_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate EXISTS (SELECT ...) and record EXISTS_TRUE/EXISTS_FALSE."""
        annotation = self.plan.annotation_for(step)
        step_id = annotation.step_id

        node = tree.get_or_create_node(
            step_id=step_id,
            step_type="SubPlan",
            site="exists",
            predicate=step.anchor,
            atoms=(step.anchor,),
            tables=(),
        )

        observed_outer_row = False
        for table_name, table in ctx.tables.items():
            for row in table.rows:
                observed_outer_row = True
                outer_bindings = _qualified_bindings_from_row(row, table_name)
                has_rows = self._inner_plan_has_rows(step.inner, outer_bindings)
                outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
                tree.record_observation(
                    node,
                    AtomObservation(
                        atom_id=0,
                        outcome=outcome,
                        row_ids=_row_ids(row),
                        concrete_values=_concrete_values(step.anchor, _env_from_row(row, table_name)),
                    ),
                )

        if not observed_outer_row:
            # Evaluate uncorrelated inner plan directly (inner plan steps are not
            # in the outer plan's annotation map, so we cannot use _walk).
            has_rows = self._inner_plan_has_rows(step.inner)
            outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
            tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))

        return ctx  # SubPlan doesn't transform the outer context

    def _resolve_subquery_predicates(
        self,
        predicate: exp.Expression,
        subplans: Tuple[SubPlan, ...],
        outer_bindings: Dict[str, Any],
        env: Optional[Environment] = None,
    ) -> exp.Expression:
        if not (
            predicate.find(exp.Subquery)
            or predicate.find(exp.Exists)
            or predicate.find(exp.In)
        ):
            return predicate

        scalar_values: Dict[str, Any] = {}
        predicate_values: Dict[str, bool] = {}
        outer_env = env or Environment(outer_bindings)
        for subplan in subplans:
            key = subplan.anchor.sql(dialect=self.dialect)
            if subplan.kind is SubPlanKind.SCALAR:
                scalar_values[key] = self._scalar_subquery_value(
                    subplan,
                    outer_bindings,
                )
            elif subplan.kind is SubPlanKind.EXISTS:
                predicate_values[key] = self._inner_plan_has_rows(subplan.inner, outer_bindings)
            elif subplan.kind is SubPlanKind.IN and isinstance(subplan.anchor, exp.In):
                outer_value = concrete(subplan.anchor.this, outer_env)
                inner_values = self._inner_plan_values(subplan.inner, outer_bindings)
                predicate_values[key] = outer_value in inner_values
        if not scalar_values and not predicate_values:
            return predicate

        def replace_subquery(node: exp.Expression):
            key = node.sql(dialect=self.dialect)
            if isinstance(node, exp.Subquery) and key in scalar_values:
                return exp.convert(scalar_values[key])
            if isinstance(node, (exp.Exists, exp.In)) and key in predicate_values:
                return exp.true() if predicate_values[key] else exp.false()
            return node

        return predicate.copy().transform(replace_subquery)

    def _eval_inner_plan(
        self,
        root: Step,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[Row], str, List[Project]]:
        """Evaluate an inner plan through the shared operator pipeline."""
        projects: List[Project] = []

        def collect_projects(step: Step) -> None:
            if isinstance(step, Project):
                projects.append(step)
            for dep in step.chain_dependencies:
                collect_projects(dep)

        collect_projects(root)
        ctx = self._evaluate_subtree(root, outer_bindings)
        if not ctx.tables:
            return [], "", projects

        table_name, table = next(iter(ctx.tables.items()))
        return list(table.rows), table_name, projects

    def _scalar_subquery_value(
        self,
        subplan: SubPlan,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        rows, table_name, projects = self._eval_inner_plan(subplan.inner, outer_bindings)
        return self._project_scalar_value(projects, rows, table_name, outer_bindings)

    def _project_scalar_value(
        self,
        projects: List[Project],
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not rows:
            return None

        if projects and projects[0].projections:
            projection = projects[0].projections[0]
            alias = self._projection_name(projection)
            try:
                return _symbol_value(rows[0][alias])
            except KeyError:
                pass

            projection_expr = projection.this if isinstance(projection, exp.Alias) else projection
            env = _env_from_row(rows[0], table_name, outer_bindings)
            return concrete(projection_expr, env)

        if len(rows[0].columns) == 1:
            return _symbol_value(next(iter(dict(rows[0].items()).values())))

        return None

    def _grouped_aggregate_rows(
        self,
        step: Aggregate,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> List[Row]:
        grouped: Dict[Tuple[Any, ...], List[Row]] = {}
        group_aliases = list(step.group)
        for row in rows:
            env = _env_from_row(row, table_name, outer_bindings)
            key = tuple(concrete(expr, env) for expr in step.group.values())
            grouped.setdefault(key, []).append(row)

        output_rows: List[Row] = []
        for key, group_rows in grouped.items():
            row_ids = tuple(row_id for row in group_rows for row_id in _row_ids(row))
            columns: Dict[str, Any] = _retained_columns(group_rows[0], table_name)
            for alias, value in zip(group_aliases, key):
                columns[alias] = _derived_variable(alias, value, row_ids)
            for aggregate in step.aggregations:
                alias = normalize_name(aggregate.alias_or_name)
                columns[alias] = _derived_variable(
                    alias,
                    self._aggregate_expression_value(
                        aggregate,
                        group_rows,
                        table_name,
                        outer_bindings,
                        getattr(step, "operands", ()) or (),
                    ),
                    row_ids,
                )
            output_rows.append(Row(this=row_ids, columns=columns))
        return output_rows

    def _aggregate_columns(self, step: Aggregate) -> Tuple[str, ...]:
        return tuple(list(step.group) + [normalize_name(aggregate.alias_or_name) for aggregate in step.aggregations])

    def _sort_key_value(
        self,
        expr: exp.Expression,
        row: Row,
        table_name: str,
    ) -> Tuple[bool, Any]:
        if isinstance(expr, exp.Literal) and expr.is_string:
            try:
                value = _symbol_value(row[str(expr.this)])
                return value is None, value
            except KeyError:
                pass
        value = concrete(expr, _env_from_row(row, table_name))
        return value is None, value

    def _aggregate_expression_value(
        self,
        aggregate: exp.Expression,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[str, Any]] = None,
        operands: Tuple[exp.Expression, ...] = (),
    ) -> Any:
        operand_rows: List[Dict[str, Any]] = []
        operand_expr_by_alias: Dict[str, exp.Expression] = {}
        for row in rows:
            source_env = _env_from_row(row, table_name, outer_bindings)
            operand_values: Dict[str, Any] = {}
            for operand in operands:
                alias = normalize_name(operand.alias_or_name)
                operand_expr = operand.this if isinstance(operand, exp.Alias) else operand
                operand_expr_by_alias[alias] = operand_expr
                operand_values[alias] = concrete(operand_expr, source_env)
            operand_rows.append(operand_values)

        aggregate_expr = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        aggregate_types = (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max)
        if isinstance(aggregate_expr, aggregate_types):
            return self._aggregate_function_value(
                aggregate_expr,
                rows,
                table_name,
                outer_bindings,
                operand_rows,
                operand_expr_by_alias,
            )

        def replace_aggregate(node: exp.Expression):
            if not isinstance(node, aggregate_types):
                return node
            value = self._aggregate_function_value(
                node,
                rows,
                table_name,
                outer_bindings,
                operand_rows,
                operand_expr_by_alias,
            )
            return exp.convert(value)

        resolved = aggregate_expr.copy().transform(replace_aggregate)
        if rows:
            return concrete(resolved, _env_from_row(rows[0], table_name, outer_bindings))
        return concrete(resolved, Environment())

    def _aggregate_function_value(
        self,
        aggregate_expr: exp.Expression,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[str, Any]],
        operand_rows: List[Dict[str, Any]],
        operand_expr_by_alias: Dict[str, exp.Expression],
    ) -> Any:
        arg = aggregate_expr.this
        if isinstance(aggregate_expr, exp.Count):
            if isinstance(arg, exp.Star):
                return len(rows)
            if isinstance(arg, exp.Column):
                operand_expr = operand_expr_by_alias.get(normalize_name(arg.name))
                if isinstance(operand_expr, exp.Star):
                    return len(rows)

        if operand_rows and any(operand_values for operand_values in operand_rows):
            values = [
                concrete(arg, Environment(operand_values))
                for operand_values in operand_rows
            ]
        else:
            values = [
                concrete(arg, _env_from_row(row, table_name, outer_bindings))
                for row in rows
            ]
        non_null_values = [value for value in values if value is not None]

        if isinstance(aggregate_expr, exp.Count):
            return len(non_null_values)
        if not non_null_values:
            return None
        if isinstance(aggregate_expr, exp.Avg):
            # Filter to numeric values only (handle TEXT columns with numeric content)
            numeric = [v for v in non_null_values if isinstance(v, (int, float))]
            return sum(numeric) / len(numeric) if numeric else None
        if isinstance(aggregate_expr, exp.Sum):
            numeric = [v for v in non_null_values if isinstance(v, (int, float))]
            return sum(numeric) if numeric else None
        if isinstance(aggregate_expr, exp.Min):
            # Try numeric comparison first, then string comparison
            try:
                return min(non_null_values)
            except TypeError:
                return min(str(v) for v in non_null_values)
        if isinstance(aggregate_expr, exp.Max):
            try:
                return max(non_null_values)
            except TypeError:
                return max(str(v) for v in non_null_values)
        return None

    def _inner_plan_has_rows(
        self,
        root: Step,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check whether an inner plan would produce at least one row."""
        rows, _, _ = self._eval_inner_plan(root, outer_bindings)
        return len(rows) > 0

    def _inner_plan_values(
        self,
        root: Step,
        outer_bindings: Optional[Dict[str, Any]] = None,
    ) -> set:
        """Evaluate inner plan and return the set of projected column values."""
        rows, table_name, projects = self._eval_inner_plan(root, outer_bindings)
        if not rows:
            return set()

        values: set = set()
        projection = projects[0].projections[0] if projects and projects[0].projections else None
        for row in rows:
            if projection is not None:
                alias = self._projection_name(projection)
                try:
                    values.add(_symbol_value(row[alias]))
                    continue
                except KeyError:
                    projection_expr = projection.this if isinstance(projection, exp.Alias) else projection
            elif len(row.columns) == 1:
                values.add(_symbol_value(next(iter(dict(_row_items_by_name(row)).values()))))
                continue
            else:
                continue

            env = _env_from_row(row, table_name, outer_bindings)
            val = concrete(projection_expr, env)
            values.add(val)
        return values

    def _eval_in_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate col IN (SELECT ...) and record IN_MATCH/IN_NO_MATCH."""
        annotation = self.plan.annotation_for(step)
        step_id = annotation.step_id

        node = tree.get_or_create_node(
            step_id=step_id,
            step_type="SubPlan",
            site="in",
            predicate=step.anchor,
            atoms=(step.anchor,),
            tables=(),
        )

        # Check each outer row against the inner result set.
        if isinstance(step.anchor, exp.In):
            outer_col = step.anchor.this
            if isinstance(outer_col, exp.Column):
                for table_name, table in ctx.tables.items():
                    for row in table.rows:
                        outer_bindings = _qualified_bindings_from_row(row, table_name)
                        inner_values = self._inner_plan_values(step.inner, outer_bindings)
                        env = _env_from_row(row, table_name)
                        outer_val = concrete(outer_col, env)

                        outcome = BranchType.IN_MATCH if outer_val in inner_values else BranchType.IN_NO_MATCH
                        tree.record_observation(
                            node,
                            AtomObservation(
                                atom_id=0,
                                outcome=outcome,
                                row_ids=_row_ids(row),
                                concrete_values=_concrete_values(step.anchor, env),
                            ),
                        )

        return ctx

__all__ = ["PlanEvaluator", "decompose_atoms"]
