"""
ParSEval logical-plan builder.

This module forks sqlglot's :mod:`sqlglot.planner` and restructures the plan
tree around ParSEval's needs for plan-aware branch-coverage analysis.

Compared to upstream sqlglot, the plan produced here:

* always ends in a :class:`Project` step (carrying the SELECT list and the
  ``DISTINCT`` flag), instead of attaching ``step.projections`` to whichever
  operator happens to be on top;
* lifts ``WHERE`` into a dedicated :class:`Filter` step above the scan/join
  rather than fusing it as ``step.condition`` on :class:`Scan`/:class:`Join`;
* lifts ``HAVING`` into a dedicated :class:`Having` step above
  :class:`Aggregate`;
* lifts ``LIMIT`` (and ``OFFSET``) into a dedicated :class:`Limit` step
  rather than setting ``step.limit`` on the top operator;
* models ``SELECT DISTINCT`` as ``Project.distinct = True`` rather than by
  wrapping the tail in an extra :class:`Aggregate`;
* surfaces every subquery reference (``FROM (SELECT ...)``, ``EXISTS``,
  ``IN (SELECT ...)``, scalar subquery in projections/filters/ON/HAVING,
  CTE references) as a first-class :class:`SubPlan` step that owns its
  inner plan. :class:`SubPlan` nodes are attached as *additional*
  dependencies of whichever outer step consumes them, producing a fan-in
  shape that the ``chain_dependencies`` / ``subplan_dependencies``
  accessors on :class:`Step` separate cleanly.

Logical shape for a SELECT:

    Limit?                (if LIMIT / OFFSET)
     └── Project          (always; projections + distinct)
          └── Sort?       (if ORDER BY)
               └── Having?    (if HAVING)
                    └── Aggregate?   (if GROUP BY or aggregate funcs)
                         └── Filter?  (if WHERE)
                              └── Join? / Scan / SetOperation
                                   └── Scan  (one per join input)

The plan's DAG does **not** descend into :class:`SubPlan` inner plans:
each ``SubPlan`` holds its inner plan's root in ``SubPlan.inner`` but has
no chain dependencies of its own, so the outer :class:`Plan`'s topological
walk sees ``SubPlan`` as a leaf. Consumers that want to recurse into a
subquery do so by walking ``subplan.inner`` explicitly.
"""

from __future__ import annotations

import enum
import heapq
import math
import typing as t
from dataclasses import dataclass, field

from sqlglot import alias, exp
from sqlglot.helper import name_sequence
from sqlglot.optimizer.eliminate_joins import join_condition
from sqlglot.optimizer.scope import Scope, traverse_scope

from parseval.dtype import (
    DataType,
    infer_semantic_datatype_from_literal,
    merge_semantic_datatypes,
    semantic_cast_datatype,
)
from parseval.identity import (
    PARSEVAL_COLUMN_ID,
    PARSEVAL_SEMANTIC_DATATYPE,
    ColumnId,
    ColumnKind,
    RelationId,
    RelationKind,
    column_id,
    column_identity,
    identifier_name,
    relation_id,
)
if t.TYPE_CHECKING:
    from parseval.instance import Instance

_PARSEVAL_AGGREGATE_ORDINAL = "parseval_aggregate_ordinal"

class Plan:
    def __init__(self, expression: exp.Expression, instance: Instance | None = None) -> None:
        """Build a plan for ``expression``.

        Every subquery reference (``FROM (...)``, ``EXISTS``, ``IN (...)``,
        scalar subqueries, CTEs) is lowered into a :class:`SubPlan` step
        attached as an extra dependency of its consumer. Correlation
        columns for each inner scope are precomputed from
        ``sqlglot.optimizer.scope.traverse_scope`` at build time and baked
        into the corresponding :class:`SubPlan`, so downstream consumers
        never need to consult a separate scope graph.
        """
        self.expression = _normalize_planner_expression(expression.copy())
        self._correlations, self._scope_sources = _build_scope_index(self.expression)
        self.root = Step.from_expression(
            self.expression, correlations=self._correlations
        )
        self._qualifier_index = _build_qualifier_index(self.root, instance)
        self._instance = instance
        self._dag: t.Dict["Step", t.Set["Step"]] = {}
        self._ordered_steps: t.Optional[t.Tuple["Step", ...]] = None
        self._annotations: t.Optional[t.Dict[int, "StepAnnotations"]] = None

    @property
    def dag(self) -> t.Dict["Step", t.Set["Step"]]:
        if not self._dag:
            dag: t.Dict["Step", t.Set["Step"]] = {}
            nodes = {self.root}

            while nodes:
                node = nodes.pop()
                dag[node] = set()

                for dep in node.dependencies:
                    dag[node].add(dep)
                    nodes.add(dep)

            self._dag = dag

        return self._dag

    @property
    def leaves(self) -> t.Iterator["Step"]:
        return (node for node, deps in self.dag.items() if not deps)

    @property
    def ordered_steps(self) -> t.Tuple["Step", ...]:
        """Deterministic topological order of the outer DAG.

        ``SubPlan`` nodes appear as leaves in this ordering because their
        ``dependencies`` set is empty by construction. The inner plans
        live under ``SubPlan.inner`` and are walked separately (usually by
        the encoder / analysis layer recursing into each ``SubPlan``).
        """
        if self._ordered_steps is None:
            self._ordered_steps = tuple(_topological_order(self))
        return self._ordered_steps

    def annotation_for(self, step: "Step") -> "StepAnnotations":
        """Return the cached :class:`StepAnnotations` for ``step``.

        Annotations are computed lazily on first access. They carry
        ``step_id`` (stable index), ``step_type``, ``step_name``,
        ``condition``, ``projected_columns``, ``source_relations``, and
        ``referenced_columns``.
        """
        if self._annotations is None:
            self._annotate()
        assert self._annotations is not None
        return self._annotations[id(step)]

    @property
    def annotations(self) -> t.Dict[int, "StepAnnotations"]:
        """All step annotations, keyed by ``id(step)``."""
        if self._annotations is None:
            self._annotate()
        assert self._annotations is not None
        return self._annotations

    def _annotate(self) -> None:
        from parseval.plan.rex import set_column_meta

        annotations: t.Dict[int, "StepAnnotations"] = {}

        def annotate_step(
            step: "Step",
            step_id: str,
            *,
            outer_step: "Step | None" = None,
        ) -> None:
            if id(step) in annotations:
                return
            exprs = _step_expressions(step)
            _prepare_step_identity(step, self._instance, plan=self)
            # SubPlan inner plans live in a separate scope — recurse
            # through all inner steps (not just the root, since the
            # correlated condition is typically on a Filter dependency).
            if isinstance(step, SubPlan):
                resolve_exprs = tuple(
                    col for col in (getattr(step, "correlation", None) or ())
                    if isinstance(col, exp.Expression)
                )
                inner = step.inner
                if inner is not None:
                    inner_steps = _identity_order(inner)
                    for inner_step in inner_steps:
                        _prepare_step_identity(inner_step, self._instance, plan=self)
                        for inner_expr in _step_expressions(inner_step):
                            for col in _iter_scope_columns(inner_expr):
                                resolved_id = _resolve_column_id(
                                    col,
                                    inner_step,
                                    self._instance,
                                    allow_unresolved=True,
                                    plan=self,
                                )
                                if resolved_id is None:
                                    continue
                                col.meta[PARSEVAL_COLUMN_ID] = resolved_id
                                _enrich_identity_column(col, resolved_id, self._instance, set_column_meta, DataType)
                        if isinstance(inner_step, Project):
                            inner_step.output_column_ids = _build_project_output_columns(
                                inner_step,
                                self._instance,
                            )
                            # Propagate updated output_column_ids to downstream
                            # steps (Limit, Sort) that copy from Project.
                            _propagate_output_columns(inner_step)
                    step.output_column_ids = tuple(
                        getattr(inner, "output_column_ids", ())
                    )
                    inner_expression = _subplan_scope_expression(step)
                    if inner_expression is not None:
                        for col in _iter_scope_columns(inner_expression):
                            resolved_id = _resolve_scope_column_id(
                                col,
                                inner,
                                self._instance,
                                allow_unresolved=True,
                                plan=self,
                            )
                            if resolved_id is None:
                                continue
                            col.meta[PARSEVAL_COLUMN_ID] = resolved_id
                            _enrich_identity_column(
                                col,
                                resolved_id,
                                self._instance,
                                set_column_meta,
                                DataType,
                            )
            else:
                resolve_exprs = exprs
            # For SubPlan correlation columns, resolve against the
            # consumer (outer step) which has visible columns.
            resolve_target = step
            if isinstance(step, SubPlan) and step.consumer is not None:
                _prepare_step_identity(step.consumer, self._instance, plan=self)
                resolve_target = step.consumer
            for expr in resolve_exprs:
                for col in _iter_scope_columns(expr):
                    if not col.name:
                        continue
                    allow_unresolved = isinstance(step, SubPlan) or (
                        isinstance(step, Aggregate)
                        and _is_synthetic_operand_name(col.name)
                    )
                    resolved_id = _resolve_column_id(
                        col,
                        resolve_target,
                        self._instance,
                        allow_unresolved=allow_unresolved or outer_step is not None,
                        plan=self,
                    )
                    if resolved_id is None and outer_step is not None:
                        _prepare_step_identity(outer_step, self._instance, plan=self)
                        resolved_id = _resolve_column_id(
                            col,
                            outer_step,
                            self._instance,
                            allow_unresolved=allow_unresolved,
                            plan=self,
                        )
                    if resolved_id is None:
                        continue
                    col.meta[PARSEVAL_COLUMN_ID] = resolved_id
                    _enrich_identity_column(col, resolved_id, self._instance, set_column_meta, DataType)
            if isinstance(step, SubPlan) and step.inner is not None and not isinstance(step.inner, SetOperation):
                inner_expression = _subplan_scope_expression(step)
                if inner_expression is not None:
                    for col in _iter_scope_columns(inner_expression):
                        if col.table and col.name and column_identity(col) is None and not _is_synthetic_operand_name(col.name):
                            _resolve_scope_column_id(
                                col,
                                step.inner,
                                self._instance,
                                allow_unresolved=False,
                                plan=self,
                            )
            if isinstance(step, Project):
                step.output_column_ids = _build_project_output_columns(
                    step,
                    self._instance,
                )
            # Enrich operand columns inside aggregations so they carry
            # type metadata for downstream constraint generation.
            if isinstance(step, Aggregate):
                for operand in step.operands:
                    if not isinstance(operand, exp.Alias) or not operand.alias:
                        continue
                    for agg in step.aggregations:
                        for col in agg.find_all(exp.Column):
                            if col.name == operand.alias:
                                cid = col.meta.get(PARSEVAL_COLUMN_ID)
                                if isinstance(cid, ColumnId):
                                    _enrich_identity_column(
                                        col, cid, self._instance,
                                        set_column_meta, DataType,
                                    )
            semantic_datatypes = _infer_semantic_datatypes(exprs)
            metadata = _generation_metadata(step, self._instance, plan=self)
            if semantic_datatypes:
                metadata["semantic_datatypes"] = semantic_datatypes

            annotations[id(step)] = StepAnnotations(
                step_id=step_id,
                step_type=type(step).__name__,
                step_name=getattr(step, "name", "") or "",
                condition=getattr(step, "condition", None),
                projected_columns=_projected_column_ids(step),
                referenced_columns=_unique_column_ids(exprs),
                source_relations=_source_relations(step),
                metadata=metadata,
            )

        def annotate_inner_steps(
            parent_id: str,
            root: "Step",
            outer_step: "Step | None",
        ) -> None:
            for inner_index, inner_step in enumerate(_identity_order(root)):
                inner_id = f"{parent_id}.inner_{inner_index}"
                annotate_step(
                    inner_step,
                    inner_id,
                    outer_step=outer_step,
                )
                if isinstance(inner_step, SubPlan) and inner_step.inner is not None:
                    nested_outer = inner_step.consumer or outer_step
                    annotate_inner_steps(inner_id, inner_step.inner, nested_outer)

        for index, step in enumerate(self.ordered_steps):
            step_id = f"step_{index}"
            annotate_step(step, step_id)
            if isinstance(step, SubPlan) and step.inner is not None:
                annotate_inner_steps(step_id, step.inner, step.consumer)
        self._annotations = annotations

    def __repr__(self) -> str:
        return f"Plan\n----\n{repr(self.root)}"


def _normalize_planner_expression(expression: exp.Expression) -> exp.Expression:
    """Normalize parser dialect variants into the planner's preferred AST."""

    def transform(node: exp.Expression) -> exp.Expression:
        normalized = _normalize_strftime(node)
        return normalized if normalized is not None else node

    return expression.transform(transform)


def _normalize_strftime(node: exp.Expression) -> exp.Expression | None:
    if not isinstance(node, exp.Anonymous) or str(node.name).upper() != "STRFTIME":
        return None
    args = list(node.expressions)
    if len(args) != 2:
        return None
    fmt, value = args
    if not isinstance(fmt, exp.Expression) or not isinstance(value, exp.Expression):
        return None
    return exp.TimeToStr(this=value.copy(), format=fmt.copy())


class Step:
    """Base class for every plan node.

    See the module docstring for the full tree shape. Subclasses only add
    their operator-specific fields; the common ones (``name``,
    ``dependencies``, ``dependents``, ``projections``, ``limit``,
    ``condition``) stay on the base so that generic traversals (e.g. in
    ``plan/scope_plan.py``) can keep working uniformly.
    """

    @classmethod
    def from_expression(
        cls,
        expression: exp.Expression,
        ctes: t.Optional[t.Dict[str, "SubPlan"]] = None,
        correlations: t.Optional[t.Dict[int, t.Tuple[exp.Column, ...]]] = None,
    ) -> "Step":
        """Build a plan DAG from ``expression``.

        The expression's tables and subqueries must be aliased. Example::

            SELECT x.a, SUM(x.b)
            FROM x AS x
            JOIN y AS y ON x.a = y.a
            WHERE x.a > 0
            GROUP BY x.a
            HAVING SUM(x.b) > 10
            ORDER BY x.a
            LIMIT 5

        produces::

            Limit
              └── Project (a, SUM(b))
                    └── Sort (x.a)
                          └── Having (SUM(x.b) > 10)
                                └── Aggregate (group: x.a, aggs: SUM(b))
                                      └── Filter (x.a > 0)
                                            └── Join (x ⋈ y)
                                                  ├── Scan x
                                                  └── Scan y
        """
        ctes = ctes or {}
        expression = expression.unnest()
        with_ = expression.args.get("with")

        # CTEs break the mold of scope and introduce themselves to all in the context.
        if with_:
            ctes = ctes.copy()
            for cte in with_.expressions:
                cte_root = Step.from_expression(
                    cte.this, ctes, correlations=correlations
                )
                cte_root.name = cte.alias
                cte_subplan = SubPlan(
                    kind=SubPlanKind.CTE,
                    inner=cte_root,
                    anchor=cte,
                    correlation=(),
                    output_columns=_output_columns_of(cte_root),
                    alias=cte.alias,
                )
                cte_subplan.name = cte.alias
                ctes[cte.alias] = cte_subplan

        from_ = expression.args.get("from")

        if isinstance(expression, exp.Select) and from_:
            step = Scan.from_expression(from_.this, ctes, correlations=correlations)
        elif isinstance(expression, (exp.Union, exp.Intersect, exp.Except)):
            step = SetOperation.from_expression(expression, ctes, correlations=correlations)
        else:
            step = Scan()

        joins = expression.args.get("joins")

        if joins:
            join = Join.from_joins(joins, ctes, correlations=correlations)
            join.name = step.name
            join.source_name = step.name
            join.add_dependency(step)
            # Subqueries in JOIN ON predicates land on the Join step.
            for join_args in (getattr(join, "joins", None) or {}).values():
                join_cond = join_args.get("condition")
                if isinstance(join_cond, exp.Expression):
                    _attach_subplans(join, join_cond, ctes, correlations)
            step = join

        # --- extract SELECT-list projections, aggregate operands, aggregations --------
        projections: t.List[exp.Expression] = []
        operands: t.Dict[exp.Expression, str] = {}
        aggregations: t.List[exp.Expression] = []
        next_operand_name = name_sequence("_a_")

        def extract_agg_operands(expr: exp.Expression) -> bool:
            agg_funcs = tuple(_iter_outer_agg_funcs(expr))
            if agg_funcs and expr not in aggregations:
                aggregations.append(expr)

            replace_agg_operands(expr)
            return bool(agg_funcs)

        def replace_agg_operands(expr: exp.Expression) -> None:
            agg_funcs = tuple(_iter_outer_agg_funcs(expr))
            for agg in agg_funcs:
                for operand in agg.unnest_operands():
                    if isinstance(operand, (exp.Column, exp.Distinct)):
                        continue
                    if operand not in operands:
                        operands[operand] = next_operand_name()

                    operand.replace(exp.column(operands[operand], quoted=True))

        def _aggregate_alias_for_having(agg_func: exp.AggFunc) -> t.Tuple[str, int | None]:
            agg_name = _aggregate_function_name(agg_func)
            agg_argument = _aggregate_argument_id(agg_func)
            agg_sql = agg_func.sql()
            for index, aggregation in enumerate(aggregations):
                existing = _direct_aggregate_function(aggregation)
                if existing is not None and existing.sql() == agg_sql:
                    alias_name = aggregation.alias_or_name
                    if alias_name:
                        return alias_name, index
                if (
                    existing is not None
                    and agg_argument is not None
                    and _aggregate_function_name(existing) == agg_name
                    and _aggregate_argument_id(existing) == agg_argument
                ):
                    alias_name = aggregation.alias_or_name
                    if alias_name:
                        return alias_name, index

            base_name = agg_name
            existing_aliases = {
                aggregation.alias_or_name
                for aggregation in aggregations
                if aggregation.alias_or_name
            }
            alias_name = base_name
            suffix = 1
            while alias_name in existing_aliases:
                alias_name = f"{base_name}_{suffix}"
                suffix += 1
            return alias_name, None

        def set_ops_and_aggs(agg_step: "Aggregate") -> None:
            agg_step.operands = tuple(
                alias(operand, alias_) for operand, alias_ in operands.items()
            )
            agg_step.aggregations = list(aggregations)

        for e in expression.expressions:
            if _has_outer_agg(e):
                projections.append(exp.column(e.alias_or_name, step.name, quoted=True))
                extract_agg_operands(e)
            else:
                projections.append(e)

        # --- WHERE -> Filter ---------------------------------------------------------
        where = expression.args.get("where")
        if where:
            filter_step = Filter()
            filter_step.name = step.name
            filter_step.source = step.name
            filter_step.condition = where.this
            filter_step.add_dependency(step)
            _attach_subplans(filter_step, where.this, ctes, correlations)
            step = filter_step

        # --- GROUP BY / aggregations -> Aggregate -----------------------------------
        group = expression.args.get("group")
        having = expression.args.get("having")
        aggregate: t.Optional[Aggregate] = None

        if group or aggregations or (having and _has_outer_agg(having.this)):
            aggregate = Aggregate()
            aggregate.name = step.name
            aggregate.source = step.name

            if having:
                aggregate.condition = having.this
                for agg_func in _iter_outer_agg_funcs(having.this):
                    replace_agg_operands(agg_func)
                    alias_name, aggregate_ordinal = _aggregate_alias_for_having(agg_func)
                    alias_expr = exp.alias_(agg_func.copy(), alias_name, quoted=True)
                    if aggregate_ordinal is None and alias_expr not in aggregations:
                        aggregations.append(alias_expr)
                        aggregate_ordinal = len(aggregations) - 1
                    alias_column = exp.column(alias_name, step.name, quoted=True)
                    if aggregate_ordinal is not None:
                        alias_column.meta[_PARSEVAL_AGGREGATE_ORDINAL] = aggregate_ordinal
                    agg_func.replace(
                        alias_column
                    )

            set_ops_and_aggs(aggregate)

            # give aggregates names and replace projections with references to them
            aggregate.group = {
                f"_g{i}": e
                for i, e in enumerate(group.expressions if group else [])
            }

            intermediate: t.Dict[t.Union[str, exp.Expression], str] = {}
            for k, v in aggregate.group.items():
                intermediate[v] = k
                if isinstance(v, exp.Column):
                    intermediate[v.name] = k

            for projection in projections:
                for node in projection.walk():
                    name = intermediate.get(node)
                    if name:
                        # Preserve the original column's table qualifier.
                        # For non-column expressions (e.g., SUBSTRING), look up
                        # the original GROUP BY expression's table qualifier.
                        if isinstance(node, exp.Column) and node.table:
                            table = node.table
                        else:
                            # Find the matching GROUP BY expression's table qualifier.
                            table = step.name
                            for gk, gv in aggregate.group.items():
                                if gv is node or intermediate.get(gv) == name:
                                    if isinstance(gv, exp.Column) and gv.table:
                                        table = gv.table
                                    elif hasattr(gv, 'find'):
                                        inner_col = gv.find(exp.Column)
                                        if inner_col and inner_col.table:
                                            table = inner_col.table
                                    break
                        node.replace(exp.column(name, table))

            if aggregate.condition is not None:
                for node in aggregate.condition.walk():
                    name = intermediate.get(node) or intermediate.get(node.name)
                    if name:
                        table = node.table if isinstance(node, exp.Column) and node.table else step.name
                        node.replace(exp.column(name, table))

            aggregate.add_dependency(step)
            for aggregation in aggregate.aggregations:
                if isinstance(aggregation, exp.Expression):
                    _attach_subplans(aggregate, aggregation, ctes, correlations)
            step = aggregate

            # lift HAVING out of Aggregate into its own Having step
            if aggregate.condition is not None:
                having_step = Having()
                having_step.name = aggregate.name
                having_step.source = aggregate.name
                having_step.condition = aggregate.condition
                aggregate.condition = None
                having_step.add_dependency(aggregate)
                if having is not None:
                    _attach_subplans(having_step, having.this, ctes, correlations)
                step = having_step
        elif having is not None:
            # HAVING without any aggregate context; treat as a plain Filter-after-scan.
            having_step = Having()
            having_step.name = step.name
            having_step.source = step.name
            having_step.condition = having.this
            having_step.add_dependency(step)
            _attach_subplans(having_step, having.this, ctes, correlations)
            step = having_step

        # --- ORDER BY -> Sort -------------------------------------------------------
        order = expression.args.get("order")
        if order:
            _resolve_order_projection_aliases(
                order.expressions,
                projections,
            )
            if aggregate is not None:
                for i, ordered in enumerate(order.expressions):
                    if extract_agg_operands(
                        exp.alias_(ordered.this, f"_o_{i}", quoted=True)
                    ):
                        ordered.this.replace(
                            exp.column(f"_o_{i}", aggregate.name, quoted=True)
                        )

                set_ops_and_aggs(aggregate)

            sort = Sort()
            sort.name = step.name
            sort.key = order.expressions
            sort.add_dependency(step)
            for ordered in order.expressions:
                _attach_subplans(sort, ordered, ctes, correlations)
            step = sort

        # --- Project (always, for Select) -------------------------------------------
        if isinstance(expression, exp.Select):
            project = Project()
            project.name = step.name
            project.source = step.name
            project.projections = projections
            project.distinct = bool(expression.args.get("distinct"))
            project.add_dependency(step)
            for projection in projections:
                if isinstance(projection, exp.Expression):
                    _attach_subplans(project, projection, ctes, correlations)
            step = project

        # --- LIMIT / OFFSET -> Limit ------------------------------------------------
        limit = expression.args.get("limit")
        offset = expression.args.get("offset")
        if limit or offset:
            limit_step = Limit()
            limit_step.name = step.name
            limit_step.source = step.name
            if limit:
                try:
                    limit_step.limit = int(limit.text("expression"))
                except (TypeError, ValueError):
                    limit_step.limit = math.inf
            if offset:
                try:
                    limit_step.offset = int(offset.text("expression"))
                except (TypeError, ValueError):
                    limit_step.offset = 0
            limit_step.add_dependency(step)
            step = limit_step

        return step

    def __init__(self) -> None:
        self.name: t.Optional[str] = None
        self.dependencies: t.Set["Step"] = set()
        self.dependents: t.Set["Step"] = set()
        self.projections: t.Sequence[exp.Expression] = []
        self.limit: float = math.inf
        self.condition: t.Optional[exp.Expression] = None

    def add_dependency(self, dependency: "Step") -> None:
        self.dependencies.add(dependency)
        dependency.dependents.add(self)

    @property
    def chain_dependencies(self) -> t.Tuple["Step", ...]:
        """Chain (operator) dependencies, excluding :class:`SubPlan` inputs.

        These are the upstream operators that feed rows into this step.
        For a :class:`Scan` leaf this is empty; for a :class:`Join` it's
        the scans (or further joins) being combined; for post-join
        operators (``Filter``/``Aggregate``/...) it's the single parent
        step in the chain.
        """
        return tuple(d for d in self.dependencies if not isinstance(d, SubPlan))

    @property
    def subplan_dependencies(self) -> t.Tuple["SubPlan", ...]:
        """Attached :class:`SubPlan` nodes (subqueries / CTEs consumed here)."""
        return tuple(d for d in self.dependencies if isinstance(d, SubPlan))

    def __repr__(self) -> str:
        return self.to_s()

    def to_s(self, level: int = 0) -> str:
        indent = "  " * level
        nested = f"{indent}    "

        context = self._to_s(f"{nested}  ")

        if context:
            context = [f"{nested}Context:"] + context

        lines = [f"{indent}- {self.id}", *context]

        if self.projections:
            lines.append(f"{nested}Projections:")
            for expression in self.projections:
                lines.append(f"{nested}  - {expression.sql()}")

        if self.condition:
            lines.append(f"{nested}Condition: {self.condition.sql()}")

        if self.limit is not math.inf:
            lines.append(f"{nested}Limit: {self.limit}")

        chain_deps = self.chain_dependencies
        sub_deps = self.subplan_dependencies

        if chain_deps:
            lines.append(f"{nested}Dependencies:")
            for dependency in chain_deps:
                lines.append("  " + dependency.to_s(level + 1))

        if sub_deps:
            lines.append(f"{nested}SubPlans:")
            for sub in sub_deps:
                lines.append("  " + sub.to_s(level + 1))

        return "\n".join(lines)

    @property
    def type_name(self) -> str:
        return self.__class__.__name__

    @property
    def id(self) -> str:
        name = self.name
        name = f" {name}" if name else ""
        return f"{self.type_name}:{name} ({id(self)})"

    def _to_s(self, _indent: str) -> t.List[str]:
        return []


class Scan(Step):
    @classmethod
    def from_expression(
        cls,
        expression: exp.Expression,
        ctes: t.Optional[t.Dict[str, "SubPlan"]] = None,
        correlations: t.Optional[t.Dict[int, t.Tuple[exp.Column, ...]]] = None,
    ) -> "Step":
        table = expression
        alias_ = expression.alias_or_name

        if isinstance(expression, exp.Subquery):
            # FROM (SELECT ...) AS alias: build the inner plan, wrap it in a
            # SubPlan(TABLE), and attach it as a dependency of a Scan whose
            # ``source`` points at the subquery expression. The outer encoder
            # resolves rows by the scan's alias rather than by an underlying
            # table name.
            inner_expr = expression.this
            inner_root = Step.from_expression(
                inner_expr, ctes, correlations=correlations
            )
            subplan = SubPlan(
                kind=SubPlanKind.TABLE,
                inner=inner_root,
                anchor=expression,
                correlation=_lookup_correlation(correlations, inner_expr),
                output_columns=_output_columns_of(inner_root),
                alias=alias_,
            )
            subplan.name = alias_

            step = Scan()
            step.name = alias_
            step.source = expression
            subplan.consumer = step
            step.add_dependency(subplan)
            return step

        step = Scan()
        step.name = alias_
        step.source = expression
        if ctes and table.name in ctes:
            # Reference to a CTE — attach its SubPlan as a dependency. Multiple
            # Scans referencing the same CTE share the same SubPlan instance.
            step.add_dependency(ctes[table.name])

        return step

    def __init__(self) -> None:
        super().__init__()
        self.source: t.Optional[exp.Expression] = None

    def _to_s(self, indent: str) -> t.List[str]:
        return [f"{indent}Source: {self.source.sql() if self.source else '-static-'}"]  # type: ignore


class Join(Step):
    @classmethod
    def from_joins(
        cls,
        joins: t.Iterable[exp.Join],
        ctes: t.Optional[t.Dict[str, "SubPlan"]] = None,
        correlations: t.Optional[t.Dict[int, t.Tuple[exp.Column, ...]]] = None,
    ) -> "Join":
        step = Join()

        for join in joins:
            source_key, join_key, condition = join_condition(join)
            # Create RelationId from the join table expression.
            join_table = join.this
            if isinstance(join_table, exp.Subquery):
                join_table = join_table.this
            join_name = join.alias_or_name
            join_rel = relation_id(
                RelationKind.TABLE,
                identifier_name(join_table.name if isinstance(join_table, exp.Table) else join_name),
                alias=identifier_name(join_name) if isinstance(join_table, exp.Table) and join_name != join_table.name else None,
            )
            step.joins[join_rel] = {
                "side": join.side,  # type: ignore
                "join_key": join_key,
                "source_key": source_key,
                "condition": condition,
            }

            step.add_dependency(
                Scan.from_expression(join.this, ctes, correlations=correlations)
            )

        return step

    def __init__(self) -> None:
        super().__init__()
        self.source_relation: t.Optional[RelationId] = None
        self.joins: t.Dict[RelationId, t.Dict[str, t.Any]] = {}

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Source: {self.source_relation.name.normalized if self.source_relation and self.source_relation.name else self.name}"]
        for rel, join in self.joins.items():
            name = rel.alias.normalized if rel.alias else (rel.name.normalized if rel.name else "?")
            lines.append(f"{indent}{name}: {join['side'] or 'INNER'}")
            join_key = ", ".join(str(key) for key in t.cast(list, join.get("join_key") or []))
            if join_key:
                lines.append(f"{indent}Key: {join_key}")
            if join.get("condition"):
                lines.append(f"{indent}On: {join['condition'].sql()}")  # type: ignore
        return lines


class Filter(Step):
    """Applies a ``WHERE`` predicate on the rows produced by its dependency."""

    def __init__(self) -> None:
        super().__init__()
        self.source: t.Optional[str] = None

    def _to_s(self, indent: str) -> t.List[str]:
        return [f"{indent}Source: {self.source or '-'}"]


class Aggregate(Step):
    def __init__(self) -> None:
        super().__init__()
        self.aggregations: t.List[exp.Expression] = []
        self.operands: t.Tuple[exp.Expression, ...] = ()
        self.group: t.Dict[str, exp.Expression] = {}
        self.source: t.Optional[str] = None

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Aggregations:"]

        for expression in self.aggregations:
            lines.append(f"{indent}  - {expression.sql()}")

        if self.group:
            lines.append(f"{indent}Group:")
            for expression in self.group.values():
                lines.append(f"{indent}  - {expression.sql()}")
        if self.operands:
            lines.append(f"{indent}Operands:")
            for expression in self.operands:
                lines.append(f"{indent}  - {expression.sql()}")

        return lines


class Having(Step):
    """Applies a ``HAVING`` predicate on the output of an :class:`Aggregate`.

    The condition expression may reference aggregate-output columns or
    ``GROUP BY`` column aliases (``_g0``, ``_g1``, ...).
    """

    def __init__(self) -> None:
        super().__init__()
        self.source: t.Optional[str] = None

    def _to_s(self, indent: str) -> t.List[str]:
        return [f"{indent}Source: {self.source or '-'}"]


class Sort(Step):
    def __init__(self) -> None:
        super().__init__()
        self.key = None

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Key:"]

        for expression in self.key:  # type: ignore
            lines.append(f"{indent}  - {expression.sql()}")

        return lines


class Project(Step):
    """Emits the final SELECT list and handles ``DISTINCT``.

    Exactly one ``Project`` is emitted for every :class:`sqlglot.exp.Select`
    at the top of its dependency chain.
    """

    def __init__(self) -> None:
        super().__init__()
        self.source: t.Optional[str] = None
        self.distinct: bool = False

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Source: {self.source or '-'}"]
        if self.distinct:
            lines.append(f"{indent}Distinct: True")
        return lines


class Limit(Step):
    """Caps the row count and optionally skips an ``OFFSET`` prefix."""

    def __init__(self) -> None:
        super().__init__()
        self.source: t.Optional[str] = None
        self.offset: int = 0

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Source: {self.source or '-'}"]
        if self.offset:
            lines.append(f"{indent}Offset: {self.offset}")
        return lines


class SetOperation(Step):
    def __init__(
        self,
        op: t.Type[exp.Expression],
        left: str | None,
        right: str | None,
        distinct: bool = False,
    ) -> None:
        super().__init__()
        self.op = op
        self.left = left
        self.right = right
        self.distinct = distinct

    @classmethod
    def from_expression(
        cls,
        expression: exp.Expression,
        ctes: t.Optional[t.Dict[str, "SubPlan"]] = None,
        correlations: t.Optional[t.Dict[int, t.Tuple[exp.Column, ...]]] = None,
    ) -> "SetOperation":
        assert isinstance(expression, (exp.Union, exp.Intersect, exp.Except))

        left = Step.from_expression(expression.left, ctes, correlations=correlations)
        # SELECT 1 UNION SELECT 2  <-- these subqueries don't have names
        left.name = left.name or "left"
        right = Step.from_expression(expression.right, ctes, correlations=correlations)
        right.name = right.name or "right"
        step = cls(
            op=expression.__class__,
            left=left.name,
            right=right.name,
            distinct=bool(expression.args.get("distinct")),
        )

        step.add_dependency(left)
        step.add_dependency(right)

        # NOTE: LIMIT / OFFSET on the union itself is handled uniformly by the
        # outer ``Step.from_expression``, which wraps this step in a ``Limit``.

        return step

    def _to_s(self, indent: str) -> t.List[str]:
        lines = []
        if self.distinct:
            lines.append(f"{indent}Distinct: {self.distinct}")
        return lines

    @property
    def type_name(self) -> str:
        return self.op.__name__


class SubPlanKind(enum.Enum):
    """Kinds of subquery references that :class:`SubPlan` represents."""

    TABLE = "table"    # FROM (SELECT ...) [AS alias] or JOIN (SELECT ...)
    SCALAR = "scalar"  # (SELECT col FROM ...) used as a value expression
    EXISTS = "exists"  # [NOT] EXISTS (SELECT ...)
    IN = "in"          # x [NOT] IN (SELECT ...)
    CTE = "cte"        # WITH cte_name AS (SELECT ...)


class SubPlan(Step):
    """A first-class reference to a subquery / CTE within a plan.

    ``SubPlan`` carries the subquery's inner plan root (``inner``), the SQL
    AST node that anchors it in the outer query (``anchor``), the subset
    of outer columns it truly correlates against (``correlation``; empty
    means non-correlated), and the schema the outer sees (``output_columns``
    and, for table/CTE kinds, ``alias``).

    It is always attached as an *extra* dependency of the outer step that
    consumes the subquery — never as a chain dependency. The outer plan's
    DAG treats ``SubPlan`` as a leaf: ``SubPlan.dependencies`` is empty and
    the inner plan's steps are reached only through ``SubPlan.inner``.
    Consumers iterate them via :attr:`Step.chain_dependencies` (which
    skips ``SubPlan``) and :attr:`Step.subplan_dependencies` (which returns
    them).
    """

    def __init__(
        self,
        kind: SubPlanKind,
        inner: Step,
        anchor: exp.Expression,
        correlation: t.Iterable[exp.Column] = (),
        output_columns: t.Iterable[str] = (),
        alias: t.Optional[str] = None,
    ) -> None:
        super().__init__()
        self.kind = kind
        self.inner = inner
        self.anchor = anchor
        self.correlation: t.Tuple[exp.Column, ...] = tuple(correlation)
        self.output_columns: t.Tuple[str, ...] = tuple(output_columns)
        self.alias = alias
        self.consumer: Step | None = None

    @property
    def correlated(self) -> bool:
        return bool(self.correlation)

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Kind: {self.kind.value}"]
        if self.alias:
            lines.append(f"{indent}Alias: {self.alias}")
        if self.output_columns:
            lines.append(f"{indent}Output: {', '.join(self.output_columns)}")
        if self.correlation:
            cols = ", ".join(column.sql() for column in self.correlation)
            lines.append(f"{indent}Correlation: {cols}")
        lines.append(f"{indent}Inner:")
        lines.append("  " + self.inner.to_s(level=1))
        return lines

    @property
    def type_name(self) -> str:
        return f"SubPlan[{self.kind.value}]"


# ---------------------------------------------------------------------------
# Subquery lowering helpers
# ---------------------------------------------------------------------------


def _iter_outer_agg_funcs(
    expression: exp.Expression,
) -> t.Iterator[exp.AggFunc]:
    """Yield every :class:`exp.AggFunc` in ``expression`` that belongs to the
    outer scope.

    ``sqlglot``'s ``find_all(exp.AggFunc)`` descends into nested subqueries,
    which would cause the planner to treat a scalar subquery like
    ``(SELECT MAX(x) FROM u)`` as if its ``MAX`` were an outer aggregation.
    This walk stops at :class:`exp.Subquery` / :class:`exp.Exists` /
    :class:`exp.In` (with a subquery query) boundaries so each scope's
    aggregates are analysed in isolation.
    """
    stack: t.List[exp.Expression] = [expression]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.AggFunc):
            yield node
            # AggFunc operands may themselves contain further AggFuncs
            # (e.g. ``SUM(a + AVG(b))`` — rare but legal). Continue descent.
        if isinstance(node, (exp.Subquery, exp.Exists)):
            continue
        if isinstance(node, exp.In) and isinstance(
            node.args.get("query"), exp.Expression
        ):
            continue
        for child in node.args.values():
            if isinstance(child, exp.Expression):
                stack.append(child)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, exp.Expression):
                        stack.append(item)


def _has_outer_agg(expression: exp.Expression) -> bool:
    """``True`` when ``expression`` contains an outer-scope aggregate."""
    for _ in _iter_outer_agg_funcs(expression):
        return True
    return False


def _projection_order_expression(projection: exp.Expression) -> exp.Expression:
    if isinstance(projection, exp.Alias):
        return projection.this.copy()
    return projection.copy()


def _resolve_order_projection_aliases(
    ordered_expressions: t.Sequence[exp.Expression],
    projections: t.Sequence[exp.Expression],
) -> None:
    projection_aliases: t.Dict[str, exp.Expression] = {}
    ambiguous_aliases: t.Set[str] = set()
    for projection in projections:
        alias_name = getattr(projection, "alias_or_name", None)
        if not alias_name:
            continue
        alias_key = identifier_name(alias_name, dialect=None).normalized
        if alias_key in projection_aliases:
            ambiguous_aliases.add(alias_key)
            continue
        projection_aliases[alias_key] = _projection_order_expression(projection)

    for ordered in ordered_expressions:
        order_expression = getattr(ordered, "this", None)
        if not isinstance(order_expression, exp.Column) or order_expression.table:
            continue
        order_key = identifier_name(order_expression.this, dialect=None).normalized
        if order_key in ambiguous_aliases:
            continue
        projection_expression = projection_aliases.get(order_key)
        if projection_expression is not None:
            order_expression.replace(projection_expression.copy())


def _output_columns_of(step: Step) -> t.Tuple[str, ...]:
    """Return the aliases the inner plan's Project will expose.

    Walks through any Limit/Sort wrappers down to the Project step, which
    is the canonical carrier of output column labels under the new plan
    shape. Returns an empty tuple for plans with no identifiable Project
    (e.g. a bare ``SetOperation``).
    """
    visited: t.Set[int] = set()
    stack: t.List[Step] = [step]
    while stack:
        current = stack.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, Project):
            return tuple(
                projection.alias_or_name
                for projection in current.projections
                if getattr(projection, "alias_or_name", None)
            )
        stack.extend(current.chain_dependencies)
    return ()


def _lookup_correlation(
    correlations: t.Optional[t.Dict[int, t.Tuple[exp.Column, ...]]],
    inner_expr: exp.Expression,
) -> t.Tuple[exp.Column, ...]:
    """Return the true correlation columns for ``inner_expr``'s scope."""
    if correlations is None:
        return ()
    return correlations.get(id(inner_expr), ())


def _iter_subquery_sites(
    expression: exp.Expression,
) -> t.Iterator[t.Tuple[exp.Expression, SubPlanKind]]:
    """Yield top-level subquery references in ``expression``.

    Each yield is ``(anchor_node, kind)`` where ``anchor_node`` is the
    ``exp.Exists`` / ``exp.In`` / ``exp.Subquery`` appearing in the outer
    expression. This function does **not** descend into nested subqueries
    — each subquery owns its own lowering via :class:`SubPlan`.
    """
    stack: t.List[exp.Expression] = [expression]
    while stack:
        node = stack.pop()

        if isinstance(node, exp.Exists):
            yield node, SubPlanKind.EXISTS
            continue

        if isinstance(node, exp.In):
            query = node.args.get("query")
            if isinstance(query, exp.Expression):
                yield node, SubPlanKind.IN
                continue
            # Not a subquery IN (e.g. ``x IN (1, 2, 3)``) — descend normally.

        if isinstance(node, exp.Subquery):
            yield node, SubPlanKind.SCALAR
            continue

        for child in node.args.values():
            if isinstance(child, exp.Expression):
                stack.append(child)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, exp.Expression):
                        stack.append(item)


def _attach_subplans(
    consumer: Step,
    expression: exp.Expression,
    ctes: t.Dict[str, SubPlan],
    correlations: t.Optional[t.Dict[int, t.Tuple[exp.Column, ...]]],
) -> None:
    """Attach ``SubPlan`` dependencies for every subquery in ``expression``.

    The ``anchor`` AST node stays inside ``expression`` unchanged; this
    function just builds the corresponding inner plans and wires them as
    extra dependencies of ``consumer`` so the outer plan DAG surfaces the
    subquery sites as first-class plan nodes.
    """
    for anchor, kind in _iter_subquery_sites(expression):
        if kind is SubPlanKind.EXISTS:
            inner_container = anchor.this
        elif kind is SubPlanKind.IN:
            inner_container = anchor.args.get("query")
        else:  # SCALAR
            inner_container = anchor

        if isinstance(inner_container, exp.Subquery):
            inner_expr = inner_container.this
        else:
            inner_expr = inner_container

        inner_root = Step.from_expression(
            inner_expr, ctes, correlations=correlations
        )
        subplan = SubPlan(
            kind=kind,
            inner=inner_root,
            anchor=anchor,
            correlation=_lookup_correlation(correlations, inner_expr),
            output_columns=_output_columns_of(inner_root),
            alias=(
                anchor.alias_or_name
                if isinstance(anchor, exp.Subquery)
                else None
            ),
        )
        subplan.name = subplan.alias or f"{kind.value}_{id(anchor)}"
        subplan.consumer = consumer
        consumer.add_dependency(subplan)


# ---------------------------------------------------------------------------
# Scope / correlation helpers (formerly in parseval.plan.graph)
# ---------------------------------------------------------------------------


def _projection_column_keys(scope: Scope) -> t.Set[t.Tuple[str, str]]:
    if not isinstance(scope.expression, exp.Select):
        return set()
    keys: t.Set[t.Tuple[str, str]] = set()
    for projection in scope.expression.expressions:
        for column in projection.find_all(exp.Column):
            keys.add((column.table or "", column.name))
    return keys


def _non_projection_column_keys(scope: Scope) -> t.Set[t.Tuple[str, str]]:
    if not isinstance(scope.expression, exp.Select):
        return set()
    keys: t.Set[t.Tuple[str, str]] = set()
    for arg_name, arg_value in scope.expression.args.items():
        if arg_name == "expressions" or arg_value is None:
            continue
        items = arg_value if isinstance(arg_value, list) else [arg_value]
        for item in items:
            if not isinstance(item, exp.Expression):
                continue
            for column in item.find_all(exp.Column):
                keys.add((column.table or "", column.name))
    return keys


def correlation_columns(scope: Scope) -> t.Tuple[exp.Column, ...]:
    """Return the outer-bound columns that a correlated subquery actually uses.

    ``sqlglot``'s ``scope.external_columns`` over-reports: it includes
    columns that merely *look* external but are really resolved against
    one of the scope's own base tables, or that appear only in the
    projection (which downstream decorrelation can safely rewrite). This
    helper filters them down to the columns that are truly outer-bound,
    in the order ``sqlglot`` produced them. An empty tuple means the
    scope is not truly correlated.
    """
    external_columns = list(getattr(scope, "external_columns", []) or [])
    if not external_columns or scope.parent is None:
        return ()

    projection_keys = _projection_column_keys(scope)
    non_projection_keys = _non_projection_column_keys(scope)

    surviving: t.List[exp.Column] = []
    seen: t.Set[str] = set()
    for column in external_columns:
        key = column.sql()
        if key in seen:
            continue
        table_name = column.table if column.table else None
        column_key = (column.table or "", column.name)
        if column_key in projection_keys and column_key not in non_projection_keys:
            continue
        if not table_name:
            surviving.append(column)
            seen.add(key)
            continue
        surviving.append(column)
        seen.add(key)
    return tuple(surviving)


def scope_columns(scope: Scope) -> t.Set[exp.Column]:
    """Deduplicate the columns referenced inside ``scope`` by SQL text.

    Mirrors the helper that lived on the old ``ScopeNode``; callers that
    need the set of columns a scope reads (for resolution or row tagging)
    use this without needing a graph wrapper.
    """
    columns: t.Set[exp.Column] = set()
    column_str: t.Set[str] = set()
    for column in scope.columns:
        if column.sql() in column_str:
            continue
        columns.add(column)
        column_str.add(column.sql())
    return columns


def _iter_all_steps(root: "Step") -> t.Iterator["Step"]:
    """Walk all steps in the DAG including SubPlan inner plans."""
    seen: t.Set[int] = set()

    def walk(step: "Step") -> t.Iterator["Step"]:
        if id(step) in seen:
            return
        seen.add(id(step))
        yield step
        for sub in step.subplan_dependencies:
            yield from walk(sub)
            if sub.inner is not None:
                yield from walk(sub.inner)
        for dep in step.chain_dependencies:
            yield from walk(dep)

    yield from walk(root)


def _build_qualifier_index(
    root: "Step",
    instance: t.Any,
) -> t.Dict[str, RelationId]:
    """Map normalized table name/alias → RelationId from Scan steps.

    Includes both physical table scans and derived-table scans (those with
    subplan dependencies that are *not* CTEs).  CTE scans are skipped so that
    the scope graph resolves ``cte.x`` through to the underlying physical table,
    preserving the existing rewrite behavior.
    """
    if instance is None:
        return {}
    dialect = getattr(instance, "dialect", None)
    index: t.Dict[str, RelationId] = {}
    for step in _iter_all_steps(root):
        if not isinstance(step, Scan):
            continue
        source = step.source
        if isinstance(source, exp.Table) and not step.subplan_dependencies:
            # Physical table scan — index by table name and alias.
            _prepare_step_identity(step, instance)
            rel_id = getattr(step, "relation_id", None)
            if rel_id is None:
                continue
            qualifiers: t.Set[str] = set()
            qualifiers.add(identifier_name(source.name, dialect=dialect).normalized)
            alias_or_name = source.alias_or_name
            if alias_or_name and alias_or_name != source.name:
                qualifiers.add(identifier_name(alias_or_name, dialect=dialect).normalized)
            for q in qualifiers:
                if q not in index:
                    index[q] = rel_id
        elif step.subplan_dependencies:
            # Derived table scan (not CTE) — index by the Scan's name (alias).
            is_cte = any(
                isinstance(dep, SubPlan) and dep.kind == SubPlanKind.CTE
                for dep in step.subplan_dependencies
            )
            if is_cte:
                continue
            _prepare_step_identity(step, instance)
            rel_id = getattr(step, "relation_id", None)
            if rel_id is None:
                continue
            alias = identifier_name(step.name, dialect=dialect).normalized if step.name else None
            if alias and alias not in index:
                index[alias] = rel_id
    return index


def _collect_scope_tables(scope: Scope) -> t.Dict[str, t.Union[exp.Table, Scope]]:
    """Collect all sources from a scope and its parent scopes.

    Walks the scope chain upward so that correlated subquery columns can
    resolve qualifiers that reference outer tables.
    """
    tables: t.Dict[str, t.Union[exp.Table, Scope]] = {}
    current: Scope | None = scope
    while current is not None:
        for key, source in current.sources.items():
            if key not in tables:
                tables[key] = source
        current = current.parent
    return tables


def _build_scope_index(
    expression: exp.Expression,
) -> t.Tuple[
    t.Dict[int, t.Tuple[exp.Column, ...]],
    t.Dict[int, t.Dict[str, t.Union[exp.Table, Scope]]],
]:
    """Build scope index in a single ``traverse_scope`` pass.

    Returns a 2-tuple:
    - correlations: ``expression_id → correlation_columns``
    - scope_sources: ``expression_id → {qualifier → Table | Scope}``
    """
    correlations: t.Dict[int, t.Tuple[exp.Column, ...]] = {}
    scope_sources: t.Dict[int, t.Dict[str, t.Union[exp.Table, Scope]]] = {}

    for scope in traverse_scope(expression):
        scope_key = id(scope.expression)
        correlations[scope_key] = correlation_columns(scope)
        scope_sources[scope_key] = _collect_scope_tables(scope)

    return correlations, scope_sources


def _subplan_scope_expression(step: "SubPlan") -> exp.Expression | None:
    anchor = step.anchor
    if isinstance(anchor, exp.In):
        inner = anchor.args.get("query")
    elif isinstance(anchor, (exp.Exists, exp.Subquery, exp.CTE)):
        inner = anchor.this
    else:
        inner = None
    if isinstance(inner, exp.Subquery):
        inner = inner.this
    return inner if isinstance(inner, exp.Expression) else None


def _resolve_scope_column_id(
    col: exp.Column,
    root: "Step",
    instance: t.Any,
    *,
    allow_unresolved: bool,
    plan: "Plan | None" = None,
) -> ColumnId | None:
    dialect = getattr(instance, "dialect", None)
    name = identifier_name(col.this, dialect=dialect)

    # --- Qualified column: scope-based resolution ---
    if col.table and plan is not None:
        rel_id = _resolve_relation_from_scope(
            col.table, plan._scope_sources, plan._qualifier_index, dialect
        )
        if rel_id is not None:
            seen_steps: t.Set[int] = set()
            stack: t.List[Step] = [root]
            while stack:
                step = stack.pop()
                if id(step) in seen_steps:
                    continue
                seen_steps.add(id(step))
                if isinstance(step, Scan):
                    for c in getattr(step, "output_column_ids", ()):
                        if (
                            _column_name_matches(c, name)
                            and c.relation is not None
                            and c.relation.name == rel_id.name
                            and c.relation.catalog == rel_id.catalog
                            and c.relation.db == rel_id.db
                            and c.relation.scope_id == rel_id.scope_id
                        ):
                            return c
                stack.extend(step.chain_dependencies)

    # --- Walk inner plan for synthetic operands and unqualified columns ---
    candidates: t.List[ColumnId] = []
    seen_steps: t.Set[int] = set()
    stack: t.List[Step] = [root]
    while stack:
        step = stack.pop()
        if id(step) in seen_steps:
            continue
        seen_steps.add(id(step))
        if isinstance(step, Scan):
            candidates.extend(
                c for c in getattr(step, "output_column_ids", ())
                if _column_name_matches(c, name)
            )
        elif isinstance(step, Aggregate):
            if _is_synthetic_operand_name(name.normalized):
                for operand in step.operands:
                    if not isinstance(operand, exp.Alias) or not operand.alias:
                        continue
                    if identifier_name(operand.alias, dialect=dialect).normalized != name.normalized:
                        continue
                    source = _first_resolved_column_id(operand.this, step, instance, plan=plan)
                    candidates.append(
                        column_id(
                            ColumnKind.SYNTHETIC,
                            name,
                            source.relation if source is not None else (_aggregate_output_relation(step, instance) or _step_scope_relation(step)),
                            scope_id=_scope_id_for(step),
                            ordinal=len(candidates),
                            source_column_id=source,
                        )
                    )
        stack.extend(step.chain_dependencies)
    unique = tuple(dict.fromkeys(candidates))
    if col.table:
        qualifier = identifier_name(col.table, dialect=dialect).normalized
        qualified = tuple(
            c for c in unique
            if (
                _relation_matches(c.relation, qualifier, dialect)
                or (
                    c.source_column_id is not None
                    and _relation_matches(c.source_column_id.relation, qualifier, dialect)
                )
            )
        )
        if len(qualified) == 1:
            return qualified[0]
        if qualified:
            unique = qualified
    if len(unique) == 1:
        return unique[0]
    if allow_unresolved:
        return None
    if not unique:
        raise ValueError(f"Unresolved column: {col.sql()}")
    raise ValueError(f"Ambiguous column: {col.sql()}")


@dataclass
class StepAnnotations:
    """Cached derived facts about a :class:`Step` in a :class:`Plan`.

    Populated lazily by :meth:`Plan.annotation_for`. ``step_id`` is a
    stable index-based identifier (``step_0``, ``step_1``, ...) usable in
    decision IDs and coverage records.
    """

    step_id: str
    step_type: str
    step_name: str
    condition: t.Optional[exp.Expression] = None
    referenced_columns: t.Tuple[ColumnId, ...] = ()
    projected_columns: t.Tuple[ColumnId, ...] = ()
    source_relations: t.Tuple[RelationId, ...] = ()
    flags: t.FrozenSet[str] = frozenset()
    metadata: t.Dict[str, t.Any] = field(default_factory=dict)


def _scope_id_for(step: "Step") -> str:
    return f"s{id(step)}"


def _step_scope_relation(step: "Step") -> RelationId:
    """Create a synthetic RelationId for columns without physical backing."""
    return relation_id(RelationKind.SYNTHETIC, None, scope_id=_scope_id_for(step))


def _identity_order(root: "Step") -> t.List["Step"]:
    ordered: t.List[Step] = []
    visited: t.Set[int] = set()

    def visit(step: "Step") -> None:
        if id(step) in visited:
            return
        visited.add(id(step))
        for dep in sorted(step.dependencies, key=lambda item: item.name or ""):
            visit(dep)
        ordered.append(step)

    visit(root)
    return ordered


def _iter_scope_columns(expression: exp.Expression) -> t.Iterator[exp.Column]:
    stack: t.List[exp.Expression] = [expression]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Column):
            yield node
            continue
        if isinstance(node, (exp.Subquery, exp.Exists)):
            continue
        if isinstance(node, exp.In) and isinstance(
            node.args.get("query"),
            exp.Expression,
        ):
            # Unlike EXISTS and scalar Subquery nodes, IN stores an outer-scope
            # operand and its inner query on the same AST node. Traverse only
            # the operand here; the inner plan resolves the query separately.
            if isinstance(node.this, exp.Expression):
                stack.append(node.this)
            continue
        for child in node.args.values():
            if isinstance(child, exp.Expression):
                stack.append(child)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, exp.Expression):
                        stack.append(item)


def _propagate_output_columns(source_step: "Step") -> None:
    """Propagate updated output_column_ids to downstream dependents.

    When a step's output_column_ids are re-computed (e.g., Project after
    identity stamping), downstream steps that copy from it (Limit, Sort)
    need to be updated too.
    """
    for dependent in source_step.dependents:
        if isinstance(dependent, (Limit, Sort)):
            columns: t.List[ColumnId] = []
            for dep in dependent.chain_dependencies:
                columns.extend(getattr(dep, "output_column_ids", ()))
            dependent.output_column_ids = tuple(columns)
            _propagate_output_columns(dependent)


def _prepare_step_identity(step: "Step", instance: t.Any, *, plan: "Plan | None" = None) -> None:
    if getattr(step, "output_column_ids", None) is not None:
        return

    if isinstance(step, SubPlan):
        for inner_step in _identity_order(step.inner):
            _prepare_step_identity(inner_step, instance)
        output_columns = getattr(step.inner, "output_column_ids", ())
        # Resolve source_column_id for columns that don't have it yet.
        # This ensures every output column tracks its physical source.
        inner_agg = next(
            (s for s in _identity_order(step.inner) if isinstance(s, Aggregate)),
            None,
        )
        resolved_columns: t.List[ColumnId] = []
        for col in output_columns:
            src = col.source_column_id
            if src is not None and col.name.normalized.startswith("_"):
                # Synthetic alias with known source: use source name.
                resolved_columns.append(column_id(
                    col.kind, src.name, col.relation,
                    scope_id=col.scope_id, ordinal=col.ordinal,
                    source_column_id=src,
                ))
            elif col.name.normalized.startswith("_") and inner_agg is not None:
                # Synthetic alias without source: resolve from group expression.
                group_expr = inner_agg.group.get(col.name.normalized)
                if group_expr is not None and isinstance(group_expr, exp.Column):
                    group_identity = _group_expression_identity(
                        group_expr,
                        inner_agg,
                        instance,
                        plan=plan,
                    )
                    if group_identity.single_lineage_source is not None:
                        resolved_columns.append(column_id(
                            col.kind,
                            group_identity.single_lineage_source.name,
                            col.relation,
                            scope_id=col.scope_id, ordinal=col.ordinal,
                            source_column_id=group_identity.single_lineage_source,
                        ))
                    else:
                        resolved_columns.append(col)
                else:
                    resolved_columns.append(col)
            else:
                resolved_columns.append(col)
        step.output_column_ids = tuple(resolved_columns)
        step.relation_id = relation_id(
            RelationKind.CTE if step.kind is SubPlanKind.CTE else RelationKind.SUBQUERY,
            identifier_name(step.alias or step.name or step.kind.value, dialect=getattr(instance, "dialect", None)),
            alias=(
                identifier_name(step.alias, dialect=getattr(instance, "dialect", None))
                if step.alias
                else None
            ),
            scope_id=_scope_id_for(step),
        )
        return

    if isinstance(step, Scan):
        _prepare_scan_identity(step, instance)
        return

    for dep in step.chain_dependencies:
        _prepare_step_identity(dep, instance)

    if isinstance(step, Aggregate):
        step.output_column_ids = _build_aggregate_output_columns(step, instance, plan=plan)
    elif isinstance(step, Project):
        step.output_column_ids = _build_project_output_columns(step, instance)
    elif isinstance(step, SetOperation):
        left = next(
            (
                dep
                for dep in step.chain_dependencies
                if dep.name == step.left
            ),
            None,
        )
        if left is None:
            left = next(iter(step.chain_dependencies), None)
        step.output_column_ids = tuple(getattr(left, "output_column_ids", ()))
    else:
        if isinstance(step, Join):
            _prepare_join_identity(step, instance)
        output_columns: t.List[ColumnId] = []
        for dep in step.chain_dependencies:
            output_columns.extend(getattr(dep, "output_column_ids", ()))
        step.output_column_ids = tuple(output_columns)


def _prepare_join_identity(step: "Join", instance: t.Any) -> None:
    dialect = getattr(instance, "dialect", None)
    scans = [
        dep
        for dep in step.chain_dependencies
        if isinstance(dep, Scan) and dep.relation_id is not None
    ]

    source_name = getattr(step, "source_name", None) or step.name
    source_scan = next(
        (
            scan
            for scan in scans
            if source_name and _relation_matches(scan.relation_id, source_name, dialect)
        ),
        None,
    )
    if source_scan is not None:
        step.source_relation = source_scan.relation_id

    rewritten_joins: t.Dict[RelationId, t.Dict[str, t.Any]] = {}
    for join_relation, join_data in step.joins.items():
        scan = next(
            (
                candidate
                for candidate in scans
                if _same_relation_identity(candidate.relation_id, join_relation, dialect)
            ),
            None,
        )
        rewritten_joins[scan.relation_id if scan is not None else join_relation] = join_data
    step.joins = rewritten_joins


def _same_relation_identity(
    candidate: RelationId | None,
    expected: RelationId | None,
    dialect: str | None,
) -> bool:
    if candidate is None or expected is None:
        return False
    if expected.alias is not None:
        return _relation_matches(candidate, expected.alias.raw, dialect)
    qualifiers = []
    if expected.name is not None:
        qualifiers.append(expected.name.raw)
    return any(_relation_matches(candidate, qualifier, dialect) for qualifier in qualifiers)


def _prepare_scan_identity(scan: "Scan", instance: t.Any) -> None:
    source = scan.source
    dialect = getattr(instance, "dialect", None)
    scope_id = _scope_id_for(scan)
    subplans = scan.subplan_dependencies

    if subplans:
        subplan = subplans[0]
        _prepare_step_identity(subplan, instance)
        kind = RelationKind.CTE if subplan.kind is SubPlanKind.CTE else RelationKind.SUBQUERY
        alias_name = scan.name or subplan.alias or subplan.name or kind.value
        rel_id = relation_id(
            kind,
            identifier_name(subplan.alias or subplan.name or alias_name, dialect=dialect),
            alias=identifier_name(alias_name, dialect=dialect),
            scope_id=scope_id,
        )
        scan.relation_id = rel_id
        scan.output_column_ids = tuple(
            column_id(
                ColumnKind.PROJECTED,
                source_col.name,
                rel_id,
                scope_id=scope_id,
                ordinal=index,
                source_column_id=source_col.source_column_id or source_col,
            )
            for index, source_col in enumerate(getattr(subplan, "output_column_ids", ()))
        )
        return

    if isinstance(source, exp.Table):
        physical = instance.table_id(source)
        alias_name = source.alias_or_name
        alias_ident = (
            identifier_name(alias_name, dialect=dialect)
            if alias_name and alias_name != source.name
            else None
        )
        rel_id = relation_id(
            RelationKind.TABLE,
            physical.name,
            catalog=physical.catalog,
            db=physical.db,
            alias=alias_ident,
            scope_id=scope_id,
        )
        scan.relation_id = rel_id
        table_key = instance._identity_key(source, is_table=True)
        table_columns = (instance._ddl_columns or instance.tables).get(table_key, {})
        scan.output_column_ids = tuple(
            _query_column_for_physical(
                instance,
                source,
                rel_id,
                column_key,
                index,
                scope_id,
            )
            for index, column_key in enumerate(table_columns)
        )
        return

    rel_id = relation_id(RelationKind.SYNTHETIC, None, scope_id=scope_id)
    scan.relation_id = rel_id
    scan.output_column_ids = ()


def _query_column_for_physical(
    instance: t.Any,
    table: exp.Table,
    relation: RelationId,
    column_key: str,
    ordinal: int,
    scope_id: str,
) -> ColumnId:
    column_source = instance._column_sources.get(
        (instance._identity_key(table, is_table=True), column_key),
        column_key,
    )
    physical_column = instance.column_id(table, exp.to_identifier(column_source))
    return column_id(
        ColumnKind.PHYSICAL,
        identifier_name(column_key, dialect=getattr(instance, "dialect", None)),
        relation,
        scope_id=scope_id,
        ordinal=ordinal,
        source_column_id=physical_column,
    )


def _build_project_output_columns(step: "Project", instance: t.Any) -> t.Tuple[ColumnId, ...]:
    result: t.List[ColumnId] = []
    dialect = getattr(instance, "dialect", None)
    for ordinal, projection in enumerate(step.projections):
        alias_name = projection.alias_or_name
        if not alias_name:
            continue
        source = None
        if isinstance(projection, exp.Column):
            source = projection.meta.get(PARSEVAL_COLUMN_ID)
        else:
            projection_columns = list(_iter_scope_columns(projection))
            if len(projection_columns) == 1:
                source = projection_columns[0].meta.get(PARSEVAL_COLUMN_ID)
        if (
            isinstance(source, ColumnId)
            and alias_name == source.name.raw
            and source not in result
        ):
            result.append(source)
            continue
        relation = source.relation if isinstance(source, ColumnId) else _step_scope_relation(step)
        result.append(
            column_id(
                ColumnKind.PROJECTED,
                identifier_name(alias_name, dialect=dialect),
                relation,
                scope_id=_scope_id_for(step),
                ordinal=ordinal,
                source_column_id=(
                    source.source_column_id or source
                    if isinstance(source, ColumnId)
                    else None
                ),
            )
        )
    return tuple(result)


@dataclass(frozen=True)
class _GroupExpressionIdentity:
    resolved_sources: t.Tuple[ColumnId, ...]
    kind: ColumnKind
    single_lineage_source: ColumnId | None


def _iter_group_scope_columns(expression: exp.Expression) -> t.Iterator[exp.Column]:
    if isinstance(expression, exp.Column):
        yield expression
        return
    if isinstance(expression, (exp.Subquery, exp.Exists)):
        return
    if isinstance(expression, exp.In) and isinstance(
        expression.args.get("query"),
        exp.Expression,
    ):
        return
    for child in expression.args.values():
        if isinstance(child, exp.Expression):
            yield from _iter_group_scope_columns(child)
        elif isinstance(child, list):
            for item in child:
                if isinstance(item, exp.Expression):
                    yield from _iter_group_scope_columns(item)


def _resolve_group_column_source(
    col: exp.Column,
    step: "Step",
    instance: t.Any,
    *,
    plan: "Plan | None" = None,
) -> "ColumnId | None":
    # Walk dependency chain to find a step that can resolve this column.
    visited: t.Set[int] = set()

    def _try_resolve(s: "Step") -> "ColumnId | None":
        if id(s) in visited:
            return None
        visited.add(id(s))
        resolved = _resolve_column_id(col, s, instance, allow_unresolved=True, plan=plan)
        if resolved is not None:
            return resolved
        # If qualified column failed, try unqualified (strip table qualifier).
        if col.table:
            unqualified = col.copy()
            unqualified.set("table", None)
            resolved = _resolve_column_id(unqualified, s, instance, allow_unresolved=True, plan=plan)
            if resolved is not None:
                return resolved
        for dep in s.chain_dependencies:
            result = _try_resolve(dep)
            if result is not None:
                return result
        return None

    return _try_resolve(step)


def _group_expression_identity(
    expression: exp.Expression,
    step: "Step",
    instance: t.Any,
    *,
    plan: "Plan | None" = None,
) -> _GroupExpressionIdentity:
    """Resolve identity inputs for one GROUP BY expression."""
    resolved_sources: t.List[ColumnId] = []
    seen: t.Set[ColumnId] = set()
    for col in _iter_group_scope_columns(expression):
        source = _resolve_group_column_source(col, step, instance, plan=plan)
        if source is None or source in seen:
            continue
        seen.add(source)
        resolved_sources.append(source)

    sources = tuple(resolved_sources)
    return _GroupExpressionIdentity(
        resolved_sources=sources,
        kind=(
            ColumnKind.PROJECTED
            if isinstance(expression, exp.Column)
            else ColumnKind.DERIVED
        ),
        single_lineage_source=sources[0] if len(sources) == 1 else None,
    )


def _build_aggregate_output_columns(step: "Aggregate", instance: t.Any, *, plan: "Plan | None" = None) -> t.Tuple[ColumnId, ...]:
    result: t.List[ColumnId] = []
    dialect = getattr(instance, "dialect", None)
    aggregate_relation = _aggregate_output_relation(step, instance)

    for ordinal, (name, expression) in enumerate(step.group.items()):
        identity = _group_expression_identity(expression, step, instance, plan=plan)
        source = identity.single_lineage_source
        result.append(
            column_id(
                identity.kind,
                identifier_name(name, dialect=dialect),
                source.relation if source is not None else (aggregate_relation or _step_scope_relation(step)),
                scope_id=_scope_id_for(step),
                ordinal=ordinal,
                source_column_id=source,
            )
        )

    seen_group_names = {column.name.normalized for column in result}
    for aggregation in step.aggregations:
        alias_name = aggregation.alias_or_name
        if not alias_name:
            continue
        name = identifier_name(alias_name, dialect=dialect)
        if name.normalized in seen_group_names:
            continue
        source = _first_resolved_column_id(aggregation, step, instance, plan=plan)
        result.append(
            column_id(
                ColumnKind.AGGREGATE,
                name,
                aggregate_relation or (source.relation if source is not None else _step_scope_relation(step)),
                scope_id=_scope_id_for(step),
                ordinal=len(result),
                source_column_id=source,
            )
        )

    # Include all non-aggregated columns from input tables as pass-through columns.
    # This handles MySQL-style GROUP BY where non-aggregated columns are allowed
    # (they are functionally dependent on the GROUP BY keys).
    seen_column_keys = {
        (column.relation, column.name.normalized)
        for column in result
    }
    for visible_col in _visible_columns(step):
        column_key = (visible_col.relation, visible_col.name.normalized)
        if column_key in seen_column_keys:
            continue
        result.append(
            column_id(
                ColumnKind.PROJECTED,
                visible_col.name,
                visible_col.relation,
                scope_id=_scope_id_for(step),
                ordinal=len(result),
                source_column_id=visible_col,
            )
        )
        seen_column_keys.add(column_key)

    return tuple(result)


def _aggregate_output_relation(step: "Aggregate", instance: t.Any) -> RelationId | None:
    dialect = getattr(instance, "dialect", None)
    visible = _visible_columns(step)
    if step.name:
        for column in visible:
            if _relation_matches(column.relation, step.name, dialect):
                return column.relation
    for column in visible:
        if column.relation is not None:
            return column.relation
    return None


def _first_resolved_column_id(
    expression: exp.Expression,
    step: "Step",
    instance: t.Any,
    *,
    plan: "Plan | None" = None,
) -> ColumnId | None:
    for column in _iter_scope_columns(expression):
        resolved = _resolve_column_id(
            column,
            step,
            instance,
            allow_unresolved=True,
            plan=plan,
        )
        if resolved is not None:
            return resolved.source_column_id or resolved
    return None


def _visible_columns(step: "Step") -> t.Tuple[ColumnId, ...]:
    if isinstance(step, Scan):
        return tuple(getattr(step, "output_column_ids", ()))
    columns: t.List[ColumnId] = []
    for dep in step.chain_dependencies:
        columns.extend(getattr(dep, "output_column_ids", ()))
    return tuple(columns)


def _relation_matches(relation: RelationId | None, qualifier: str, dialect: str | None) -> bool:
    """Check if *relation*'s alias or name matches *qualifier*."""
    if relation is None:
        return False
    key = identifier_name(qualifier, dialect=dialect).normalized
    return (
        relation.alias is not None and relation.alias.normalized == key
    ) or (
        relation.name is not None and relation.name.normalized == key
    )


def _column_name_matches(candidate: ColumnId, name: IdentifierName) -> bool:
    """Check if a ColumnId's name matches the target name.

    Handles projected columns with synthetic ``_g`` aliases by also checking
    ``source_column_id.name``.
    """
    if candidate.name.normalized == name.normalized:
        return True
    source = candidate.source_column_id
    return (
        candidate.kind is ColumnKind.PROJECTED
        and candidate.name.normalized.startswith("_g")
        and source is not None
        and source.name.normalized == name.normalized
    )


def _resolve_relation_from_scope(
    qualifier: str,
    scope_sources: t.Dict[int, t.Dict[str, t.Union[exp.Table, Scope]]],
    qualifier_index: t.Dict[str, RelationId],
    dialect: str | None,
) -> RelationId | None:
    """Resolve a table qualifier to a :class:`RelationId`` via the scope graph.

    The qualifier (from ``col.table``) is first looked up directly in the
    qualifier index (handles aliases like ``T2``).  If not found, the scope
    graph is consulted to map the qualifier to a physical table name, which
    is then looked up in the qualifier index.
    """
    normalized = identifier_name(qualifier, dialect=dialect).normalized

    # Direct lookup: the qualifier itself may be an alias in the index.
    rel_id = qualifier_index.get(normalized)
    if rel_id is not None:
        return rel_id

    # Indirect: resolve qualifier → source table → index.
    for tables in scope_sources.values():
        source = tables.get(normalized)
        if source is None:
            continue
        if isinstance(source, exp.Table):
            source_name = source.alias_or_name
        elif isinstance(source, Scope):
            # Derived table / CTE — the scope's expression carries the alias.
            source_expr = source.expression
            source_name = (
                source_expr.alias_or_name
                if hasattr(source_expr, "alias_or_name")
                else None
            )
        else:
            continue
        if source_name is None:
            continue
        key = identifier_name(source_name, dialect=dialect).normalized
        rel_id = qualifier_index.get(key)
        if rel_id is not None:
            return rel_id
    return None


def _resolve_column_id(
    col: exp.Column,
    step: "Step",
    instance: t.Any,
    *,
    allow_unresolved: bool = False,
    plan: "Plan | None" = None,
) -> ColumnId | None:
    """Resolve a column expression to its :class:`ColumnId`.

    Uses the scope graph from *plan* to resolve the column's table qualifier
    to a physical ``RelationId``, then finds the matching ``ColumnId`` from
    the step's visible columns.
    """
    dialect = getattr(instance, "dialect", None)
    name = identifier_name(col.this, dialect=dialect)

    if col.table and plan is not None:
        rel_id = _resolve_relation_from_scope(
            col.table, plan._scope_sources, plan._qualifier_index, dialect
        )
        if rel_id is not None:
            for c in _visible_columns(step):
                if (
                    _column_name_matches(c, name)
                    and c.relation is not None
                    and c.relation.name == rel_id.name
                    and c.relation.catalog == rel_id.catalog
                    and c.relation.db == rel_id.db
                    and c.relation.scope_id == rel_id.scope_id
                ):
                    return c

    # Unqualified column or scope-based resolution did not find a match.
    candidates = [
        c for c in _visible_columns(step) if _column_name_matches(c, name)
    ]
    if not candidates:
        if allow_unresolved:
            return None
        raise ValueError(f"Unresolved column: {col.sql()}")
    if col.table:
        # Filter by qualifier: match relation alias or name against col.table.
        qualifier = identifier_name(col.table, dialect=dialect).normalized
        qualified = [
            c for c in candidates
            if c.relation is not None and (
                (c.relation.alias is not None and c.relation.alias.normalized == qualifier)
                or (c.relation.name is not None and c.relation.name.normalized == qualifier)
                or (
                    c.source_column_id is not None
                    and c.source_column_id.relation is not None
                    and c.source_column_id.relation.name is not None
                    and c.source_column_id.relation.name.normalized == qualifier
                )
            )
        ]
        if qualified:
            return qualified[0]
        if allow_unresolved:
            return None
        raise ValueError(f"Unresolved column: {col.sql()}")
    relations = {c.relation for c in candidates if c.relation is not None}
    if len(relations) > 1:
        raise ValueError(f"Ambiguous column: {col.sql()}")
    return candidates[0]


def _enrich_identity_column(
    col: exp.Column,
    resolved_id: ColumnId,
    instance: t.Any,
    set_column_meta: t.Callable,
    DataType: t.Any,
) -> None:
    """Set type/nullable/unique metadata on *col* from the catalog.

    Walks the ``source_column_id`` chain to find the first
    :attr:`ColumnKind.PHYSICAL` entry and looks it up in the catalog.
    Columns without a physical backing (aggregate outputs, derived
    expressions) are skipped — they have no catalog entry to enrich from.
    """
    source: ColumnId | None = resolved_id
    for _ in range(10):
        if source is None or source.relation is None:
            return
        if source.kind is ColumnKind.PHYSICAL and source.relation.name is not None:
            # Use the base RelationId (without scope_id or alias) for catalog lookup.
            base_rel = relation_id(
                source.relation.kind,
                source.relation.name,
                catalog=source.relation.catalog,
                db=source.relation.db,
            )
            catalog_column = instance.catalog_column(
                base_rel, exp.to_identifier(source.name.raw)
            )
            dtype = DataType.build(catalog_column.datatype)
            meta = {
                "table": source.relation.name.normalized,
                "nullable": catalog_column.nullable,
                "unique": catalog_column.unique,
                "domain": dtype,
            }
            set_column_meta(col, meta)
            col.type = dtype
            return
        source = source.source_column_id


def _generation_metadata(step: "Step", instance: t.Any, *, plan: "Plan | None" = None) -> t.Dict[str, t.Any]:
    metadata: t.Dict[str, t.Any] = {}
    if isinstance(step, Aggregate):
        aggregation = _aggregation_metadata(step, instance, plan=plan)
        if aggregation["group_keys"] or aggregation["aggregate_outputs"]:
            metadata["aggregation"] = aggregation
    if isinstance(step, Having):
        constraints = _having_constraints(step)
        if constraints:
            metadata["having_constraints"] = constraints
    if isinstance(step, SubPlan):
        metadata["subquery"] = _subquery_metadata(step, instance, plan=plan)
    return metadata


def _aggregation_metadata(step: "Aggregate", instance: t.Any, *, plan: "Plan | None" = None) -> t.Dict[str, t.Any]:
    group_keys = tuple(
        column
        for column in getattr(step, "output_column_ids", ())
        if column.name.normalized in set(step.group.keys())
    )
    group_expressions = {
        column: step.group[column.name.normalized]
        for column in group_keys
        if column.name.normalized in step.group
    }
    group_sources = {
        column: _group_expression_identity(
            step.group[column.name.normalized],
            step,
            instance,
            plan=plan,
        ).resolved_sources
        for column in group_keys
        if column.name.normalized in step.group
    }
    outputs: t.Dict[ColumnId, t.Dict[str, t.Any]] = {}
    aggregate_columns = [
        column
        for column in getattr(step, "output_column_ids", ())
        if column.kind is ColumnKind.AGGREGATE
    ]
    for aggregation, output_column in zip(step.aggregations, aggregate_columns):
        alias_name = aggregation.alias_or_name
        if not alias_name:
            continue
        aggregate_function = _direct_aggregate_function(aggregation)
        if output_column is None or aggregate_function is None:
            continue
        argument = _aggregate_argument_id(aggregate_function)
        outputs[output_column] = {
            "alias": alias_name,
            "output_column": output_column,
            "function": _aggregate_function_name(aggregate_function),
            "argument": argument,
            "distinct": _aggregate_is_distinct(aggregate_function),
            "semantic_datatype": _aggregate_semantic_datatype(
                aggregate_function,
            ),
        }
    return {
        "group_keys": group_keys,
        "group_expressions": group_expressions,
        "group_sources": group_sources,
        "aggregate_outputs": outputs,
    }


def _having_constraints(step: "Having") -> t.Tuple[t.Dict[str, t.Any], ...]:
    aggregate = next(
        (dep for dep in step.chain_dependencies if isinstance(dep, Aggregate)),
        None,
    )
    if aggregate is None or step.condition is None:
        return ()
    aggregate_columns = [
        column
        for column in getattr(aggregate, "output_column_ids", ())
        if column.kind is ColumnKind.AGGREGATE
    ]
    aggregate_bindings = []
    for index, aggregation in enumerate(aggregate.aggregations):
        if not (
            isinstance(aggregation, exp.Alias)
            and aggregation.alias_or_name
            and index < len(aggregate_columns)
        ):
            continue
        aggregate_function = _direct_aggregate_function(aggregation)
        if aggregate_function is None:
            continue
        aggregate_bindings.append(
            (index, aggregation.alias_or_name, aggregate_function, aggregate_columns[index])
        )
    constraints: t.List[t.Dict[str, t.Any]] = []
    for comparison in step.condition.walk():
        if not isinstance(comparison, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            continue
        rewritten = comparison.copy()
        matched = False
        matched_output_column = None
        for column in rewritten.find_all(exp.Column):
            aggregate_binding = _aggregate_binding_for_column(
                column,
                aggregate_bindings,
            )
            if aggregate_binding is None:
                continue
            _, _, aggregate_function, matched_output_column = aggregate_binding
            column.replace(aggregate_function.copy())
            matched = True
        if not matched and _aggregate_comparison_constraint(rewritten) is None:
            continue
        constraint = _aggregate_comparison_constraint(rewritten)
        if constraint is not None:
            if matched_output_column is not None:
                constraint["output_column"] = matched_output_column
            constraints.append(constraint)
    return tuple(constraints)


def _aggregate_binding_for_column(
    column: exp.Column,
    bindings: t.Sequence[t.Tuple[int, str, exp.AggFunc, ColumnId]],
) -> t.Tuple[int, str, exp.AggFunc, ColumnId] | None:
    ordinal = column.meta.get(_PARSEVAL_AGGREGATE_ORDINAL)
    if isinstance(ordinal, int):
        for binding in bindings:
            if binding[0] == ordinal:
                return binding
    alias_matches = [binding for binding in bindings if binding[1] == column.name]
    if len(alias_matches) == 1:
        return alias_matches[0]
    cid = column_identity(column)
    if cid is not None:
        for binding in bindings:
            if binding[3] == cid:
                return binding
    return None


def _condition_column_names(expression: exp.Expression) -> t.Set[str]:
    return {column.name for column in _iter_scope_columns(expression)}


def _aggregate_comparison_constraint(expression: exp.Expression) -> t.Dict[str, t.Any] | None:
    if not isinstance(expression, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return None
    left_agg = _direct_aggregate_function(expression.left)
    right_agg = _direct_aggregate_function(expression.right)
    if left_agg is not None and right_agg is None:
        literal = _literal_value(expression.right)
        aggregate_function = left_agg
    elif right_agg is not None and left_agg is None:
        literal = _literal_value(expression.left)
        aggregate_function = right_agg
    else:
        return None
    if literal is None:
        return None
    function = _aggregate_function_name(aggregate_function)
    constraint = {
        "function": function,
        "argument": _aggregate_argument_id(aggregate_function),
        "distinct": _aggregate_is_distinct(aggregate_function),
        "operator": expression.key,
        "value": literal,
        "semantic_datatype": _aggregate_semantic_datatype(
            aggregate_function,
        ),
    }
    required_rows = _required_rows_for_count(function, expression.key, literal)
    if required_rows is not None:
        constraint["required_rows"] = required_rows
    return constraint


def _required_rows_for_count(function: str, operator: str, value: t.Any) -> int | None:
    if function != "count" or not isinstance(value, int):
        return None
    if operator == "gt":
        return value + 1
    if operator in {"gte", "eq"}:
        return max(value, 0)
    return None


def _literal_value(expression: t.Any) -> t.Any:
    if not isinstance(expression, exp.Literal):
        return None
    if expression.is_string:
        return str(expression.this)
    text = expression.this
    try:
        return int(text)
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return text


def _direct_aggregate_function(expression: exp.Expression) -> exp.AggFunc | None:
    inner = expression.this if isinstance(expression, exp.Alias) else expression
    return inner if isinstance(inner, exp.AggFunc) else None


def _aggregate_function_name(expression: exp.AggFunc) -> str:
    return expression.key.lower()


def _aggregate_argument_id(expression: exp.AggFunc) -> ColumnId | None:
    for column in _iter_scope_columns(expression):
        if _is_synthetic_operand_name(column.name):
            continue
        cid = column.meta.get(PARSEVAL_COLUMN_ID)
        if isinstance(cid, ColumnId):
            return cid
    return None


def _aggregate_is_distinct(expression: exp.AggFunc) -> bool:
    return isinstance(expression.this, exp.Distinct)


def _aggregate_semantic_datatype(expression: exp.AggFunc) -> DataType:
    if isinstance(expression, exp.Count):
        return DataType.build("INT")
    if isinstance(expression, exp.Avg):
        return DataType.build("REAL")
    argument_type = _aggregate_argument_datatype(expression)
    if argument_type is not None:
        return argument_type
    if isinstance(expression, exp.Sum):
        return DataType.build("REAL")
    return DataType.build("UNKNOWN")


def _aggregate_argument_datatype(expression: exp.AggFunc) -> DataType | None:
    for column in _iter_scope_columns(expression):
        if _is_synthetic_operand_name(column.name):
            continue
        meta = column.args.get("_parseval_meta")
        if meta is None:
            continue
        domain = dict(meta).get("domain")
        if domain is not None:
            return DataType.build(domain)
    return None


def _output_column_by_name(step: "Step", name: str) -> ColumnId | None:
    normalized = identifier_name(name).normalized
    for column in getattr(step, "output_column_ids", ()):
        if column.name.normalized == normalized:
            return column
    return None


def _subquery_metadata(step: "SubPlan", instance: t.Any, *, plan: "Plan | None" = None) -> t.Dict[str, t.Any]:
    polarity = _subquery_polarity(step.anchor)
    metadata: t.Dict[str, t.Any] = {
        "kind": step.kind.value,
        "polarity": polarity,
        "cardinality": _subquery_cardinality(step.kind, polarity),
        "output_columns": tuple(getattr(step, "output_column_ids", ())),
        "correlations": _subquery_correlation_links(step, instance, plan=plan),
    }
    if step.kind is SubPlanKind.IN:
        predicate_column = _subquery_predicate_column(step, instance, plan=plan)
        if predicate_column is not None:
            metadata["predicate_column"] = predicate_column
    return metadata


def _subquery_polarity(anchor: exp.Expression) -> str:
    negated = False
    current = getattr(anchor, "parent", None)
    while isinstance(current, exp.Expression):
        if isinstance(current, exp.Not):
            negated = not negated
        current = getattr(current, "parent", None)
    return "negative" if negated else "positive"


def _subquery_cardinality(kind: SubPlanKind, polarity: str) -> str:
    if kind is SubPlanKind.EXISTS:
        return "zero" if polarity == "negative" else "one_or_more"
    if kind is SubPlanKind.IN:
        return "zero_matching" if polarity == "negative" else "one_or_more"
    if kind is SubPlanKind.SCALAR:
        return "one"
    return "many"


def _subquery_predicate_column(step: "SubPlan", instance: t.Any, *, plan: "Plan | None" = None) -> ColumnId | None:
    if not isinstance(step.anchor, exp.In):
        return None
    column = _single_scope_column(step.anchor.this)
    if column is None:
        return None
    return _resolve_outer_column_id(step, column, instance, plan=plan)


def _subquery_correlation_links(
    step: "SubPlan",
    instance: t.Any,
    *,
    plan: "Plan | None" = None,
) -> t.Tuple[t.Dict[str, t.Any], ...]:
    links: t.List[t.Dict[str, t.Any]] = []
    seen: t.Set[t.Tuple[ColumnId, ColumnId, str]] = set()
    for inner_step in _identity_order(step.inner):
        _prepare_step_identity(inner_step, instance, plan=plan)
        for expression in _step_expressions(inner_step):
            for node in _iter_scope_nodes(expression):
                if not isinstance(node, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
                    continue
                left = _single_scope_column(node.left)
                right = _single_scope_column(node.right)
                if left is None or right is None:
                    continue
                link = _correlation_link_for_columns(step, inner_step, left, right, node.key, instance, plan=plan)
                if link is None:
                    link = _correlation_link_for_columns(step, inner_step, right, left, node.key, instance, plan=plan)
                if link is None:
                    continue
                key = (link["inner"], link["outer"], link["operator"])
                if key in seen:
                    continue
                seen.add(key)
                links.append(link)
    return tuple(links)


def _correlation_link_for_columns(
    subplan: "SubPlan",
    inner_step: "Step",
    inner_col: exp.Column,
    outer_col: exp.Column,
    operator: str,
    instance: t.Any,
    *,
    plan: "Plan | None" = None,
) -> t.Dict[str, t.Any] | None:
    inner_id = _resolve_column_id(inner_col, inner_step, instance, allow_unresolved=True, plan=plan)
    outer_id = _resolve_outer_column_id(subplan, outer_col, instance, plan=plan)
    if inner_id is None or outer_id is None:
        return None
    return {
        "inner": inner_id,
        "outer": outer_id,
        "operator": operator,
    }


def _resolve_outer_column_id(
    subplan: "SubPlan",
    column: exp.Column,
    instance: t.Any,
    *,
    plan: "Plan | None" = None,
) -> ColumnId | None:
    consumer = getattr(subplan, "consumer", None)
    if consumer is None:
        return None
    _prepare_step_identity(consumer, instance)
    return _resolve_column_id(column, consumer, instance, allow_unresolved=True, plan=plan)


def _is_synthetic_operand_name(name: str) -> bool:
    return name.startswith("_a_") or name.startswith("_o_")


def _infer_semantic_datatypes(
    expressions: t.Iterable[exp.Expression],
) -> t.Dict[ColumnId, DataType]:
    expression_tuple = tuple(expr for expr in expressions if expr is not None)
    candidates: t.Dict[ColumnId, t.List[t.Tuple[int, DataType]]] = {}

    def add(column: exp.Column, datatype: DataType, priority: int) -> None:
        if not _column_accepts_semantic_datatype(column):
            return
        cid = column.meta.get(PARSEVAL_COLUMN_ID)
        if isinstance(cid, ColumnId):
            candidates.setdefault(cid, []).append((priority, DataType.build(datatype)))

    for expression in expression_tuple:
        for node in _iter_scope_nodes(expression):
            if isinstance(node, exp.Cast):
                target = node.args.get("to")
                if isinstance(target, exp.DataType):
                    for column in _iter_scope_columns(node.this):
                        add(column, DataType.build(target), priority=3)
                continue

            temporal_function_type = _temporal_function_semantic_datatype(node)
            if temporal_function_type is not None:
                for column in _iter_scope_columns(node):
                    add(column, temporal_function_type, priority=2)
                continue

            if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
                _collect_range_comparison_semantics(node, add)
                continue

            if isinstance(node, (exp.EQ, exp.NEQ)):
                _collect_equality_comparison_semantics(node, add)
                continue

            if isinstance(node, exp.Between):
                _collect_between_semantics(node, add)

    semantic_datatypes: t.Dict[ColumnId, DataType] = {}
    for cid, column_candidates in candidates.items():
        datatype = _resolve_semantic_datatype(column_candidates)
        if datatype is not None:
            semantic_datatypes[cid] = datatype

    if semantic_datatypes:
        from parseval.plan.rex import set_column_meta
        for expression in expression_tuple:
            for column in _iter_scope_columns(expression):
                cid = column.meta.get(PARSEVAL_COLUMN_ID)
                if isinstance(cid, ColumnId) and cid in semantic_datatypes:
                    semantic_dt = semantic_datatypes[cid]
                    column.meta[PARSEVAL_SEMANTIC_DATATYPE] = semantic_dt
                    # Only override col.type when the catalog type is TEXT
                    # but the semantic type is temporal.  For TEXT columns
                    # the solver must generate strings (the DB stores text),
                    # so we keep col.type=TEXT and let the semantic type
                    # inform boundary value generation and analysis instead.
                    existing_meta = column.args.get("_parseval_meta")
                    if existing_meta is not None:
                        meta = dict(existing_meta)
                        meta["domain"] = semantic_dt
                        set_column_meta(column, meta)
        # Don't wrap literals in CAST — the solver's _coerce_temporal_pair
        # already handles temporal coercion.  CAST wrapping causes the solver
        # to generate datetime objects that serialize differently than the
        # original literal format in SQLite.

    return semantic_datatypes


def _iter_scope_nodes(expression: exp.Expression) -> t.Iterator[exp.Expression]:
    stack: t.List[exp.Expression] = [expression]
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (exp.Subquery, exp.Exists)):
            continue
        if isinstance(node, exp.In) and isinstance(node.args.get("query"), exp.Expression):
            continue
        for child in node.args.values():
            if isinstance(child, exp.Expression):
                stack.append(child)
            elif isinstance(child, list):
                for item in child:
                    if isinstance(item, exp.Expression):
                        stack.append(item)


def _column_accepts_semantic_datatype(column: exp.Column) -> bool:
    meta = column.args.get("_parseval_meta")
    if meta is None:
        return False
    domain = dict(meta).get("domain")
    if domain is None:
        return False
    datatype = DataType.build(domain)
    return datatype.is_type(*DataType.TEXT_TYPES)


def _collect_range_comparison_semantics(
    node: exp.Expression,
    add: t.Callable[[exp.Column, DataType, int], None],
) -> None:
    left_column = _single_scope_column(node.left)
    right_column = _single_scope_column(node.right)
    if left_column is not None and right_column is None:
        datatype = _literal_semantic_datatype(node.right)
        if datatype is not None:
            add(left_column, datatype, 1)
    elif right_column is not None and left_column is None:
        datatype = _literal_semantic_datatype(node.left)
        if datatype is not None:
            add(right_column, datatype, 1)


def _collect_between_semantics(
    node: exp.Between,
    add: t.Callable[[exp.Column, DataType, int], None],
) -> None:
    column = _single_scope_column(node.this)
    if column is None:
        return
    low_type = _literal_semantic_datatype(node.args.get("low"))
    high_type = _literal_semantic_datatype(node.args.get("high"))
    datatype = merge_semantic_datatypes(
        tuple(dtype for dtype in (low_type, high_type) if dtype is not None)
    )
    if datatype is not None:
        add(column, datatype, 1)


def _collect_equality_comparison_semantics(
    node: exp.Expression,
    add: t.Callable[[exp.Column, DataType, int], None],
) -> None:
    left_column = _single_scope_column(node.left)
    right_column = _single_scope_column(node.right)
    if left_column is not None and right_column is None:
        datatype = _literal_semantic_datatype(node.right)
        if datatype is not None:
            add(left_column, datatype, 1)
    elif right_column is not None and left_column is None:
        datatype = _literal_semantic_datatype(node.left)
        if datatype is not None:
            add(right_column, datatype, 1)


def _single_scope_column(expression: t.Any) -> exp.Column | None:
    if not isinstance(expression, exp.Expression):
        return None
    columns = list(_iter_scope_columns(expression))
    return columns[0] if len(columns) == 1 and isinstance(expression, exp.Column) else None


def _wrap_semantic_literal_casts(
    expressions: t.Iterable[exp.Expression],
    semantic_datatypes: t.Mapping[ColumnId, DataType],
) -> None:
    for expression in expressions:
        for node in _iter_scope_nodes(expression):
            if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
                _wrap_binary_semantic_literal(node, semantic_datatypes)
            elif isinstance(node, exp.Between):
                _wrap_between_semantic_literals(node, semantic_datatypes)


def _wrap_binary_semantic_literal(
    node: exp.Binary,
    semantic_datatypes: t.Mapping[ColumnId, DataType],
) -> None:
    left_datatype = _literal_cast_datatype_for_expression(
        node.left,
        semantic_datatypes,
    )
    right_datatype = _literal_cast_datatype_for_expression(
        node.right,
        semantic_datatypes,
    )
    if left_datatype is not None and right_datatype is None:
        wrapped = _semantic_literal_cast(node.right, left_datatype)
        if wrapped is not node.right:
            node.set("expression", wrapped)
    elif right_datatype is not None and left_datatype is None:
        wrapped = _semantic_literal_cast(node.left, right_datatype)
        if wrapped is not node.left:
            node.set("this", wrapped)


def _wrap_between_semantic_literals(
    node: exp.Between,
    semantic_datatypes: t.Mapping[ColumnId, DataType],
) -> None:
    column = _single_scope_column(node.this)
    if column is None:
        return
    datatype = _literal_cast_datatype_for_column(column, semantic_datatypes)
    if datatype is None:
        return
    for key in ("low", "high"):
        value = node.args.get(key)
        wrapped = _semantic_literal_cast(value, datatype)
        if wrapped is not value:
            node.set(key, wrapped)


def _literal_cast_datatype_for_expression(
    expression: t.Any,
    semantic_datatypes: t.Mapping[ColumnId, DataType],
) -> DataType | None:
    column = _single_semantic_column(expression)
    if column is None:
        return None
    if isinstance(expression, exp.Column):
        return _literal_cast_datatype_for_column(column, semantic_datatypes)
    if isinstance(expression, exp.Cast):
        target = expression.args.get("to")
        return (
            semantic_cast_datatype(DataType.build(target))
            if isinstance(target, exp.DataType)
            else None
        )
    if isinstance(expression, exp.Date):
        return DataType.build("DATE")
    if isinstance(expression, exp.Anonymous) and str(expression.name).upper() in {
        "DATETIME",
        "TIMESTAMP",
    }:
        return DataType.build("DATETIME")
    return None


def _literal_cast_datatype_for_column(
    column: exp.Column,
    semantic_datatypes: t.Mapping[ColumnId, DataType],
) -> DataType | None:
    cid = column.meta.get(PARSEVAL_COLUMN_ID)
    if not isinstance(cid, ColumnId):
        return None
    datatype = semantic_datatypes.get(cid)
    if datatype is None:
        return None
    return semantic_cast_datatype(datatype)


def _single_semantic_column(expression: t.Any) -> exp.Column | None:
    if not isinstance(expression, exp.Expression):
        return None
    columns = list(_iter_scope_columns(expression))
    return columns[0] if len(columns) == 1 else None


def _semantic_literal_cast(expression: t.Any, datatype: DataType) -> t.Any:
    if not isinstance(expression, exp.Literal) or not expression.is_string:
        return expression
    return exp.Cast(this=expression.copy(), to=datatype.copy())


def _literal_semantic_datatype(expression: t.Any) -> DataType | None:
    if not isinstance(expression, exp.Literal) or not expression.is_string:
        return None
    return infer_semantic_datatype_from_literal(str(expression.this))


def _temporal_function_semantic_datatype(node: exp.Expression) -> DataType | None:
    if isinstance(node, exp.Date):
        return DataType.build("DATE")
    if isinstance(node, exp.TimeToStr):
        return DataType.build("DATETIME")
    if isinstance(node, exp.Anonymous) and str(node.name).upper() in {
        "DATETIME",
        "TIMESTAMP",
    }:
        return DataType.build("DATETIME")
    return None


def _resolve_semantic_datatype(
    candidates: t.Sequence[t.Tuple[int, DataType]],
) -> DataType | None:
    if not candidates:
        return None
    highest_priority = max(priority for priority, _ in candidates)
    selected = tuple(
        datatype for priority, datatype in candidates if priority == highest_priority
    )
    return merge_semantic_datatypes(selected)


def _unique_column_ids(
    expressions: t.Iterable[exp.Expression],
) -> t.Tuple[ColumnId, ...]:
    seen: t.Set[ColumnId] = set()
    columns: t.List[ColumnId] = []
    for expression in expressions:
        if expression is None:
            continue
        for column in _iter_scope_columns(expression):
            cid = column.meta.get(PARSEVAL_COLUMN_ID)
            if isinstance(cid, ColumnId) and cid not in seen:
                seen.add(cid)
                columns.append(cid)
    return tuple(columns)


def _projected_column_ids(step: "Step") -> t.Tuple[ColumnId, ...]:
    return tuple(getattr(step, "output_column_ids", ()))


def _source_relations(step: "Step") -> t.Tuple[RelationId, ...]:
    seen: t.Set[RelationId] = set()
    relations: t.List[RelationId] = []
    if isinstance(step, (Limit, Sort)):
        for dep in step.chain_dependencies:
            for relation in _source_relations(dep):
                if relation not in seen:
                    seen.add(relation)
                    relations.append(relation)
        return tuple(relations)
    for column in _visible_columns(step):
        if column.relation is not None and column.relation not in seen:
            seen.add(column.relation)
            relations.append(column.relation)
    return tuple(relations)


def _step_expressions(step: "Step") -> t.Tuple[exp.Expression, ...]:
    expressions: t.List[exp.Expression] = []

    condition = getattr(step, "condition", None)
    if condition is not None:
        expressions.append(condition)

    projections = getattr(step, "projections", None) or []
    expressions.extend(
        projection for projection in projections if isinstance(projection, exp.Expression)
    )

    if isinstance(step, Join):
        for join_data in (getattr(step, "joins", None) or {}).values():
            expressions.extend(join_data.get("source_key", []))
            expressions.extend(join_data.get("join_key", []))
            join_cond = join_data.get("condition")
            if isinstance(join_cond, exp.Expression):
                expressions.append(join_cond)

    if isinstance(step, Aggregate):
        group = getattr(step, "group", None) or {}
        expressions.extend(
            value for value in group.values() if isinstance(value, exp.Expression)
        )
        aggregations = getattr(step, "aggregations", None) or []
        expressions.extend(
            agg for agg in aggregations if isinstance(agg, exp.Expression)
        )
        operands = getattr(step, "operands", None) or ()
        expressions.extend(
            operand for operand in operands if isinstance(operand, exp.Expression)
        )

    if isinstance(step, Sort):
        for ordered in getattr(step, "key", None) or ():
            if isinstance(ordered, exp.Expression):
                expressions.append(ordered)

    if isinstance(step, SubPlan):
        anchor = getattr(step, "anchor", None)
        if isinstance(anchor, exp.Expression):
            expressions.append(anchor)
        for col in (getattr(step, "correlation", None) or ()):
            if isinstance(col, exp.Expression):
                expressions.append(col)

    return tuple(expressions)


def _collect_inner_steps(root: "Step") -> t.List["Step"]:
    """Collect all steps in a SubPlan's inner plan (root + all dependencies)."""
    steps: t.List["Step"] = []
    visited: t.Set[int] = set()

    def _walk(s: "Step") -> None:
        if id(s) in visited:
            return
        visited.add(id(s))
        steps.append(s)
        for dep in s.chain_dependencies:
            _walk(dep)

    _walk(root)
    return steps


# ---------------------------------------------------------------------------
# Topological order (formerly in scope_plan._order_steps)
# ---------------------------------------------------------------------------


def _topological_order(plan: "Plan") -> t.List["Step"]:
    """Deterministic topological walk of ``plan.dag``.

    Uses Kahn's algorithm with a stable tie-break on ``(type, name, id)``
    so the resulting order is reproducible across runs.
    """

    def sort_key(step: "Step") -> t.Tuple[str, str, int]:
        return (type(step).__name__, step.name or "", id(step))

    indegree: t.Dict["Step", int] = {
        step: len(step.dependencies) for step in plan.dag
    }
    heap = [
        (sort_key(step), step)
        for step, degree in indegree.items() if degree == 0
    ]
    heapq.heapify(heap)
    ordered: t.List["Step"] = []
    while heap:
        _, current = heapq.heappop(heap)
        ordered.append(current)
        for dependent in current.dependents:
            if dependent not in indegree:
                continue
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(heap, (sort_key(dependent), dependent))
    return ordered
