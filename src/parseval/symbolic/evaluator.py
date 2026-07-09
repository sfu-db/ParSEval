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
    RangeReader,
    Row,
    RowReader,
    build_context_from_instance,
)
from parseval.plan.rex import Const, Environment, Variable, concrete, column_meta
from parseval.instance import Instance

from .branch_tree import (
    BranchCoverageRecorder,
    BranchTreeBuilder,
    _aggregate_coverage_expressions,
    _case_arm_condition,
    _join_facts_for_step,
    _subquery_paths_for_atom,
    decompose_atoms,
    project_coverage_expressions,
    project_coverage_items,
    scalar_subquery_atoms,
)
from .types import (
    AtomObservation,
    BranchTree,
    BranchType,
    JoinFact,
    OperatorObligation,
    SubqueryPath,
)

_SUBPLAN_ANCHOR_ID = "parseval_subplan_anchor_id"

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


def _outer_environment(outer_bindings: Optional[Any] = None) -> Optional[Environment]:
    if outer_bindings is None:
        return None
    if isinstance(outer_bindings, Environment):
        return outer_bindings
    return Environment(outer_bindings)


def _reader_for_row(row: Row) -> RowReader:
    reader = RowReader(row.columns)
    reader.row = row
    return reader


def _env_from_row(
    row: Row,
    outer_bindings: Optional[Any] = None,
) -> Environment:
    """Build a reader-backed Environment from a single row."""
    return Environment.from_readers(
        {"row": _reader_for_row(row)},
        outer=_outer_environment(outer_bindings),
    )


def _env_from_join(
    source_row: Row,
    join_row: Row,
    outer_bindings: Optional[Any] = None,
) -> Environment:
    """Build a reader-backed Environment from two joined rows."""
    return Environment.from_readers(
        {
            "source": _reader_for_row(source_row),
            "join": _reader_for_row(join_row),
        },
        outer=_outer_environment(outer_bindings),
    )


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
    aggregate_index: int | None = None,
) -> ColumnId:
    if aggregate_index is not None:
        aggregate_columns = [
            col_id
            for col_id in getattr(step, "output_column_ids", ()) or ()
            if isinstance(col_id, ColumnId) and col_id.kind is ColumnKind.AGGREGATE
        ]
        if aggregate_index < len(aggregate_columns):
            return aggregate_columns[aggregate_index]
    return _output_column_by_name(step, alias) or _aggregate_col_id(alias, relation_id)


def _row_value_tuple(row: Row, columns: Tuple[Any, ...]) -> Tuple[Any, ...]:
    return tuple(_symbol_value(row[column]) for column in columns)


def _set_output_row(
    row: Row,
    source_columns: Tuple[Any, ...],
    output_columns: Tuple[Any, ...],
    row_prefix: str,
) -> Row:
    row_id = (row_prefix,) + _row_ids(row)
    values = _row_value_tuple(row, source_columns)
    return Row(
        this=row_id,
        columns={
            output_column: _derived_variable(
                (
                    output_column.name.normalized
                    if isinstance(output_column, ColumnId)
                    else str(output_column)
                ),
                values[index] if index < len(values) else None,
                row_id,
                output_column.relation if isinstance(output_column, ColumnId) else None,
                column_id_override=output_column if isinstance(output_column, ColumnId) else None,
            )
            for index, output_column in enumerate(output_columns)
        },
    )


def _sql_tuple_membership(
    outer_value: Tuple[Any, ...],
    inner_values: Tuple[Tuple[Any, ...], ...],
) -> Optional[bool]:
    if any(value is None for value in outer_value):
        return None
    saw_unknown = False
    for inner_value in inner_values:
        if len(inner_value) != len(outer_value):
            continue
        candidate_unknown = False
        candidate_false = False
        for left, right in zip(outer_value, inner_value):
            if right is None:
                candidate_unknown = True
            elif left != right:
                candidate_false = True
                break
        if candidate_false:
            continue
        if candidate_unknown:
            saw_unknown = True
            continue
        return True
    return None if saw_unknown else False


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
        self._uncorrelated_scalar_cache: Dict[int, Any] = {}
        self._uncorrelated_predicate_cache: Dict[int, exp.Expression] = {}
        self._coverage_recorder: Optional[BranchCoverageRecorder] = None

    def evaluate(self, tree: Optional[BranchTree] = None) -> BranchTree:
        if tree is None:
            tree = BranchTreeBuilder(self.plan, self.instance).build()
        self.evaluate_context(tree)
        return tree

    def evaluate_context(self, tree: Optional[BranchTree] = None) -> Context:
        if tree is None:
            tree = BranchTreeBuilder(self.plan, self.instance).build()
        self._coverage_recorder = BranchCoverageRecorder(tree)
        self._uncorrelated_scalar_cache = {}
        self._uncorrelated_predicate_cache = {}
        ctx = build_context_from_instance(self.instance)
        output = self._walk(self.plan.root, ctx, tree)
        self._record_root_result(output, tree)
        return output

    def _runtime_node(self, **kwargs: Any):
        if self._coverage_recorder is None:
            raise RuntimeError("coverage_recorder_not_initialized")
        return self._coverage_recorder.runtime_node(**kwargs)

    def _observe(self, node, observation: AtomObservation) -> None:
        if self._coverage_recorder is None:
            raise RuntimeError("coverage_recorder_not_initialized")
        self._coverage_recorder.observe(node, observation)

    def _record_root_result(self, output: Context, tree: BranchTree) -> None:
        root_node = next((node for node in tree.nodes if node.site == "root_result"), None)
        if root_node is None:
            return
        rows: List[Row] = []
        for table in output.tables.values():
            rows.extend(table.rows)
        seen_values: dict[Tuple[Any, ...], Tuple[Any, ...]] = {}
        for row in rows:
            output_values = tuple(_symbol_value(symbol) for _column, symbol in row.items())
            previous_row_ids = seen_values.get(output_values)
            if previous_row_ids is None:
                seen_values[output_values] = _row_ids(row)
            else:
                self._observe(
                    root_node,
                    AtomObservation(
                        atom_id=0,
                        outcome=BranchType.DUPLICATE,
                        row_ids=_row_ids(row),
                    ),
                )
                tree.record_operator_trace(
                    root_node,
                    outcome=BranchType.DUPLICATE,
                    input_row_ids=(previous_row_ids, _row_ids(row)),
                    output_row_ids=(previous_row_ids, _row_ids(row)),
                )
            self._observe(
                root_node,
                AtomObservation(
                    atom_id=0,
                    outcome=BranchType.ATOM_TRUE,
                    row_ids=_row_ids(row),
                ),
            )
            tree.record_row_lineage(
                step_id=root_node.step_id,
                site="root_result",
                output_row_ids=_row_ids(row),
                source_row_ids=(_row_ids(row),),
                relations=root_node.tables,
            )
            tree.record_operator_trace(
                root_node,
                outcome=BranchType.ATOM_TRUE,
                input_row_ids=(_row_ids(row),),
                output_row_ids=(_row_ids(row),),
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
        if isinstance(step, SetOperation):
            dep_contexts = [
                (
                    dep,
                    self._walk(
                        dep,
                        ctx,
                        tree,
                        observe=observe,
                        outer_bindings=outer_bindings,
                    ),
                )
                for dep in step.chain_dependencies
            ]
            return self._eval_set_operation(
                step,
                dep_contexts,
                tree,
                observe=observe,
            )

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
            return self._eval_scan(step, ctx, tree, observe=observe)
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
            return self._eval_sort(step, input_ctx, tree, observe=observe)
        elif isinstance(step, Limit):
            return self._eval_limit(step, input_ctx, tree, observe=observe)
        return input_ctx

    def _step_id(self, step: Step) -> str:
        return self.plan.annotation_for(step).step_id

    def _record_row_flow(
        self,
        tree: BranchTree,
        step: Step,
        site: str,
        row: Row,
        *,
        sources: Tuple[Tuple[Any, ...], ...] = (),
        relations: Tuple[RelationId, ...] = (),
    ) -> None:
        tree.record_row_lineage(
            step_id=self._step_id(step),
            site=site,
            output_row_ids=_row_ids(row),
            source_row_ids=sources,
            relations=relations,
        )

    def _eval_scan(
        self,
        step: Scan,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
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
                rows = [
                    Row(
                        this=row.rowid,
                        columns={
                            column: _materialize_column_from_row(column, row)
                            for column in output_columns
                        },
                    )
                    for row in inner_rows
                ]
                if observe:
                    for row in rows:
                        self._record_row_flow(
                            tree,
                            step,
                            "derived_scan",
                            row,
                            sources=(_row_ids(row),),
                            relations=(step.relation_id,) if step.relation_id is not None else (),
                        )
                return Context(
                    tables={
                        output_key: DerivedSchema(
                            columns=output_columns,
                            rows=rows,
                        )
                    }
                )
            table_name = step.name
            if table_name in ctx.tables:
                table = ctx.tables[table_name]
                output_columns = tuple(getattr(step, "output_column_ids", ()) or table.columns)
                rows = [
                    Row(
                        this=row.rowid,
                        columns={
                            column: _materialize_column_from_row(column, row)
                            for column in output_columns
                        },
                    )
                    for row in table.rows
                ]
                if observe:
                    for row in rows:
                        self._record_row_flow(
                            tree,
                            step,
                            "scan",
                            row,
                            sources=(_row_ids(row),),
                            relations=(step.relation_id,) if step.relation_id is not None else (),
                        )
                return Context(
                    tables={
                        output_key: DerivedSchema(
                            columns=output_columns,
                            rows=rows,
                        )
                    }
                )
            return Context(tables={output_key: DerivedSchema(columns=(), rows=[])})

        table_name = step.source.name
        cte_subplan = next(
            (
                subplan
                for subplan in step.subplan_dependencies
                if subplan.kind is SubPlanKind.CTE
            ),
            None,
        )
        if cte_subplan is not None:
            inner_ctx = self._evaluate_subtree(cte_subplan.inner)
            inner_rows = []
            for _name, table in inner_ctx.tables.items():
                inner_rows.extend(table.rows)
            output_columns = tuple(getattr(step, "output_column_ids", ()) or ())
            if not output_columns and inner_rows:
                output_columns = tuple(inner_rows[0].columns)
            rows = [
                Row(
                    this=row.rowid,
                    columns={
                        column: _materialize_column_from_row(column, row)
                        for column in output_columns
                    },
                )
                for row in inner_rows
            ]
            if observe:
                for row in rows:
                    self._record_row_flow(
                        tree,
                        step,
                        "cte_scan",
                        row,
                        sources=(_row_ids(row),),
                        relations=(step.relation_id,) if step.relation_id is not None else (),
                    )
            return Context(
                tables={
                    output_key: DerivedSchema(
                        columns=output_columns,
                        rows=rows,
                    )
                }
            )
        if table_name not in ctx.tables:
            return Context(tables={output_key: DerivedSchema(columns=(), rows=[])})
        table = ctx.tables[table_name]
        output_columns = tuple(getattr(step, "output_column_ids", ()) or table.columns)
        rows = [
            Row(
                this=row.rowid,
                columns={
                    column: _materialize_column_from_row(column, row)
                    for column in output_columns
                },
            )
            for row in table.rows
        ]
        if observe:
            for row in rows:
                self._record_row_flow(
                    tree,
                    step,
                    "scan",
                    row,
                    sources=(_row_ids(row),),
                    relations=(step.relation_id,) if step.relation_id is not None else (),
                )
        return Context(
            tables={
                output_key: DerivedSchema(
                    columns=output_columns,
                    rows=rows,
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
            node = self._runtime_node(
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
            node.subqueries = tuple(
                subquery
                for atom in scalar_subquery_atoms(predicate)
                for subquery in _subquery_paths_for_atom(
                    node,
                    atom,
                    step.subplan_dependencies,
                )
            )
            for subquery_atom in scalar_subquery_atoms(predicate):
                scalar_node = self._runtime_node(
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
                scalar_node.subqueries = _subquery_paths_for_atom(
                    scalar_node,
                    subquery_atom,
                    step.subplan_dependencies,
                )
                scalar_nodes.append(scalar_node)

        passing_rows: List[Row] = []
        has_subplans = bool(step.subplan_dependencies)
        for _, table in ctx.tables.items():
            reader = RowReader(table.columns)
            for row in table.rows:
                reader.row = row
                env = Environment.from_readers(
                    {"row": reader},
                    outer=_outer_environment(outer_bindings),
                )
                predicate_for_row = (
                    self._resolve_subquery_predicates(
                        predicate,
                        step.subplan_dependencies,
                        env,
                        env,
                    )
                    if has_subplans
                    else predicate
                )
                predicate_value = concrete(predicate_for_row, env)
                # Record per-atom observations.
                for atom_id, atom in enumerate(atoms):
                    atom_for_row = (
                        self._resolve_subquery_predicates(
                            atom,
                            step.subplan_dependencies,
                            env,
                            env,
                        )
                        if has_subplans
                        else atom
                    )
                    outcome = _try_early_classify(atom)
                    if outcome is None:
                        value = concrete(atom_for_row, env)
                        outcome = _classify_outcome(value)
                    if node is not None:
                        self._observe(
                            node,
                            AtomObservation(
                                atom_id=atom_id,
                                outcome=outcome,
                                row_ids=_row_ids(row),
                                concrete_values=_concrete_values(atom_for_row, env),
                            ),
                        )
                if node is not None:
                    self._observe(
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
                        env,
                        env,
                    )
                    self._observe(
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
                    if node is not None:
                        tree.record_row_lineage(
                            step_id=node.step_id,
                            site="filter",
                            output_row_ids=_row_ids(row),
                            source_row_ids=(_row_ids(row),),
                            relations=node.tables,
                        )
                        tree.record_operator_trace(
                            node,
                            outcome=BranchType.FILTER_TRUE,
                            input_row_ids=(_row_ids(row),),
                            output_row_ids=(_row_ids(row),),
                            concrete_values=_concrete_values(predicate_for_row, env),
                        )
                elif node is not None:
                    tree.record_operator_trace(
                        node,
                        outcome=_classify_filter_outcome(predicate_value),
                        input_row_ids=(_row_ids(row),),
                        concrete_values=_concrete_values(predicate_for_row, env),
                    )

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
                node = self._runtime_node(
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
            source_reader_cache: Dict[int, RowReader] = {}
            join_reader_cache: Dict[int, RowReader] = {}

            def cached_reader(row: Row, cache: Dict[int, RowReader]) -> RowReader:
                key = id(row)
                reader = cache.get(key)
                if reader is None:
                    reader = _reader_for_row(row)
                    cache[key] = reader
                return reader

            def env_for_source(row: Row) -> Environment:
                return Environment.from_readers(
                    {"source": cached_reader(row, source_reader_cache)},
                    outer=_outer_environment(outer_bindings),
                )

            def env_for_join_row(row: Row) -> Environment:
                return Environment.from_readers(
                    {"join": cached_reader(row, join_reader_cache)},
                    outer=_outer_environment(outer_bindings),
                )

            def env_for_pair(source_row: Row, join_row: Row) -> Environment:
                return Environment.from_readers(
                    {
                        "source": cached_reader(source_row, source_reader_cache),
                        "join": cached_reader(join_row, join_reader_cache),
                    },
                    outer=_outer_environment(outer_bindings),
                )

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
                self._observe(
                    node,
                    AtomObservation(
                        atom_id=-1,
                        outcome=BranchType.JOIN_MATCH,
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
                    self._observe(
                        node,
                        AtomObservation(
                            atom_id=atom_id,
                            outcome=atom_outcome,
                            row_ids=row_ids,
                            concrete_values=_concrete_values(atom, env),
                        ),
                    )
                branch_atom_id = {
                    BranchType.JOIN_MATCH: -1,
                    BranchType.JOIN_NULL: -4,
                }.get(outcome)
                if branch_atom_id is not None:
                    self._observe(
                        node,
                        AtomObservation(
                            atom_id=branch_atom_id,
                            outcome=outcome,
                            row_ids=row_ids,
                            concrete_values=join_key_values(env),
                        ),
                    )

            joined_rows: List[Row] = []
            matched_join_rows: set[int] = set()
            source_keys = tuple(join_data.get("source_key", ()) or ())
            join_keys = tuple(join_data.get("join_key", ()) or ())
            use_key_buckets = bool(source_keys and join_keys and len(source_keys) == len(join_keys))
            key_buckets: Dict[Tuple[Any, ...], List[Tuple[int, Row]]] = {}
            if use_key_buckets:
                for join_index, join_row in enumerate(join_table.rows):
                    join_env = env_for_join_row(join_row)
                    key = tuple(concrete(key_expr, join_env) for key_expr in join_keys)
                    key_buckets.setdefault(key, []).append((join_index, join_row))

            def candidate_rows(source_row: Row) -> List[Tuple[int, Row]]:
                if not use_key_buckets:
                    return list(enumerate(join_table.rows))
                source_env = env_for_source(source_row)
                source_key = tuple(concrete(key_expr, source_env) for key_expr in source_keys)
                return key_buckets.get(source_key, [])

            def record_source_gap(source_row: Row, outcome: BranchType) -> None:
                if node is None:
                    return
                null_right = _null_join_row(join_table, _row_ids(source_row))
                env = env_for_pair(source_row, null_right)
                self._observe(
                    node,
                    AtomObservation(
                        atom_id=-4 if outcome == BranchType.JOIN_NULL else -2,
                        outcome=outcome,
                        row_ids=_row_ids(source_row),
                        concrete_values=join_key_values(env),
                    ),
                )

            for source_row in source_table.rows:
                source_matched = False
                candidates = candidate_rows(source_row)
                if use_key_buckets and not candidates:
                    source_env = env_for_source(source_row)
                    source_key = tuple(concrete(key_expr, source_env) for key_expr in source_keys)
                    record_source_gap(
                        source_row,
                        BranchType.JOIN_NULL
                        if any(value is None for value in source_key)
                        else BranchType.JOIN_LEFT,
                    )
                for join_index, join_row in candidates:
                    env = env_for_pair(source_row, join_row)
                    joined_row_ids = _row_ids(source_row) + _row_ids(join_row)
                    keys_match, condition_matches, join_outcome = evaluate_join_pair(env)
                    record_join_pair(env, joined_row_ids, join_outcome)

                    if keys_match and condition_matches:
                        source_matched = True
                        matched_join_rows.add(join_index)
                        joined = _joined_row(source_row, join_row)
                        joined_rows.append(joined)
                        if node is not None:
                            tree.record_row_lineage(
                                step_id=node.step_id,
                                site="join",
                                output_row_ids=_row_ids(joined),
                                source_row_ids=(
                                    _row_ids(source_row),
                                    _row_ids(join_row),
                                ),
                                relations=node.tables,
                            )
                            tree.record_operator_trace(
                                node,
                                outcome=BranchType.JOIN_MATCH,
                                input_row_ids=(
                                    _row_ids(source_row),
                                    _row_ids(join_row),
                                ),
                                output_row_ids=(_row_ids(joined),),
                                concrete_values=join_key_values(env),
                            )
                    elif node is not None:
                        tree.record_operator_trace(
                            node,
                            outcome=join_outcome,
                            input_row_ids=(
                                _row_ids(source_row),
                                _row_ids(join_row),
                            ),
                            concrete_values=join_key_values(env),
                        )
                if preserves_source and not source_matched:
                    null_right = _null_join_row(join_table, _row_ids(source_row))
                    preserved = _joined_row(source_row, null_right)
                    joined_rows.append(preserved)
                    if node is not None:
                        tree.record_row_lineage(
                            step_id=node.step_id,
                            site="join",
                            output_row_ids=_row_ids(preserved),
                            source_row_ids=(_row_ids(source_row),),
                            relations=node.tables,
                        )
                    record_preserved_row(
                        preserved,
                        env_for_pair(source_row, null_right),
                    )

            if preserves_join:
                for join_index, join_row in enumerate(join_table.rows):
                    if join_index in matched_join_rows:
                        continue
                    null_source = _null_join_row(source_table, _row_ids(join_row))
                    preserved = _joined_row(null_source, join_row)
                    joined_rows.append(preserved)
                    if node is not None:
                        tree.record_row_lineage(
                            step_id=node.step_id,
                            site="join",
                            output_row_ids=_row_ids(preserved),
                            source_row_ids=(_row_ids(join_row),),
                            relations=node.tables,
                        )
                    record_preserved_row(
                        preserved,
                        env_for_pair(null_source, join_row),
                    )
            elif node is not None:
                for join_index, join_row in enumerate(join_table.rows):
                    if join_index in matched_join_rows:
                        continue
                    null_source = _null_join_row(source_table, _row_ids(join_row))
                    env = env_for_pair(null_source, join_row)
                    join_key = tuple(concrete(key_expr, env_for_join_row(join_row)) for key_expr in join_keys)
                    self._observe(
                        node,
                        AtomObservation(
                            atom_id=-4 if any(value is None for value in join_key) else -3,
                            outcome=(
                                BranchType.JOIN_NULL
                                if any(value is None for value in join_key)
                                else BranchType.JOIN_RIGHT
                            ),
                            row_ids=_row_ids(join_row),
                            concrete_values=join_key_values(env),
                        ),
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
        aggregate_input_node = None
        distinct_input_node = None
        case_arm_nodes = []
        if observe:
            annotation = self.plan.annotation_for(step)
            parent_node = self._find_upstream_branch_node(step, tree)
            path_preds, join_eqs = self._collect_upstream(step)
            # Use a synthetic "group_cardinality" atom for group-size branches.
            group_pred = exp.Literal.string("GROUP_SIZE")
            group_count_pred = exp.Literal.string("GROUP_COUNT")
            node = self._runtime_node(
                step_id=annotation.step_id,
                step_type="Aggregate",
                site="group",
                predicate=group_pred,
                atoms=(group_pred, group_count_pred),
                tables=_annotation_relation_ids(annotation),
                step_obj=step,
                parent=parent_node,
                path_predicates=path_preds,
                join_equalities=join_eqs,
                annotation_metadata=annotation.metadata,
            )
            aggregate_expressions = _aggregate_coverage_expressions(step)
            if aggregate_expressions:
                aggregate_node = self._runtime_node(
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
                    annotation_metadata=annotation.metadata,
                )
                aggregate_inputs = tuple(
                    argument
                    for aggregation in aggregate_expressions
                    for function in aggregation.find_all(exp.AggFunc)
                    for argument in (function.this,)
                    if argument is not None
                    and not isinstance(argument, (exp.Star, exp.Distinct))
                )
                if aggregate_inputs:
                    aggregate_input_node = self._runtime_node(
                        step_id=annotation.step_id,
                        step_type="Aggregate",
                        site="aggregate_input",
                        predicate=exp.Literal.string("AGGREGATE_INPUT"),
                        atoms=aggregate_inputs,
                        tables=_annotation_relation_ids(annotation),
                        step_obj=step,
                        parent=parent_node,
                        path_predicates=path_preds,
                        join_equalities=join_eqs,
                        annotation_metadata=annotation.metadata,
                    )
                distinct_arguments = tuple(
                    argument.expressions[0]
                    for aggregation in aggregate_expressions
                    for function in aggregation.find_all(exp.AggFunc)
                    for argument in (function.this,)
                    if isinstance(argument, exp.Distinct) and argument.expressions
                )
                if distinct_arguments:
                    distinct_input_node = self._runtime_node(
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
                        annotation_metadata=annotation.metadata,
                    )

            for aggregation in aggregate_expressions:
                for case_expr in aggregation.find_all(exp.Case):
                    ifs = case_expr.args.get("ifs") or []
                    for arm in ifs:
                        raw_arm_pred = arm.args.get("this")
                        if not isinstance(raw_arm_pred, exp.Expression):
                            continue
                        arm_pred = _case_arm_condition(case_expr, raw_arm_pred)
                        atoms = decompose_atoms(arm_pred)
                        case_node = self._runtime_node(
                            step_id=annotation.step_id,
                            step_type="Aggregate",
                            site="case_arm",
                            predicate=arm_pred,
                            atoms=atoms,
                            tables=_annotation_relation_ids(annotation),
                            step_obj=step,
                            parent=parent_node,
                            path_predicates=path_preds,
                            join_equalities=join_eqs,
                            annotation_metadata=annotation.metadata,
                        )
                        case_arm_nodes.append((case_node, arm_pred, atoms))

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
                    table.columns,
                    table_name,
                )
            else:
                all_rows = list(table.rows)
                source_row_ids = tuple(row.rowid for row in all_rows)
                group_schema = DerivedSchema(columns=table.columns, rows=all_rows)
                group_schema.range_reader.range = range(0, len(all_rows))

                output_row_id = ("agg", step.name, "global")
                aggregate_values: Dict[Any, Any] = {}
                columns = {}
                relation_id = _step_relation_id(step)
                output_columns = self._aggregate_columns(step)
                subplans = getattr(step, "subplan_dependencies", ()) or ()
                for aggregate_index, aggregate in enumerate(step.aggregations):
                    alias = aggregate.alias_or_name
                    col_id = _aggregate_output_col_id(
                        step,
                        alias,
                        relation_id,
                        aggregate_index,
                    )
                    value = self._aggregate_expression_value(
                        aggregate,
                        group_schema.range_reader,
                        all_rows,
                        table_name,
                        operands=getattr(step, "operands", ()) or (),
                        subplans=subplans,
                    )
                    aggregate_values[alias] = value
                    columns[col_id] = _derived_variable(
                        alias,
                        value,
                        output_row_id,
                        relation_id,
                        column_id_override=col_id,
                    )
                if all_rows:
                    source_row = all_rows[0]
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
                groups[((),)] = len(all_rows)

            if node is not None:
                group_count = len(groups)
                self._observe(
                    node,
                    AtomObservation(
                        atom_id=1,
                        outcome=(
                            BranchType.GROUP_SINGLE
                            if group_count == 1
                            else BranchType.GROUP_MULTI
                        ),
                    ),
                )
                for count in groups.values():
                    outcome = BranchType.GROUP_SINGLE if count == 1 else BranchType.GROUP_MULTI
                    self._observe(node, AtomObservation(atom_id=0, outcome=outcome))

            if observe:
                aggregate_step_id = self._step_id(step)
                for output_row_id, group in aggregate_groups.items():
                    tree.record_group_lineage(
                        step_id=aggregate_step_id,
                        output_row_ids=output_row_id,
                        source_row_ids=group.source_row_ids,
                        group_key=group.group_key,
                        aggregate_values=tuple(group.aggregate_values.items()),
                    )
                    tree.record_row_lineage(
                        step_id=aggregate_step_id,
                        site="aggregate",
                        output_row_ids=output_row_id,
                        source_row_ids=group.source_row_ids,
                        relations=_annotation_relation_ids(
                            self.plan.annotation_for(step)
                        ),
                    )
                    if node is not None:
                        tree.record_operator_trace(
                            node,
                            outcome=(
                                BranchType.GROUP_SINGLE
                                if len(group.source_row_ids) == 1
                                else BranchType.GROUP_MULTI
                            ),
                            input_row_ids=group.source_row_ids,
                            output_row_ids=(output_row_id,),
                        )

            if aggregate_node is not None:
                rows_by_id = {_row_ids(row): row for row in table.rows}
                for output_row_id, group in aggregate_groups.items():
                    for atom_id, aggregation in enumerate(step.aggregations):
                        alias = aggregation.alias_or_name
                        value = group.aggregate_values.get(alias)
                        self._observe(
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
                    if aggregate_input_node is not None:
                        source_rows = [
                            rows_by_id[row_id]
                            for row_id in group.source_row_ids
                            if row_id in rows_by_id
                        ]
                        for atom_id, argument in enumerate(aggregate_input_node.atoms):
                            values = [
                                concrete(argument, _env_from_row(row))
                                for row in source_rows
                            ]
                            if any(value is None for value in values):
                                self._observe(
                                    aggregate_input_node,
                                    AtomObservation(
                                        atom_id=atom_id,
                                        outcome=BranchType.AGGREGATE_NULL,
                                        row_ids=(*output_row_id, "null", atom_id),
                                    ),
                                )
                            non_null = [value for value in values if value is not None]
                            if len(non_null) != len(set(non_null)):
                                self._observe(
                                    aggregate_input_node,
                                    AtomObservation(
                                        atom_id=atom_id,
                                        outcome=BranchType.DUPLICATE,
                                        row_ids=(*output_row_id, "duplicate", atom_id),
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
                                self._observe(
                                    distinct_input_node,
                                    AtomObservation(
                                        atom_id=atom_id,
                                        outcome=outcome,
                                        row_ids=(*output_row_id, outcome.name),
                                    ),
                                )
                    if case_arm_nodes:
                        arm_source_rows = [
                            rows_by_id[row_id]
                            for row_id in group.source_row_ids
                            if row_id in rows_by_id
                        ]
                        for case_node, arm_pred, atoms in case_arm_nodes:
                            arm_was_taken = False
                            for row in arm_source_rows:
                                env = _env_from_row(row)
                                arm_value = concrete(arm_pred, env)
                                if arm_value is True:
                                    arm_was_taken = True
                                for atom_id, atom in enumerate(atoms):
                                    outcome = _try_early_classify(atom)
                                    if outcome is None:
                                        value = concrete(atom, env)
                                        outcome = _classify_outcome(value)
                                    self._observe(
                                        case_node,
                                        AtomObservation(
                                            atom_id=atom_id,
                                            outcome=outcome,
                                            row_ids=_row_ids(row),
                                            concrete_values=_concrete_values(atom, env),
                                        ),
                                    )
                            self._observe(
                                case_node,
                                AtomObservation(
                                    atom_id=-1,
                                    outcome=(
                                        BranchType.CASE_ARM_TAKEN
                                        if arm_was_taken
                                        else BranchType.CASE_ARM_SKIPPED
                                    ),
                                    row_ids=output_row_id,
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
            node = self._runtime_node(
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
                        self._observe(
                            node,
                            AtomObservation(
                                atom_id=atom_id,
                                outcome=outcome,
                                row_ids=_row_ids(row),
                                concrete_values=_concrete_values(atom, env),
                            ),
                        )
                if node is not None:
                    self._observe(
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
                    if node is not None:
                        tree.record_row_lineage(
                            step_id=node.step_id,
                            site="having",
                            output_row_ids=_row_ids(row),
                            source_row_ids=(_row_ids(row),),
                            relations=node.tables,
                        )
                        tree.record_operator_trace(
                            node,
                            outcome=BranchType.HAVING_PASS,
                            input_row_ids=(_row_ids(row),),
                            output_row_ids=(_row_ids(row),),
                            concrete_values=_concrete_values(predicate, env),
                        )
                elif node is not None:
                    tree.record_operator_trace(
                        node,
                        outcome=_classify_having_outcome(predicate_value),
                        input_row_ids=(_row_ids(row),),
                        concrete_values=_concrete_values(predicate, env),
                    )

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
            project_items = project_coverage_items(step)
            project_expressions = tuple(
                projection for _index, projection in project_items
            )
            project_node = None
            if project_expressions:
                project_node = self._runtime_node(
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
                        for atom_id, (projection_index, _projection) in enumerate(project_items):
                            if projection_index >= len(output_ids):
                                break
                            output_id = output_ids[projection_index]
                            value = _symbol_value(projected.get(output_id))
                            self._observe(
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
                    for arm in ifs:
                        raw_arm_pred = arm.args.get("this")
                        if not isinstance(raw_arm_pred, exp.Expression):
                            continue
                        arm_pred = _case_arm_condition(case_expr, raw_arm_pred)

                        atoms = decompose_atoms(arm_pred)
                        node = self._runtime_node(
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
                                    self._observe(
                                        node,
                                        AtomObservation(
                                            atom_id=atom_id,
                                            outcome=outcome,
                                            row_ids=_row_ids(row),
                                            concrete_values=_concrete_values(atom, env),
                                        ),
                                    )
                                self._observe(
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
                distinct_node = self._runtime_node(
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
                self._observe(distinct_node, AtomObservation(atom_id=0, outcome=outcome))

        return self._materialize_project(step, ctx, tree, observe=observe)

    def _eval_set_operation(
        self,
        step: SetOperation,
        dep_contexts: List[Tuple[Step, Context]],
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
        def table_for(branch_name: Optional[str]) -> Optional[DerivedSchema]:
            for dep, dep_ctx in dep_contexts:
                if branch_name is not None and dep.name != branch_name:
                    continue
                if dep_ctx.tables:
                    return next(iter(dep_ctx.tables.values()))
            return None

        left_table = table_for(step.left)
        right_table = table_for(step.right)
        if left_table is None and dep_contexts:
            left_table = next(iter(dep_contexts[0][1].tables.values()), None)
        if right_table is None and len(dep_contexts) > 1:
            right_table = next(iter(dep_contexts[1][1].tables.values()), None)
        if left_table is None:
            return Context(tables={step.name or "set_operation": DerivedSchema(columns=(), rows=[])})

        output_columns = tuple(getattr(step, "output_column_ids", ()) or left_table.columns)
        left_rows = [
            _set_output_row(row, left_table.columns, output_columns, "set_left")
            for row in left_table.rows
        ]
        right_rows = (
            [
                _set_output_row(row, right_table.columns, output_columns, "set_right")
                for row in right_table.rows
            ]
            if right_table is not None
            else []
        )

        def row_key(row: Row) -> Tuple[Any, ...]:
            return _row_value_tuple(row, output_columns)

        def distinct_rows(rows: List[Row]) -> List[Row]:
            seen: set[Tuple[Any, ...]] = set()
            result: List[Row] = []
            for row in rows:
                key = row_key(row)
                if key in seen:
                    continue
                seen.add(key)
                result.append(row)
            return result

        if issubclass(step.op, exp.Intersect):
            right_keys = {row_key(row) for row in right_rows}
            rows = [row for row in left_rows if row_key(row) in right_keys]
            if step.distinct:
                rows = distinct_rows(rows)
        elif issubclass(step.op, exp.Except):
            right_keys = {row_key(row) for row in right_rows}
            rows = [row for row in left_rows if row_key(row) not in right_keys]
            if step.distinct:
                rows = distinct_rows(rows)
        elif issubclass(step.op, exp.Union):
            rows = left_rows + right_rows
            if step.distinct:
                rows = distinct_rows(rows)
        else:
            rows = left_rows

        if observe:
            for row in rows:
                tree.record_row_lineage(
                    step_id=self._step_id(step),
                    site="set_operation",
                    output_row_ids=_row_ids(row),
                    source_row_ids=(_row_ids(row),),
                )

        return Context(
            tables={
                step.name or "set_operation": DerivedSchema(
                    columns=output_columns,
                    rows=rows,
                    datatypes=left_table.datatypes,
                    nullables=left_table.nullables,
                    uniqueness=left_table.uniqueness,
                )
            }
        )

    def _eval_sort(
        self,
        step: Sort,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
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
            if observe:
                for row in rows:
                    tree.record_row_lineage(
                        step_id=self._step_id(step),
                        site="sort",
                        output_row_ids=_row_ids(row),
                        source_row_ids=(_row_ids(row),),
                    )
            output[step.name] = table.with_rows(rows)
            break
        return Context(tables=output)

    def _eval_limit(
        self,
        step: Limit,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
        output: Dict[str, DerivedSchema] = {}
        for _table_name, table in ctx.tables.items():
            offset = max(int(getattr(step, "offset", 0) or 0), 0)
            if step.limit == float("inf"):
                rows = list(table.rows)[offset:]
            else:
                limit_value = max(int(step.limit), 0)
                rows = list(table.rows)[offset : offset + limit_value]
            if observe:
                for row in rows:
                    tree.record_row_lineage(
                        step_id=self._step_id(step),
                        site="limit",
                        output_row_ids=_row_ids(row),
                        source_row_ids=(_row_ids(row),),
                    )
            output[step.name] = table.with_rows(rows)
            break
        return Context(tables=output)

    def _materialize_project(
        self,
        step: Project,
        ctx: Context,
        tree: BranchTree,
        *,
        observe: bool = True,
    ) -> Context:
        output: Dict[str, DerivedSchema] = {}
        for table_name, table in ctx.tables.items():
            rows: List[Row] = []
            visible_columns = self._projected_columns(step, table.columns)
            for row in table.rows:
                projected = self._projected_values(step, row, table_name)
                output_row = Row(this=_row_ids(row), columns=projected)
                rows.append(output_row)
                if observe:
                    tree.record_row_lineage(
                        step_id=self._step_id(step),
                        site="project",
                        output_row_ids=_row_ids(output_row),
                        source_row_ids=(_row_ids(row),),
                    )

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
        raise ValueError(f"missing_project_output_column_ids:{step.name}")

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
        node = self._runtime_node(
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
                env = _env_from_row(row)
                has_rows = self._inner_plan_has_rows(step.inner, env)
                outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
                self._observe(
                    node,
                    AtomObservation(
                        atom_id=0,
                        outcome=outcome,
                        row_ids=_row_ids(row),
                        concrete_values=_concrete_values(step.anchor, env),
                    ),
                )

        if not observed_outer_row:
            # Evaluate uncorrelated inner plan directly (inner plan steps are not
            # in the outer plan's annotation map, so we cannot use _walk).
            has_rows = self._inner_plan_has_rows(step.inner)
            outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
            self._observe(node, AtomObservation(atom_id=0, outcome=outcome))

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
        cacheable = all(
            subplan.kind is SubPlanKind.SCALAR and not subplan.correlated
            for subplan in subplans
        )
        if cacheable:
            cached = self._uncorrelated_predicate_cache.get(id(predicate))
            if cached is not None:
                return cached

        scalar_values: Dict[int, Any] = {}
        scalar_values_by_sql: Dict[str, Any] = {}
        predicate_values: Dict[int, bool] = {}
        outer_env = env or _outer_environment(outer_bindings) or Environment()
        for subplan in subplans:
            key = id(subplan)
            subplan.anchor.meta[_SUBPLAN_ANCHOR_ID] = key
            if subplan.kind is SubPlanKind.SCALAR:
                anchor_sql = subplan.anchor.sql(dialect=self.dialect)
                if not subplan.correlated and key in self._uncorrelated_scalar_cache:
                    scalar_values[key] = self._uncorrelated_scalar_cache[key]
                else:
                    scalar_value = self._scalar_subquery_value(
                        subplan,
                        outer_bindings,
                    )
                    if not subplan.correlated:
                        self._uncorrelated_scalar_cache[key] = scalar_value
                    scalar_values[key] = scalar_value
                scalar_values_by_sql[anchor_sql] = scalar_values[key]
            elif subplan.kind is SubPlanKind.EXISTS:
                predicate_values[key] = self._inner_plan_has_rows(subplan.inner, outer_bindings)
            elif subplan.kind is SubPlanKind.IN and isinstance(subplan.anchor, exp.In):
                predicate_values[key] = self._subquery_membership_value(
                    subplan.anchor,
                    subplan.inner,
                    outer_bindings,
                    outer_env,
                )
        if not scalar_values and not predicate_values:
            return predicate

        def replace_subquery(node: exp.Expression):
            key = node.meta.get(_SUBPLAN_ANCHOR_ID)
            if isinstance(node, exp.Subquery) and key in scalar_values:
                return exp.convert(scalar_values[key])
            if isinstance(node, exp.Subquery):
                scalar_sql = node.sql(dialect=self.dialect)
                if scalar_sql in scalar_values_by_sql:
                    return exp.convert(scalar_values_by_sql[scalar_sql])
            if isinstance(node, (exp.Exists, exp.In)) and key in predicate_values:
                value = predicate_values[key]
                if value is None:
                    return exp.Null()
                return exp.true() if value else exp.false()
            return node

        resolved = predicate.copy().transform(replace_subquery)
        if cacheable:
            self._uncorrelated_predicate_cache[id(predicate)] = resolved
        return resolved

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
        source_columns: Tuple[Any, ...],
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
            group_schema = DerivedSchema(columns=source_columns, rows=group_rows)
            group_schema.range_reader.range = range(0, len(group_rows))

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
            subplans = getattr(step, "subplan_dependencies", ()) or ()
            for aggregate_index, aggregate in enumerate(step.aggregations):
                alias = aggregate.alias_or_name
                value = self._aggregate_expression_value(
                    aggregate,
                    group_schema.range_reader,
                    group_rows,
                    table_name,
                    outer_bindings,
                    getattr(step, "operands", ()) or (),
                    subplans=subplans,
                )
                aggregate_values[alias] = value
                col_id = _aggregate_output_col_id(
                    step,
                    alias,
                    relation_id,
                    aggregate_index,
                )
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
        range_reader: RangeReader,
        rows: List[Row],
        table_name: str,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
        operands: Tuple[exp.Expression, ...] = (),
        subplans: Tuple[Any, ...] = (),
    ) -> Any:
        operand_expr_by_alias = {
            operand.alias_or_name: (
                operand.this if isinstance(operand, exp.Alias) else operand
            )
            for operand in operands
        }

        aggregate_expr = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        if subplans and aggregate_expr.find(exp.Subquery):
            aggregate_expr = self._resolve_subquery_predicates(
                aggregate_expr,
                subplans,
                outer_bindings or {},
            )
        aggregate_types = (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max)
        if isinstance(aggregate_expr, aggregate_types):
            return self._aggregate_function_value(
                aggregate_expr,
                range_reader,
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
                range_reader,
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
        range_reader: RangeReader,
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

        # Use RangeReader for simple column references; fall back to per-row
        # concrete() for complex expressions.
        if is_distinct:
            inner = resolved_arg.expressions[0] if resolved_arg.expressions else None
            if inner is not None and isinstance(inner, exp.Column):
                raw_values = [_symbol_value(v) for v in range_reader[inner]]
            elif inner is not None:
                raw_values = [
                    concrete(inner, _env_from_row(row, outer_bindings))
                    for row in rows
                ]
            else:
                raw_values = []
        elif isinstance(resolved_arg, exp.Column):
            raw_values = [_symbol_value(v) for v in range_reader[resolved_arg]]
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
            numeric = [v for v in non_null_values if isinstance(v, (int, float))]
            return sum(numeric) / len(numeric) if numeric else None
        if isinstance(aggregate_expr, exp.Sum):
            numeric = [v for v in non_null_values if isinstance(v, (int, float))]
            return sum(numeric) if numeric else None
        if isinstance(aggregate_expr, exp.Min):
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
        return {
            value
            for value, _row_id in self._inner_plan_value_rows(root, outer_bindings)
        }

    def _expression_tuple_value(
        self,
        expression: exp.Expression,
        env: Environment,
    ) -> Tuple[Any, ...]:
        if isinstance(expression, exp.Tuple):
            return tuple(concrete(item, env) for item in expression.expressions)
        return (concrete(expression, env),)

    def _inner_plan_tuple_rows(
        self,
        root: Step,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> List[Tuple[Tuple[Any, ...], Tuple[Any, ...]]]:
        rows, _table_name, _projects = self._eval_inner_plan(root, outer_bindings)
        return [
            (_row_value_tuple(row, row.columns), _row_ids(row))
            for row in rows
        ]

    def _subquery_membership_value(
        self,
        anchor: exp.In,
        inner: Step,
        outer_bindings: Optional[Dict[ColumnId, Any]],
        outer_env: Environment,
    ) -> Optional[bool]:
        outer_value = self._expression_tuple_value(anchor.this, outer_env)
        inner_value_rows = self._inner_plan_tuple_rows(inner, outer_bindings)
        inner_values = tuple(value for value, _row_id in inner_value_rows)
        return _sql_tuple_membership(outer_value, inner_values)

    def _inner_plan_value_rows(
        self,
        root: Step,
        outer_bindings: Optional[Dict[ColumnId, Any]] = None,
    ) -> List[Tuple[Any, Tuple[Any, ...]]]:
        """Evaluate inner plan and return projected values with their row ids."""
        rows, table_name, projects = self._eval_inner_plan(root, outer_bindings)
        if not rows:
            return []

        values: List[Tuple[Any, Tuple[Any, ...]]] = []
        projection = projects[0].projections[0] if projects and projects[0].projections else None
        for row in rows:
            if projection is not None:
                # Try ColumnId from output_column_ids first
                output_ids = getattr(projects[0], "output_column_ids", None)
                if output_ids and output_ids[0] in row:
                    values.append((_symbol_value(row[output_ids[0]]), _row_ids(row)))
                    continue
                alias = self._projection_name(projection)
                if alias in row:
                    values.append((_symbol_value(row[alias]), _row_ids(row)))
                    continue
                projection_expr = projection.this if isinstance(projection, exp.Alias) else projection
            elif len(row.columns) == 1:
                values.append(
                    (
                        _symbol_value(next(iter(dict(row.items()).values()))),
                        _row_ids(row),
                    )
                )
                continue
            else:
                continue

            env = _env_from_row(row, outer_bindings)
            val = concrete(projection_expr, env)
            values.append((val, _row_ids(row)))
        return values

    def _eval_in_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate col IN (SELECT ...) and record IN_MATCH/IN_NO_MATCH."""
        annotation = self.plan.annotation_for(step)
        step_id = annotation.step_id

        parent_node = self._find_upstream_branch_node(step, tree)
        path_preds, join_eqs = self._collect_upstream(step)
        node = self._runtime_node(
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
            for table_name, table in ctx.tables.items():
                for row in table.rows:
                    env = _env_from_row(row)
                    outer_value = self._expression_tuple_value(step.anchor.this, env)
                    inner_value_rows = self._inner_plan_tuple_rows(step.inner, env)
                    membership = _sql_tuple_membership(
                        outer_value,
                        tuple(value for value, _row_id in inner_value_rows),
                    )
                    outcome = (
                        BranchType.IN_MATCH
                        if membership is True
                        else BranchType.IN_NO_MATCH
                    )
                    self._observe(
                        node,
                        AtomObservation(
                            atom_id=0,
                            outcome=outcome,
                            row_ids=_row_ids(row),
                            concrete_values=_concrete_values(step.anchor, env),
                        ),
                    )
                    matching_inner_rows = tuple(
                        inner_row_id
                        for value, inner_row_id in inner_value_rows
                        if _sql_tuple_membership(outer_value, (value,)) is True
                    )
                    tree.record_operator_trace(
                        node,
                        outcome=outcome,
                        input_row_ids=(_row_ids(row),) + matching_inner_rows,
                        output_row_ids=(_row_ids(row),) if outcome == BranchType.IN_MATCH else (),
                        concrete_values=_concrete_values(step.anchor, env),
                    )

        return ctx


__all__ = ["PlanEvaluator"]
