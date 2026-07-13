from __future__ import annotations

import unittest

from sqlglot import exp, parse_one

from parseval.dtype import DataType
from parseval.generator.bindings import RowBinding, Scope, ScopeResolutionError
from parseval.generator.constraints import UnsupportedQueryFeature, rewrite_expr
from parseval.plan.explain import TableScan


def scan(table: str) -> TableScan:
    step = TableScan()
    step.table = exp.table_(table)
    return step


class TestBindingsAndConstraints(unittest.TestCase):
    def test_unambiguous_column_resolution(self):
        scope = Scope(query_id=0, scope_id="s0")
        table = exp.table_("users")
        alias = exp.to_identifier("u")
        column = exp.to_identifier("id")
        row = RowBinding.for_table(
            table=table,
            alias=alias,
            row_index=0,
            columns={column: DataType.build("INT")},
            scope=scope,
            source_step=scan("users"),
        )
        scope.add_row(row)

        resolved = scope.resolve_column(exp.column("id"))

        self.assertEqual("q0.s0.u.r0.id", resolved.var_key)
        self.assertIs(row.table, table)
        self.assertIs(row.alias, alias)
        self.assertIs(next(iter(row.columns)), column)

    def test_ambiguous_column_rejection(self):
        scope = Scope(query_id=0, scope_id="s0")
        for alias in ("u", "a"):
            scope.add_row(
                RowBinding.for_table(
                    table=exp.table_(alias),
                    alias=exp.to_identifier(alias),
                    row_index=0,
                    columns={exp.to_identifier("id"): DataType.build("INT")},
                    scope=scope,
                    source_step=scan(alias),
                )
            )

        with self.assertRaisesRegex(ScopeResolutionError, "ambiguous"):
            scope.resolve_column(exp.column("id"))

    def test_alias_qualified_column_resolution(self):
        scope = Scope(query_id=0, scope_id="s0")
        scope.add_row(
            RowBinding.for_table(
                table=exp.table_("users"),
                alias=exp.to_identifier("u"),
                row_index=0,
                columns={exp.to_identifier("id"): DataType.build("INT")},
                scope=scope,
                source_step=scan("users"),
            )
        )

        resolved = scope.resolve_column(exp.column("id", table="u"))

        self.assertEqual("users", resolved.meta["table"])
        self.assertEqual("u", resolved.meta["alias"])

    def test_rewrite_replaces_columns_with_solvervars(self):
        scope = Scope(query_id=0, scope_id="s0")
        scope.add_row(
            RowBinding.for_table(
                table=exp.table_("users"),
                alias=exp.to_identifier("u"),
                row_index=0,
                columns={exp.to_identifier("age"): DataType.build("INT")},
                scope=scope,
                source_step=scan("users"),
            )
        )
        predicate = parse_one("age > 21")

        rewritten = rewrite_expr(predicate, scope)

        self.assertFalse(list(rewritten.find_all(exp.Column)))
        self.assertIn("SolverVar(q0.s0.u.r0.age)", rewritten.sql())

    def test_rewrite_wraps_ambiguous_columns_as_unsupported(self):
        scope = Scope(query_id=0, scope_id="s0")
        for alias in ("u", "a"):
            scope.add_row(
                RowBinding.for_table(
                    table=exp.table_(alias),
                    alias=exp.to_identifier(alias),
                    row_index=0,
                    columns={exp.to_identifier("id"): DataType.build("INT")},
                    scope=scope,
                    source_step=scan(alias),
                )
            )

        with self.assertRaisesRegex(UnsupportedQueryFeature, "ambiguous"):
            rewrite_expr(parse_one("id = 1"), scope)


if __name__ == "__main__":
    unittest.main()
