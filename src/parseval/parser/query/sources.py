"""Lowering of FROM sources: tables, derived tables, VALUES, and joins."""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from sqlglot import exp

from parseval.errors import ErrorCode, fail
from parseval.identifiers import Identifier, name_key
from parseval.parser.scope import ColumnBinding, OuterScope, Relation
from parseval.terms.builder import TermRef
from parseval.terms.sorts import STRING, ScalarSort


class SourceLowering:
    """Lower FROM items and joins into relations. Mixed into ``QueryCompiler``."""

    __slots__ = ()

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

    def _joined_source(
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
        if side:
            return self._outer_join(
                side,
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
            relation = self._apply_alias(relation, lateral)
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
                return self._apply_alias(replace(cte, term=self.as_bag(cte)), source)

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
            return self._apply_alias(relation, source)

        if isinstance(source, exp.Subquery):
            relation = self.lower(
                source.this,
                outer_scopes=outer_scopes,
                ctes=ctes,
            )
            relation = replace(relation, term=self.as_bag(relation))
            return self._apply_alias(relation, source)

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
        return self._apply_alias(relation, source)

    def _apply_alias(self, relation: Relation, source: exp.Expression) -> Relation:
        alias_node = source.args.get("alias")
        if not isinstance(alias_node, exp.TableAlias):
            return relation
        alias = self.context.dialect.identifier(
            alias_node.this,
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
