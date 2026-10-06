"""Compile qualified SQL queries into the checked term algebra."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from sqlglot import exp, parse
from sqlglot.errors import ParseError
from sqlglot.optimizer import annotate_types, qualify

from parseval.errors import ErrorCode, fail
from parseval.identifiers import Identifier
from parseval.parser.context import LoweringSession
from parseval.parser.expression import ExpressionCompiler
from parseval.parser.scope import ColumnBinding, EmitEnvironment, FieldSlot, OuterScope, Relation
from parseval.terms.arena import TermArena
from parseval.terms.builder import TermRef
from parseval.terms.sorts import BagSort, ScalarSort
from parseval.terms.terms import TermId

from .aggregates import AggregateLowering
from .analysis import Projection, SelectAnalyzer
from .inference import SortInference
from .normalize import EXPANDED_STAR, normalize_syntax
from .projections import ProjectionLowering
from .sources import SourceLowering
from .windows import WindowLowering


class QueryCompiler(SortInference, SourceLowering, AggregateLowering, WindowLowering, ProjectionLowering):
    """Lower one qualified query; each mixin lowers one family of clauses."""

    __slots__ = (
        "context",
        "scalar",
        "analyzer",
        "expressions",
        "active_ctes",
    )

    def __init__(self, context: LoweringSession) -> None:
        self.context = context
        self.analyzer = SelectAnalyzer(context.dialect)
        self.active_ctes: Mapping[str, Relation] = {}
        self.expressions = ExpressionCompiler(
            context,
            relations=self,
            expression_key=self.analyzer.key,
        )
        self.scalar = self.expressions.scalar

    def lower(
        self,
        expression: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...] = (),
        ctes: Mapping[str, Relation] | None = None,
    ) -> Relation:
        while isinstance(expression, exp.Paren):
            expression = expression.this
        cte_bindings = {} if ctes is None else ctes
        previous_ctes = self.active_ctes
        self.active_ctes = cte_bindings
        try:
            if isinstance(expression, exp.Subquery):
                return self._joined_source(
                    expression,
                    outer_scopes=outer_scopes,
                    ctes=cte_bindings,
                )
            with_clause = expression.args.get("with")
            if isinstance(with_clause, exp.With):
                if with_clause.args.get("recursive"):
                    self.context.unsupported(
                        "Recursive CTEs require fixed-point U-Semiring semantics",
                        with_clause,
                    )
                return self._ctes(
                    expression,
                    tuple(with_clause.expressions),
                    0,
                    dict(cte_bindings),
                    outer_scopes,
                )
            if isinstance(expression, exp.Select):
                return self._select(
                    expression,
                    outer_scopes=outer_scopes,
                    ctes=cte_bindings,
                )
            if isinstance(expression, (exp.Union, exp.Intersect, exp.Except)):
                return self._set_operation(
                    expression,
                    outer_scopes=outer_scopes,
                    ctes=cte_bindings,
                )
            if isinstance(expression, exp.Values):
                return self._values(expression, outer_scopes=outer_scopes)
            if isinstance(expression, exp.Table):
                return self._joined_source(
                    expression,
                    outer_scopes=outer_scopes,
                    ctes=cte_bindings,
                )
            self.context.unsupported(
                "Unsupported query root",
                expression,
            )
        finally:
            self.active_ctes = previous_ctes

    def as_bag(self, relation: Relation) -> TermRef:
        sort = self.context.arena[self.context.builder.resolve(relation.term)].sort
        if isinstance(sort, BagSort):
            return relation.term
        return self.context.builder.forget_order(relation.term)

    def _select(
        self,
        select: exp.Select,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        for key, message in (
            ("windows", "Unresolved named window declarations"),
            ("connect", "Hierarchical queries require recursive IR semantics"),
        ):
            node = select.args.get(key)
            if node:
                self.context.unsupported(message, node)

        source = self._from(select, outer_scopes=outer_scopes, ctes=ctes)
        environment = self._environment(source, None, outer_scopes=outer_scopes)
        where = select.args.get("where")
        if isinstance(where, exp.Where):
            source, environment = self._filter(where.this, environment)

        facts = self.analyzer.analyze(select)
        projections = self._projections(
            tuple(select.expressions),
            source,
            expanded_star=bool(select.meta.get(EXPANDED_STAR)),
        )

        working = source
        if facts.requires_aggregation:
            working, environment = self._aggregate(source, facts, outer_scopes=outer_scopes)
            if facts.having is not None:
                working, environment = self._filter(facts.having, environment)
        elif facts.having is not None:
            self.context.unsupported(
                "HAVING requires GROUP BY or an aggregate",
                facts.having,
                code=ErrorCode.UNSUPPORTED_AGGREGATION,
            )
        if facts.windows:
            working, environment = self._window(working, environment, facts.windows)
        if facts.qualify is not None:
            working, environment = self._filter(facts.qualify, environment)

        order = select.args.get("order")
        hidden = self._hidden_order_projections(order, projections)
        if select.args.get("distinct"):
            if hidden:
                grouped, environment = self._distinct_groups(
                    working, environment, projections, hidden, outer_scopes=outer_scopes
                )
                ordered = self._order_and_limit(select, grouped, environment, projections, order)
                return self._project(ordered, replace(environment, relation=ordered), projections)
            projected = self._project(working, environment, projections)
            projected = replace(projected, term=self.context.builder.distinct(projected.term))
            environment = self._projection_environment(projected, projections, outer_scopes=outer_scopes)
            return self._order_and_limit(select, projected, environment, projections, order)

        if isinstance(order, exp.Order):
            decorated = (*projections, *hidden)
            working = self._project(working, environment, decorated)
            environment = self._projection_environment(working, decorated, outer_scopes=outer_scopes)
        ordered = self._order_and_limit(select, working, environment, projections, order)
        return self._project(ordered, replace(environment, relation=ordered), projections)

    def _filter(
        self, condition: exp.Expression, environment: EmitEnvironment
    ) -> tuple[Relation, EmitEnvironment]:
        """Keep the rows of ``environment.relation`` that satisfy ``condition``."""
        relation = replace(
            environment.relation,
            term=self.context.builder.filter(
                environment.relation.term,
                lambda row: self.expressions.lower_condition(condition, replace(environment, row=row)),
            ),
        )
        return relation, replace(environment, relation=relation)

    def _ctes(
        self,
        query: exp.Expression,
        cte_nodes: tuple[exp.CTE, ...],
        index: int,
        bindings: dict[str, Relation],
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        """Bind ``cte_nodes[index:]`` in order around the query body."""
        if index == len(cte_nodes):
            body = query.copy()
            body.set("with", None)
            return self.lower(body, outer_scopes=outer_scopes, ctes=bindings)

        cte = cte_nodes[index]
        definition = self.lower(
            cte.this,
            outer_scopes=outer_scopes,
            ctes=bindings,
        )
        alias = self.context.dialect.identifier(
            cte.args["alias"].this,
        )
        body_result: list[Relation] = []

        def body(variable: TermRef) -> TermRef:
            qualifiers = frozenset({(alias.text,)})
            bound = Relation(
                variable,
                definition.schema,
                tuple(
                    ColumnBinding(
                        column.name,
                        column.sort,
                        qualifiers,
                        column.collation,
                    )
                    for column in definition.columns
                ),
            )
            nested_bindings = dict(bindings)
            nested_bindings[alias.text] = bound
            result = self._ctes(
                query,
                cte_nodes,
                index + 1,
                nested_bindings,
                outer_scopes,
            )
            body_result.append(result)
            return result.term

        term = self.context.builder.let_rel(definition.term, body)
        result = body_result[0]
        return replace(result, term=term)

    def _set_operation(
        self,
        expression: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        left = self.lower(
            expression.this,
            outer_scopes=outer_scopes,
            ctes=ctes,
        )
        right = self.lower(
            expression.expression,
            outer_scopes=outer_scopes,
            ctes=ctes,
        )
        left_fields = self.context.catalog.context.schema(left.schema).fields
        right_fields = self.context.catalog.context.schema(right.schema).fields
        if len(left_fields) != len(right_fields):
            fail(
                ErrorCode.TYPE_ERROR,
                "Set-operation operands have different schemas",
                node=expression,
            )
        common_fields: list[ScalarSort] = []
        for left_sort, right_sort in zip(left_fields, right_fields, strict=True):
            common = self.context.dialect.common_scalar_sort(left_sort, right_sort)
            if common is None:
                fail(
                    ErrorCode.TYPE_ERROR,
                    "Set-operation operands have incompatible column types",
                    node=expression,
                )
            common_fields.append(common)
        target_fields = tuple(common_fields)
        left = self._cast_columns(left, target_fields, expression)
        right = self._cast_columns(right, target_fields, expression)
        left_term = self.as_bag(left)
        right_term = self.as_bag(right)
        # In the pinned SQLGlot AST, Intersect and Except subclass Union.
        # Dispatch on the actual set operator, not the shared AST base class.
        if type(expression) is exp.Union:
            term = self.context.builder.union_all(left_term, right_term)
            if expression.args.get("distinct") is not False:
                term = self.context.builder.distinct(term)
        else:
            if expression.args.get("distinct") is False:
                self.context.unsupported(
                    f"{type(expression).__name__.upper()} ALL requires "
                    "bag monus semantics",
                    expression,
                    code=ErrorCode.UNSUPPORTED_QUERY,
                )
            left_term = self.context.builder.distinct(left_term)
            right_term = self.context.builder.distinct(right_term)

            def matching_right(left_row: TermRef) -> TermRef:
                def matches(right_row: TermRef) -> TermRef:
                    predicates = tuple(
                        self.context.builder.is_not_distinct(
                            self.context.builder.field(left_row, index),
                            self.context.builder.field(right_row, index),
                        )
                        for index in range(len(target_fields))
                    )
                    predicate = predicates[0]
                    for candidate in predicates[1:]:
                        predicate = self.context.builder.and3(predicate, candidate)
                    return predicate

                return self.context.builder.filter(right_term, matches)

            join = (
                self.context.builder.semi_join
                if isinstance(expression, exp.Intersect)
                else self.context.builder.anti_join
            )
            term = join(left_term, matching_right)
        relation = Relation(term, left.schema, left.columns)
        projections = tuple(
            Projection(
                exp.column(column.name.text),
                column.name,
                column.name.text,
            )
            for column in left.columns
        )
        environment = self._projection_environment(
            relation, projections, outer_scopes=outer_scopes
        )
        return self._order_and_limit(
            expression,
            relation,
            environment,
            projections,
            expression.args.get("order"),
        )

    def _cast_columns(
        self,
        relation: Relation,
        target_fields: tuple[ScalarSort, ...],
        node: exp.Expression,
    ) -> Relation:
        source_fields = self.context.catalog.context.schema(relation.schema).fields
        target_schema = self.context.schema_for_sorts(target_fields)
        source = self.as_bag(relation)
        if source_fields != target_fields:
            source = self.context.builder.map(
                source,
                lambda row: self.context.builder.row(
                    target_schema,
                    tuple(
                        self.context.cast_term(
                            self.context.builder.field(row, index),
                            actual,
                            target,
                            node,
                        )
                        for index, (actual, target) in enumerate(
                            zip(source_fields, target_fields, strict=True)
                        )
                    ),
                ),
            )
        return Relation(
            source,
            target_schema,
            tuple(
                replace(column, sort=sort)
                for column, sort in zip(
                    relation.columns, target_fields, strict=True
                )
            ),
        )

    def _environment(
        self,
        relation: Relation,
        expressions: Mapping[str, FieldSlot] | None,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> EmitEnvironment:
        return EmitEnvironment(
            relation,
            relation.term,
            {} if expressions is None else expressions,
            outer_scopes,
        )


@dataclass(frozen=True, slots=True)
class QueryColumn:
    """One output column of a lowered query."""

    name: Identifier
    sort: ScalarSort


@dataclass(frozen=True, slots=True)
class LoweredQuery:
    """A query as a checked term: ``root`` in ``arena``, with its output columns."""

    arena: TermArena
    root: TermId
    columns: tuple[QueryColumn, ...]


def lower_query(sql: str, catalog, *, ignore_root_limit: bool = False) -> LoweredQuery:
    """Parse SQL with SQLGlot and lower the qualified query into checked terms.

    Generation may ignore the outermost LIMIT to grow multiple output rows.
    Nested query limits, ordering, and offsets retain their normal semantics.
    """
    try:
        statements = parse(sql, read=catalog.dialect.name)
    except ParseError as error:
        fail(ErrorCode.PARSE_ERROR, str(error), cause=error)
    if len(statements) != 1 or statements[0] is None:
        fail(ErrorCode.INVALID_INPUT, "Expected exactly one SQL query")
    tree = statements[0]
    if not isinstance(
        tree,
        (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery, exp.Values),
    ):
        fail(
            ErrorCode.UNSUPPORTED_QUERY,
            "Unsupported query root",
            node=tree,
        )

    if ignore_root_limit:
        outer = tree
        while isinstance(outer, exp.Subquery) and outer.args.get("limit") is None:
            outer = outer.this
        if isinstance(outer.args.get("limit"), exp.Limit):
            outer.set("limit", None)

    tree = normalize_syntax(tree, dialect=catalog.dialect.name)
    mapping = catalog.to_schema_mapping()
    try:
        tree = qualify.qualify(
            tree,
            schema=mapping,
            dialect=catalog.dialect.name,
            validate_qualify_columns=False,
        )
        tree = annotate_types.annotate_types(
            tree,
            schema=mapping,
        )
    except Exception as error:
        fail(
            ErrorCode.QUALIFICATION_ERROR,
            str(error),
            node=tree,
            cause=error,
        )

    arena = TermArena(catalog.context)
    session = LoweringSession(catalog, arena)
    query = QueryCompiler(session)
    relation = query.lower(tree)
    root = session.builder.finish(relation.term)
    return LoweredQuery(
        arena,
        root,
        tuple(QueryColumn(column.name, column.sort) for column in relation.columns),
    )
