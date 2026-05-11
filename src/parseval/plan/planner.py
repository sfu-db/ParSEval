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
import math
import typing as t
from dataclasses import dataclass, field

from sqlglot import alias, exp
from sqlglot.helper import name_sequence
from sqlglot.optimizer.eliminate_joins import join_condition
from sqlglot.optimizer.scope import Scope, traverse_scope

from parseval.helper import normalize_name


class Plan:
    def __init__(self, expression: exp.Expression) -> None:
        """Build a plan for ``expression``.

        Every subquery reference (``FROM (...)``, ``EXISTS``, ``IN (...)``,
        scalar subqueries, CTEs) is lowered into a :class:`SubPlan` step
        attached as an extra dependency of its consumer. Correlation
        columns for each inner scope are precomputed from
        ``sqlglot.optimizer.scope.traverse_scope`` at build time and baked
        into the corresponding :class:`SubPlan`, so downstream consumers
        never need to consult a separate scope graph.
        """
        self.expression = expression.copy()
        self._correlations = _build_correlation_map(self.expression)
        self.root = Step.from_expression(
            self.expression, correlations=self._correlations
        )
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
        ``condition``, ``projected_columns``, ``source_tables`` (recursive
        base-table resolution), and ``referenced_columns``.
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
        annotations: t.Dict[int, "StepAnnotations"] = {}
        for index, step in enumerate(self.ordered_steps):
            annotations[id(step)] = StepAnnotations(
                step_id=f"step_{index}",
                step_type=type(step).__name__,
                step_name=getattr(step, "name", "") or "",
                condition=getattr(step, "condition", None),
                projected_columns=_projected_columns(step),
                source_tables=_source_tables(step),
                referenced_columns=_unique_columns(_step_expressions(step)),
            )
        self._annotations = annotations

    def __repr__(self) -> str:
        return f"Plan\n----\n{repr(self.root)}"


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
        elif isinstance(expression, exp.Union):
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
        aggregations: t.Set[exp.Expression] = set()
        next_operand_name = name_sequence("_a_")

        def extract_agg_operands(expr: exp.Expression) -> bool:
            agg_funcs = tuple(_iter_outer_agg_funcs(expr))
            if agg_funcs:
                aggregations.add(expr)

            for agg in agg_funcs:
                for operand in agg.unnest_operands():
                    if isinstance(operand, exp.Column):
                        continue
                    if operand not in operands:
                        operands[operand] = next_operand_name()

                    operand.replace(exp.column(operands[operand], quoted=True))

            return bool(agg_funcs)

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
                if extract_agg_operands(
                    exp.alias_(having.this, "_h", quoted=True)
                ):
                    aggregate.condition = exp.column("_h", step.name, quoted=True)
                else:
                    aggregate.condition = having.this

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
                        node.replace(exp.column(name, step.name))

            if aggregate.condition is not None:
                for node in aggregate.condition.walk():
                    name = intermediate.get(node) or intermediate.get(node.name)
                    if name:
                        node.replace(exp.column(name, step.name))

            aggregate.add_dependency(step)
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
            step.joins[join.alias_or_name] = {
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
        self.source_name: t.Optional[str] = None
        self.joins: t.Dict[str, t.Dict[str, t.List[str] | exp.Expression]] = {}

    def _to_s(self, indent: str) -> t.List[str]:
        lines = [f"{indent}Source: {self.source_name or self.name}"]
        for name, join in self.joins.items():
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

    The condition expression may reference aggregate-output columns (such as
    the synthetic ``_h`` alias that :meth:`Step.from_expression` creates when
    the ``HAVING`` clause itself contains aggregate functions) or
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
        assert isinstance(expression, exp.Union)

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
        consumer.add_dependency(subplan)


# ---------------------------------------------------------------------------
# Scope / correlation helpers (formerly in parseval.plan.graph)
# ---------------------------------------------------------------------------


def _scope_local_base_tables(scope: Scope) -> t.Set[str]:
    names: t.Set[str] = set()
    for table in scope.expression.find_all(exp.Table):
        names.add(normalize_name(table.name))
    return names


def _parent_alias_base_tables(scope: Scope) -> t.Dict[str, str]:
    if scope.parent is None:
        return {}
    alias_to_table: t.Dict[str, str] = {}
    for source in scope.parent.expression.find_all(exp.Table):
        alias_name = source.alias_or_name
        if not alias_name:
            continue
        alias_to_table[normalize_name(alias_name)] = normalize_name(source.name)
    return alias_to_table


def _projection_column_keys(scope: Scope) -> t.Set[t.Tuple[str, str]]:
    if not isinstance(scope.expression, exp.Select):
        return set()
    keys: t.Set[t.Tuple[str, str]] = set()
    for projection in scope.expression.expressions:
        for column in projection.find_all(exp.Column):
            keys.add((normalize_name(column.table or ""), normalize_name(column.name)))
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
                keys.add((normalize_name(column.table or ""), normalize_name(column.name)))
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

    local_base_tables = _scope_local_base_tables(scope)
    parent_alias_to_base = _parent_alias_base_tables(scope)
    projection_keys = _projection_column_keys(scope)
    non_projection_keys = _non_projection_column_keys(scope)

    surviving: t.List[exp.Column] = []
    seen: t.Set[str] = set()
    for column in external_columns:
        key = column.sql()
        if key in seen:
            continue
        table_name = normalize_name(column.table) if column.table else None
        column_key = (normalize_name(column.table or ""), normalize_name(column.name))
        if column_key in projection_keys and column_key not in non_projection_keys:
            continue
        if not table_name:
            surviving.append(column)
            seen.add(key)
            continue
        parent_base = parent_alias_to_base.get(table_name)
        if parent_base is not None and parent_base in local_base_tables:
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


def _build_correlation_map(
    expression: exp.Expression,
) -> t.Dict[int, t.Tuple[exp.Column, ...]]:
    """Precompute ``expression_id → correlation_columns`` for every subscope.

    ``Plan.__init__`` calls this once; ``Step.from_expression`` then uses
    it to fill ``SubPlan.correlation`` without re-traversing scopes for
    each subquery.
    """
    result: t.Dict[int, t.Tuple[exp.Column, ...]] = {}
    for scope in traverse_scope(expression):
        result[id(scope.expression)] = correlation_columns(scope)
    return result


# ---------------------------------------------------------------------------
# Annotations (formerly in parseval.plan.scope_plan)
# ---------------------------------------------------------------------------


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
    referenced_columns: t.Tuple[exp.Column, ...] = ()
    projected_columns: t.Tuple[str, ...] = ()
    source_tables: t.Tuple[str, ...] = ()
    flags: t.FrozenSet[str] = frozenset()
    metadata: t.Dict[str, t.Any] = field(default_factory=dict)


def _unique_columns(
    expressions: t.Iterable[exp.Expression],
) -> t.Tuple[exp.Column, ...]:
    seen: t.Set[str] = set()
    columns: t.List[exp.Column] = []
    for expression in expressions:
        if expression is None:
            continue
        for column in expression.find_all(exp.Column):
            sql = column.sql()
            if sql in seen:
                continue
            seen.add(sql)
            columns.append(column)
    return tuple(columns)


def _projected_columns(step: "Step") -> t.Tuple[str, ...]:
    projections = getattr(step, "projections", None) or []
    names: t.List[str] = []
    for projection in projections:
        alias_name = projection.alias_or_name
        if alias_name:
            names.append(alias_name)
    return tuple(names)


def _source_tables(step: "Step") -> t.Tuple[str, ...]:
    """Base tables this step ultimately reads from.

    ``Scan`` yields its underlying table; ``Join`` yields the source plus
    every joined alias; ``Filter`` / ``Having`` / ``Project`` / ``Limit`` /
    ``Sort`` / ``Aggregate`` recurse through chain dependencies to the
    leaves. ``Scan`` with an ``exp.Subquery`` source resolves through its
    ``SubPlan(TABLE)`` inner tree. ``SubPlan`` branches of other kinds are
    intentionally not followed — they represent subqueries whose rows
    belong to a different scope.
    """
    names: t.List[str] = []
    seen: t.Set[str] = set()

    def add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    def collect(current: "Step") -> None:
        source = getattr(current, "source", None)
        if isinstance(source, exp.Table):
            add(source.name)
        if isinstance(current, Scan):
            for dependency in current.chain_dependencies:
                collect(dependency)
            for sub in current.subplan_dependencies:
                if sub.kind is SubPlanKind.TABLE:
                    collect(sub.inner)
            return
        if isinstance(current, Join):
            for dependency in sorted(
                current.chain_dependencies,
                key=lambda dep: dep.name or "",
            ):
                collect(dependency)
            for join_name in sorted((getattr(current, "joins", None) or {}).keys()):
                add(join_name)
            return
        if isinstance(current, SubPlan):
            return
        for dependency in current.chain_dependencies:
            collect(dependency)

    collect(step)
    return tuple(names)


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

    return tuple(expressions)


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
    ready: t.List["Step"] = sorted(
        [step for step, degree in indegree.items() if degree == 0],
        key=sort_key,
    )
    ordered: t.List["Step"] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        newly_ready: t.List["Step"] = []
        for dependent in sorted(current.dependents, key=sort_key):
            if dependent not in indegree:
                continue
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                newly_ready.append(dependent)
        ready = sorted(ready + newly_ready, key=sort_key)
    return ordered
