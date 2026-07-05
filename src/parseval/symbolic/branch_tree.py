"""Branch-tree ownership helpers for symbolic coverage.

This module names the boundaries around branch topology, runtime recording,
coverage analysis, and path extraction.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import Any, List, Optional, Tuple

from sqlglot import exp

from parseval.identity import (
    ColumnId,
    ColumnKind,
    PARSEVAL_COLUMN_ID,
    RelationId,
    column_identity,
    identifier_name,
    physical_column,
)
from parseval.instance import Instance
from parseval.plan import Plan, Step
from parseval.plan.planner import (
    Aggregate,
    Filter,
    Having,
    Join,
    Limit,
    Project,
    Scan,
    Sort,
    SubPlan,
    SubPlanKind,
)


from .types import (
    AtomObservation,
    BranchNode,
    BranchPath,
    BranchTree,
    BranchType,
    CoverageTarget,
    CoverageThresholds,
    CoverageObligation,
    JoinFact,
    OperatorObligation,
    PathPredicate,
    RowSetObligation,
    SubqueryPath,
)

MAX_ROOT_GENERATION_ROWS = 20


def decompose_atoms(predicate: exp.Expression) -> Tuple[exp.Expression, ...]:
    """Break a compound predicate into atom-level predicates."""
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


def _column_expr_from_id(column: ColumnId) -> exp.Column:
    relation = column.relation
    table_name = ""
    if relation is not None:
        visible = relation.alias or relation.name
        if visible is not None:
            table_name = visible.raw
    expression = exp.Column(
        this=exp.to_identifier(column.name.raw, quoted=column.name.quoted),
        table=exp.to_identifier(table_name) if table_name else None,
    )
    expression.meta[PARSEVAL_COLUMN_ID] = column
    return expression


def _subquery_paths_for_atom(
    node: Any,
    atom: exp.Expression,
    subplans: Tuple[SubPlan, ...],
) -> Tuple[SubqueryPath, ...]:
    subqueries = tuple(atom.find_all(exp.Subquery))
    if isinstance(atom, exp.Subquery):
        subqueries = (atom,) + subqueries
    paths: List[SubqueryPath] = []
    for subquery in subqueries:
        for subplan in subplans:
            anchor = getattr(subplan, "anchor", None)
            if anchor is subquery:
                paths.append(
                    SubqueryPath(
                        node=node,
                        inner_root=subplan.inner,
                        outer_columns=tuple(
                            col_id
                            for col in atom.find_all(exp.Column)
                            for col_id in (column_identity(col),)
                            if col_id is not None
                            and col_id.relation is not None
                            and col not in set(subquery.find_all(exp.Column))
                        ),
                        inner_columns=tuple(
                            col_id
                            for col in subquery.find_all(exp.Column)
                            for col_id in (column_identity(col),)
                            if col_id is not None and col_id.relation is not None
                        ),
                        predicate=atom,
                    )
                )
                break
    return tuple(paths)


def _join_facts_for_step(plan: Plan, step: Step) -> Tuple[JoinFact, ...]:
    if not isinstance(step, Join):
        return ()

    facts: List[JoinFact] = []
    for join_rel, join_data in (step.joins or {}).items():
        equalities: List[Tuple[ColumnId, ColumnId]] = []
        source_keys = tuple(join_data.get("source_key", ()))
        join_keys = tuple(join_data.get("join_key", ()))
        for source_key, join_key in zip(source_keys, join_keys):
            source_id = (
                column_identity(source_key)
                if isinstance(source_key, exp.Column)
                else None
            )
            join_id = (
                column_identity(join_key)
                if isinstance(join_key, exp.Column)
                else None
            )
            if source_id is None or join_id is None:
                # Column identity not resolvable — skip this join equality.
                continue
            equalities.append((source_id, join_id))

        source_relation = equalities[0][0].relation if equalities else None
        target_relation = join_rel if isinstance(join_rel, RelationId) else None
        if source_relation is None or target_relation is None:
            # Malformed join condition (e.g., same alias on both sides) — skip.
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


def _case_arm_condition(case_expr: exp.Case, arm_pred: exp.Expression) -> exp.Expression:
    if isinstance(case_expr.this, exp.Expression):
        return exp.EQ(this=case_expr.this.copy(), expression=arm_pred.copy())
    return arm_pred


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


def _is_aggregate_projection(projection: exp.Expression) -> bool:
    expression = projection.this if isinstance(projection, exp.Alias) else projection
    if expression.find(exp.AggFunc):
        return True
    identity = column_identity(expression) if isinstance(expression, exp.Column) else None
    if identity is not None and identity.kind is ColumnKind.AGGREGATE:
        return True
    for col in expression.find_all(exp.Column):
        col_id = column_identity(col)
        if col_id is not None and col_id.kind is ColumnKind.AGGREGATE:
            return True
    return False


def project_coverage_items(step: Project) -> Tuple[Tuple[int, exp.Expression], ...]:
    items: List[Tuple[int, exp.Expression]] = []
    for index, projection in enumerate(step.projections):
        if not isinstance(projection, exp.Expression):
            continue
        if _is_aggregate_projection(projection):
            continue
        items.append((index, projection))
    return tuple(items)


def project_coverage_expressions(step: Project) -> Tuple[exp.Expression, ...]:
    return tuple(projection for _index, projection in project_coverage_items(step))


def coverage_obligations_for_site(
    site: str,
    atoms: Tuple[exp.Expression, ...],
) -> Tuple[CoverageObligation, ...]:
    obligations: List[CoverageObligation] = []

    def add(
        metric: str,
        atom_id: int,
        expression: exp.Expression,
        outcomes: Tuple[BranchType, ...],
    ) -> None:
        obligations.append(
            CoverageObligation(
                metric=metric,
                atom_id=atom_id,
                expression=expression,
                outcomes=outcomes,
            )
        )

    if site == "case_arm":
        expression = atoms[0] if atoms else exp.true()
        add(
            "case_arm",
            -1,
            expression,
            (BranchType.CASE_ARM_TAKEN, BranchType.CASE_ARM_SKIPPED),
        )
    elif site == "group":
        size_expr = atoms[0] if atoms else exp.Literal.string("GROUP_SIZE")
        count_expr = atoms[1] if len(atoms) > 1 else exp.Literal.string("GROUP_COUNT")
        add("group_size", 0, size_expr, (BranchType.GROUP_SINGLE, BranchType.GROUP_MULTI))
        add("group_count", 1, count_expr, (BranchType.GROUP_SINGLE, BranchType.GROUP_MULTI))
    elif site == "aggregate_input":
        for atom_id, atom in enumerate(atoms):
            add("aggregate_input_null", atom_id, atom, (BranchType.AGGREGATE_NULL,))
            add("aggregate_input_duplicate", atom_id, atom, (BranchType.DUPLICATE,))
    elif site == "aggregate_output":
        for atom_id, atom in enumerate(atoms):
            expression = atom.this if isinstance(atom, exp.Alias) else atom
            outcomes = (
                (BranchType.AGGREGATE_NON_NULL,)
                if isinstance(expression, exp.Count)
                else (BranchType.AGGREGATE_NULL, BranchType.AGGREGATE_NON_NULL)
            )
            add("aggregate_output", atom_id, atom, outcomes)
    elif site == "aggregate_distinct_input":
        for atom_id, atom in enumerate(atoms):
            add(
                "aggregate_distinct_input",
                atom_id,
                atom,
                (
                    BranchType.AGG_DISTINCT_NULL_IGNORED,
                    BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED,
                    BranchType.AGG_DISTINCT_MULTIPLE_RETAINED,
                ),
            )
    elif site == "project_output":
        for atom_id, atom in enumerate(atoms):
            add(
                "project_output",
                atom_id,
                atom,
                (BranchType.PROJECT_NULL, BranchType.PROJECT_NON_NULL),
            )
    elif site == "distinct":
        expression = atoms[0] if atoms else exp.Literal.string("DISTINCT")
        add(
            "distinct",
            0,
            expression,
            (BranchType.DISTINCT_UNIQUE, BranchType.DISTINCT_DUPLICATE),
        )
    elif site == "join_on":
        expression = atoms[0] if atoms else exp.true()
        add("join_match", -1, expression, (BranchType.JOIN_MATCH,))
        add("join_left_unmatched", -2, expression, (BranchType.JOIN_LEFT,))
        add("join_right_unmatched", -3, expression, (BranchType.JOIN_RIGHT,))
        add("join_null", -4, expression, (BranchType.JOIN_NULL,))
    elif site == "root_result":
        expression = atoms[0] if atoms else exp.true()
        add("root_result", 0, expression, (BranchType.ATOM_TRUE,))
        add("root_duplicate", 0, expression, (BranchType.DUPLICATE,))
    elif site == "exists":
        expression = atoms[0] if atoms else exp.true()
        add("exists", 0, expression, (BranchType.EXISTS_TRUE, BranchType.EXISTS_FALSE))
    elif site == "in":
        expression = atoms[0] if atoms else exp.true()
        add("in", 0, expression, (BranchType.IN_MATCH, BranchType.IN_NO_MATCH))

    return tuple(obligations)


class BranchCoverageRecorder:
    """Explicit mutation API for evaluator observations."""

    def __init__(self, tree: BranchTree):
        self.tree = tree

    def planned_node(
        self,
        *,
        step_id: str,
        step_type: str,
        site: str,
        predicate: exp.Expression,
        atoms: Tuple[exp.Expression, ...],
        tables: Tuple[RelationId, ...] = (),
        step_obj: Any = None,
        parent: Optional[BranchNode] = None,
        path_predicates: Tuple[exp.Expression, ...] = (),
        join_equalities: Tuple[Tuple[ColumnId, ColumnId], ...] = (),
        join_facts: Tuple[JoinFact, ...] = (),
        subqueries: Tuple[SubqueryPath, ...] = (),
        obligations: Tuple[OperatorObligation, ...] = (),
        coverage_obligations: Tuple[CoverageObligation, ...] = (),
        annotation_metadata: Optional[dict[str, Any]] = None,
        origin: Optional[str] = None,
    ) -> BranchNode:
        if not coverage_obligations:
            coverage_obligations = coverage_obligations_for_site(site, atoms)
        return self.tree.get_or_create_node(
            step_id=step_id,
            step_type=step_type,
            site=site,
            predicate=predicate,
            atoms=atoms,
            tables=tables,
            step_obj=step_obj,
            parent=parent,
            path_predicates=path_predicates,
            join_equalities=join_equalities,
            join_facts=join_facts,
            subqueries=subqueries,
            obligations=obligations,
            coverage_obligations=coverage_obligations,
            annotation_metadata=annotation_metadata,
            discovery="planned",
            origin=origin,
        )

    def runtime_node(
        self,
        *,
        step_id: str,
        step_type: str,
        site: str,
        predicate: exp.Expression,
        atoms: Tuple[exp.Expression, ...],
        tables: Tuple[RelationId, ...] = (),
        step_obj: Any = None,
        parent: Optional[BranchNode] = None,
        path_predicates: Tuple[exp.Expression, ...] = (),
        join_equalities: Tuple[Tuple[ColumnId, ColumnId], ...] = (),
        join_facts: Tuple[JoinFact, ...] = (),
        subqueries: Tuple[SubqueryPath, ...] = (),
        obligations: Tuple[OperatorObligation, ...] = (),
        coverage_obligations: Tuple[CoverageObligation, ...] = (),
        annotation_metadata: Optional[dict[str, Any]] = None,
        origin: Optional[str] = None,
    ) -> BranchNode:
        if not coverage_obligations:
            coverage_obligations = coverage_obligations_for_site(site, atoms)
        return self.tree.get_or_create_node(
            step_id=step_id,
            step_type=step_type,
            site=site,
            predicate=predicate,
            atoms=atoms,
            tables=tables,
            step_obj=step_obj,
            parent=parent,
            path_predicates=path_predicates,
            join_equalities=join_equalities,
            join_facts=join_facts,
            subqueries=subqueries,
            obligations=obligations,
            coverage_obligations=coverage_obligations,
            annotation_metadata=annotation_metadata,
            discovery="runtime",
            origin=origin,
        )

    def observe(self, node: BranchNode, observation: AtomObservation) -> None:
        self.tree.record_observation(node, observation)


class CoverageAnalyzer:
    """Coverage-target policy for an evaluated BranchTree."""

    def __init__(self, tree: BranchTree):
        self.tree = tree

    def target_specs_for_node(
        self, node: BranchNode
    ) -> List[Tuple[int, BranchType, int]]:
        return self.tree._target_specs_for_node(node)

    @property
    def uncovered_targets(self) -> List[CoverageTarget]:
        if not self.tree._cache_dirty and self.tree._uncovered_cache is not None:
            return self.tree._uncovered_cache

        targets: List[CoverageTarget] = []
        for node in self.tree.nodes:
            if self._uses_atom_combinations(node):
                for atom_outcomes, threshold in self._combination_specs_for_node(node):
                    if node.is_atom_outcomes_infeasible(atom_outcomes):
                        continue
                    if self._combination_count(node, atom_outcomes) >= threshold:
                        continue
                    targets.append(
                        CoverageTarget(
                            node=node,
                            atom_id=-1,
                            target_outcome=BranchType.ATOM_TRUE,
                            atom_outcomes=atom_outcomes,
                        )
                    )
                continue
            for atom_id, outcome, threshold in self.target_specs_for_node(node):
                if node.is_infeasible(atom_id, outcome):
                    continue
                if self._covered_observation_count(node, atom_id, outcome) >= threshold:
                    continue
                obligation = self._obligation_for(node, atom_id, outcome)
                targets.append(
                    CoverageTarget(
                        node=node,
                        atom_id=atom_id,
                        target_outcome=outcome,
                        obligation=obligation,
                    )
                )

        self.tree._uncovered_cache = targets
        self.tree._cache_dirty = False
        return targets

    def _uses_atom_combinations(self, node: BranchNode) -> bool:
        return (
            not node.coverage_obligations
            and node.site in {"filter", "join_on", "having"}
            and len(node.atoms) > 1
        )

    def _obligation_for(
        self,
        node: BranchNode,
        atom_id: int,
        outcome: BranchType,
    ) -> CoverageObligation | None:
        for obligation in node.coverage_obligations:
            if obligation.atom_id == atom_id and outcome in obligation.outcomes:
                return obligation
        return None

    def _root_result_row_count(self, node: BranchNode) -> int:
        counts = [
            obligation.row_count
            for obligation in node.obligations
            if obligation.kind == "root_result"
        ]
        return max(counts or [1])

    def _covered_observation_count(
        self,
        node: BranchNode,
        atom_id: int,
        outcome: BranchType,
    ) -> int:
        if node.site == "root_result" and outcome == BranchType.ATOM_TRUE:
            return len(self.tree.root_output_lineages())
        if node.site == "in":
            return self.tree.operator_trace_count(
                node,
                outcome,
                require_output=outcome == BranchType.IN_MATCH,
            )
        if node.site == "group":
            return self.tree.operator_trace_count(node, outcome, require_output=True)
        if atom_id < 0:
            return self.tree.operator_trace_count(
                node,
                outcome,
                require_output=outcome
                in {
                    BranchType.FILTER_TRUE,
                    BranchType.JOIN_MATCH,
                    BranchType.HAVING_PASS,
                    BranchType.ATOM_TRUE,
                },
            )
        return node.observation_count(atom_id, outcome)

    def _combination_specs_for_node(
        self,
        node: BranchNode,
    ) -> List[Tuple[Tuple[Tuple[int, BranchType], ...], int]]:
        outcomes: List[BranchType] = []
        if self.tree.thresholds.atom_true > 0:
            outcomes.append(BranchType.ATOM_TRUE)
        if self.tree.thresholds.atom_false > 0:
            outcomes.append(BranchType.ATOM_FALSE)
        if self.tree.thresholds.atom_null > 0:
            outcomes.append(BranchType.ATOM_NULL)
        if not outcomes:
            return []
        return [
            (
                tuple((atom_id, outcome) for atom_id, outcome in enumerate(combo)),
                1,
            )
            for combo in product(outcomes, repeat=len(node.atoms))
        ]

    def _combination_count(
        self,
        node: BranchNode,
        atom_outcomes: Tuple[Tuple[int, BranchType], ...],
    ) -> int:
        row_ids = set(node.all_row_ids())
        for atom_id, _outcome in atom_outcomes:
            row_ids.intersection_update(node.observations.get(atom_id, {}).keys())
        return sum(
            1
            for row_id in row_ids
            if all(
                node.observations.get(atom_id, {}).get(row_id) == outcome
                for atom_id, outcome in atom_outcomes
            )
        )

    @property
    def total_targets(self) -> int:
        count = 0
        for node in self.tree.nodes:
            if self._uses_atom_combinations(node):
                count += sum(
                    1
                    for atom_outcomes, _threshold in self._combination_specs_for_node(node)
                    if not node.is_atom_outcomes_infeasible(atom_outcomes)
                )
                continue
            count += sum(
                1
                for atom_id, outcome, _threshold in self.target_specs_for_node(node)
                if not node.is_infeasible(atom_id, outcome)
            )
        return count

    @property
    def covered_count(self) -> int:
        return self.total_targets - len(self.uncovered_targets)

    @property
    def root_witness_targets(self) -> List[CoverageTarget]:
        targets: List[CoverageTarget] = []
        for node in self.tree.nodes:
            if node.site == "root_result":
                if node.is_infeasible(0, BranchType.ATOM_TRUE):
                    continue
                if (
                    self._covered_observation_count(node, 0, BranchType.ATOM_TRUE)
                    < self._root_result_row_count(node)
                ):
                    targets.append(
                        CoverageTarget(
                            node=node,
                            atom_id=0,
                            target_outcome=BranchType.ATOM_TRUE,
                        )
                    )
                continue
            if node.site == "in":
                threshold = self.tree.thresholds.threshold_for(BranchType.IN_MATCH)
                if threshold <= 0:
                    continue
                if node.is_infeasible(0, BranchType.IN_MATCH):
                    continue
                if (
                    self._covered_observation_count(node, 0, BranchType.IN_MATCH)
                    >= threshold
                ):
                    continue
                targets.append(
                    CoverageTarget(
                        node=node,
                        atom_id=0,
                        target_outcome=BranchType.IN_MATCH,
                        obligation=self._obligation_for(node, 0, BranchType.IN_MATCH),
                    )
                )
                continue
            if node.site in {"filter", "join_on", "scalar_subquery", "having"}:
                for atom_id in range(len(node.atoms)):
                    threshold = self.tree.thresholds.threshold_for(
                        BranchType.ATOM_TRUE
                    )
                    if threshold <= 0:
                        continue
                    if node.is_infeasible(atom_id, BranchType.ATOM_TRUE):
                        continue
                    if (
                        self._covered_observation_count(
                            node,
                            atom_id,
                            BranchType.ATOM_TRUE,
                        )
                        >= threshold
                    ):
                        continue
                    targets.append(
                        CoverageTarget(
                            node=node,
                            atom_id=atom_id,
                            target_outcome=BranchType.ATOM_TRUE,
                        )
                    )
        return targets

    @property
    def fully_covered(self) -> bool:
        return len(self.uncovered_targets) == 0


class BranchPathBuilder:
    """Build root-to-target BranchPath instances from a BranchTree."""

    def _obligations_for_target_node(
        self,
        node: BranchNode,
        target: CoverageTarget,
    ) -> Tuple[OperatorObligation, ...]:
        if (
            node.site in {"aggregate_input", "aggregate_distinct_input"}
            and target.target_outcome
            in {BranchType.DUPLICATE, BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED}
        ):
            row_set = RowSetObligation(
                anchor_step_id=node.step_id,
                required_rows=2,
                generation_rows=2,
                row_scopes=("r0", "r1"),
                relations=node.tables,
                join_facts=node.join_facts,
                path_predicates=node.path_predicates,
                duplicate_expressions=(target.atom,),
            )
            return (
                *node.obligations,
                OperatorObligation(
                    kind="row_set",
                    step_id=node.step_id,
                    site=node.site,
                    row_count=2,
                    row_set=row_set,
                ),
            )

        if (
            node.site != "root_result"
            or target.target_outcome != BranchType.DUPLICATE
        ):
            return node.obligations

        obligations: List[OperatorObligation] = []
        for obligation in node.obligations:
            if obligation.kind != "row_set" or obligation.row_set is None:
                obligations.append(obligation)
                continue
            row_set = obligation.row_set
            if not row_set.duplicate_expressions:
                continue
            generation_rows = max(row_set.generation_rows, 2)
            duplicate_row_set = replace(
                row_set,
                required_rows=max(row_set.required_rows, 2),
                generation_rows=generation_rows,
                row_scopes=tuple(f"out{index}" for index in range(generation_rows)),
            )
            obligations.append(
                replace(
                    obligation,
                    row_count=generation_rows,
                    row_set=duplicate_row_set,
                )
            )
        return tuple(obligations)

    def path_for_target(self, target: CoverageTarget) -> BranchPath:
        nodes: List[BranchNode] = []
        current: Optional[BranchNode] = target.node
        while current is not None:
            nodes.append(current)
            current = current.parent
        nodes.reverse()

        predicates: List[PathPredicate] = []
        join_facts: List[JoinFact] = []
        subqueries: List[SubqueryPath] = []
        obligations: List[OperatorObligation] = []
        relations: List[RelationId] = []
        seen_relations: set[RelationId] = set()

        for node in nodes:
            for relation in node.tables:
                if relation not in seen_relations:
                    seen_relations.add(relation)
                    relations.append(relation)
            join_facts.extend(node.join_facts)
            subqueries.extend(node.subqueries)
            if node is target.node:
                obligations.extend(self._obligations_for_target_node(node, target))
                if target.atom_outcomes:
                    predicates.extend(
                        PathPredicate(
                            node=node,
                            expression=node.atoms[atom_id],
                            outcome=outcome,
                            obligation=None,
                        )
                        for atom_id, outcome in target.atom_outcomes
                    )
                else:
                    predicates.append(
                        PathPredicate(
                            node=node,
                            expression=target.atom,
                            outcome=target.target_outcome,
                            obligation=target.obligation,
                        )
                    )
            elif node.site in {"filter", "having", "join_on"}:
                obligations.extend(node.obligations)
                predicates.append(
                    PathPredicate(
                        node=node,
                        expression=node.predicate,
                        outcome=BranchType.ATOM_TRUE,
                        obligation=None,
                    )
                )
            else:
                obligations.extend(node.obligations)

        return BranchPath(
            target=target,
            predicates=tuple(predicates),
            join_facts=tuple(join_facts),
            subqueries=tuple(subqueries),
            obligations=tuple(obligations),
            relations=tuple(relations),
        )


class BranchTreeBuilder:
    """Build plan-derived branch topology without recording observations."""

    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        thresholds: Optional[Any] = None,
    ):
        self.plan = plan
        self.instance = instance
        self.thresholds = thresholds

    def build(self) -> BranchTree:
        return _build_branch_tree(self.plan, self.instance, self.thresholds)


def build_branch_tree(
    plan: Plan,
    instance: Instance,
    thresholds: Optional[Any] = None,
) -> BranchTree:
    return BranchTreeBuilder(plan, instance, thresholds).build()


def _build_branch_tree(
    plan: Plan,
    instance: Instance,
    thresholds: Optional[Any] = None,
) -> BranchTree:
    """Build a BranchTree hierarchy from a Plan without running evaluation."""
    tree = BranchTree(thresholds=thresholds or CoverageThresholds())

    step_nodes: dict[str, Any] = {}
    scan_columns_by_relation: dict[RelationId, tuple[ColumnId, ...]] = {}
    storage_by_relation: dict[RelationId, RelationId] = {}

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

    def _find_parent(step: Step) -> Optional[BranchNode]:
        for dep in step.chain_dependencies:
            ann = plan.annotation_for(dep)
            if ann.step_id in step_nodes:
                return step_nodes[ann.step_id]
            parent = _find_parent(dep)
            if parent is not None:
                return parent
        return None

    def _collect_upstream(
        step: Step,
    ) -> tuple[tuple[Any, ...], tuple[tuple[ColumnId, ColumnId], ...]]:
        predicates: list[Any] = []
        join_eqs: list[tuple[ColumnId, ColumnId]] = []
        visited: set[int] = set()

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
        tables: tuple[RelationId, ...],
        row_count: int = 1,
        keyed_only: bool = False,
    ) -> tuple[OperatorObligation, ...]:
        obligations: list[OperatorObligation] = []
        for relation in tables:
            storage_relation = storage_by_relation.get(relation)
            table_name = _storage_table_key(storage_relation or relation)
            if table_name is None or table_name not in instance.tables:
                continue
            columns = scan_columns_by_relation.get(relation, ())
            if not columns:
                columns = tuple(
                    physical_column(
                        column_name,
                        relation,
                        dialect=getattr(instance, "dialect", None),
                    )
                    for column_name in instance.tables[table_name]
                )
            if keyed_only:
                def key_name(value: Any) -> str:
                    if hasattr(value, "raw") and hasattr(value, "quoted"):
                        identifier = exp.to_identifier(value.raw, quoted=value.quoted)
                        return identifier_name(
                            identifier,
                            dialect=getattr(instance, "dialect", None),
                        ).normalized
                    return identifier_name(
                        getattr(value, "name", value),
                        dialect=getattr(instance, "dialect", None),
                    ).normalized

                key_names = {
                    key_name(key)
                    for key in instance.primary_keys.get(table_name, ())
                }
                for unique_columns in instance.unique_constraints.get(table_name, ()):
                    key_names.update(key_name(key) for key in unique_columns)
                columns = tuple(
                    column
                    for column in columns
                    if key_name(column.name) in key_names
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

    def _lineage_relations(step: Step) -> tuple[RelationId, ...]:
        relations: list[RelationId] = []
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
            add_relation(column.relation or (column.source_column_id or column).relation)

        def walk_step(s: Step, visited: set[int]) -> None:
            if id(s) in visited:
                return
            visited.add(id(s))
            expr_values = (
                getattr(s, "condition", None),
                *tuple(getattr(s, "projections", ()) or ()),
                *tuple(getattr(s, "order", ()) or ()),
                *tuple((getattr(s, "group", {}) or {}).values()),
                *tuple(getattr(s, "aggregations", ()) or ()),
            )
            for expr_value in expr_values:
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
            if isinstance(s, SubPlan) and s.inner is not None:
                walk_step(s.inner, visited)
            for subplan in getattr(s, "subplan_dependencies", ()) or ():
                walk_step(subplan, visited)
                if subplan.inner is not None:
                    walk_step(subplan.inner, visited)
            for dep in s.chain_dependencies:
                walk_step(dep, visited)

        for column in tuple(getattr(step, "output_column_ids", ()) or ()):
            add_column(column)
        walk_step(step, set())
        return tuple(relations)

    def _canonical_relations(relations: tuple[RelationId, ...]) -> tuple[RelationId, ...]:
        canonical: list[RelationId] = []
        seen: set[RelationId] = set()
        for relation in relations:
            mapped = relation
            if relation not in storage_by_relation:
                for alias_relation, storage_relation in storage_by_relation.items():
                    if storage_relation == relation:
                        mapped = alias_relation
                        break
                else:
                    if relation.alias is None and relation.name is not None:
                        for alias_relation, storage_relation in storage_by_relation.items():
                            if alias_relation.alias is None or storage_relation.name is None:
                                continue
                            if storage_relation.name.normalized == relation.name.normalized:
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
        atoms: tuple[exp.Expression, ...],
        tables: tuple[RelationId, ...],
    ) -> None:
        annotation = plan.annotation_for(step)
        parent_node = _find_parent(step)
        path_preds, join_eqs = _collect_upstream(step)
        join_facts = _join_facts_for_step(plan, step) if isinstance(step, Join) else ()
        own_join_equalities = tuple(
            equality for fact in join_facts for equality in fact.equalities
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
            annotation_metadata=annotation.metadata,
            discovery="planned",
            origin=f"planner:{step_type}",
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
        step_nodes.setdefault(annotation.step_id, node)

    def _root_required_row_count(root: Step) -> int:
        required = 1
        visited: set[int] = set()

        def walk(step: Step) -> None:
            nonlocal required
            if id(step) in visited:
                return
            visited.add(id(step))
            if isinstance(step, Limit):
                offset = max(int(getattr(step, "offset", 0) or 0), 0)
                limit = getattr(step, "limit", 1)
                limit_value = 1 if limit == float("inf") else max(int(limit or 0), 1)
                required = max(required, offset + limit_value)
            for subplan in getattr(step, "subplan_dependencies", ()) or ():
                walk(subplan)
                if subplan.inner is not None:
                    walk(subplan.inner)
            for dep in step.chain_dependencies:
                walk(dep)

        walk(root)
        return max(required, 1)

    def _row_scopes(prefix: str, count: int) -> tuple[str, ...]:
        return tuple(f"{prefix}{index}" for index in range(max(count, 0)))

    def _root_path_data(root: Step) -> tuple[tuple[exp.Expression, ...], tuple[JoinFact, ...]]:
        predicates: list[exp.Expression] = []
        join_facts: list[JoinFact] = []
        visited: set[int] = set()

        def walk(step: Step) -> None:
            if id(step) in visited:
                return
            visited.add(id(step))
            condition = getattr(step, "condition", None)
            if isinstance(condition, exp.Expression) and not isinstance(step, Having):
                predicates.append(condition)
            if isinstance(step, Join):
                join_facts.extend(_join_facts_for_step(plan, step))
            for subplan in getattr(step, "subplan_dependencies", ()) or ():
                walk(subplan)
                if subplan.inner is not None:
                    walk(subplan.inner)
            for dep in step.chain_dependencies:
                walk(dep)

        for dep in root.chain_dependencies:
            walk(dep)
        return tuple(predicates), tuple(join_facts)

    def _root_ordering(root: Step) -> tuple[exp.Expression, ...]:
        ordering: list[exp.Expression] = []
        visited: set[int] = set()

        def walk(step: Step) -> None:
            if id(step) in visited:
                return
            visited.add(id(step))
            if isinstance(step, Sort):
                ordering.extend(tuple(getattr(step, "key", ()) or ()))
            for subplan in getattr(step, "subplan_dependencies", ()) or ():
                walk(subplan)
                if subplan.inner is not None:
                    walk(subplan.inner)
            for dep in step.chain_dependencies:
                walk(dep)

        walk(root)
        return tuple(ordering)

    def _having_cardinality_constraints() -> tuple[dict[str, Any], ...]:
        constraints: list[dict[str, Any]] = []
        for step in plan.ordered_steps:
            if isinstance(step, Having):
                metadata = plan.annotation_for(step).metadata
                constraints.extend(metadata.get("having_constraints", ()))
        return tuple(constraints)

    def _aggregate_group_keys_for_having() -> tuple[ColumnId, ...]:
        return _aggregate_group_keys()

    def _aggregate_group_keys() -> tuple[ColumnId, ...]:
        for step in plan.ordered_steps:
            if isinstance(step, Aggregate):
                metadata = plan.annotation_for(step).metadata.get("aggregation", {})
                group_sources = metadata.get("group_sources", {})
                keys: list[ColumnId] = []
                seen: set[ColumnId] = set()
                for sources in group_sources.values():
                    for source in sources:
                        physical = source.source_column_id or source
                        if physical in seen:
                            continue
                        seen.add(physical)
                        keys.append(physical)
                return tuple(keys)
        return ()

    def _root_group_distinct_expression() -> exp.Expression | None:
        group_keys = _aggregate_group_keys()
        if not group_keys:
            return None
        return _column_expr_from_id(group_keys[0])

    def _root_duplicate_expressions(root: Step) -> tuple[exp.Expression, ...]:
        if not isinstance(root, Project) or root.distinct:
            return ()
        if any(isinstance(step, Aggregate) for step in plan.ordered_steps):
            return ()
        return project_coverage_expressions(root)

    def _root_obligations(root: Step) -> tuple[OperatorObligation, ...]:
        annotation = plan.annotation_for(root)
        row_count = _root_required_row_count(root)
        generation_row_count = min(row_count, MAX_ROOT_GENERATION_ROWS)
        root_relations = _canonical_relations(
            annotation.source_relations + _lineage_relations(root)
        )
        path_predicates, join_facts = _root_path_data(root)
        ordering = _root_ordering(root)
        distinct_expression = _root_group_distinct_expression()
        duplicate_expressions = _root_duplicate_expressions(root)
        root_row_set = RowSetObligation(
            anchor_step_id=annotation.step_id,
            required_rows=row_count,
            generation_rows=generation_row_count,
            row_scopes=_row_scopes("out", generation_row_count),
            relations=root_relations,
            join_facts=join_facts,
            path_predicates=path_predicates,
            distinct_expression=distinct_expression,
            duplicate_expressions=duplicate_expressions,
            ordering=ordering,
        )
        obligations: list[OperatorObligation] = [
            OperatorObligation(
                kind="root_result",
                step_id=annotation.step_id,
                site="root_result",
                row_count=row_count,
            ),
            OperatorObligation(
                kind="row_set",
                step_id=annotation.step_id,
                site="root_result",
                row_count=generation_row_count,
                row_set=root_row_set,
            ),
        ]
        group_keys = _aggregate_group_keys_for_having()
        for index, constraint in enumerate(_having_cardinality_constraints()):
            required_rows = constraint.get("required_rows")
            if not isinstance(required_rows, int) or required_rows <= 0:
                continue
            counted = constraint.get("argument")
            counted_expression = (
                _column_expr_from_id(counted)
                if isinstance(counted, ColumnId)
                else None
            )
            having_generation_rows = required_rows
            having_row_set = RowSetObligation(
                anchor_step_id=annotation.step_id,
                required_rows=required_rows,
                generation_rows=having_generation_rows,
                row_scopes=_row_scopes(f"having{index}_", having_generation_rows),
                relations=root_relations,
                join_facts=join_facts,
                path_predicates=path_predicates,
                group_keys=group_keys,
                counted_expression=counted_expression,
                distinct_expression=(
                    counted_expression.copy()
                    if constraint.get("distinct") and counted_expression is not None
                    else None
                ),
            )
            obligations.append(
                OperatorObligation(
                    kind="row_set",
                    step_id=annotation.step_id,
                    site="having",
                    row_count=having_generation_rows,
                    row_set=having_row_set,
                )
            )
        return tuple(obligations)

    for step in plan.ordered_steps:
        annotation = plan.annotation_for(step)
        tables = annotation.source_relations

        if isinstance(step, Filter) and step.condition is not None:
            atoms = decompose_atoms(step.condition)
            _add_node(step, "Filter", "filter", step.condition, atoms, tables)
            for atom in scalar_subquery_atoms(step.condition):
                _add_node(step, "Filter", "scalar_subquery", atom, (atom,), tables)

        elif isinstance(step, Join):
            for join_data in (step.joins or {}).values():
                condition = join_data.get("condition")
                if condition is not None and isinstance(condition, exp.Expression):
                    atoms = decompose_atoms(condition)
                    _add_node(step, "Join", "join_on", condition, atoms, tables)

        elif isinstance(step, Aggregate):
            if step.group or step.aggregations:
                group_pred = exp.Literal.string("GROUP_SIZE")
                group_count_pred = exp.Literal.string("GROUP_COUNT")
                _add_node(
                    step,
                    "Aggregate",
                    "group",
                    group_pred,
                    (group_pred, group_count_pred),
                    tables,
                )
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
                aggregate_inputs = tuple(
                    argument
                    for aggregation in aggregate_expressions
                    for function in aggregation.find_all(exp.AggFunc)
                    for argument in (function.this,)
                    if argument is not None
                    and not isinstance(argument, (exp.Star, exp.Distinct))
                )
                if aggregate_inputs:
                    _add_node(
                        step,
                        "Aggregate",
                        "aggregate_input",
                        exp.Literal.string("AGGREGATE_INPUT"),
                        aggregate_inputs,
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
            project_expressions = project_coverage_expressions(step)
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
            discovery="planned",
            origin=f"planner:{type(plan.root).__name__}",
        )

    return tree


__all__ = [
    "BranchCoverageRecorder",
    "BranchPathBuilder",
    "BranchTreeBuilder",
    "CoverageAnalyzer",
    "build_branch_tree",
    "decompose_atoms",
    "scalar_subquery_atoms",
]
