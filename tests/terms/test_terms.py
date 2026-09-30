import unittest
from datetime import datetime, time, timezone

from parseval.errors import IRValidationError
from parseval.terms import terms as n
from parseval.terms.arena import TermArena
from parseval.terms.binding import substitute_row
from parseval.terms.builder import IRBuilder
from parseval.terms.context import AggregateSpec, Context
from parseval.terms.names import (
    AggregateSpecId,
    ColumnId,
    RelationId,
    SchemaId,
)
from parseval.terms.printer import format_arena, format_term
from parseval.terms.decls import ColumnSpec, RelationSpec, RowShape
from parseval.terms.sorts import (
    PREDICATE,
    BagSort,
    RowSort,
    ScalarSort,
    SeqSort,
    BOOLEAN,
    INTEGER,
    TIME,
    TIMESTAMP,
    parse_iso_temporal_value,
)
from parseval.terms.verify import verify_closed, verify_uexpr
from parseval.terms.walk import post_order


class TermsTests(unittest.TestCase):
    def test_term_families(self):
        b, a = self.builder, self.arena
        value = a[b.literal(1, INTEGER)]
        predicate = a[b.eq3(b.literal(1, INTEGER), b.literal(2, INTEGER))]
        self.assertIsInstance(value, n.Value)
        self.assertIsInstance(value, n.SQLExpr)
        self.assertIsInstance(predicate, n.Predicate)
        self.assertIsInstance(predicate, n.SQLExpr)
        self.assertNotIsInstance(predicate, n.Value)
        self.assertIsInstance(a[b.one()], n.UCore)
        self.assertFalse(hasattr(value, "__dict__"))
        with self.assertRaises(AttributeError):
            value.sort = PREDICATE

    def test_term_families_are_not_concrete_operations(self):
        families = (
            n.SQLExpr,
            n.Value,
            n.Predicate,
            n.UCore,
            n.UAgg,
            n.UOrder,
            n.UWindow,
            n.UBind,
            n.CompactTerm,
        )
        for family in (n.TermNode, *families):
            with self.subTest(family=family):
                self.assertNotIn(family, n.NODE_TYPES)
                with self.assertRaises(TypeError):
                    family(PREDICATE)
        groups = (
            n.SQL_EXPR_NODES,
            n.UCORE_NODES,
            n.UAGG_NODES,
            n.UORDER_NODES,
            n.UWINDOW_NODES,
            n.UBIND_NODES,
            n.COMPACT_ONLY_NODES,
        )
        self.assertEqual(set().union(*groups), set(n.NODE_TYPES))
        self.assertEqual(sum(map(len, groups)), len(n.NODE_TYPES))

    def test_timestamp_parsing_accepts_utc_z_on_python_310(self):
        self.assertEqual(
            parse_iso_temporal_value("2026-09-24T12:30:00Z", TIMESTAMP),
            datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            parse_iso_temporal_value("2026-09-24T12:30:00", TIMESTAMP),
            datetime(2026, 9, 24, 12, 30),
        )

    def test_time_parsing_preserves_time_of_day(self):
        self.assertEqual(parse_iso_temporal_value("12:30:45", TIME), time(12, 30, 45))
        self.builder.literal(time(12, 30, 45), TIME)
        with self.assertRaises(IRValidationError):
            self.builder.literal("12:30:45", TIME)

    def setUp(self):
        self.context = Context()
        self.arena = TermArena(self.context)
        self.builder = IRBuilder(self.arena)

    def schema(self, *fields):
        return self.context.intern_schema(RowShape(tuple(fields)))

    def test_add_preserves_multiplicity(self):
        b = self.builder
        result = b.add(b.one(), b.one())
        self.assertIsInstance(self.arena[result], n.Add)
        self.assertEqual(self.arena[result].children, (b.one(), b.one()))

    def test_zero_does_not_hide_invalid_multiplication_operand(self):
        b = self.builder
        for operands in (
            (b.zero(), b.literal(7, INTEGER)),
            (b.literal(7, INTEGER), b.zero()),
        ):
            with self.subTest(operands=operands):
                with self.assertRaises(IRValidationError):
                    b.mul(*operands)

    def test_substitution_does_not_capture_free_relation_variable(self):
        a = self.arena
        schema = self.context.intern_schema(RowShape((ScalarSort(INTEGER, True),)))
        free_relation = a.rel_var(0, BagSort(schema))
        scalar = a.intern_checked(n.Scalarize, (free_relation,))
        replacement = a.intern_checked(n.Row, (scalar,), n.SchemaPayload(schema))
        empty = a.intern_checked(n.Empty, payload=n.SchemaPayload(schema))
        body = a.intern_checked(n.LetRel, (empty, a.row_var(0, RowSort(schema))))

        result = substitute_row(a, body, replacement)

        inserted_row = a[a[result].children[1]]
        inserted_scalar = a[inserted_row.children[0]]
        inserted_relation = a[inserted_scalar.children[0]]
        self.assertEqual(inserted_relation.payload.depth, 1)

    def test_substitution_accounts_for_both_binder_namespaces(self):
        a = self.arena
        schema = self.schema(ScalarSort(INTEGER, True))
        # The replacement depends on both a free row and a free relation.
        scalar = self.builder.case(
            self.builder.true3(),
            self.builder.field(a.row_var(0, RowSort(schema)), 0),
            self.builder.scalarize(a.rel_var(0, BagSort(schema))),
        )
        replacement = self.builder.row(schema, (scalar,))
        empty = self.builder.empty(schema)
        target = a.row_var(1, RowSort(schema))
        let = a.intern_checked(n.LetRel, (empty, target))
        body = a.intern_checked(n.RowLambda, (let,), n.RowLambdaPayload(schema))
        result = substitute_row(a, body, replacement)
        variables = [
            a[t]
            for t in post_order(a, (result,))
            if isinstance(a[t], (n.RowVar, n.RelVar))
        ]
        self.assertEqual(
            {(type(v), v.payload.depth) for v in variables},
            {(n.RowVar, 1), (n.RelVar, 1)},
        )

    def test_substitution_memo_distinguishes_relation_scopes(self):
        a = self.arena
        schema = self.schema(ScalarSort(INTEGER, True))
        row = a.row_var(0, RowSort(schema))
        replacement = self.builder.row(
            schema, (self.builder.scalarize(a.rel_var(0, BagSort(schema))),)
        )
        let = a.intern_checked(n.LetRel, (self.builder.empty(schema), row))
        root = self.builder.row_identity_eq(row, let)
        result = substitute_row(a, root, replacement)
        depths = {
            a[t].payload.depth
            for t in post_order(a, (result,))
            if isinstance(a[t], n.RelVar)
        }
        self.assertEqual(depths, {0, 1})

    def test_all_construction_paths_normalize_indicators(self):
        b, a = self.builder, self.arena
        first = b.eq3(b.literal(1, INTEGER), b.literal(2, INTEGER))
        second = b.lt3(b.literal(1, INTEGER), b.literal(2, INTEGER))
        for predicate in (b.and3(first, second), b.or3(first, second)):
            with self.subTest(predicate=predicate):
                direct = a.intern_checked(n.Indicator, (predicate,))
                self.assertEqual(b.indicator(predicate), direct)
                original = a.intern_checked(n.Indicator, (first,))
                self.assertEqual(a.rebuild(original, (predicate,)), direct)
                self.assertNotIsInstance(a[direct], n.Indicator)

    def test_indicator_preserves_sql_unknown_semantics(self):
        b = self.builder
        self.assertEqual(b.indicator(b.true3()), b.one())
        self.assertEqual(b.indicator(b.false3()), b.zero())
        self.assertEqual(b.indicator(b.unknown3()), b.zero())
        # SQL NOT UNKNOWN remains UNKNOWN, not the complement of [UNKNOWN].
        result = b.indicator(b.not3(b.unknown3()))
        self.assertIsInstance(self.arena[result], n.Indicator)
        self.assertIsInstance(self.arena[self.arena[result].children[0]], n.Not3)

    def test_ac_construction_is_flat_and_preserves_duplicates(self):
        b, a = self.builder, self.arena
        x = b.indicator(b.eq3(b.literal(1, INTEGER), b.literal(2, INTEGER)))
        y = b.indicator(b.lt3(b.literal(1, INTEGER), b.literal(2, INTEGER)))
        for op, identity in ((b.add, b.zero()), (b.mul, b.one())):
            self.assertEqual(op(), identity)
            self.assertEqual(op(identity, x), x)
            self.assertEqual(op(x, y), op(y, x))
            result = op(op(x, y), x)
            self.assertEqual(result, op(x, x, y))
            self.assertEqual(a[result].children.count(x), 2)

    def test_rejects_foreign_handles_and_wrong_payloads(self):
        b, a = self.builder, self.arena
        other = IRBuilder(TermArena(self.context))
        with self.assertRaises(IRValidationError):
            b.mul(b.zero(), other.one())
        with self.assertRaises(IRValidationError):
            a.intern_checked(n.Literal, payload=n.FieldPayload(0))
        with self.assertRaises(IRValidationError):
            a.intern_checked(n.Not3, ())
        with self.assertRaises(KeyError):
            a.row_var(0, RowSort(SchemaId(999)))

    def test_nullable_boolean_values_can_filter_and_project_predicates(self):
        b, a = self.builder, self.arena
        schema = self.schema(ScalarSort(BOOLEAN, True))
        source = b.empty(schema)
        filtered = b.filter(source, lambda row: b.to_predicate(b.field(row, 0)))
        mapped = b.map(
            filtered,
            lambda row: b.row(
                schema, (b.to_boolean(b.not3(b.to_predicate(b.field(row, 0)))),)
            ),
        )
        root = b.finish(mapped)
        self.assertEqual(a[root].sort, BagSort(schema))
        text = format_term(a, root)
        self.assertIn("to_boolean", text)
        self.assertIn("to_predicate", text)
        self.assertEqual(a[b.to_predicate(b.null(BOOLEAN))].sort, PREDICATE)
        with self.assertRaises(IRValidationError):
            b.to_predicate(b.literal(1, INTEGER))
        with self.assertRaises(IRValidationError):
            b.to_boolean(b.literal(True, BOOLEAN))

    def test_builder_restores_scope_after_callback_failure(self):
        b = self.builder
        schema = self.schema(ScalarSort(INTEGER))

        def fail(_):
            raise RuntimeError("callback failed")

        with self.assertRaises(RuntimeError):
            b.row_lambda(schema, fail)
        with self.assertRaises(RuntimeError):
            b.let_rel(b.empty(schema), fail)
        self.assertIsInstance(b.one(), n.TermId)
        self.assertEqual(b.finish(b.one()), b.one())

    def test_scoped_variables_cannot_escape_or_cross_sibling_binders(self):
        b = self.builder
        schema = self.schema(ScalarSort(INTEGER))
        captured = []

        def capture(row):
            captured.append(row)
            return row

        b.row_lambda(schema, capture)
        with self.assertRaises(IRValidationError):
            b.field(captured[0], 0)
        with self.assertRaises(IRValidationError):
            b.row_lambda(schema, lambda _: captured[0])
        with self.assertRaises(IRValidationError):
            verify_closed(self.arena, self.arena.row_var(0, RowSort(schema)))

    def test_relation_replacement_cannot_invalidate_existing_terms(self):
        b, c = self.builder, self.context
        field = ScalarSort(INTEGER)
        schema = self.schema(field)
        column = ColumnSpec(ColumnId(0), field)
        spec = RelationSpec(schema, (column,))
        c.register_relation(RelationId(0), spec)
        root = b.base(RelationId(0))
        c.replace_relation(RelationId(0), spec)
        other_schema = self.schema(ScalarSort(BOOLEAN))
        with self.assertRaises(ValueError):
            c.replace_relation(RelationId(0), RelationSpec(other_schema, (column,)))
        with self.assertRaises(ValueError):
            c.register_relation(RelationId(1), RelationSpec(schema, ()))
        self.assertEqual(self.arena[root].sort, BagSort(schema))

    def test_aggregate_accepts_nonnullable_input_for_nullable_parameter(self):
        b, c = self.builder, self.context
        schema = self.schema(ScalarSort(INTEGER))
        aggregate = AggregateSpecId(0)
        c.register_aggregate(
            aggregate,
            AggregateSpec(
                ScalarSort(INTEGER, True),
                ScalarSort(INTEGER),
            ),
        )
        call = b.aggregate_call(aggregate, argument=lambda row: b.field(row, 0))
        folded = b.global_fold(b.empty(schema), (call,), schema)
        self.assertEqual(self.arena[b.finish(folded)].sort, BagSort(schema))

    def test_sequence_bounds_and_sorts(self):
        b, a = self.builder, self.arena
        schema = self.schema(ScalarSort(INTEGER))
        sequence = b.order_by(b.empty(schema), (b.order_key(lambda r: b.field(r, 0)),))
        count = b.literal(3, INTEGER)
        for result in (
            b.take(count, sequence),
            b.drop(count, sequence),
            b.slice(count, count, sequence),
        ):
            self.assertEqual(a[b.finish(result)].sort, SeqSort(schema))
        with self.assertRaises(IRValidationError):
            b.take(b.literal(-1, INTEGER), sequence)
        with self.assertRaises(IRValidationError):
            b.take(b.null(INTEGER), sequence)

    def test_build_print_and_verify_selection_as_uexpr(self):
        b, a, c = self.builder, self.arena, self.context
        field = ScalarSort(INTEGER)
        schema = self.schema(field)
        c.register_relation(
            RelationId(0),
            RelationSpec(schema, (ColumnSpec(ColumnId(0), field),)),
        )
        base = b.base(RelationId(0))
        # SELECT x FROM R WHERE x = 7, with output multiplicity indexed by out.
        root = b.finish(
            b.bag_lam(
                schema,
                lambda out: b.sum(
                    RowSort(schema),
                    lambda row: b.mul(
                        b.at(base, row),
                        b.indicator(b.eq3(b.field(row, 0), b.literal(7, INTEGER))),
                        b.indicator(b.row_identity_eq(out, row)),
                    ),
                ),
            )
        )
        self.assertEqual(verify_uexpr(a, root), BagSort(schema))
        self.assertNotIn("free_row", format_term(a, root))
        self.assertIn("sum", str(a.view(root)))
        self.assertIn("bag.lambda", format_arena(a, root))
        reached = list(post_order(a, (root, root)))
        self.assertEqual(len(reached), len(set(reached)))
        with self.assertRaises(IRValidationError):
            verify_uexpr(a, b.filter(base, lambda _: b.true3()))


if __name__ == "__main__":
    unittest.main()
