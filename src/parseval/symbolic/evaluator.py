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
from parseval.plan.rex import Const, Environment, Variable, concrete
from parseval.instance import Instance

from .types import (
    AtomObservation,
    BranchNode,
    BranchTree,
    BranchType,
    CoverageThresholds,
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


# =============================================================================
# Environment builder
# =============================================================================


def _env_from_row(row: Row, table_name: str) -> Environment:
    """Build an Environment with both bare and table-qualified keys."""
    bindings: Dict[str, Any] = {}
    for col_name, symbol in row.items():
        value = symbol.concrete if isinstance(symbol, (Variable, Const)) else symbol
        bindings[col_name] = value
        bindings[f"{table_name}.{col_name}"] = value
    return Environment(bindings)


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

    def evaluate(self, tree: Optional[BranchTree] = None) -> BranchTree:
        if tree is None:
            tree = BranchTree()
        ctx = build_context_from_instance(self.instance)
        self._walk(self.plan.root, ctx, tree)
        return tree

    def _walk(self, step: Step, ctx: Context, tree: BranchTree) -> Context:
        """Recursively evaluate the plan bottom-up."""
        dep_contexts: Dict[str, DerivedSchema] = {}
        for dep in step.chain_dependencies:
            dep_ctx = self._walk(dep, ctx, tree)
            for name, table in dep_ctx.tables.items():
                dep_contexts[name] = table

        input_ctx = Context(tables=dep_contexts) if dep_contexts else ctx

        # Walk subplan dependencies (EXISTS, IN, scalar subqueries) for
        # branch observation recording.  They don't transform the context.
        for sub in step.subplan_dependencies:
            self._walk(sub, input_ctx, tree)

        if isinstance(step, Scan):
            return self._eval_scan(step, ctx)
        elif isinstance(step, Filter):
            return self._eval_filter(step, input_ctx, tree)
        elif isinstance(step, Join):
            return self._eval_join(step, ctx, tree)
        elif isinstance(step, Aggregate):
            return self._eval_aggregate(step, input_ctx, tree)
        elif isinstance(step, Having):
            return self._eval_having(step, input_ctx, tree)
        elif isinstance(step, Project):
            return self._eval_project(step, input_ctx, tree)
        elif isinstance(step, SubPlan):
            return self._eval_subplan(step, input_ctx, tree)
        elif isinstance(step, (Sort, Limit, SetOperation)):
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

    def _eval_filter(self, step: Filter, ctx: Context, tree: BranchTree) -> Context:
        if step.condition is None:
            return ctx

        annotation = self.plan.annotation_for(step)
        predicate = step.condition
        atoms = decompose_atoms(predicate)

        node = tree.get_or_create_node(
            step_id=annotation.step_id,
            step_type="Filter",
            site="filter",
            predicate=predicate,
            atoms=atoms,
            tables=annotation.source_tables,
        )

        passing_rows: List[Row] = []
        for table_name, table in ctx.tables.items():
            for row in table.rows:
                env = _env_from_row(row, table_name)
                # Record per-atom observations.
                for atom_id, atom in enumerate(atoms):
                    value = concrete(atom, env)
                    outcome = _classify_outcome(value)
                    tree.record_observation(
                        node,
                        AtomObservation(
                            atom_id=atom_id,
                            outcome=outcome,
                            row_ids=row.rowid if hasattr(row, "rowid") else (),
                        ),
                    )
                # Full predicate for pass/fail.
                if concrete(predicate, env) is True:
                    passing_rows.append(row)

        return Context(
            tables={
                name: DerivedSchema(columns=table.columns, rows=passing_rows, column_range=table.column_range)
                if passing_rows and len(passing_rows[0]) == len(table.columns)
                else DerivedSchema(columns=tuple(passing_rows[0].columns) if passing_rows else table.columns, rows=passing_rows)
                for name, table in ctx.tables.items()
            }
        )

    def _eval_join(self, step: Join, ctx: Context, tree: BranchTree) -> Context:
        annotation = self.plan.annotation_for(step)
        for join_name, join_data in (step.joins or {}).items():
            condition = join_data.get("condition")
            if condition is None or not isinstance(condition, exp.Expression):
                continue

            atoms = decompose_atoms(condition)
            node = tree.get_or_create_node(
                step_id=annotation.step_id,
                step_type="Join",
                site="join_on",
                predicate=condition,
                atoms=atoms,
                tables=annotation.source_tables,
            )

            source_name = step.source_name or step.name
            source_table = ctx.tables.get(source_name)
            join_table = ctx.tables.get(join_name)
            if source_table is None or join_table is None:
                continue

            for source_row in source_table.rows:
                for join_row in join_table.rows:
                    env = Environment()
                    for col, sym in source_row.items():
                        val = sym.concrete if isinstance(sym, (Variable, Const)) else sym
                        env.bind(f"{source_name}.{col}", val)
                        env.bind(col, val)
                    for col, sym in join_row.items():
                        val = sym.concrete if isinstance(sym, (Variable, Const)) else sym
                        env.bind(f"{join_name}.{col}", val)
                        env.bind(col, val)

                    for atom_id, atom in enumerate(atoms):
                        value = concrete(atom, env)
                        outcome = _classify_outcome(value)
                        tree.record_observation(node, AtomObservation(atom_id=atom_id, outcome=outcome))

        return ctx

    def _eval_aggregate(self, step: Aggregate, ctx: Context, tree: BranchTree) -> Context:
        if not step.group:
            return ctx

        annotation = self.plan.annotation_for(step)
        # Use a synthetic "group_cardinality" atom for group-size branches.
        group_pred = exp.Literal.number(1)  # placeholder expression
        node = tree.get_or_create_node(
            step_id=annotation.step_id,
            step_type="Aggregate",
            site="group",
            predicate=group_pred,
            atoms=(group_pred,),
            tables=annotation.source_tables,
        )

        groups: Dict[tuple, int] = {}
        for table_name, table in ctx.tables.items():
            for row in table.rows:
                env = _env_from_row(row, table_name)
                key = tuple(concrete(g, env) for g in step.group.values())
                groups[key] = groups.get(key, 0) + 1

        for key, count in groups.items():
            outcome = BranchType.GROUP_SINGLE if count == 1 else BranchType.GROUP_MULTI
            tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))

        return ctx

    def _eval_having(self, step: Having, ctx: Context, tree: BranchTree) -> Context:
        if step.condition is None:
            return ctx

        annotation = self.plan.annotation_for(step)
        predicate = step.condition
        atoms = decompose_atoms(predicate)

        node = tree.get_or_create_node(
            step_id=annotation.step_id,
            step_type="Having",
            site="having",
            predicate=predicate,
            atoms=atoms,
            tables=annotation.source_tables,
        )

        for table_name, table in ctx.tables.items():
            for row in table.rows:
                env = _env_from_row(row, table_name)
                for atom_id, atom in enumerate(atoms):
                    value = concrete(atom, env)
                    outcome = _classify_outcome(value)
                    tree.record_observation(node, AtomObservation(atom_id=atom_id, outcome=outcome))

        return ctx

    def _eval_project(self, step: Project, ctx: Context, tree: BranchTree) -> Context:
        annotation = self.plan.annotation_for(step)
        for projection in step.projections:
            if not isinstance(projection, exp.Expression):
                continue
            for case_expr in projection.find_all(exp.Case):
                ifs = case_expr.args.get("ifs") or []
                for arm_index, arm in enumerate(ifs):
                    arm_pred = arm.args.get("this")
                    if not isinstance(arm_pred, exp.Expression):
                        continue

                    atoms = decompose_atoms(arm_pred)
                    node = tree.get_or_create_node(
                        step_id=annotation.step_id,
                        step_type="Project",
                        site="case_arm",
                        predicate=arm_pred,
                        atoms=atoms,
                        tables=annotation.source_tables,
                    )

                    for table_name, table in ctx.tables.items():
                        for row in table.rows:
                            env = _env_from_row(row, table_name)
                            for atom_id, atom in enumerate(atoms):
                                value = concrete(atom, env)
                                outcome = _classify_outcome(value)
                                tree.record_observation(
                                    node, AtomObservation(atom_id=atom_id, outcome=outcome)
                                )
        return ctx

    def _eval_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate a SubPlan and record branch observations."""
        if step.kind is SubPlanKind.EXISTS:
            return self._eval_exists_subplan(step, ctx, tree)
        elif step.kind is SubPlanKind.IN:
            return self._eval_in_subplan(step, ctx, tree)
        elif step.kind is SubPlanKind.SCALAR:
            return self._eval_scalar_subplan(step, ctx, tree)
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

        # Evaluate inner plan directly (inner plan steps are not in the
        # outer plan's annotation map, so we cannot use _walk).
        has_rows = self._inner_plan_has_rows(step.inner)

        outcome = BranchType.EXISTS_TRUE if has_rows else BranchType.EXISTS_FALSE
        tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))

        return ctx  # SubPlan doesn't transform the outer context

    def _inner_plan_has_rows(self, root: Step) -> bool:
        """Check whether an inner plan would produce at least one row."""
        # Collect Scan and Filter steps from the inner plan.
        scans: List[Scan] = []
        filters: List[Filter] = []

        def _collect(s: Step) -> None:
            if isinstance(s, Scan):
                scans.append(s)
            if isinstance(s, Filter):
                filters.append(s)
            for dep in s.chain_dependencies:
                _collect(dep)

        _collect(root)

        if not scans:
            return False

        # For simple single-table EXISTS subqueries, evaluate directly.
        scan = scans[0]
        source = scan.source
        if isinstance(source, exp.Table):
            table_name = source.name
        else:
            table_name = scan.name

        if table_name not in self.instance.tables:
            return False

        rows = self.instance.get_rows(table_name)
        if not rows:
            return False

        # Apply any filter conditions.
        for filt in filters:
            if filt.condition is None:
                continue
            passing: List[Row] = []
            for row in rows:
                env = _env_from_row(row, table_name)
                if concrete(filt.condition, env) is True:
                    passing.append(row)
            rows = passing

        return len(rows) > 0

    def _eval_in_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate IN (SELECT ...) -- placeholder for Task 5."""
        return ctx

    def _eval_scalar_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
        """Evaluate scalar subquery -- placeholder."""
        return ctx


__all__ = ["PlanEvaluator", "decompose_atoms"]
