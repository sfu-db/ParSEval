"""Compile qualified SQL queries into the checked term algebra."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from sqlglot import exp

from parseval.errors import CatalogError, DDLImportError, ErrorCode, fail
from parseval.terms.context import AggregateKind, AggregateSpec
from parseval.identifiers import Identifier, name_key
from parseval.terms.names import CollationId
from parseval.terms.sorts import (
    BagSort,
    ScalarSort,
    SeqSort,
    FLOAT,
    INTEGER,
    STRING,
    TypeKind,
)
from parseval.terms.arena import TermArena
from parseval.terms.terms import TermId
from sqlglot import parse
from sqlglot.errors import ParseError
from sqlglot.optimizer import annotate_types, qualify
from parseval.terms.builder import (
    AggregateCall,
    Direction,
    NullPlacement,
    OrderKey,
    TermRef,
    WindowCall,
)
from parseval.terms.terms import (
    WindowBoundary,
    WindowBoundaryKind,
    WindowFrame,
    WindowFrameExclusion,
    WindowFrameMode,
    WindowFunctionKind,
)

from parseval.parser.context import LoweringSession
from parseval.parser.expression import (
    ExpressionCompiler,
    ScalarPlan,
    scalar_sort_from_expression,
)
from parseval.parser.syntax import (
    aggregate_expression,
    function_name,
    integer_literal,
    query_body,
    strip_alias,
)
from .normalize import normalize_query_syntax
from parseval.parser.scope import (
    ColumnBinding,
    EmitEnvironment,
    FieldSlot,
    OuterScope,
    Relation,
)
from .analysis import Projection, QueryBlockAnalyzer, QueryBlockFacts

_EXPANDED_STAR = "parseval_expanded_star"


class QueryCompiler:
    __slots__ = (
        "context",
        "scalar",
        "analyzer",
        "expressions",
        "active_ctes",
    )

    def __init__(self, context: LoweringSession) -> None:
        self.context = context
        self.analyzer = QueryBlockAnalyzer(context.dialect)
        self.active_ctes: Mapping[str, Relation] = {}
        self.expressions = ExpressionCompiler(
            context,
            relations=self,
            expression_key=self.analyzer.expression_id,
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
                return self._relation_source(
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
                return self._with(
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
                return self._relation_source(
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
        if isinstance(sort, SeqSort):
            return self.context.builder.forget_order(relation.term)
        raise TypeError(f"Expected a relation term, got {sort!r}")

    def infer_scalar_query_sort(self, query: exp.Expression) -> ScalarSort:
        """Infer the sole output sort of a scalar query expression."""

        query = query_body(query)
        if isinstance(query, (exp.Union, exp.Intersect, exp.Except)):
            left = self.infer_scalar_query_sort(query.this)
            right = self.infer_scalar_query_sort(query.expression)
            result = self.context.dialect.common_scalar_sort(left, right)
            if result is not None:
                return result
            self.context.unsupported(
                "Scalar set-operation branches have incompatible types",
                query,
                code=ErrorCode.TYPE_ERROR,
            )
        if not isinstance(query, exp.Select) or len(query.expressions) != 1:
            self.context.unsupported(
                "Scalar subqueries must project exactly one column",
                query,
                code=ErrorCode.UNSUPPORTED_EXPRESSION,
            )
        expression = strip_alias(query.expressions[0])
        inferred = self._infer_expression_sort(expression)
        if inferred is not None:
            return inferred
        self.context.unsupported(
            "Could not infer scalar subquery output type",
            expression,
            code=ErrorCode.TYPE_ERROR,
        )

    def _infer_expression_sort(
        self, expression: exp.Expression
    ) -> ScalarSort | None:
        expression = strip_alias(expression)
        aggregate = aggregate_expression(expression)
        if aggregate is not None:
            if isinstance(aggregate, exp.Filter):
                aggregate = aggregate.this
            if not isinstance(aggregate, exp.AggFunc):
                return None
            argument = aggregate.this
            if isinstance(argument, exp.Distinct):
                values = tuple(argument.expressions)
                if len(values) != 1:
                    return None
                argument = values[0]
            if isinstance(argument, exp.Star) or argument is None:
                argument_sort = None
            else:
                try:
                    argument_sort = scalar_sort_from_expression(
                        argument, self.context.dialect
                    )
                except DDLImportError:
                    argument_sort = self._infer_expression_sort(argument)
                if argument_sort is None:
                    return None
            try:
                return self._aggregate_spec(aggregate, argument_sort).output
            except CatalogError:
                return None
        if isinstance(expression, exp.Column):
            column_name = self.context.dialect.identifier(expression.this).text
            table_name = (
                self.context.dialect.identifier(
                    expression.args["table"], is_table=True
                ).text
                if expression.args.get("table") is not None
                else None
            )
            relations = (
                (self.active_ctes.get(table_name),)
                if table_name is not None
                else tuple(self.active_ctes.values())
            )
            matches = [
                column.sort
                for relation in relations
                if relation is not None
                for column in relation.columns
                if column.name.text == column_name
            ]
            if len(matches) == 1:
                return matches[0]
            return self._declared_source_column_sort(expression)
        if isinstance(expression, exp.Subquery) and isinstance(
            expression.this, exp.Select
        ):
            return self._infer_expression_sort(
                strip_alias(expression.this.expressions[0])
            )
        if isinstance(expression, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)):
            left = self._infer_expression_sort(expression.this)
            right = self._infer_expression_sort(expression.expression)
            if left is not None and right is not None:
                return self.context.dialect.common_scalar_sort(
                    left,
                    right,
                    prefer_float=isinstance(expression, exp.Div),
                    arithmetic=True,
                )
            return None
        try:
            return scalar_sort_from_expression(expression, self.context.dialect)
        except DDLImportError:
            return None

    def _declared_source_column_sort(
        self,
        expression: exp.Column,
    ) -> ScalarSort | None:
        """Resolve a qualified field through a base table's positional alias.

        SQLGlot does not propagate declared types through PostgreSQL table
        aliases of the form ``table alias(col1, ...)``.  This is binding
        information, so recover it from the containing query scope and the
        catalog rather than guessing from the renamed identifier.
        """

        select = expression.parent
        while select is not None and not isinstance(select, exp.Select):
            select = select.parent
        if not isinstance(select, exp.Select):
            return None

        wanted_name = self.context.dialect.identifier(expression.this).text
        wanted_table = (
            self.context.dialect.identifier(
                expression.args["table"], is_table=True
            ).text
            if expression.args.get("table") is not None
            else None
        )
        matches: list[ScalarSort] = []
        for table in select.find_all(exp.Table):
            owner = table.parent
            while owner is not None and not isinstance(owner, exp.Select):
                owner = owner.parent
            if owner is not select:
                continue
            alias_node = table.args.get("alias")
            alias_name = (
                self.context.dialect.identifier(
                    alias_node.this, is_table=True
                ).text
                if isinstance(alias_node, exp.TableAlias)
                else self.context.dialect.identifier(
                    table.this, is_table=True
                ).text
            )
            if wanted_table is not None and alias_name != wanted_table:
                continue
            try:
                declaration = self.context.resolve_table(
                    self.context.dialect.qualified_name(table)
                )
            except CatalogError:
                continue
            aliases = (
                tuple(alias_node.args.get("columns") or ())
                if isinstance(alias_node, exp.TableAlias)
                else ()
            )
            for index, column in enumerate(declaration.columns):
                bound_name = (
                    self.context.dialect.identifier(aliases[index]).text
                    if index < len(aliases)
                    else column.name.text
                )
                if bound_name == wanted_name:
                    matches.append(declaration.column_spec(column.id).sort)
        for subquery in select.find_all(exp.Subquery):
            owner = subquery.parent
            while owner is not None and not isinstance(owner, exp.Select):
                owner = owner.parent
            if owner is not select:
                continue
            alias_node = subquery.args.get("alias")
            if not isinstance(alias_node, exp.TableAlias):
                continue
            alias_name = self.context.dialect.identifier(
                alias_node.this, is_table=True
            ).text
            if wanted_table is not None and alias_name != wanted_table:
                continue
            body = query_body(subquery)
            if not isinstance(body, exp.Select):
                continue
            aliases = tuple(alias_node.args.get("columns") or ())
            for index, projection in enumerate(body.expressions):
                bound_name = (
                    self.context.dialect.identifier(aliases[index]).text
                    if index < len(aliases)
                    else self.context.dialect.identifier(
                        projection.alias_or_name
                    ).text
                )
                if bound_name != wanted_name:
                    continue
                inferred = self._infer_expression_sort(strip_alias(projection))
                if inferred is not None:
                    matches.append(inferred)
        return matches[0] if len(matches) == 1 else None

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

        source = self._from(
            select,
            outer_scopes=outer_scopes,
            ctes=ctes,
        )
        source_environment = self._environment(
            source,
            None,
            outer_scopes=outer_scopes,
        )
        where = select.args.get("where")
        if isinstance(where, exp.Where):
            source = replace(
                source,
                term=self.context.builder.filter(
                    source.term,
                    lambda row: self.expressions.lower_condition(
                        where.this,
                        replace(source_environment, row=row),
                    ),
                ),
            )
            source_environment = replace(source_environment, relation=source)

        facts = self.analyzer.query_block(select)
        projections = self._projections(
            tuple(select.expressions),
            source,
            expanded_star=bool(select.meta.get(_EXPANDED_STAR)),
        )

        if facts.requires_aggregation:
            working, environment = self._aggregate(
                source,
                facts,
                outer_scopes=outer_scopes,
            )
            if facts.having is not None:
                having_environment = environment
                working = replace(
                    working,
                    term=self.context.builder.filter(
                        working.term,
                        lambda row: self.expressions.lower_condition(
                            facts.having,
                            replace(having_environment, row=row),
                        ),
                    ),
                )
                environment = replace(environment, relation=working)
        else:
            if facts.having is not None:
                self.context.unsupported(
                    "HAVING requires GROUP BY or an aggregate",
                    facts.having,
                    code=ErrorCode.UNSUPPORTED_AGGREGATION,
                )
            working = source
            environment = source_environment

        if facts.window_expressions:
            working, environment = self._window(
                working,
                environment,
                facts.window_expressions,
            )
        if facts.qualify is not None:
            qualify_environment = environment
            working = replace(
                working,
                term=self.context.builder.filter(
                    working.term,
                    lambda row: self.expressions.lower_condition(
                        facts.qualify,
                        replace(qualify_environment, row=row),
                    ),
                ),
            )
            environment = replace(environment, relation=working)

        distinct = bool(select.args.get("distinct"))
        order = select.args.get("order")

        if distinct:
            hidden: list[Projection] = []
            if isinstance(order, exp.Order):
                visible_ids = {
                    projection.expression_id for projection in projections
                }
                for index, ordered_expression in enumerate(order.expressions):
                    expression = self._resolve_order_expression(
                        ordered_expression.this, projections
                    )
                    expression_id = self.analyzer.expression_id(expression)
                    if expression_id in visible_ids:
                        continue
                    hidden.append(
                        Projection(
                            expression,
                            Identifier(f"_order_{index}"),
                            expression_id,
                        )
                    )
                    visible_ids.add(expression_id)
            if hidden:
                grouped, grouped_environment = self._distinct_order_inputs(
                    working,
                    environment,
                    projections,
                    tuple(hidden),
                    outer_scopes=outer_scopes,
                )
                ordered = self._order_and_bounds(
                    select,
                    grouped,
                    grouped_environment,
                    projections,
                    order,
                )
                return self._project(
                    ordered,
                    replace(grouped_environment, relation=ordered),
                    projections,
                )
            projected = self._project(working, environment, projections)
            projected = replace(
                projected,
                term=self.context.builder.distinct(projected.term),
            )
            projected_environment = self._projection_environment(
                projected,
                projections,
                outer_scopes=outer_scopes,
            )
            ordered = self._order_and_bounds(
                select,
                projected,
                projected_environment,
                projections,
                order,
            )
            return ordered

        ordering_input = working
        ordering_environment = environment
        if isinstance(order, exp.Order):
            visible_ids = {projection.expression_id for projection in projections}
            hidden: list[Projection] = []
            for index, ordered_expression in enumerate(order.expressions):
                expression = self._resolve_order_expression(
                    ordered_expression.this, projections
                )
                expression_id = self.analyzer.expression_id(expression)
                if expression_id in visible_ids:
                    continue
                hidden.append(
                    Projection(
                        expression,
                        Identifier(f"_order_{index}"),
                        expression_id,
                    )
                )
                visible_ids.add(expression_id)
            decorated_projections = (*projections, *hidden)
            ordering_input = self._project(working, environment, decorated_projections)
            ordering_environment = self._projection_environment(
                ordering_input,
                decorated_projections,
                outer_scopes=outer_scopes,
            )
        ordered = self._order_and_bounds(
            select,
            ordering_input,
            ordering_environment,
            projections,
            order,
        )
        ordered_environment = replace(ordering_environment, relation=ordered)
        return self._project(
            ordered,
            ordered_environment,
            projections,
        )

    def _distinct_order_inputs(
        self,
        source: Relation,
        environment: EmitEnvironment,
        projections: tuple[Projection, ...],
        hidden: tuple[Projection, ...],
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        """Group DISTINCT rows and retain an arbitrary SQLite order value."""
        key_plans = tuple(
            self._projection_plan(projection, environment)
            for projection in projections
        )
        key_sorts = tuple(plan.sort for plan in key_plans)
        calls: list[AggregateCall] = []
        hidden_sorts: list[ScalarSort] = []
        for projection in hidden:
            call, result_sort = self._arbitrary_aggregate_call(
                projection.expression, environment
            )
            calls.append(call)
            hidden_sorts.append(result_sort)

        key_schema = self.context.schema_for_sorts(key_sorts)
        output_schema = self.context.schema_for_sorts(
            (*key_sorts, *hidden_sorts)
        )

        def key_row(row: TermRef) -> TermRef:
            current = replace(environment, row=row)
            return self.context.builder.row(
                key_schema,
                tuple(plan.emit(current) for plan in key_plans),
            )

        term = self.context.builder.group_fold(
            self.as_bag(source), key_row, calls, output_schema
        )
        all_projections = (*projections, *hidden)
        all_sorts = (*key_sorts, *hidden_sorts)
        relation = Relation(
            term,
            output_schema,
            tuple(
                ColumnBinding(
                    projection.name,
                    sort,
                    frozenset(),
                )
                for projection, sort in zip(
                    all_projections, all_sorts, strict=True
                )
            ),
        )
        slots = {
            projection.expression_id: FieldSlot(index, all_sorts[index])
            for index, projection in enumerate(all_projections)
        }
        return relation, EmitEnvironment(
            relation,
            relation.term,
            slots,
            outer_scopes,
        )

    def _with(
        self,
        query: exp.Expression,
        cte_nodes: tuple[exp.CTE, ...],
        index: int,
        bindings: dict[str, Relation],
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        if index == len(cte_nodes):
            previous_ctes = self.active_ctes
            self.active_ctes = bindings
            try:
                return self._select_without_with(
                    query,
                    outer_scopes=outer_scopes,
                    ctes=bindings,
                )
            finally:
                self.active_ctes = previous_ctes

        cte = cte_nodes[index]
        definition = self.lower(
            cte.this,
            outer_scopes=outer_scopes,
            ctes=bindings,
        )
        alias = self.context.dialect.identifier(
            cte.args["alias"].this,
            is_table=True,
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
            result = self._with(
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

    def _select_without_with(
        self,
        query: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        body = query.copy()
        body.set("with", None)
        return self.lower(
            body,
            outer_scopes=outer_scopes,
            ctes=ctes,
        )

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
        left = self._coerce_relation(left, target_fields, expression)
        right = self._coerce_relation(right, target_fields, expression)
        left_term = self.as_bag(left)
        right_term = self.as_bag(right)
        if isinstance(expression, exp.Union):
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
        return self._order_and_bounds(
            expression,
            relation,
            environment,
            projections,
            expression.args.get("order"),
        )

    def _coerce_relation(
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

    def _from(
        self,
        select: exp.Select,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        from_clause = select.args.get("from")
        if not isinstance(from_clause, exp.From) or from_clause.this is None:
            frame = self._singleton()
        else:
            frame = self._source(
                from_clause.this,
                outer_scopes=outer_scopes,
                ctes=ctes,
            )
            for extra in from_clause.expressions:
                frame = self._cross(
                    frame,
                    self._source(
                        extra,
                        outer_scopes=outer_scopes,
                        ctes=ctes,
                    ),
                )

        for join in tuple(select.args.get("joins") or ()):
            frame = self._join(
                frame,
                join,
                outer_scopes=outer_scopes,
                ctes=ctes,
            )
        return frame

    def _relation_source(
        self,
        source: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        relation = self._source(
            source,
            outer_scopes=outer_scopes,
            ctes=ctes,
        )
        for join in tuple(source.args.get("joins") or ()):
            relation = self._join(
                relation,
                join,
                outer_scopes=outer_scopes,
                ctes=ctes,
            )
        return relation

    def _join(
        self,
        left: Relation,
        join: exp.Join,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        if isinstance(join.this, exp.Lateral):
            return self._lateral_join(
                left,
                join,
                outer_scopes=outer_scopes,
                ctes=ctes,
            )
        right = self._source(
            join.this,
            outer_scopes=outer_scopes,
            ctes=ctes,
        )
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        method = str(join.args.get("method") or "").upper()
        if method or kind not in {"", "INNER", "CROSS", "OUTER"}:
            self.context.unsupported(
                "Unsupported join kind or method",
                join,
                code=ErrorCode.UNSUPPORTED_JOIN,
            )
        if join.args.get("using") or join.args.get("natural"):
            self.context.unsupported(
                "USING and NATURAL must be normalized to ON",
                join,
                code=ErrorCode.UNSUPPORTED_JOIN,
            )
        if side not in {"", "LEFT", "RIGHT", "FULL"}:
            self.context.unsupported(
                f"Unsupported join side {side!r}",
                join,
                code=ErrorCode.UNSUPPORTED_JOIN,
            )
        if side and join.args.get("on") is None:
            self.context.unsupported(
                "Outer joins require an ON predicate",
                join,
                code=ErrorCode.UNSUPPORTED_JOIN,
            )
        if kind == "CROSS" or join.args.get("on") is None:
            return self._cross(left, right)
        if side == "LEFT":
            return self._left_join(
                left,
                right,
                join.args["on"],
                outer_scopes=outer_scopes,
            )
        if side == "RIGHT":
            return self._right_join(
                left,
                right,
                join.args["on"],
                outer_scopes=outer_scopes,
            )
        if side == "FULL":
            return self._full_join(
                left,
                right,
                join.args["on"],
                outer_scopes=outer_scopes,
            )
        return self._inner_join(
            left,
            right,
            join.args["on"],
            outer_scopes=outer_scopes,
        )

    def _lateral_join(
        self,
        left: Relation,
        join: exp.Join,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        lateral = join.this
        if not isinstance(lateral, exp.Lateral):
            raise TypeError("Expected a LATERAL source")
        side = str(join.args.get("side") or "").upper()
        kind = str(join.args.get("kind") or "").upper()
        if side not in {"", "LEFT"} or kind not in {"", "INNER", "CROSS", "OUTER"}:
            self.context.unsupported(
                "Unsupported LATERAL join kind",
                join,
                code=ErrorCode.UNSUPPORTED_JOIN,
            )

        result: list[Relation] = []
        predicate = join.args.get("on")

        def inner(outer_row: TermRef) -> TermRef:
            outer_scope = OuterScope(left, outer_row)
            relation = self._source(
                lateral.this,
                outer_scopes=(outer_scope, *outer_scopes),
                ctes=ctes,
            )
            relation = self._alias_relation(relation, lateral)
            if isinstance(predicate, exp.Expression):
                environment = self._environment(
                    relation,
                    None,
                    outer_scopes=(outer_scope, *outer_scopes),
                )
                relation = replace(
                    relation,
                    term=self.context.builder.filter(
                        self.as_bag(relation),
                        lambda row: self.expressions.lower_condition(
                            predicate,
                            replace(environment, row=row),
                        ),
                    ),
                )
            result.append(relation)
            return self.as_bag(relation)

        term = (
            self.context.builder.dependent_left_join(left.term, inner)
            if side == "LEFT"
            else self.context.builder.dependent_join(left.term, inner)
        )
        right = result[0]
        right_columns = (
            tuple(
                replace(column, sort=ScalarSort(column.sort.sql_type, True))
                for column in right.columns
            )
            if side == "LEFT"
            else right.columns
        )
        sort = self.context.arena[self.context.builder.resolve(term)].sort
        if not isinstance(sort, BagSort):
            raise TypeError("LATERAL join must produce a bag")
        return Relation(term, sort.schema, left.columns + right_columns)

    def _source(
        self,
        source: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
        ctes: Mapping[str, Relation],
    ) -> Relation:
        if isinstance(source, exp.Table):
            short_name = source.name
            cte = ctes.get(short_name)
            if cte is not None:
                return self._alias_relation(replace(cte, term=self.as_bag(cte)), source)

            declaration = self.context.resolve_table(
                self.context.dialect.qualified_name(source)
            )
            qualifiers = frozenset(
                {
                    (declaration.name.parts[-1].text,),
                    name_key(declaration.name),
                }
            )
            relation = Relation(
                self.context.builder.base(declaration.relation),
                declaration.schema,
                tuple(
                    ColumnBinding(
                        binding.name,
                        specification.sort,
                        qualifiers,
                        specification.collation,
                    )
                    for binding, specification in zip(
                        declaration.columns,
                        declaration.spec.columns,
                        strict=True,
                    )
                ),
            )
            return self._alias_relation(relation, source)

        if isinstance(source, exp.Subquery):
            relation = self.lower(
                source.this,
                outer_scopes=outer_scopes,
                ctes=ctes,
            )
            relation = replace(relation, term=self.as_bag(relation))
            return self._alias_relation(relation, source)

        if isinstance(source, exp.Values):
            return self._values(source, outer_scopes=outer_scopes)

        self.context.unsupported(
            "Unsupported FROM source",
            source,
            code=ErrorCode.UNSUPPORTED_SOURCE,
        )

    def _values(
        self,
        source: exp.Values,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        rows = tuple(source.expressions)
        if not rows or not all(isinstance(row, exp.Tuple) for row in rows):
            self.context.unsupported(
                "VALUES requires one or more row constructors",
                source,
                code=ErrorCode.UNSUPPORTED_SOURCE,
            )
        width = len(rows[0].expressions)
        if any(len(row.expressions) != width for row in rows):
            fail(
                ErrorCode.TYPE_ERROR,
                "VALUES rows have different widths",
                node=source,
            )

        empty = self._singleton()
        environment = self._environment(
            empty,
            None,
            outer_scopes=outer_scopes,
        )
        column_sorts: list[ScalarSort] = []
        for index in range(width):
            expressions = tuple(row.expressions[index] for row in rows)
            plans = [
                self.scalar.plan(expression, environment)
                for expression in expressions
                if not isinstance(expression, exp.Null)
            ]
            if plans:
                common = plans[0].sort
                for plan in plans[1:]:
                    common = self.context.require_common_scalar_sort(
                        common,
                        plan.sort,
                        source,
                        message="VALUES column has incompatible scalar types",
                    )
                nullable = len(plans) != len(expressions) or common.nullable
                column_sorts.append(ScalarSort(common.sql_type, nullable))
            else:
                column_sorts.append(ScalarSort(STRING, True))

        sorts = tuple(column_sorts)
        schema = self.context.schema_for_sorts(sorts)

        def multiplicity(output: TermRef) -> TermRef:
            indicators: list[TermRef] = []
            for row in rows:
                values: list[TermRef] = []
                for expression, target in zip(row.expressions, sorts, strict=True):
                    plan = self.scalar.plan(expression, environment, target)
                    values.append(
                        self.context.cast_term(
                            plan.emit(environment),
                            plan.sort,
                            target,
                            expression,
                        )
                    )
                value = self.context.builder.row(schema, values)
                indicators.append(
                    self.context.builder.indicator(
                        self.context.builder.row_identity_eq(value, output)
                    )
                )
            return self.context.builder.add(*indicators)

        relation = Relation(
            self.context.builder.bag_lam(schema, multiplicity),
            schema,
            tuple(
                ColumnBinding(
                    Identifier(f"column{index + 1}"),
                    sort,
                    frozenset(),
                )
                for index, sort in enumerate(sorts)
            ),
        )
        return self._alias_relation(relation, source)

    def _alias_relation(self, relation: Relation, source: exp.Expression) -> Relation:
        alias_node = source.args.get("alias")
        if not isinstance(alias_node, exp.TableAlias):
            return relation
        alias = self.context.dialect.identifier(
            alias_node.this,
            is_table=True,
        )
        qualifiers = frozenset({(alias.text,)})
        aliases = tuple(alias_node.args.get("columns") or ())
        columns = tuple(
            ColumnBinding(
                (
                    self.context.dialect.identifier(aliases[index])
                    if index < len(aliases)
                    else column.name
                ),
                column.sort,
                qualifiers,
                column.collation,
            )
            for index, column in enumerate(relation.columns)
        )
        return replace(relation, columns=columns)

    def _singleton(self) -> Relation:
        schema = self.context.schema_for_sorts(())
        return Relation(
            self.context.builder.bag_lam(
                schema, lambda _row: self.context.builder.one()
            ),
            schema,
            (),
        )

    def _cross(self, left: Relation, right: Relation) -> Relation:
        schema = self.context.catalog.context.concat_schema(left.schema, right.schema)
        return Relation(
            self.context.builder.product(left.term, right.term),
            schema,
            left.columns + right.columns,
        )

    def _inner_join(
        self,
        left: Relation,
        right: Relation,
        predicate: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        schema = self.context.catalog.context.concat_schema(left.schema, right.schema)
        combined = Relation(
            left.term,
            schema,
            left.columns + right.columns,
        )
        environment = self._environment(
            combined,
            None,
            outer_scopes=outer_scopes,
        )
        term = self.context.builder.join(
            left.term,
            right.term,
            lambda row: self.expressions.lower_condition(predicate, replace(environment, row=row)),
        )
        return replace(combined, term=term)

    def _left_join(
        self,
        left: Relation,
        right: Relation,
        predicate: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        return self._outer_join(
            "LEFT", left, right, predicate, outer_scopes=outer_scopes
        )

    def _right_join(
        self,
        left: Relation,
        right: Relation,
        predicate: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        return self._outer_join(
            "RIGHT", left, right, predicate, outer_scopes=outer_scopes
        )

    def _full_join(
        self,
        left: Relation,
        right: Relation,
        predicate: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        return self._outer_join(
            "FULL", left, right, predicate, outer_scopes=outer_scopes
        )

    def _outer_join(
        self,
        side: str,
        left: Relation,
        right: Relation,
        predicate: exp.Expression,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> Relation:
        context = self.context.catalog.context
        nullable_left = side in {"RIGHT", "FULL"}
        nullable_right = side in {"LEFT", "FULL"}
        schema = context.outer_join_schema(
            left.schema,
            right.schema,
            nullable_left=nullable_left,
            nullable_right=nullable_right,
        )
        original = Relation(
            left.term,
            context.concat_schema(left.schema, right.schema),
            left.columns + right.columns,
        )
        environment = self._environment(
            original,
            None,
            outer_scopes=outer_scopes,
        )
        builder_method = {
            "LEFT": self.context.builder.left_join,
            "RIGHT": self.context.builder.right_join,
            "FULL": self.context.builder.full_join,
        }[side]
        term = builder_method(
            left.term,
            right.term,
            lambda row: self.expressions.lower_condition(predicate, replace(environment, row=row)),
        )
        columns = tuple(
            replace(column, sort=ScalarSort(column.sort.sql_type, True))
            if nullable
            else column
            for nullable, column in (
                *((nullable_left, column) for column in left.columns),
                *((nullable_right, column) for column in right.columns),
            )
        )
        return Relation(term, schema, columns)

    def _aggregate(
        self,
        source: Relation,
        facts: QueryBlockFacts,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        if facts.grouping_expressions or (
            facts.grouping_sets is not None and len(facts.grouping_sets) > 1
        ):
            return self._aggregate_grouping_sets(
                source,
                facts,
                outer_scopes=outer_scopes,
            )

        source_environment = self._environment(
            source,
            None,
            outer_scopes=outer_scopes,
        )
        key_plans = tuple(
            self.scalar.plan(expression, source_environment)
            for expression in facts.group_expressions
        )
        key_sorts = tuple(plan.sort for plan in key_plans)
        calls: list[AggregateCall] = []
        aggregate_sorts: list[ScalarSort] = []
        for expression in facts.aggregate_expressions:
            call, result_sort = self._aggregate_call(expression, source_environment)
            calls.append(call)
            aggregate_sorts.append(result_sort)
        bare_sorts: list[ScalarSort] = []
        for expression in facts.bare_columns:
            call, result_sort = self._arbitrary_aggregate_call(
                expression, source_environment
            )
            calls.append(call)
            bare_sorts.append(result_sort)

        sorts = (*key_sorts, *aggregate_sorts, *bare_sorts)
        output_schema = self.context.schema_for_sorts(sorts)
        key_schema = self.context.schema_for_sorts(key_sorts)

        def key_row(row: TermRef) -> TermRef:
            environment = replace(source_environment, row=row)
            return self.context.builder.row(
                key_schema,
                tuple(plan.emit(environment) for plan in key_plans),
            )

        if facts.group_expressions:
            term = (
                self.context.builder.group_fold(
                    source.term,
                    key_row,
                    calls,
                    output_schema,
                )
                if calls
                else self.context.builder.distinct(
                    self.context.builder.map(source.term, key_row)
                )
            )
        else:
            term = self.context.builder.global_fold(source.term, calls, output_schema)

        columns = tuple(
            ColumnBinding(Identifier(f"_g{index}"), sort, frozenset())
            for index, sort in enumerate(key_sorts)
        ) + tuple(
            ColumnBinding(Identifier(f"_a{index}"), sort, frozenset())
            for index, sort in enumerate(aggregate_sorts)
        ) + tuple(
            ColumnBinding(Identifier(f"_b{index}"), sort, frozenset())
            for index, sort in enumerate(bare_sorts)
        )
        relation = Relation(term, output_schema, columns)
        expression_slots: dict[str, FieldSlot] = {}
        for index, expression in enumerate(facts.group_expressions):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                index, key_sorts[index]
            )
        for index, expression in enumerate(facts.aggregate_expressions):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                len(key_sorts) + index,
                aggregate_sorts[index],
            )
        bare_offset = len(key_sorts) + len(aggregate_sorts)
        for index, expression in enumerate(facts.bare_columns):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                bare_offset + index,
                bare_sorts[index],
            )
        environment = EmitEnvironment(
            relation,
            relation.term,
            expression_slots,
            outer_scopes,
        )
        return relation, environment

    def _window(
        self,
        source: Relation,
        environment: EmitEnvironment,
        expressions: tuple[exp.Window, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        calls = tuple(
            self._window_call(expression, source, environment)
            for expression in expressions
        )
        result_sorts = tuple(call.result for call in calls)
        source_sorts = self.context.catalog.context.schema(source.schema).fields
        output_schema = self.context.schema_for_sorts((*source_sorts, *result_sorts))
        term = self.context.builder.window(
            self.as_bag(source),
            calls,
            output_schema,
        )
        columns = source.columns + tuple(
            ColumnBinding(Identifier(f"_window{index}"), sort, frozenset())
            for index, sort in enumerate(result_sorts)
        )
        relation = Relation(term, output_schema, columns)
        slots = dict(environment.expressions)
        offset = len(source.columns)
        for index, (expression, sort) in enumerate(
            zip(expressions, result_sorts, strict=True)
        ):
            slots[self.analyzer.expression_id(expression)] = FieldSlot(
                offset + index,
                sort,
            )
        return relation, EmitEnvironment(
            relation,
            relation.term,
            slots,
            environment.outer_scopes,
        )

    def _window_call(
        self,
        expression: exp.Window,
        source: Relation,
        environment: EmitEnvironment,
    ) -> WindowCall:
        function = expression.this
        filter_expression = None
        if isinstance(function, exp.Filter):
            filter_node = function.expression
            filter_expression = (
                filter_node.this if isinstance(filter_node, exp.Where) else filter_node
            )
            function = function.this

        aggregate_id = None
        distinct = False
        argument_plans: tuple[ScalarPlan, ...]
        name = function_name(function).casefold()
        if isinstance(function, (exp.Lag, exp.Lead)):
            kind = (
                WindowFunctionKind.LAG
                if isinstance(function, exp.Lag)
                else WindowFunctionKind.LEAD
            )
            operands = tuple(function.iter_expressions())
            if not operands:
                self.context.unsupported(
                    f"{name.upper()} requires a value argument",
                    expression,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )
            value_plan = self.scalar.plan(operands[0], environment)
            plans = [value_plan]
            if len(operands) >= 2:
                plans.append(
                    self.scalar.plan(
                        operands[1],
                        environment,
                        ScalarSort(INTEGER, False),
                    )
                )
            if len(operands) >= 3:
                plans.append(
                    self.scalar.plan(operands[2], environment, value_plan.sort)
                )
            argument_plans = tuple(plans)
            result_sort = ScalarSort(value_plan.sort.sql_type, True)
        elif isinstance(function, exp.AggFunc):
            argument = function.this
            if isinstance(argument, exp.Distinct):
                arguments = tuple(argument.expressions)
                if len(arguments) != 1:
                    self.context.unsupported(
                        "DISTINCT window aggregates require one argument",
                        expression,
                        code=ErrorCode.UNSUPPORTED_AGGREGATION,
                    )
                argument = arguments[0]
                distinct = True
            if argument is None or isinstance(argument, exp.Star):
                argument_plans = ()
                argument_sort = None
            else:
                plan = self.scalar.plan(argument, environment)
                argument_plans = (plan,)
                argument_sort = plan.sort
            aggregate = self._aggregate_spec(function, argument_sort)
            aggregate_id = self.context.catalog.context.intern_aggregate(aggregate)
            kind = WindowFunctionKind.AGGREGATE
            result_sort = aggregate.output
        else:
            ranking = {
                "row_number": WindowFunctionKind.ROW_NUMBER,
                "rank": WindowFunctionKind.RANK,
                "dense_rank": WindowFunctionKind.DENSE_RANK,
            }
            if name in ranking:
                kind = ranking[name]
                argument_plans = ()
                result_sort = ScalarSort(INTEGER, False)
            else:
                self.context.unsupported(
                    f"Unsupported window function {function_name(function)}",
                    expression,
                    code=ErrorCode.UNSUPPORTED_EXPRESSION,
                )

        row_environment = lambda row: replace(
            environment,
            relation=source,
            row=row,
        )
        arguments = tuple(
            lambda row, plan=plan: plan.emit(row_environment(row))
            for plan in argument_plans
        )
        partition_plans = tuple(
            self.scalar.plan(item, environment)
            for item in expression.args.get("partition_by") or ()
        )
        partitions = tuple(
            lambda row, plan=plan: plan.emit(row_environment(row))
            for plan in partition_plans
        )
        order_keys: list[OrderKey] = []
        order = expression.args.get("order")
        if isinstance(order, exp.Order):
            for ordered in order.expressions:
                plan = self.scalar.plan(ordered.this, environment)
                descending = bool(ordered.args.get("desc"))
                order_keys.append(
                    self.context.builder.order_key(
                        lambda row, plan=plan: plan.emit(row_environment(row)),
                        direction=(Direction.DESC if descending else Direction.ASC),
                        nulls=(
                            NullPlacement.FIRST
                            if self.context.nulls_first(
                                ordered, descending=descending
                            )
                            else NullPlacement.LAST
                        ),
                        collation=self._expression_collation(
                            ordered.this,
                            source,
                            environment.outer_scopes,
                            environment.expressions,
                        ),
                    )
                )

        filter_body = (
            None
            if filter_expression is None
            else lambda row: self.expressions.lower_condition(
                filter_expression,
                row_environment(row),
            )
        )
        return WindowCall(
            kind,
            result_sort,
            arguments,
            partitions,
            tuple(order_keys),
            self._window_frame(expression, bool(order_keys)),
            aggregate_id,
            filter_body,
            distinct,
        )

    def _window_frame(
        self,
        expression: exp.Window,
        ordered: bool,
    ) -> WindowFrame:
        spec = expression.args.get("spec")
        if not isinstance(spec, exp.WindowSpec):
            return WindowFrame(
                WindowFrameMode.RANGE,
                WindowBoundary(WindowBoundaryKind.UNBOUNDED_PRECEDING),
                WindowBoundary(
                    WindowBoundaryKind.CURRENT_ROW
                    if ordered
                    else WindowBoundaryKind.UNBOUNDED_FOLLOWING
                ),
            )
        mode = WindowFrameMode(str(spec.args.get("kind") or "range").casefold())
        exclusion_text = str(spec.args.get("exclude") or "no others")
        exclusion = WindowFrameExclusion(
            exclusion_text.casefold().replace(" ", "_")
        )
        return WindowFrame(
            mode,
            self._window_boundary(
                spec.args.get("start"),
                spec.args.get("start_side"),
            ),
            self._window_boundary(
                spec.args.get("end"),
                spec.args.get("end_side"),
            ),
            exclusion,
        )

    def _window_boundary(
        self,
        value: object,
        side: object,
    ) -> WindowBoundary:
        text = str(value or "CURRENT ROW").upper()
        direction = str(side or "").upper()
        if text == "UNBOUNDED":
            return WindowBoundary(
                WindowBoundaryKind.UNBOUNDED_PRECEDING
                if direction == "PRECEDING"
                else WindowBoundaryKind.UNBOUNDED_FOLLOWING
            )
        if text == "CURRENT ROW":
            return WindowBoundary(WindowBoundaryKind.CURRENT_ROW)
        if isinstance(value, exp.Expression):
            offset = integer_literal(value)
            if offset is not None:
                return WindowBoundary(
                    WindowBoundaryKind.OFFSET_PRECEDING
                    if direction == "PRECEDING"
                    else WindowBoundaryKind.OFFSET_FOLLOWING,
                    offset,
                )
        self.context.unsupported(
            "Window frame offsets must be nonnegative integer literals",
            value,
            code=ErrorCode.UNSUPPORTED_EXPRESSION,
        )

    def _aggregate_grouping_sets(
        self,
        source: Relation,
        facts: QueryBlockFacts,
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> tuple[Relation, EmitEnvironment]:
        source_environment = self._environment(
            source,
            None,
            outer_scopes=outer_scopes,
        )
        grouping_sets = facts.grouping_sets
        if grouping_sets is None:
            grouping_sets = (facts.group_expressions,)

        key_plans = {
            self.analyzer.expression_id(expression): self.scalar.plan(
                expression, source_environment
            )
            for expression in facts.group_expressions
        }
        key_ids = tuple(
            self.analyzer.expression_id(expression)
            for expression in facts.group_expressions
        )
        key_sorts = tuple(key_plans[identity].sort for identity in key_ids)
        output_key_sorts = tuple(
            ScalarSort(
                sort.sql_type,
                sort.nullable
                or any(
                    identity
                    not in {
                        self.analyzer.expression_id(expression)
                        for expression in grouping_set
                    }
                    for grouping_set in grouping_sets
                ),
            )
            for identity, sort in zip(key_ids, key_sorts, strict=True)
        )

        calls: list[AggregateCall] = []
        aggregate_sorts: list[ScalarSort] = []
        for expression in facts.aggregate_expressions:
            call, result_sort = self._aggregate_call(expression, source_environment)
            calls.append(call)
            aggregate_sorts.append(result_sort)
        bare_sorts: list[ScalarSort] = []
        for expression in facts.bare_columns:
            call, result_sort = self._arbitrary_aggregate_call(
                expression, source_environment
            )
            calls.append(call)
            bare_sorts.append(result_sort)

        grouping_sorts = tuple(
            ScalarSort(INTEGER, False) for _ in facts.grouping_expressions
        )
        output_sorts = (
            *output_key_sorts,
            *aggregate_sorts,
            *bare_sorts,
            *grouping_sorts,
        )
        output_schema = self.context.schema_for_sorts(output_sorts)
        branches: list[TermRef] = []

        for grouping_set in grouping_sets:
            active_ids = tuple(
                self.analyzer.expression_id(expression)
                for expression in grouping_set
            )
            active_sorts = tuple(key_plans[identity].sort for identity in active_ids)
            key_schema = self.context.schema_for_sorts(active_sorts)

            def key_row(row: TermRef, identities=active_ids) -> TermRef:
                current = replace(source_environment, row=row)
                return self.context.builder.row(
                    key_schema,
                    tuple(key_plans[identity].emit(current) for identity in identities),
                )

            branch_schema = self.context.schema_for_sorts(
                (*active_sorts, *aggregate_sorts, *bare_sorts)
            )
            if active_ids:
                branch = (
                    self.context.builder.group_fold(
                        source.term,
                        key_row,
                        calls,
                        branch_schema,
                    )
                    if calls
                    else self.context.builder.distinct(
                        self.context.builder.map(source.term, key_row)
                    )
                )
            elif calls:
                branch = self.context.builder.global_fold(
                    source.term, calls, branch_schema
                )
            else:
                branch = self._singleton().term

            active_positions = {
                identity: index for index, identity in enumerate(active_ids)
            }
            call_offset = len(active_ids)
            active_set = frozenset(active_ids)

            def project_branch(
                row: TermRef,
                positions=active_positions,
                offset=call_offset,
                included=active_set,
            ) -> TermRef:
                keys = tuple(
                    self.context.builder.field(row, positions[identity])
                    if identity in positions
                    else self.context.builder.null(sort.sql_type)
                    for identity, sort in zip(
                        key_ids, output_key_sorts, strict=True
                    )
                )
                aggregate_values = tuple(
                    self.context.builder.field(row, offset + index)
                    for index in range(len(calls))
                )
                grouping_values = tuple(
                    self.context.builder.literal(
                        self._grouping_mask(expression, included, key_ids),
                        INTEGER,
                    )
                    for expression in facts.grouping_expressions
                )
                return self.context.builder.row(
                    output_schema,
                    (*keys, *aggregate_values, *grouping_values),
                )

            branches.append(self.context.builder.map(branch, project_branch))

        term = branches[0]
        for branch in branches[1:]:
            term = self.context.builder.union_all(term, branch)

        key_count = len(output_key_sorts)
        aggregate_count = len(aggregate_sorts)
        bare_count = len(bare_sorts)
        columns = tuple(
            ColumnBinding(Identifier(f"_g{index}"), sort, frozenset())
            for index, sort in enumerate(output_key_sorts)
        ) + tuple(
            ColumnBinding(Identifier(f"_a{index}"), sort, frozenset())
            for index, sort in enumerate(aggregate_sorts)
        ) + tuple(
            ColumnBinding(Identifier(f"_b{index}"), sort, frozenset())
            for index, sort in enumerate(bare_sorts)
        ) + tuple(
            ColumnBinding(Identifier(f"_grouping{index}"), sort, frozenset())
            for index, sort in enumerate(grouping_sorts)
        )
        relation = Relation(term, output_schema, columns)
        expression_slots: dict[str, FieldSlot] = {}
        for index, expression in enumerate(facts.group_expressions):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                index, output_key_sorts[index]
            )
        for index, expression in enumerate(facts.aggregate_expressions):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                key_count + index, aggregate_sorts[index]
            )
        for index, expression in enumerate(facts.bare_columns):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                key_count + aggregate_count + index,
                bare_sorts[index],
            )
        grouping_offset = key_count + aggregate_count + bare_count
        for index, expression in enumerate(facts.grouping_expressions):
            expression_slots[self.analyzer.expression_id(expression)] = FieldSlot(
                grouping_offset + index,
                grouping_sorts[index],
            )
        return relation, EmitEnvironment(
            relation,
            relation.term,
            expression_slots,
            outer_scopes,
        )

    def _grouping_mask(
        self,
        expression: exp.Anonymous,
        included: frozenset[str],
        group_keys: tuple[str, ...],
    ) -> int:
        mask = 0
        known = frozenset(group_keys)
        arguments = tuple(expression.expressions)
        for index, argument in enumerate(arguments):
            identity = self.analyzer.expression_id(strip_alias(argument))
            if identity not in known:
                self.context.unsupported(
                    "GROUPING arguments must be GROUP BY expressions",
                    argument,
                    code=ErrorCode.UNSUPPORTED_AGGREGATION,
                )
            if identity not in included:
                mask |= 1 << (len(arguments) - index - 1)
        return mask

    def _aggregate_spec(
        self,
        expression: exp.AggFunc,
        input_sort: ScalarSort | None,
    ) -> AggregateSpec:
        """Type one aggregate and return its canonical IR declaration."""

        name = function_name(expression).casefold()
        if isinstance(expression, exp.Count):
            return AggregateSpec(
                input_sort,
                ScalarSort(INTEGER, False),
                kind=AggregateKind.COUNT,
                operator="count",
            )
        if input_sort is None:
            raise CatalogError(f"Aggregate {name} requires one argument")

        if isinstance(expression, (exp.Min, exp.Max)):
            kind = (
                AggregateKind.MIN
                if isinstance(expression, exp.Min)
                else AggregateKind.MAX
            )
            return AggregateSpec(
                input_sort,
                ScalarSort(input_sort.sql_type, True),
                kind=kind,
                operator=name,
            )

        numeric = input_sort.sql_type.kind in (
            TypeKind.INTEGER,
            TypeKind.FLOAT,
            TypeKind.DECIMAL,
        )
        if not numeric and self.context.dialect.name != "sqlite":
            raise CatalogError(f"Aggregate {name} requires a numeric argument")

        if isinstance(expression, exp.Sum):
            if self.context.dialect.name == "sqlite":
                output_type = (
                    INTEGER
                    if input_sort.sql_type.kind in (TypeKind.BOOLEAN, TypeKind.INTEGER)
                    else FLOAT
                )
            else:
                output_type = input_sort.sql_type
            return AggregateSpec(
                input_sort,
                ScalarSort(output_type, True),
                kind=AggregateKind.SUM,
                operator="sum",
            )

        if isinstance(expression, exp.Avg):
            output_type = (
                input_sort.sql_type
                if input_sort.sql_type.kind is TypeKind.DECIMAL
                and self.context.dialect.name != "sqlite"
                else FLOAT
            )
            return AggregateSpec(
                input_sort,
                ScalarSort(output_type, True),
                kind=AggregateKind.AVG,
                operator="avg",
            )

        if isinstance(expression, exp.StddevSamp):
            return AggregateSpec(
                input_sort,
                ScalarSort(FLOAT, True),
                operator="stddev_samp",
            )
        raise CatalogError(f"Unsupported aggregate {name}")

    def _arbitrary_aggregate_call(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> tuple[AggregateCall, ScalarSort]:
        plan = self.scalar.plan(expression, environment)
        output_sort = ScalarSort(plan.sort.sql_type, True)
        name = "__sqlite_arbitrary_value"
        aggregate = self.context.catalog.context.intern_aggregate(
            AggregateSpec(plan.sort, output_sort, operator=name)
        )
        return (
            self.context.builder.aggregate_call(
                aggregate,
                argument=lambda row: plan.emit(replace(environment, row=row)),
            ),
            output_sort,
        )

    def _aggregate_call(
        self,
        expression: exp.Expression,
        environment: EmitEnvironment,
    ) -> tuple[AggregateCall, ScalarSort]:
        filter_expression = None
        aggregate = aggregate_expression(expression)
        if aggregate is None:
            raise AssertionError("Expected an aggregate expression")
        if isinstance(aggregate, exp.Filter):
            filter_node = aggregate.expression
            filter_expression = (
                filter_node.this if isinstance(filter_node, exp.Where) else filter_node
            )
            aggregate = aggregate.this
        if not isinstance(aggregate, exp.AggFunc):
            raise AssertionError("Aggregate wrapper has no aggregate")

        argument = aggregate.this
        distinct = False
        if isinstance(argument, exp.Distinct):
            values = tuple(argument.expressions)
            if len(values) != 1:
                self.context.unsupported(
                    "DISTINCT aggregates require one argument",
                    expression,
                    code=ErrorCode.UNSUPPORTED_AGGREGATION,
                )
            argument = values[0]
            distinct = True
        if isinstance(argument, exp.Star) or argument is None:
            argument = None
            argument_sort = None
            argument_plan = None
        else:
            argument_plan = self.scalar.plan(argument, environment)
            argument_sort = argument_plan.sort

        aggregate_spec = self._aggregate_spec(aggregate, argument_sort)
        aggregate_id = self.context.catalog.context.intern_aggregate(aggregate_spec)
        if argument_plan is None:
            argument_body = None
        else:
            def lower_argument(row: TermRef) -> TermRef:
                value = argument_plan.emit(replace(environment, row=row))
                return value

            argument_body = lower_argument

        filter_body = (
            None
            if filter_expression is None
            else lambda row: self.expressions.lower_condition(
                filter_expression,
                replace(environment, row=row),
            )
        )
        return (
            self.context.builder.aggregate_call(
                aggregate_id,
                argument=argument_body,
                filter=filter_body,
                distinct=distinct,
            ),
            aggregate_spec.output,
        )

    def _projections(
        self,
        expressions: tuple[exp.Expression, ...],
        source: Relation,
        *,
        expanded_star: bool = False,
    ) -> tuple[Projection, ...]:
        if not expressions:
            self.context.unsupported(
                "SELECT must contain at least one expression",
                expressions,
            )
        positional = (
            self._expanded_star_projection(expressions, source)
            if expanded_star
            else None
        )
        if positional is not None:
            return positional

        result: list[Projection] = []
        for index, expression in enumerate(expressions):
            core = strip_alias(expression)
            star_qualifier: NameKey | None = None
            star: exp.Star | None = None
            if isinstance(core, exp.Star):
                star = core
            elif isinstance(core, exp.Column) and isinstance(core.this, exp.Star):
                star = core.this
                _, star_qualifier = self.context.binder.column_name(
                    exp.Column(
                        this=exp.Identifier(this="_star"),
                        table=core.args.get("table"),
                        db=core.args.get("db"),
                        catalog=core.args.get("catalog"),
                    )
                )
            if star is not None:
                if star.args.get("except") or star.args.get("replace"):
                    self.context.unsupported(
                        "Star EXCEPT/REPLACE requires projection rewrite semantics",
                        expression,
                        code=ErrorCode.UNSUPPORTED_EXPRESSION,
                    )
                matched = False
                for source_index, column in enumerate(source.columns):
                    if (
                        star_qualifier is not None
                        and star_qualifier not in column.qualifiers
                    ):
                        continue
                    matched = True
                    bound_expression = exp.Column(
                        this=exp.Identifier(
                            this=column.name.text,
                            quoted=column.name.quoted,
                        )
                    )
                    result.append(
                        Projection(
                            bound_expression,
                            column.name,
                            f"$source_field_{source_index}",
                            FieldSlot(source_index, column.sort),
                        )
                    )
                if not matched:
                    self.context.unsupported(
                        "Qualified star does not identify a source relation",
                        expression,
                        code=ErrorCode.UNKNOWN_COLUMN,
                    )
                continue
            source_index = self.context.binder.local_index(
                core, source, allow_missing=True
            )
            inherited = (
                source.columns[source_index].name if source_index is not None else None
            )
            result.append(
                Projection(
                    core,
                    self.context.output_name(
                        expression,
                        fallback=f"_col_{index}",
                        inherited=inherited,
                    ),
                    self.analyzer.expression_id(core),
                )
            )
        return tuple(result)

    def _expanded_star_projection(
        self,
        expressions: tuple[exp.Expression, ...],
        source: Relation,
    ) -> tuple[Projection, ...] | None:
        """Recover source ordinals from an expanded star projection.

        SQLGlot expands ``SELECT *`` before query lowering.  Names are not a
        sufficient identity for that expansion because a derived relation may
        legally expose duplicate names.  An expansion that consumes every
        source column exactly once is therefore bound by ordinal.
        """

        if len(expressions) != len(source.columns):
            return None

        projections: list[Projection] = []
        available = set(range(len(source.columns)))
        for index, expression in enumerate(expressions):
            core = strip_alias(expression)
            if not isinstance(core, exp.Column) or isinstance(core.this, exp.Star):
                return None
            candidates = tuple(
                candidate
                for candidate in self.context.binder.matching_indices(core, source)
                if candidate in available
            )
            if not candidates:
                return None
            source_index = candidates[0]
            column = source.columns[source_index]
            output_name = self.context.output_name(
                expression,
                fallback=f"_col_{index}",
                inherited=column.name,
            )
            if output_name.text != column.name.text:
                return None
            available.remove(source_index)
            projections.append(
                Projection(
                    core,
                    column.name,
                    self.analyzer.expression_id(core),
                    FieldSlot(source_index, column.sort),
                )
            )
        return tuple(projections) if not available else None

    def _projection_plan(
        self,
        projection: Projection,
        environment: EmitEnvironment,
    ) -> ScalarPlan:
        slot = projection.source_slot
        if slot is None:
            return self.scalar.plan(projection.expression, environment)
        return ScalarPlan(
            slot.sort,
            lambda current: self.context.builder.field(current.row, slot.index),
        )

    def _project(
        self,
        source: Relation,
        environment: EmitEnvironment,
        projections: tuple[Projection, ...],
    ) -> Relation:
        projection_sorts: tuple[ScalarSort, ...]
        output_schema = None

        def mapper(row: TermRef) -> TermRef:
            nonlocal projection_sorts, output_schema
            row_environment = replace(environment, relation=source, row=row)
            values = tuple(
                self._projection_plan(projection, row_environment).emit(
                    row_environment
                )
                for projection in projections
            )
            projection_sorts = tuple(
                self.context.term_scalar_sort(value) for value in values
            )
            output_schema = self.context.schema_for_sorts(projection_sorts)
            return self.context.builder.row(output_schema, values)

        source_sort = self.context.arena[self.context.builder.resolve(source.term)].sort
        if isinstance(source_sort, BagSort):
            term = self.context.builder.map(source.term, mapper)
        elif isinstance(source_sort, SeqSort):
            term = self.context.builder.sequence_map(source.term, mapper)
        else:
            raise TypeError(f"Expected relation sort, got {source_sort!r}")

        if output_schema is None:
            raise AssertionError("Projection mapper was not elaborated")
        return Relation(
            term,
            output_schema,
            tuple(
                ColumnBinding(
                    projection.name,
                    sort,
                    frozenset(),
                    (
                        source.columns[projection.source_slot.index].collation
                        if projection.source_slot is not None
                        else self._expression_collation(
                            projection.expression,
                            source,
                            environment.outer_scopes,
                            environment.expressions,
                        )
                    ),
                )
                for projection, sort in zip(projections, projection_sorts, strict=True)
            ),
        )

    def _projection_environment(
        self,
        relation: Relation,
        projections: tuple[Projection, ...],
        *,
        outer_scopes: tuple[OuterScope, ...],
    ) -> EmitEnvironment:
        expressions = {
            projection.expression_id: FieldSlot(index, relation.columns[index].sort)
            for index, projection in enumerate(projections)
        }
        return self._environment(
            relation,
            expressions,
            outer_scopes=outer_scopes,
        )

    def _order_and_bounds(
        self,
        query: exp.Select | exp.Union,
        relation: Relation,
        environment: EmitEnvironment,
        projections: tuple[Projection, ...],
        order: exp.Expression | None,
    ) -> Relation:
        limit = query.args.get("limit")
        offset = query.args.get("offset")
        if order is None:
            if isinstance(limit, exp.Limit):
                count = self._bound(limit.expression)
                offset_term = (
                    self._bound(offset.expression)
                    if isinstance(offset, exp.Offset)
                    else self.context.builder.literal(0, INTEGER)
                )
                return replace(
                    relation,
                    term=self.context.builder.topk(
                        relation.term,
                        (),
                        offset_term,
                        count,
                    ),
                )
            if isinstance(offset, exp.Offset):
                arbitrary = self.context.builder.order_by(relation.term, ())
                return replace(
                    relation,
                    term=self.context.builder.drop(
                        self._bound(offset.expression),
                        arbitrary,
                    ),
                )
            return relation
        if not isinstance(order, exp.Order):
            raise TypeError("ORDER BY AST must be exp.Order")

        keys: list[OrderKey] = []
        for ordered in order.expressions:
            expression = self._resolve_order_expression(ordered.this, projections)
            descending = bool(ordered.args.get("desc"))
            keys.append(
                self.context.builder.order_key(
                    lambda row, expression=expression: self.scalar.emit(
                        expression,
                        replace(
                            environment,
                            relation=relation,
                            row=row,
                        ),
                    ),
                    direction=(Direction.DESC if descending else Direction.ASC),
                    nulls=(
                        NullPlacement.FIRST
                        if self.context.nulls_first(ordered, descending=descending)
                        else NullPlacement.LAST
                    ),
                    collation=self._expression_collation(
                        expression,
                        relation,
                        environment.outer_scopes,
                        environment.expressions,
                    ),
                )
            )

        if isinstance(limit, exp.Limit):
            count = self._bound(limit.expression)
            offset_term = (
                self._bound(offset.expression)
                if isinstance(offset, exp.Offset)
                else self.context.builder.literal(0, INTEGER)
            )
            term = self.context.builder.topk(
                relation.term,
                keys,
                offset_term,
                count,
            )
        else:
            term = self.context.builder.order_by(relation.term, keys)
            if isinstance(offset, exp.Offset):
                term = self.context.builder.drop(self._bound(offset.expression), term)
        return replace(relation, term=term)

    def _resolve_order_expression(
        self,
        expression: exp.Expression,
        projections: tuple[Projection, ...],
    ) -> exp.Expression:
        if isinstance(expression, exp.Literal) and not expression.is_string:
            ordinal = integer_literal(expression)
            if ordinal is not None and 1 <= ordinal <= len(projections):
                return projections[ordinal - 1].expression
        if isinstance(expression, exp.Column) and not expression.table:
            name = self.context.dialect.identifier(expression.this)
            matches = [
                projection.expression
                for projection in projections
                if projection.name.text == name.text
            ]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                fail(
                    ErrorCode.AMBIGUOUS_COLUMN,
                    f"Ambiguous ORDER BY alias {name.text!r}",
                    node=expression,
                )
        return strip_alias(expression)

    def _expression_collation(
        self,
        expression: exp.Expression,
        relation: Relation,
        outer_scopes: tuple[OuterScope, ...],
        expressions: Mapping[str, FieldSlot],
    ) -> CollationId | None:
        expression = strip_alias(expression)
        materialized = expressions.get(self.analyzer.expression_id(expression))
        if materialized is not None:
            return relation.columns[materialized.index].collation
        if not isinstance(expression, exp.Column):
            return None
        resolved = self.context.binder.resolve(
            expression,
            relation,
            outer_scopes=outer_scopes,
        )
        return None if resolved is None else resolved[2].collation

    def _bound(self, expression: exp.Expression) -> TermRef:
        value = integer_literal(expression)
        if value is None or value < 0:
            fail(
                ErrorCode.INVALID_LIMIT,
                "LIMIT/OFFSET must be a nonnegative integer literal",
                node=expression,
            )
        return self.context.builder.literal(value, INTEGER)

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
    name: Identifier
    sort: ScalarSort


@dataclass(frozen=True, slots=True)
class QueryResult:
    arena: TermArena
    root: TermId
    columns: tuple[QueryColumn, ...]


def lower_query(sql: str, catalog, *, arena=None) -> QueryResult:
    """Parse SQL with SQLGlot and lower the qualified query into checked terms."""
    if arena is None:
        arena = TermArena(catalog.context)
    if not isinstance(arena, TermArena):
        raise TypeError("arena must be a TermArena")
    if arena.context is not catalog.context:
        raise ValueError("Catalog and TermArena must share the same Context")

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

    tree = normalize_query_syntax(tree, dialect=catalog.dialect.name)
    for select in tree.find_all(exp.Select):
        if any(
            isinstance(core := strip_alias(expression), exp.Star)
            or isinstance(core, exp.Column)
            and isinstance(core.this, exp.Star)
            for expression in select.expressions
        ):
            select.meta[_EXPANDED_STAR] = True
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

    session = LoweringSession(catalog, arena)
    query = QueryCompiler(session)
    relation = query.lower(tree)
    root = session.builder.finish(relation.term)
    return QueryResult(
        arena,
        root,
        tuple(QueryColumn(column.name, column.sort) for column in relation.columns),
    )
