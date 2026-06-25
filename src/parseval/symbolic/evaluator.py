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

from parseval.identity import (
    ColumnId,
    ColumnKind,
    RelationId,
    column_id,
    column_identity,
    identifier_name,
    physical_column,
)
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
from parseval.plan.context import (
    AggregateGroup,
    Context,
    DerivedSchema,
    Row,
    build_context_from_instance,
)
from parseval.plan.rex import Const, Environment, Variable, concrete, column_meta
from parseval.instance import Instance

from .types import (
    AtomObservation,
    BranchTree,
    BranchType,
    JoinFact,
    OperatorObligation,
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


def scalar_subquery_atoms(predicate: exp.Expression) -> Tuple[exp.Expression, ...]:
    atoms: List[exp.Expression] = []

    def walk(node: exp.Expression) -> None:
        if isinstance(node, exp.And):
            walk(node.left)
            walk(node.right)
            return
        if isinstance(node, exp.Or):
            walk(node.left)
            walk(node.right)
            return
        if node.find(exp.Subquery) or node.find(exp.Exists):
            atoms.append(node)

    walk(predicate)
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


def _concrete_values(expr: exp.Expression, env: Environment) -> Tuple[Tuple[ColumnId, Any], ...]:
    values: List[Tuple[ColumnId, Any]] = []
    seen: set[ColumnId] = set()
    for col in expr.find_all(exp.Column):
        col_id = column_identity(col)
        if col_id is None:
            continue
        if col_id in seen:
            continue
        seen.add(col_id)
        values.append((col_id, concrete(col, env)))
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


def _annotation_relation_ids(annotation) -> Tuple[RelationId, ...]:
    return tuple(getattr(annotation, "source_relations", ()))


def _join_facts_for_step(plan: Plan, step: Step) -> Tuple[JoinFact, ...]:
    if not isinstance(step, Join):
        return ()

    facts: List[JoinFact] = []
    referenced = list(plan.annotation_for(step).referenced_columns)
    ref_index = 0
    for join_rel, join_data in (step.joins or {}).items():
        equalities: List[Tuple[ColumnId, ColumnId]] = []
        source_keys = tuple(join_data.get("source_key", ()))
        join_keys = tuple(join_data.get("join_key", ()))
        for source_key, join_key in zip(source_keys, join_keys):
            source_id = column_identity(source_key) if isinstance(source_key, exp.Column) else None
            join_id = column_identity(join_key) if isinstance(join_key, exp.Column) else None
            if (source_id is None or join_id is None) and ref_index + 1 < len(referenced):
                source_id = referenced[ref_index]
                join_id = referenced[ref_index + 1]
            if source_id is not None and join_id is not None:
                equalities.append((source_id, join_id))
            ref_index += 2

        source_relation = equalities[0][0].relation if equalities else None
        target_relation = join_rel if isinstance(join_rel, RelationId) else None
        if source_relation is None or target_relation is None:
            continue
        facts.append(
            JoinFact(
                source_relation=source_relation,
                target_relation=target_relation,
                equalities=tuple(equalities),
                predicate=join_data.get("condition")
                if isinstance(join_data.get("condition"), exp.Expression)
                else None,
                side=str(join_data.get("side") or "").lower(),
            )
        )
    return tuple(facts)


def _derived_variable(
    name: str,
    value: Any,
    row_ids: Tuple[Any, ...],
    relation_id: Optional[RelationId] = None,
    column_id_override: Optional[ColumnId] = None,
) -> Variable:
    normalized = name
    row_suffix = "_".join(str(row_id) for row_id in row_ids) or "scalar"
    return Variable(
        this=f"derived_{normalized}_{row_suffix}",
        concrete=value,
        is_bound=True,
        is_null=value is None,
        column_id=(
            column_id_override
            or column_id(ColumnKind.DERIVED, identifier_name(normalized), relation_id)
        ),
        rowid=row_ids,
        source="evaluator",
    )


def _copy_variable_for_column(
    column_id: ColumnId,
    symbol: Any,
    row_ids: Tuple[Any, ...],
) -> Variable:
    value = _symbol_value(symbol)
    kwargs: Dict[str, Any] = {
        "this": f"{column_id.display}_{'_'.join(str(row_id) for row_id in row_ids) or 'row'}",
        "concrete": value,
        "is_bound": getattr(symbol, "is_bound", True),
        "is_null": getattr(symbol, "is_null", value is None),
        "column_id": column_id,
        "relation_id": column_id.relation,
        "rowid": row_ids,
        "source": getattr(symbol, "source", None) or "evaluator",
    }
    for key in ("type", "nullable", "unique", "domain"):
        if hasattr(symbol, "args") and key in symbol.args:
            kwargs[key] = symbol.args[key]
    return Variable(**kwargs)


def _source_symbol(row: Row, column_id: ColumnId) -> Any:
    columns = row.column_values
    current: Optional[ColumnId] = column_id
    while current is not None:
        if current in columns:
            return columns[current]
        current = current.source_column_id
    target_lineage = _column_lineage(column_id)
    for row_column, symbol in columns.items():
        if not isinstance(row_column, ColumnId):
            continue
        row_lineage = _column_lineage(row_column)
        if any(
            _lineage_columns_match(target_column, source_column)
            for target_column in target_lineage
            for source_column in row_lineage
        ):
            return symbol
    return row[column_id]


def _column_lineage(column_id: ColumnId) -> Tuple[ColumnId, ...]:
    columns: List[ColumnId] = []
    current: Optional[ColumnId] = column_id
    while current is not None:
        columns.append(current)
        current = current.source_column_id
    return tuple(columns)


def _lineage_columns_match(left: ColumnId, right: ColumnId) -> bool:
    if left == right:
        return True
    return _relation_identity_key(left.relation) == _relation_identity_key(
        right.relation
    ) and _column_name_key(left) == _column_name_key(right)


def _relation_identity_key(relation: Optional[RelationId]) -> Tuple[Any, ...]:
    if relation is None:
        return ()
    return (
        relation.kind,
        relation.catalog.normalized if relation.catalog is not None else None,
        relation.db.normalized if relation.db is not None else None,
        relation.name.normalized if relation.name is not None else None,
    )


def _column_name_key(column_id: ColumnId) -> str:
    return "".join(
        char
        for char in column_id.name.normalized.casefold()
        if char.isalnum()
    )


def _materialize_column_from_row(
    column_id: ColumnId,
    row: Row,
    row_ids: Optional[Tuple[Any, ...]] = None,
) -> Any:
    return _copy_variable_for_column(
        column_id,
        _source_symbol(row, column_id),
        row.rowid if row_ids is None else row_ids,
    )


def _case_arm_condition(case_expr: exp.Case, arm_pred: exp.Expression) -> exp.Expression:
    if isinstance(case_expr.this, exp.Expression):
        return exp.EQ(this=case_expr.this.copy(), expression=arm_pred.copy())
    return arm_pred


def _row_bindings(row: Row) -> Dict[ColumnId, Any]:
    """Build ColumnId-keyed bindings from a row."""
    return {col_id: _symbol_value(symbol) for col_id, symbol in row.items()}


def _env_from_row(
    row: Row,
    outer_bindings: Optional[Dict[ColumnId, Any]] = None,
) -> Environment:
    """Build an Environment from a single row."""
    bindings: Dict[ColumnId, Any] = dict(outer_bindings) if outer_bindings else {}
    bindings.update(_row_bindings(row))
    return Environment(bindings)


def _env_from_join(
    source_row: Row,
    join_row: Row,
    outer_bindings: Optional[Dict[ColumnId, Any]] = None,
) -> Environment:
    """Build an Environment from two joined rows."""
    bindings: Dict[ColumnId, Any] = dict(outer_bindings) if outer_bindings else {}
    bindings.update(_row_bindings(source_row))
    bindings.update(_row_bindings(join_row))
    return Environment(bindings)


def _joined_row(source_row: Row, join_row: Row) -> Row:
    return Row(
        this=source_row.rowid + join_row.rowid,
        columns={
            **dict(source_row.items()),
            **dict(join_row.items()),
        },
    )


def _null_join_row(table: DerivedSchema, row_ids: Tuple[Any, ...]) -> Row:
    relation_id = None
    for col in table.columns:
        if isinstance(col, ColumnId) and col.relation is not None:
            relation_id = col.relation
            break
    return Row(
        this=(),
        columns={
            col_id: _derived_variable(col_id.name.normalized, None, row_ids, relation_id)
            for col_id in table.columns
        },
    )


def _step_relation_id(step: Step) -> Optional[RelationId]:
    """Get the RelationId from a step's output columns."""
    for col_id in getattr(step, "output_column_ids", ()):
        if isinstance(col_id, ColumnId) and col_id.relation is not None:
            return col_id.relation
    return None


def _aggregate_col_id(alias: str, relation_id: Optional[RelationId]) -> ColumnId:
    """Create a ColumnId for an aggregate output column."""
    return column_id(ColumnKind.AGGREGATE, identifier_name(alias), relation_id)


def _output_column_by_name(step: Step, name: str) -> Optional[ColumnId]:
    normalized = name
    for col_id in getattr(step, "output_column_ids", ()) or ():
        if isinstance(col_id, ColumnId) and col_id.name.normalized == normalized:
            return col_id
    return None


def _aggregate_output_col_id(
    step: Aggregate,
    alias: str,
    relation_id: Optional[RelationId],
) -> ColumnId:
    return _output_column_by_name(step, alias) or _aggregate_col_id(alias, relation_id)


def _aggregate_coverage_expressions(step: Aggregate) -> Tuple[exp.Expression, ...]:
    operands = {
        operand.alias_or_name: (
            operand.this if isinstance(operand, exp.Alias) else operand
        )
        for operand in (getattr(step, "operands", ()) or ())
        if operand.alias_or_name
    }

    def expand(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table:
            operand = operands.get(node.name)
            if operand is not None:
                return operand.copy()
        return node

    return tuple(
        aggregation.copy().transform(expand)
        for aggregation in step.aggregations
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
        # Strict runtime lookup requires planner identities to be prepared
        # before the first context is built or any expression is evaluated.
        self.plan.annotations

    def evaluate(self, tree: Optional[BranchTree] = None) -> BranchTree:
        if tree is None:
            tree = BranchTree()
        self.evaluate_context(tree)
        return tree

    def evaluate_context(self, tree: Optional[BranchTree] = None) -> Context:
        if tree is None:
            tree = BranchTree()
        ctx = build_context_from_instance(self.instance)
        output = self._walk(self.plan.root, ctx, tree)
        self._record_root_result(output, tree)
        return output

    def _record_root_result(self, output: Context, tree: BranchTree) -> None:
        root_node = next((node for node in tree.nodes if node.site == "root_result"), None)
        if root_node is None:
            return
        rows: List[Row] = []
        for table in output.tables.values():
            rows.extend(table.rows)
        if not rows:
            return
        tree.record_observation(
            root_node,
            AtomObservation(
                atom_id=0,
                outcome=BranchType.ATOM_TRUE,
                row_ids=_row_ids(rows[0]),
            ),
        )

    def _evaluate_subtree(
        self,
        root: Step,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> Context:
        ctx = build_context_from_instance(self.instance)
        return self._walk(root, ctx, BranchTree(), observe=False, outer_bindings=outer_bindings)

    def _find_upstream_branch_node(
        self, step: Step, tree: BranchTree
    ) -> Optional[Any]:
        """Find the nearest upstream BranchNode in the plan chain."""
        for dep in step.chain_dependencies:
            annotation = self.plan.annotation_for(dep)
            if annotation.step_id in tree.step_map:
                return tree.step_map[annotation.step_id]
            parent = self._find_upstream_branch_node(dep, tree)
            if parent is not None:
                return parent
        return None

    def _collect_upstream(self, step: Step) -> Tuple[Tuple[Any, ...], Tuple[Tuple[ColumnId, ColumnId], ...]]:
        """Collect path predicates and join equalities from upstream steps."""
        predicates: List[Any] = []
        join_eqs: List[Tuple[ColumnId, ColumnId]] = []
        visited: set = set()

        def walk(s: Step, is_target: bool) -> None:
            if id(s) in visited:
                return
            visited.add(id(s))
            if not is_target:
                cond = getattr(s, "condition", None)
                if isinstance(cond, exp.Expression):
                    predicates.append(cond)
            if isinstance(s, Join):
                for fact in _join_facts_for_step(self.plan, s):
                    join_eqs.extend(fact.equalities)
            for dep in s.chain_dependencies:
                walk(dep, False)

        for dep in step.chain_dependencies:
            walk(dep, False)
        return tuple(predicates), tuple(join_eqs)

    def _walk(
        self,
        step: Step,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
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
        output_key = step.relation_id or step.name
        if step.source is None or not isinstance(step.source, exp.Table):
            # For subquery scans, evaluate the SubPlan's inner plan.
            subplans = step.subplan_dependencies
            if subplans:
                subplan = subplans[0]
                inner_ctx = self._evaluate_subtree(subplan.inner)
                inner_rows = []
                for _name, table in inner_ctx.tables.items():
                    inner_rows.extend(table.rows)
                output_columns = tuple(getattr(step, "output_column_ids", ()) or ())
                if not output_columns and inner_rows:
                    output_columns = tuple(inner_rows[0].columns)
                return Context(
                    tables={
                        output_key: DerivedSchema(
                            columns=output_columns,
                            rows=[
                                Row(
                                    this=row.rowid,
                                    columns={
                                        column: _materialize_column_from_row(column, row)
                                        for column in output_columns
                                    },
                                )
                                for row in inner_rows
                            ],
                        )
                    }
                )
            table_name = step.name
            if table_name in ctx.tables:
                table = ctx.tables[table_name]
                output_columns = tuple(getattr(step, "output_column_ids", ()) or table.columns)
                return Context(
                    tables={
                        output_key: DerivedSchema(
                            columns=output_columns,
                            rows=[
                                Row(
                                    this=row.rowid,
                                    columns={
                                        column: _materialize_column_from_row(column, row)
                                        for column in output_columns
                                    },
                                )
                                for row in table.rows
                            ],
                        )
                    }
                )
            return Context(tables={output_key: DerivedSchema(columns=(), rows=[])})

        table_name = step.source.name
        if table_name not in ctx.tables:
            return Context(tables={output_key: DerivedSchema(columns=(), rows=[])})
        table = ctx.tables[table_name]
        output_columns = tuple(getattr(step, "output_column_ids", ()) or table.columns)
        return Context(
            tables={
                output_key: DerivedSchema(
                    columns=output_columns,
                    rows=[
                        Row(
                            this=row.rowid,
                            columns={
                                column: _materialize_column_from_row(column, row)
                                for column in output_columns
                            },
                        )
                        for row in table.rows
                    ],
                )
            }
        )

    def _eval_filter(
        self,
        step: Filter,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> Context:
        if step.condition is None:
            return ctx

        predicate = step.condition
        atoms = decompose_atoms(predicate) if observe else ()
        node = None
        scalar_nodes: List[Any] = []
        if observe:
            annotation = self.plan.annotation_for(step)
            parent_node = self._find_upstream_branch_node(step, tree)
            path_preds, join_eqs = self._collect_upstream(step)
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Filter",
                site="filter",
                predicate=predicate,
                atoms=atoms,
                tables=_annotation_relation_ids(annotation),
                step_obj=step,
                parent=parent_node,
                path_predicates=path_preds,
                join_equalities=join_eqs,
            )
            for subquery_atom in scalar_subquery_atoms(predicate):
                scalar_nodes.append(
                    tree.get_or_create_node(
                        step_id=f"{annotation.step_id}:scalar_subquery:{subquery_atom.sql()}",
                        step_type="Filter",
                        site="scalar_subquery",
                        predicate=subquery_atom,
                        atoms=(subquery_atom,),
                        tables=_annotation_relation_ids(annotation),
                        step_obj=step,
                        parent=parent_node,
                        path_predicates=path_preds,
                        join_equalities=join_eqs,
                    )
                )

        passing_rows: List[Row] = []
        for _, table in ctx.tables.items():
            for row in table.rows:
                row_bindings = _row_bindings(row)
                if outer_bindings:
                    row_bindings.update(outer_bindings)
                env = Environment(row_bindings)
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
                for scalar_node in scalar_nodes:
                    scalar_atom = scalar_node.atoms[0]
                    scalar_for_row = self._resolve_subquery_predicates(
                        scalar_atom,
                        step.subplan_dependencies,
                        row_bindings,
                        env,
                    )
                    tree.record_observation(
                        scalar_node,
                        AtomObservation(
                            atom_id=0,
                            outcome=_classify_outcome(concrete(scalar_for_row, env)),
                            row_ids=_row_ids(row),
                            concrete_values=_concrete_values(scalar_for_row, env),
                        ),
                    )
                # Full predicate for pass/fail.
                if predicate_value is True:
                    passing_rows.append(row)

        return Context(
            tables={
                name: table.with_rows(passing_rows, column_range=table.column_range)
                if passing_rows and len(passing_rows[0]) == len(table.columns)
                else table.with_rows(
                    passing_rows,
                    columns=tuple(passing_rows[0].columns) if passing_rows else table.columns,
                )
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
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> Context:
        input_ctx = ctx
        for join_name, join_data in (step.joins or {}).items():
            condition = join_data.get("condition")
            if condition is None or not isinstance(condition, exp.Expression):
                continue

            atoms = decompose_atoms(condition) if observe else ()
            node = None
            if observe:
                annotation = self.plan.annotation_for(step)
                parent_node = self._find_upstream_branch_node(step, tree)
                path_preds, join_eqs = self._collect_upstream(step)
                own_join_facts = _join_facts_for_step(self.plan, step)
                own_join_equalities = tuple(
                    equality
                    for fact in own_join_facts
                    for equality in fact.equalities
                )
                node = tree.get_or_create_node(
                    step_id=annotation.step_id,
                    step_type="Join",
                    site="join_on",
                    predicate=condition,
                    atoms=atoms,
                    tables=_annotation_relation_ids(annotation),
                    step_obj=step,
                    parent=parent_node,
                    path_predicates=path_preds,
                    join_equalities=tuple(join_eqs) + own_join_equalities,
                    join_facts=own_join_facts,
                )

            source_relation = step.source_relation
            source_table = (
                ctx.tables.get(source_relation)
                if source_relation is not None
                else None
            )
            join_table = input_ctx.tables.get(join_name)
            if source_table is None or join_table is None:
                continue

            side = str(join_data.get("side") or "").lower()
            preserves_source = side in {"left", "full"}
            preserves_join = side in {"right", "full"}

            def join_key_values(env: Environment) -> Tuple[Tuple[ColumnId, Any], ...]:
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
                    env = _env_from_join(source_row, join_row, outer_bindings)
                    joined_row_ids = _row_ids(source_row) + _row_ids(join_row)
                    keys_match, condition_matches, join_outcome = evaluate_join_pair(env)
                    record_join_pair(env, joined_row_ids, join_outcome)

                    if keys_match and condition_matches:
                        source_matched = True
                        matched_join_rows.add(join_index)
                        joined_rows.append(_joined_row(source_row, join_row))
                if preserves_source and not source_matched:
                    null_right = _null_join_row(join_table, _row_ids(source_row))
                    preserved = _joined_row(source_row, null_right)
                    joined_rows.append(preserved)
                    record_preserved_row(
                        preserved,
                        _env_from_join(source_row, null_right, outer_bindings),
                    )

            if preserves_join:
                for join_index, join_row in enumerate(join_table.rows):
                    if join_index in matched_join_rows:
                        continue
                    null_source = _null_join_row(source_table, _row_ids(join_row))
                    preserved = _joined_row(null_source, join_row)
                    joined_rows.append(preserved)
                    record_preserved_row(
                        preserved,
                        _env_from_join(null_source, join_row, outer_bindings),
                    )

            columns = tuple(joined_rows[0].columns) if joined_rows else (
                tuple(source_table.columns) + tuple(join_table.columns)
            )
            ctx = Context(
                tables={
                    (source_relation or step.name): DerivedSchema(columns=columns, rows=joined_rows),
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
        aggregate_node = None
        distinct_input_node = None
        if observe:
            annotation = self.plan.annotation_for(step)
            parent_node = self._find_upstream_branch_node(step, tree)
            path_preds, join_eqs = self._collect_upstream(step)
            # Use a synthetic "group_cardinality" atom for group-size branches.
            group_pred = exp.Literal.number(1)  # placeholder expression
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Aggregate",
                site="group",
                predicate=group_pred,
                atoms=(group_pred,),
                tables=_annotation_relation_ids(annotation),
                step_obj=step,
                parent=parent_node,
                path_predicates=path_preds,
                join_equalities=join_eqs,
            )
            aggregate_expressions = _aggregate_coverage_expressions(step)
            if aggregate_expressions:
                aggregate_node = tree.get_or_create_node(
                    step_id=annotation.step_id,
                    step_type="Aggregate",
                    site="aggregate_output",
                    predicate=exp.Literal.string("AGGREGATE_OUTPUT"),
                    atoms=aggregate_expressions,
                    tables=_annotation_relation_ids(annotation),
                    step_obj=step,
                    parent=parent_node,
                    path_predicates=path_preds,
                    join_equalities=join_eqs,
                )
                distinct_arguments = tuple(
                    argument.expressions[0]
                    for aggregation in aggregate_expressions
                    for function in aggregation.find_all(exp.AggFunc)
                    for argument in (function.this,)
                    if isinstance(argument, exp.Distinct) and argument.expressions
                )
                if distinct_arguments:
                    distinct_input_node = tree.get_or_create_node(
                        step_id=annotation.step_id,
                        step_type="Aggregate",
                        site="aggregate_distinct_input",
                        predicate=exp.Literal.string("AGGREGATE_DISTINCT_INPUT"),
                        atoms=distinct_arguments,
                        tables=_annotation_relation_ids(annotation),
                        step_obj=step,
                        parent=parent_node,
                        path_predicates=path_preds,
                        join_equalities=join_eqs,
                    )

        for table_name, table in ctx.tables.items():
            groups: Dict[tuple, int] = {}
            self._aggregation_metadata(step)
            if step.group:
                for row in table.rows:
                    env = _env_from_row(row)
                    key = tuple(concrete(g, env) for g in step.group.values())
                    groups[key] = groups.get(key, 0) + 1
                output_rows, aggregate_groups = self._grouped_aggregate_rows(
                    step,
                    list(table.rows),
                    table_name,
                )
            else:
                source_row_ids = tuple(row.rowid for row in table.rows)
                output_row_id = ("agg", step.name, "global")
                aggregate_values: Dict[Any, Any] = {}
                columns = {}
                relation_id = _step_relation_id(step)
                output_columns = self._aggregate_columns(step)
                for aggregate in step.aggregations:
                    alias = aggregate.alias_or_name
                    col_id = _aggregate_output_col_id(step, alias, relation_id)
                    value = self._aggregate_expression_value(
                        aggregate,
                        list(table.rows),
                        table_name,
                        operands=getattr(step, "operands", ()) or (),
                    )
                    aggregate_values[alias] = value
                    columns[col_id] = _derived_variable(
                        alias,
                        value,
                        output_row_id,
                        relation_id,
                        column_id_override=col_id,
                    )
                if table.rows:
                    source_row = table.rows[0]
                    for col_id in output_columns:
                        if col_id in columns:
                            continue
                        columns[col_id] = _materialize_column_from_row(
                            col_id,
                            source_row,
                            output_row_id,
                        )
                else:
                    for col_id in output_columns:
                        if col_id in columns:
                            continue
                        columns[col_id] = _derived_variable(
                            col_id.name.normalized,
                            None,
                            output_row_id,
                            col_id.relation,
                            column_id_override=col_id,
                        )
                output_rows = [Row(this=output_row_id, columns=columns)]
                aggregate_groups = {
                    output_row_id: AggregateGroup(
                        output_row_id=output_row_id,
                        group_key=(),
                        source_row_ids=source_row_ids,
                        aggregate_values=aggregate_values,
                    )
                }
                groups[((),)] = len(table.rows)

            if node is not None:
                for count in groups.values():
                    outcome = BranchType.GROUP_SINGLE if count == 1 else BranchType.GROUP_MULTI
                    tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))

            if aggregate_node is not None:
                rows_by_id = {_row_ids(row): row for row in table.rows}
                for output_row_id, group in aggregate_groups.items():
                    for atom_id, aggregation in enumerate(step.aggregations):
                        alias = aggregation.alias_or_name
                        value = group.aggregate_values.get(alias)
                        tree.record_observation(
                            aggregate_node,
                            AtomObservation(
                                atom_id=atom_id,
                                outcome=(
                                    BranchType.AGGREGATE_NULL
                                    if value is None
                                    else BranchType.AGGREGATE_NON_NULL
                                ),
                                row_ids=output_row_id,
                            ),
                        )
                    if distinct_input_node is not None:
                        source_rows = [
                            rows_by_id[row_id]
                            for row_id in group.source_row_ids
                            if row_id in rows_by_id
                        ]
                        for atom_id, argument in enumerate(distinct_input_node.atoms):
                            raw_values = [
                                concrete(argument, _env_from_row(row))
                                for row in source_rows
                            ]
                            non_null = [value for value in raw_values if value is not None]
                            outcomes = []
                            if len(non_null) < len(raw_values):
                                outcomes.append(BranchType.AGG_DISTINCT_NULL_IGNORED)
                            if len(non_null) != len(set(non_null)):
                                outcomes.append(
                                    BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED
                                )
                            if len(set(non_null)) >= 2:
                                outcomes.append(BranchType.AGG_DISTINCT_MULTIPLE_RETAINED)
                            for outcome in outcomes:
                                tree.record_observation(
                                    distinct_input_node,
                                    AtomObservation(
                                        atom_id=atom_id,
                                        outcome=outcome,
                                        row_ids=(*output_row_id, outcome.name),
                                    ),
                                )

            return Context(
                tables={
                    step.name: DerivedSchema(
                        columns=self._aggregate_columns(step),
                        rows=output_rows,
                        aggregate_groups=aggregate_groups,
                    )
                }
            )

        return Context(tables={step.name: DerivedSchema(columns=self._aggregate_columns(step), rows=[])})

    def _aggregation_metadata(self, step: Aggregate) -> Dict[str, Any]:
        try:
            return self.plan.annotation_for(step).metadata.get("aggregation", {})
        except (KeyError, ValueError):
            return {}

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
            parent_node = self._find_upstream_branch_node(step, tree)
            path_preds, join_eqs = self._collect_upstream(step)
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Having",
                site="having",
                predicate=predicate,
                atoms=atoms,
                tables=_annotation_relation_ids(annotation),
                step_obj=step,
                parent=parent_node,
                path_predicates=path_preds,
                join_equalities=join_eqs,
            )

        passing_rows: List[Row] = []
        columns: Tuple[ColumnId, ...] = ()
        for table_name, table in ctx.tables.items():
            columns = table.columns
            for row in table.rows:
                env = _env_from_row(row)
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

        for table in ctx.tables.values():
            return Context(tables={step.name: table.with_rows(passing_rows, columns=columns)})
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
            parent_node = self._find_upstream_branch_node(step, tree)
            path_preds, join_eqs = self._collect_upstream(step)
            project_expressions = tuple(
                projection
                for projection in step.projections
                if isinstance(projection, exp.Expression)
            )
            project_node = None
            if project_expressions:
                project_node = tree.get_or_create_node(
                    step_id=annotation.step_id,
                    step_type="Project",
                    site="project_output",
                    predicate=exp.Literal.string("PROJECT_OUTPUT"),
                    atoms=project_expressions,
                    tables=_annotation_relation_ids(annotation),
                    step_obj=step,
                    parent=parent_node,
                    path_predicates=path_preds,
                    join_equalities=join_eqs,
                )
                for table_name, table in ctx.tables.items():
                    output_ids = self._projected_columns(step, table.columns)
                    for row in table.rows:
                        projected = self._projected_values(step, row, table_name)
                        for atom_id, output_id in enumerate(output_ids):
                            if atom_id >= len(project_expressions):
                                break
                            value = _symbol_value(projected.get(output_id))
                            tree.record_observation(
                                project_node,
                                AtomObservation(
                                    atom_id=atom_id,
                                    outcome=(
                                        BranchType.PROJECT_NULL
                                        if value is None
                                        else BranchType.PROJECT_NON_NULL
                                    ),
                                    row_ids=_row_ids(row),
                                    concrete_values=((output_id, value),),
                                ),
                            )
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
                            tables=_annotation_relation_ids(annotation),
                            step_obj=step,
                            parent=parent_node,
                            path_predicates=path_preds,
                            join_equalities=join_eqs,
                        )

                        for table_name, table in ctx.tables.items():
                            for row in table.rows:
                                env = _env_from_row(row)
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
                    tables=_annotation_relation_ids(annotation),
                    step_obj=step,
                    parent=parent_node,
                    path_predicates=path_preds,
                    join_equalities=join_eqs,
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
                    key=lambda row: self._sort_key_value(expr, row),
                    reverse=descending,
                )
            output[step.name] = table.with_rows(rows)
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
            output[step.name] = table.with_rows(rows)
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

            output[step.name] = DerivedSchema(
                columns=visible_columns,
                rows=rows,
                datatypes=table.datatypes,
                nullables=table.nullables,
                uniqueness=table.uniqueness,
                aggregate_groups={
                    row.rowid: table.aggregate_groups[row.rowid]
                    for row in rows
                    if row.rowid in table.aggregate_groups
                },
                window_frames={
                    row.rowid: table.window_frames[row.rowid]
                    for row in rows
                    if row.rowid in table.window_frames
                },
            )
            break
        return Context(tables=output)

    def _projected_columns(self, step: Project, input_columns: Tuple[ColumnId, ...]) -> Tuple[ColumnId, ...]:
        output_ids = getattr(step, "output_column_ids", None)
        if output_ids and len(output_ids) == len(step.projections):
            return output_ids
        # Fallback: build from input columns
        columns: List[ColumnId] = []
        for projection in step.projections:
            if self._is_star_projection(projection):
                columns.extend(input_columns)
            else:
                name = projection.alias_or_name or projection.sql(dialect=self.dialect)
                relation_id = None
                for col in input_columns:
                    if isinstance(col, ColumnId) and col.relation is not None:
                        relation_id = col.relation
                        break
                columns.append(column_id(ColumnKind.PROJECTED, identifier_name(name), relation_id))
        return tuple(columns)

    def _projected_values(self, step: Project, row: Row, table_name: str) -> Dict[ColumnId, Any]:
        values: Dict[ColumnId, Any] = {}
        env = _env_from_row(row)
        output_ids = getattr(step, "output_column_ids", None)
        for proj_index, projection in enumerate(step.projections):
            if self._is_star_projection(projection):
                values.update(dict(row.items()))
                continue
            expr = projection.this if isinstance(projection, exp.Alias) else projection
            col_id = output_ids[proj_index] if output_ids and proj_index < len(output_ids) else None
            if col_id is None:
                name = projection.alias_or_name or projection.sql(dialect=self.dialect)
                relation_id = None
                for col in row.columns:
                    if isinstance(col, ColumnId) and col.relation is not None:
                        relation_id = col.relation
                        break
                col_id = column_id(ColumnKind.PROJECTED, identifier_name(name), relation_id)
            values[col_id] = self._projection_value(expr, row, env, col_id)
        return values

    def _projected_key(self, row: Row, visible_columns: Tuple[ColumnId, ...]) -> Tuple[Any, ...]:
        return tuple(_symbol_value(row[column]) for column in visible_columns)

    def _projection_value(self, expr: exp.Expression, row: Row, env: Environment, col_id: ColumnId) -> Any:
        if isinstance(expr, exp.Column) and expr in row:
            return row[expr]
        value = concrete(expr, env)
        return _derived_variable(col_id.name.normalized, value, _row_ids(row), col_id.relation)

    def _projection_name(self, projection: exp.Expression) -> str:
        return projection.alias_or_name or projection.sql(dialect=self.dialect)

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

        parent_node = self._find_upstream_branch_node(step, tree)
        path_preds, join_eqs = self._collect_upstream(step)
        node = tree.get_or_create_node(
            step_id=step_id,
            step_type="SubPlan",
            site="exists",
            predicate=step.anchor,
            atoms=(step.anchor,),
            tables=(),
            step_obj=step,
            parent=parent_node,
            path_predicates=path_preds,
            join_equalities=join_eqs,
        )

        observed_outer_row = False
        for table_name, table in ctx.tables.items():
            for row in table.rows:
                observed_outer_row = True
                outer_bindings = _row_bindings(row)
                has_rows = self._inner_plan_has_rows(step.inner, outer_bindings)
                outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
                tree.record_observation(
                    node,
                    AtomObservation(
                        atom_id=0,
                        outcome=outcome,
                        row_ids=_row_ids(row),
                        concrete_values=_concrete_values(step.anchor, _env_from_row(row)),
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
        outer_bindings: Dict[ColumnId, Any],
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
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
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
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> Any:
        rows, table_name, projects = self._eval_inner_plan(subplan.inner, outer_bindings)
        return self._project_scalar_value(projects, rows, outer_bindings)

    def _project_scalar_value(
        self,
        projects: List[Project],
        rows: List[Row],
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> Any:
        if not rows:
            return None

        if projects and projects[0].projections:
            projection = projects[0].projections[0]
            # Try to resolve by ColumnId from output_column_ids
            output_ids = getattr(projects[0], "output_column_ids", None)
            if output_ids and output_ids[0] in rows[0]:
                return _symbol_value(rows[0][output_ids[0]])
            alias = self._projection_name(projection)
            if alias in rows[0]:
                return _symbol_value(rows[0][alias])

            projection_expr = projection.this if isinstance(projection, exp.Alias) else projection
            env = _env_from_row(rows[0], outer_bindings)
            return concrete(projection_expr, env)

        if len(rows[0].columns) == 1:
            return _symbol_value(next(iter(dict(rows[0].items()).values())))

        return None

    def _grouped_aggregate_rows(
        self,
        step: Aggregate,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> Tuple[List[Row], Dict[Tuple[Any, ...], AggregateGroup]]:
        grouped: Dict[Tuple[Any, ...], List[Row]] = {}
        group_aliases = list(step.group)
        for row in rows:
            env = _env_from_row(row, outer_bindings)
            key = tuple(concrete(expr, env) for expr in step.group.values())
            grouped.setdefault(key, []).append(row)

        metadata = self._aggregation_metadata(step)
        relation_id = _step_relation_id(step)
        group_expressions = metadata.get("group_expressions", {})
        group_sources = metadata.get("group_sources", {})
        output_rows: List[Row] = []
        aggregate_groups: Dict[Tuple[Any, ...], AggregateGroup] = {}
        for group_index, (key, group_rows) in enumerate(grouped.items()):
            output_row_id = ("agg", step.name, group_index)
            source_row_ids = tuple(row.rowid for row in group_rows)
            columns: Dict[ColumnId, Any] = {}
            group_key_values: Dict[ColumnId, Any] = {}
            for alias, value in zip(group_aliases, key):
                col_id = _aggregate_output_col_id(step, alias, relation_id)
                group_key_values[col_id] = value
                columns[col_id] = _derived_variable(
                    alias,
                    value,
                    output_row_id,
                    relation_id,
                    column_id_override=col_id,
                )
            aggregate_values: Dict[Any, Any] = {}
            for aggregate in step.aggregations:
                alias = aggregate.alias_or_name
                value = self._aggregate_expression_value(
                    aggregate,
                    group_rows,
                    table_name,
                    outer_bindings,
                    getattr(step, "operands", ()) or (),
                )
                aggregate_values[alias] = value
                col_id = _aggregate_output_col_id(step, alias, relation_id)
                columns[col_id] = _derived_variable(
                    alias,
                    value,
                    output_row_id,
                    relation_id,
                    column_id_override=col_id,
                )
            for col_id in self._aggregate_columns(step):
                if col_id in columns:
                    continue
                columns[col_id] = _materialize_column_from_row(
                    col_id,
                    group_rows[0],
                    output_row_id,
                )
            output_rows.append(Row(this=output_row_id, columns=columns))
            aggregate_groups[output_row_id] = AggregateGroup(
                output_row_id=output_row_id,
                group_key=key,
                source_row_ids=source_row_ids,
                aggregate_values=aggregate_values,
                group_expressions={
                    col_id: group_expressions[col_id]
                    for col_id in group_key_values
                    if col_id in group_expressions
                },
                group_sources={
                    col_id: group_sources[col_id]
                    for col_id in group_key_values
                    if col_id in group_sources
                },
                group_key_values=group_key_values,
            )
        return output_rows, aggregate_groups

    def _aggregate_columns(self, step: Aggregate) -> Tuple[ColumnId, ...]:
        output_ids = getattr(step, "output_column_ids", None)
        if output_ids:
            return output_ids
        relation_id = _step_relation_id(step)
        cols = []
        for alias in step.group:
            cols.append(_aggregate_col_id(alias, relation_id))
        for aggregate in step.aggregations:
            cols.append(_aggregate_col_id(aggregate.alias_or_name, relation_id))
        return tuple(cols)

    def _sort_key_value(
        self,
        expr: exp.Expression,
        row: Row,
    ) -> Tuple[bool, Any]:
        if isinstance(expr, exp.Literal) and expr.is_string:
            key = str(expr.this)
            if key in row:
                value = _symbol_value(row[key])
                return value is None, value
        value = concrete(expr, _env_from_row(row))
        return value is None, value

    def _aggregate_expression_value(
        self,
        aggregate: exp.Expression,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
        operands: Tuple[exp.Expression, ...] = (),
    ) -> Any:
        operand_expr_by_alias = {
            operand.alias_or_name: (
                operand.this if isinstance(operand, exp.Alias) else operand
            )
            for operand in operands
        }

        aggregate_expr = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        aggregate_types = (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max)
        if isinstance(aggregate_expr, aggregate_types):
            return self._aggregate_function_value(
                aggregate_expr,
                rows,
                table_name,
                outer_bindings,
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
                operand_expr_by_alias,
            )
            return exp.convert(value)

        resolved = aggregate_expr.copy().transform(replace_aggregate)
        if rows:
            return concrete(resolved, _env_from_row(rows[0], outer_bindings))
        return concrete(resolved, Environment())

    def _aggregate_function_value(
        self,
        aggregate_expr: exp.Expression,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[ColumnId, Any]],
        operand_expr_by_alias: Dict[str, exp.Expression],
    ) -> Any:
        arg = aggregate_expr.this
        if isinstance(aggregate_expr, exp.Count):
            if isinstance(arg, exp.Star):
                return len(rows)
            if isinstance(arg, exp.Column):
                operand_expr = operand_expr_by_alias.get(arg.name)
                if isinstance(operand_expr, exp.Star):
                    return len(rows)

        resolved_arg = arg
        if isinstance(arg, exp.Column):
            resolved_arg = operand_expr_by_alias.get(arg.name, arg)
        is_distinct = isinstance(resolved_arg, exp.Distinct)
        if is_distinct:
            if not resolved_arg.expressions:
                raw_values = []
            else:
                value_expression = resolved_arg.expressions[0]
                raw_values = [
                    concrete(value_expression, _env_from_row(row, outer_bindings))
                    for row in rows
                ]
        else:
            raw_values = [
                concrete(resolved_arg, _env_from_row(row, outer_bindings))
                for row in rows
            ]
        non_null_values = [value for value in raw_values if value is not None]
        if is_distinct:
            non_null_values = list(dict.fromkeys(non_null_values))

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
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> bool:
        """Check whether an inner plan would produce at least one row."""
        rows, _, _ = self._eval_inner_plan(root, outer_bindings)
        return len(rows) > 0

    def _inner_plan_values(
        self,
        root: Step,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> set:
        """Evaluate inner plan and return the set of projected column values."""
        rows, table_name, projects = self._eval_inner_plan(root, outer_bindings)
        if not rows:
            return set()

        values: set = set()
        projection = projects[0].projections[0] if projects and projects[0].projections else None
        for row in rows:
            if projection is not None:
                # Try ColumnId from output_column_ids first
                output_ids = getattr(projects[0], "output_column_ids", None)
                if output_ids and output_ids[0] in row:
                    values.add(_symbol_value(row[output_ids[0]]))
                    continue
                alias = self._projection_name(projection)
                if alias in row:
                    values.add(_symbol_value(row[alias]))
                    continue
                projection_expr = projection.this if isinstance(projection, exp.Alias) else projection
            elif len(row.columns) == 1:
                values.add(_symbol_value(next(iter(dict(row.items()).values()))))
                continue
            else:
                continue

            env = _env_from_row(row, outer_bindings)
            val = concrete(projection_expr, env)
            values.add(val)
        return values

    def _eval_in_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate col IN (SELECT ...) and record IN_MATCH/IN_NO_MATCH."""
        annotation = self.plan.annotation_for(step)
        step_id = annotation.step_id

        parent_node = self._find_upstream_branch_node(step, tree)
        path_preds, join_eqs = self._collect_upstream(step)
        node = tree.get_or_create_node(
            step_id=step_id,
            step_type="SubPlan",
            site="in",
            predicate=step.anchor,
            atoms=(step.anchor,),
            tables=(),
            step_obj=step,
            parent=parent_node,
            path_predicates=path_preds,
            join_equalities=join_eqs,
        )

        # Check each outer row against the inner result set.
        if isinstance(step.anchor, exp.In):
            outer_col = step.anchor.this
            if isinstance(outer_col, exp.Column):
                for table_name, table in ctx.tables.items():
                    for row in table.rows:
                        outer_bindings = _row_bindings(row)
                        inner_values = self._inner_plan_values(step.inner, outer_bindings)
                        env = _env_from_row(row)
                        outer_val = _symbol_value(row[outer_col])

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


def build_branch_tree(
    plan: Plan,
    instance: Instance,
    thresholds: Optional[Any] = None,
) -> BranchTree:
    """Build a BranchTree hierarchy from a Plan without running evaluation.

    Constructs the full tree structure — nodes, parent/child links, cached
    path_predicates and join_equalities — but no observations. The evaluator
    populates observations by calling ``tree.record_observation`` during
    evaluation.
    """
    from .types import BranchTree, CoverageThresholds

    tree = BranchTree(
        thresholds=thresholds or CoverageThresholds(),
    )

    # Ensure plan annotations are computed (needed for step_id, tables).
    if getattr(plan, "_instance", None) is not instance:
        plan._instance = instance
        plan._annotations = None

    # Index: step_id → BranchNode for parent lookup.
    step_nodes: Dict[str, Any] = {}
    scan_columns_by_relation: Dict[RelationId, Tuple[ColumnId, ...]] = {}
    storage_by_relation: Dict[RelationId, RelationId] = {}

    def _storage_table_key(relation: RelationId) -> str | None:
        try:
            return instance._table_key_for_storage(relation)
        except Exception:
            return None

    for step in plan.ordered_steps:
        plan.annotation_for(step)

    for step in plan.ordered_steps:
        if not isinstance(step, Scan) or getattr(step, "relation_id", None) is None:
            continue
        output_columns = tuple(getattr(step, "output_column_ids", ()) or ())
        scan_columns_by_relation[step.relation_id] = output_columns
        storage_relation = None
        for column in output_columns:
            source = column.source_column_id or column
            if source.relation is not None and source.relation.name is not None:
                if _storage_table_key(source.relation) in instance.tables:
                    storage_relation = source.relation
                    break
        if storage_relation is None and step.relation_id.name is not None:
            if _storage_table_key(step.relation_id) in instance.tables:
                storage_relation = step.relation_id
        if storage_relation is not None:
            storage_by_relation[step.relation_id] = storage_relation

    def _find_parent(step: Step) -> Optional[Any]:
        """Find the nearest upstream step that has a BranchNode."""
        for dep in step.chain_dependencies:
            ann = plan.annotation_for(dep)
            if ann.step_id in step_nodes:
                return step_nodes[ann.step_id]
            parent = _find_parent(dep)
            if parent is not None:
                return parent
        return None

    def _collect_upstream(step: Step) -> Tuple[Tuple[Any, ...], Tuple[Tuple[ColumnId, ColumnId], ...]]:
        predicates: List[Any] = []
        join_eqs: List[Tuple[ColumnId, ColumnId]] = []
        visited: set = set()

        def walk(s: Step, is_target: bool) -> None:
            if id(s) in visited:
                return
            visited.add(id(s))
            if not is_target:
                cond = getattr(s, "condition", None)
                if isinstance(cond, exp.Expression):
                    predicates.append(cond)
            if isinstance(s, Join):
                for fact in _join_facts_for_step(plan, s):
                    join_eqs.extend(fact.equalities)
            for dep in s.chain_dependencies:
                walk(dep, False)

        for dep in step.chain_dependencies:
            walk(dep, False)
        return tuple(predicates), tuple(join_eqs)

    def _scan_obligations(
        step_id: str,
        tables: Tuple[RelationId, ...],
        row_count: int = 1,
        keyed_only: bool = False,
    ) -> Tuple[OperatorObligation, ...]:
        obligations: List[OperatorObligation] = []
        for relation in tables:
            storage_relation = storage_by_relation.get(relation)
            table_name = _storage_table_key(storage_relation or relation)
            if table_name is None or table_name not in instance.tables:
                continue
            columns = scan_columns_by_relation.get(relation, ())
            if not columns:
                columns = tuple(
                    physical_column(column_name, relation, dialect=getattr(instance, "dialect", None))
                    for column_name in instance.tables[table_name]
                )
            if keyed_only:
                def key_name(value: Any) -> str:
                    return str(getattr(value, "name", value)).lower()

                key_names = {
                    key_name(key)
                    for key in instance.primary_keys.get(table_name, ())
                }
                for unique_columns in instance.unique_constraints.get(table_name, ()):
                    key_names.update(key_name(key) for key in unique_columns)
                columns = tuple(
                    column for column in columns
                    if column.name.normalized.lower() in key_names
                )
                if not columns:
                    continue
            obligations.append(
                OperatorObligation(
                    kind="scan_exists",
                    step_id=step_id,
                    site="scan",
                    relation=relation,
                    storage_relation=storage_relation,
                    columns=columns,
                    row_count=row_count,
                )
            )
        return tuple(obligations)

    def _lineage_relations(step: Step) -> Tuple[RelationId, ...]:
        relations: List[RelationId] = []
        seen: set[RelationId] = set()

        def add_relation(relation: RelationId | None) -> None:
            if relation is None:
                return
            for alias_relation, storage_relation in storage_by_relation.items():
                if storage_relation == relation and alias_relation not in seen:
                    seen.add(alias_relation)
                    relations.append(alias_relation)
                    return
            if relation not in seen:
                seen.add(relation)
                relations.append(relation)

        def add_column(column: ColumnId) -> None:
            source = column.source_column_id or column
            add_relation(source.relation or column.relation)

        def walk_step(s: Step, visited: set[int]) -> None:
            if id(s) in visited:
                return
            visited.add(id(s))
            for expr_value in (
                getattr(s, "condition", None),
                *tuple(getattr(s, "projections", ()) or ()),
                *tuple(getattr(s, "order", ()) or ()),
                *tuple((getattr(s, "group", {}) or {}).values()),
                *tuple(getattr(s, "aggregations", ()) or ()),
            ):
                if not isinstance(expr_value, exp.Expression):
                    continue
                for col in expr_value.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id is not None:
                        add_column(col_id)
            if isinstance(s, Join):
                for join_data in (s.joins or {}).values():
                    for expr_value in (
                        *tuple(join_data.get("source_key", ()) or ()),
                        *tuple(join_data.get("join_key", ()) or ()),
                        join_data.get("condition"),
                    ):
                        if not isinstance(expr_value, exp.Expression):
                            continue
                        for col in expr_value.find_all(exp.Column):
                            col_id = column_identity(col)
                            if col_id is not None:
                                add_column(col_id)
            for dep in s.chain_dependencies:
                walk_step(dep, visited)

        for column in tuple(getattr(step, "output_column_ids", ()) or ()):
            add_column(column)
        walk_step(step, set())
        return tuple(relations)

    def _canonical_relations(relations: Tuple[RelationId, ...]) -> Tuple[RelationId, ...]:
        canonical: List[RelationId] = []
        seen: set[RelationId] = set()
        for relation in relations:
            mapped = relation
            if relation not in storage_by_relation:
                for alias_relation, storage_relation in storage_by_relation.items():
                    if storage_relation == relation:
                        mapped = alias_relation
                        break
            if mapped in seen:
                continue
            seen.add(mapped)
            canonical.append(mapped)
        return tuple(canonical)

    def _add_node(
        step: Step,
        step_type: str,
        site: str,
        predicate: exp.Expression,
        atoms: Tuple[exp.Expression, ...],
        tables: Tuple[RelationId, ...],
    ) -> None:
        annotation = plan.annotation_for(step)
        parent_node = _find_parent(step)
        path_preds, join_eqs = _collect_upstream(step)
        join_facts = _join_facts_for_step(plan, step) if isinstance(step, Join) else ()
        own_join_equalities = tuple(
            equality
            for fact in join_facts
            for equality in fact.equalities
        )
        node = tree.get_or_create_node(
            step_id=annotation.step_id,
            step_type=step_type,
            site=site,
            predicate=predicate,
            atoms=atoms,
            tables=tables,
            step_obj=step,
            parent=parent_node,
            path_predicates=path_preds,
            join_equalities=tuple(join_eqs) + own_join_equalities,
            join_facts=join_facts,
            obligations=_scan_obligations(annotation.step_id, tables, keyed_only=True),
        )
        step_nodes.setdefault(annotation.step_id, node)

    def _root_required_row_count(root: Step) -> int:
        max_root_rows = 20
        if isinstance(root, Limit):
            offset = max(int(getattr(root, "offset", 0) or 0), 0)
            limit = getattr(root, "limit", 1)
            limit_value = 1 if limit == float("inf") else max(int(limit or 0), 1)
            return min(max(offset + limit_value, 1), max_root_rows)
        return 1

    def _root_obligations(root: Step) -> Tuple[OperatorObligation, ...]:
        annotation = plan.annotation_for(root)
        row_count = _root_required_row_count(root)
        root_relations = _canonical_relations(
            annotation.source_relations + _lineage_relations(root)
        )
        obligations: List[OperatorObligation] = [
            OperatorObligation(
                kind="root_result",
                step_id=annotation.step_id,
                site="root_result",
                row_count=row_count,
            )
        ]
        return tuple(obligations) + _scan_obligations(
            annotation.step_id,
            root_relations,
            row_count=row_count,
        )

    for step in plan.ordered_steps:
        annotation = plan.annotation_for(step)
        tables = annotation.source_relations

        if isinstance(step, Filter) and step.condition is not None:
            atoms = decompose_atoms(step.condition)
            _add_node(step, "Filter", "filter", step.condition, atoms, tables)
            for atom in scalar_subquery_atoms(step.condition):
                _add_node(step, "Filter", "scalar_subquery", atom, (atom,), tables)

        elif isinstance(step, Join):
            for join_name, join_data in (step.joins or {}).items():
                condition = join_data.get("condition")
                if condition is not None and isinstance(condition, exp.Expression):
                    atoms = decompose_atoms(condition)
                    _add_node(step, "Join", "join_on", condition, atoms, tables)

        elif isinstance(step, Aggregate):
            if step.group or step.aggregations:
                group_pred = exp.Literal.number(1)
                _add_node(step, "Aggregate", "group", group_pred, (group_pred,), tables)
            if step.aggregations:
                aggregate_expressions = _aggregate_coverage_expressions(step)
                _add_node(
                    step,
                    "Aggregate",
                    "aggregate_output",
                    exp.Literal.string("AGGREGATE_OUTPUT"),
                    aggregate_expressions,
                    tables,
                )
                distinct_arguments = tuple(
                    argument.expressions[0]
                    for aggregation in aggregate_expressions
                    for function in aggregation.find_all(exp.AggFunc)
                    for argument in (function.this,)
                    if isinstance(argument, exp.Distinct) and argument.expressions
                )
                if distinct_arguments:
                    _add_node(
                        step,
                        "Aggregate",
                        "aggregate_distinct_input",
                        exp.Literal.string("AGGREGATE_DISTINCT_INPUT"),
                        distinct_arguments,
                        tables,
                    )

        elif isinstance(step, Having) and step.condition is not None:
            atoms = decompose_atoms(step.condition)
            _add_node(step, "Having", "having", step.condition, atoms, tables)

        elif isinstance(step, Project):
            project_expressions = tuple(
                projection
                for projection in step.projections
                if isinstance(projection, exp.Expression)
            )
            if project_expressions:
                _add_node(
                    step,
                    "Project",
                    "project_output",
                    exp.Literal.string("PROJECT_OUTPUT"),
                    project_expressions,
                    tables,
                )
            for projection in step.projections:
                if not isinstance(projection, exp.Expression):
                    continue
                for case_expr in projection.find_all(exp.Case):
                    ifs = case_expr.args.get("ifs") or []
                    for arm in ifs:
                        raw_arm_pred = arm.args.get("this")
                        if not isinstance(raw_arm_pred, exp.Expression):
                            continue
                        arm_pred = _case_arm_condition(case_expr, raw_arm_pred)
                        atoms = decompose_atoms(arm_pred)
                        _add_node(step, "Project", "case_arm", arm_pred, atoms, tables)
            if step.distinct:
                dist_pred = exp.Literal.string("DISTINCT")
                _add_node(step, "Project", "distinct", dist_pred, (dist_pred,), tables)

        elif isinstance(step, SubPlan):
            if step.kind is SubPlanKind.EXISTS:
                _add_node(step, "SubPlan", "exists", step.anchor, (step.anchor,), ())
            elif step.kind is SubPlanKind.IN:
                _add_node(step, "SubPlan", "in", step.anchor, (step.anchor,), ())

    root_annotation = plan.annotation_for(plan.root)
    root_obligations = _root_obligations(plan.root)
    if root_obligations:
        tree.get_or_create_node(
            step_id=f"{root_annotation.step_id}:root_result",
            step_type=type(plan.root).__name__,
            site="root_result",
            predicate=exp.true(),
            atoms=(exp.true(),),
            tables=_canonical_relations(
                root_annotation.source_relations + _lineage_relations(plan.root)
            ),
            step_obj=plan.root,
            parent=_find_parent(plan.root),
            obligations=root_obligations,
        )

    return tree


__all__ = ["PlanEvaluator", "build_branch_tree", "decompose_atoms"]
